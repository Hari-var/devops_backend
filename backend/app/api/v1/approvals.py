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

# SSE subscribers: approval_id → list[asyncio.Queue]
_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}

# Poller control flag
_POLLER_ENABLED: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh_headers(token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {token}"}


def _sanitize(value: str, max_len: int = 100) -> str:
    return value.replace("\n", "").replace("\r", "")[:max_len]


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
        if stage > 0:
            sl = dict(record.stage_logs or {})
            key = str(stage)
            sl[key] = sl.get(key, []) + [message]
            record.stage_logs = sl
        await db.commit()

    # Fan-out: prefix with stage so frontend can route to correct panel
    event_data = f"{stage}|{message}" if stage > 0 else message
    for queue in _SUBSCRIBERS.get(approval_id, []):
        queue.put_nowait(event_data)


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
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
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
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
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
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Approval not found.")
    if record.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending approvals can be approved.")
    logger.info("Approve called for %s (prev_status=%s) — scheduling pipeline", approval_id, record.status)
    record.status = "running"
    await db.commit()
    asyncio.create_task(_run_pipeline(approval_id, gh_token))
    return {"status": "running", "approval_id": approval_id}


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
        _SUBSCRIBERS.setdefault(approval_id, []).append(queue)

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
            subs = _SUBSCRIBERS.get(approval_id, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Pipeline runner — called in background after approval
# ---------------------------------------------------------------------------

async def _run_pipeline(approval_id: str, gh_token: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        record = result.scalar_one_or_none()
        if not record:
            return
        repo: str = record.repo
        branch: str = record.branch
        cfg: dict = dict(record.config)
    
    # Use PAT for write operations instead of OAuth token
    pat = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
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
        for queue in _SUBSCRIBERS.get(approval_id, []):
            queue.put_nowait(f"STAGE:{stage}")

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
            # AZURE: Generate GitHub Actions YAML
            await log("Generating CI pipeline YAML...", 2)
            stage3_log = lambda m: log(m, 2)  # noqa: E731
            await _scaffold_missing_files(repo, resolved_branch, tech, pat, stage3_log)
            await _ensure_gitignore(repo, resolved_branch, pat)

            # Generate CI/CD YAML with both build and deploy stages
            ci_yaml = await _generate_ci_with_deploy(resolved_branch, tech, cfg)
            await _commit_file(
                repo, resolved_branch,
                ".github/workflows/ci.yml",
                ci_yaml,
                "chore: add CI pipeline via DevOps Agent",
                pat,
            )
            await log("Committed: .github/workflows/ci.yml with deploy stage", 2)

            app_name = str(cfg.get("APP_NAME", "devops-app"))
            resource_group = str(cfg.get("RESOURCE_GROUP", "devops-rg"))
            await _push_azure_secrets(repo, cfg, pat, app_name, resource_group)
            await log("Secrets pushed: AZURE_CREDENTIALS, AZURE_WEBAPP_NAME", 2)
        
        await log("CI/CD configuration complete.", 2)
        
        # ── STAGE 3: Infrastructure Provisioning ────────────────────────────
        await _set_stage(3)
        await log("Starting infrastructure provisioning...", 3)
        await log(f"Deploy target  : {deploy_target}", 3)
        await log(f"App name       : {cfg.get('APP_NAME', 'devops-app')}", 3)
        
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
            deployed_url = await _run_terraform(cfg, lambda m: log(m, 3))
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Approval).where(Approval.id == approval_id))
                rec = r.scalar_one_or_none()
                if rec:
                    rec.terraform_url = deployed_url
                    await db.commit()
            await log(f"Provisioned URL: {deployed_url}", 3)
            await log("Infrastructure provisioning complete.", 3)
        
        await _push_stage_event(approval_id, 3, "info", "Infrastructure provisioning complete")        

        # ── STAGE 2: CD Pipeline Generation ─────────────────────────────
        await _set_stage(4)
        if deploy_target == "render":
            # RENDER: Skip GitHub Actions YAML generation
            await log("Skipping CD YAML generation (Render auto-deploys)", 4)
            await log("Render will automatically deploy from GitHub on push", 4)
        else:
            # AZURE: Generate GitHub Actions YAML
            await log("Generating CD pipeline YAML...", 4)
            stage3_log = lambda m: log(m, 4)  # noqa: E731
            await _scaffold_missing_files(repo, resolved_branch, tech, pat, stage3_log)
            await _ensure_gitignore(repo, resolved_branch, pat)

            # Generate CI/CD YAML with both build and deploy stages
            cd_yaml = await _generate_cd_with_deploy(resolved_branch, tech, cfg)
            await _commit_file(
                repo, resolved_branch,
                ".github/workflows/cd.yml",
                cd_yaml,
                "chore: add CD pipeline via DevOps Agent",
                pat,
            )
            await log("Committed: .github/workflows/cd.yml with deploy stage", 4)

            app_name = str(cfg.get("APP_NAME", "devops-app"))
            resource_group = str(cfg.get("RESOURCE_GROUP", "devops-rg"))
            await _push_azure_secrets(repo, cfg, pat, app_name, resource_group)
            await log("Secrets pushed: AZURE_CREDENTIALS, AZURE_WEBAPP_NAME", 4)
        
        await log("CD configuration complete.", 4)
        # ── STAGE 4: Monitor Deployment ─────────────────────────────────────
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

        # ── DONE ─────────────────────────────────────────────────────────────
        await _set_stage(6, status="done",
                         deployed_url=deployed_url,
                         actions_run_url=run_url or None)
        await log(f"PIPELINE COMPLETE", 0)
        await log(f"Deployed URL : {deployed_url}", 0)
        if run_url:
            await log(f"Actions Run  : {run_url}", 0)
        for queue in _SUBSCRIBERS.get(approval_id, []):
            queue.put_nowait("DONE")

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
        for queue in _SUBSCRIBERS.get(approval_id, []):
            queue.put_nowait("FAILED")


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
    is_static = (
        language in ("javascript", "typescript")
        and tech.get("framework", "") in (None, "", "react", "vue", "angular", "vite")
        and not tech.get("hasDockerfile", False)
    )
    if is_static:
        await log("  Static frontend detected — skipping scaffold")
        return

    templates = _SCAFFOLD_TEMPLATES.get(language, {})
    if not templates:
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


async def _run_terraform(cfg: dict, log) -> str:
    """
    Run terraform init + apply in the matching module directory.
    Returns the real app_url from `terraform output -json`, or falls back to
    the guessed Azure Web App URL if Terraform is not available / fails.
    """
    import json as _json  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    app_name: str = str(cfg.get("APP_NAME", "devops-app"))
    fallback_url = f"https://{app_name}.azurewebsites.net"

    try:
        if not shutil.which("terraform"):
            await log("  terraform binary not found — skipping IaC provisioning")
            return fallback_url

        deploy_target: str = str(cfg.get("DEPLOY_TARGET", "app_service")).lower()
        module_map = {"aks": "aks", "vm": "vm", "azure_vm": "vm"}
        module_dir_name = module_map.get(deploy_target, "app-service")

        base = os.path.join(
            os.path.dirname(__file__),  # backend/app/api/v1/
            "..", "..", "..", "..",    # → project root
            "templates", "terraform", "modules", module_dir_name,
        )
        tf_dir = os.path.normpath(base)

        if not os.path.isdir(tf_dir):
            await log(f"  Terraform module dir not found: {tf_dir} — skipping")
            return fallback_url

        env = {
            **os.environ,
            "TF_VAR_app_name":        app_name,
            "TF_VAR_resource_group":  str(cfg.get("RESOURCE_GROUP", "devops-rg")),
            "TF_VAR_location":        str(cfg.get("LOCATION", "eastus")),
            "TF_VAR_sku":             str(cfg.get("APP_SERVICE_SKU", "B1")),
            "TF_VAR_node_count":      str(cfg.get("NODE_COUNT", "1")),
            "TF_VAR_vm_size":         str(cfg.get("VM_SIZE", "Standard_B1s")),
            "TF_VAR_admin_username":  str(cfg.get("ADMIN_USER", "azureuser")),
            "TF_INPUT":               "false",
        }

        async def _tf(args: list[str]) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                "terraform", *args,
                cwd=tf_dir, env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode, stdout.decode(errors="replace")

        rc, out = await _tf(["init", "-no-color"])
        await log(f"  terraform init: {'OK' if rc == 0 else 'FAILED'}")
        if rc != 0:
            await log(out[-2000:])
            await log("  terraform failure, using fallback URL and continuing")
            return fallback_url

        rc, out = await _tf(["apply", "-auto-approve", "-no-color"])
        await log(f"  terraform apply: {'OK' if rc == 0 else 'FAILED'}")
        if rc != 0:
            await log(out[-2000:])
            await log("  terraform apply failed, using fallback URL and continuing")
            return fallback_url

        rc, out = await _tf(["output", "-json"])
        if rc == 0:
            try:
                outputs = _json.loads(out)
                url = outputs.get("app_url", {}).get("value", "")
                if url:
                    return str(url)
            except _json.JSONDecodeError:
                await log("  terraform output JSON parse failed")

        return fallback_url

    except FileNotFoundError as exc:
        await log(f"  terraform command not found: {exc}")
        await log("  ensure terraform is installed and in PATH")
        return fallback_url
    except Exception as exc:
        await log(f"  unexpected terraform exception: {exc}")
        await log(traceback.format_exc())
        await log("  continuing with fallback URL")
        return fallback_url

    env = {
        **os.environ,
        "TF_VAR_app_name":        app_name,
        "TF_VAR_resource_group":  str(cfg.get("RESOURCE_GROUP", "devops-rg")),
        "TF_VAR_location":        str(cfg.get("LOCATION", "eastus")),
        "TF_VAR_sku":             str(cfg.get("APP_SERVICE_SKU", "B1")),
        "TF_VAR_node_count":      str(cfg.get("NODE_COUNT", "1")),
        "TF_VAR_vm_size":         str(cfg.get("VM_SIZE", "Standard_B1s")),
        "TF_VAR_admin_username":  str(cfg.get("ADMIN_USER", "azureuser")),
        "TF_INPUT":               "false",
    }

    async def _tf(args: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "terraform", *args,
            cwd=tf_dir, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace")

    rc, out = await _tf(["init", "-no-color"])
    await log(f"  terraform init: {'OK' if rc == 0 else 'FAILED'}")
    if rc != 0:
        await log(out[-500:])
        return fallback_url

    rc, out = await _tf(["apply", "-auto-approve", "-no-color"])
    await log(f"  terraform apply: {'OK' if rc == 0 else 'FAILED'}")
    if rc != 0:
        await log(out[-500:])
        return fallback_url

    rc, out = await _tf(["output", "-json"])
    if rc == 0:
        try:
            outputs = _json.loads(out)
            url = outputs.get("app_url", {}).get("value", "")
            if url:
                return str(url)
        except _json.JSONDecodeError:
            pass

    return fallback_url


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


async def _generate_ci_with_deploy(branch: str, tech: dict, config: dict) -> str:
    """Generate CI/CD YAML with both build and deploy stages."""
    from .pipelines import _build_lang_steps, _build_deploy_steps  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    
    language = tech.get("language", "python")
    build_tool = tech.get("buildTool", "pip")
    lang_steps = _build_lang_steps(language, build_tool)
    
    # Determine artifact path
    artifact_paths = {
        "javascript": "dist/",
        "typescript": "dist/",
        "python": "app.zip",
        "java": "target/*.jar" if build_tool == "maven" else "build/libs/*.jar",
        "go": "main",
        "dotnet": "publish/",
    }
    artifact_path = artifact_paths.get(language, "dist/")
    
    # Build job (ci)
    build_steps = [
        {"uses": "actions/checkout@v4"},
        *lang_steps,
        {
            "name": "Upload artifact",
            "uses": "actions/upload-artifact@v4",
            "with": {"name": "build-artifact", "path": artifact_path, "retention-days": 7},
        },
    ]
    
    
    # Create complete workflow
    workflow = {
        "name": "CI/CD Pipeline",
        "on": {
            "push": {"branches": [branch]},
            "pull_request": {"branches": [branch]},
        },
        "jobs": {
            "build": {
                "runs-on": "ubuntu-latest",
                "steps": build_steps,
            },
        },
    }
    
    return yaml.dump(workflow, default_flow_style=False, sort_keys=False, allow_unicode=True)

async def _generate_cd_with_deploy(branch: str, tech: dict, config: dict) -> str:
    """Generate CI/CD YAML with both build and deploy stages."""
    from .pipelines import _build_lang_steps, _build_deploy_steps  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    
    language = tech.get("language", "python")
    build_tool = tech.get("buildTool", "pip")
    
    
    # Create deploy config (cd)
    deploy_config = {
        "infrastructure_type": "azure-web-app",
        "resource_name": config.get("APP_NAME", "devops-app"),
        "resource_group": config.get("RESOURCE_GROUP", "devops-rg"),
        "sku": config.get("APP_SERVICE_SKU", "B1"),
        "app_type": "server",
        "tech": tech,
    }
    
    # Get deploy steps
    deploy_steps = _build_deploy_steps(deploy_config)
    
    # Create complete workflow
    workflow = {
        "name": "CI/CD Pipeline",
        "on": {
            "push": {"branches": [branch]},
            "pull_request": {"branches": [branch]},
        },
        "jobs": {
            "deploy": {
                "runs-on": "ubuntu-latest",
                "needs": "build",
                "if": f"github.ref == 'refs/heads/{branch}' && needs.build.result == 'success'",
                "steps": deploy_steps,
            },
        },
    }
    
    return yaml.dump(workflow, default_flow_style=False, sort_keys=False, allow_unicode=True)


async def _push_azure_secrets(
    repo: str, cfg: dict, gh_token: str, actual_app_name: str, resource_group: str = ""
) -> None:
    from .pipelines import _set_github_secret  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    # Prefer credentials supplied in the committed config.py; fall back to environment variables
    tenant_id       = str(cfg.get("TENANT_ID",       os.getenv("AZURE_TENANT_ID",       "")))
    subscription_id = str(cfg.get("SUBSCRIPTION_ID", os.getenv("AZURE_SUBSCRIPTION_ID", "")))
    client_id       = str(cfg.get("AZURE_CLIENT_ID",  os.getenv("AZURE_CLIENT_ID", "")))
    client_secret   = str(cfg.get("AZURE_CLIENT_SECRET", os.getenv("AZURE_CLIENT_SECRET", "")))
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

    if rg and actual_app_name:
        try:
            from azure.identity import ClientSecretCredential  # noqa: PLC0415
            from azure.mgmt.web import WebSiteManagementClient  # noqa: PLC0415
            import asyncio as _asyncio  # noqa: PLC0415
            cred = ClientSecretCredential(tenant_id, client_id, client_secret)
            web_client = WebSiteManagementClient(cred, subscription_id)
            profile = await _asyncio.to_thread(
                lambda: web_client.web_apps.list_publishing_profile_xml_with_secrets(
                    rg, actual_app_name, {"format": "WebDeploy"}
                ).read().decode("utf-8")
            )
            await _set_github_secret(repo, "AZURE_WEBAPP_PUBLISH_PROFILE", profile, gh_token)
            logger.info("Publish profile pushed for %s", actual_app_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch publish profile for %s: %s", actual_app_name, exc)


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
