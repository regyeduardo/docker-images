"""
Módulo de Geração — DeepSeek (OpenAI-compatible) para produção de Markdown.

Funções para:
- Extrair texto de documentos (PDF, DOCX)
- Montar conteúdo multimodal (texto + metadados de arquivos)
- Gerar markdown via DeepSeek API
"""

import logging
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI

from services.constants import (
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    MIME_MAP,
    TEXT_EXTENSIONS,
)
from services.utils import get_file_extension

logger = logging.getLogger(__name__)

# ── DeepSeek / OpenAI-compatible API configuration ─────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_TEXT_MODEL = os.environ.get("DEEPSEEK_TEXT_MODEL", "")


def _get_deepseek_client() -> OpenAI:
    """Retorna um cliente OpenAI configurado para DeepSeek (ou qualquer OpenAI-compatible)."""
    if not DEEPSEEK_API_KEY:
        raise ValueError(
            "DeepSeek API key não configurada. Defina DEEPSEEK_API_KEY no ambiente."
        )
    return OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )


# ── Extração de texto de documentos ────────────────────────────────────────


def extrair_texto_documento(file_path: Path) -> str:
    """Extrai texto de PDF, DOCX ou TXT."""
    ext = get_file_extension(file_path.name)
    try:
        if ext == ".pdf":
            from PyPDF2 import PdfReader

            reader = PdfReader(str(file_path))
            text_parts = []
            for page_num, page in enumerate(reader.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Página {page_num} ---\n{page_text}")
            return "\n\n".join(text_parts)
        elif ext in (".doc", ".docx"):
            from docx import Document

            doc = Document(str(file_path))
            text_parts = []
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)
            return "\n\n".join(text_parts)
        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception as e:
        logger.error("Erro ao extrair texto de %s: %s", file_path.name, str(e))
        return f"[Erro ao extrair texto de {file_path.name}: {e}]"


# ── Montagem de conteúdo multimodal ────────────────────────────────────────


def montar_conteudo_multimodal(
    file_path: Path,
    prompt: str,
    language: Optional[str] = None,
) -> list:
    """
    Constrói o array de content parts para enviar ao DeepSeek.

    Para imagens: envia metadados + nome do arquivo.
    Para texto/documentos: extrai e envia como texto.
    Para mídia (vídeo/áudio): usa o TRANSCRIPTION_PROVIDER definido
    no ambiente (compose.yml) para escolher o módulo de transcrição.

    Args:
        file_path: Caminho para o arquivo.
        prompt: Instruções adicionais do usuário.
        language: Código do idioma para transcrição (ex: "pt", "en", "es").
                  Atualmente usado apenas pelo provedor Deepgram.
    """
    ext = get_file_extension(file_path.name)
    file_size_mb = file_path.stat().st_size / (1024 * 1024)
    mime = MIME_MAP.get(ext, "application/octet-stream")

    content_parts = []

    if ext in IMAGE_EXTENSIONS:
        content_parts.append(
            {
                "type": "text",
                "text": (
                    f"# Arquivo de Imagem: {file_path.name}\n"
                    f"- **Tamanho**: {file_size_mb:.1f} MB\n"
                    f"- **Tipo**: {mime}\n"
                    f"- **Extensão**: {ext}\n\n"
                    f"Uma imagem foi enviada. Analise o nome do arquivo e metadados para inferir o contexto."
                ),
            }
        )

    elif ext in TEXT_EXTENSIONS:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text_content = f.read()
        content_parts.append(
            {
                "type": "text",
                "text": (f"# Conteúdo do Arquivo: {file_path.name}\n\n{text_content}"),
            }
        )

    elif ext in DOCUMENT_EXTENSIONS:
        text_content = extrair_texto_documento(file_path)
        content_parts.append(
            {
                "type": "text",
                "text": (
                    f"# Conteúdo do Documento: {file_path.name}\n\n{text_content}"
                ),
            }
        )

    elif ext in MEDIA_EXTENSIONS:
        # Usa o TRANSCRIPTION_PROVIDER definido no ambiente (compose.yml)
        from app import TRANSCRIPTION_PROVIDER

        logger.info(
            "Arquivo de mídia detectado. Transcrevendo via %s...",
            TRANSCRIPTION_PROVIDER,
        )

        # Import DINÂMICO: só importa o módulo necessário
        if TRANSCRIPTION_PROVIDER == "groq":
            from services.transcript.groq import transcrever as transcrever_audio

            transcricao = transcrever_audio(file_path)
        elif TRANSCRIPTION_PROVIDER == "deepgram":
            from services.transcript.deepgram import transcrever as transcrever_audio

            transcricao = transcrever_audio(file_path, language=language)
        else:
            from services.transcript.silicon import transcrever as transcrever_audio

            transcricao = transcrever_audio(file_path)

        logger.info(
            "Transcrição recebida do provedor: length=%d, preview='%s'",
            len(transcricao),
            transcricao[:300],
        )

        content_parts.append(
            {
                "type": "text",
                "text": (
                    f"# Transcrição do Arquivo de Mídia: {file_path.name}\n"
                    f"- **Tamanho**: {file_size_mb:.1f} MB\n"
                    f"- **Tipo**: {mime}\n"
                    f"- **Extensão**: {ext}\n\n"
                    f"## Transcrição Completa\n\n"
                    f"{transcricao}"
                ),
            }
        )

    if prompt:
        content_parts.append(
            {
                "type": "text",
                "text": f"# Instruções Adicionais do Usuário\n{prompt}",
            }
        )

    return content_parts


