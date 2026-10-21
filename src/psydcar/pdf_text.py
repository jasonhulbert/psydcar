"""Extract page-bound text from PDFs without performing OCR."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO


class PdfTextError(Exception):
    """A PDF cannot be read or its text cannot be extracted."""


@dataclass(frozen=True)
class PdfTextPage:
    number: int  # One-based PDF page number.
    text: str


def _reader(data: bytes):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PdfTextError("PDF support requires pypdf. Install Psydcar with 'python -m pip install -e .'.") from exc
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfTextError("PDF is encrypted and needs a password.")
        return reader
    except PdfTextError:
        raise
    except Exception as exc:
        raise PdfTextError(f"PDF cannot be opened: {exc}") from exc


def extract_pdf_pages(data: bytes) -> tuple[list[PdfTextPage], list[int], list[tuple[int, str]]]:
    """Return searchable pages, pages without text, and page extraction errors."""
    reader = _reader(data)
    pages: list[PdfTextPage] = []
    no_text: list[int] = []
    errors: list[tuple[int, str]] = []
    try:
        count = len(reader.pages)
    except Exception as exc:
        raise PdfTextError(f"PDF page list cannot be read: {exc}") from exc
    for number in range(1, count + 1):
        try:
            text = reader.pages[number - 1].extract_text() or ""
        except Exception as exc:
            errors.append((number, str(exc)))
            continue
        if text.strip():
            pages.append(PdfTextPage(number, text))
        else:
            no_text.append(number)
    return pages, no_text, errors


def extract_pdf_page(data: bytes, number: int) -> str:
    """Extract current text from one one-based PDF page."""
    reader = _reader(data)
    try:
        if number < 1 or number > len(reader.pages):
            raise PdfTextError(f"PDF no longer has page {number}.")
        text = reader.pages[number - 1].extract_text() or ""
    except PdfTextError:
        raise
    except Exception as exc:
        raise PdfTextError(f"PDF page {number} cannot be read: {exc}") from exc
    if not text.strip():
        raise PdfTextError(f"PDF page {number} has no searchable text.")
    return text
