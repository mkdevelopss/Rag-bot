import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path

import pytest
import numpy as np
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.services.chunking import chunk_text
from app.services.answer_cache import AnswerCache
from app.services.document_loader import DocumentLoadError, extract_document
from app.services.embeddings import EmbeddingService
from app.services.rag_pipeline import (
    LLMService,
    RagPipeline,
    is_greeting,
    no_context_answer,
)
from app.services.text_processor import clean_text
from app.services.vector_store import VectorStore
from app.core.config import Settings


def test_text_cleaning_normalizes_spacing_and_unicode():
    assert clean_text("  Cafe\u0301   \x00\n\n\n  second  ") == "Café\n\n second"


def test_text_file_extraction_and_empty_document(tmp_path: Path):
    source = tmp_path / "guide.txt"
    source.write_text("A useful guide.", encoding="utf-8")
    assert extract_document(source)[0].text == "A useful guide."

    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    assert extract_document(empty) == []


def test_docx_and_pdf_extraction(tmp_path: Path):
    word_file = tmp_path / "notes.docx"
    word_document = DocxDocument()
    word_document.add_paragraph("Quarterly planning notes.")
    word_document.save(word_file)
    assert extract_document(word_file)[0].text == "Quarterly planning notes."

    pdf_file = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_file)
    assert extract_document(pdf_file) == []


def test_unsupported_document_format_is_reported(tmp_path: Path):
    with pytest.raises(DocumentLoadError, match="Unsupported"):
        extract_document(tmp_path / "virus.exe")


