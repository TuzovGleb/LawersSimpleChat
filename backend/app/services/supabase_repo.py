"""Supabase persistence (service-role).

Writes chat sessions/messages and loads attached document texts directly to the
same Supabase project the Next.js app uses. Mirrors the persistence logic that
previously lived in app/api/chat/route.ts. Persistence failures are logged and
swallowed so a DB hiccup never blocks the assistant's reply.
"""
from datetime import datetime, timezone
import logging

from supabase import AsyncClient, acreate_client

logger = logging.getLogger(__name__)

MAX_CONTEXT_DOCUMENTS = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def unique_document_ids(message: dict | None) -> list[str]:
    if not message or not isinstance(message.get("attachedDocumentIds"), list):
        return []
    seen: dict[str, None] = {}
    for doc_id in message["attachedDocumentIds"]:
        if isinstance(doc_id, str) and doc_id.strip():
            seen[doc_id] = None
    return list(seen)[:MAX_CONTEXT_DOCUMENTS]


class SupabaseRepo:
    def __init__(self, client: AsyncClient):
        self._client = client

    @classmethod
    async def create(cls, url: str, service_role_key: str) -> "SupabaseRepo":
        client = await acreate_client(url, service_role_key)
        return cls(client)

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
        id_list = list(ids)[:MAX_CONTEXT_DOCUMENTS]

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

    async def save_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        attached_document_ids: list[str],
    ) -> None:
        try:
            await self._client.table("chat_messages").insert(
                [
                    {
                        "session_id": session_id,
                        "role": "user",
                        "content": user_content,
                        "attached_document_ids": attached_document_ids,
                        "created_at": _now_iso(),
                    },
                    {
                        "session_id": session_id,
                        "role": "assistant",
                        "content": assistant_content,
                        "attached_document_ids": [],
                        "created_at": _now_iso(),
                    },
                ]
            ).execute()
        except Exception:
            logger.exception("Failed to save chat messages", extra={"session_id": session_id})
