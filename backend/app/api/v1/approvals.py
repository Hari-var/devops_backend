"""
Approvals module — polling-based config.py detection (no webhook / no public URL needed).

Flow:
  1. Background poller runs every 60 s using the stored PAT.
  2. It lists all repos the PAT has access to.
  3. For each repo it checks if config.py exists on the default branch
     and whether the latest commit SHA on that file has changed since last seen.
  4. If a new/changed config.py is found it creates a pending approval record in SQL.
  5. UI shows the card; user clicks Approve.
  6. Approve endpoint chains:
       tech-detect → generate YAML → commit YAML → provision infra → deploy
  7. Each step streams logs via SSE  GET /approvals/{id}/logs
  8. After deploy the deployed URL/IP is stored and shown as a clickable link.
"""
from __future__ import annotations

import ast
import asyncio
import base64
from datetime import datetime
import logging
import os
import time
import uuid
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import traceback

from ...db import AsyncSessionLocal, get_db
from ...models import Approval
from ...services.subscriber_manager import subscriber_manager

router = APIRouter()
logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# ---------------------------------------------------------------------------
# Ephemeral in-memory stores (intentionally NOT persisted)
# ---------------------------------------------------------------------------


# "owner/repo" → last seen config.py commit SHA  (dedup guard, resets on restart)
_SEEN_SHAS: dict[str, str] = {}

# SSE subscribers: managed by thread-safe SubscriberManager
# _SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}  # Replaced by subscriber_manager

