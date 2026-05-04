"""
``services`` — pacote principal da aplicação.

Subpacotes:
- ``services.generate`` — geração de conteúdo via DeepSeek
- ``services.transcript`` — transcrição de áudio/vídeo (Groq, SiliconFlow)

Módulos:
- ``services.constants`` — MIME types e extensões de arquivo
- ``services.utils`` — utilitários (extração de áudio, extensão de arquivo)
"""

from services.generate.gerador import (
    extrair_texto_documento,
    gerar_markdown,
    montar_conteudo_multimodal,
)

__all__ = [
    "gerar_markdown",
    "extrair_texto_documento",
    "montar_conteudo_multimodal",
]
