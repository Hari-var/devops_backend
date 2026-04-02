from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import httpx
import yaml
from fastapi import APIRouter, Cookie, HTTPException
from nacl import encoding
from nacl import public as nacl_public
from pydantic import BaseModel

from ...services.ai_analyzer import analyze_pipeline_error
from ...services.pipeline_monitor import (
    extract_error_from_logs,
    get_failed_workflow_runs,
    get_workflow_run_jobs,
    get_workflow_run_logs,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

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
    deploy: dict | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth_headers(gh_token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {gh_token}"}


async def _find_build_file(
    repo_full_name: str,
    branch: str,
    gh_token: str,
    filename: str,
    max_depth: int = 3,
) -> str | None:
    """Recursively search for a build file by name. Returns relative path or None."""
    _SKIP = {"node_modules", "vendor", ".git", ".github", "__pycache__", ".venv", "venv"}

    async def _search(path: str, depth: int) -> str | None:
        if depth < 0:
            return None
        url = f"{_GITHUB_API}/repos/{repo_full_name}/contents/{path}"
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url, headers=_auth_headers(gh_token), params={"ref": branch})
        if res.status_code != 200:
            return None
        for item in res.json():
            name = item.get("name", "")
            if name in _SKIP:
                continue
            if name.lower() == filename.lower():
                return item.get("path", "")
            if item.get("type") == "dir":
                found = await _search(item.get("path", ""), depth - 1)
                if found:
                    return found
        return None

    return await _search("", max_depth)


async def _set_github_secret(
    repo_full_name: str,
    secret_name: str,
    secret_value: str,
    gh_token: str,
) -> None:
    """Encrypt and push a GitHub Actions secret to the repository using libsodium."""
    async with httpx.AsyncClient(timeout=15) as client:
        key_res = await client.get(
            f"{_GITHUB_API}/repos/{repo_full_name}/actions/secrets/public-key",
            headers=_auth_headers(gh_token),
        )
        if key_res.status_code != 200:
            logger.warning("Could not fetch repo public key for secret '%s': %s", secret_name, key_res.text)
            return

        key_data = key_res.json()
        public_key_b64: str = key_data["key"]
        key_id: str = key_data["key_id"]

        pk = nacl_public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder)
        box = nacl_public.SealedBox(pk)
        encrypted_b64 = base64.b64encode(box.encrypt(secret_value.encode())).decode()

        put_res = await client.put(
            f"{_GITHUB_API}/repos/{repo_full_name}/actions/secrets/{secret_name}",
            headers=_auth_headers(gh_token),
            json={"encrypted_value": encrypted_b64, "key_id": key_id},
        )

    if put_res.status_code in (201, 204):
        logger.info("Secret '%s' set on repo %s", secret_name, repo_full_name)
    else:
        logger.warning("Failed to set secret '%s': %s", secret_name, put_res.text)


def _build_lang_steps(language: str, build_tool: str) -> list[dict]:
    """Return language-specific CI steps."""
    if language == "python":
        if build_tool == "poetry":
            return [
                {"uses": "actions/setup-python@v5", "with": {"python-version": "3.11"}},
                {"uses": "snok/install-poetry@v1"},
                {"name": "Install dependencies", "run": "poetry install --no-interaction --no-ansi"},
                {"name": "Build", "run": "poetry build"},
            ]
        return [
            {"uses": "actions/setup-python@v5", "with": {"python-version": "3.11"}},
            {"name": "Install dependencies", "run": "pip install -r requirements.txt || echo 'No requirements.txt'"},
            {"name": "Package app", "run": "zip -r app.zip . -x '*.git*' '__pycache__/*' '*.pyc' 'venv/*' '.venv/*'"},
            {"name": "Verify artifact", "run": "ls -lh app.zip"},
        ]

    if language == "javascript":
        return [
            {"uses": "actions/setup-node@v4", "with": {"node-version": "20"}},
            {"name": "Install dependencies", "run": "rm -rf node_modules && npm install"},
            {"name": "Build", "run": "npm run build"},
        ]

    if language == "java":
        if build_tool == "maven":
            return [
                {"uses": "actions/setup-java@v4", "with": {"distribution": "temurin", "java-version": "17"}},
                {"name": "Build", "run": "mvn clean package -DskipTests"},
            ]
        return [
            {"uses": "actions/setup-java@v4", "with": {"distribution": "temurin", "java-version": "17"}},
            {"name": "Build", "run": "./gradlew build -x test"},
        ]

    if language == "go":
        return [
            {"uses": "actions/setup-go@v5", "with": {"go-version": "1.21"}},
            {"name": "Build", "run": "go build -o main ./..."},
        ]

    if language == "dotnet":
        return [
            {"uses": "actions/setup-dotnet@v4", "with": {"dotnet-version": "8.0.x"}},
            {"name": "Build", "run": "dotnet publish -c Release -o publish/"},
        ]

    return [{"name": "Build", "run": "echo 'Add build steps for your language'"}]


