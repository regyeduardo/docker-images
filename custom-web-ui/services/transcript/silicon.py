"""
Módulo de Transcrição — SiliconFlow.

Dependências: requests, services.utils
NÃO importa groq nem openai.

Suporte a chunking automático: se o áudio extraído ultrapassar 25 MB,
o arquivo é dividido em partes menores, cada parte é transcrita
individualmente, e os resultados são concatenados para garantir
100% de precisão na transcrição completa.
"""

import logging
import os
import tempfile
import time
from pathlib import Path

import requests

from services.utils import (
    dividir_audio_em_chunks,
    extrair_audio,
    get_file_extension,
    limpar_chunks,
)

logger = logging.getLogger(__name__)

# ── Configuração ───────────────────────────────────────────────────────────
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE_URL = os.environ.get(
    "SILICONFLOW_BASE_URL",
    "https://api.ap.siliconflow.com/v1",
)
SILICONFLOW_AUDIO_MODEL = os.environ.get(
    "SILICONFLOW_AUDIO_MODEL",
    "FunAudioLLM/SenseVoiceSmall",
)

_SILICONFLOW_TIMEOUT = 600
_SILICONFLOW_MAX_RETRIES = 3
_SILICONFLOW_RETRY_BACKOFF = [1, 4, 10]


def _build_url() -> str:
    """Monta a URL de transcrição a partir da base configurada."""
    base = SILICONFLOW_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/v1/audio/transcriptions"


def _parse_response(response: requests.Response) -> str:
    """Extrai o texto da resposta da API (JSON ou text/plain)."""
    ct = response.headers.get("Content-Type", "")
    logger.debug("SiliconFlow response Content-Type: %s", ct)
    logger.debug("SiliconFlow response status: %d", response.status_code)
    if "application/json" in ct:
        body = response.json()
        logger.debug("SiliconFlow response body (JSON): %s", str(body)[:1000])
        # Log the actual keys present in the response
        if isinstance(body, dict):
            logger.info("SiliconFlow response keys: %s", list(body.keys()))
        text = body.get("text", str(body))
        logger.info("SiliconFlow extracted text length: %d, text preview: %s", len(text), text[:200])
        return text
    logger.info("SiliconFlow response body (text/plain): %s", response.text[:500])
    return response.text


def _transcrever_chunk(
    chunk_path: Path,
    transcription_url: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Envia um único chunk de áudio para a API SiliconFlow e retorna a transcrição.

    Inclui lógica de retry para lidar com erros transitórios.
    """
    for attempt in range(1, _SILICONFLOW_MAX_RETRIES + 1):
        try:
            with open(chunk_path, "rb") as f:
                response = requests.post(
                    transcription_url,
                    headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}"},
                    files={"file": (chunk_path.name, f, "audio/wav")},
                    data={"model": SILICONFLOW_AUDIO_MODEL},
                    timeout=_SILICONFLOW_TIMEOUT,
                )

            if response.status_code == 200:
                transcription = _parse_response(response)
                logger.info(
                    "Chunk %d/%d transcrito: %d caracteres",
                    chunk_index,
                    total_chunks,
                    len(transcription),
                )
                return transcription

            if (
                response.status_code in {429, 500, 502, 503, 504}
                and attempt < _SILICONFLOW_MAX_RETRIES
            ):
                wait = _SILICONFLOW_RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "SiliconFlow retornou %d no chunk %d/%d (tentativa %d/%d). "
                    "Re-tentando em %ds...",
                    response.status_code,
                    chunk_index,
                    total_chunks,
                    attempt,
                    _SILICONFLOW_MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue

            detail = response.text[:500]
            logger.error(
                "SiliconFlow retornou %d no chunk %d/%d: %s",
                response.status_code,
                chunk_index,
                total_chunks,
                detail,
            )
            raise RuntimeError(
                f"SiliconFlow retornou status {response.status_code} "
                f"no chunk {chunk_index}/{total_chunks}: {detail}"
            )

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            if attempt < _SILICONFLOW_MAX_RETRIES:
                wait = _SILICONFLOW_RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "Erro de conexão no chunk %d/%d (tentativa %d/%d): %s. "
                    "Re-tentando em %ds...",
                    chunk_index,
                    total_chunks,
                    attempt,
                    _SILICONFLOW_MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"Falha de conexão com SiliconFlow no chunk {chunk_index}/{total_chunks} "
                f"após {_SILICONFLOW_MAX_RETRIES} tentativas: {exc}"
            ) from exc

    raise RuntimeError(
        f"Falha ao transcrever chunk {chunk_index}/{total_chunks} "
        f"com SiliconFlow após {_SILICONFLOW_MAX_RETRIES} tentativas."
    )


def transcrever(file_path: Path) -> str:
    """
    Transcreve o áudio de um arquivo de mídia usando a API do SiliconFlow.

    Fluxo:
      1. Extrai o áudio como WAV 16 kHz mono via ffmpeg
      2. Se o áudio ultrapassar 25 MB, divide em chunks menores
      3. Envia cada chunk via multipart/form-data para a API do SiliconFlow
      4. Concatena todas as transcrições em ordem
      5. Retorna o texto completo transcrito
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Caminho não é um arquivo: {file_path}")

    ext = get_file_extension(file_path.name)
    logger.info(
        "Transcrevendo mídia via SiliconFlow: %s (extensão: %s)", file_path.name, ext
    )

    if not SILICONFLOW_API_KEY:
        raise ValueError(
            "SiliconFlow API key não configurada. Defina SILICONFLOW_API_KEY no ambiente."
        )

    transcription_url = _build_url()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = Path(tmp.name)

    chunks_temp_dir: Path | None = None

    try:
        extrair_audio(file_path, audio_path)

        # Divide o áudio em chunks se necessário (limite de 25 MB da API)
        chunk_paths, chunks_temp_dir = dividir_audio_em_chunks(audio_path)
        total_chunks = len(chunk_paths)

        if total_chunks == 1:
            logger.info(
                "Enviando áudio para SiliconFlow (modelo: %s, tentativas: %d)...",
                SILICONFLOW_AUDIO_MODEL,
                _SILICONFLOW_MAX_RETRIES,
            )
            return _transcrever_chunk(
                chunk_paths[0],
                transcription_url,
                chunk_index=1,
                total_chunks=1,
            )

        # Múltiplos chunks: transcreve cada um e concatena
        logger.info(
            "Áudio dividido em %d chunks (limite de 25 MB da API SiliconFlow). "
            "Transcrevendo cada chunk individualmente...",
            total_chunks,
        )

        transcricoes: list[str] = []
        for i, chunk_path in enumerate(chunk_paths, start=1):
            logger.info(
                "Transcrevendo chunk %d/%d: %s (%.1f MB)...",
                i,
                total_chunks,
                chunk_path.name,
                chunk_path.stat().st_size / (1024 * 1024),
            )
            transcricao = _transcrever_chunk(
                chunk_path,
                transcription_url,
                chunk_index=i,
                total_chunks=total_chunks,
            )
            transcricoes.append(transcricao)

        # Junta todas as transcrições em ordem
        transcricao_completa = "\n\n".join(transcricoes)
        logger.info(
            "Transcrição SiliconFlow concluída (chunking): %d chunks, %d caracteres totais",
            total_chunks,
            len(transcricao_completa),
        )
        return transcricao_completa

    except Exception:
        logger.error("Erro na transcrição SiliconFlow", exc_info=True)
        raise
    finally:
        if audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.debug("Arquivo temporário removido: %s", audio_path.name)
        limpar_chunks(chunks_temp_dir)
