"""
Módulo de download do YouTube — extraído de app.py para modularização.

Funções:
- ``is_valid_youtube_url``: valida se uma URL é do YouTube.
- ``download_youtube``: baixa áudio do YouTube via yt-dlp.
"""

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Padrão para validar URLs do YouTube (youtube.com e youtu.be)
YOUTUBE_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|embed/|v/|shorts/|live/)|youtu\.be/)"
    r"[a-zA-Z0-9_-]{11}"
    r"([&?]\S*)?$"
)


def is_valid_youtube_url(url: str) -> bool:
    """Valida se a URL é um link válido do YouTube.

    Args:
        url: URL a ser validada.

    Returns:
        True se a URL corresponde ao padrão do YouTube, False caso contrário.
    """
    return bool(YOUTUBE_URL_PATTERN.match(url.strip()))


def download_youtube(url: str, output_dir: Path) -> Path:
    """Baixa o áudio do YouTube na melhor qualidade usando yt-dlp.

    Diferentemente da versão anterior, agora baixa **apenas o áudio**
    (MP3 na melhor qualidade disponível), sem interação do usuário.
    Isso é mais rápido, consome menos banda e é suficiente para transcrição.

    Args:
        url: URL do YouTube.
        output_dir: Diretório de saída.

    Returns:
        Caminho para o arquivo de áudio baixado.

    Raises:
        RuntimeError: Se o download falhar.
        FileNotFoundError: Se o arquivo baixado não for encontrado.
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


def _find_downloaded_file(output_dir: Path) -> Path:
    """Procura o arquivo baixado no diretório de saída.

    Args:
        output_dir: Diretório onde o arquivo foi baixado.

    Returns:
        Caminho para o arquivo encontrado.

    Raises:
        FileNotFoundError: Se nenhum arquivo for encontrado.
    """
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