def _azure_runtime(tech: dict) -> tuple[str, str]:
    """Returns (az_runtime, startup_command) for Azure Web App."""
    lang = tech.get("language", "javascript").lower()
    build_tool = tech.get("buildTool", "").lower()
    framework = (tech.get("framework") or "").lower()

    if lang == "python":
        if framework in ("fastapi", "starlette"):
            return "PYTHON:3.11", "uvicorn app:app --host 0.0.0.0 --port $PORT"
        if framework == "django":
            return "PYTHON:3.11", "gunicorn app.wsgi:application --bind 0.0.0.0:$PORT"
        return "PYTHON:3.11", "uvicorn app:app --host 0.0.0.0 --port $PORT"

    if lang == "java":
        if build_tool == "maven":
            return "JAVA:17-java17", "java -jar target/*.jar"
        return "JAVA:17-java17", "java -jar build/libs/*.jar"

    if lang in ("typescript", "javascript"):
        return "NODE:20-lts", "npm start"

    if lang == "go":
        return "GO:1.21", "./main"

    if lang == "dotnet":
        return "DOTNETCORE:8.0", ""

    return "NODE:20-lts", "npm start"


def _is_static_app(deploy: dict) -> bool:
    return deploy.get("app_type", "") == "static"


def _build_deploy_steps(deploy: dict) -> list[dict]:
    """Return deploy job steps based on infrastructure type."""
    infra_type: str = deploy.get("infrastructure_type", "")
    resource_name: str = deploy.get("resource_name", "")
    resource_group: str = deploy.get("resource_group", "")
    public_ip: str = deploy.get("public_ip", "")
    admin_user: str = deploy.get("admin_user", "azureuser")

    azure_login = {
        "name": "Azure Login",
        "uses": "azure/login@v2",
        "with": {"creds": "${{ secrets.AZURE_CREDENTIALS }}"},
    }

    if infra_type == "azure-web-app":
        rg = deploy.get("resource_group", "devops-rg")
        sku = deploy.get("sku", "B1")
        is_static = _is_static_app(deploy)
        tech = deploy.get("tech", {})
        runtime, startup_cmd = _azure_runtime(tech)
        lang = tech.get("language", "javascript").lower()

        if lang == "python":
            dl_path, zip_cmd = ".", ""
        elif lang == "dotnet":
            dl_path, zip_cmd = "publish", "cd publish && zip -r ../release.zip . && cd .."
        elif lang == "go":
            dl_path, zip_cmd = ".", "zip release.zip main"
        elif lang in ("javascript", "typescript"):
            dl_path, zip_cmd = "dist", "cd dist && zip -r ../release.zip . && cd .."
        else:
            dl_path, zip_cmd = "artifact", "cd artifact && zip -r ../release.zip . && cd .."

        static_inject = (
            'echo \'{"name":"app","scripts":{"start":"npx serve -s . -l $PORT"}}\''
            " > dist/package.json"
        )
        steps: list[dict] = [
            {"name": "Download artifact", "uses": "actions/download-artifact@v4",
             "with": {"name": "build-artifact", "path": dl_path}},
            {"name": "Verify download", "run": f"ls -lah {dl_path}"},
        ]
        if is_static:
            steps.append({"name": "Inject serve package.json", "run": static_inject})
        if zip_cmd:
            steps.append({"name": "Zip artifact", "run": zip_cmd})
        else:
            steps.append({"name": "Use pre-zipped artifact", "run": "mv app.zip release.zip"})
        steps += [
            azure_login,
            {"name": "Provision Resource Group",
             "run": f"az group create --name {rg} --location eastus"},
            {"name": "Provision App Service Plan",
             "run": f"az appservice plan create --name ${{{{ secrets.AZURE_WEBAPP_NAME }}}}-plan --resource-group {rg} --sku {sku} --is-linux"},
            {"name": "Provision Web App",
             "run": f"az webapp create --name ${{{{ secrets.AZURE_WEBAPP_NAME }}}} --resource-group {rg} --plan ${{{{ secrets.AZURE_WEBAPP_NAME }}}}-plan --runtime '{runtime}'"},
        ]
        if startup_cmd:
            steps.append({
                "name": "Set startup command",
                "run": f"az webapp config set --name ${{{{ secrets.AZURE_WEBAPP_NAME }}}} --resource-group {rg} --startup-file '{startup_cmd}'",
            })
        steps.append({
            "name": "Deploy to Azure Web App",
            "run": "az webapp deploy --name ${{ secrets.AZURE_WEBAPP_NAME }} --resource-group " + rg + " --src-path release.zip --type zip",
        })
        return steps

    if infra_type == "aks":
        return [
            {"uses": "actions/checkout@v4"},
            azure_login,
            {"name": "Set AKS context", "uses": "azure/aks-set-context@v3",
             "with": {"resource-group": resource_group, "cluster-name": resource_name}},
            {"name": "Deploy to AKS", "run": "kubectl apply -f k8s/ || echo 'No k8s manifests found'"},
        ]

    if infra_type == "vm":
        return [
            {"name": "Download artifact", "uses": "actions/download-artifact@v4",
             "with": {"name": "build-artifact"}},
            {"name": "Deploy to VM via SSH", "uses": "appleboy/ssh-action@v1",
             "with": {
                 "host": public_ip,
                 "username": admin_user,
                 "key": "${{ secrets.VM_SSH_PRIVATE_KEY }}",
                 "script": "mkdir -p ~/app && cd ~/app && echo 'Deployment complete'",
             }},
        ]

    return []


