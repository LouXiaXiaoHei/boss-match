"""Resume file parser — extract text from PDF, DOCX, TXT, MD files."""

import io
import logging
import os

log = logging.getLogger(__name__)


def parse_resume_file(filename: str, file_bytes: bytes) -> str:
    """根据文件扩展名选择解析器，提取纯文本。

    Args:
        filename: Original filename with extension (e.g. "resume.pdf")
        file_bytes: Raw file content as bytes

    Returns:
        Extracted plain text content

    Raises:
        ValueError: Unsupported file format or extraction failure
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return _parse_pdf(file_bytes)
    if ext in (".docx", ".doc"):
        if ext == ".doc":
            log.warning(".doc 格式支持有限，建议转换为 .docx")
        return _parse_docx(file_bytes)
    if ext == ".txt":
        return file_bytes.decode("utf-8", errors="ignore")
    if ext == ".md":
        return file_bytes.decode("utf-8", errors="ignore")
    raise ValueError(f"不支持的文件格式: {ext}")


def _parse_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF using pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    if not pages:
        raise ValueError("PDF 文件无法提取文本（可能是扫描件）")
    return "\n\n".join(pages)


def _parse_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        raise ValueError("DOCX 文件无法提取文本")
    return "\n\n".join(paragraphs)
