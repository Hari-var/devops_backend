"""
Render deployment module - Stage 3 alternative for Render-based deployments.

This module provides functions to deploy applications directly to Render
using their REST API, bypassing GitHub Actions and Terraform.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

_RENDER_API = "https://api.render.com/v1"


def _get_render_headers() -> dict[str, str]:
    """Get Render API headers with authorization."""
    api_key = os.getenv("RENDER_API_KEY", "")
    if not api_key:
        raise ValueError("RENDER_API_KEY not set in environment")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _determine_service_type(tech: dict, cfg: dict) -> str:
    """
    Determine Render service type based on tech stack.
    
    Returns: "web_service", "static_site", "private_service", or "background_worker"
    """
    # Check if explicitly set in config
    service_type = cfg.get("RENDER_SERVICE_TYPE", "").lower()
    if service_type in ("web_service", "static_site", "private_service", "background_worker"):
        return service_type
    
    # Auto-detect from tech stack
    lang = tech.get("language", "").lower()
    framework = (tech.get("framework") or "").lower()
    
    # Static sites
    if lang in ("javascript", "typescript") and framework in ("react", "vue", "angular", "vite", "next"):
        return "static_site"
    
    # Web services (default for most backends)
    return "web_service"


def _build_command_for_tech(tech: dict) -> str:
    """Generate build command based on detected tech stack."""
    lang = tech.get("language", "").lower()
    build_tool = tech.get("buildTool", "").lower()
    framework = (tech.get("framework") or "").lower()
    
    if lang == "python":
        if build_tool == "poetry":
            return "poetry install && poetry build"
        return "pip install -r requirements.txt"
    
    if lang in ("javascript", "typescript"):
        if framework in ("react", "vue", "angular", "vite"):
            return "npm install && npm run build"
        return "npm install"
    
    if lang == "java":
        if build_tool == "maven":
            return "mvn clean package -DskipTests"
        return "./gradlew build -x test"
    
    if lang == "go":
        return "go build -o main ."
    
    if lang == "dotnet":
        return "dotnet publish -c Release -o out"
    
    return ""  # Render will auto-detect


def _start_command_for_tech(tech: dict) -> str:
    """Generate start command based on detected tech stack."""
    lang = tech.get("language", "").lower()
    framework = (tech.get("framework") or "").lower()
    build_tool = tech.get("buildTool", "").lower()
    
    if lang == "python":
        if framework in ("fastapi", "starlette"):
            return "uvicorn app:app --host 0.0.0.0 --port $PORT"
        if framework == "django":
            return "gunicorn app.wsgi:application --bind 0.0.0.0:$PORT"
        if framework == "flask":
            return "gunicorn app:app --bind 0.0.0.0:$PORT"
        return "python app.py"
    
    if lang in ("javascript", "typescript"):
        return "npm start"
    
    if lang == "java":
        if build_tool == "maven":
            return "java -jar target/*.jar"
        return "java -jar build/libs/*.jar"
    
    if lang == "go":
        return "./main"
    
    if lang == "dotnet":
        return "dotnet out/app.dll"
    
    return ""  # Render will auto-detect


def _get_runtime_for_tech(tech: dict) -> dict:
    """Get Render runtime configuration based on tech stack."""
    lang = tech.get("language", "").lower()
    
    runtime_map = {
        "python": "python",
        "javascript": "node",
        "typescript": "node",
        "java": "java",
        "go": "go",
        "dotnet": "dotnet",
        "ruby": "ruby",
    }
    
    return {"runtime": runtime_map.get(lang, "docker")}


async def create_render_service(
    repo: str,
    branch: str,
    tech: dict,
    cfg: dict,
    log: Callable[[str], Awaitable[None]],
) -> tuple[str, str, str]:
    """
    Create a new Render service via API.
    
    Args:
        repo: GitHub repo full name (e.g., "owner/repo")
        branch: Branch to deploy from
        tech: Detected tech stack
        cfg: Config from config.py
        log: Logging function
    
    Returns:
        Tuple of (service_url, service_id, deploy_id)
    """
    await log("Creating Render service...")
    
    # Get owner ID first
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            owner_res = await client.get(
                f"{_RENDER_API}/owners",
                headers=_get_render_headers(),
            )
        
        if owner_res.status_code != 200:
            raise RuntimeError(f"Failed to fetch Render owner: {owner_res.text}")
        
        owners = owner_res.json()
        if not owners:
            raise RuntimeError("No Render owners found. Please create a team/account on Render.")
        
        owner_id = owners[0].get("owner", {}).get("id")
        if not owner_id:
            raise RuntimeError("Could not extract owner ID from Render API response")
        
        await log(f"  Using Render owner ID: {owner_id}")
    
    except httpx.HTTPError as exc:
        await log(f"✗ HTTP error fetching owner: {exc}")
        raise
    
    app_name = str(cfg.get("APP_NAME", "devops-app"))
    region = str(cfg.get("REGION", "oregon"))
    plan = str(cfg.get("PLAN", "free"))
    service_type = _determine_service_type(tech, cfg)
    
    await log(f"  Service name: {app_name}")
    await log(f"  Service type: {service_type}")
    await log(f"  Region: {region}")
    await log(f"  Plan: {plan}")
    
    # Build service payload
    payload: dict = {
        "type": service_type,
        "name": app_name,
        "ownerId": owner_id,
        "repo": f"https://github.com/{repo}",
        "branch": branch,
        "autoDeploy": "yes",
        "region": region,
    }
    
    # Add service-specific configuration
    if service_type == "web_service":
        build_cmd = _build_command_for_tech(tech)
        start_cmd = _start_command_for_tech(tech)
        
        payload["serviceDetails"] = {
            "plan": plan,
            "env": "docker" if tech.get("hasDockerfile") else "native",
            "buildCommand": build_cmd,
            "startCommand": start_cmd,
            "healthCheckPath": "/",
        }
        
        # Add environment variables
        env_vars = []
        if "ENV_VARS" in cfg and isinstance(cfg["ENV_VARS"], dict):
            for key, value in cfg["ENV_VARS"].items():
                env_vars.append({"key": str(key), "value": str(value)})
        
        # Always add PORT
        env_vars.append({"key": "PORT", "value": "10000"})
        payload["envVars"] = env_vars
        
        await log(f"  Build command: {build_cmd or 'auto-detect'}")
        await log(f"  Start command: {start_cmd or 'auto-detect'}")
    
    elif service_type == "static_site":
        build_cmd = _build_command_for_tech(tech)
        publish_path = str(cfg.get("PUBLISH_PATH", "./dist"))
        
        payload["serviceDetails"] = {
            "buildCommand": build_cmd,
            "publishPath": publish_path,
        }
        
        await log(f"  Build command: {build_cmd}")
        await log(f"  Publish path: {publish_path}")
    
    # Create service
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{_RENDER_API}/services",
                headers=_get_render_headers(),
                json=payload,
            )
        await log(f"DEBUG RESPONSE: {response.text}")
        print("DEBUG RESPONSE:", response.json())
        
        if response.status_code not in (200, 201):
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("message", error_detail)
            except Exception:
                pass
            raise RuntimeError(f"Render API error ({response.status_code}): {error_detail}")
        
        service_data = response.json()
        service = service_data.get("service", service_data)
        deploy_id = service_data.get("deployId", "")

        service_id = service.get("id", "")

        if not service_id:
            raise RuntimeError("Service created but no ID returned from Render API")
        
        if not deploy_id:
            raise RuntimeError("Service created but no deploy ID returned from Render API")

        service_url = (service.get("serviceDetails") or {}).get("url", "")
        
        if not service_url:
            await log("  Service URL not immediately available, fetching from API...")
            await asyncio.sleep(3)
            
            async with httpx.AsyncClient(timeout=15) as client2:
                svc_res = await client2.get(
                    f"{_RENDER_API}/services/{service_id}",
                    headers=_get_render_headers(),
                )
            
            if svc_res.status_code == 200:
                svc_data = svc_res.json()
                service_url = svc_data.get("serviceDetails", {}).get("url", "")
            
            if not service_url:
                raise RuntimeError("Service created but URL not available. Check Render dashboard.")
        
        await log(f"✓ Service created: {service_id}")
        await log(f"✓ Deploy ID: {deploy_id}")
        await log(f"✓ Service URL: {service_url}")
        
        return service_url, service_id, deploy_id
    
    except httpx.HTTPError as exc:
        await log(f"✗ HTTP error creating service: {exc}")
        raise
    except Exception as exc:
        await log(f"✗ Error creating service: {exc}")
        raise


async def monitor_render_deployment(
    d_id: str,
    s_id: str,
    log: Callable[[str], Awaitable[None]],
    timeout_minutes: int = 15,
) -> str:
    """
    Monitor Render deployment status until completion.
    
    Args:
        deploy_id: Render deploy ID
        service_id: Render service ID
        log: Logging function
        timeout_minutes: Max time to wait for deployment
    
    Returns:
        Deployment status ("live", "failed", "timeout")
    """
    await log("Monitoring Render deployment...")
    
    headers = _get_render_headers()
    max_attempts = timeout_minutes * 6
    
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{_RENDER_API}/services/{s_id}/deploys?limit=1",
                    headers=headers,
                )
            
            await log(f"  Debug response status: {response.status_code}")
            await log(f"  Debug response: {response.text[:500]}")
            
            if response.status_code != 200:
                await log(f"  [{attempt + 1:02d}] Failed to fetch deploy status (HTTP {response.status_code})")
                await asyncio.sleep(10)
                continue
            
            try:
                deploys_data = response.json()
            except Exception as json_err:
                await log(f"  [{attempt + 1:02d}] JSON parse error: {json_err}")
                await log(f"  Response text: {response.text[:200]}")
                await asyncio.sleep(10)
                continue
            
            # Find the specific deploy by ID
            deploy = None
            if isinstance(deploys_data, list) and deploys_data:
                deploy = deploys_data[0].get("deploy")
            
            if not deploy:
                await log(f"  [{attempt + 1:02d}] Deploy not found")
                await asyncio.sleep(10)
                continue
            
            status = deploy.get("status", "unknown")
            
            await log(f"  [{attempt + 1:02d}] Deploy status: {status.upper()}")
            
            if status == "live":
                await log("✓ Deployment successful!")
                return "live"
            
            if status in ("build_failed", "deploy_failed", "canceled"):
                await log(f"✗ Deployment failed with status: {status}")
                return "failed"
            
            await asyncio.sleep(10)
        
        except Exception as exc:
            await log(f"  Error polling deployment: {exc}")
            await asyncio.sleep(10)
    
    await log("✗ Deployment timeout - service may still be deploying")
    await log("  Check Render dashboard for status")
    return "timeout"


async def get_render_service_logs(
    service_id: str,
    log: Callable[[str], Awaitable[None]],
    tail: int = 100,
) -> None:
    """
    Fetch and display recent service logs from Render.
    
    Args:
        service_id: Render service ID
        log: Logging function
        tail: Number of log lines to fetch
    """
    await log("Fetching service logs...")
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{_RENDER_API}/services/{service_id}/logs",
                headers=_get_render_headers(),
                params={"tail": tail},
            )
        
        if response.status_code != 200:
            await log("  Could not fetch logs")
            return
        
        logs_data = response.json()
        for log_entry in logs_data:
            timestamp = log_entry.get("timestamp", "")
            message = log_entry.get("message", "")
            await log(f"  [{timestamp}] {message}")
    
    except Exception as exc:
        await log(f"  Error fetching logs: {exc}")


async def delete_render_service(
    service_id: str,
    log: Callable[[str], Awaitable[None]],
) -> bool:
    """
    Delete a Render service (useful for cleanup/rollback).
    
    Args:
        service_id: Render service ID
        log: Logging function
    
    Returns:
        True if deleted successfully
    """
    await log(f"Deleting Render service {service_id}...")
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.delete(
                f"{_RENDER_API}/services/{service_id}",
                headers=_get_render_headers(),
            )
        
        if response.status_code in (200, 204):
            await log("✓ Service deleted")
            return True
        
        await log(f"✗ Failed to delete service: {response.status_code}")
        return False
    
    except Exception as exc:
        await log(f"✗ Error deleting service: {exc}")
        return False
