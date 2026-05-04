"""
Módulo de Transcrição — Deepgram (REST API).

Usa requests diretamente (sem SDK deepgram) para chamar a API REST do Deepgram
no endpoint /v1/listen, enviando o áudio como raw binary.

Compatível com os modelos nova-2 e nova-3.

Suporte a chunking automático: se o áudio extraído ultrapassar 100 MB,
o arquivo é dividido em partes menores, cada parte é transcrita
individualmente, e os resultados são concatenados para garantir
100% de precisão na transcrição completa.
"""

import json
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
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
DEEPGRAM_BASE_URL = os.environ.get(
    "DEEPGRAM_BASE_URL",
    "https://api.deepgram.com",
)
DEEPGRAM_MODEL = os.environ.get(
    "DEEPGRAM_MODEL",
    "nova-3",
)

_DEEPGRAM_TIMEOUT = 600
_DEEPGRAM_MAX_RETRIES = 3
_DEEPGRAM_RETRY_BACKOFF = [1, 4, 10]

# Deepgram pre-recorded API tem limite de 100 MB para upload direto
_DEEPGRAM_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB


def _build_url() -> str:
    """
    Monta a URL REST de transcrição a partir da base configurada.
    Usa a API v1/listen — compatível com nova-2, nova-3 e todos os modelos.
    """
    base = DEEPGRAM_BASE_URL.rstrip("/")

    # Remove sufixo /v1/listen ou /v1 se já vier na base_url
    for suffix in ("/v1/listen", "/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break

    return (
        f"{base}/v1/listen"
        f"?model={DEEPGRAM_MODEL}"
        f"&smart_format=true"
        f"&language=pt"
        f"&punctuate=true"
    )


def _parse_response(response: requests.Response) -> str:
    """
    Extrai o transcript da resposta JSON da Deepgram v1/listen.

    Formato da resposta:
    {
      "results": {
        "channels": [{
          "alternatives": [{
            "transcript": "...",
            "confidence": 0.99
          }]
        }]
      }
    }
    """
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Resposta inválida da Deepgram (não-JSON): {response.text[:500]}"
        ) from exc

    # Verifica erro na resposta
    if "err_code" in body or "err_msg" in body:
        err_code = body.get("err_code", body.get("code", "desconhecido"))
        err_msg = body.get("err_msg", body.get("message", str(body)[:500]))
        raise RuntimeError(
            f"Deepgram erro {err_code}: {err_msg}"
        )

    try:
        transcript = body["results"]["channels"][0]["alternatives"][0]["transcript"]
        return transcript.strip()
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Resposta Deepgram inesperada: %s", body)
        raise RuntimeError(
            f"Não foi possível extrair o transcript da resposta Deepgram: {body}"
        ) from exc


