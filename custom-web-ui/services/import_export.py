"""
Módulo de Import/Export — lógica de manipulação de arquivos ZIP e Markdown.

Funções:
- ``create_export_zip``: cria um ZIP em memória com conteudo.md e opcional questoes.json.
- ``extract_import_data``: extrai dados de um arquivo .md ou .zip enviado pelo usuário.
"""

import io
import json as json_lib
import logging
import zipfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def create_export_zip(
    markdown: str,
    questions: Optional[dict[str, Any]] = None,
    filename_base: str = "conteudo",
) -> io.BytesIO:
    """Cria um arquivo ZIP em memória contendo o markdown e opcionalmente as questões.

    O ZIP gerado contém:
    - ``conteudo.md``: o markdown principal.
    - ``questoes.json``: as questões (se fornecidas).

    Args:
        markdown: Conteúdo markdown a ser incluído.
        questions: Dicionário com as questões (opcional).
        filename_base: Nome base para o arquivo ZIP (sem extensão).

    Returns:
        Objeto BytesIO contendo o ZIP gerado.
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Adiciona o markdown
        zf.writestr("conteudo.md", markdown)

        # Adiciona as questões, se houver
        if questions and questions.get("questions"):
            json_bytes = json_lib.dumps(
                questions, ensure_ascii=False, indent=2
            ).encode("utf-8")
            zf.writestr("questoes.json", json_bytes)

    zip_buffer.seek(0)
    logger.info(
        "ZIP criado: %d bytes, %squestoes incluídas",
        zip_buffer.getbuffer().nbytes,
        "" if questions else "sem ",
    )
    return zip_buffer


def extract_import_data(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Extrai dados de um arquivo .md ou .zip enviado para importação.

    Para arquivos .md:
        Retorna ``{"markdown": "<conteúdo>"}``.

    Para arquivos .zip:
        Extrai o ZIP e procura por ``conteudo.md`` (obrigatório) e
        ``questoes.json`` (opcional). Retorna:
        ``{"markdown": "...", "questions": {...} | None}``.

    Args:
        file_bytes: Conteúdo bruto do arquivo enviado.
        filename: Nome original do arquivo (para detectar extensão).

    Returns:
        Dicionário com os dados extraídos.

    Raises:
        ValueError: Se o formato for inválido, ZIP corrompido, ou
                    ``conteudo.md`` não for encontrado no ZIP.
    """
    ext = Path(filename).suffix.lower()

    if ext == ".md":
        markdown = file_bytes.decode("utf-8", errors="replace")
        logger.info("Import .md: %d caracteres", len(markdown))
        return {"markdown": markdown}

    elif ext == ".zip":
        return _extract_from_zip(file_bytes)

    else:
        raise ValueError(
            f"Formato não suportado: '{ext}'. Aceitos apenas .md e .zip."
        )


def _extract_from_zip(file_bytes: bytes) -> dict[str, Any]:
    """Extrai conteudo.md e opcionalmente questoes.json de um arquivo ZIP.

    Args:
        file_bytes: Conteúdo bruto do arquivo ZIP.

    Returns:
        Dicionário com ``markdown`` e opcionalmente ``questions``.

    Raises:
        ValueError: Se o ZIP for inválido ou não contiver ``conteudo.md``.
    """
    result: dict[str, Any] = {"markdown": "", "questions": None}

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            # Lista arquivos para debug
            file_list = zf.namelist()
            logger.info("Arquivos no ZIP: %s", file_list)

            # Verifica se conteudo.md existe
            if "conteudo.md" not in file_list:
                raise ValueError(
                    "ZIP inválido: arquivo 'conteudo.md' não encontrado. "
                    f"Arquivos encontrados: {', '.join(file_list)}"
                )

            # Lê o markdown
            with zf.open("conteudo.md") as f:
                result["markdown"] = f.read().decode("utf-8", errors="replace")

            # Lê questoes.json (opcional)
            if "questoes.json" in file_list:
                with zf.open("questoes.json") as f:
                    try:
                        questions_data = json_lib.loads(
                            f.read().decode("utf-8", errors="replace")
                        )
                        if isinstance(questions_data, dict) and questions_data.get("questions"):
                            result["questions"] = questions_data
                            logger.info(
                                "Questões restauradas do ZIP: %d questões",
                                len(questions_data["questions"]),
                            )
                    except json_lib.JSONDecodeError:
                        logger.warning("questoes.json inválido no ZIP, ignorando")

            logger.info(
                "Import ZIP: %d caracteres de markdown, questions=%s",
                len(result["markdown"]),
                "sim" if result["questions"] else "não",
            )
            return result

    except zipfile.BadZipFile:
        raise ValueError("Arquivo ZIP corrompido ou inválido.")
