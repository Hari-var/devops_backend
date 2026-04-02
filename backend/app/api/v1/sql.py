from ...db import AsyncSessionLocal, get_db
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException #type: ignore
from fastapi.responses import StreamingResponse
from starlette import status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ...models import Approval
from typing import Annotated, Optional, Literal, AsyncGenerator
import asyncio

db_dependency = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter()

# SSE subscribers: approval_id → list[asyncio.Queue]
_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}

class Repo_response(BaseModel):

    repo : str
    branch : str
    status : Literal['pending', 'approved']
    commit_sha : str
    commit_message : str
    committed_by: str
    committed_at: datetime
    changed_files: list
    config: dict
    # provision_response : dict
    # techstack : dict

class Repo_update_response(BaseModel):

    repo_name : Optional[str]
    branch : Optional[str]
    infrastructure : Optional[dict]
    status : Optional[Literal['pending', 'approved']]
    techstack : Optional[dict]
    commit_sha: Optional[str]
    commit_message: Optional[str]
    committed_by: Optional[str]
    committed_at: Optional[datetime]
    changed_files: Optional[list]

    # Config parsed from config.py in the repo
    config: Optional[dict]

    # Filled after Stage 1 (tech detection)
    detected_tech: Optional[dict]

    # Pipeline progress: 0=pending 1=tech 2=terraform 3=cicd 4=monitoring 5=done
    pipeline_stage: Optional[int]

    # Per-stage logs: {"1": ["line1","line2"], "2": [...], ...}
    stage_logs: Optional[dict]

    # Legacy flat log list (kept for backwards compat with SSE replay)
    logs: Optional[list]

    # URLs captured after pipeline completes
    terraform_url: Optional[str | None]
    deployed_url: Optional[str | None]
    actions_run_url: Optional[str | None]
    created_at: Optional[datetime]
    
@router.get("/all_details")
async def get_all_details(db: db_dependency):
    try:
        result = await db.execute(select(Approval))
        repos = result.scalars().all()
        return repos if repos else {"message": "No repositories found"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"An error occurred: {str(e)}")

@router.get("/get_details/{id}")
async def get_repo_details(id: str, db: db_dependency):
    result = await db.execute(select(Approval).where(Approval.id == id))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repo

async def save_approval(repo: Repo_response) -> Approval:
    import uuid  # noqa: PLC0415
    new_repo = Approval(
        id=str(uuid.uuid4()),
        created_at=datetime.now(),
        detected_tech={},
        pipeline_stage=0,
        stage_logs={},
        logs=[],
        terraform_url=None,
        deployed_url=None,
        actions_run_url=None,
        **repo.dict(),
    )
    async with AsyncSessionLocal() as db:
        db.add(new_repo)
        await db.commit()
        await db.refresh(new_repo)
    return new_repo


@router.post("/add_details")
async def add_repo_details(repo: Repo_response, db: db_dependency):
    try:
        return await save_approval(repo)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"An error occurred: {str(e)}")


@router.put("/update_details/{commit_sha}")
async def update_repo_data(commit_sha: str, updated_data: Repo_update_response):
    try:
        await update_repo_details(commit_sha, updated_data)
        return {"message": "Repository data updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"An error occurred: {str(e)}")


async def update_repo_details(commit_sha: str, updated_data: Repo_update_response):
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.commit_sha == commit_sha))
            repo = result.scalar_one_or_none()
            if not repo:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
            for key, value in updated_data.model_dump(exclude_unset=True).items():
                setattr(repo, key, value)
            await db.commit()
            await db.refresh(repo)
            return repo
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"An error occurred: {str(e)}")

async def approve_repo(commit_sha: str, db: AsyncSession):
    try:
        result = await db.execute(select(Approval).where(Approval.commit_sha == commit_sha))
        repo = result.scalar_one_or_none()
        if not repo:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
        repo.status = 'approved'
        await db.commit()
        await db.refresh(repo)
        return repo
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"An error occurred: {str(e)}")


# ---------------------------------------------------------------------------
# Log storage and streaming functions
# ---------------------------------------------------------------------------

async def push_log(approval_id: str, message: str, stage: int = 0) -> None:
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

    # Fan-out to SSE subscribers
    event_data = f"{stage}|{message}" if stage > 0 else message
    for queue in _SUBSCRIBERS.get(approval_id, []):
        queue.put_nowait(event_data)


