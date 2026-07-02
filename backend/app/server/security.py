"""Shared-secret auth between the Next.js proxy and this backend."""
import logging

from fastapi import Header, HTTPException, Request, status

logger = logging.getLogger(__name__)


async def verify_backend_secret(
    request: Request, x_backend_secret: str | None = Header(default=None)
) -> None:
    app_cfg = request.app.state.config["app"]
    expected = app_cfg.get("backend_secret")
    if not expected:
        # FAIL CLOSED. The container is deployed allow-unauthenticated-invoke, so
        # a missing secret with the old fail-open behaviour left every endpoint
        # open to the internet. Reject unless the operator explicitly opts into
        # insecure mode (BACKEND_ALLOW_INSECURE=1) for local development only.
        if app_cfg.get("allow_insecure"):
            logger.warning(
                "BACKEND_SHARED_SECRET is not set and BACKEND_ALLOW_INSECURE is on; "
                "skipping proxy auth check (dev only — never do this in production)"
            )
            return
        logger.error(
            "BACKEND_SHARED_SECRET is not set; refusing request. Set the secret, or "
            "set BACKEND_ALLOW_INSECURE=1 for local development only."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Backend not configured"
        )
    if x_backend_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid backend secret")
