from __future__ import annotations

from fastapi import APIRouter, Cookie, HTTPException
import logging
from pydantic import BaseModel
import httpx
import base64
import yaml
import asyncio

from ...services.pipeline_monitor import (
    get_failed_workflow_runs,
    get_workflow_run_logs,
    get_workflow_run_jobs,
    extract_error_from_logs,
)
from ...services.ai_analyzer import analyze_pipeline_error

router = APIRouter()
logger = logging.getLogger(__name__)


class PipelinePreviewRequest(BaseModel):
    repoFullName: str
    branch: str
    tech: dict
    enableSast: bool = True
    enableDast: bool = True


class PipelineCreateRequest(BaseModel):
    repoFullName: str
    branch: str
    tech: dict
    enableSast: bool = True
    enableDast: bool = True


def _generate_pipeline_yaml(
    repo_full_name: str, branch: str, tech: dict, enable_sast: bool, enable_dast: bool
) -> str:
    """Generate GitHub Actions YAML based on detected technology."""
    language = tech.get("language", "python")
    build_tool = tech.get("buildTool", "pip")
    framework = tech.get("framework", "")

    # Build steps based on language
    build_steps = []
    artifact_path = ""
    
    if language == "python":
        artifact_path = "dist/"
        if build_tool == "poetry":
            build_steps = [
                "- uses: actions/checkout@v4",
                "- uses: actions/setup-python@v5",
                "  with:",
                "    python-version: '3.11'",
                "- name: Install Poetry",
                "  uses: snok/install-poetry@v1",
                "- name: Install dependencies",
                "  run: poetry install --no-interaction --no-ansi",
                "- name: Build package",
                "  run: poetry build",
            ]
        else:
            build_steps = [
                "- uses: actions/checkout@v4",
                "- uses: actions/setup-python@v5",
                "  with:",
                "    python-version: '3.11'",
                "- name: Install dependencies",
                "  run: pip install -r requirements.txt",
                "- name: Create distribution",
                "  run: |",
                "    pip install build",
                "    python -m build",
            ]
    elif language == "javascript":
        artifact_path = "build/"
        build_steps = [
            "- uses: actions/checkout@v4",
            "- uses: actions/setup-node@v4",
            "  with:",
            "    node-version: '20'",
            "- name: Install dependencies",
            "  run: npm ci",
            "- name: Build application",
            "  run: npm run build",
        ]
    elif language == "java":
        if build_tool == "maven":
            artifact_path = "target/*.jar"
            build_steps = [
                "- uses: actions/checkout@v4",
                "- uses: actions/setup-java@v4",
                "  with:",
                "    distribution: 'temurin'",
                "    java-version: '17'",
                "- name: Build with Maven",
                "  run: mvn clean package -DskipTests",
            ]
        else:  # gradle
            artifact_path = "build/libs/*.jar"
            build_steps = [
                "- uses: actions/checkout@v4",
                "- uses: actions/setup-java@v4",
                "  with:",
                "    distribution: 'temurin'",
                "    java-version: '17'",
                "- name: Build with Gradle",
                "  run: ./gradlew build -x test",
            ]
    else:
        artifact_path = "build/"
        build_steps = [
            "- uses: actions/checkout@v4",
            "- name: Build application",
            "  run: echo 'Add build steps for your language'",
        ]

    yaml_content = f"""name: CI Build Pipeline

on:
  push:
    branches: [{branch}]
  pull_request:
    branches: [{branch}]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
"""
    for step in build_steps:
        yaml_content += f"      {step}\n"
    
    # Add artifact upload
    yaml_content += f"""
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: build-artifact
          path: {artifact_path}
          retention-days: 30
"""

    return yaml_content