def test_chunking_preserves_short_text_and_overlapping_content():
    assert chunk_text("short", chunk_size=20, chunk_overlap=4)[0].text == "short"
    source = "0123456789" * 20
    chunks = chunk_text(source, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 1
    assert chunks[0].text.endswith(chunks[1].text[:10])
    assert "".join([chunks[0].text] + [item.text[10:] for item in chunks[1:]]) == source
    assert all(item.chunk_index == index for index, item in enumerate(chunks))


def test_chunking_rejects_invalid_overlap_and_handles_empty_text():
    assert chunk_text("   ") == []
    with pytest.raises(ValueError):
        chunk_text("text", chunk_size=10, chunk_overlap=10)


def test_answer_cache_expires_entries_and_evicts_oldest(monkeypatch):
    import app.services.answer_cache as answer_cache_module

    now = [0.0]
    monkeypatch.setattr(answer_cache_module.time, "monotonic", lambda: now[0])
    cache = AnswerCache(max_entries=1, ttl_seconds=5)
    cache.set("first", "answer one")
    assert cache.get("first") == "answer one"
    cache.set("second", "answer two")
    assert cache.get("first") is None
    assert cache.get("second") == "answer two"
    now[0] = 5.0
    assert cache.get("second") is None


def test_embedding_service_batches_and_normalizes_with_loaded_model():
    class Model:
        def encode(self, texts, **kwargs):
            assert kwargs["normalize_embeddings"] is True
            return np.asarray([[0.6, 0.8] for _ in texts])

    service = EmbeddingService(Settings())
    service._model = Model()
    assert service.embed_texts(["one", "two"]) == [[0.6, 0.8], [0.6, 0.8]]
    assert service.embed_query("query") == [0.6, 0.8]
    assert service.embed_texts([]) == []


def test_chroma_persistence_and_document_deletion(tmp_path: Path):
    store = VectorStore(tmp_path / "chroma")
    record = {
        "chunk_id": "doc1_0",
        "text": "orchids grow in shade",
        "filename": "garden.txt",
        "chunk_index": 0,
        "source_type": "txt",
        "page_number": 2,
        "upload_timestamp": "2026-01-01T00:00:00+00:00",
    }
    store.add_chunks("doc1", [record], [[1.0, 0.0]])
    matches = store.search([1.0, 0.0], top_k=2)
    assert matches[0]["filename"] == "garden.txt"
    assert matches[0]["page_number"] == 2
    assert matches[0]["similarity"] == pytest.approx(1.0)
    assert store.count_document_chunks("doc1") == 1
    store.delete_document("doc1")
    assert store.count_document_chunks("doc1") == 0


def test_rag_pipeline_filters_irrelevant_results_and_constructs_citations():
    class Embeddings:
        def embed_query(self, query):
            assert query == "What grows in shade?"
            return [1.0, 0.0]

    class Store:
        def has_chunks(self):
            return True

        def search(self, embedding, top_k):
            return [
                {
                    "chunk_id": "c1", "text": "Orchids grow well in shade.",
                    "document_id": "d1", "filename": "plants.pdf",
                    "page_number": 4, "similarity": 0.91,
                },
                {
                    "chunk_id": "c2", "text": "The office opens at nine.",
                    "document_id": "d2", "filename": "hours.txt",
                    "page_number": None, "similarity": 0.1,
                },
            ]

    pipeline = RagPipeline(
        Embeddings(), Store(),
        lambda: Settings(similarity_threshold=0.25, max_context_chars=1000),
    )
    sources, context = pipeline.retrieve("  What grows in shade?  ")
    assert len(sources) == 1
    assert sources[0]["filename"] == "plants.pdf"
    assert "page 4" in context
    assert "office opens" not in context
    assert no_context_answer("What is in this?", True).startswith(
        "I couldn't find that information"
    )


@pytest.mark.parametrize("message", ["hi", "Hello!", "hey there", "good morning"])
def test_greetings_are_recognized_without_matching_document_content(message):
    assert is_greeting(message)


def test_normal_questions_are_not_classified_as_greetings():
    assert not is_greeting("Hi, summarize the project document")
    assert not is_greeting("What are the project requirements?")


def test_summary_retrieval_uses_relevant_top_chunks_even_below_threshold():
    received = {}

    class Embeddings:
        def embed_query(self, query):
            received["query"] = query
            return [1.0, 0.0]

    class Store:
        def has_chunks(self):
            return True

        def search(self, embedding, top_k):
            received["top_k"] = top_k
            return [{
                "chunk_id": "c1", "text": "The project provides online bidding.",
                "document_id": "d1", "filename": "project.pdf",
                "page_number": 1, "similarity": 0.12,
            }]

    pipeline = RagPipeline(
        Embeddings(), Store(),
        lambda: Settings(similarity_threshold=0.25, top_k=5),
    )

    sources, context = pipeline.retrieve("summarize it")

    assert received["top_k"] == 10
    assert "main purpose" in received["query"]
    assert sources[0]["filename"] == "project.pdf"
    assert "online bidding" in context


def test_groq_provider_streams_messages_and_keeps_api_key_private(monkeypatch):
    received = {}

    class FakeGroq:
        def __init__(self, api_key):
            received["api_key"] = api_key
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            received["request"] = kwargs
            return iter([
                SimpleNamespace(choices=[
                    SimpleNamespace(delta=SimpleNamespace(content="Grounded "))
                ]),
                SimpleNamespace(choices=[
                    SimpleNamespace(delta=SimpleNamespace(content="answer."))
                ]),
            ])

    fake_module = ModuleType("groq")
    fake_module.Groq = FakeGroq
    monkeypatch.setitem(sys.modules, "groq", fake_module)
    settings = Settings(
        llm_provider="groq",
        groq_api_key="secret-test-key",
        groq_model="test-model",
    )
    service = LLMService(lambda: settings)
    output = "".join(service.stream(
        "What is in the document?",
        "A grounded excerpt.",
        [{"role": "user", "content": "Earlier question"}],
    ))

    assert output == "Grounded answer."
    assert service.is_configured()
    assert received["api_key"] == "secret-test-key"
    assert received["request"]["model"] == "test-model"
    assert received["request"]["stream"] is True
    messages = received["request"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "Earlier question"}
    assert "A grounded excerpt." in messages[-1]["content"]
