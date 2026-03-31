import re
import requests
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from json import loads
import json
from .sql import save_approval, Repo_response
from ...db import AsyncSessionLocal, get_db
router = APIRouter()
from typing import Annotated
db_dependency = Annotated[AsyncSession, Depends(get_db)]

def check_config(commits):
    pattern = re.compile(r'.*config.*\.py$')

    for commit in commits:
        for file in commit.get("modified", []):
            if pattern.match(file):
                return file  # return matching file path
    return None
import re
import os
import httpx
from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter()

def text_to_json(text):
    """
    Convert Python-style variable assignments in a string to JSON.
    
    Example input:
    TENANT_ID = "abc"
    ENABLE_SAST = True
    """
    config = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue  # skip empty lines and comments
        if "=" not in line:
            continue  # skip invalid lines
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        
        # Convert string values
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        # Convert booleans
        elif value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        # Convert numbers
        else:
            try:
                if "." in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass  # leave as string if cannot parse
        
        config[key] = value
    
    return json.dumps(config, indent=4)

_CONFIG_PATTERN = re.compile(r'.*config.*\.py$')

def _find_config_file(commits: list) :
    changed_files = [file for commit in commits for file in commit.get("modified", []) + commit.get("added", [])]

    # iterate over all changed files to find a config file
    for file in changed_files:
        if _CONFIG_PATTERN.search(file):
            return file, changed_files
    return None


@router.post("/run_flow2")
async def run_flow2(request: Request):
    data = await request.json()
    commits = data.get("commits", [])
    repo_name = data.get("repository", {}).get("full_name", "")
    ref = data.get("ref", "").replace("refs/heads/", "")
    head_commit = data.get("head_commit", {})

    config_file, changed_files = _find_config_file(commits)
    if not config_file:
        return {"message": "No config file found"}

    raw_url = f"https://raw.githubusercontent.com/{repo_name}/{ref}/{config_file}"

    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(raw_url)

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Failed to fetch config file: {response.status_code}")
    payload = Repo_response(
        repo=repo_name,
        branch=ref,
        status="pending",
        commit_sha=head_commit.get("id", ""),
        commit_message=head_commit.get("message", ""),
        committed_at=head_commit.get("timestamp", ""),
        committed_by=head_commit.get("pusher", {}).get("name", ""),
        changed_files=changed_files,
        config= loads(text_to_json(response.text)),
    )
    # payload = {
    #     "repo": repo_name,
    #     "branch": ref,
    #     "status": "pending",
    #     "commit_sha": head_commit.get("id", ""),
    #     "commit_message": head_commit.get("message", ""),
    #     "committed_at": head_commit.get("timestamp", ""),
    #     "committed_by": head_commit.get("pusher", {}).get("name", ""),
    #     "changed_files": changed_files,
    #     "config": text_to_json(response.text),
    # }
    await save_approval(payload)
    # config_data = loads(response.text)
    return {
        "message": "Config file found",
        "payload": payload,
    }


@router.post("/run_flow")
def run_flow(data):  # FastAPI already parses JSON
    commits = data.get("commits", [])

    repo_name = data.get("repository", {}).get("full_name", "")
    ref = data.get("ref", "").replace("refs/heads/", "")

    checker = check_config(commits)

    # Safely get commit info
    head_commit = data.get("head_commit", {})
    commit_id = head_commit.get("id", "")
    commit_message = head_commit.get("message", "")
    commit_timestamp = head_commit.get("timestamp", "")

    if not checker:
        return {"message": "No config file found"}

    # Build correct raw GitHub URL
    raw_git = f"https://raw.githubusercontent.com/{repo_name}/{ref}/{checker}"


    response = requests.get(raw_git)
    print(response)

    # if response.status_code != 200:
    #     return {"error": "Failed to fetch file"}

    file_content = response.text

    return {
        "message": "Config file found",
        "file_path": checker,
        "file_content": file_content
    }

if __name__ == "__main__":
    url=input("Enter the URL: ")
    response = requests.get(url)
    print(text_to_json(response.text))