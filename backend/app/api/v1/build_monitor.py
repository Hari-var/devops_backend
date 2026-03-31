from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Set

from fastapi import APIRouter, Cookie, HTTPException, WebSocket, WebSocketDisconnect

from ...services.pipeline_monitor import get_workflow_runs, get_workflow_run_logs

router = APIRouter()
logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, repo_key: str):
        await websocket.accept()
        if repo_key not in self.active_connections:
            self.active_connections[repo_key] = set()
        self.active_connections[repo_key].add(websocket)

    def disconnect(self, websocket: WebSocket, repo_key: str):
        if repo_key in self.active_connections:
            self.active_connections[repo_key].discard(websocket)
            if not self.active_connections[repo_key]:
                del self.active_connections[repo_key]

    async def send_to_repo(self, repo_key: str, message: dict):
        if repo_key in self.active_connections:
            disconnected = []
            for connection in self.active_connections[repo_key]:
                try:
                    await connection.send_text(json.dumps(message))
                except:
                    disconnected.append(connection)
            for conn in disconnected:
                self.active_connections[repo_key].discard(conn)

manager = ConnectionManager()

@router.websocket("/ws/{repo_owner}/{repo_name}")
async def websocket_endpoint(websocket: WebSocket, repo_owner: str, repo_name: str):
    repo_key = f"{repo_owner}/{repo_name}"
    await manager.connect(websocket, repo_key)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, repo_key)

@router.get("/{repo_owner}/{repo_name}/status")
async def get_build_status(repo_owner: str, repo_name: str, gh_token: str | None = Cookie(default=None)):
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    repo_full_name = f"{repo_owner}/{repo_name}"
    runs = await get_workflow_runs(repo_full_name, gh_token, limit=10)
    if not runs:
        return {"status": "no_runs", "runs": []}
    processed_runs = []
    for run in runs:
        processed_runs.append({
            "id": run["id"],
            "status": run["status"],
            "conclusion": run.get("conclusion"),
            "workflow_name": run.get("name", "Unknown"),
            "branch": run.get("head_branch", "unknown"),
            "commit_sha": run.get("head_sha", "")[:7],
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "html_url": run.get("html_url")
        })
    latest_run = processed_runs[0]
    overall_status = "unknown"
    if latest_run["status"] == "in_progress":
        overall_status = "building"
    elif latest_run["status"] == "completed":
        if latest_run["conclusion"] == "success":
            overall_status = "success"
        elif latest_run["conclusion"] == "failure":
            overall_status = "failed"
        else:
            overall_status = "completed"
    elif latest_run["status"] == "queued":
        overall_status = "queued"
    return {"status": overall_status, "latest_run": latest_run, "runs": processed_runs}

@router.get("/{repo_owner}/{repo_name}/runs/{run_id}/logs")
async def get_run_logs(repo_owner: str, repo_name: str, run_id: int, gh_token: str | None = Cookie(default=None)):
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    repo_full_name = f"{repo_owner}/{repo_name}"
    logs = await get_workflow_run_logs(repo_full_name, run_id, gh_token)
    return {"logs": logs or "No logs available"}

async def monitor_repository_builds(repo_full_name: str, gh_token: str):
    last_run_id = None
    while repo_full_name in manager.active_connections:
        try:
            runs = await get_workflow_runs(repo_full_name, gh_token, limit=1)
            if runs:
                current_run = runs[0]
                current_run_id = current_run["id"]
                if current_run_id != last_run_id:
                    await manager.send_to_repo(repo_full_name, {
                        "type": "status_update",
                        "run": {
                            "id": current_run["id"],
                            "status": current_run["status"],
                            "conclusion": current_run.get("conclusion"),
                            "workflow_name": current_run.get("name", "Unknown"),
                            "branch": current_run.get("head_branch", "unknown"),
                            "commit_sha": current_run.get("head_sha", "")[:7],
                            "created_at": current_run.get("created_at"),
                            "updated_at": current_run.get("updated_at"),
                            "html_url": current_run.get("html_url")
                        }
                    })
                    last_run_id = current_run_id
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Error monitoring {repo_full_name}: {e}")
            await asyncio.sleep(30)

@router.post("/{repo_owner}/{repo_name}/monitor/start")
async def start_monitoring(repo_owner: str, repo_name: str, gh_token: str | None = Cookie(default=None)):
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    repo_full_name = f"{repo_owner}/{repo_name}"
    asyncio.create_task(monitor_repository_builds(repo_full_name, gh_token))
    return {"status": "monitoring_started", "repo": repo_full_name}