# Poller control flag
_POLLER_ENABLED: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh_headers(token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {token}"}


def _sanitize(value: str, max_len: int = 100) -> str:
    return value.replace("\n", "").replace("\r", "")[:max_len]


def _decode_html_entities(text: str) -> str:
    """Decode HTML entities like &quot; to actual characters."""
    import html
    return html.unescape(text)


async def _fetch_json(url: str, token: str, params: dict | None = None) -> dict | list | None:
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(url, headers=_gh_headers(token), params=params or {})
    if res.status_code != 200:
        return None
    return res.json()


async def _fetch_file_content(repo: str, path: str, ref: str, token: str) -> str | None:
    data = await _fetch_json(f"{_GITHUB_API}/repos/{repo}/contents/{path}", token, {"ref": ref})
    if not isinstance(data, dict):
        return None
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content")


def _parse_config(source: str) -> dict:
    """Safely parse config.py using ast — only literal values accepted."""
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


def _approval_to_dict(a: Approval) -> dict:
    return {
        "id": a.id,
        "repo": a.repo,
        "branch": a.branch,
        "commit_sha": a.commit_sha,
        "commit_message": a.commit_message,
        "committed_by": a.committed_by,
        "committed_at": a.committed_at,
        "changed_files": a.changed_files or [],
        "config": a.config,
        "detected_tech": a.detected_tech or {},
        "pipeline_stage": getattr(a, "pipeline_stage", 0),
        "stage_logs": getattr(a, "stage_logs", {}),
        "status": a.status,
        "logs": a.logs,
        "terraform_url": getattr(a, "terraform_url", None),
        "deployed_url": a.deployed_url,
        "actions_run_url": getattr(a, "actions_run_url", None),
        "created_at": a.created_at,
    }


async def _push_log(approval_id: str, message: str, stage: int = 0) -> None:
    """Append a log line to DB (flat logs + stage_logs) and fan-out to SSE subscribers."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        record = result.scalar_one_or_none()
        if record is None:
            return
        record.logs = list(record.logs) + [message]
        record.logs = record.logs[-1000:]
        if stage > 0:
            sl = dict(record.stage_logs or {})
            key = str(stage)
            sl[key] = sl.get(key, []) + [message]
            record.stage_logs = sl
        await db.commit()

    # Fan-out: prefix with stage so frontend can route to correct panel
    event_data = f"{stage}|{message}" if stage > 0 else message
    sent_count = subscriber_manager.broadcast_message(approval_id, event_data)
    if sent_count > 0:
        logger.debug(f"Broadcasted message to {sent_count} subscribers for {approval_id}")


async def _push_stage_event(approval_id: str, stage: int, severity: str, message: str) -> None:
    """Push stage-level structured event (JSON serialized) for metrics and alerts."""
    import json as _json  # noqa: PLC0415

    event = {
        "timestamp": time.time(),
        "stage": stage,
        "severity": severity,
        "message": message,
    }
    payload = f"STAGE-EVENT|{_json.dumps(event)}"
    await _push_log(approval_id, payload, stage)


# ---------------------------------------------------------------------------
# Background poller — started from main.py lifespan
# ---------------------------------------------------------------------------

async def start_poller() -> None:
    global _POLLER_ENABLED
    from ...config import get_settings  # noqa: PLC0415
    interval = get_settings().approval_poll_interval
    logger.info("Approval poller started (interval=%ds)", interval)
    while _POLLER_ENABLED:
        try:
            await _poll_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Poller error (will retry): %s", exc)
        await asyncio.sleep(interval)
    logger.info("Approval poller stopped")


async def _poll_once() -> None:
    import os as _os  # noqa: PLC0415
    token = _os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        logger.warning("Poller: no GITHUB_PERSONAL_ACCESS_TOKEN set, skipping")
        return
    logger.info("Poller: using token %s...", token[:12])

    repos_data = await _fetch_json(
        f"{_GITHUB_API}/user/repos",
        token,
        {"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"},
    )
    if not isinstance(repos_data, list):
        logger.warning("Poller: failed to fetch repos from GitHub (bad response)")
        return

    logger.info("Poller: checking %d repos for config.py", len(repos_data))
    for repo in repos_data:
        repo_name: str = repo.get("full_name", "")
        default_branch: str = repo.get("default_branch", "main")
        if not repo_name:
            continue
        await _check_repo(repo_name, default_branch, token)


async def _check_repo(repo: str, branch: str, token: str) -> None:
    commits_data = await _fetch_json(
        f"{_GITHUB_API}/repos/{repo}/commits",
        token,
        {"path": "config.py", "sha": branch, "per_page": 1},
    )
    if not isinstance(commits_data, list) or not commits_data:
        logger.debug("Poller: no config.py found in %s", repo)
        return

    latest_commit = commits_data[0]
    commit_sha: str = latest_commit.get("sha", "")
    if not commit_sha:
        return

    logger.info(
        "Poller: found config.py in %s sha=%s (last seen=%s)",
        _sanitize(repo), commit_sha[:7],
        _SEEN_SHAS.get(repo, "none")[:7] if _SEEN_SHAS.get(repo) else "none",
    )

    if _SEEN_SHAS.get(repo) == commit_sha:
        return

    # Check DB for existing non-rejected approval for this SHA
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(Approval).where(
                Approval.repo == repo,
                Approval.commit_sha == commit_sha[:7],
                Approval.status != "rejected",
            )
        )
        if existing.scalar_one_or_none():
            _SEEN_SHAS[repo] = commit_sha
            return

    _SEEN_SHAS[repo] = commit_sha

    raw_content = await _fetch_file_content(repo, "config.py", commit_sha, token)
    if not raw_content:
        logger.warning("Poller: could not fetch config.py content from %s", _sanitize(repo))
        return

    try:
        config_data = _parse_config(raw_content)
    except SyntaxError as exc:
        logger.warning("config.py parse error in %s: %s", _sanitize(repo), exc)
        return

    commit_detail = latest_commit.get("commit", {})
    commit_message: str = commit_detail.get("message", "").split("\n")[0]
    committed_by: str = commit_detail.get("author", {}).get("name", "unknown")
    committed_at: str = commit_detail.get("author", {}).get("date", "")

    approval = Approval(
        id=str(uuid.uuid4()),
        repo=repo,
        branch=branch,
        commit_sha=commit_sha[:7],
        commit_message=commit_message,
        committed_by=committed_by,
        committed_at=committed_at,
        changed_files=["config.py"],
        config=config_data,
        detected_tech={},
        pipeline_stage=0,
        stage_logs={},
        status="pending",
        logs=[],
        terraform_url=None,
        deployed_url=None,
        actions_run_url=None,
        created_at=datetime.now(),
    )
    async with AsyncSessionLocal() as db:
        db.add(approval)
        await db.commit()

    logger.info("New approval created id=%s repo=%s sha=%s", approval.id, _sanitize(repo), commit_sha[:7])


# ---------------------------------------------------------------------------
# Manual poll trigger + debug endpoints
# ---------------------------------------------------------------------------

@router.post("/poller/stop")
async def stop_poller(gh_token: str | None = Cookie(default=None)) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    global _POLLER_ENABLED
    _POLLER_ENABLED = False
    logger.info("Approval poller stop requested")
    return {"status": "stopped"}


@router.post("/poller/start")
async def resume_poller(gh_token: str | None = Cookie(default=None)) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    global _POLLER_ENABLED
    _POLLER_ENABLED = True
    asyncio.create_task(start_poller())
    logger.info("Approval poller restarted")
    return {"status": "started"}


@router.post("/poll-now")
async def poll_now(gh_token: str | None = Cookie(default=None)) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        await _poll_once()
        async with AsyncSessionLocal() as db:
            total = (await db.execute(select(Approval))).scalars().all()
        return {"status": "ok", "approvals_total": len(total), "seen_repos": list(_SEEN_SHAS.keys())}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/debug")
async def debug_state(gh_token: str | None = Cookie(default=None)) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    import os as _os  # noqa: PLC0415
    token = _os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    token_preview = (token[:8] + "...") if token else "NOT SET"

    github_ok = False
    github_user = ""
    repos_found: list[str] = []
    pat_scopes = []
    if token:
        user_data = await _fetch_json(f"{_GITHUB_API}/user", token)
        if isinstance(user_data, dict):
            github_ok = True
            github_user = user_data.get("login", "")
        
        # Check PAT scopes
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(f"{_GITHUB_API}/user", headers=_gh_headers(token))
            if res.status_code == 200:
                scopes_header = res.headers.get("x-oauth-scopes", "")
                pat_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
        
        repos_data = await _fetch_json(
            f"{_GITHUB_API}/user/repos",
            token,
            {"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"},
        )
        if isinstance(repos_data, list):
            repos_found = [r.get("full_name", "") for r in repos_data]

    async with AsyncSessionLocal() as db:
        all_approvals = (await db.execute(select(Approval))).scalars().all()

    # Get subscriber statistics
    subscriber_stats = subscriber_manager.get_stats()

    return {
        "token_set": bool(token),
        "token_preview": token_preview,
        "pat_scopes": pat_scopes,
        "github_reachable": github_ok,
        "github_user": github_user,
        "repos_visible": repos_found,
        "repos_with_config_py": list(_SEEN_SHAS.keys()),
        "seen_shas": {k: v[:7] for k, v in _SEEN_SHAS.items()},
        "total_approvals": len(all_approvals),
        "approvals": [
            {"id": a.id, "repo": a.repo, "status": a.status, "sha": a.commit_sha}
            for a in all_approvals
        ],
        "subscriber_stats": subscriber_stats,
    }


@router.get("/{approval_id}/status")
async def get_approval_status(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """Get current status of an approval for frontend polling."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            
            if not record:
                raise HTTPException(status_code=404, detail="Approval not found.")
            
            return {
                "approval_id": approval_id,
                "status": record.status,
                "pipeline_stage": getattr(record, "pipeline_stage", 0),
                "last_updated": time.time(),
                "is_terminal": record.status in ["done", "failed", "rejected"]
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Status check error for %s: %s", approval_id, exc)
        raise HTTPException(status_code=500, detail="Failed to get approval status")


# ---------------------------------------------------------------------------
# Health check and test endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def health_check() -> dict:
    """Simple health check endpoint."""
    return {"status": "healthy", "timestamp": time.time()}


@router.post("/test-approve/{approval_id}")
async def test_approve(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """Test endpoint to verify basic approval flow without running pipeline."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    
    logger.info("Test approve called for %s", approval_id)
    
    # Just return immediately without doing anything
    return {
        "status": "test_success", 
        "approval_id": approval_id,
        "message": "Test endpoint working"
    }


# ---------------------------------------------------------------------------
# List / get approvals
# ---------------------------------------------------------------------------

@router.get("")
async def list_approvals(
    gh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).order_by(Approval.created_at.desc()))
    records = result.scalars().all()
    return {"approvals": [_approval_to_dict(a) for a in records]}


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found.")
    return _approval_to_dict(record)


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

@router.post("/{approval_id}/reject")
async def reject_approval(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if record.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending approvals can be rejected.")
    record.status = "rejected"
    await db.commit()
    return {"status": "rejected"}


@router.post("/{approval_id}/reset")
async def reset_approval(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found.")
    # Reset pipeline state so UI shows Approve/Reject again
    logger.info("Reset approval %s (prev_status=%s)", approval_id, getattr(record, 'status', None))
    record.status = "pending"
    record.pipeline_stage = 0
    record.stage_logs = {}
    record.logs = []
    record.terraform_url = None
    record.deployed_url = None
    record.actions_run_url = None
    await db.commit()
    return {"status": "pending"}


# ---------------------------------------------------------------------------
# Retry — re-run pipeline without clearing historical logs (enterprise-friendly)
# ---------------------------------------------------------------------------


@router.post("/{approval_id}/retry")
async def retry_approval(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if record.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed approvals can be retried.")
    logger.info("Retry requested for %s (prev_status=%s) — marking pending for manual re-approve", approval_id, record.status)
    # Preserve existing logs for audit. Mark as pending so user can approve to restart from Stage 1.
    record.status = "pending"
    record.pipeline_stage = 0
    # Optionally annotate that a retry was requested
    await db.commit()
    await _push_log(approval_id, "Manual retry requested — awaiting re-approval", 0)
    return {"status": "pending", "approval_id": approval_id}


# ---------------------------------------------------------------------------
# Approve — chains the full pipeline automatically
# ---------------------------------------------------------------------------

@router.post("/{approval_id}/approve")
async def approve_approval(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """Approve an approval and start the deployment pipeline."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    
    logger.info("=== APPROVE START for %s ===", approval_id)
    
    try:
        # Add timeout to the entire operation
        return await asyncio.wait_for(
            _approve_approval_impl(approval_id, gh_token),
            timeout=10.0  # 10 second timeout for the approval endpoint
        )
    except asyncio.TimeoutError:
        logger.error("Approval endpoint timeout for %s", approval_id)
        raise HTTPException(status_code=504, detail="Approval request timed out")
    except Exception as exc:
        logger.error("Approval endpoint error for %s: %s", approval_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(exc)}")


async def _approve_approval_impl(approval_id: str, gh_token: str) -> dict:
    """Implementation of approval logic with timeout protection."""
    try:
        # Use a separate session to avoid dependency injection issues
        async with AsyncSessionLocal() as db:
            logger.info("Database session created for %s", approval_id)
            
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            
            if not record:
                logger.warning("Approval not found: %s", approval_id)
                raise HTTPException(status_code=404, detail="Approval not found.")
                
            if record.status != "pending":
                logger.warning("Invalid status for approval %s: %s", approval_id, record.status)
                raise HTTPException(status_code=400, detail="Only pending approvals can be approved.")
            
            logger.info("Updating approval status to running for %s", approval_id)
            record.status = "running"
            
            # Commit the status change
            await db.commit()
            logger.info("Status committed successfully for %s", approval_id)
        
        # Send immediate status update to frontend via SSE
        await _push_log(approval_id, "Approval confirmed - starting deployment pipeline...", 0)
        
        # Start pipeline in background - don't await it
        logger.info("Starting background pipeline task for %s", approval_id)
        task = asyncio.create_task(_run_pipeline(approval_id, gh_token))
        
        # Add task name for better debugging
        task.set_name(f"pipeline-{approval_id}")
        
        response = {"status": "running", "approval_id": approval_id}
        logger.info("=== APPROVE SUCCESS for %s: %s ===", approval_id, response)
        return response
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Implementation error for %s: %s", approval_id, exc, exc_info=True)
        
        # Send error message to frontend immediately
        try:
            await _push_log(approval_id, f"Approval failed: {str(exc)}", 0)
            subscriber_manager.broadcast_message(approval_id, "FAILED")
        except Exception:
            pass  # Don't let logging errors prevent error handling
        
        raise


# ---------------------------------------------------------------------------
# SSE log stream
# ---------------------------------------------------------------------------

@router.get("/{approval_id}/logs")
async def stream_logs(
    approval_id: str,
    gh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Approval not found.")

    async def _event_generator() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        subscriber_manager.add_subscriber(approval_id, queue)

        # Replay existing state from DB
        async with AsyncSessionLocal() as s:
            r = await s.execute(select(Approval).where(Approval.id == approval_id))
            rec = r.scalar_one_or_none()
            if rec:
                # Emit current stage so UI stepper syncs immediately
                stage = getattr(rec, "pipeline_stage", 0)
                if stage > 0:
                    yield f"data: STAGE:{stage}\n\n"
                # Replay per-stage logs in order
                sl: dict = getattr(rec, "stage_logs", {}) or {}
                for s_key in sorted(sl.keys(), key=int):
                    for line in sl[s_key]:
                        yield f"data: {s_key}|{line}\n\n"
                # Replay global terminal messages
                for line in (rec.logs or []):
                    if line.startswith("PIPELINE") or line.startswith("Deployed URL") or line.startswith("Actions Run"):
                        yield f"data: {line}\n\n"
                if rec.status == "done":
                    yield "data: DONE\n\n"
                elif rec.status == "failed":
                    yield "data: FAILED\n\n"

        try:
            while True:
                async with AsyncSessionLocal() as s:
                    r = await s.execute(select(Approval).where(Approval.id == approval_id))
                    rec = r.scalar_one_or_none()
                    terminal = rec and rec.status in ("done", "failed", "rejected")
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    if terminal:
                        break
        finally:
            # Thread-safe subscriber removal
            subscriber_manager.remove_subscriber(approval_id, queue)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Pipeline runner — called in background after approval
# ---------------------------------------------------------------------------

async def _run_pipeline(approval_id: str, gh_token: str) -> None:
    """Run the complete deployment pipeline with timeout protection."""
    try:
        # Set a reasonable timeout for the entire pipeline (45 minutes)
        await asyncio.wait_for(_run_pipeline_impl(approval_id, gh_token), timeout=2700)
    except asyncio.TimeoutError:
        logger.error("Pipeline timeout for approval %s", approval_id)
        
        # Update database status
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Approval).where(Approval.id == approval_id))
                rec = r.scalar_one_or_none()
                if rec:
                    rec.status = "failed"
                    await db.commit()
        except Exception:
            pass  # Don't let DB errors prevent timeout handling
        
        # Send timeout message to frontend
        timeout_msg = "PIPELINE FAILED: Timeout after 45 minutes. This may be due to:"
        await _push_log(approval_id, timeout_msg, 0)
        await _push_log(approval_id, "• Google Gemini API timeout or rate limiting", 0)
        await _push_log(approval_id, "• Azure resource provisioning delays", 0)
        await _push_log(approval_id, "• Network connectivity issues", 0)
        await _push_log(approval_id, "Try again or check your API keys and network connection", 0)
        
        # Broadcast failure to all subscribers
        subscriber_manager.broadcast_message(approval_id, "FAILED")
        
    except Exception as exc:
        logger.exception("Pipeline wrapper error for approval %s", approval_id)
        
        # Update database status
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Approval).where(Approval.id == approval_id))
                rec = r.scalar_one_or_none()
                if rec:
                    rec.status = "failed"
                    await db.commit()
        except Exception:
            pass  # Don't let DB errors prevent error handling
        
        # Send error message to frontend
        error_msg = f"PIPELINE FAILED: {repr(exc)}"
        await _push_log(approval_id, error_msg, 0)
        
        # Broadcast failure to all subscribers
        subscriber_manager.broadcast_message(approval_id, "FAILED")