async def _generate_pipeline_yaml(
    repo_full_name: str,
    branch: str,
    tech: dict,
    enable_sast: bool,  # noqa: ARG001
    enable_dast: bool,  # noqa: ARG001
    gh_token: str,
    deploy: dict | None = None,
) -> str:
    """Build and serialise a GitHub Actions CI/CD workflow as YAML."""
    language: str = tech.get("language", "python")
    build_tool: str = tech.get("buildTool", "pip")

    working_dir = ""
    artifact_path = "build/"

    if language == "python":
        artifact_path = "app.zip"
        lookup = "pyproject.toml" if build_tool == "poetry" else "requirements.txt"
        found = await _find_build_file(repo_full_name, branch, gh_token, lookup)
        if found and "/" in found:
            working_dir = found.rsplit("/", 1)[0]
            artifact_path = f"{working_dir}/app.zip"

    elif language == "javascript":
        artifact_path = "dist/"
        found = await _find_build_file(repo_full_name, branch, gh_token, "package.json")
        if found and "/" in found:
            working_dir = found.rsplit("/", 1)[0]
            artifact_path = f"{working_dir}/dist/"

    elif language == "java":
        lookup = "pom.xml" if build_tool == "maven" else "build.gradle"
        found = await _find_build_file(repo_full_name, branch, gh_token, lookup)
        if found and "/" in found:
            working_dir = found.rsplit("/", 1)[0]
        if build_tool == "maven":
            artifact_path = f"{working_dir}/target/*.jar" if working_dir else "target/*.jar"
        else:
            artifact_path = f"{working_dir}/build/libs/*.jar" if working_dir else "build/libs/*.jar"

    lang_steps = _build_lang_steps(language, build_tool)

    build_steps: list[dict] = [
        {"uses": "actions/checkout@v4"},
        *lang_steps,
        {
            "name": "Upload artifact",
            "uses": "actions/upload-artifact@v4",
            "with": {"name": "build-artifact", "path": artifact_path, "retention-days": 7},
        },
    ]

    build_job: dict = {"runs-on": "ubuntu-latest", "steps": build_steps}
    if working_dir:
        build_job["defaults"] = {"run": {"working-directory": working_dir}}

    deploy_steps = _build_deploy_steps(deploy) if deploy else []

    workflow: dict = {
        "name": "CI/CD Pipeline",
        "on": {
            "push": {"branches": [branch]},
            "pull_request": {"branches": [branch]},
        },
        "jobs": {"build": build_job},
    }

    if deploy_steps:
        workflow["jobs"]["deploy"] = {
            "runs-on": "ubuntu-latest",
            "needs": "build",
            "if": f"github.ref == 'refs/heads/{branch}' && needs.build.result == 'success'",
            "steps": deploy_steps,
        }

    return yaml.dump(workflow, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _generate_ci_yaml(branch: str, tech: dict) -> str:
    """Generate CI-only workflow — build + upload artifact. No deploy."""
    language: str = tech.get("language", "python")
    build_tool: str = tech.get("buildTool", "pip")
    lang_steps = _build_lang_steps(language, build_tool)

    artifact_paths: dict[str, str] = {
        "javascript": "dist/",
        "typescript": "dist/",
        "python": "app.zip",
        "java": "target/*.jar" if build_tool == "maven" else "build/libs/*.jar",
        "go": "main",
        "dotnet": "publish/",
    }
    artifact_path = artifact_paths.get(language, "dist/")

    workflow = {
        "name": "CI",
        "on": {
            "push": {"branches": [branch]},
            "pull_request": {"branches": [branch]},
        },
        "jobs": {
            "build": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    *lang_steps,
                    {
                        "name": "Upload artifact",
                        "uses": "actions/upload-artifact@v4",
                        "with": {"name": "build-artifact", "path": artifact_path, "retention-days": 7},
                    },
                ],
            }
        },
    }
    return yaml.dump(workflow, default_flow_style=False, sort_keys=False, allow_unicode=True)


