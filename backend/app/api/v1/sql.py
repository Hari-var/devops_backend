from app.db import AsyncSessionLocal, get_db
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException #type: ignore
from starlette import status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Approval
from typing import Annotated, Optional, Literal

db_dependency = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter()

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
    created_at: Optional[float]
    
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
    import uuid, time  # noqa: PLC0415
    new_repo = Approval(
        id=str(uuid.uuid4()),
        created_at=time.time(),
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