"""Shared-secret auth between the Next.js proxy and this backend."""
import logging

from fastapi import Header, HTTPException, Request, status

logger = logging.getLogger(__name__)


async def verify_backend_secret(
    request: Request, x_backend_secret: str | None = Header(default=None)
) -> None:
    expected = request.app.state.config["app"].get("backend_secret")
    # If no secret is configured, the backend is assumed to run on a trusted
    # network (e.g. behind the Next.js proxy only). Warn loudly once per call.
    if not expected:
        logger.warning("BACKEND_SHARED_SECRET is not set; skipping proxy auth check")
        return
    if x_backend_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid backend secret")
