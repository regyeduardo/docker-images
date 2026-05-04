import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.utils import secure_filename

from services.constants import SUPPORTED_EXTENSIONS
from services.generate.gerador import gerar_markdown, montar_conteudo_multimodal
from services.utils import get_file_extension
from skills.educador import EDUCADOR_SYSTEM_PROMPT
from skills.resumidor import RESUMIDOR_SYSTEM_PROMPT

app = Flask(__name__)
CORS(app)

# Explicitly set max content length to 2GB to match nginx
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# Garante que erros não capturados sempre retornem JSON em vez de HTML
@app.errorhandler(500)
def internal_error(e):
    logger.error("Erro 500 não tratado: %s", str(e), exc_info=True)
    return jsonify(
        {"error": "Erro interno do servidor. Verifique os logs para mais detalhes."}
    ), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint não encontrado."}), 404


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({"error": "Arquivo muito grande. O limite é de 2GB."}), 413


# ── TRANSCRIPTION_PROVIDER lido do ambiente ────────────────────────────────
# Usado para decidir QUAL módulo de transcrição importar
# Valores possíveis: siliconflow | groq | deepgram
TRANSCRIPTION_PROVIDER = (
    os.environ.get("TRANSCRIPTION_PROVIDER", "siliconflow").lower().strip()
)


# --- YouTube download ---

# Padrão para validar URLs do YouTube (youtube.com e youtu.be)
YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|embed/|v/|shorts/|live/)|youtu\.be/)"
    r"[a-zA-Z0-9_-]{11}"
    r"([&?]\S*)?$"
)


def is_valid_youtube_url(url: str) -> bool:
    """Valida se a URL é um link válido do YouTube."""
    return bool(YOUTUBE_URL_PATTERN.match(url.strip()))


