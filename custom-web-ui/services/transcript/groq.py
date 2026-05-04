"""
Módulo de Transcrição — Groq (Whisper).

Usa requests diretamente (sem SDK groq) para chamar a API REST do Groq.

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
from typing import Optional

import requests

from services.utils import (
    dividir_audio_em_chunks,
    extrair_audio,
    get_file_extension,
    limpar_chunks,
)

logger = logging.getLogger(__name__)

# ── Configuração ───────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get(
    "GROQ_BASE_URL",
    "https://api.groq.com",
)
GROQ_AUDIO_MODEL = os.environ.get(
    "GROQ_AUDIO_MODEL",
    "whisper-large-v3",
)

_GROQ_TIMEOUT = 600
_GROQ_MAX_RETRIES = 3
_GROQ_RETRY_BACKOFF = [1, 4, 10]


def _build_url() -> str:
    """Monta a URL de transcrição a partir da base configurada."""
    base = GROQ_BASE_URL.rstrip("/")
    return f"{base}/openai/v1/audio/transcriptions"


def _parse_response(response: requests.Response) -> str:
    """Extrai o texto da resposta da API (JSON ou text/plain)."""
    ct = response.headers.get("Content-Type", "")
    if "application/json" in ct:
        body = response.json()
        return body.get("text", str(body))
    return response.text


def _transcrever_chunk(
    chunk_path: Path,
    transcription_url: str,
    effective_api_key: str,
    effective_model: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Envia um único chunk de áudio para a API Groq e retorna a transcrição.

    Inclui lógica de retry para lidar com erros transitórios.
    """
    for attempt in range(1, _GROQ_MAX_RETRIES + 1):
        try:
            with open(chunk_path, "rb") as f:
                response = requests.post(
                    transcription_url,
                    headers={"Authorization": f"Bearer {effective_api_key}"},
                    files={"file": (chunk_path.name, f, "audio/wav")},
                    data={"model": effective_model},
                    timeout=_GROQ_TIMEOUT,
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

            # 401 — Unauthorized (chave inválida) — não retentável
            if response.status_code == 401:
                detail = response.text[:500]
                raise RuntimeError(
                    f"Groq API key inválida (401). "
                    f"Verifique a variável GROQ_API_KEY. Detalhe: {detail}"
                )

            # 429 ou 5xx — retentável
            if (
                response.status_code in {429, 500, 502, 503, 504}
                and attempt < _GROQ_MAX_RETRIES
            ):
                wait = _GROQ_RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "Groq retornou %d no chunk %d/%d (tentativa %d/%d). "
                    "Re-tentando em %ds...",
                    response.status_code,
                    chunk_index,
                    total_chunks,
                    attempt,
                    _GROQ_MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue

            detail = response.text[:500]
            logger.error(
                "Groq retornou %d no chunk %d/%d: %s",
                response.status_code,
                chunk_index,
                total_chunks,
                detail,
            )
            raise RuntimeError(
                f"Groq retornou status {response.status_code} "
                f"no chunk {chunk_index}/{total_chunks}: {detail}"
            )

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            if attempt < _GROQ_MAX_RETRIES:
                wait = _GROQ_RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "Erro de conexão Groq no chunk %d/%d (tentativa %d/%d): %s. "
                    "Re-tentando em %ds...",
                    chunk_index,
                    total_chunks,
                    attempt,
                    _GROQ_MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"Falha de conexão com Groq no chunk {chunk_index}/{total_chunks} "
                f"após {_GROQ_MAX_RETRIES} tentativas: {exc}"
            ) from exc

    raise RuntimeError(
        f"Falha ao transcrever chunk {chunk_index}/{total_chunks} "
        f"com Groq após {_GROQ_MAX_RETRIES} tentativas."
    )


def transcrever(
    file_path: Path,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Transcreve o áudio de um arquivo de mídia usando a API REST do Groq (Whisper).

    Pode receber api_key e model explicitamente (vindos do frontend).
    Se não forem fornecidos, usa as variáveis de ambiente GROQ_API_KEY e GROQ_AUDIO_MODEL.

    Faz a requisição via multipart/form-data diretamente com requests,
    sem depender do SDK groq.

    Se o áudio extraído ultrapassar 25 MB, o arquivo é automaticamente dividido
    em chunks menores, cada chunk é transcrito individualmente, e os resultados
    são concatenados para garantir 100% de precisão.
    """
    effective_api_key = (api_key or "").strip() or (GROQ_API_KEY or "").strip()
    effective_model = (model or "").strip() or (GROQ_AUDIO_MODEL or "").strip()

    if not effective_api_key:
        raise ValueError(
            "Groq API key não configurada. "
            "Defina GROQ_API_KEY no ambiente ou forneça uma chave."
        )

    masked = effective_api_key[:6] + "****" + effective_api_key[-4:]
    logger.info("Groq API key em uso: %s", masked)

    if not file_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Caminho não é um arquivo: {file_path}")

    ext = get_file_extension(file_path.name)
    logger.info("Transcrevendo mídia via Groq: %s (extensão: %s)", file_path.name, ext)

    transcription_url = _build_url()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = Path(tmp.name)

    chunks_temp_dir: Path | None = None

    try:
        extrair_audio(file_path, audio_path)

        # Divide o áudio em chunks se necessário (limite de 25 MB da API Groq)
        chunk_paths, chunks_temp_dir = dividir_audio_em_chunks(audio_path)
        total_chunks = len(chunk_paths)

        if total_chunks == 1:
            logger.info(
                "Enviando áudio para Groq (modelo: %s, tentativas máx: %d)...",
                effective_model,
                _GROQ_MAX_RETRIES,
            )
            return _transcrever_chunk(
                chunk_paths[0],
                transcription_url,
                effective_api_key,
                effective_model,
                chunk_index=1,
                total_chunks=1,
            )

        # Múltiplos chunks: transcreve cada um e concatena
        logger.info(
            "Áudio dividido em %d chunks (limite de 25 MB da API Groq). "
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
                effective_api_key,
                effective_model,
                chunk_index=i,
                total_chunks=total_chunks,
            )
            transcricoes.append(transcricao)

        # Junta todas as transcrições em ordem
        transcricao_completa = "\n\n".join(transcricoes)
        logger.info(
            "Transcrição Groq concluída (chunking): %d chunks, %d caracteres totais",
            total_chunks,
            len(transcricao_completa),
        )
        return transcricao_completa

    except Exception:
        logger.error("Erro na transcrição Groq", exc_info=True)
        raise
    finally:
        if audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.debug("Arquivo temporário removido: %s", audio_path.name)
        limpar_chunks(chunks_temp_dir)
