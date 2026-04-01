from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse, Response

router = APIRouter()
logger = logging.getLogger(__name__)

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_SCOPES = "repo workflow read:user"

_COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "").lower() == "true"
_FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")

# In-memory state store (use Redis in production)
_oauth_states: dict[str, datetime] = {}


def _get_oauth_config() -> tuple[str, str, str]:
    """Return (client_id, client_secret, callback_url) or raise 503."""
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
    callback_url = os.getenv("GITHUB_OAUTH_CALLBACK_URL", "")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured.")
    return client_id, client_secret, callback_url


@router.get("/github", summary="Start GitHub OAuth flow")
async def github_oauth_start(request: Request) -> RedirectResponse:  # noqa: ARG001
    """
    Initiate GitHub OAuth flow.
    Generates a random state token stored server-side to prevent CSRF.
    """
    client_id, _, callback_url = _get_oauth_config()
    state = secrets.token_urlsafe(32)

    # Store state server-side with expiry
    _oauth_states[state] = datetime.now() + timedelta(minutes=5)
    logger.info(f"OAuth Start - state: {state}")

    params = (
        f"client_id={client_id}"
        f"&redirect_uri={callback_url}"
        f"&scope={_GITHUB_SCOPES.replace(' ', '%20')}"
        f"&state={state}"
    )
    return RedirectResponse(f"{_GITHUB_AUTHORIZE_URL}?{params}")


@router.get("/github/callback", summary="GitHub OAuth callback")
async def github_oauth_callback(code: str, state: str, request: Request) -> RedirectResponse:
    """
    GitHub OAuth callback.
    Validates CSRF state, exchanges code for access token, stores in HttpOnly cookie.
    Redirects to /approvals on success.
    """
    client_id, client_secret, _ = _get_oauth_config()
    
    logger.info(f"Callback received - state: {state}")
    
    # Validate state from server-side store
    if state not in _oauth_states:
        logger.error("State not found in server store")
        raise HTTPException(status_code=400, detail="Invalid OAuth state — possible CSRF attack.")
    
    # Check expiry
    if datetime.now() > _oauth_states[state]:
        del _oauth_states[state]
        logger.error("State expired")
        raise HTTPException(status_code=400, detail="OAuth state expired.")
    
    # Remove used state
    del _oauth_states[state]
    logger.info("State validated successfully")

    async with httpx.AsyncClient(timeout=15) as client:
        token_res = await client.post(
            _GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            },
        )

    if token_res.status_code >= 400:
        raise HTTPException(status_code=502, detail="Failed to exchange GitHub OAuth code.")

    payload = token_res.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="GitHub OAuth token missing in response.")

    response = RedirectResponse(url=f"{_FRONTEND_URL}/approvals", status_code=302)
    response.set_cookie(
        key="gh_token",
        value=access_token,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        max_age=3600,
    )
    response.delete_cookie("oauth_state", samesite="none", secure=True)
    return response


@router.get("/me", summary="Get current authenticated user")
async def get_current_user() -> dict[str, str]:
    """Return the current authenticated user identity (stub — extend with real session lookup)."""
    return {"username": "demo-user", "provider": "github"}


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Suppress noisy 404s from browser favicon requests during OAuth redirects."""
    return Response(status_code=204)
