from __future__ import annotations

<<<<<<< HEAD
<<<<<<< HEAD
=======
from fastapi import APIRouter, Cookie, HTTPException, WebSocket, WebSocketDisconnect
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
=======
from fastapi import APIRouter, Cookie, HTTPException, WebSocket, WebSocketDisconnect
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
import asyncio
import json
import logging
from typing import Dict, Set
<<<<<<< HEAD
<<<<<<< HEAD

from fastapi import APIRouter, Cookie, HTTPException, WebSocket, WebSocketDisconnect

from app.services.pipeline_monitor import get_workflow_run_logs, get_workflow_runs
=======
from app.services.pipeline_monitor import get_workflow_runs, get_workflow_run_logs
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
=======
from app.services.pipeline_monitor import get_workflow_runs, get_workflow_run_logs
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374

router = APIRouter()
logger = logging.getLogger(__name__)

<<<<<<< HEAD
<<<<<<< HEAD
_RUN_STATUS_MAP: dict[str, str] = {
    "in_progress": "building",
    "queued": "queued",
}


class ConnectionManager:
    """Manages active WebSocket connections grouped by repository key."""

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, repo_key: str) -> None:
        await websocket.accept()
        self._connections.setdefault(repo_key, set()).add(websocket)

    def disconnect(self, websocket: WebSocket, repo_key: str) -> None:
        if repo_key in self._connections:
            self._connections[repo_key].discard(websocket)
            if not self._connections[repo_key]:
                del self._connections[repo_key]

    def has_connections(self, repo_key: str) -> bool:
        return bool(self._connections.get(repo_key))

    async def broadcast(self, repo_key: str, message: dict) -> None:
        """Send a JSON message to all connections for a repo; prune stale ones."""
        if repo_key not in self._connections:
            return
        stale: list[WebSocket] = []
        for ws in list(self._connections[repo_key]):
            try:
                await ws.send_text(json.dumps(message))
            except Exception:  # noqa: BLE001
                stale.append(ws)
        for ws in stale:
            self._connections[repo_key].discard(ws)


manager = ConnectionManager()


def _format_run(run: dict) -> dict:
    """Extract and normalise fields from a raw GitHub workflow run dict."""
    return {
        "id": run["id"],
        "status": run["status"],
        "conclusion": run.get("conclusion"),
        "workflow_name": run.get("name", "Unknown"),
        "branch": run.get("head_branch", "unknown"),
        "commit_sha": run.get("head_sha", "")[:7],
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "html_url": run.get("html_url"),
    }


def _overall_status(run: dict) -> str:
    raw = run.get("status", "")
    if raw in _RUN_STATUS_MAP:
        return _RUN_STATUS_MAP[raw]
    if raw == "completed":
        conclusion = run.get("conclusion", "")
        if conclusion == "success":
            return "success"
        if conclusion == "failure":
            return "failed"
        return "completed"
    return "unknown"


@router.websocket("/ws/{repo_owner}/{repo_name}")
async def websocket_endpoint(
    websocket: WebSocket, repo_owner: str, repo_name: str
) -> None:
    """Accept and maintain a WebSocket connection for a repository."""
    repo_key = f"{repo_owner}/{repo_name}"
    await manager.connect(websocket, repo_key)
    try:
        while True:
=======
=======
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
# Store active WebSocket connections
active_connections: Dict[str, Set[WebSocket]] = {}

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
            
            # Clean up disconnected connections
            for conn in disconnected:
                self.active_connections[repo_key].discard(conn)

manager = ConnectionManager()

@router.websocket("/ws/{repo_owner}/{repo_name}")
async def websocket_endpoint(websocket: WebSocket, repo_owner: str, repo_name: str):
    repo_key = f"{repo_owner}/{repo_name}"
    await manager.connect(websocket, repo_key)
    
    try:
        while True:
            # Keep connection alive and listen for client messages
<<<<<<< HEAD
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
=======
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, repo_key)

<<<<<<< HEAD
<<<<<<< HEAD