# ── Pós-processamento pós-LLM ──────────────────────────────────────────────


def _substituir_literal_backslash_n(mermaid_code: str) -> str:
    """
    Substitui ocorrências de \\n literal (dois caracteres: backslash + n)
    por quebras de linha reais dentro de código Mermaid.

    LLMs frequentemente geram `\\n` em vez de quebras de linha reais,
    especialmente dentro de strings JSON (ex: campo `diagrama` no agente Provas).
    Isso quebra a renderização do Mermaid.
    """
    return mermaid_code.replace("\\n", "\n")


def _sanitizar_codigo_mermaid(mermaid_code: str) -> str:
    """
    Aplica correções adicionais em código Mermaid gerado por LLM.

    1. Substitui \\n literal por quebras de linha reais.
    2. Corrige aspas desbalanceadas (comum em output de LLM):
       - [""texto""] → ["texto"] (simétrico)
       - [""texto"]  → ["texto"] (abertura duplicada)
       - ["texto""]  → ["texto"] (fechamento duplicado)
       - Mesmo para labels { }
    3. Remove linhas em branco consecutivas.
    """
    import re

    # Passo 1: \\n literal → quebras de linha reais
    code = _substituir_literal_backslash_n(mermaid_code)

    # Passo 2: Corrige aspas desbalanceadas em labels Mermaid
    # [^"\n] impede match cross-line (labels Mermaid são single-line)
    # 2a: Simétricas [""texto""] → ["texto"]
    code = re.sub(r'(\w+)\[""([^"\n]*)""\]', r'\1["\2"]', code)
    code = re.sub(r'(\w+)\{""([^"\n]*)""\}', r'\1{"\2"}', code)
    # 2b: Abertura duplicada [""texto"] → ["texto"]
    code = re.sub(r'(\w+)\[""([^"\n]*)"\]', r'\1["\2"]', code)
    code = re.sub(r'(\w+)\{""([^"\n]*)"\}', r'\1{"\2"}', code)
    # 2c: Fechamento duplicado ["texto""] → ["texto"]
    code = re.sub(r'(\w+)\["([^"\n]*?)""\]', r'\1["\2"]', code)
    code = re.sub(r'(\w+)\{"([^"\n]*?)""\}', r'\1{"\2"}', code)

    # Passo 3: Remove linhas em branco consecutivas (deixa no máximo 1)
    code = re.sub(r"\n\s*\n", "\n", code)

    return code.strip()


def pos_processar_markdown(markdown: str) -> str:
    """
    Pós-processa o Markdown gerado pelo LLM para corrigir problemas comuns
    de sintaxe Mermaid antes de enviar ao frontend.

    Correções aplicadas:
    - Substitui \\n literal por quebras de linha reais dentro de blocos ```mermaid
    - Remove espaços em excesso
    - Remove linhas em branco consecutivas

    Args:
        markdown: String Markdown bruta gerada pelo LLM.

    Returns:
        String Markdown com as correções aplicadas.
    """
    import re

    # Padrão: encontra blocos ```mermaid ... ```
    pattern = re.compile(
        r"(```mermaid\s*\n?)(.*?)(\n?```)",
        re.DOTALL,
    )

    def _replacer(match):
        prefix = match.group(1)
        code = match.group(2)
        suffix = match.group(3)
        corrected = _sanitizar_codigo_mermaid(code)
        return f"{prefix}{corrected}{suffix}"

    return pattern.sub(_replacer, markdown)


# ── Geração de Markdown via DeepSeek ───────────────────────────────────────


def gerar_markdown(
    content_parts: list,
    system_prompt: str,
    model: Optional[str] = None,
) -> str:
    """
    Gera conteúdo Markdown usando DeepSeek API.

    Args:
        content_parts: Lista de dicionários com o conteúdo multimodal.
        system_prompt: Prompt de sistema para o agente (Educador ou Resumidor).
        model: Nome do modelo (opcional, usa DEEPSEEK_TEXT_MODEL como fallback).

    Returns:
        String com o Markdown gerado.
    """
    try:
        client = _get_deepseek_client()

        kwargs = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
            ],
            "max_tokens": 16384,
            "timeout": 600,
        }

        model_name = model or DEEPSEEK_TEXT_MODEL
        if model_name:
            kwargs["model"] = model_name

        response = client.chat.completions.create(**kwargs)
        markdown_raw = response.choices[0].message.content
        return pos_processar_markdown(markdown_raw)
    except Exception as e:
        logger.error("Erro ao gerar conteúdo com DeepSeek: %s", str(e))
        raise
