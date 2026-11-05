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
from app.services.llm import LLMClient
from app.services.rag_pipeline import (
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


class FakeEmbeddings:
    """Bag-of-words vectors over a tiny vocabulary: enough to test retrieval logic."""

    VOCAB = ["orchid", "shade", "office", "nine", "refund", "days", "screen", "repair", "price"]

    def _vector(self, text):
        words = text.lower()
        vector = [float(words.count(term)) for term in self.VOCAB] + [0.01]
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector]

    def embed_texts(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, query):
        return self._vector(query)


def _store_with(tmp_path, records, namespace="default"):
    store = VectorStore(tmp_path / "chroma")
    embeddings = FakeEmbeddings()
    for document_id, texts in records.items():
        chunks = [
            {
                "chunk_id": f"{document_id}_{index}", "text": text, "filename": f"{document_id}.txt",
                "chunk_index": index, "source_type": "txt", "page_number": None,
                "upload_timestamp": "2026-01-01T00:00:00+00:00",
            }
            for index, text in enumerate(texts)
        ]
        store.add_chunks(document_id, chunks, embeddings.embed_texts(texts), namespace)
    return store, embeddings


def test_rag_pipeline_filters_irrelevant_results_and_numbers_citations(tmp_path):
    store, embeddings = _store_with(tmp_path, {
        "plants": ["Orchids grow well in shade."],
        "hours": ["The office opens at nine."],
    })
    pipeline = RagPipeline(
        embeddings, store, lambda: Settings(similarity_threshold=0.5, max_context_chars=1000)
    )
    sources, context = pipeline.retrieve("  What grows in shade? orchid  ")
    assert [source["filename"] for source in sources] == ["plants.txt"]
    assert sources[0]["number"] == 1
    assert context.startswith("[1] (plants.txt)")
    assert "office opens" not in context
    assert no_context_answer("What is in this?", True).startswith("I couldn't find that information")


def test_hybrid_search_finds_exact_codes_that_embeddings_miss(tmp_path):
    store, embeddings = _store_with(tmp_path, {
        "orders": ["Repair order ORD-7781 was shipped to Gulberg on Monday.", "Office hours are nine to five."],
    })
    pipeline = RagPipeline(embeddings, store, lambda: Settings(similarity_threshold=0.9))
    hits = pipeline.search("status of ORD-7781")
    assert hits and "ORD-7781" in hits[0]["text"]
    no_hybrid = RagPipeline(embeddings, store, lambda: Settings(similarity_threshold=0.9, hybrid_search=False))
    assert no_hybrid.search("status of ORD-7781") == []


def test_namespaces_are_isolated(tmp_path):
    store, embeddings = _store_with(tmp_path, {"a": ["Screen repair price is 5000."]}, namespace="marketbid")
    _, _ = store, embeddings
    other_chunk = [{
        "chunk_id": "b_0", "text": "Refund within 7 days.", "filename": "b.txt", "chunk_index": 0,
        "source_type": "txt", "page_number": None, "upload_timestamp": "x",
    }]
    store.add_chunks("b", other_chunk, embeddings.embed_texts(["Refund within 7 days."]), "distos")
    pipeline = RagPipeline(embeddings, store, lambda: Settings(similarity_threshold=0.1))
    assert {hit["document_id"] for hit in pipeline.search("screen repair price refund", "marketbid")} == {"a"}
    assert {hit["document_id"] for hit in pipeline.search("screen repair price refund", "distos")} == {"b"}
    assert pipeline.search("screen repair", "default") == []


def test_large_documents_are_written_in_batches(tmp_path):
    texts = [f"repair note number {index}" for index in range(1200)]
    store, _ = _store_with(tmp_path, {"big": texts})
    assert store.count_document_chunks("big") == 1200
    assert [chunk["chunk_index"] for chunk in store.get_document_chunks("big", 10, 3)] == [10, 11, 12]


@pytest.mark.parametrize("message", ["hi", "Hello!", "hey there", "good morning", "salam"])
def test_greetings_are_recognized_without_matching_document_content(message):
    assert is_greeting(message)


def test_normal_questions_are_not_classified_as_greetings():
    assert not is_greeting("Hi, summarize the project document")
    assert not is_greeting("What are the project requirements?")


def test_summary_retrieval_spreads_across_the_document(tmp_path):
    texts = [f"Section {index} talks about repair price and screen quality." for index in range(20)]
    store, embeddings = _store_with(tmp_path, {"guide": texts})
    pipeline = RagPipeline(embeddings, store, lambda: Settings(similarity_threshold=0.99, top_k=5))
    documents = [{"document_id": "guide", "status": "ready", "chunk_count": 20}]
    sources, context = pipeline.retrieve("summarize it", documents=documents)
    indexes = {source["chunk_id"] for source in sources}
    assert "guide_0" in indexes and any(int(item.split("_")[1]) >= 10 for item in indexes)
    assert "Section 0" in context


def test_chunking_prefers_sentence_boundaries():
    text = " ".join(f"Sentence number {index} is here." for index in range(60))
    chunks = chunk_text(text, chunk_size=200, chunk_overlap=40)
    assert len(chunks) > 3
    assert all(chunk.text.endswith(".") for chunk in chunks[:-1])
    assert all(not chunk.text[0].islower() or chunk.text.split()[0] in {"is", "here."} for chunk in chunks)


def _fake_openai(monkeypatch, responses, received):
    """Install a fake `openai` module whose stream yields the given chunk lists in order."""

    def delta(content=None, tool_calls=None):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))])

    class FakeOpenAI:
        def __init__(self, api_key, base_url, timeout, max_retries):
            received.setdefault("clients", []).append({"api_key": api_key, "base_url": base_url})
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            received.setdefault("requests", []).append(kwargs)
            return iter(responses.pop(0)(delta))

    module = ModuleType("openai")
    module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)


def test_llm_client_streams_text_and_uses_provider_base_url(monkeypatch):
    received = {}
    _fake_openai(monkeypatch, [lambda d: [d("Grounded "), d("answer.")]], received)
    settings = Settings(llm_provider="groq", groq_api_key="secret-test-key", groq_model="test-model")
    client = LLMClient(lambda: settings)
    output = "".join(client.stream_text([{"role": "user", "content": "q"}]))
    assert output == "Grounded answer."
    assert received["clients"][0] == {"api_key": "secret-test-key", "base_url": "https://api.groq.com/openai/v1"}
    assert received["requests"][0]["model"] == "test-model"
    assert received["requests"][0]["stream"] is True


def test_llm_client_assembles_streamed_tool_calls(monkeypatch):
    received = {}

    def tool_chunks(d):
        part = lambda **kw: SimpleNamespace(index=0, id=kw.get("id"), function=SimpleNamespace(name=kw.get("name"), arguments=kw.get("args")))
        return [
            d(tool_calls=[part(id="call_1", name="search_knowledge_base", args="")]),
            d(tool_calls=[part(args='{"query": ')]),
            d(tool_calls=[part(args='"refund"}')]),
        ]

    _fake_openai(monkeypatch, [tool_chunks], received)
    client = LLMClient(lambda: Settings(llm_provider="gemini", gemini_api_key="k"))
    events = list(client.stream_chat([{"role": "user", "content": "q"}], tools=[{"type": "function"}]))
    kind, calls = events[-1]
    assert kind == "tool_calls"
    assert calls[0].name == "search_knowledge_base"
    assert calls[0].arguments == '{"query": "refund"}'
    assert "generativelanguage" in received["clients"][0]["base_url"]


def test_agent_config_loads_tools_and_http_tool_sends_trusted_context(tmp_path, monkeypatch):
    import json
    import httpx
    from app.services.tools import AgentConfig, call_http_tool

    path = tmp_path / "agent_config.json"
    path.write_text(json.dumps({"namespaces": {"marketbid": {
        "assistant_name": "MarketBid Assistant",
        "instructions": "Prices are in PKR.",
        "tools": [{
            "name": "get_repair_status", "description": "Repair status",
            "parameters": {"type": "object", "properties": {"request_id": {"type": "string"}}},
            "url": "http://marketbid.test/ai-tools/repair-status", "secret_env": "MB_TOOL_SECRET",
        }],
    }}}))
    config = AgentConfig(path)
    tool = config.profile("marketbid").tools[0]
    assert config.profile("unknown").assistant_name == "Inkwell"

    assert "missing MB_TOOL_SECRET" in call_http_tool(tool, {}, "marketbid", None)
    monkeypatch.setenv("MB_TOOL_SECRET", "s3cret")
    sent = {}

    def fake_request(method, url, json=None, headers=None, timeout=None):
        sent.update(method=method, url=url, json=json, headers=headers)
        return httpx.Response(200, text='{"status": "assigned"}')

    monkeypatch.setattr(httpx, "request", fake_request)
    output = call_http_tool(tool, {"request_id": "R-1"}, "marketbid", {"user_id": 7})
    assert output == '{"status": "assigned"}'
    assert sent["headers"]["X-Tool-Secret"] == "s3cret"
    assert sent["json"] == {"arguments": {"request_id": "R-1"}, "context": {"user_id": 7}, "namespace": "marketbid"}

    path.write_text(json.dumps({"namespaces": {"x": {"tools": [{"name": "list_documents", "description": "d", "url": "u"}]}}}))
    with pytest.raises(ValueError):
        AgentConfig(path)