def _transcrever_chunk(
    chunk_path: Path,
    transcription_url: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Envia um único chunk de áudio para a Deepgram via REST API e retorna a transcrição.

    Envia o arquivo WAV como raw binary no body da requisição POST,
    com Content-Type: audio/wav, conforme a documentação da Deepgram.

    Inclui lógica de retry para lidar com erros transitórios.
    """
    for attempt in range(1, _DEEPGRAM_MAX_RETRIES + 1):
        try:
            with open(chunk_path, "rb") as f:
                audio_data = f.read()

            response = requests.post(
                transcription_url,
                headers={
                    "Authorization": f"Token {DEEPGRAM_API_KEY}",
                    "Content-Type": "audio/wav",
                },
                data=audio_data,
                timeout=_DEEPGRAM_TIMEOUT,
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
                    f"Deepgram API key inválida (401). "
                    f"Verifique a variável DEEPGRAM_API_KEY. Detalhe: {detail}"
                )

            # 413 — Payload Too Large — o chunk pode estar acima do limite
            if response.status_code == 413:
                detail = response.text[:500]
                raise RuntimeError(
                    f"Deepgram retornou 413 (Payload Too Large) no chunk "
                    f"{chunk_index}/{total_chunks}. O arquivo excede o limite "
                    f"de 100 MB. Detalhe: {detail}"
                )

            # 429 ou 5xx — retentável
            if (
                response.status_code in {429, 500, 502, 503, 504}
                and attempt < _DEEPGRAM_MAX_RETRIES
            ):
                wait = _DEEPGRAM_RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "Deepgram retornou %d no chunk %d/%d (tentativa %d/%d). "
                    "Re-tentando em %ds...",
                    response.status_code,
                    chunk_index,
                    total_chunks,
                    attempt,
                    _DEEPGRAM_MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue

            detail = response.text[:500]
            logger.error(
                "Deepgram retornou %d no chunk %d/%d: %s",
                response.status_code,
                chunk_index,
                total_chunks,
                detail,
            )
            raise RuntimeError(
                f"Deepgram retornou status {response.status_code} "
                f"no chunk {chunk_index}/{total_chunks}: {detail}"
            )

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            if attempt < _DEEPGRAM_MAX_RETRIES:
                wait = _DEEPGRAM_RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "Erro de conexão Deepgram no chunk %d/%d (tentativa %d/%d): %s. "
                    "Re-tentando em %ds...",
                    chunk_index,
                    total_chunks,
                    attempt,
                    _DEEPGRAM_MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"Falha de conexão com Deepgram no chunk {chunk_index}/{total_chunks} "
                f"após {_DEEPGRAM_MAX_RETRIES} tentativas: {exc}"
            ) from exc

    raise RuntimeError(
        f"Falha ao transcrever chunk {chunk_index}/{total_chunks} "
        f"com Deepgram após {_DEEPGRAM_MAX_RETRIES} tentativas."
    )


def transcrever(file_path: Path) -> str:
    """
    Transcreve o áudio de um arquivo de mídia usando a API REST do Deepgram (v1/listen).

    Fluxo:
      1. Extrai o áudio como WAV 16 kHz mono via ffmpeg
      2. Se o áudio ultrapassar 100 MB, divide em chunks menores
      3. Para cada chunk, envia o WAV como raw binary via POST para a Deepgram
      4. Concatena todas as transcrições em ordem
      5. Retorna o texto completo transcrito
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Caminho não é um arquivo: {file_path}")

    if not DEEPGRAM_API_KEY:
        raise ValueError(
            "Deepgram API key não configurada. Defina DEEPGRAM_API_KEY no ambiente."
        )

    ext = get_file_extension(file_path.name)
    logger.info(
        "Transcrevendo via Deepgram REST API: %s (extensão: %s)",
        file_path.name,
        ext,
    )

    transcription_url = _build_url()
    logger.info(
        "URL Deepgram: %s (modelo: %s)",
        transcription_url,
        DEEPGRAM_MODEL,
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = Path(tmp.name)

    chunks_temp_dir: Optional[Path] = None

    try:
        extrair_audio(file_path, audio_path)

        chunk_paths, chunks_temp_dir = dividir_audio_em_chunks(
            audio_path, max_bytes=_DEEPGRAM_MAX_FILE_BYTES
        )
        total_chunks = len(chunk_paths)

        if total_chunks == 1:
            logger.info(
                "Enviando áudio para Deepgram (modelo: %s, tentativas máx: %d)...",
                DEEPGRAM_MODEL,
                _DEEPGRAM_MAX_RETRIES,
            )
            return _transcrever_chunk(
                chunk_paths[0],
                transcription_url,
                chunk_index=1,
                total_chunks=1,
            )

        logger.info(
            "Áudio dividido em %d chunks (limite de 100 MB da API Deepgram). "
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

        # Filtra chunks vazios (silêncio) antes de juntar
        transcricao_completa = "\n\n".join(t for t in transcricoes if t)
        logger.info(
            "Transcrição Deepgram concluída: %d chunks, %d caracteres totais",
            total_chunks,
            len(transcricao_completa),
        )
        return transcricao_completa

    except Exception:
        logger.error("Erro na transcrição Deepgram", exc_info=True)
        raise
    finally:
        if audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.debug("Arquivo temporário removido: %s", audio_path.name)
        limpar_chunks(chunks_temp_dir)