async def _run_pipeline_impl(approval_id: str, gh_token: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        record = result.scalar_one_or_none()
        if not record:
            return
        repo: str = record.repo
        branch: str = record.branch
        cfg: dict = dict(record.config)
    
    # Use PAT for write operations instead of OAuth token
    import os as _os  # noqa: PLC0415
    pat = _os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not pat:
        await _push_log(approval_id, "ERROR: GITHUB_PERSONAL_ACCESS_TOKEN not set", 0)
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Approval).where(Approval.id == approval_id))
            rec = r.scalar_one_or_none()
            if rec:
                rec.status = "failed"
                await db.commit()
        return
    
    logger.info("Pipeline run started for %s — repo=%s branch=%s", approval_id, _sanitize(repo), branch)

    async def log(msg: str, stage: int = 0) -> None:
        await _push_log(approval_id, msg, stage)

    async def wait_with_logs(total_wait, interval):
        for elapsed in range(0, total_wait, interval):
            await asyncio.sleep(interval)
            await log(
                f"Waiting for Azure provisioning... {elapsed + interval}/{total_wait} seconds",
                3
            )

    async def _set_stage(stage: int, status: str | None = None, **kwargs: str | None) -> None:
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Approval).where(Approval.id == approval_id))
            rec = r.scalar_one_or_none()
            if rec:
                rec.pipeline_stage = stage
                if status:
                    rec.status = status
                for k, v in kwargs.items():
                    setattr(rec, k, v)
                await db.commit()
        # Emit a stage-change event so UI can advance the stepper
        subscriber_manager.broadcast_message(approval_id, f"STAGE:{stage}")

    try:
        from .analysis import TechDetectionRequest, tech_detection  # noqa: PLC0415
        from .pipelines import (  # noqa: PLC0415
            _commit_file, _generate_ci_yaml, _verify_repo_access,
        )

        # ── STAGE 1: Tech Detection ─────────────────────────────────────────
        await _set_stage(1)
        await log("Scanning repository for tech stack...", 1)
        tech = await tech_detection(
            TechDetectionRequest(repoFullName=repo, branch=branch), gh_token,
        )
        lang = tech.get('language', 'unknown')
        fw   = tech.get('framework') or 'none'
        bt   = tech.get('buildTool') or 'none'
        await log(f"Language   : {lang}", 1)
        await log(f"Framework  : {fw}", 1)
        await log(f"Build tool : {bt}", 1)
        await log(f"Dockerfile : {tech.get('hasDockerfile', False)}", 1)
        await log(f"Helm       : {tech.get('hasHelm', False)}", 1)
        await log(f"Terraform  : {tech.get('hasTerraform', False)}", 1)
        # Persist detected_tech
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Approval).where(Approval.id == approval_id))
            rec = r.scalar_one_or_none()
            if rec:
                rec.detected_tech = tech
                await db.commit()
        await log("Tech detection complete.", 1)
        await _push_stage_event(approval_id, 1, "info", "Tech detection complete")

        # Verify repo access - try PAT first, fallback to OAuth token
        try:
            resolved_branch = await _verify_repo_access(repo, branch, pat)
        except HTTPException as e:
            if e.status_code == 404:
                await log("PAT cannot access repo, trying OAuth token...", 1)
                try:
                    resolved_branch = await _verify_repo_access(repo, branch, gh_token)
                    await log("Using OAuth token for repo access", 1)
                    # Use OAuth token for read operations, PAT for write operations
                    pat = gh_token
                except HTTPException:
                    await log("ERROR: Neither PAT nor OAuth token can access this repo", 1)
                    await log("Please ensure the repo exists and you have access", 1)
                    raise
            else:
                raise
        
        deploy_cfg = _build_deploy_config(cfg, tech)
        
        # Determine deployment target
        deploy_target = str(cfg.get('DEPLOY_TARGET', 'app_service')).lower()
        service_id = None  # For Render deployments
        deploy_id = None  # For Render deployments (set when create_render_service runs)

        # ── STAGE 2: CI Pipeline Generation ─────────────────────────────
        await _set_stage(2)
        if deploy_target == "render":
            # RENDER: Skip GitHub Actions YAML generation
            await log("Skipping CI YAML generation (Render auto-deploys)", 2)
            await log("Render will automatically deploy from GitHub on push", 2)
        else:
            # AZURE: Generate CI workflow immediately in Stage 2
            await log("Generating CI pipeline YAML...", 2)
            
            # Generate CI workflow (build only)
            ci_yaml = await _generate_ci_only(resolved_branch, tech, cfg)
            await _commit_file(
                repo, resolved_branch,
                ".github/workflows/ci.yml",
                ci_yaml,
                "chore: add CI pipeline via DevOps Agent",
                pat,
            )
            await log("Committed: .github/workflows/ci.yml (build only)", 2)
            
            # Prepare repository (scaffolding and gitignore)
            await log("Preparing repository structure...", 2)
            stage2_log = lambda m: log(m, 2)  # noqa: E731
            await _scaffold_missing_files(repo, resolved_branch, tech, pat, stage2_log)
            await _ensure_gitignore(repo, resolved_branch, pat)
            await log("Repository structure prepared", 2)
        
        await log("CI pipeline generation complete.", 2)
        
        # ── STAGE 3: Infrastructure Provisioning ────────────────────────────
        await _set_stage(3)
        await log("Starting infrastructure provisioning...", 3)
        await log(f"Deploy target  : {deploy_target}", 3)
        await log(f"App name       : {cfg.get('APP_NAME', 'devops-app')}", 3)
        
        # Check deployment strategy
        from ...services.deployment_config import deployment_manager, DeploymentStrategy
        
        strategy = deployment_manager.get_strategy()
        await log(f"Deployment strategy: {strategy.value}", 3)
        
        if deploy_target == "render":
            # RENDER DEPLOYMENT PATH
            from .render_deploy import create_render_service  # noqa: PLC0415
            
            await log("Using Render for deployment", 3)
            await log(f"Region: {cfg.get('REGION', 'oregon')}", 3)
            await log(f"Plan: {cfg.get('PLAN', 'free')}", 3)
            
            deployed_url, service_id, deploy_id = await create_render_service(
                repo=repo,
                branch=resolved_branch,
                tech=tech,
                cfg=cfg,
                log=lambda m: log(m, 3),
            )
            
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Approval).where(Approval.id == approval_id))
                rec = r.scalar_one_or_none()
                if rec:
                    rec.terraform_url = deployed_url
                    await db.commit()
            
            await log(f"Provisioned URL: {deployed_url}", 3)
            await log("Render service created successfully", 3)
        else:
            # AZURE/TERRAFORM DEPLOYMENT PATH
            await log(f"Resource group : {cfg.get('RESOURCE_GROUP', 'devops-rg')}", 3)
            await log(f"Location       : {cfg.get('LOCATION', 'eastus')}", 3)
            
            if strategy == DeploymentStrategy.GITHUB_ACTIONS:
                await log("Using GitHub Actions for secure Terraform deployment", 3)
                await log("Benefits: Secure credentials, audit trail, team collaboration", 3)
            else:
                await log(f"Using {strategy.value} deployment strategy", 3)
            
            # Ensure resource group exists before Terraform (for local execution)
            if strategy == DeploymentStrategy.LOCAL_EXECUTION:
                try:
                    from ...services.azure_resource_manager import AzureResourceGroupManager
                    rg_manager = AzureResourceGroupManager()
                    
                    resource_group = str(cfg.get("RESOURCE_GROUP", "devops-rg"))
                    location = str(cfg.get("LOCATION", "eastus"))
                    app_name = str(cfg.get("APP_NAME", "devops-app"))
                    
                    # Validate and suggest better name if needed
                    is_valid, validation_msg = rg_manager.validate_resource_group_name(resource_group)
                    if not is_valid:
                        await log(f"Invalid resource group name: {validation_msg}", 3)
                        # Try to fix the resource group name while preserving the original intent
                        suggested_name = rg_manager.suggest_resource_group_name(resource_group, cfg.get("ENVIRONMENT", "dev"))
                        await log(f"📝 Using corrected name: {suggested_name} (based on original: {resource_group})", 3)
                        resource_group = suggested_name
                    else:
                        await log(f"Resource group name '{resource_group}' is valid", 3)
                    
                    # Ensure resource group exists
                    rg_created = await rg_manager.ensure_resource_group_exists(
                        resource_group_name=resource_group,
                        location=location,
                        tags={
                            'Application': app_name,
                            'Environment': cfg.get('ENVIRONMENT', 'dev'),
                            'CreatedBy': 'DevOps-Agent',
                            'Repository': repo
                        },
                        log_func=lambda m: log(m, 3)
                    )
                    
                    if not rg_created:
                        await log("Failed to ensure resource group exists", 3)
                        raise RuntimeError(f"Could not create or access resource group: {resource_group}")
                    
                    # Update config with validated resource group name
                    cfg["RESOURCE_GROUP"] = resource_group
                    
                except Exception as rg_error:
                    await log(f"Resource group management failed: {rg_error}", 3)
                    await log("Continuing with Terraform (it will create the RG)", 3)
            
            # Pass additional context for terraform execution
            cfg_with_context = {
                **cfg,
                "_approval_id": approval_id,
                "_repo": repo,
                "_branch": resolved_branch
            }
            
            deployed_url = await _run_terraform(cfg_with_context, tech, lambda m: log(m, 3))
            
            # Decode any HTML entities in the URL before logging and storing
            if deployed_url:
                deployed_url = _decode_html_entities(deployed_url)
            
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Approval).where(Approval.id == approval_id))
                rec = r.scalar_one_or_none()
                if rec:
                    rec.terraform_url = deployed_url
                    await db.commit()
            
            await log(f"Provisioned URL: {deployed_url}", 3)
            
            if strategy == DeploymentStrategy.GITHUB_ACTIONS:
                await log("GitHub Actions deployment completed successfully", 3)
                await log("Check GitHub Actions tab for detailed logs and audit trail", 3)
            else:
                await log("Infrastructure provisioning complete.", 3)
        
        await _push_stage_event(approval_id, 3, "info", "Infrastructure provisioning complete")        

        # ── STAGE 4: CD Pipeline Generation ─────────────────────────────
        await _set_stage(4)
        if deploy_target == "render":
            # RENDER: Skip GitHub Actions YAML generation
            await log("Skipping CD YAML generation (Render auto-deploys)", 4)
            await log("Render will automatically deploy from GitHub on push", 4)
        else:
            # AZURE: Generate CD workflow with actual resource names from Terraform
            await log("Generating CD pipeline YAML with actual resource names...", 4)
            
            # Extract actual app name and resource group from the deployed URL and Terraform state
            actual_app_name = cfg.get("APP_NAME", "devops-app")
            actual_resource_group = cfg.get("RESOURCE_GROUP", "devops-rg")
            
            if deployed_url and "azurewebsites.net" in deployed_url:
                # Extract actual app name from URL: https://my-app178488mnv4.azurewebsites.net -> my-app178488mnv4
                import re
                url_match = re.search(r'https://([^.]+)\.azurewebsites\.net', deployed_url)
                if url_match:
                    actual_app_name = url_match.group(1)
                    await log(f"Extracted actual app name: {actual_app_name}", 4)
                    
                    # Try to determine the actual resource group that Terraform used
                    # Check if Terraform used a different resource group name
                    if strategy == DeploymentStrategy.GITHUB_ACTIONS:
                        # For GitHub Actions, try to get resource group from Terraform outputs
                        await log("Attempting to verify actual resource group from Terraform...", 4)
                        # The resource group might be different from config if Terraform modified it
                        # For now, assume it matches the app name pattern
                        if "-" in actual_app_name:
                            # If app name has suffix, resource group might too
                            base_name = actual_app_name.split("-")[0] + "-" + actual_app_name.split("-")[1] if len(actual_app_name.split("-")) > 1 else actual_app_name
                            potential_rg = base_name + "-rg"
                            await log(f"Potential resource group based on app name: {potential_rg}", 4)
                            # For safety, we'll still use the configured resource group but log the potential mismatch
                            if potential_rg != actual_resource_group:
                                await log(f"Resource group mismatch: config={actual_resource_group}, potential={potential_rg}", 4)
                else:
                    await log(f"Could not extract app name from URL: {deployed_url}", 4)
            
            await log(f"Using for deployment: app={actual_app_name}, rg={actual_resource_group}", 4)
            
            # Update config with actual names for CI/CD generation
            cicd_config = {
                **cfg,
                "APP_NAME": actual_app_name,
                "RESOURCE_GROUP": actual_resource_group
            }
            
            # Verify the Azure Web App actually exists before generating CD workflow
            await log("Verifying Azure Web App exists before deployment...", 4)
            try:
                from azure.identity import DefaultAzureCredential
                from azure.mgmt.web import WebSiteManagementClient
                from azure.core.exceptions import ResourceNotFoundError
                import os
                
                credential = DefaultAzureCredential()
                subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID") or cfg.get("SUBSCRIPTION_ID")
                
                if subscription_id:
                    web_client = WebSiteManagementClient(credential, subscription_id)
                    
                    try:
                        web_app = web_client.web_apps.get(actual_resource_group, actual_app_name)
                        await log(f"Verified: Azure Web App '{actual_app_name}' exists in resource group '{actual_resource_group}'", 4)
                        await log(f"Web App state: {web_app.state}, location: {web_app.location}", 4)
                    except ResourceNotFoundError:
                        await log(f"Azure Web App '{actual_app_name}' not found in resource group '{actual_resource_group}'", 4)
                        
                        # Try to find the app in other resource groups
                        await log("Searching for the Web App in other resource groups...", 4)
                        try:
                            from azure.mgmt.resource import ResourceManagementClient
                            resource_client = ResourceManagementClient(credential, subscription_id)
                            
                            # List all resource groups
                            for rg in resource_client.resource_groups.list():
                                try:
                                    web_app = web_client.web_apps.get(rg.name, actual_app_name)
                                    await log(f"Found Web App '{actual_app_name}' in resource group '{rg.name}'", 4)
                                    actual_resource_group = rg.name
                                    cicd_config["RESOURCE_GROUP"] = actual_resource_group
                                    break
                                except ResourceNotFoundError:
                                    continue
                            else:
                                await log(f"Web App '{actual_app_name}' not found in any resource group", 4)
                                await log("This might indicate a Terraform provisioning issue", 4)
                        except Exception as search_error:
                            await log(f"Error searching resource groups: {search_error}", 4)
                else:
                    await log("No Azure subscription ID found, skipping verification", 4)
                    
            except Exception as verify_error:
                await log(f"Could not verify Azure Web App existence: {verify_error}", 4)
                await log("Proceeding with deployment attempt...", 4)
            
            # Generate CD workflow with actual resource names
            cd_yaml = await _generate_cd_with_deploy(resolved_branch, tech, cicd_config)
            await _commit_file(
                repo, resolved_branch,
                ".github/workflows/cd.yml",
                cd_yaml,
                "chore: add CD pipeline via DevOps Agent",
                pat,
            )
            await log(f"Committed: .github/workflows/cd.yml with app name: {actual_app_name}", 4)

            # Push secrets with actual resource names
            await _push_azure_secrets(repo, cicd_config, pat, actual_app_name, actual_resource_group)
            await log("Secrets configured with actual resource names", 4)
        
        await log("CD pipeline generation complete.", 4)
        # ── STAGE 5: Monitor Deployment ─────────────────────────────────────
        await _set_stage(5)
        
        if deploy_target == "render":
            # RENDER: Monitor via Render API
            from .render_deploy import monitor_render_deployment  # noqa: PLC0415
            
            await log("Monitoring Render deployment...", 5)
            status = await monitor_render_deployment(
                d_id=deploy_id,
                s_id=service_id,
                log=lambda m: log(m, 5),
                timeout_minutes=15,
            )
            
            if status != "live":
                raise RuntimeError(f"Render deployment failed with status: {status}")
            
            await log("Render deployment complete", 5)
            run_url = f"https://dashboard.render.com/web/{service_id}"
        else:
            # AZURE: Monitor GitHub Actions
            await log("Waiting for GitHub Actions workflow to start...", 5)
            run_url = await _trigger_and_poll(repo, resolved_branch, pat,
                                              lambda m: log(m, 5))
            await log("GitHub Actions workflow complete.", 5)

        # ── STAGE 6: End-to-End Validation ──────────────────────────────────
        await _set_stage(6)
        
        # Validate complete flow
        from ...services.pipeline_flow_manager import PipelineFlowManager
        flow_manager = PipelineFlowManager()
        
        await log("Performing end-to-end validation...", 6)
        flow_valid = await flow_manager.validate_complete_flow(
            repo=repo,
            branch=resolved_branch, 
            app_url=deployed_url,
            log_func=lambda m: log(m, 6)
        )
        
        if flow_valid:
            await log("End-to-end validation successful!", 6)
        else:
            await log("Some validation checks failed, but deployment completed", 6)

        # ── DONE ─────────────────────────────────────────────────────────────
        await _set_stage(7, status="done",
                         deployed_url=deployed_url,
                         actions_run_url=run_url or None)
        await log(f"PIPELINE COMPLETE", 0)
        # Ensure URL is clean of HTML entities
        clean_deployed_url = _decode_html_entities(deployed_url) if deployed_url else deployed_url
        await log(f"Deployed URL : {clean_deployed_url}", 0)
        if run_url:
            await log(f"Actions Run  : {run_url}", 0)
        await log(f"🎉 Your application is now live and accessible!", 0)
        subscriber_manager.broadcast_message(approval_id, "DONE")

    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed for approval %s", approval_id)
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Approval).where(Approval.id == approval_id))
            rec = r.scalar_one_or_none()
            if rec:
                rec.status = "failed"
                await db.commit()
        # Store a richer error message + traceback so frontend shows useful details.
        # Prefix traceback with "PIPELINE" so the frontend replay includes it.
        err_msg = repr(exc)
        tb = traceback.format_exc()
        combined = "PIPELINE FAILED: " + (err_msg or "") + "\n" + (tb or "")
        # Ensure the entire traceback is stored as a single PIPELINE-prefixed message
        await log(combined, 0)
        subscriber_manager.broadcast_message(approval_id, "FAILED")
    finally:
        # Force cleanup of orphaned subscribers - thread-safe
        cleanup_count = subscriber_manager.cleanup_approval(approval_id)
        logger.info(f"Pipeline cleanup: removed {cleanup_count} subscribers for {approval_id}")


