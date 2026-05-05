"""
Aplicação Flask — Agentes de Análise de Conteúdo.

Endpoints:
- ``GET /api/deepseek-status`` — status da API DeepSeek.
- ``GET /api/config`` — configurações do backend.
- ``POST /api/process`` — processamento de conteúdo (upload ou YouTube).
- ``POST /api/questions`` — geração de provas de múltipla escolha.
- ``POST /api/import`` — importação de arquivo .md ou .zip.
- ``POST /api/export-zip`` — exportação de ZIP com markdown + questões.
"""

import json as json_lib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from services.constants import SUPPORTED_EXTENSIONS
from services.generate.gerador import gerar_markdown, montar_conteudo_multimodal, _sanitizar_codigo_mermaid
from services.import_export import create_export_zip, extract_import_data
from services.utils import get_file_extension
from services.youtube import download_youtube, is_valid_youtube_url
from skills.educador import EDUCADOR_SYSTEM_PROMPT
from skills.provas import PROVAS_SYSTEM_PROMPT
from skills.resumidor import RESUMIDOR_SYSTEM_PROMPT

app = Flask(__name__)
CORS(app)

# Explicitly set max content length to 2GB to match nginx
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ── Error Handlers ────────────────────────────────────────────────────────────


@app.errorhandler(500)
def internal_error(e):
    """Retorna JSON para erros 500 não tratados."""
    logger.error("Erro 500 não tratado: %s", str(e), exc_info=True)
    return jsonify(
        {"error": "Erro interno do servidor. Verifique os logs para mais detalhes."}
    ), 500


@app.errorhandler(404)
def not_found(e):
    """Retorna JSON para endpoints não encontrados."""
    return jsonify({"error": "Endpoint não encontrado."}), 404


@app.errorhandler(413)
def request_entity_too_large(e):
    """Retorna JSON para arquivos muito grandes."""
    return jsonify({"error": "Arquivo muito grande. O limite é de 2GB."}), 413


# ── Configuração ──────────────────────────────────────────────────────────────

TRANSCRIPTION_PROVIDER = (
    os.environ.get("TRANSCRIPTION_PROVIDER", "siliconflow").lower().strip()
)

UPLOAD_FOLDER = Path("/data/uploads")
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


# ── Endpoints de Configuração ─────────────────────────────────────────────────


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


@app.route("/api/config")
def config():
    """Retorna configurações do backend para o frontend."""
    return jsonify(
        {
            "transcription_provider": TRANSCRIPTION_PROVIDER,
        }
    )


# ── Processamento de Conteúdo ─────────────────────────────────────────────────


