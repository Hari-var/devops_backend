"""
Example: How to integrate Render deployment into the approval pipeline.

This file shows how to modify _run_pipeline() in approvals.py to support
Render deployments when DEPLOY_TARGET = "render" in config.py.

DO NOT USE THIS FILE DIRECTLY - it's just a reference implementation.
"""

# ============================================================================
# EXAMPLE INTEGRATION (for reference only)
# ============================================================================

async def _run_pipeline_with_render_support(approval_id: str, gh_token: str) -> None:
    """
    Modified version of _run_pipeline that supports both Azure and Render deployments.
    
    This is an EXAMPLE showing how to integrate render_deploy.py.
    """
    # ... existing code for fetching approval record ...
    
    deploy_target = str(cfg.get("DEPLOY_TARGET", "app_service")).lower()
    
    # ── STAGE 1: Tech Detection (unchanged) ─────────────────────────────────
    # ... existing tech detection code ...
    
    # ── STAGE 2: Infrastructure Provisioning ────────────────────────────────
    await _set_stage(2)
    await log("Starting infrastructure provisioning...", 2)
    
    if deploy_target == "render":
        # RENDER DEPLOYMENT PATH
        from .render_deploy import create_render_service, monitor_render_deployment
        
        await log("Using Render for deployment", 2)
        await log(f"App name: {cfg.get('APP_NAME', 'devops-app')}", 2)
        await log(f"Region: {cfg.get('REGION', 'oregon')}", 2)
        await log(f"Plan: {cfg.get('PLAN', 'free')}", 2)
        
        # Create Render service
        deployed_url, service_id = await create_render_service(
            repo=repo,
            branch=resolved_branch,
            tech=tech,
            cfg=cfg,
            log=lambda m: log(m, 2),
        )
        
        # Store service ID for later monitoring
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Approval).where(Approval.id == approval_id))
            rec = r.scalar_one_or_none()
            if rec:
                rec.terraform_url = deployed_url
                # Store service_id in a new field or in config
                await db.commit()
        
        await log(f"Provisioned URL: {deployed_url}", 2)
        await log("Render service created successfully", 2)
        
    else:
        # EXISTING AZURE/TERRAFORM PATH
        await log(f"Deploy target: {cfg.get('DEPLOY_TARGET', 'app_service')}", 2)
        # ... existing terraform code ...
    
    await _push_stage_event(approval_id, 2, "info", "Infrastructure provisioning complete")
    
    # ── STAGE 3: CI/CD Pipeline Generation (conditional) ───────────────────
    await _set_stage(3)
    
    if deploy_target == "render":
        # RENDER: Skip GitHub Actions YAML generation
        await log("Skipping CI/CD YAML generation (Render auto-deploys)", 3)
        await log("Render will automatically deploy from GitHub on push", 3)
        
    else:
        # EXISTING: Generate GitHub Actions YAML
        await log("Generating CI/CD pipeline YAML...", 3)
        # ... existing YAML generation code ...
    
    await log("CI/CD configuration complete.", 3)
    
    # ── STAGE 4: Monitor Deployment ─────────────────────────────────────────
    await _set_stage(4)
    
    if deploy_target == "render":
        # RENDER: Monitor via Render API
        from .render_deploy import monitor_render_deployment
        
        await log("Monitoring Render deployment...", 4)
        status = await monitor_render_deployment(
            service_id=service_id,
            log=lambda m: log(m, 4),
            timeout_minutes=15,
        )
        
        if status != "live":
            raise RuntimeError(f"Render deployment failed with status: {status}")
        
        await log("Render deployment complete", 4)
        run_url = f"https://dashboard.render.com/web/{service_id}"
        
    else:
        # EXISTING: Monitor GitHub Actions
        await log("Waiting for GitHub Actions workflow to start...", 4)
        # ... existing GitHub Actions monitoring code ...
    
    # ── DONE ─────────────────────────────────────────────────────────────────
    await _set_stage(5, status="done",
                     deployed_url=deployed_url,
                     actions_run_url=run_url or None)
    # ... existing completion code ...


