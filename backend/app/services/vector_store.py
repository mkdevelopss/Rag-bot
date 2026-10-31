from pathlib import Path
from typing import Any


class VectorStore:
    def __init__(self, path: Path):
        import chromadb

        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            name="document_chunks",
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        if not chunks:
            return
        self.collection.add(
            ids=[chunk["chunk_id"] for chunk in chunks],
            documents=[chunk["text"] for chunk in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    "document_id": document_id,
                    "filename": chunk["filename"],
                    "chunk_index": chunk["chunk_index"],
                    "source_type": chunk["source_type"],
                    "page_number": chunk["page_number"] or 0,
                    "upload_timestamp": chunk["upload_timestamp"],
                }
                for chunk in chunks
            ],
        )

    def search(
        self,
        embedding: list[float],
        top_k: int,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where = {"document_id": document_id} if document_id else None
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        matches = []
        for index, text in enumerate(result["documents"][0]):
            metadata = result["metadatas"][0][index]
            distance = result["distances"][0][index]
            matches.append({
                "chunk_id": result["ids"][0][index],
                "text": text,
                "document_id": metadata["document_id"],
                "filename": metadata["filename"],
                "page_number": metadata.get("page_number") or None,
                "chunk_index": metadata["chunk_index"],
                "similarity": 1.0 - float(distance),
            })
        return matches

    def delete_document(self, document_id: str) -> None:
        self.collection.delete(where={"document_id": document_id})

    def count_document_chunks(self, document_id: str) -> int:
        return len(self.collection.get(where={"document_id": document_id}, include=[])["ids"])

    def has_chunks(self) -> bool:
        return self.collection.count() > 0