@app.route("/api/process", methods=["POST"])
def process():
    """
    Endpoint único de processamento.

    Fluxo:
    1. Recebe um arquivo (upload) OU uma URL do YouTube.
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
    language = request.form.get("language", "").strip() or None
    logger.info("=== Nova requisição /api/process ===")
    logger.info("model: %s", model)
    logger.info("agent: %s", agent)
    logger.info("language: %s", language or "(não especificado)")
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
            language=language,
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
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info("Diretório temporário removido: %s", temp_dir)
        except Exception as e:
            logger.warning("Não foi possível limpar diretório temporário: %s", e)


# ── Geração de Provas ────────────────────────────────────────────────────────


@app.route("/api/questions", methods=["POST"])
def generate_questions():
    """
    Gera uma prova de múltipla escolha a partir do markdown já produzido.

    Recebe:
        ``{"markdown": "...", "title": "..."}``

    Retorna:
        ``{"questions": [...]}`` — JSON com as questões geradas pelo DeepSeek.
    """
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Corpo da requisição deve ser JSON."}), 400

    markdown_content = data.get("markdown", "").strip()
    title = data.get("title", "Conteúdo")

    if not markdown_content:
        return jsonify({"error": "Campo 'markdown' é obrigatório."}), 400

    logger.info("=== Nova requisição /api/questions ===")
    logger.info("Título: %s", title)
    logger.info("Markdown length: %d caracteres", len(markdown_content))

    try:
        from services.generate.gerador import gerar_markdown

        content_parts = [
            {
                "type": "text",
                "text": (
                    f"# Conteúdo para Gerar Prova\n\n"
                    f"## Título: {title}\n\n"
                    f"## Conteúdo Completo\n\n"
                    f"{markdown_content}"
                ),
            }
        ]

        raw_output = gerar_markdown(
            content_parts,
            PROVAS_SYSTEM_PROMPT,
        )

        # Tenta fazer o parse do JSON retornado
        # Remove possíveis blocos ```json ... ``` que o modelo possa incluir
        cleaned = raw_output.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        questions_data = json_lib.loads(cleaned)

        # Sanitiza diagramas Mermaid em cada questão (corrige \\n, aspas desbalanceadas)
        for q in questions_data.get("questions", []):
            diagrama = q.get("diagrama")
            if diagrama and isinstance(diagrama, str):
                q["diagrama"] = _sanitizar_codigo_mermaid(diagrama)

        logger.info(
            "Prova gerada: %d questões",
            len(questions_data.get("questions", [])),
        )
        return jsonify(questions_data)

    except json_lib.JSONDecodeError as e:
        logger.error("Erro ao fazer parse do JSON das questões: %s", str(e))
        logger.error("Resposta bruta (primeiros 500 chars): %s", raw_output[:500])
        return jsonify({
            "error": "Erro ao processar a resposta do gerador de provas. Tente novamente.",
            "questions": [],
        }), 500
    except Exception as e:
        logger.error("Erro ao gerar questões: %s", str(e), exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Importação ────────────────────────────────────────────────────────────────


@app.route("/api/import", methods=["POST"])
def import_file():
    """
    Importa um arquivo .md ou .zip e retorna o conteúdo extraído.

    Request: ``multipart/form-data`` com campo ``file`` (.md ou .zip).

    Para .md:
        Retorna ``{"markdown": "..."}``.

    Para .zip:
        Extrai o ZIP, procura por ``conteudo.md`` e ``questoes.json``, retorna:
        ``{"markdown": "...", "questions": {...}}``.
    """
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Nome de arquivo vazio."}), 400

    filename = secure_filename(file.filename)
    ext = Path(filename).suffix.lower()

    if ext not in (".md", ".zip"):
        return jsonify({
            "error": f"Formato não suportado: '{ext}'. Aceitos apenas .md e .zip."
        }), 400

    logger.info("=== Nova requisição /api/import ===")
    logger.info("Arquivo: %s", filename)

    try:
        file_bytes = file.read()
        result = extract_import_data(file_bytes, filename)
        logger.info(
            "Import concluído: %d caracteres de markdown, questions=%s",
            len(result.get("markdown", "")),
            "sim" if result.get("questions") else "não",
        )
        return jsonify(result)
    except ValueError as e:
        logger.error("Erro de validação no import: %s", str(e))
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("Erro inesperado no import: %s", str(e), exc_info=True)
        return jsonify({"error": f"Erro ao importar arquivo: {str(e)}"}), 500


# ── Exportação ZIP ────────────────────────────────────────────────────────────


@app.route("/api/export-zip", methods=["POST"])
def export_zip():
    """
    Exporta o markdown e opcionalmente as questões como um arquivo ZIP.

    Request: ``application/json``
        ``{"markdown": "...", "questions": {...} | null, "filename": "..."}``

    Response: ``application/zip`` — arquivo binário do ZIP contendo
    ``conteudo.md`` e (se fornecido) ``questoes.json``.
    """
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Corpo da requisição deve ser JSON."}), 400

    markdown = data.get("markdown", "").strip()
    if not markdown:
        return jsonify({"error": "Campo 'markdown' é obrigatório."}), 400

    questions = data.get("questions")
    filename_base = data.get("filename", "conteudo")

    logger.info("=== Nova requisição /api/export-zip ===")
    logger.info(
        "Markdown: %d caracteres, questions: %s, filename_base: %s",
        len(markdown),
        "sim" if questions else "não",
        filename_base,
    )

    try:
        zip_buffer = create_export_zip(markdown, questions, filename_base)
        zip_filename = f"{filename_base}.zip"

        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=zip_filename,
        )
    except Exception as e:
        logger.error("Erro ao exportar ZIP: %s", str(e), exc_info=True)
        return jsonify({"error": f"Erro ao gerar ZIP: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