async def update_pipeline_stage(approval_id: str, stage: int, status_val: str | None = None, **kwargs) -> None:
    """Update pipeline stage and optionally status and other fields."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        record = result.scalar_one_or_none()
        if record:
            record.pipeline_stage = stage
            if status_val:
                record.status = status_val
            for k, v in kwargs.items():
                setattr(record, k, v)
            await db.commit()
    
    # Emit stage change event
    for queue in _SUBSCRIBERS.get(approval_id, []):
        queue.put_nowait(f"STAGE:{stage}")


# ---------------------------------------------------------------------------
# CI/CD Pipeline Generation (Step 2)
# ---------------------------------------------------------------------------

async def generate_and_commit_cicd(approval_id: str, gh_token: str) -> str:
    """Generate CI/CD YAML with both build and deploy stages, commit to repo. Returns actions run URL."""
    from .pipelines import _commit_file, _verify_repo_access, _build_lang_steps, _build_deploy_steps  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Approval not found")
        repo = record.repo
        branch = record.branch
        tech = record.detected_tech or {}
        config = record.config or {}
    
    await push_log(approval_id, "Generating CI/CD pipeline YAML...", 2)
    
    # Verify repo access
    resolved_branch = await _verify_repo_access(repo, branch, gh_token)
    
    # Build language-specific steps
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
    
    # Build job
    build_steps = [
        {"uses": "actions/checkout@v4"},
        *lang_steps,
        {
            "name": "Upload artifact",
            "uses": "actions/upload-artifact@v4",
            "with": {"name": "build-artifact", "path": artifact_path, "retention-days": 7},
        },
    ]
    
    # Create deploy config from approval config
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
            "push": {"branches": [resolved_branch]},
            "pull_request": {"branches": [resolved_branch]},
        },
        "jobs": {
            "build": {
                "runs-on": "ubuntu-latest",
                "steps": build_steps,
            },
            "deploy": {
                "runs-on": "ubuntu-latest",
                "needs": "build",
                "if": f"github.ref == 'refs/heads/{resolved_branch}' && needs.build.result == 'success'",
                "steps": deploy_steps,
            },
        },
    }
    
    cicd_yaml = yaml.dump(workflow, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    # Commit to repo
    await _commit_file(
        repo, resolved_branch,
        ".github/workflows/cicd.yml",
        cicd_yaml,
        "chore: add CI/CD pipeline via DevOps Agent",
        gh_token,
    )
    
    await push_log(approval_id, "Committed: .github/workflows/cicd.yml", 2)
    await push_log(approval_id, "CI/CD pipeline with deploy stage created.", 2)
    
    return f"https://github.com/{repo}/actions"


async def _push_azure_secrets(repo: str, config: dict, gh_token: str, record) -> None:
    """Push Azure credentials and app name as GitHub secrets."""
    from .pipelines import _set_github_secret  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415
    
    # Get credentials from config or environment
    tenant_id = str(config.get("TENANT_ID", os.getenv("AZURE_TENANT_ID", "")))
    subscription_id = str(config.get("SUBSCRIPTION_ID", os.getenv("AZURE_SUBSCRIPTION_ID", "")))
    client_id = str(config.get("AZURE_CLIENT_ID", os.getenv("AZURE_CLIENT_ID", "")))
    client_secret = str(config.get("AZURE_CLIENT_SECRET", os.getenv("AZURE_CLIENT_SECRET", "")))
    app_name = str(config.get("APP_NAME", "devops-app"))
    
    if not all([tenant_id, subscription_id, client_id, client_secret]):
        return
    
    # Create Azure credentials JSON
    azure_creds = _json.dumps({
        "clientId": client_id,
        "clientSecret": client_secret,
        "tenantId": tenant_id,
        "subscriptionId": subscription_id,
    })
    
    # Push secrets to GitHub
    await _set_github_secret(repo, "AZURE_CREDENTIALS", azure_creds, gh_token)
    await _set_github_secret(repo, "AZURE_WEBAPP_NAME", app_name, gh_token)


# ---------------------------------------------------------------------------
# Automated Flow (run_flow2) - Triggered on config detection
# ---------------------------------------------------------------------------

async def run_flow2(approval_id: str, gh_token: str) -> None:
    """Automated flow: Tech detection → CI generation → Monitor build → Provision → Deploy."""
    import traceback  # noqa: PLC0415
    
    try:
        # Update status to running
        await update_pipeline_stage(approval_id, 0, "running")
        await push_log(approval_id, "Starting automated deployment flow...", 0)
        
        # Stage 1: Tech Detection
        await update_pipeline_stage(approval_id, 1)
        await push_log(approval_id, "Detecting technology stack...", 1)
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            if not record:
                return
            repo = record.repo
            branch = record.branch
        
        # Detect tech stack
        from .analysis import TechDetectionRequest, tech_detection  # noqa: PLC0415
        tech = await tech_detection(
            TechDetectionRequest(repoFullName=repo, branch=branch), gh_token
        )
        
        # Store detected tech
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            if record:
                record.detected_tech = tech
                await db.commit()
        
        await push_log(approval_id, f"Language: {tech.get('language', 'unknown')}", 1)
        await push_log(approval_id, f"Framework: {tech.get('framework') or 'none'}", 1)
        await push_log(approval_id, f"Build tool: {tech.get('buildTool') or 'none'}", 1)
        await push_log(approval_id, "Tech detection complete.", 1)
        
        # Stage 2: Generate and commit CI/CD
        await update_pipeline_stage(approval_id, 2)
        actions_url = await generate_and_commit_cicd(approval_id, gh_token)
        
        # Push Azure secrets to GitHub
        await push_log(approval_id, "Configuring Azure secrets...", 2)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            if record:
                config = record.config or {}
                await _push_azure_secrets(repo, config, gh_token, record)
        
        await push_log(approval_id, "Azure secrets configured.", 2)
        
        # Store actions URL
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            if record:
                record.actions_run_url = actions_url
                await db.commit()
        
        # Mark as done (for now - will add more stages later)
        await update_pipeline_stage(approval_id, 2, "done")
        await push_log(approval_id, "PIPELINE COMPLETE", 0)
        await push_log(approval_id, f"Actions URL: {actions_url}", 0)
        
        # Notify subscribers
        for queue in _SUBSCRIBERS.get(approval_id, []):
            queue.put_nowait("DONE")
    
    except Exception as exc:
        # Handle errors
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            record = result.scalar_one_or_none()
            if record:
                record.status = "failed"
                await db.commit()
        
        err_msg = f"PIPELINE FAILED: {repr(exc)}\n{traceback.format_exc()}"
        await push_log(approval_id, err_msg, 0)
        
        for queue in _SUBSCRIBERS.get(approval_id, []):
            queue.put_nowait("FAILED")


@router.post("/trigger/{approval_id}")
async def trigger_flow(approval_id: str, gh_token: str | None = Cookie(default=None)):
    """Manually trigger the automated flow for an approval."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Approval not found")
        if record.status != "pending":
            raise HTTPException(status_code=400, detail="Only pending approvals can be triggered")
    
    # Run flow in background
    asyncio.create_task(run_flow2(approval_id, gh_token))
    
    return {"status": "running", "approval_id": approval_id}


@router.get("/{approval_id}/logs")
async def stream_logs(approval_id: str) -> StreamingResponse:
    """Stream logs via SSE for real-time updates."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.id == approval_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Approval not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        _SUBSCRIBERS.setdefault(approval_id, []).append(queue)

        # Replay existing logs from DB
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Approval).where(Approval.id == approval_id))
            rec = result.scalar_one_or_none()
            if rec:
                stage = getattr(rec, "pipeline_stage", 0)
                if stage > 0:
                    yield f"data: STAGE:{stage}\n\n"
                
                # Replay stage logs
                sl: dict = getattr(rec, "stage_logs", {}) or {}
                for s_key in sorted(sl.keys(), key=int):
                    for line in sl[s_key]:
                        yield f"data: {s_key}|{line}\n\n"
                
                # Replay global logs
                for line in (rec.logs or []):
                    yield f"data: {line}\n\n"
                
                if rec.status == "done":
                    yield "data: DONE\n\n"
                elif rec.status == "failed":
                    yield "data: FAILED\n\n"

        try:
            while True:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Approval).where(Approval.id == approval_id))
                    rec = result.scalar_one_or_none()
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
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )