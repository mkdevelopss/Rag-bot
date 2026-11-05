# Changelog

## 2.0.0 — production RAG + agent

### Bugs and logic errors fixed

| Problem in 1.x | Effect | Fix |
|---|---|---|
| Retrieval used only the latest message | Follow-ups ("what about its price?") found nothing | Agent writes standalone queries; classic mode rewrites follow-ups first |
| Everything below the similarity threshold became "I couldn't find that" | Small talk and follow-ups got canned refusals | Agent decides when to search; only first-turn greetings are canned |
| Context was `json.dumps`-encoded into the prompt | Newlines became `\n` escapes, wasting tokens and confusing models | Plain numbered excerpts `[1] (file, page)` |
| Single Chroma `add()` for all chunks | Large documents failed past Chroma's batch limit | Batched upserts |
| Fixed character windows | Chunks cut mid-word and mid-sentence | Boundary-aware chunking |
| Vector search only | Order numbers, SKUs and names were missed | Hybrid BM25 + vector search with rank fusion |
| User message saved before generation | Errors left orphan questions; history became user, user… | Question and answer saved together; partial answers saved on disconnect |
| Documents stuck in `processing` after a restart | Never indexed | Pending ingestion resumes on startup |
| Delete during ingestion | Orphan vectors kept answering from a deleted file | Re-check after embedding; clean up |
| Failed upload blocked re-uploading the same file | "Duplicate" error forever | Failed copies are replaced |
| Non-stream `/api/chat` only mapped Gemini's missing-key error | Missing Groq key returned 502 instead of 503 | One shared check for every provider |
| Answer cache keyed by user + full history | Almost never hit | Shared per namespace for first-turn questions, invalidated by document changes |
| Summaries took top-K hits across all files | Summary covered random fragments | Chunks spread across each document |
| Chunk-size change only affected new uploads | Mixed chunking, no way to rebuild | `POST /api/documents/reindex` |
| Reprocess endpoint blocked the request | Long uploads timed out | Runs in the background |
| SQLite in rollback-journal mode | Writes during ingestion blocked chat reads | WAL mode, indexes |
| Messages ordered by timestamp only | Same-timestamp messages could swap | Ordered by timestamp then insertion |
| Two separate SDKs (`google-genai`, `groq`) | Duplicated code paths, no tool calling | One OpenAI-compatible client, retries, timeouts |

### New

- Tool-calling agent: `search_knowledge_base`, `list_documents`, `read_document`, plus per-namespace HTTP tools for live app data.
- Namespaces and `/api/v1` service API with scoped API keys, `user_ref` conversation isolation and trusted `context` passed to tools.
- Text upsert by `external_id` for syncing app records.
- OpenAI provider and `LLM_BASE_URL` for any OpenAI-compatible server.
- Per-user rate limiting, request IDs and request logging.
- Agent steps shown in the UI while answering.
- Docker, nginx and docker-compose files.
- 50 automated tests covering retrieval, the agent loop, the service API and namespace isolation.
