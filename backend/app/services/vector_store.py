import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "default"
# Chroma rejects very large single writes; stay well below its limit.
WRITE_BATCH_SIZE = 500
# Safety net when several worker processes share one Chroma directory.
KEYWORD_INDEX_TTL_SECONDS = 60

_TOKEN_RE = re.compile(r"[\w][\w\-./]*", re.UNICODE)
_STOPWORDS = frozenset(
    "a an and are as at be by can could did do does for from had has have how i if in "
    "is it its me my of on or our so that the their them then there these they this to "
    "us was we were what when where which who why will with would you your about please "
    "tell give show explain".split()
)


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in (match.group(0).lower().strip(".-/") for match in _TOKEN_RE.finditer(text))
        if token and token not in _STOPWORDS
    ]


class _KeywordIndex:
    """Okapi BM25 with Lucene's always-positive IDF, via an inverted index.

    Only chunks that share at least one query term get a score, so a positive
    score always means a real keyword match (important for codes like ORD-7781).
    """

    K1 = 1.2
    B = 0.75

    def __init__(self, ids: list[str], texts: list[str], metadatas: list[dict[str, Any]]):
        self.ids = ids
        self.texts = texts
        self.metadatas = metadatas
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.lengths: list[int] = []
        for position, text in enumerate(texts):
            counts: dict[str, int] = {}
            tokens = tokenize(text)
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            for token, count in counts.items():
                self.postings.setdefault(token, []).append((position, count))
            self.lengths.append(len(tokens))
        self.average_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        total = len(self.ids)
        if not total:
            return []
        scores: dict[int, float] = {}
        for token in set(tokenize(query)):
            postings = self.postings.get(token)
            if not postings:
                continue
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for position, frequency in postings:
                norm = self.K1 * (1 - self.B + self.B * self.lengths[position] / (self.average_length or 1))
                scores[position] = scores.get(position, 0.0) + idf * frequency * (self.K1 + 1) / (frequency + norm)
        return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]