async def _verify_repo_access(repo_full_name: str, branch: str, gh_token: str) -> str:
    """
    Verify the token has push access to the repo and the branch exists.
    Returns the resolved branch name.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        repo_res = await client.get(
            f"{_GITHUB_API}/repos/{repo_full_name}",
            headers=_auth_headers(gh_token),
        )
        if repo_res.status_code != 200:
            raise HTTPException(
                status_code=404,
                detail=f"Repository '{repo_full_name}' not found or inaccessible.",
            )
        repo_data = repo_res.json()
        permissions: dict = repo_data.get("permissions", {})
        if not permissions.get("push", False):
            raise HTTPException(
                status_code=403,
                detail=f"No write access to '{repo_full_name}'. Ensure you are the owner or have push permission.",
            )

        default_branch: str = repo_data.get("default_branch", "main")
        resolved_branch = branch if branch == default_branch else default_branch
        if resolved_branch != branch:
            logger.warning(
                "Requested branch '%s' differs from default '%s'; using default.",
                branch, default_branch,
            )

        branch_res = await client.get(
            f"{_GITHUB_API}/repos/{repo_full_name}/branches/{resolved_branch}",
            headers=_auth_headers(gh_token),
        )
        if branch_res.status_code != 200:
            raise HTTPException(
                status_code=404,
                detail=f"Branch '{resolved_branch}' not found in '{repo_full_name}'.",
            )

    return resolved_branch


async def _commit_file(
    repo_full_name: str,
    branch: str,
    path: str,
    content: str,
    commit_message: str,
    gh_token: str,
    max_retries: int = 3,
) -> dict:
    """Commit any file to the repo at the given path with SHA-conflict retry."""
    url = f"{_GITHUB_API}/repos/{repo_full_name}/contents/{path}"
    encoded = base64.b64encode(content.encode()).decode()

    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=20) as client:
            get_res = await client.get(url, headers=_auth_headers(gh_token), params={"ref": branch})
            body: dict = {"message": commit_message, "content": encoded, "branch": branch}
            if get_res.status_code == 200:
                body["sha"] = get_res.json().get("sha")
            put_res = await client.put(url, headers=_auth_headers(gh_token), json=body)

        if put_res.status_code in (200, 201):
            return put_res.json()
        if put_res.status_code == 409 and attempt < max_retries - 1:
            logger.warning("SHA conflict on attempt %d for %s, retrying…", attempt + 1, path)
            await asyncio.sleep(0.5)
            continue
        try:
            error_msg = put_res.json().get("message", put_res.text)
        except Exception:
            error_msg = put_res.text
        raise HTTPException(status_code=400, detail=f"GitHub API error committing {path}: {error_msg}")

    raise HTTPException(status_code=409, detail=f"File conflict after max retries for {path}.")


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.post("/preview", summary="Preview generated CI/CD pipeline YAML")
async def preview_pipeline(
    payload: PipelinePreviewRequest,
    gh_token: str | None = Cookie(default=None),
) -> dict[str, str]:
    """
    Generate and return a GitHub Actions YAML preview based on detected tech stack.
    Does NOT commit anything to the repository.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    safe_repo = payload.repoFullName.replace("\n", "").replace("\r", "")[:100]
    safe_branch = payload.branch.replace("\n", "").replace("\r", "")[:100]
    logger.info("Pipeline preview: repo=%s branch=%s", safe_repo, safe_branch)

    yaml_content = await _generate_pipeline_yaml(
        payload.repoFullName,
        payload.branch,
        payload.tech,
        payload.enableSast,
        payload.enableDast,
        gh_token,
    )
    return {"yaml": yaml_content}


