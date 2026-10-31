import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.services.chunking import chunk_text
from app.services.database import Database
from app.services.document_loader import DocumentLoadError, extract_document
from app.services.embeddings import EmbeddingService
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


class DuplicateDocumentError(ValueError):
    pass


class DocumentService:
    def __init__(
        self,
        database: Database,
        vector_store: VectorStore,
        embeddings: EmbeddingService,
        settings_provider,
    ):
        self.database = database
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.settings_provider = settings_provider

    @staticmethod
    def safe_filename(filename: str) -> str:
        basename = Path(filename).name
        cleaned = re.sub(r"[^\w .()-]", "_", basename, flags=re.UNICODE).strip(" .")
        return cleaned[:180] or "uploaded-document"

    def register_upload(self, filename: str, content: bytes) -> dict:
        settings: Settings = self.settings_provider()
        safe_name = self.safe_filename(filename)
        content_hash = hashlib.sha256(content).hexdigest()
        duplicate = self.database.find_document_by_hash(content_hash)
        if duplicate:
            raise DuplicateDocumentError(
                f"{safe_name} matches an existing document: {duplicate['filename']}"
            )

        extension = Path(safe_name).suffix.lower()
        upload_dir = settings.resolve_path(settings.upload_path)
        upload_dir.mkdir(parents=True, exist_ok=True)
        document_id = str(uuid4())
        stored_path = upload_dir / f"{document_id}{extension}"
        stored_path.write_bytes(content)
        record = {
            "document_id": document_id,
            "filename": safe_name,
            "file_type": extension.lstrip(".").upper(),
            "size_bytes": len(content),
            "content_hash": content_hash,
            "storage_path": str(stored_path),
            "status": "processing",
            "chunk_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }
        try:
            self.database.create_document(record)
        except Exception:
            stored_path.unlink(missing_ok=True)
            raise
        result = self.database.get_document(document_id)
        if result is None:
            raise RuntimeError("Document disappeared after upload registration")
        return result

    def ingest(self, filename: str, content: bytes) -> dict:
        document = self.register_upload(filename, content)
        return self.process_document(document["document_id"])

    def process_document(self, document_id: str) -> dict | None:
        document = self.database.get_document(document_id)
        path = self.database.get_document_storage_path(document_id)
        if document is None or path is None:
            return None
        try:
            self._process(document_id, path, document["filename"], document["created_at"])
        except Exception as exc:
            logger.exception("Document ingestion failed for %s", document["filename"])
            self.database.update_document(document_id, status="failed", error=str(exc)[:500])
        return self.database.get_document(document_id)

    def _process(self, document_id: str, path: Path, filename: str, uploaded_at: str) -> None:
        settings: Settings = self.settings_provider()
        self.vector_store.delete_document(document_id)
        pages = extract_document(path)
        if not pages:
            raise DocumentLoadError("No readable text was found in this document.")
        chunks = []
        for page in pages:
            for chunk in chunk_text(
                page.text,
                settings.chunk_size,
                settings.chunk_overlap,
                page.page_number,
            ):
                chunks.append({
                    "chunk_id": f"{document_id}_{len(chunks)}",
                    "text": chunk.text,
                    "filename": filename,
                    "chunk_index": len(chunks),
                    "source_type": path.suffix.lower().lstrip("."),
                    "page_number": chunk.page_number,
                    "upload_timestamp": uploaded_at,
                })
        if not chunks:
            raise DocumentLoadError("The document did not contain text to index.")
        vectors = self.embeddings.embed_texts([chunk["text"] for chunk in chunks])
        self.vector_store.add_chunks(document_id, chunks, vectors)
        self.database.update_document(document_id, status="ready", chunk_count=len(chunks), error=None)

    def reprocess(self, document_id: str) -> dict | None:
        document = self.database.get_document(document_id)
        path = self.database.get_document_storage_path(document_id)
        if document is None or path is None:
            return None
        self.database.update_document(document_id, status="processing", error=None)
        return self.process_document(document_id)

    def delete(self, document_id: str) -> bool:
        document = self.database.get_document(document_id)
        path = self.database.get_document_storage_path(document_id)
        if document is None:
            return False
        self.vector_store.delete_document(document_id)
        self.database.delete_document(document_id)
        if path:
            path.unlink(missing_ok=True)
        return True