class VectorStore:
    def __init__(self, path: Path):
        import chromadb

        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            name="document_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        self._lock = threading.Lock()
        self._versions: dict[str, int] = {}
        self._keyword_indexes: dict[str, tuple[int, float, _KeywordIndex]] = {}
        self._migrate_namespace_metadata()

    # ------------------------------------------------------------------ admin
    def _migrate_namespace_metadata(self) -> None:
        """Chunks written by older versions have no namespace; assign the default one."""
        offset = 0
        while True:
            batch = self.collection.get(include=["metadatas"], limit=WRITE_BATCH_SIZE, offset=offset)
            if not batch["ids"]:
                return
            missing = [
                (chunk_id, {**(metadata or {}), "namespace": DEFAULT_NAMESPACE})
                for chunk_id, metadata in zip(batch["ids"], batch["metadatas"])
                if not (metadata or {}).get("namespace")
            ]
            if missing:
                self.collection.update(
                    ids=[item[0] for item in missing],
                    metadatas=[item[1] for item in missing],
                )
            offset += len(batch["ids"])

    def _bump(self, namespace: str) -> None:
        with self._lock:
            self._versions[namespace] = self._versions.get(namespace, 0) + 1
            self._keyword_indexes.pop(namespace, None)

    # ------------------------------------------------------------------ write
    def add_chunks(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        if not chunks:
            return
        for start in range(0, len(chunks), WRITE_BATCH_SIZE):
            batch = chunks[start:start + WRITE_BATCH_SIZE]
            self.collection.upsert(
                ids=[chunk["chunk_id"] for chunk in batch],
                documents=[chunk["text"] for chunk in batch],
                embeddings=embeddings[start:start + WRITE_BATCH_SIZE],
                metadatas=[
                    {
                        "namespace": namespace,
                        "document_id": document_id,
                        "filename": chunk["filename"],
                        "chunk_index": chunk["chunk_index"],
                        "source_type": chunk["source_type"],
                        "page_number": chunk["page_number"] or 0,
                        "upload_timestamp": chunk["upload_timestamp"],
                    }
                    for chunk in batch
                ],
            )
        self._bump(namespace)

    def delete_document(self, document_id: str, namespace: str | None = None) -> None:
        self.collection.delete(where={"document_id": document_id})
        if namespace:
            self._bump(namespace)
        else:
            with self._lock:
                self._keyword_indexes.clear()

    # ------------------------------------------------------------------- read
    @staticmethod
    def _where(namespace: str, document_ids: list[str] | None = None) -> dict[str, Any]:
        if not document_ids:
            return {"namespace": namespace}
        document_filter = (
            {"document_id": document_ids[0]}
            if len(document_ids) == 1
            else {"document_id": {"$in": document_ids}}
        )
        return {"$and": [{"namespace": namespace}, document_filter]}

    @staticmethod
    def _match(chunk_id: str, text: str, metadata: dict[str, Any], similarity: float) -> dict[str, Any]:
        return {
            "chunk_id": chunk_id,
            "text": text,
            "document_id": metadata["document_id"],
            "filename": metadata["filename"],
            "page_number": metadata.get("page_number") or None,
            "chunk_index": metadata.get("chunk_index", 0),
            "similarity": similarity,
        }

    def search(
        self,
        embedding: list[float],
        top_k: int,
        namespace: str = DEFAULT_NAMESPACE,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=self._where(namespace, document_ids),
            include=["documents", "metadatas", "distances"],
        )
        return [
            self._match(
                result["ids"][0][index],
                text,
                result["metadatas"][0][index],
                1.0 - float(result["distances"][0][index]),
            )
            for index, text in enumerate(result["documents"][0])
        ]

    def keyword_search(
        self,
        query: str,
        top_k: int,
        namespace: str = DEFAULT_NAMESPACE,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        index = self._keyword_index(namespace)
        matches = []
        allowed = set(document_ids or [])
        # Over-fetch when filtering by document so the filter doesn't starve results.
        for position, score in index.search(query, top_k * 4 if allowed else top_k):
            metadata = index.metadatas[position]
            if allowed and metadata["document_id"] not in allowed:
                continue
            match = self._match(index.ids[position], index.texts[position], metadata, 0.0)
            match["keyword_score"] = score
            matches.append(match)
            if len(matches) >= top_k:
                break
        return matches

    def _keyword_index(self, namespace: str) -> _KeywordIndex:
        with self._lock:
            version = self._versions.get(namespace, 0)
            cached = self._keyword_indexes.get(namespace)
            if cached and cached[0] == version and time.monotonic() - cached[1] < KEYWORD_INDEX_TTL_SECONDS:
                return cached[2]
        data = self.collection.get(where={"namespace": namespace}, include=["documents", "metadatas"])
        index = _KeywordIndex(data["ids"], data["documents"], data["metadatas"])
        with self._lock:
            if self._versions.get(namespace, 0) == version:
                self._keyword_indexes[namespace] = (version, time.monotonic(), index)
        return index

    def similarities(self, chunk_ids: list[str], embedding: list[float]) -> dict[str, float]:
        if not chunk_ids:
            return {}
        data = self.collection.get(ids=chunk_ids, include=["embeddings"])
        return {
            chunk_id: float(sum(a * b for a, b in zip(vector, embedding)))
            for chunk_id, vector in zip(data["ids"], data["embeddings"])
        }

    def get_document_chunks(
        self, document_id: str, start: int = 0, limit: int = 10
    ) -> list[dict[str, Any]]:
        data = self.collection.get(
            where={"$and": [
                {"document_id": document_id},
                {"chunk_index": {"$gte": start}},
                {"chunk_index": {"$lt": start + limit}},
            ]},
            include=["documents", "metadatas"],
        )
        matches = [
            self._match(chunk_id, text, metadata, 1.0)
            for chunk_id, text, metadata in zip(data["ids"], data["documents"], data["metadatas"])
        ]
        return sorted(matches, key=lambda item: item["chunk_index"])

    def count_document_chunks(self, document_id: str) -> int:
        return len(self.collection.get(where={"document_id": document_id}, include=[])["ids"])

    def has_chunks(self, namespace: str | None = None) -> bool:
        if namespace is None:
            return self.collection.count() > 0
        return bool(self.collection.get(where={"namespace": namespace}, limit=1, include=[])["ids"])
