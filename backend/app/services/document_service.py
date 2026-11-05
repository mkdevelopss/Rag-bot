import hashlib
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.services.chunking import chunk_text
from app.services.database import Database
from app.services.document_loader import DocumentLoadError, extract_document
from app.services.embeddings import EmbeddingService
from app.services.vector_store import DEFAULT_NAMESPACE, VectorStore

logger = logging.getLogger(__name__)


class DuplicateDocumentError(ValueError):
    pass


def namespaced_hash(namespace: str, digest: str) -> str:
    # The default namespace keeps bare hashes so existing databases stay valid.
    return digest if namespace == DEFAULT_NAMESPACE else f"{namespace}:{digest}"


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
        # Embedding is CPU-heavy; one ingestion at a time keeps chat responsive.
        self._ingest_lock = threading.Lock()

    @staticmethod
    def safe_filename(filename: str) -> str:
        basename = Path(filename).name
        cleaned = re.sub(r"[^\w .()-]", "_", basename, flags=re.UNICODE).strip(" .")
        return cleaned[:180] or "uploaded-document"

    def register_upload(
        self,
        filename: str,
        content: bytes,
        namespace: str = DEFAULT_NAMESPACE,
        external_id: str | None = None,
    ) -> dict:
        settings: Settings = self.settings_provider()
        safe_name = self.safe_filename(filename)
        content_hash = namespaced_hash(namespace, hashlib.sha256(content).hexdigest())
        duplicate = self.database.find_document_by_hash(content_hash)
        if duplicate:
            if duplicate["status"] != "failed":
                raise DuplicateDocumentError(
                    f"{safe_name} matches an existing document: {duplicate['filename']}"
                )
            # A failed copy shouldn't block uploading the same file again.
            self.delete(duplicate["document_id"])

        extension = Path(safe_name).suffix.lower()
        upload_dir = settings.resolve_path(settings.upload_path) / namespace
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
            "namespace": namespace,
            "external_id": external_id,
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

    def upsert_text(
        self,
        namespace: str,
        title: str,
        text: str,
        external_id: str | None = None,
    ) -> dict:
        """Index plain text (FAQ entry, product description, policy) from another app."""
        if external_id:
            existing = self.database.find_document_by_external_id(namespace, external_id)
            if existing:
                self.delete(existing["document_id"])
        filename = self.safe_filename(title)
        if not filename.lower().endswith((".txt", ".md")):
            filename += ".md"
        return self.register_upload(filename, text.encode("utf-8"), namespace, external_id)

    def ingest(self, filename: str, content: bytes, namespace: str = DEFAULT_NAMESPACE) -> dict:
        document = self.register_upload(filename, content, namespace)
        return self.process_document(document["document_id"])

    def process_document(self, document_id: str) -> dict | None:
        with self._ingest_lock:
            document = self.database.get_document(document_id)
            path = self.database.get_document_storage_path(document_id)
            if document is None or path is None:
                return None
            try:
                self._process(document, path)
            except Exception as exc:
                logger.exception("Document ingestion failed for %s", document["filename"])
                if self.database.get_document(document_id) is not None:
                    self.database.update_document(document_id, status="failed", error=str(exc)[:500])
            return self.database.get_document(document_id)

    def _process(self, document: dict, path: Path) -> None:
        settings: Settings = self.settings_provider()
        document_id = document["document_id"]
        namespace = document.get("namespace") or DEFAULT_NAMESPACE
        self.vector_store.delete_document(document_id, namespace)
        pages = extract_document(path)
        if not pages:
            raise DocumentLoadError("No readable text was found in this document.")
        chunks = []
        for page in pages:
            for chunk in chunk_text(page.text, settings.chunk_size, settings.chunk_overlap, page.page_number):
                chunks.append({
                    "chunk_id": f"{document_id}_{len(chunks)}",
                    "text": chunk.text,
                    "filename": document["filename"],
                    "chunk_index": len(chunks),
                    "source_type": path.suffix.lower().lstrip("."),
                    "page_number": chunk.page_number,
                    "upload_timestamp": document["created_at"],
                })
        if not chunks:
            raise DocumentLoadError("The document did not contain text to index.")
        vectors = self.embeddings.embed_texts([chunk["text"] for chunk in chunks])
        if self.database.get_document(document_id) is None:
            return  # deleted while embedding
        self.vector_store.add_chunks(document_id, chunks, vectors, namespace)
        if self.database.get_document(document_id) is None:
            # Deleted between the check and the write: don't leave orphan vectors.
            self.vector_store.delete_document(document_id, namespace)
            return
        self.database.update_document(document_id, status="ready", chunk_count=len(chunks), error=None)

    def reprocess(self, document_id: str) -> dict | None:
        document = self.database.get_document(document_id)
        path = self.database.get_document_storage_path(document_id)
        if document is None or path is None:
            return None
        self.database.update_document(document_id, status="processing", error=None)
        return self.process_document(document_id)

    def mark_all_for_reindex(self, namespace: str = DEFAULT_NAMESPACE) -> list[str]:
        ids = [doc["document_id"] for doc in self.database.list_documents(namespace)]
        for document_id in ids:
            self.database.update_document(document_id, status="processing", error=None)
        return ids

    def process_many(self, document_ids: list[str]) -> None:
        for document_id in document_ids:
            self.process_document(document_id)

    def resume_pending(self) -> threading.Thread | None:
        """Documents left 'processing' by a restart would otherwise stay stuck forever."""
        pending = self.database.list_processing_documents()
        if not pending:
            return None
        logger.info("Resuming ingestion for %d document(s)", len(pending))
        thread = threading.Thread(target=self.process_many, args=(pending,), daemon=True)
        thread.start()
        return thread

    def delete(self, document_id: str) -> bool:
        document = self.database.get_document(document_id)
        path = self.database.get_document_storage_path(document_id)
        if document is None:
            return False
        self.vector_store.delete_document(document_id, document.get("namespace") or DEFAULT_NAMESPACE)
        self.database.delete_document(document_id)
        if path:
            path.unlink(missing_ok=True)
        return True
