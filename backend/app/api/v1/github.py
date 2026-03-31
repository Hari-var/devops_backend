from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Cookie, HTTPException

router = APIRouter()
logger = logging.getLogger(__name__)

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _auth_headers(gh_token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {gh_token}"}


@router.get("/repositories", summary="List authenticated user's GitHub repositories")
async def list_repositories(
    gh_token: str | None = Cookie(default=None),
) -> list[dict]:
    """
    List GitHub repositories for the authenticated user.
    Returns id, name, full_name, default_branch, language, private flag.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(
            "https://api.github.com/user/repos?per_page=100&sort=updated",
            headers=_auth_headers(gh_token),
        )

    if res.status_code >= 400:
        logger.error("GitHub repos fetch failed: status=%s", res.status_code)
        raise HTTPException(status_code=502, detail="Failed to fetch repositories from GitHub.")

    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "full_name": r.get("full_name"),
            "default_branch": r.get("default_branch", "main"),
            "language": r.get("language"),
            "private": r.get("private", False),
        }
        for r in res.json()
    ]