@router.post("/preview")
async def preview_pipeline(payload: PipelinePreviewRequest, gh_token: str | None = Cookie(default=None)):
    """
    Generate a GitHub Actions YAML preview based on tech and feature flags.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub")

    # Use the repo and branch from the request payload
    repo_full_name = payload.repoFullName
    branch = payload.branch
    logger.info("Generating preview for repo=%s branch=%s", repo_full_name, branch)

    yaml_content = _generate_pipeline_yaml(
        repo_full_name,
        branch,
        payload.tech,
        payload.enableSast,
        payload.enableDast,
    )
    return {"yaml": yaml_content}


@router.post("/create")
async def create_pipeline(
    payload: PipelineCreateRequest, gh_token: str | None = Cookie(default=None)
):
    """
    Generate and commit the GitHub Actions workflow file to the selected repository's default branch.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub")

    # Use the repo and branch from the request payload
    repo_full_name = payload.repoFullName
    branch = payload.branch
    logger.info("Creating pipeline in repo=%s branch=%s", repo_full_name, branch)
    logger.info("Tech payload: %s", payload.tech)
    
    # Check token permissions first
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Check user permissions on the repo
            perm_url = f"https://api.github.com/repos/{repo_full_name}"
            perm_res = await client.get(
                perm_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {gh_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if perm_res.status_code == 200:
                repo_data = perm_res.json()
                permissions = repo_data.get("permissions", {})
                logger.info("Repository permissions: %s", permissions)
                
                if not permissions.get("push", False):
                    raise HTTPException(
                        status_code=403,
                        detail=f"You don't have write access to '{repo_full_name}'. Please make sure you're the owner or have push access."
                    )
            else:
                logger.error("Failed to check permissions: %s", perm_res.text)
                raise HTTPException(
                    status_code=404,
                    detail=f"Repository '{repo_full_name}' not found or you don't have access"
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error checking permissions: %s", e)
        raise HTTPException(status_code=500, detail=f"Error checking permissions: {str(e)}")
    
    # Verify the repository and branch exist
    repo_data = None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            repo_url = f"https://api.github.com/repos/{repo_full_name}"
            logger.info("Verifying repository: %s", repo_url)
            repo_res = await client.get(
                repo_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {gh_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if repo_res.status_code != 200:
                logger.error("Repository not found or no access: %s", repo_res.text)
                raise HTTPException(
                    status_code=404, 
                    detail=f"Repository '{repo_full_name}' not found or you don't have access to it"
                )
            repo_data = repo_res.json()
            default_branch = repo_data.get("default_branch", "main")
            logger.info("Repository default branch: %s, requested branch: %s", default_branch, branch)
            
            # If requested branch is not the default, use default branch instead
            if branch != default_branch:
                logger.warning("Requested branch '%s' is not default branch '%s'. Using default branch.", branch, default_branch)
                branch = default_branch
            
            # Verify branch exists
            branch_url = f"https://api.github.com/repos/{repo_full_name}/branches/{branch}"
            logger.info("Verifying branch: %s", branch_url)
            branch_res = await client.get(
                branch_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {gh_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if branch_res.status_code != 200:
                logger.error("Branch not found: %s", branch_res.text)
                raise HTTPException(
                    status_code=404, 
                    detail=f"Branch '{branch}' not found in repository '{repo_full_name}'"
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error verifying repository/branch: %s", e)
        raise HTTPException(status_code=500, detail=f"Error verifying repository: {str(e)}")

    # Generate YAML
    yaml_content = _generate_pipeline_yaml(
        repo_full_name, branch, payload.tech, payload.enableSast, payload.enableDast
    )

    # Use GitHub's contents API for workflow file creation
    contents_url = f"https://api.github.com/repos/{repo_full_name}/contents/.github/workflows/ci.yml"
    logger.info("Uploading workflow to: %s", contents_url)
    encoded_content = base64.b64encode(yaml_content.encode("utf-8")).decode("utf-8")
    
    # Retry logic for handling SHA conflicts
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Get the latest file SHA each time to avoid conflicts
                get_res = await client.get(
                    contents_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {gh_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params={"ref": branch}
                )
                
                file_sha = None
                if get_res.status_code == 200:
                    # File exists, get its SHA for update
                    file_sha = get_res.json().get("sha")
                    logger.info("Attempt %d: File exists, will update with SHA: %s", attempt + 1, file_sha)
                else:
                    logger.info("Attempt %d: File doesn't exist, will create new", attempt + 1)
                
                # Prepare the request body
                request_body = {
                    "message": "chore: add CI/CD pipeline via DevOps Agent",
                    "content": encoded_content
                }
                if file_sha:
                    request_body["sha"] = file_sha
                
                put_res = await client.put(
                    contents_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {gh_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=request_body,
                )

                if put_res.status_code in [200, 201]:
                    result = put_res.json()
                    logger.info("Successfully created/updated workflow file on attempt %d", attempt + 1)
                    return {
                        "status": "created",
                        "repo": repo_full_name,
                        "branch": branch,
                        "workflow_path": ".github/workflows/ci.yml",
                        "commit_sha": result.get("commit", {}).get("sha", "")
                    }
                elif put_res.status_code == 409:
                    # SHA conflict - retry with fresh SHA
                    logger.warning("Attempt %d: SHA conflict (409), retrying...", attempt + 1)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)  # Brief delay before retry
                        continue
                    else:
                        raise HTTPException(
                            status_code=409,
                            detail="File was modified by another process. Please try again."
                        )
                else:
                    error_detail = put_res.text
                    logger.error(
                        "Commit failed on attempt %d: status=%s, body=%s",
                        attempt + 1,
                        put_res.status_code,
                        error_detail,
                    )
                    
                    # Parse error message for better user feedback
                    try:
                        error_json = put_res.json()
                        error_msg = error_json.get("message", error_detail)
                    except:
                        error_msg = error_detail
                        
                    raise HTTPException(
                        status_code=400, 
                        detail=f"GitHub API error: {error_msg}. Make sure you have write access to the repository."
                    )
        except HTTPException:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning("Attempt %d failed with error: %s, retrying...", attempt + 1, str(e))
                await asyncio.sleep(0.5)
                continue
            else:
                logger.exception("All attempts failed: %s", e)
                raise HTTPException(status_code=500, detail=f"Failed after {max_retries} attempts: {str(e)}")
                
    # If we get here, all retries failed
    raise HTTPException(status_code=500, detail="Failed to create pipeline after multiple attempts")


@router.get("/failed")
async def get_failed_pipelines(
    days: int = 7, gh_token: str | None = Cookie(default=None)
):
    """
    Get all failed pipeline runs across all repositories the user has access to.
    Includes AI analysis for each failure.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub")

    # Get user's repositories
    async with httpx.AsyncClient(timeout=20) as client:
        repos_res = await client.get(
            "https://api.github.com/user/repos?per_page=100&sort=updated",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {gh_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        if repos_res.status_code != 200:
            raise HTTPException(
                status_code=502, detail="Failed to fetch repositories from GitHub"
            )

        repos = repos_res.json()

    # Collect failed runs from all repos
    all_failed_runs = []

    for repo in repos:
        repo_full_name = repo["full_name"]
        failed_runs = await get_failed_workflow_runs(repo_full_name, gh_token, days=days)

        for run in failed_runs:
            # Get jobs to identify which job failed
            jobs = await get_workflow_run_jobs(repo_full_name, run["id"], gh_token)
            failed_job = next((j for j in jobs if j.get("conclusion") == "failure"), None)

            # Get error logs
            logs = await get_workflow_run_logs(repo_full_name, run["id"], gh_token)
            error_excerpt = extract_error_from_logs(logs)

            # Try to get tech stack - in production, store this when pipeline is created
            # For now, we'll use a basic detection or defaults
            # TODO: Store tech stack in database when pipeline is created
            tech_stack = {
                "language": "unknown",
                "framework": None,
                "buildTool": None,
                "hasDockerfile": False,
                "hasHelm": False,
                "hasTerraform": False,
            }
            
            # Try to detect tech stack from repo (quick check)
            # In production, you'd query a database where tech stack is stored
            try:
                from .analysis import tech_detection
                from .analysis import TechDetectionRequest
                
                detection_req = TechDetectionRequest(
                    repoFullName=repo_full_name,
                    branch=run.get("head_branch", "main"),
                )
                detected_tech = await tech_detection(detection_req, gh_token)
                tech_stack = detected_tech
            except Exception:
                # If detection fails, use defaults
                pass

            # Get AI analysis (synchronous call, but we're in async context)
            ai_analysis = await asyncio.to_thread(analyze_pipeline_error, error_excerpt, tech_stack)

            all_failed_runs.append(
                {
                    "id": run["id"],
                    "repo": repo_full_name,
                    "workflow_name": run.get("name", "Unknown"),
                    "branch": run.get("head_branch", "unknown"),
                    "commit_sha": run.get("head_sha", "")[:7],
                    "failed_at": run.get("updated_at", run.get("created_at")),
                    "failed_job": failed_job.get("name") if failed_job else "Unknown",
                    "error_excerpt": error_excerpt[:500],  # First 500 chars
                    "ai_reason": ai_analysis.get("reason", ""),
                    "ai_resolution": ai_analysis.get("resolution", ""),
                    "run_url": run.get("html_url", ""),
                }
            )

    # Sort by most recent first
    all_failed_runs.sort(key=lambda x: x["failed_at"], reverse=True)

    return {"failed_runs": all_failed_runs, "total": len(all_failed_runs)}


@router.get("/{repo_full_name}/runs/{run_id}/analyze")
async def analyze_pipeline_run(
    repo_full_name: str,
    run_id: int,
    gh_token: str | None = Cookie(default=None),
):
    """
    Get detailed AI analysis for a specific failed pipeline run.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub")

    # Get run details
    async with httpx.AsyncClient(timeout=20) as client:
        run_res = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {gh_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        if run_res.status_code != 200:
            raise HTTPException(status_code=404, detail="Pipeline run not found")

        run = run_res.json()

        if run.get("conclusion") != "failure":
            raise HTTPException(
                status_code=400, detail="This pipeline run did not fail"
            )

    # Get logs and extract error
    logs = await get_workflow_run_logs(repo_full_name, run_id, gh_token)
    error_excerpt = extract_error_from_logs(logs)

    # Get tech stack (try to get from analysis endpoint or use defaults)
    # In production, you'd store tech stack when pipeline is created
    tech_stack = {
        "language": "unknown",
        "framework": None,
        "buildTool": None,
        "hasDockerfile": False,
        "hasHelm": False,
        "hasTerraform": False,
    }

    # Get AI analysis (synchronous call, but we're in async context)
    ai_analysis = await asyncio.to_thread(analyze_pipeline_error, error_excerpt, tech_stack)

    return {
        "run_id": run_id,
        "repo": repo_full_name,
        "workflow_name": run.get("name", "Unknown"),
        "branch": run.get("head_branch", "unknown"),
        "commit_sha": run.get("head_sha", ""),
        "failed_at": run.get("updated_at", run.get("created_at")),
        "error_logs": error_excerpt,
        "ai_analysis": ai_analysis,
        "run_url": run.get("html_url", ""),
    }