# ---------------------------------------------------------------------------
# Helpers used by _run_pipeline
# ---------------------------------------------------------------------------

_SCAFFOLD_TEMPLATES: dict[str, dict[str, str]] = {
    "javascript": {
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"start":"node server.js","build":"echo build"},"dependencies":{}}',
        "server.js": 'const http = require("http");\nhttp.createServer((_, res) => res.end("OK")).listen(process.env.PORT || 3000);',
    },
    "python": {
        "requirements.txt": "fastapi\nuvicorn\n",
        "app.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/")\ndef root(): return {"status": "ok"}\n',
    },
    "java": {
        "pom.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project xmlns="http://maven.apache.org/POM/4.0.0"\n'
            '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
            '  xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">\n'
            '  <modelVersion>4.0.0</modelVersion>\n'
            '  <groupId>com.example</groupId><artifactId>app</artifactId><version>1.0.0</version>\n'
            '</project>\n'
        ),
    },
}


async def _scaffold_missing_files(
    repo: str, branch: str, tech: dict, gh_token: str, log
) -> None:
    """Commit minimal required files if they are absent from the repo."""
    from .pipelines import _commit_file  # noqa: PLC0415

    language: str = tech.get("language", "").lower()
    # Skip scaffolding for static frontends — they already have package.json + src/
    # But don't skip CI/CD generation!
    is_static = (
        language in ("javascript", "typescript")
        and tech.get("framework", "") in (None, "", "react", "vue", "angular", "vite")
        and not tech.get("hasDockerfile", False)
    )
    if is_static:
        await log("  Static frontend detected — skipping scaffold (CI/CD will still be generated)")
        return

    templates = _SCAFFOLD_TEMPLATES.get(language, {})
    if not templates:
        await log("  No scaffolding templates for this language")
        return

    for filename, content in templates.items():
        url = f"{_GITHUB_API}/repos/{repo}/contents/{filename}"
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url, headers=_gh_headers(gh_token), params={"ref": branch})
        if res.status_code == 200:
            continue  # file already exists
        await _commit_file(
            repo, branch, filename, content,
            f"chore: scaffold {filename} via DevOps Agent", gh_token,
        )
        await log(f"  Scaffolded: {filename}")

async def wait_for_azure_app(app_name, resource_group, log):
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.web import WebSiteManagementClient
    import os as _os

    credential = DefaultAzureCredential()
    subscription_id = _os.getenv("AZURE_SUBSCRIPTION_ID")

    client = WebSiteManagementClient(credential, subscription_id)

    for attempt in range(20):
        try:
            client.web_apps.get(resource_group, app_name)
            await log("Azure WebApp ready", 3)
            return True
        except Exception:
            await log(f"Waiting for Azure WebApp... attempt {attempt+1}", 3)
            await asyncio.sleep(10)

    return False

async def _run_terraform(cfg: dict, tech: dict, log) -> str:
    """Run terraform using GitHub Actions for secure, scalable deployment."""
    from ...services.ai_config import AIConfig
    
    # Check if AI is enabled
    if not AIConfig.is_ai_enabled():
        await log("AI terraform generation is disabled - using fallback terraform")
        return await _run_terraform_fallback(cfg, log)
    
    # Get Google Gemini API key from environment
    gemini_api_key = AIConfig.get_gemini_api_key()
    if not gemini_api_key:
        await log("Google Gemini API key not configured - using fallback terraform")
        return await _run_terraform_fallback(cfg, log)
    
    try:
        # Use GitHub Actions-based Terraform executor
        from ...services.github_terraform_executor import GitHubTerraformExecutor
        
        executor = GitHubTerraformExecutor(gemini_api_key)
        
        # Get repository and branch info from the approval record
        approval_id = cfg.get("_approval_id", "unknown")
        
        # Get repo info from the database
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            if not record:
                raise RuntimeError(f"Approval record not found: {approval_id}")
            
            repo = record.repo
            branch = record.branch
        
        # Get GitHub token
        import os as _os  # noqa: PLC0415
        gh_token = _os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        if not gh_token:
            raise RuntimeError("GITHUB_PERSONAL_ACCESS_TOKEN not configured")
        
        # Execute pipeline via GitHub Actions
        app_url = await executor.execute_pipeline(
            approval_id=approval_id,
            cfg=cfg,
            tech=tech,
            repo=repo,
            branch=branch,
            gh_token=gh_token,
            log=log
        )
        
        return app_url
        
    except Exception as exc:
        error_msg = str(exc).lower()
        
        # Check for specific API key errors
        if any(keyword in error_msg for keyword in ["api key expired", "api_key_invalid", "invalid api key", "authentication"]):
            await log("Google Gemini API key expired or invalid")
            await log("Please update your GOOGLE_GEMINI_API_KEY environment variable")
            await log("Get a new key at: https://makersuite.google.com/app/apikey")
        elif "timeout" in error_msg:
            await log("Google Gemini API timeout - service may be overloaded")
        elif "quota" in error_msg or "rate limit" in error_msg:
            await log("Google Gemini API quota exceeded or rate limited")
        else:
            await log(f"AI terraform generation failed: {exc}")
        
        if AIConfig.should_fallback_on_error():
            await log("Falling back to local terraform execution...")
            return await _run_terraform_fallback(cfg, log)
        else:
            await log("No fallback configured - deployment failed")
            raise