@router.get("/{repo_owner}/{repo_name}/status")
async def get_build_status(
    repo_owner: str,
    repo_name: str,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """Return the current build status for a repository."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    repo_full_name = f"{repo_owner}/{repo_name}"
    runs = await get_workflow_runs(repo_full_name, gh_token, limit=10)

    if not runs:
        return {"status": "no_runs", "runs": []}

    processed = [_format_run(r) for r in runs]
    return {
        "status": _overall_status(runs[0]),
        "latest_run": processed[0],
        "runs": processed,
    }


@router.get("/{repo_owner}/{repo_name}/runs/{run_id}/logs")
async def get_run_logs(
    repo_owner: str,
    repo_name: str,
    run_id: int,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """Return logs for a specific workflow run."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    logs = await get_workflow_run_logs(f"{repo_owner}/{repo_name}", run_id, gh_token)
    return {"logs": logs or "No logs available."}


async def _monitor_repository_builds(repo_full_name: str, gh_token: str) -> None:
    """Background task: poll GitHub every 10 s and broadcast status changes."""
    last_run_id: int | None = None

    while manager.has_connections(repo_full_name):
        try:
            runs = await get_workflow_runs(repo_full_name, gh_token, limit=1)
            if runs:
                current = runs[0]
                if current["id"] != last_run_id:
                    await manager.broadcast(
                        repo_full_name,
                        {"type": "status_update", "run": _format_run(current)},
                    )
                    last_run_id = current["id"]
            await asyncio.sleep(10)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error monitoring %s: %s", repo_full_name, exc)
            await asyncio.sleep(30)

=======
=======
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
@router.get("/{repo_owner}/{repo_name}/status")
async def get_build_status(
    repo_owner: str, 
    repo_name: str, 
    gh_token: str | None = Cookie(default=None)
):
    """Get current build status for a repository"""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    repo_full_name = f"{repo_owner}/{repo_name}"
    
    # Get latest workflow runs
    runs = await get_workflow_runs(repo_full_name, gh_token, limit=10)
    
    if not runs:
        return {"status": "no_runs", "runs": []}
    
    # Process runs for status
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
    
    # Determine overall status
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
    
    return {
        "status": overall_status,
        "latest_run": latest_run,
        "runs": processed_runs
    }

@router.get("/{repo_owner}/{repo_name}/runs/{run_id}/logs")
async def get_run_logs(
    repo_owner: str,
    repo_name: str, 
    run_id: int,
    gh_token: str | None = Cookie(default=None)
):
    """Get logs for a specific workflow run"""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    repo_full_name = f"{repo_owner}/{repo_name}"
    logs = await get_workflow_run_logs(repo_full_name, run_id, gh_token)
    
    return {"logs": logs or "No logs available"}

async def monitor_repository_builds(repo_full_name: str, gh_token: str):
    """Background task to monitor builds for a repository"""
    last_run_id = None
    
    while repo_full_name in manager.active_connections:
        try:
            runs = await get_workflow_runs(repo_full_name, gh_token, limit=1)
            
            if runs:
                current_run = runs[0]
                current_run_id = current_run["id"]
                
                # If this is a new run or status changed
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
            
            await asyncio.sleep(10)  # Check every 10 seconds
            
        except Exception as e:
            logger.error(f"Error monitoring {repo_full_name}: {e}")
            await asyncio.sleep(30)  # Wait longer on error
<<<<<<< HEAD
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
=======
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374

@router.post("/{repo_owner}/{repo_name}/monitor/start")
async def start_monitoring(
    repo_owner: str,
    repo_name: str,
<<<<<<< HEAD
<<<<<<< HEAD
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """Start a background polling task for a repository's builds."""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    repo_full_name = f"{repo_owner}/{repo_name}"
    asyncio.create_task(_monitor_repository_builds(repo_full_name, gh_token))
    return {"status": "monitoring_started", "repo": repo_full_name}
=======
=======
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
    gh_token: str | None = Cookie(default=None)
):
    """Start monitoring builds for a repository"""
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    repo_full_name = f"{repo_owner}/{repo_name}"
    
    # Start background monitoring task
    asyncio.create_task(monitor_repository_builds(repo_full_name, gh_token))
    
<<<<<<< HEAD
    return {"status": "monitoring_started", "repo": repo_full_name}
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
=======
    return {"status": "monitoring_started", "repo": repo_full_name}
>>>>>>> 3a7c3ddc753b8fc8e40879fb1da83561691d7374
