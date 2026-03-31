from __future__ import annotations

import base64
import logging

import httpx
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class TechDetectionRequest(BaseModel):
    repoFullName: str
    branch: str


def _auth_headers(gh_token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {gh_token}"}


async def _get_file_content(repo: str, branch: str, path: str, gh_token: str) -> str | None:
    """Fetch and decode a single file from the GitHub Contents API."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(url, headers=_auth_headers(gh_token), params={"ref": branch})
    if res.status_code != 200:
        return None
    data = res.json()
    if data.get("type") == "file" and "content" in data:
        return base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    return None


async def _list_dir(repo: str, branch: str, path: str, gh_token: str) -> list[dict]:
    """List items in a repository directory."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(url, headers=_auth_headers(gh_token), params={"ref": branch})
    if res.status_code != 200:
        return []
    items = res.json()
    return items if isinstance(items, list) else []


async def _collect_all_files(repo: str, branch: str, gh_token: str, max_depth: int = 2) -> set[str]:
    """
    Recursively collect all file paths up to max_depth levels deep.
    Returns a set of lowercase relative paths.
    Skips common non-source directories.
    """
    _SKIP_DIRS = {"node_modules", "vendor", ".git", ".github", "__pycache__", ".venv", "venv", "dist", "build"}
    collected: set[str] = set()

    async def _recurse(path: str, depth: int) -> None:
        if depth < 0:
            return
        items = await _list_dir(repo, branch, path, gh_token)
        for item in items:
            item_path: str = item.get("path", "")
            item_name: str = item.get("name", "")
            item_type: str = item.get("type", "")
            if item_name in _SKIP_DIRS:
                continue
            if item_type == "file":
                collected.add(item_path.lower())
            elif item_type == "dir":
                await _recurse(item_path, depth - 1)

    await _recurse("", max_depth)
    return collected


def _detect_framework_from_package_json(content: str) -> str | None:
    checks = [
        ('"next"', "nextjs"),
        ('"react"', "react"),
        ('"react-dom"', "react"),
        ('"vue"', "vue"),
        ('"@angular/core"', "angular"),
        ('"express"', "express"),
    ]
    for marker, framework in checks:
        if marker in content:
            return framework
    return None


def _detect_framework_from_pyproject(content: str) -> str | None:
    lower = content.lower()
    if "fastapi" in lower:
        return "fastapi"
    if "flask" in lower:
        return "flask"
    if "django" in lower:
        return "django"
    return None


@router.post("/tech-detection", summary="Detect technology stack of a GitHub repository")
async def tech_detection(
    payload: TechDetectionRequest,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """
    Scan a GitHub repository and detect its technology stack.

    Returns:
    - language: primary programming language
    - framework: detected framework (react, fastapi, spring-boot, etc.)
    - buildTool: build tool (npm, poetry, pip, maven, gradle, etc.)
    - hasDockerfile: whether a Dockerfile exists
    - hasHelm: whether Helm charts exist
    - hasTerraform: whether Terraform files exist
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    repo = payload.repoFullName
    branch = payload.branch

    result: dict[str, str | bool | None] = {
        "language": None,
        "framework": None,
        "buildTool": None,
        "hasDockerfile": False,
        "hasHelm": False,
        "hasTerraform": False,
    }

    all_files = await _collect_all_files(repo, branch, gh_token, max_depth=2)

    # Language & build tool detection (priority order)
    if any(f.endswith("package.json") and "node_modules" not in f for f in all_files):
        result["language"] = "javascript"
        result["buildTool"] = "npm"
        for f in all_files:
            if f.endswith("package.json") and "node_modules" not in f:
                content = await _get_file_content(repo, branch, f, gh_token)
                if content:
                    result["framework"] = _detect_framework_from_package_json(content)
                break

    elif any(f.endswith("pyproject.toml") for f in all_files):
        result["language"] = "python"
        result["buildTool"] = "poetry"
        for f in all_files:
            if f.endswith("pyproject.toml"):
                content = await _get_file_content(repo, branch, f, gh_token)
                if content:
                    result["framework"] = _detect_framework_from_pyproject(content)
                break

    elif any(f.endswith("requirements.txt") for f in all_files):
        result["language"] = "python"
        result["buildTool"] = "pip"

    elif any(f.endswith("pom.xml") for f in all_files):
        result["language"] = "java"
        result["buildTool"] = "maven"
        for f in all_files:
            if f.endswith("pom.xml"):
                content = await _get_file_content(repo, branch, f, gh_token)
                if content and "spring-boot" in content.lower():
                    result["framework"] = "spring-boot"
                break

    elif any(f.endswith("build.gradle") or f.endswith("build.gradle.kts") for f in all_files):
        result["language"] = "java"
        result["buildTool"] = "gradle"

    elif any(f.endswith("go.mod") for f in all_files):
        result["language"] = "go"
        result["buildTool"] = "go"

    elif any(f.endswith("cargo.toml") for f in all_files):
        result["language"] = "rust"
        result["buildTool"] = "cargo"

    elif any(f.endswith("gemfile") for f in all_files):
        result["language"] = "ruby"
        result["buildTool"] = "bundler"

    elif any(f.endswith(".csproj") for f in all_files):
        result["language"] = "csharp"
        result["buildTool"] = "dotnet"

    if not result["language"]:
        result["language"] = "python"
        result["buildTool"] = "pip"

    # Infrastructure file detection
    result["hasDockerfile"] = any(
        f.split("/")[-1] == "dockerfile" for f in all_files
    )
    result["hasHelm"] = any(
        "charts/" in f or "helm/" in f or f.endswith("chart.yaml") for f in all_files
    )
    result["hasTerraform"] = any(
        f.endswith(".tf") or f.endswith(".tfvars") for f in all_files
    )

    safe_repo = repo.replace("\n", "").replace("\r", "")[:100]
    safe_branch = branch.replace("\n", "").replace("\r", "")[:100]
    logger.info("Tech detection for %s@%s: %s", safe_repo, safe_branch, result)
    return result
