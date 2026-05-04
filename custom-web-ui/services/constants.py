"""
Constantes compartilhadas — MIME types e conjuntos de extensões de arquivo.

Usado por:
- ``services.utils`` — função ``get_file_extension``
- ``services.generate.gerador`` — classificação de arquivos
- ``app.py`` — validação de extensões suportadas
"""

MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
}

IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
TEXT_EXTENSIONS: set[str] = {".txt", ".md", ".csv", ".json", ".xml", ".html"}
DOCUMENT_EXTENSIONS: set[str] = {".pdf", ".doc", ".docx"}
MEDIA_EXTENSIONS: set[str] = {
    ".mp4", ".webm", ".avi", ".mkv", ".mov", ".flv", ".wmv",
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".wma",
}

SUPPORTED_EXTENSIONS: set[str] = (
    IMAGE_EXTENSIONS | TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | MEDIA_EXTENSIONS
)
