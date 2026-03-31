"""
GitHub Webhook receiver.

GitHub sends POST /api/webhooks/github on every push event.
We verify the HMAC-SHA256 signature, check if config.py was changed,
parse it, and create a pending Approval row in the DB.

No polling needed when this endpoint is reachable from the internet.
For local dev the poller in approvals.py acts as fallback.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import logging
import os
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ...config import get_settings
from ...db import AsyncSessionLocal
from ...models import Approval

router = APIRouter()
logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify X-Hub-Signature-256 header using HMAC-SHA256."""
    if not secret:
        # No secret configured — skip verification (dev mode only)
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature check")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Config.py helpers
# ---------------------------------------------------------------------------

def _parse_config(source: str) -> dict:
    """Safely parse config.py — only literal values via ast."""
    tree = ast.parse(source)
    result: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return result


async def _fetch_file(repo: str, path: str, ref: str, token: str) -> str | None:
    """Fetch a file from GitHub Contents API and return decoded text."""
    url = f"{_GITHUB_API}/repos/{repo}/contents/{path}"
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(
            url,
            headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
            params={"ref": ref},
        )
    if res.status_code != 200:
        return None
    data = res.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content")


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/github")
async def github_webhook(request: Request) -> dict:
    """
    Receive GitHub push webhook events.

    GitHub sends this on every push. We:
    1. Verify the HMAC signature
    2. Check if config.py is in the changed files list
    3. Fetch + parse config.py content
    4. Save a pending Approval to the DB
    """
    body = await request.body()
    settings = get_settings()

    # -- Signature check ----------------------------------------------------
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(body, sig, settings.github_webhook_secret):
        logger.warning("Webhook: invalid signature — rejected")
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    # -- Only handle push events --------------------------------------------
    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type == "ping":
        return {"status": "pong"}
    if event_type != "push":
        return {"status": "ignored", "event": event_type}

    payload: dict = await request.json()

    # Extract push metadata
    repo: str = payload.get("repository", {}).get("full_name", "")
    branch_ref: str = payload.get("ref", "")                          # refs/heads/main
    branch: str = branch_ref.removeprefix("refs/heads/")
    commit_sha: str = payload.get("after", "")
    head_commit: dict = payload.get("head_commit") or {}
    commit_message: str = (head_commit.get("message") or "").split("\n")[0]
    committed_by: str = (head_commit.get("author") or {}).get("name", "unknown")
    committed_at: str = (head_commit.get("timestamp") or "")

    # Collect all changed files across all commits in this push
    changed_files: list[str] = []
    for commit in payload.get("commits", []):
        changed_files += commit.get("added", [])
        changed_files += commit.get("modified", [])
        changed_files += commit.get("removed", [])
    changed_files = list(set(changed_files))  # deduplicate

    if not repo or not commit_sha:
        return {"status": "ignored", "reason": "missing repo or sha"}

    # -- Check if config.py was changed -------------------------------------
    config_py_changed = any(
        f == "config.py" or f.endswith("/config.py")
        for f in changed_files
    )
    if not config_py_changed:
        logger.debug("Webhook: push to %s — no config.py change, ignoring", repo)
        return {"status": "ignored", "reason": "config.py not in changed files"}

    logger.info("Webhook: config.py changed in %s @ %s", repo, commit_sha[:7])

    # -- Dedup: skip if we already have a non-rejected approval for this SHA -
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(Approval).where(
                Approval.repo == repo,
                Approval.commit_sha == commit_sha[:7],
                Approval.status != "rejected",
            )
        )
        if existing.scalar_one_or_none():
            logger.info("Webhook: approval already exists for %s @ %s", repo, commit_sha[:7])
            return {"status": "duplicate"}

    # -- Fetch and parse config.py ------------------------------------------
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        logger.warning("Webhook: no PAT set — cannot fetch config.py content")
        return {"status": "error", "reason": "no PAT configured"}

    raw = await _fetch_file(repo, "config.py", commit_sha, token)
    if not raw:
        logger.warning("Webhook: could not fetch config.py from %s @ %s", repo, commit_sha[:7])
        return {"status": "error", "reason": "could not fetch config.py"}

    try:
        config_data = _parse_config(raw)
    except SyntaxError as exc:
        logger.warning("Webhook: config.py syntax error in %s: %s", repo, exc)
        return {"status": "error", "reason": f"config.py syntax error: {exc}"}

    # -- Save Approval to DB ------------------------------------------------
    approval = Approval(
        id=str(uuid.uuid4()),
        repo=repo,
        branch=branch,
        commit_sha=commit_sha[:7],
        commit_message=commit_message,
        committed_by=committed_by,
        committed_at=committed_at,
        changed_files=changed_files,
        config=config_data,
        detected_tech={},
        pipeline_stage=0,
        stage_logs={},
        status="pending",
        logs=[],
        terraform_url=None,
        deployed_url=None,
        actions_run_url=None,
        created_at=time.time(),
    )
    async with AsyncSessionLocal() as db:
        db.add(approval)
        await db.commit()

    logger.info("Webhook: approval created id=%s repo=%s sha=%s", approval.id, repo, commit_sha[:7])
    return {"status": "created", "approval_id": approval.id}