@router.post("/create", summary="Generate and commit CI/CD pipeline to repository")
async def create_pipeline(
    payload: PipelineCreateRequest,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """
    Generate a GitHub Actions workflow YAML and commit it to `.github/workflows/ci.yml`.
    Optionally sets AZURE_CREDENTIALS secret if deploy config is provided.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    safe_repo = payload.repoFullName.replace("\n", "").replace("\r", "")[:100]
    safe_branch = payload.branch.replace("\n", "").replace("\r", "")[:100]
    logger.info("Pipeline create: repo=%s branch=%s", safe_repo, safe_branch)

    resolved_branch = await _verify_repo_access(payload.repoFullName, payload.branch, gh_token)

    yaml_content = await _generate_pipeline_yaml(
        payload.repoFullName,
        resolved_branch,
        payload.tech,
        payload.enableSast,
        payload.enableDast,
        gh_token,
        deploy=payload.deploy,
    )

    await _commit_file(
        payload.repoFullName, resolved_branch,
        ".github/workflows/ci.yml",
        yaml_content,
        "chore: add CI/CD pipeline via DevOps Agent",
        gh_token,
    )

    if payload.deploy:
        azure_creds = json.dumps({
            "clientId": os.getenv("AZURE_CLIENT_ID", ""),
            "clientSecret": os.getenv("AZURE_CLIENT_SECRET", ""),
            "tenantId": os.getenv("AZURE_TENANT_ID", ""),
            "subscriptionId": os.getenv("AZURE_SUBSCRIPTION_ID", ""),
        })
        await _set_github_secret(payload.repoFullName, "AZURE_CREDENTIALS", azure_creds, gh_token)

    return {
        "status": "created",
        "repo": payload.repoFullName,
        "branch": resolved_branch,
        "workflow_path": ".github/workflows/ci.yml",
        "secrets_configured": bool(payload.deploy),
    }


@router.get("/failed", summary="Get all failed pipeline runs with AI analysis")
async def get_failed_pipelines(
    days: int = 7,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """
    Return all failed pipeline runs across the user's repositories.
    Each failure includes AI-generated reason and resolution via Google Gemini.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    async with httpx.AsyncClient(timeout=20) as client:
        repos_res = await client.get(
            "https://api.github.com/user/repos?per_page=100&sort=updated",
            headers=_auth_headers(gh_token),
        )
    if repos_res.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch repositories from GitHub.")

    all_failed: list[dict] = []

    for repo in repos_res.json():
        repo_name: str = repo["full_name"]
        failed_runs = await get_failed_workflow_runs(repo_name, gh_token, days=days)

        for run in failed_runs:
            jobs = await get_workflow_run_jobs(repo_name, run["id"], gh_token)
            failed_job = next((j for j in jobs if j.get("conclusion") == "failure"), None)
            logs = await get_workflow_run_logs(repo_name, run["id"], gh_token)
            error_excerpt = extract_error_from_logs(logs or "")

            tech_stack: dict = {
                "language": "unknown", "framework": None,
                "buildTool": None, "hasDockerfile": False,
                "hasHelm": False, "hasTerraform": False,
            }
            try:
                from .analysis import TechDetectionRequest, tech_detection  # noqa: PLC0415
                detected = await tech_detection(
                    TechDetectionRequest(repoFullName=repo_name, branch=run.get("head_branch", "main")),
                    gh_token,
                )
                tech_stack = detected
            except Exception as exc:  # noqa: BLE001
                logger.debug("Tech detection skipped for %s: %s", repo_name, exc)

            ai_analysis: dict = await asyncio.to_thread(analyze_pipeline_error, error_excerpt, tech_stack)

            all_failed.append({
                "id": run["id"],
                "repo": repo_name,
                "workflow_name": run.get("name", "Unknown"),
                "branch": run.get("head_branch", "unknown"),
                "commit_sha": run.get("head_sha", "")[:7],
                "failed_at": run.get("updated_at", run.get("created_at")),
                "failed_job": failed_job.get("name") if failed_job else "Unknown",
                "error_excerpt": error_excerpt[:500],
                "ai_reason": ai_analysis.get("reason", ""),
                "ai_resolution": ai_analysis.get("resolution", ""),
                "run_url": run.get("html_url", ""),
            })

    all_failed.sort(key=lambda x: x["failed_at"], reverse=True)
    return {"failed_runs": all_failed, "total": len(all_failed)}


@router.get("/{repo_full_name:path}/runs/{run_id}/analyze",
            summary="AI analysis for a specific failed pipeline run")
async def analyze_pipeline_run(
    repo_full_name: str,
    run_id: int,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """
    Return detailed AI analysis for a specific failed pipeline run.
    Fetches logs, extracts errors, and calls Google Gemini for root cause analysis.
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    async with httpx.AsyncClient(timeout=20) as client:
        run_res = await client.get(
            f"{_GITHUB_API}/repos/{repo_full_name}/actions/runs/{run_id}",
            headers=_auth_headers(gh_token),
        )
    if run_res.status_code != 200:
        raise HTTPException(status_code=404, detail="Pipeline run not found.")

    run = run_res.json()
    if run.get("conclusion") != "failure":
        raise HTTPException(status_code=400, detail="This pipeline run did not fail.")

    logs = await get_workflow_run_logs(repo_full_name, run_id, gh_token)
    error_excerpt = extract_error_from_logs(logs or "")

    tech_stack: dict = {
        "language": "unknown", "framework": None,
        "buildTool": None, "hasDockerfile": False,
        "hasHelm": False, "hasTerraform": False,
    }
    ai_analysis: dict = await asyncio.to_thread(analyze_pipeline_error, error_excerpt, tech_stack)

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
