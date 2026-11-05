# Inkwell Project Guide

This guide explains how the application is put together, what its main files do, and how information moves through it. It is written for someone new to the project; you do not need to know AI terminology to start.

## Contents

- [What the application does](#what-the-application-does)
- [Technology, in plain language](#technology-in-plain-language)
- [Project map](#project-map)
- [How the pieces work together](#how-the-pieces-work-together)
- [Important backend files](#important-backend-files)
- [Important frontend files](#important-frontend-files)
- [API overview](#api-overview)
- [Run and test the project](#run-and-test-the-project)
- [Local data and security](#local-data-and-security)
- [Troubleshooting and limitations](#troubleshooting-and-limitations)
- [Glossary](#glossary)

## What the application does

Inkwell is a single-company question-and-answer application. Anyone who reaches the chat automatically gets an employee session for that browser, with conversation history private to that session. An administrator manages the shared document library and workspace settings. The backend extracts supported files, divides their text into overlapping passages, and indexes them for meaning-based search. Answers stream into the chat with source citations.

Supported file types are PDF, DOCX, TXT, Markdown (`.md`), and CSV. Text extraction, embeddings, vector search, uploaded files, and conversation storage run on your computer. Generating an answer requires your own Gemini or Groq API key and an internet connection to that provider.

## Technology, in plain language

| Technology | What it does here |
|---|---|
| React | Builds the interactive screens: chat, document library, sidebar, and settings. |
| TypeScript | Adds data types to the frontend so mismatched API data and UI code can be caught during the build. |
| Vite | Runs the frontend during development and builds its production assets. It also forwards browser `/api` requests to the local backend. |
| Tailwind CSS and `index.css` | Provide styling utilities and the application's custom theme and responsive layout. |
| Python and FastAPI | Run the backend and expose HTTP endpoints to the frontend. |
| Pydantic and Pydantic Settings | Validate API request data and load validated configuration from environment variables. |
| Sentence Transformers | Convert document passages and questions into numerical vectors locally. The default model produces 384-dimensional vectors. |
| ChromaDB | Stores and searches the passage vectors on disk. |
| SQLite | Stores browser sessions, shared document records, user-owned conversations, and UI setting overrides. |
| PBKDF2 and HTTP-only cookies | Hash the one-time administrator key and keep browser sessions out of JavaScript-readable storage. |
| In-memory answer cache | Reuses generated answers briefly for the same browser session and document/model state. |
| Gemini or Groq | Generate the final natural-language answer from the question and retrieved passages. The model provider is remote; document embeddings and search are local. |
| pytest and FastAPI TestClient | Exercise backend service behavior and API routes. The frontend is checked with TypeScript and a Vite production build. |

This is a Retrieval-Augmented Generation (RAG) application: it retrieves relevant document passages first, then gives those passages to a language model to help compose an answer.

## Project map

```text
chatbot-rag2/
|-- backend/
|   |-- app/
|   |   |-- api/routes/       Session, admin claim, document, chat, and settings APIs
|   |   |-- core/             Configuration and logging
|   |   |-- models/           Reserved package; no ORM models currently
|   |   |-- schemas/          Validated API request and response types
|   |   |-- services/         Authentication, RAG, answer cache, ingestion, and persistence
|   |   |-- cli.py            Local one-time administrator-key generation
|   |   `-- main.py           FastAPI app construction and wiring
|   |-- data/                 Local database, uploads, and vector index
|   |-- tests/                Backend unit and API tests
|   |-- .env.example          Safe configuration template
|   `-- requirements.txt      Python dependencies
|-- frontend/
|   |-- src/
|   |   |-- components/       Reusable UI panels and chat message
|   |   |-- services/api.ts   HTTP and streaming client
|   |   |-- types/            Frontend data contracts
|   |   |-- App.tsx           Main screens and application state
|   |   |-- index.css         Theme, layout, and responsive styles
|   |   `-- main.tsx          Browser entry point
|   |-- index.html            HTML shell and browser metadata
|   |-- package.json          Frontend dependencies and scripts
|   |-- package-lock.json     Exact npm dependency resolution
|   |-- tsconfig*.json        TypeScript compiler settings
|   `-- vite.config.ts        Vite development server and API proxy
|-- README.md                 Setup, provider configuration, and operational notes
|-- PROJECT_GUIDE.md          This architecture and code map
`-- start.bat                 Windows helper to set up and launch both servers
```

`backend/.env` and runtime data are local machine files and are intentionally excluded from Git. Generated folders such as `.venv`, `node_modules`, `__pycache__`, and the frontend build output are not source files.

## How the pieces work together

### Upload and indexing

```text
Browser file picker / drag and drop
        |
        v
App.tsx -> services/api.ts -> POST /api/documents/upload
        |
        v
Authentication checks that the browser session has administrator access
        |
        v
documents.py validates the upload
        |
        v
DocumentService saves and registers the file in SQLite
        |
        v
extract_document -> clean_text -> chunk_text
        |
        v
EmbeddingService creates local vectors
        |
        v
VectorStore saves passages and metadata in ChromaDB
```

The API validates supported file types and the configured upload-size limit. Only administrators can upload, delete, or reprocess company documents; every signed-in employee can list and search the shared library. The document service sanitizes filenames and checks content hashes to prevent duplicate uploads. A document can be marked `processing`, `ready`, or `failed`; its record and any extraction error are shown in the Documents panel. PDF page numbers are retained where extraction provides them. Reprocessing uses current chunk settings; it does not change the original uploaded file.

### Chat and citations

```text
Chat composer in App.tsx
        |
        v
Authentication verifies the session and scopes the conversation to its owner
        |
        v
api.ts sends POST /api/chat/stream and reads Server-Sent Events
        |
        v
chat.py prepares conversation context and calls RagPipeline
        |
        +--> greeting / no-indexed-document response, when applicable
        |
        v
EmbeddingService embeds the question -> VectorStore searches ChromaDB
        |
        v
RagPipeline builds bounded context and source records
        |
        v
LLMClient streams a response from Groq, Gemini or OpenAI (agent mode may first call tools)
        |
        v
chat.py emits sources, text deltas, and completion events
        |
        +--> SQLite stores the user and assistant messages
        `--> ChatMessage renders formatted text and expandable citations
```

The model receives the question, the current browser session's recent conversation context, and selected retrieved passages, not the entire document library. Conversations are owned by one session, and the API checks that ownership for reads, edits, deletion, chat, and regeneration. Generated answers are cached in memory for up to 10 minutes (512 entries maximum); the key includes the session, question/history/context, document versions, and model/retrieval settings. Retrieval runs each time, so citations remain current. Greetings can receive a friendly reply without retrieval. If no documents are indexed, the app asks the user to contact an administrator; if the search finds no useful passages, it explains that it cannot answer from the library. Summary-style prompts search more broadly. Citations show the filename, an available page number, a snippet, and a similarity score.

### Accounts and roles

Every browser starts with an anonymous employee session. To create the first administrator, `python -m app.cli generate-admin-key` writes a PBKDF2 hash to the server's ignored `.env` and prints the raw random key once. Enter that key under the collapsed **Settings → Administrator access** section. The database atomically allows only one active administrator claim; the successful browser session is promoted, and later visitors remain employees. Private history stays scoped to each browser cookie. Anyone who can reach the app can query the shared company library, so keep it on a trusted network and use HTTPS for real data.

`authentication.py` hashes passwords and issues random per-browser sessions, storing only session hashes in SQLite. The browser receives the raw session only as a `SameSite=Strict`, `HttpOnly` cookie. API mutations also require a custom same-origin request header. Backend route dependencies enforce session and administrator permissions; the UI only hides controls for usability and is not the security boundary.

### Settings and API keys

The Settings panel is visible to employees for the hidden administrator-key claim and exposes configuration only after the session is promoted. It fetches non-secret settings from `/api/settings`; general indexing and retrieval adjustments are saved as SQLite overrides. Provider changes have an additional password-gated flow:

1. `settings_security.py` verifies the password and issues a short-lived in-memory token.
2. The frontend sends the token in an authorization header when saving the provider and key.
3. `runtime_settings.py` updates the local backend `.env` file and refreshes runtime configuration.
4. The API reports whether a provider key exists, but does not return the key itself.

If `SETTINGS_PASSWORD_HASH` is empty, the key-generation CLI asks you to create a separate provider-settings password and stores only its salted hash. The provider API key is stored as plain text in the Git-ignored backend `.env`; protect that file on the server. The application does not provide API credits or create a provider key for you.

## Important backend files

| File | Purpose and main responsibilities |
|---|---|
| [`backend/app/main.py`](backend/app/main.py) | `create_app()` initializes logging, database and services; attaches the shared answer cache and other dependencies to `app.state`; checks a same-origin header on API mutations; enables CORS; registers all API routers. The module-level `app` is the ASGI application Uvicorn runs. |
| [`backend/app/cli.py`](backend/app/cli.py) | `generate_admin_key()` creates a random one-time key, writes only its PBKDF2 hash to `.env`, and initializes the separate provider-settings password hash if needed. `main()` dispatches the CLI command. |
| [`backend/app/core/config.py`](backend/app/core/config.py) | `Settings` defines defaults and validates configuration, resolves data paths, and selects the active provider's model/key. `get_settings()` caches base settings. |
| `backend/app/core/logging.py` | `configure_logging()` configures backend log output. |
| [`backend/app/schemas/api.py`](backend/app/schemas/api.py) | Pydantic request/response models constrain chat input, upload records, citations, conversation operations, settings, and provider credentials. `SecretStr` helps keep request keys out of representations. |
| `backend/app/api/routes/health.py` | `health()` reports service/model readiness without disclosing secrets. |
| `backend/app/api/routes/auth.py` | `/status` provisions a per-browser employee session when needed; `/claim-admin` verifies and consumes the one-time bootstrap key. Legacy login/account endpoints remain for existing installations. |
| [`backend/app/services/authentication.py`](backend/app/services/authentication.py) | `hash_password()` / `verify_password()` use PBKDF2; `AuthenticationService` provisions sessions, validates legacy logins, persists hashed sessions, and changes passwords. `get_current_user()`, `require_user()`, and `require_admin()` are reusable backend access controls. |
| `backend/app/api/routes/documents.py` | `validate_upload()` checks file extension, content type, size, and non-empty content. Backend dependencies restrict uploads, deletion, and reprocessing to admins; signed-in employees can view and search the shared library. |
| `backend/app/api/routes/chat.py` | `_conversation_context()` loads only the current session's history; `_prepare()` enforces ownership and handles greetings, retrieval, and no-context behavior. `_answer_cache_key()` fingerprints session, context, document versions, and settings. `chat()` and `stream_chat()` reuse cached generations while streaming current citations. |
| `backend/app/services/answer_cache.py` | `AnswerCache` provides a thread-safe in-memory LRU cache with a 10-minute TTL and a 512-entry cap. |
| `backend/app/api/routes/conversations.py` | Routes list, create, fetch, rename, and delete conversations scoped to the signed-in owner. |
| `backend/app/api/routes/settings.py` | Admin-only routes return safe settings, update retrieval options, unlock the provider-key lock, save provider information, and lock tokens. |
| [`backend/app/services/document_service.py`](backend/app/services/document_service.py) | `register_upload()` sanitizes, hashes, stores, and registers an upload. `process_document()` / `_process()` run extraction, chunking, embedding, and indexing. `reprocess()` rebuilds an index; `delete()` removes the record, file, and vectors. |
| [`backend/app/services/document_loader.py`](backend/app/services/document_loader.py) | `extract_document()` extracts text from supported formats into page-aware records. `DocumentLoadError` reports extraction problems; `ExtractedPage` carries page text and page number. |
| [`backend/app/services/text_processor.py`](backend/app/services/text_processor.py) | `clean_text()` normalizes extracted text and whitespace before chunking. |
| [`backend/app/services/chunking.py`](backend/app/services/chunking.py) | `TextChunk` represents a passage and its metadata. `chunk_text()` divides text into overlapping passages while retaining source page information. |
| [`backend/app/services/embeddings.py`](backend/app/services/embeddings.py) | `EmbeddingService` lazily loads the local Sentence Transformers model; `embed_texts()` batches document passages and `embed_query()` embeds a search question. |
| [`backend/app/services/vector_store.py`](backend/app/services/vector_store.py) | `VectorStore` manages persistent ChromaDB. `add_chunks()` indexes passages; `search()` retrieves nearest matches; delete/count/availability methods support document management and retrieval. |
| [`backend/app/services/rag_pipeline.py`](backend/app/services/rag_pipeline.py) | `is_greeting()` and `no_context_answer()` provide conversational and missing-context responses. `RagPipeline.search()` runs hybrid vector + BM25 search; `retrieve()` builds numbered context for classic RAG; `rewrite_query()` turns follow-ups into standalone queries. |
| [`backend/app/services/llm.py`](backend/app/services/llm.py) | `LLMClient`: one OpenAI-compatible client for Groq, Gemini and OpenAI, with streaming, tool-call assembly, retries and error mapping. |
| [`backend/app/services/agent.py`](backend/app/services/agent.py) / [`tools.py`](backend/app/services/tools.py) | Tool-calling agent loop, built-in knowledge-base tools, per-namespace HTTP tools from `agent_config.json`. |
| [`backend/app/services/chat_service.py`](backend/app/services/chat_service.py) | One chat flow for the web UI and the service API: preparation, caching, agent/classic routing, persistence. |
| [`backend/app/api/routes/service_api.py`](backend/app/api/routes/service_api.py) | `/api/v1/namespaces/{ns}/...` server-to-server API (see INTEGRATION.md). |
| [`backend/app/services/database.py`](backend/app/services/database.py) | `Database` owns SQLite connections and schema migration. It persists browser users, hashed sessions, login lockouts, versioned shared document metadata, user-owned conversation/message history, and setting overrides. There is no ORM layer. |
| [`backend/app/services/runtime_settings.py`](backend/app/services/runtime_settings.py) | `RuntimeSettings` combines base environment settings with SQLite overrides. It safely updates provider settings and persists provider keys in `.env`. |
| [`backend/app/services/settings_security.py`](backend/app/services/settings_security.py) | `hash_settings_password()` creates salted PBKDF2 hashes. `SettingsAuth` verifies the password, issues time-limited tokens, authorizes changes, and revokes tokens. |
| `backend/app/models/__init__.py` | Empty package placeholder; the project currently uses SQLite directly rather than ORM model classes. |
| `backend/app/**/__init__.py` | Empty Python package markers that allow modules to be imported; they do not contain application logic. |

### Backend tests and dependencies

| File | What it checks |
|---|---|
| `backend/tests/conftest.py` | Adds the backend folder to Python's import path for tests. |
| `backend/tests/test_services.py` | Tests text processing, file extraction, chunking, embeddings, Chroma persistence, retrieval, and provider behavior. |
| `backend/tests/test_api.py` | Tests sign-in, admin permissions, employee first-login password changes, conversation privacy, deactivation, provider settings, document uploads, and chat/SSE responses. |
| `backend/requirements.txt` | Lists FastAPI/Uvicorn, validation/settings, provider SDKs, embedding/vector tools, file readers, and pytest/httpx. |
| `backend/.env.example` | Documents safe-to-share configuration names and defaults. It is a template, not the live secret store. |

## Important frontend files

| File | Purpose and main responsibilities |
|---|---|
| [`frontend/src/main.tsx`](frontend/src/main.tsx) | Browser entry point: imports global styles and mounts the React `App` in the HTML root. |
| [`frontend/src/App.tsx`](frontend/src/App.tsx) | `App` checks sign-in state and shows login or forced password change before mounting the workspace. `Workspace` loads user-scoped chats and the shared document list, and exposes admin functions for users, settings, and uploads. `sendMessage()` consumes stream events. |
| `frontend/src/components/auth/AuthScreen.tsx` | Renders sign-in, first-admin setup instructions, and the forced new-password form. |
| `frontend/src/components/admin/UsersPanel.tsx` | Lets admins create employees, reveal a generated temporary password once, reset passwords, and deactivate/reactivate employees. |
| [`frontend/src/services/api.ts`](frontend/src/services/api.ts) | `request<T>()` sends same-origin authenticated requests with the CSRF header and parses errors. The `api` object defines typed auth/admin and workspace calls. `uploadFiles()` sends multipart uploads. `streamChat()` reads and parses SSE events. |
| [`frontend/src/types/index.ts`](frontend/src/types/index.ts) | Shared TypeScript shapes for users/auth status, sources, messages, conversations, documents, and settings; these mirror API response data. |
| [`frontend/src/components/layout/Sidebar.tsx`](frontend/src/components/layout/Sidebar.tsx) | `Sidebar` renders shared-library navigation, private recent conversations, Settings, and the new-private-session action. |
| [`frontend/src/components/chat/ChatMessage.tsx`](frontend/src/components/chat/ChatMessage.tsx) | `ChatMessage` renders user and assistant messages, markdown, loading state, copy/regenerate controls, and citations. Its `SourceCard` expands to show the cited snippet. |
| [`frontend/src/components/documents/DocumentsPanel.tsx`](frontend/src/components/documents/DocumentsPanel.tsx) | `DocumentsPanel` provides browse/drag-drop upload controls and lists document status, errors, size, chunks, delete, and reprocess actions. `fileSize()` formats sizes. |
| [`frontend/src/components/ui/SettingsPanel.tsx`](frontend/src/components/ui/SettingsPanel.tsx) | `SettingsPanel` shows employees a collapsed one-time admin-key claim; after promotion it edits chunking/retrieval and handles the provider password unlock, API key form, and lock action. |
| [`frontend/src/index.css`](frontend/src/index.css) | Imports Tailwind and defines the blue-toned dark/light theme, component layout, and mobile breakpoints. |
| [`frontend/index.html`](frontend/index.html) | Vite HTML shell, page title, description, theme color, and mount point. |
| `frontend/vite.config.ts` | Enables React/Tailwind plugins, listens on port 5173 on network interfaces for local phone testing, and proxies `/api` to the backend at `127.0.0.1:8001`. |
| `frontend/package.json` / `frontend/package-lock.json` | Frontend scripts/dependencies and their exact resolved versions. `npm run build` runs TypeScript project checks and Vite bundling. |
| `frontend/tsconfig.json`, `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json` | TypeScript project references and strict compiler settings for browser code and Vite configuration. |
| `frontend/src/vite-env.d.ts` | Declares Vite's `import.meta.env` API and the optional `VITE_API_BASE_URL`. |

## API overview

The backend's interactive API reference is available at `http://localhost:8001/docs` while it is running.

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Backend and configured-model status. |
| `GET /api/auth/status` | Reuse the current session or create an anonymous employee session and HTTP-only cookie. |
| `POST /api/auth/claim-admin` | Promote the current session to admin with the one-time bootstrap key. |
| `POST /api/auth/login` / `POST /api/auth/logout` | Start or revoke an HTTP-only browser session. |
| `POST /api/auth/change-password` | Replace a temporary employee password. |
| `GET /api/auth/users` / `POST /api/auth/users` | Admin: list users or create an employee with a generated temporary password. |
| `POST /api/auth/users/{id}/reset-password` | Admin: issue a replacement employee temporary password. |
| `PATCH /api/auth/users/{id}/active` | Admin: deactivate or reactivate an employee. |
| `POST /api/documents/upload` | Admin: upload one or more shared company files. |
| `GET /api/documents` | List documents and processing status. |
| `GET /api/documents/{id}` | Read one document record. |
| `DELETE /api/documents/{id}` | Delete the file, metadata, and indexed chunks. |
| `POST /api/documents/{id}/reprocess` | Rebuild chunks and vectors. |
| `POST /api/chat` | Get a non-streaming answer. |
| `POST /api/chat/stream` | Stream chat events: `meta`, `sources`, `delta`, `done`, or `error`. |
| `GET /api/conversations` | List conversation summaries. |
| `POST /api/conversations` | Create a conversation. |
| `GET`, `PATCH`, `DELETE /api/conversations/{id}` | Fetch, rename, or delete a conversation. |
| `GET /api/settings` | Read safe settings (never the provider key). |
| `PATCH /api/settings` | Update chunking and retrieval options. |
| `POST /api/settings/unlock` | Verify settings password; returns a short-lived token. |
| `POST /api/settings/provider` | Set provider, model, and key using the unlock token. |
| `POST /api/settings/lock` | Revoke the unlock token. |

## Run and test the project

The helper script is [`start.bat`](start.bat). It checks for Python 3.14 and Node.js, installs missing dependencies, creates `backend/.env` from the example if needed, and opens separate backend and frontend windows. Generate the one-time admin key from the backend folder before the first admin claim:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m app.cli generate-admin-key
Set-Location ..
```

Save the key printed by the command and restart the backend before entering it under **Settings → Administrator access**. If no provider-settings password hash exists, the command prompts for a separate password. Anyone who can reach the app is an employee, so keep local testing on a trusted network. For a real company deployment, serve the frontend and API on a company-controlled domain with HTTPS, set `AUTH_COOKIE_SECURE=true`, and do not expose the Vite development server or Uvicorn reload mode.

To run the pieces manually, open PowerShell in the project folder:

```powershell
# Backend: create the environment and install Python dependencies once
py -3.14 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8001
```

In a second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite proxy forwards API calls to port 8001. For backend tests and the frontend type-check/build:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
Set-Location frontend
npm run build
```

The first embedding operation can take longer while the local embedding model is downloaded and initialized. Do not start a second copy of either server on the same ports.

## Server data and security

- Uploaded source files live under `backend/data/uploads`.
- Chroma's persistent vector index lives under `backend/data/chroma_db`.
- SQLite defaults to `backend/data/rag_chatbot.sqlite3`.
- Anonymous browser identities, session hashes, login lockouts, and private conversation ownership are stored in SQLite. Uploaded documents and vectors form a shared company library.
- Provider configuration and the settings-password hash are in `backend/.env`; this file is Git-ignored. Never paste it into a public issue or commit it.
- The provider key is sent to Gemini or Groq when generating answers. Embeddings and vector search remain local.
- The development servers use plain HTTP. Do not use the development server for actual employee credentials or company documents. For a company rollout, terminate TLS at a trusted reverse proxy and set `AUTH_COOKIE_SECURE=true`.
- The raw admin bootstrap key is printed only once by the local CLI; the server stores only its salted hash. Generate a replacement before the first claim if you lose it, and restart the backend after generation.
- A provider key was previously pasted into chat. If that key has not already been revoked, revoke it with its provider and replace it with a new key kept only in the local `.env` or entered through the protected Settings page. This guide deliberately contains no credential.
- Provider availability, model identifiers, quotas, and free-tier terms change. The default Groq model is `openai/gpt-oss-120b`; confirm current availability with the provider if a request reports a model or quota error.

## Troubleshooting and limitations

| Symptom | What to check |
|---|---|
| Admin claim reports the key is not configured | From the backend folder, run `python -m app.cli generate-admin-key`, then restart the backend and use the key printed in that terminal. |
| Employee cannot upload or change Settings | This is expected until that browser session claims administrator access. |
| Upload or Settings calls fail | Confirm the backend runs on port 8001 and the current browser session has administrator access. |
| Phone cannot reach the page | Use the PC's current Wi-Fi IPv4 address and port 5173, keep both devices on the same private network, and allow Node.js through Windows Firewall if prompted. |
| Upload is marked failed | Read the document's error in the Documents page; check supported type and file size. Scanned PDFs need OCR, which is not included. |
| First upload takes a while | Sentence Transformers may need to download the local embedding model before the first indexing pass. |
| Provider answers fail | Check the active provider, key, model name, internet access, provider account limits, and backend log output. |
| No useful source appears | Ask a more document-specific question, lower the similarity threshold, increase top-K, or reprocess after changing chunk settings. |

Files are stored unencrypted on local disk. Protect the Windows account and backups. Retrieved passages help ground the model but do not guarantee correctness; check citations, especially for important decisions. OCR for scanned pages and image files is not implemented.

## Glossary

- **API:** The backend's defined HTTP interface used by the browser.
- **Chunk / passage:** A small section of extracted document text used for search.
- **Embedding / vector:** A list of numbers representing the meaning of text; similar meanings tend to have nearby vectors.
- **RAG:** Retrieval-Augmented Generation: search for relevant passages, then provide them as context to a language model.
- **Similarity threshold:** The minimum search match score used to filter passages (except special broader retrieval flows such as summaries).
- **SSE:** Server-Sent Events, a simple HTTP format for delivering an answer incrementally.
- **SQLite / ChromaDB:** SQLite stores structured records and chat history; ChromaDB stores and searches passage vectors.
