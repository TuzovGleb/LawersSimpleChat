# LawersSimpleChat — Python Backend

FastAPI + LangGraph chat backend for the legal assistant. It owns the chat
generation pipeline (system prompt, conversation context, attached-document
injection, OpenRouter call with web search), persists conversations to Supabase,
and emits LangSmith traces for prod diagnosis. The Next.js app talks to it
through a thin authenticated proxy at `app/api/chat`.

## Architecture

```
Browser ──> Next.js /api/chat (proxy: validates Supabase session)
              │  forwards body + X-Backend-Secret
              ▼
        FastAPI /chat  ──>  LangGraph: build_context ──> generate (OpenRouter + web search)
              │                                   │
              │ persists turn                     └─ traced in LangSmith (grouped by session)
              ▼
        Supabase (chat_sessions, chat_messages, project_documents)
```

The `/chat` endpoint reproduces the exact SSE contract the frontend already
parses: `:heartbeat` keep-alives followed by a single
`data: {message, sessionId, projectId, metadata}` event.

## Layout

| Path | Purpose |
|------|---------|
| `app/config.py`, `app/config/*.yaml` | metayaml config + logging bootstrap |
| `app/rag_core/prompt.py` | legal system prompt (synced with `lib/prompts.ts`) |
| `app/rag_core/llm.py` | OpenRouter model registry + web-search plugin |
| `app/rag_core/persistence.py` | LangGraph checkpointer (memory/postgres) |
| `app/pipelines/` | graph state, nodes (`build_context`, `generate`), workflow |
| `app/services/supabase_repo.py` | Supabase reads/writes (service role) |
| `app/server/` | FastAPI app, SSE streaming, shared-secret auth |

## Run locally

```bash
cd backend
cp .env.example .env   # fill in OPENROUTER_API_KEY, SUPABASE_*, BACKEND_SHARED_SECRET
uv sync
uv run uvicorn app.server.main:app --reload --port 8001
```

Point the Next.js app at it by setting `BACKEND_URL=http://localhost:8001` and
the same `BACKEND_SHARED_SECRET` in the Next.js `.env`.

## Docker

```bash
docker build -t lawers-chat-backend .
docker run --env-file .env -p 8001:8001 lawers-chat-backend
```

## Adding RAG later

The graph is intentionally minimal. Insert a `retrieve` node between
`build_context` and `generate` in `app/pipelines/workflows.py` and feed retrieved
context into the state — the rest of the pipeline, persistence, and tracing stay
unchanged.