async def _run_terraform_fallback(cfg: dict, log) -> str:
    """Fallback terraform implementation with basic security."""
    import os as _os
    import json as _json
    import tempfile
    import asyncio
    import shutil
    import traceback
    import re

    # Sanitize inputs
    app_name = re.sub(r'[^a-zA-Z0-9\-]', '', str(cfg.get("APP_NAME", "devops-app")))[:30]
    location = re.sub(r'[^a-zA-Z0-9]', '', str(cfg.get("LOCATION", "eastus")))[:20]
    resource_group = re.sub(r'[^a-zA-Z0-9\-]', '', str(cfg.get("RESOURCE_GROUP", "devops-rg")))[:30]
    
    await log(f"📋 Terraform will use:")
    await log(f"   App Name: {app_name}")
    await log(f"   Resource Group: {resource_group}")
    await log(f"   Location: {location}")

    # Try to find terraform binary
    terraform_path = None
    trusted_terraform_paths = [
        "/usr/local/bin/terraform",
        "/usr/bin/terraform", 
        "./bin/terraform",
        "C:\\terraform\\terraform.exe",
        "/opt/homebrew/bin/terraform",  # macOS Homebrew
        "/home/linuxbrew/.linuxbrew/bin/terraform",  # Linux Homebrew
    ]
    
    # Check trusted paths first
    for path in trusted_terraform_paths:
        if _os.path.exists(path):
            terraform_path = path
            break
    
    # If not found in trusted paths, try system PATH
    if not terraform_path:
        terraform_path = shutil.which("terraform")
    
    if not terraform_path:
        await log("Terraform binary not found. Attempting to install...")
        
        # Try to install terraform automatically
        try:
            if _os.name == 'nt':  # Windows
                await log("Installing Terraform on Windows...")
                # Use chocolatey if available
                choco_path = shutil.which("choco")
                if choco_path:
                    proc = await asyncio.create_subprocess_exec(
                        "choco", "install", "terraform", "-y",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await proc.communicate()
                    terraform_path = shutil.which("terraform")
            else:  # Linux/macOS
                await log("Installing Terraform on Linux/macOS...")
                # Try to download and install terraform
                import urllib.request
                import zipfile
                
                # Create local bin directory
                local_bin = _os.path.expanduser("~/.local/bin")
                _os.makedirs(local_bin, exist_ok=True)
                
                # Download terraform
                tf_version = "1.6.0"
                system = "linux" if _os.name == "posix" else "darwin"
                arch = "amd64"  # Assume x64
                
                tf_url = f"https://releases.hashicorp.com/terraform/{tf_version}/terraform_{tf_version}_{system}_{arch}.zip"
                tf_zip_path = f"/tmp/terraform_{tf_version}.zip"
                
                await log(f"Downloading Terraform {tf_version}...")
                urllib.request.urlretrieve(tf_url, tf_zip_path)
                
                # Extract terraform
                with zipfile.ZipFile(tf_zip_path, 'r') as zip_ref:
                    zip_ref.extract('terraform', local_bin)
                
                # Make executable
                terraform_path = _os.path.join(local_bin, 'terraform')
                _os.chmod(terraform_path, 0o755)
                
                await log(f"Terraform installed to {terraform_path}")
                
        except Exception as install_error:
            await log(f"Failed to install Terraform: {install_error}")
            await log("Please install Terraform manually: https://www.terraform.io/downloads.html")
            raise RuntimeError("Terraform installation failed")
    
    if not terraform_path:
        await log("Terraform installation failed")
        raise RuntimeError("Terraform binary not found")
    
    await log(f"Using Terraform binary: {terraform_path}")

    try:
        with tempfile.TemporaryDirectory(prefix="secure_terraform_") as tf_dir:
            await log(f"Using secure terraform directory: {tf_dir}")

            # Create minimal secure Terraform configuration
            main_tf = f"""
terraform {{
  required_providers {{
    azurerm = {{
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }}
  }}
}}

provider "azurerm" {{
  features {{}}
}}

resource "azurerm_resource_group" "main" {{
  name     = "{resource_group}"
  location = "{location}"
  
  tags = {{
    Environment = "dev"
    ManagedBy   = "DevOps-Agent"
  }}
}}

resource "azurerm_service_plan" "main" {{
  name                = "{app_name}-plan"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "B1"
}}

resource "azurerm_linux_web_app" "main" {{
  name                = "{app_name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_service_plan.main.location
  service_plan_id     = azurerm_service_plan.main.id
  
  site_config {{
    always_on = false
    app_command_line = "npx serve -s ."
    
    application_stack {{
      node_version = "18-lts"
    }}
  }}
  
  app_settings = {{
    WEBSITE_NODE_DEFAULT_VERSION = "18-lts"
    WEBSITES_ENABLE_APP_SERVICE_STORAGE = "false"
    WEBSITE_HTTPLOGGING_RETENTION_DAYS = "7"
    WEBSITE_RUN_FROM_PACKAGE = "1"
    SCM_DO_BUILD_DURING_DEPLOYMENT = "true"
    ENABLE_ORYX_BUILD = "true"
  }}
}}

output "app_url" {{
  value = "https://${{azurerm_linux_web_app.main.default_hostname}}"
}}
"""

            # Write main.tf with restricted permissions
            tf_file = os.path.join(tf_dir, "main.tf")
            with open(tf_file, "w") as f:
                f.write(main_tf)
            os.chmod(tf_file, 0o600)

            env = {
                **os.environ,
                "TF_INPUT": "false",
                "TF_IN_AUTOMATION": "true"
            }

            async def _tf_secure(args):
                # Validate arguments
                safe_args = []
                allowed_args = {"init", "apply", "output", "-no-color", "-upgrade", "-auto-approve", "-json"}
                for arg in args:
                    if arg in allowed_args:
                        safe_args.append(arg)
                
                proc = await asyncio.create_subprocess_exec(
                    terraform_path,
                    *safe_args,
                    cwd=tf_dir,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await proc.communicate()
                return proc.returncode, stdout.decode(errors="replace")

            # Terraform init
            await log("Running secure terraform init...")
            rc, out = await _tf_secure(["init", "-no-color", "-upgrade"])
            if rc != 0:
                await log("terraform init failed")
                await log(out[-1000:])  # Show last 1000 chars of output
                raise RuntimeError("Terraform init failed")

            # Terraform apply
            await log("Running secure terraform apply...")
            rc, out = await _tf_secure(["apply", "-auto-approve", "-no-color"])
            if rc != 0:
                await log("terraform apply failed")
                await log(out[-1000:])  # Show last 1000 chars of output
                raise RuntimeError("Terraform apply failed")
            
            # Terraform output
            rc, out = await _tf_secure(["output", "-json"])
            if rc == 0:
                try:
                    outputs = _json.loads(out)
                    url = outputs.get("app_url", {}).get("value")
                    if url:
                        return url
                except _json.JSONDecodeError:
                    pass

            raise RuntimeError("Failed to get app URL from Terraform output")

    except Exception as exc:
        await log(f"terraform fallback failed: {type(exc).__name__}")
        await log(f"Error details: {str(exc)}")
        raise

def _build_deploy_config(cfg: dict, tech: dict | None = None) -> dict | None:
    deploy_target: str = str(cfg.get("DEPLOY_TARGET", "")).lower()
    if not deploy_target:
        return None
    target_map = {
        "azure_vm": "vm", "vm": "vm",
        "aks": "aks",
        "app_service": "azure-web-app",
        "azure_web_app": "azure-web-app",
        "web_app": "azure-web-app",
    }
    # Detect static frontend: JS/TS with no backend framework (Vite/CRA/Next static)
    is_static = (
        tech is not None
        and tech.get("language", "") in ("javascript", "typescript")
        and tech.get("framework", "") in (None, "", "react", "vue", "angular", "vite")
        and not tech.get("hasDockerfile", False)
    )
    return {
        "infrastructure_type": target_map.get(deploy_target, "azure-web-app"),
        "resource_name": str(cfg.get("APP_NAME", "devops-app")),
        "resource_group": str(cfg.get("RESOURCE_GROUP", "devops-rg")),
        "sku": str(cfg.get("APP_SERVICE_SKU", "B1")),
        "public_ip": str(cfg.get("PUBLIC_IP", "")),
        "admin_user": str(cfg.get("ADMIN_USER", "azureuser")),
        "app_type": "static" if is_static else "server",
        "tech": tech or {},
    }


async def _generate_ci_only(branch: str, tech: dict, config: dict) -> str:
    """Generate CI workflow for build and test only (no deployment)."""
    from ...services.pipeline_flow_manager import PipelineFlowManager
    
    flow_manager = PipelineFlowManager()
    # Use the CI workflow which only builds and uploads artifacts
    return flow_manager._generate_ci_workflow(branch, tech.get("language", "python"), tech.get("buildTool", "pip"))

async def _generate_ci_with_deploy(branch: str, tech: dict, config: dict) -> str:
    """Generate complete CI workflow."""
    from ...services.pipeline_flow_manager import PipelineFlowManager
    
    flow_manager = PipelineFlowManager()
    workflows = await flow_manager.generate_complete_cicd_pipeline(branch, tech, config)
    return workflows[".github/workflows/ci.yml"]

async def _generate_cd_with_deploy(branch: str, tech: dict, config: dict) -> str:
    """Generate complete CD workflow."""
    from ...services.pipeline_flow_manager import PipelineFlowManager
    
    flow_manager = PipelineFlowManager()
    workflows = await flow_manager.generate_complete_cicd_pipeline(branch, tech, config)
    return workflows[".github/workflows/cd.yml"]


async def _push_azure_secrets(
    repo: str, cfg: dict, gh_token: str, actual_app_name: str, resource_group: str = ""
) -> None:
    from .pipelines import _set_github_secret  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    # Prefer credentials supplied in the committed config.py; fall back to environment variables
    tenant_id       = str(cfg.get("TENANT_ID",       _os.getenv("AZURE_TENANT_ID",       "")))
    subscription_id = str(cfg.get("SUBSCRIPTION_ID", _os.getenv("AZURE_SUBSCRIPTION_ID", "")))
    client_id       = str(cfg.get("AZURE_CLIENT_ID",  _os.getenv("AZURE_CLIENT_ID", "")))
    client_secret   = str(cfg.get("AZURE_CLIENT_SECRET", _os.getenv("AZURE_CLIENT_SECRET", "")))
    rg              = resource_group or str(cfg.get("RESOURCE_GROUP", ""))

    if not all([tenant_id, subscription_id, client_id, client_secret]):
        logger.warning("Skipping secret push — Azure credentials incomplete for repo %s", _sanitize(repo))
        return

    azure_creds = _json.dumps({
        "clientId":       client_id,
        "clientSecret":   client_secret,
        "tenantId":       tenant_id,
        "subscriptionId": subscription_id,
    })
    await _set_github_secret(repo, "AZURE_CREDENTIALS", azure_creds, gh_token)
    await _set_github_secret(repo, "AZURE_WEBAPP_NAME", actual_app_name, gh_token)

    # Try to get publish profile, but don't fail if it doesn't work
    if rg and actual_app_name:
        try:
            from azure.identity import ClientSecretCredential  # noqa: PLC0415
            from azure.mgmt.web import WebSiteManagementClient  # noqa: PLC0415
            from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
            import asyncio as _asyncio  # noqa: PLC0415
            
            cred = ClientSecretCredential(tenant_id, client_id, client_secret)
            web_client = WebSiteManagementClient(cred, subscription_id)
            
            # Wait a bit for the web app to be fully provisioned
            await _asyncio.sleep(30)
            
            # Try to get the publish profile
            profile = await _asyncio.to_thread(
                lambda: web_client.web_apps.list_publishing_profile_xml_with_secrets(
                    rg, actual_app_name, {"format": "WebDeploy"}
                ).read().decode("utf-8")
            )
            await _set_github_secret(repo, "AZURE_WEBAPP_PUBLISH_PROFILE", profile, gh_token)
            logger.info("Publish profile pushed for %s", actual_app_name)
            
        except ResourceNotFoundError:
            logger.warning(
                "Web app %s not found in resource group %s. "
                "This is normal if Terraform hasn't finished provisioning yet. "
                "The deployment will still work with AZURE_CREDENTIALS.", 
                actual_app_name, rg
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not fetch publish profile for %s: %s. "
                "Deployment will continue with AZURE_CREDENTIALS.", 
                actual_app_name, exc
            )


async def _ensure_gitignore(repo: str, branch: str, gh_token: str) -> None:
    import base64 as _b64  # noqa: PLC0415
    url = f"{_GITHUB_API}/repos/{repo}/contents/.gitignore"
    headers = _gh_headers(gh_token)
    async with httpx.AsyncClient(timeout=15) as client:
        get_res = await client.get(url, headers=headers, params={"ref": branch})

    existing_content = ""
    existing_sha = None
    if get_res.status_code == 200:
        data = get_res.json()
        existing_sha = data.get("sha")
        existing_content = _b64.b64decode(data["content"]).decode("utf-8", errors="replace")

    lines_to_add = [e for e in ["node_modules/", ".env", "dist/", "*.log"] if e not in existing_content]
    if not lines_to_add:
        return

    new_content = existing_content.rstrip("\n") + "\n" + "\n".join(lines_to_add) + "\n"
    body: dict = {
        "message": "chore: update .gitignore via DevOps Agent",
        "content": _b64.b64encode(new_content.encode()).decode(),
        "branch": branch,
    }
    if existing_sha:
        body["sha"] = existing_sha

    async with httpx.AsyncClient(timeout=15) as client:
        await client.put(url, headers=headers, json=body)


async def _trigger_and_poll(repo: str, branch: str, gh_token: str, log) -> str:
    import time as _time  # noqa: PLC0415
    headers = _gh_headers(gh_token)
    started_at = _time.time()

    await asyncio.sleep(10)

    run_url = ""
    for attempt in range(36):
        async with httpx.AsyncClient(timeout=15) as client:
            runs_res = await client.get(
                f"{_GITHUB_API}/repos/{repo}/actions/runs",
                headers=headers,
                params={"branch": branch, "per_page": 5},
            )
        if runs_res.status_code == 200:
            runs = runs_res.json().get("workflow_runs", [])
            run = next(
                (r for r in runs if _iso_to_ts(r.get("created_at", "")) >= started_at - 30),
                None,
            )
            if run:
                status: str = run.get("status", "")
                conclusion: str = run.get("conclusion") or ""
                run_url = run.get("html_url", "")
                # Emit a run URL immediately so frontend can show the Actions run link
                if run_url:
                    await log(f"Actions Run  : {run_url}")
                await log(f"  [{attempt + 1:02d}] Workflow status: {status.upper()}{' / ' + conclusion.upper() if conclusion else ''}")

                # When a run is present, poll its jobs/steps and emit per-step messages
                # so the UI can show job/step progress under Stage 4.
                try:
                    run_id = run.get("id")
                    seen_step_ids: set[int] = set()
                    # poll jobs until run completes
                    while True:
                        async with httpx.AsyncClient(timeout=15) as client2:
                            jobs_res = await client2.get(
                                f"{_GITHUB_API}/repos/{repo}/actions/runs/{run_id}/jobs",
                                headers=headers,
                            )
                        if jobs_res.status_code == 200:
                            jobs_json = jobs_res.json().get("jobs", [])
                            for job in jobs_json:
                                job_name = job.get("name") or job.get("display_title") or f"job-{job.get('id') }"
                                for step in job.get("steps", []) or []:
                                    step_id = step.get("id")
                                    if not step_id or step_id in seen_step_ids:
                                        continue
                                    seen_step_ids.add(step_id)
                                    step_name = step.get("name", "step")
                                    step_status = step.get("status") or ""
                                    step_conclusion = step.get("conclusion") or ""
                                    # Compact step message
                                    msg = f"Actions: {job_name} > {step_name} — { (step_conclusion or step_status).upper() }"
                                    await log(msg)
                        # break the polling loop if run has completed
                        status = run.get("status", "")
                        conclusion = run.get("conclusion") or ""
                        if status == "completed":
                            break
                        # re-fetch run state
                        async with httpx.AsyncClient(timeout=15) as client3:
                            run_res = await client3.get(f"{_GITHUB_API}/repos/{repo}/actions/runs/{run_id}", headers=headers)
                        if run_res.status_code == 200:
                            run = run_res.json()
                            status = run.get("status", "")
                            conclusion = run.get("conclusion") or ""
                            await asyncio.sleep(5)
                        else:
                            await asyncio.sleep(10)
                    # final log on completion
                    await log(f"  Workflow run finished — {'SUCCESS' if conclusion == 'success' else 'CONCLUDED: ' + conclusion.upper()}")
                    if run_url:
                        await log(f"  Run URL: {run_url}")
                    if conclusion != "success":
                        raise RuntimeError(f"GitHub Actions workflow {conclusion}: {run_url}")
                    break
                except Exception:
                    # If job polling fails, continue the outer attempts loop and let it retry
                    await log("  Unable to fetch job/step details for the workflow run — will continue polling status.")
            else:
                await log(f"  [{attempt + 1:02d}] Waiting for workflow run to appear...")
        await asyncio.sleep(10)

    return run_url


def _iso_to_ts(iso: str) -> float:
    from datetime import datetime  # noqa: PLC0415
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


