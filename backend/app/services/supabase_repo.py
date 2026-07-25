"""Supabase persistence (service-role).

Writes chat sessions/messages and loads attached document texts directly to the
same Supabase project the Next.js app uses. Mirrors the persistence logic that
previously lived in app/api/chat/route.ts. Persistence failures are logged and
swallowed so a DB hiccup never blocks the assistant's reply.
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import logging

from supabase import AsyncClient, acreate_client

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def unique_document_ids(message: dict | None) -> list[str]:
    if not message or not isinstance(message.get("attachedDocumentIds"), list):
        return []
    seen: dict[str, None] = {}
    for doc_id in message["attachedDocumentIds"]:
        if isinstance(doc_id, str) and doc_id.strip():
            seen[doc_id] = None
    return list(seen)


def map_project_document(row: dict) -> dict:
    """DB row -> camelCase shape the frontend's normalizeDocument expects.

    Mirrors mapProjectDocument in lib/projects.ts so the proxied response is
    drop-in for the existing client.
    """
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "name": row.get("name"),
        "mimeType": row.get("mime_type"),
        "size": row.get("size"),
        "text": row.get("text"),
        "truncated": row.get("truncated"),
        "rawTextLength": row.get("raw_text_length"),
        "strategy": row.get("strategy") or "text",
        "uploadedAt": row.get("uploaded_at"),
    }


# Kept in sync with DRAFT_TOOL_NAME in app.pipelines.tools.drafting. Duplicated
# as a plain string so the services layer doesn't import the pipelines/LLM stack.
_DRAFT_TOOL_NAME = "draft_document"


def _draft_artifact(draft_call: dict, draft_states: dict[str, dict]) -> dict:
    """Build the frontend Artifact from a draft_document tool call + its state.

    The tool drafted + segmented the document and stored {status, file_name,
    blocks} in tool_state; the .docx is rendered on demand from those blocks. The
    frontend turns ``id`` into the download URL (/api/chat/{sessionId}/documents/{id}).
    """
    call_id = draft_call.get("id") or ""
    state = draft_states.get(call_id) or {}
    return {
        "id": call_id,
        "kind": "docx",
        "fileName": state.get("file_name") or "Документ",
        "status": state.get("status") or "failed",
    }


class SupabaseRepo:
    def __init__(self, client: AsyncClient):
        self._client = client
        # Serialize the read-then-insert seq assignment per session so two
        # concurrent turns (double-submit / retry) can't collide on seq.
        # Process-local: assumes a single backend instance.
        self._save_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Serialize check-then-insert of a document per object_key so a client
        # retry can't create a duplicate row. Process-local (single instance);
        # a DB unique(object_key) constraint would harden this across instances.
        self._doc_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @classmethod
    async def create(cls, url: str, service_role_key: str) -> "SupabaseRepo":
        client = await acreate_client(url, service_role_key)
        return cls(client)

    async def session_exists(self, session_id: str) -> bool:
        try:
            res = (
                await self._client.table("chat_sessions")
                .select("id")
                .eq("id", session_id)
                .maybe_single()
                .execute()
            )
            return bool(res and res.data)
        except Exception:
            logger.exception("Failed to check chat session", extra={"session_id": session_id})
            return False

    async def is_foreign_session(self, session_id: str, user_id: str | None) -> bool:
        """True only when the session PROVABLY belongs to a different user.

        Authoritative cross-tenant guard: this repo uses the service role and so
        bypasses Postgres RLS, which is exactly the path an attacker would use to
        post into someone else's chat (RLS can't help here — see the RLS
        migration note). We block only on a positive mismatch: a non-null owner
        that differs from ``user_id`` and no project-based access. Sessions with
        no explicit owner (legacy/ambiguous) are left alone to avoid locking
        legitimate users out.
        """
        try:
            res = (
                await self._client.table("chat_sessions")
                .select("user_id, project_id")
                .eq("id", session_id)
                .maybe_single()
                .execute()
            )
            row = res.data if res else None
        except Exception:
            logger.exception("Failed to authorize chat session", extra={"session_id": session_id})
            return False

        if not row:
            return False  # unknown / not yet created — treated as a new session
        owner = row.get("user_id")
        if not owner:
            return False  # no explicit owner: don't block
        if user_id and owner == user_id:
            return False  # the caller owns it directly

        # Owner differs — last chance is shared access via the session's project.
        project_id = row.get("project_id")
        if project_id and user_id:
            try:
                proj = (
                    await self._client.table("projects")
                    .select("id")
                    .eq("id", project_id)
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
                if proj and proj.data:
                    return False
            except Exception:
                logger.exception(
                    "Failed to check project ownership", extra={"session_id": session_id}
                )
                return False

        return True  # provably someone else's session

    async def get_messages(self, session_id: str) -> list[dict]:
        try:
            res = (
                await self._client.table("chat_messages")
                .select("*")
                .eq("session_id", session_id)
                .order("seq")
                .order("created_at")
                .execute()
            )
            rows = res.data or []
            # Draft documents are first-class artifacts: their blocks live in the
            # draft_document tool row's tool_state. Index those by call id so the
            # owning assistant message can carry a downloadable chip.
            draft_states = {
                row.get("tool_call_id"): (row.get("tool_state") or {})
                for row in rows
                if row.get("role") == "tool"
                and row.get("tool_name") == _DRAFT_TOOL_NAME
                and row.get("tool_call_id")
            }

            # User-facing rows: user messages, plain assistant answers, the
            # short note of a draft turn (assistant row carrying a draft_document
            # call), and assistant text that preceded a tool call (the streamed
            # preamble — the client commits it live, so reload must match).
            # Only text-less tool-call rows and tool rows stay context-only.
            messages = []
            artifacts_by_index: dict[int, list[dict]] = {}
            for row in rows:
                role = row.get("role")
                if role == "user":
                    messages.append(row)
                    continue
                if role != "assistant":
                    continue
                calls = row.get("tool_calls") or []
                draft_call = next(
                    (c for c in calls if isinstance(c, dict) and c.get("name") == _DRAFT_TOOL_NAME),
                    None,
                )
                if not calls:
                    messages.append(row)
                elif draft_call:
                    artifacts_by_index[len(messages)] = [_draft_artifact(draft_call, draft_states)]
                    messages.append(row)
                elif (row.get("content") or "").strip():
                    # Preamble before a (non-draft) tool call, e.g. reasoning
                    # before a search: it streamed to the user live, so it must
                    # survive a reload too.
                    messages.append(row)
                # else: text-less tool-call message (e.g. bare search) -> skip

            attached_ids: dict[str, None] = {}
            for message in messages:
                for doc_id in message.get("attached_document_ids") or []:
                    if isinstance(doc_id, str) and doc_id.strip():
                        attached_ids[doc_id] = None

            documents_by_id: dict[str, dict] = {}
            if attached_ids:
                project_id = await self.resolve_project_id(session_id)
                if project_id:
                    documents_by_id = await self.load_attached_documents(
                        project_id,
                        [{"attachedDocumentIds": list(attached_ids)}],
                    )

            # The raw DB row carries internal machinery (tool_calls with real
            # tool names/args, tool_state). Never put it on the wire: the UI
            # ignores it, and exposing it undoes the prompt-extraction
            # hardening (status events deliberately send only a human label).
            internal_keys = {"tool_calls", "tool_call_id", "tool_name", "tool_state"}
            return [
                {
                    **{k: v for k, v in message.items() if k not in internal_keys},
                    "attachedDocumentIds": [
                        doc_id
                        for doc_id in (message.get("attached_document_ids") or [])
                        if isinstance(doc_id, str)
                    ],
                    "attachedDocuments": [
                        documents_by_id[doc_id]
                        for doc_id in (message.get("attached_document_ids") or [])
                        if isinstance(doc_id, str) and doc_id in documents_by_id
                    ],
                    "artifacts": artifacts_by_index.get(index, []),
                }
                for index, message in enumerate(messages)
            ]
        except Exception:
            logger.exception("Failed to load chat messages", extra={"session_id": session_id})
            return []

    async def get_draft_state(self, session_id: str, draft_id: str) -> dict | None:
        """Load a drafted document's stored state ({status, file_name, blocks}) by
        the drafting tool's call id, for render-on-demand."""
        try:
            res = (
                await self._client.table("chat_messages")
                .select("tool_state")
                .eq("session_id", session_id)
                .eq("tool_call_id", draft_id)
                .eq("tool_name", _DRAFT_TOOL_NAME)
                .limit(1)
                .execute()
            )
            data = res.data or []
            return (data[0].get("tool_state") or {}) if data else None
        except Exception:
            logger.exception(
                "Failed to load draft state",
                extra={"session_id": session_id, "draft_id": draft_id},
            )
            return None

    async def load_history(self, session_id: str) -> list[dict] | None:
        """Ordered history (user/assistant/tool) for LLM context assembly.

        Tool rows and assistant tool-call metadata are included so the agent
        loop can be rebuilt faithfully. Returns None on failure (not []) so the
        caller can distinguish "empty session" from "DB unavailable" and fall
        back to client-provided history.
        """
        try:
            res = (
                await self._client.table("chat_messages")
                .select(
                    "role, content, attached_document_ids, tool_calls, "
                    "tool_call_id, tool_name, tool_state"
                )
                .eq("session_id", session_id)
                .order("seq")
                .order("created_at")
                .execute()
            )
        except Exception:
            logger.exception("Failed to load history", extra={"session_id": session_id})
            return None

        history: list[dict] = []
        for row in res.data or []:
            role = row.get("role")
            if role == "tool":
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": row.get("tool_call_id") or "",
                        "tool_name": row.get("tool_name") or "",
                        "tool_state": row.get("tool_state") or {},
                    }
                )
            elif role == "assistant":
                history.append(
                    {
                        "role": "assistant",
                        "content": row.get("content") or "",
                        "tool_calls": row.get("tool_calls") or None,
                    }
                )
            elif role == "user":
                history.append(
                    {
                        "role": "user",
                        "content": row.get("content") or "",
                        "attachedDocumentIds": [
                            doc_id
                            for doc_id in (row.get("attached_document_ids") or [])
                            if isinstance(doc_id, str)
                        ],
                    }
                )
        return history

    async def resolve_project_id(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        try:
            res = (
                await self._client.table("chat_sessions")
                .select("project_id")
                .eq("id", session_id)
                .maybe_single()
                .execute()
            )
            if res and res.data:
                return res.data.get("project_id")
        except Exception:
            logger.exception("Failed to resolve project for session", extra={"session_id": session_id})
        return None

    async def load_attached_documents(
        self, project_id: str | None, messages: list[dict]
    ) -> dict[str, dict]:
        ids: dict[str, None] = {}
        for message in messages:
            for doc_id in unique_document_ids(message):
                ids[doc_id] = None
        id_list = list(ids)

        if not project_id or not id_list:
            return {}

        try:
            res = (
                await self._client.table("project_documents")
                .select("id, name, text")
                .eq("project_id", project_id)
                .in_("id", id_list)
                .execute()
            )
        except Exception:
            logger.exception("Failed to load attached documents", extra={"project_id": project_id})
            return {}

        documents: dict[str, dict] = {}
        for doc in res.data or []:
            text = doc.get("text")
            if isinstance(doc.get("id"), str) and isinstance(text, str) and text.strip():
                documents[doc["id"]] = {
                    "id": doc["id"],
                    "name": doc.get("name") or "Документ",
                    "text": text,
                }
        return documents

    async def create_session(
        self,
        session_id: str,
        user_id: str | None,
        project_id: str | None,
        initial_message: str,
        utm: dict | None,
    ) -> bool:
        try:
            await self._client.table("chat_sessions").insert(
                {
                    "id": session_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "initial_message": initial_message,
                    "created_at": _now_iso(),
                    "utm": utm or None,
                }
            ).execute()
            return True
        except Exception:
            logger.exception("Failed to create chat session", extra={"session_id": session_id})
            return False

    async def _next_seq(self, session_id: str) -> int:
        """Next per-session sequence number (rows ordered globally by seq)."""
        try:
            res = (
                await self._client.table("chat_messages")
                .select("seq")
                .eq("session_id", session_id)
                .order("seq", desc=True)
                .limit(1)
                .execute()
            )
            if res.data and res.data[0].get("seq") is not None:
                return int(res.data[0]["seq"]) + 1
        except Exception:
            logger.exception("Failed to read max seq", extra={"session_id": session_id})
        return 0

    async def save_messages(self, session_id: str, rows: list[dict]) -> None:
        """Persist a turn's rows (user + generated assistant/tool messages).

        Each row is a normalized dict produced by the pipeline; this method only
        maps it to DB columns and assigns monotonic ``seq`` values.
        """
        if not rows:
            return
        try:
            async with self._save_locks[session_id]:
                start = await self._next_seq(session_id)
                records = []
                for offset, row in enumerate(rows):
                    records.append(
                        {
                            "session_id": session_id,
                            "role": row["role"],
                            "content": row.get("content") or "",
                            "attached_document_ids": row.get("attached_document_ids") or [],
                            "tool_calls": row.get("tool_calls"),
                            "tool_call_id": row.get("tool_call_id"),
                            "tool_name": row.get("tool_name"),
                            "tool_state": row.get("tool_state"),
                            "seq": start + offset,
                            "created_at": _now_iso(),
                        }
                    )
                await self._client.table("chat_messages").insert(records).execute()
        except Exception:
            logger.exception("Failed to save chat messages", extra={"session_id": session_id})

    # --- project_documents (document extraction write path) ---
    #
    # Unlike the chat path, these DO NOT swallow errors: the caller (the extract
    # endpoint) must surface a failure to the client rather than silently drop a
    # document.

    def document_lock(self, object_key: str) -> asyncio.Lock:
        return self._doc_locks[object_key]

    async def get_document_by_object_key(
        self, project_id: str, object_key: str
    ) -> dict | None:
        res = (
            await self._client.table("project_documents")
            .select("*")
            .eq("project_id", project_id)
            .eq("object_key", object_key)
            .order("uploaded_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    async def insert_project_document(self, record: dict) -> dict:
        res = await self._client.table("project_documents").insert([record]).execute()
        rows = res.data or []
        if not rows:
            raise RuntimeError("project_documents insert returned no row")
        return rows[0]

    async def update_project_document_if_improved(
        self, document_id: str, fields: dict, *, min_raw_text_length: int | None
    ) -> dict | None:
        """Compare-and-set replacement of a partial extraction (the WHERE clause
        is atomic in Postgres, so concurrent retries on different instances
        can't clobber each other): only while the row is still ``truncated`` (a
        complete extraction is never overwritten), and — when the new result is
        itself partial (``min_raw_text_length`` set) — only if it actually adds
        coverage. Returns None when the update lost the race / did not improve."""
        query = (
            self._client.table("project_documents")
            .update(fields)
            .eq("id", document_id)
            .eq("truncated", True)
        )
        if min_raw_text_length is not None:
            query = query.lt("raw_text_length", min_raw_text_length)
        res = await query.execute()
        rows = res.data or []
        return rows[0] if rows else None

    async def touch_project(
        self, project_id: str, user_id: str | None, now: str
    ) -> None:
        """Bump projects.updated_at (drives UI sort order). Non-fatal on failure."""
        try:
            query = self._client.table("projects").update({"updated_at": now}).eq("id", project_id)
            if user_id:
                query = query.eq("user_id", user_id)
            await query.execute()
        except Exception:
            logger.warning("Failed to touch project updated_at", extra={"project_id": project_id})