# ============================================================================
# CONFIG.PY EXAMPLES
# ============================================================================

# Example 1: Render Web Service (Python FastAPI)
"""
DEPLOY_TARGET = "render"
APP_NAME = "my-fastapi-app"
REGION = "oregon"  # oregon, frankfurt, singapore, ohio
PLAN = "free"  # free, starter, standard, pro

# Optional: Custom environment variables
ENV_VARS = {
    "DATABASE_URL": "postgresql://...",
    "SECRET_KEY": "your-secret-key",
    "DEBUG": "false",
}
"""

# Example 2: Render Static Site (React)
"""
DEPLOY_TARGET = "render"
APP_NAME = "my-react-app"
RENDER_SERVICE_TYPE = "static_site"  # Force static site
PUBLISH_PATH = "./build"  # or "./dist" for Vite
REGION = "oregon"
"""

# Example 3: Render with Custom Commands
"""
DEPLOY_TARGET = "render"
APP_NAME = "custom-app"
PLAN = "starter"

# Render will auto-detect, but you can override in render_deploy.py
# by checking cfg.get("BUILD_COMMAND") and cfg.get("START_COMMAND")
"""


# ============================================================================
# ENVIRONMENT VARIABLES NEEDED
# ============================================================================

"""
Add to your .env file:

# Render API Key (get from https://dashboard.render.com/u/settings)
RENDER_API_KEY=rnd_xxxxxxxxxxxxxxxxxxxxx

# Existing variables (keep these)
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxxxx
GITHUB_CLIENT_ID=xxxxx
GITHUB_CLIENT_SECRET=xxxxx
# ... other vars ...
"""


# ============================================================================
# RENDER API KEY SETUP
# ============================================================================

"""
1. Go to https://dashboard.render.com/u/settings
2. Scroll to "API Keys" section
3. Click "Create API Key"
4. Name it "DevOps Agent"
5. Copy the key (starts with "rnd_")
6. Add to .env: RENDER_API_KEY=rnd_xxxxxxxxxxxxxxxxxxxxx
7. Restart your backend
"""


# ============================================================================
# TESTING THE INTEGRATION
# ============================================================================

"""
1. Create a test repo with config.py:
   
   DEPLOY_TARGET = "render"
   APP_NAME = "test-app-123"
   REGION = "oregon"
   PLAN = "free"

2. Push to GitHub

3. Backend poller will detect it

4. Approve in UI

5. Pipeline will:
   - Stage 1: Detect tech (Python/Node/etc)
   - Stage 2: Create Render service via API
   - Stage 3: Skip (Render auto-deploys)
   - Stage 4: Monitor deployment status
   - Done: Show deployed URL

6. Check https://dashboard.render.com to see your service
"""


# ============================================================================
# ADVANTAGES OF RENDER DEPLOYMENT
# ============================================================================

"""
✅ Simpler: No Terraform, no GitHub Actions YAML
✅ Faster: Direct API calls, auto-deploy from GitHub
✅ Cheaper: Free tier available (vs Azure costs)
✅ Automatic: HTTPS, scaling, monitoring included
✅ Less config: Auto-detects build/start commands
✅ Better DX: Render dashboard for logs/metrics

Render handles:
- SSL certificates (automatic HTTPS)
- Load balancing
- Auto-scaling
- Health checks
- Log aggregation
- Metrics & monitoring
- Zero-downtime deploys
"""


# ============================================================================
# LIMITATIONS TO CONSIDER
# ============================================================================

"""
⚠️ Render-specific: Locked into Render platform
⚠️ Less control: Can't customize infrastructure as much
⚠️ Region limits: Only 4 regions (vs Azure's global presence)
⚠️ Free tier limits: 
   - Services spin down after 15 min inactivity
   - 750 hours/month free
   - Limited resources
⚠️ No VPC: Free tier doesn't support private networking
"""