def download_youtube(url, output_dir):
    """Baixa o áudio do YouTube na melhor qualidade usando yt-dlp.

    Diferentemente da versão anterior, agora baixa **apenas o áudio**
    (MP3 na melhor qualidade disponível), sem interação do usuário.
    Isso é mais rápido, consome menos banda e é suficiente para transcrição.

    Args:
        url: URL do YouTube.
        output_dir: Diretório de saída.
    """
    logger.info("Iniciando download de áudio do YouTube: %s", url)

    outtmpl = str(output_dir / "audio.%(ext)s")
    cmd = [
        "yt-dlp",
        "--ffmpeg-location",
        "/usr/bin/ffmpeg",
        "-f",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",  # 0 = melhor qualidade possível
        "-o",
        outtmpl,
        "--no-playlist",
        "--js-runtime",
        "node",
        "--retries",
        "15",
        "--extractor-retries",
        "15",
        "--retry-sleep",
        "linear=5:30",
        "--sleep-requests",
        "2.0",
        "--sleep-interval",
        "5",
        "--max-sleep-interval",
        "30",
        "--throttled-rate",
        "50K",
        "--add-header",
        "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "--add-header",
        "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "--add-header",
        "Accept-Language:pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "--extractor-args",
        "youtube:player_client=android,web;skip=webpage;player_skip=webpage,configs",
        "--cookies-from-browser",
        "chromium",
        url,
    ]

    logger.info("yt-dlp: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode == 0:
        return _find_downloaded_file(output_dir)

    stderr_combined = (
        result.stderr.strip()[:500] if result.stderr else "(sem saída de erro)"
    )
    logger.error(
        "Download de áudio falhou (código %d): %s",
        result.returncode,
        stderr_combined,
    )

    chromium_check = subprocess.run(
        ["which", "chromium-browser", "||", "which", "chromium"],
        capture_output=True,
        text=True,
        shell=True,
    )
    chromium_available = bool(chromium_check.stdout.strip())
    cookies_db_path = "/root/.config/chromium/Default/Cookies"
    cookies_db_exists = os.path.exists(cookies_db_path)
    node_check = subprocess.run(
        ["which", "node"],
        capture_output=True,
        text=True,
        shell=True,
    )
    node_available = bool(node_check.stdout.strip())
    ytdlp_version = subprocess.run(
        ["yt-dlp", "--version"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    diagnostics = (
        f"Falha ao baixar áudio do YouTube.\n"
        f"Código: {result.returncode}.\n"
        f"Stderr: {stderr_combined}\n"
        f"yt-dlp versão: {ytdlp_version}\n"
        f"Node.js instalado: {'sim' if node_available else 'não'}\n"
        f"Chromium instalado: {'sim' if chromium_available else 'não'}\n"
        f"Banco de cookies em {cookies_db_path}: {'presente' if cookies_db_exists else 'ausente'}\n"
        f"\nDicas:\n"
        f"- Se o YouTube está bloqueando (429), tente usar uma URL diferente ou verificar se o vídeo é público.\n"
        f"- Para vídeos restritos, é necessário fornecer cookies reais do navegador.\n"
        f"- Verifique se a versão do yt-dlp está atualizada (já atualizamos na build do container).\n"
        f"- O Node.js foi instalado como JS runtime para melhorar a extração do YouTube."
    )
    raise RuntimeError(diagnostics)


def _find_downloaded_file(output_dir):
    """Procura o arquivo baixado no diretório de saída."""
    candidates = list(output_dir.glob("video.*"))
    if not candidates:
        candidates = list(output_dir.glob("*"))
    if not candidates:
        raise FileNotFoundError(
            f"Download concluído mas arquivo não encontrado em {output_dir}"
        )
    video_path = candidates[0]
    logger.info(
        "Download concluído: %s (%.1f MB)",
        video_path.name,
        video_path.stat().st_size / 1e6,
    )
    return video_path


UPLOAD_FOLDER = Path("/data/uploads")
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


# --- API Routes ---


@app.route("/api/deepseek-status")
def deepseek_status():
    """Verifica se o DeepSeek está configurado (via env var) e retorna status."""
    from services.generate.gerador import DEEPSEEK_API_KEY, DEEPSEEK_TEXT_MODEL

    available = bool(DEEPSEEK_API_KEY)
    return jsonify(
        {
            "available": available,
            "text_model": DEEPSEEK_TEXT_MODEL if available else None,
        }
    )


@app.route("/api/process", methods=["POST"])
def process():
    """
    Endpoint único de processamento.

    Fluxo:
    1. Recebe um arquivo (upload) OU uma URL do YouTube
    2. Para YouTube: baixa o áudio (MP3 na melhor qualidade) e transcreve automaticamente.
    3. Para upload de arquivo: transcreve (se mídia) ou extrai texto diretamente.
    4. Envia o conteúdo extraído/transcrito para o DeepSeek junto com o
       system prompt do agente selecionado (Resumidor ou Educador).
    5. Retorna o markdown gerado.
    """
    youtube_url = request.form.get("youtube_url", "").strip()
    prompt = request.form.get("prompt", "")
    model = request.form.get("model", "")
    if not model or model.strip() == "" or model.strip().lower() == "default":
        model = None
    agent = request.form.get("agent", "resumidor")
    logger.info("=== Nova requisição /api/process ===")
    logger.info("model: %s", model)
    logger.info("agent: %s", agent)
    logger.info("prompt: %s", prompt[:100] if prompt else "(vazio)")

    # Seleciona o system prompt baseado no agente final
    if agent == "educador":
        final_system_prompt = EDUCADOR_SYSTEM_PROMPT
        logger.info("Usando Agente Educador")
    else:
        final_system_prompt = RESUMIDOR_SYSTEM_PROMPT
        logger.info("Usando Agente Resumidor")

    # --- Validação: apenas UM modo de entrada por vez ---
    has_uploaded_file = "file" in request.files and request.files["file"].filename
    has_youtube = bool(youtube_url)

    input_modes = []
    if has_uploaded_file:
        input_modes.append("file")
    if has_youtube:
        input_modes.append("youtube_url")

    if len(input_modes) > 1:
        logger.error("Múltiplos modos de entrada detectados: %s", input_modes)
        return jsonify({"error": "Selecione apenas UM modo de entrada por vez."}), 400

    if len(input_modes) == 0:
        logger.error("Nenhum modo de entrada selecionado")
        return jsonify(
            {"error": "Selecione um modo de entrada: arquivo ou link do YouTube."}
        ), 400

    logger.info("Modo de entrada: %s", input_modes[0])

    # Cria diretório temporário
    temp_dir = Path(tempfile.mkdtemp(dir=UPLOAD_FOLDER))
    logger.info("Diretório temporário: %s", temp_dir)

    try:
        file_path = None

        # ============================================================
        # MODO 1: Upload de arquivo
        # ============================================================
        if has_uploaded_file:
            file = request.files["file"]
            filename = secure_filename(file.filename)
            ext = get_file_extension(filename)
            logger.info("Arquivo enviado: %s (extensão: %s)", filename, ext)

            if ext not in SUPPORTED_EXTENSIONS:
                return jsonify(
                    {
                        "error": (
                            f"Formato de arquivo não suportado: '{ext}'. "
                            f"Formatos aceitos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
                        )
                    }
                ), 400

            file_path = temp_dir / filename
            file.save(file_path)
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            logger.info("Arquivo salvo: %s (%.1f MB)", filename, file_size_mb)

        # ============================================================
        # MODO 2: URL do YouTube (APENAS YouTube é aceito)
        # ============================================================
        elif has_youtube:
            if not is_valid_youtube_url(youtube_url):
                error_msg = (
                    "Apenas links do YouTube (youtube.com ou youtu.be) são aceitos. "
                    "Para outros vídeos (Vimeo, Google Drive, redes sociais, etc.), "
                    "faça o download do arquivo e utilize a opção 'Upload de Arquivo'.\n\n"
                    "📌 **Alternativas gratuitas para transcrição de vídeos não-YouTube:**\n"
                    "- **OpenAI Whisper (local, gratuito)**: https://github.com/openai/whisper\n"
                    "- **Whisper Web**: https://huggingface.co/spaces/openai/whisper\n"
                    "- **Google Docs (VivaVoice)**: Ferramenta de ditado gratuita\n"
                    "- **CapCut**: Editor de vídeo gratuito com legendas automáticas\n"
                    "- **Subtitle Edit**: Software livre para transcrição"
                )
                return jsonify({"error": error_msg}), 400

            logger.info("Iniciando download do YouTube...")
            file_path = download_youtube(youtube_url, temp_dir)
            logger.info(
                "Áudio baixado: %s (%.1f MB)",
                file_path.name,
                file_path.stat().st_size / 1e6,
            )

        # ============================================================
        # PASSO ÚNICO: Monta conteúdo e envia para o DeepSeek
        # ============================================================
        logger.info("Montando conteúdo multimodal para o DeepSeek...")
        content_parts = montar_conteudo_multimodal(
            file_path,
            prompt,
        )

        logger.info(
            "Enviando para o DeepSeek (agente: %s, modelo: %s, partes: %d)...",
            agent,
            model,
            len(content_parts),
        )

        markdown_output = gerar_markdown(
            content_parts,
            final_system_prompt,
            model=model,
        )

        logger.info("Conteúdo gerado: %d caracteres", len(markdown_output))
        return jsonify({"markdown": markdown_output})

    except subprocess.CalledProcessError as e:
        stderr = e.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        error_msg = stderr if stderr else str(e)
        logger.error("subprocess error: %s", error_msg)
        return jsonify({"error": f"Erro no processamento de mídia: {error_msg}"}), 500
    except Exception as e:
        logger.error("Erro inesperado: %s", str(e), exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        # Limpa arquivos temporários
        try:
            if temp_dir and temp_dir.exists():
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info("Diretório temporário removido: %s", temp_dir)
        except Exception as e:
            logger.warning("Não foi possível limpar diretório temporário: %s", e)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
