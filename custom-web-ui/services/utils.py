"""
Utilitários compartilhados — funções auxiliares para manipulação de arquivos.

Dependências:
- ``services.constants`` (indiretamente, via quem chama)
- ``ffmpeg`` (para extração de áudio)
"""

import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 600

# Limite máximo de arquivo de áudio para APIs de transcrição (25 MB)
MAX_AUDIO_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


def get_file_extension(filename: str) -> str:
    """Retorna a extensão do arquivo em minúsculas."""
    _, ext = os.path.splitext(filename)
    return ext.lower()


def extrair_audio(file_path: Path, dst: Path) -> None:
    """Extrai o áudio de *file_path* para *dst* como WAV 16 kHz mono via ffmpeg."""
    logger.info("Extraindo áudio: %s -> %s", file_path.name, dst.name)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(file_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(dst),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timeout ao extrair áudio com ffmpeg (limite de 10 min).")
    except FileNotFoundError:
        raise RuntimeError("ffmpeg não encontrado no sistema. Verifique a instalação.")

    if result.returncode != 0:
        stderr = result.stderr.strip()[:300]
        logger.error("ffmpeg falhou (código %d): %s", result.returncode, stderr)
        raise RuntimeError(
            f"Falha ao extrair áudio com ffmpeg (código {result.returncode}): {stderr}"
        )

    size_mb = dst.stat().st_size / (1024 * 1024)
    logger.info("Áudio extraído: %s (%.1f MB)", dst.name, size_mb)


def _obter_duracao_segundos(audio_path: Path) -> float:
    """
    Obtém a duração total do áudio em segundos usando ffprobe.

    Retorna 0.0 em caso de falha (para evitar quebra no fluxo de chunking).
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(audio_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as exc:
        logger.warning("Não foi possível obter duração do áudio: %s", exc)
    return 0.0


def _calcular_chunks(
    audio_path: Path, max_bytes: int = MAX_AUDIO_FILE_BYTES
) -> int:
    """
    Calcula em quantos chunks o áudio precisa ser dividido para que cada
    chunk fique abaixo de *max_bytes*.

    Retorna 1 se o arquivo já está abaixo do limite.
    """
    file_size = audio_path.stat().st_size
    if file_size <= max_bytes:
        return 1
    chunks = math.ceil(file_size / max_bytes)
    logger.info(
        "Áudio de %d MB será dividido em %d chunks (~%d MB cada)",
        file_size // (1024 * 1024),
        chunks,
        file_size // (1024 * 1024 * chunks),
    )
    return chunks


def _extrair_chunks_ffmpeg(
    audio_path: Path,
    num_chunks: int,
    output_dir: Path,
) -> list[Path]:
    """
    Divide o áudio WAV em *num_chunks* partes aproximadamente iguais usando ffmpeg.

    Retorna uma lista com os caminhos dos arquivos de chunk gerados.
    """
    duracao = _obter_duracao_segundos(audio_path)
    if duracao <= 0:
        raise RuntimeError(
            f"Não foi possível determinar a duração do áudio para dividi-lo em chunks: {audio_path}"
        )

    chunk_duration = duracao / num_chunks
    chunk_paths: list[Path] = []

    logger.info(
        "Dividindo áudio de %.1f s em %d chunks de ~%.1f s cada",
        duracao,
        num_chunks,
        chunk_duration,
    )

    for i in range(num_chunks):
        start_time = i * chunk_duration
        chunk_path = output_dir / f"chunk_{i:04d}.wav"

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ss",
            str(start_time),
            "-t",
            str(chunk_duration),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(chunk_path),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Timeout ao extrair chunk {i + 1}/{num_chunks} com ffmpeg."
            )

        if result.returncode != 0:
            stderr = result.stderr.strip()[:300]
            raise RuntimeError(
                f"Falha ao extrair chunk {i + 1}/{num_chunks} com ffmpeg "
                f"(código {result.returncode}): {stderr}"
            )

        chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
        logger.info(
            "Chunk %d/%d: %s (%.1f MB, início=%.1f s, duração=%.1f s)",
            i + 1,
            num_chunks,
            chunk_path.name,
            chunk_size_mb,
            start_time,
            chunk_duration,
        )
        chunk_paths.append(chunk_path)

    return chunk_paths


def dividir_audio_em_chunks(
    audio_path: Path,
    max_bytes: int = MAX_AUDIO_FILE_BYTES,
) -> tuple[list[Path], Path | None]:
    """
    Divide um arquivo de áudio WAV em chunks menores que *max_bytes*.

    Se o arquivo já estiver abaixo do limite, retorna ([audio_path], None).
    Caso contrário, cria chunks temporários em um diretório temporário.

    O caller DEVE chamar ``limpar_chunks(temp_dir)`` quando terminar de usar
    os chunks, para remover o diretório temporário.

    Args:
        audio_path: Caminho para o arquivo WAV a ser dividido.
        max_bytes: Tamanho máximo em bytes para cada chunk (default: 25 MB).

    Returns:
        Tupla (chunk_paths, temp_dir):
        - chunk_paths: lista de caminhos para os arquivos de chunk.
        - temp_dir: diretório temporário (None se não houve divisão).
    """
    file_size = audio_path.stat().st_size
    if file_size <= max_bytes:
        logger.debug(
            "Áudio já está abaixo do limite de %d MB: %s",
            max_bytes // (1024 * 1024),
            audio_path.name,
        )
        return [audio_path], None

    num_chunks = _calcular_chunks(audio_path, max_bytes)
    temp_dir = Path(tempfile.mkdtemp(prefix="audio_chunks_"))

    chunk_paths = _extrair_chunks_ffmpeg(audio_path, num_chunks, temp_dir)
    return chunk_paths, temp_dir


def limpar_chunks(temp_dir: Path | None) -> None:
    """
    Remove o diretório temporário de chunks criado por ``dividir_audio_em_chunks``.

    Args:
        temp_dir: Diretório temporário a ser removido (pode ser None).
    """
    if temp_dir is None:
        return
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)
    logger.debug("Diretório temporário de chunks removido: %s", temp_dir)
