"""Pipeline monitoring service — fetches and analyses GitHub Actions workflow runs."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _auth_headers(gh_token: str) -> dict[str, str]:
    return {**_GITHUB_HEADERS, "Authorization": f"Bearer {gh_token}"}


async def get_workflow_runs(
    repo_full_name: str,
    gh_token: str,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Fetch GitHub Actions workflow runs for a repository."""
    params: dict[str, str | int] = {"per_page": min(limit, 100)}
    if status:
        params["status"] = status

    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/actions/runs",
            headers=_auth_headers(gh_token),
            params=params,
        )

    if res.status_code != 200:
        logger.warning("Failed to fetch runs for %s: status=%d", repo_full_name, res.status_code)
        return []

    return res.json().get("workflow_runs", [])


async def get_failed_workflow_runs(
    repo_full_name: str,
    gh_token: str,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """Return failed workflow runs within the last ``days`` days."""
    all_runs = await get_workflow_runs(repo_full_name, gh_token, status="completed", limit=limit)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    failed: list[dict] = []
    for run in all_runs:
        if run.get("conclusion") != "failure":
            continue
        created_raw: str = run.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse created_at '%s' for run %s", created_raw, run.get("id"))
            continue
        if created_at >= cutoff:
            failed.append(run)

    return failed


async def get_workflow_run_logs(
    repo_full_name: str,
    run_id: int,
    gh_token: str,
) -> str | None:
    """
    Download and extract the ZIP log archive for a workflow run.
    Returns concatenated text content of all .txt files, or None on failure.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}/logs"

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(url, headers=_auth_headers(gh_token), follow_redirects=True)

    if res.status_code != 200:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            texts = [
                zf.open(name).read().decode("utf-8", errors="replace")
                for name in zf.namelist()
                if name.endswith(".txt")
            ]
        return "\n\n".join(texts) if texts else None
    except zipfile.BadZipFile:
        logger.warning("Log archive for run %d is not a valid ZIP", run_id)
        return None


async def get_workflow_run_jobs(
    repo_full_name: str,
    run_id: int,
    gh_token: str,
) -> list[dict]:
    """Return the jobs list for a workflow run."""
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}/jobs",
            headers=_auth_headers(gh_token),
        )

    if res.status_code != 200:
        return []

    return res.json().get("jobs", [])


def extract_error_from_logs(logs: str, max_length: int = 2_000) -> str:
    """
    Extract the most relevant error lines from workflow logs.
    Prioritises lines containing error indicators and their surrounding context.
    """
    if not logs:
        return "No logs available."

    lines = logs.splitlines()
    error_indicators = {"error", "failed", "failure", "exception", "traceback"}
    collected: list[str] = []

    for idx, line in enumerate(lines):
        if any(indicator in line.lower() for indicator in error_indicators):
            start = max(0, idx - 5)
            end = min(len(lines), idx + 10)
            collected.extend(lines[start:end])

    excerpt = "\n".join(collected[-50:]) if collected else "\n".join(lines[-100:])

    if len(excerpt) > max_length:
        excerpt = excerpt[-max_length:]

    return excerpt
