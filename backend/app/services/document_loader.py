import csv
from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader

from app.services.text_processor import clean_text


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}


class DocumentLoadError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    page_number: int | None = None


def extract_document(path: Path) -> list[ExtractedPage]:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentLoadError(f"Unsupported document format: {extension or 'unknown'}")
    try:
        if extension == ".pdf":
            pages = [
                ExtractedPage(clean_text(page.extract_text() or ""), index)
                for index, page in enumerate(PdfReader(path).pages, start=1)
            ]
        elif extension == ".docx":
            document = DocxDocument(path)
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            for table in document.tables:
                text += "\n" + "\n".join(
                    " | ".join(cell.text for cell in row.cells) for row in table.rows
                )
            pages = [ExtractedPage(clean_text(text))]
        elif extension == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = csv.reader(file)
                pages = [ExtractedPage(clean_text("\n".join(" | ".join(row) for row in rows)))]
        else:
            pages = [ExtractedPage(clean_text(path.read_text(encoding="utf-8-sig")))]
    except Exception as exc:
        raise DocumentLoadError(f"Could not extract text from {path.name}: {exc}") from exc

    return [page for page in pages if page.text]
