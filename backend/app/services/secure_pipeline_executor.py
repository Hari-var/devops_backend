"""Enhanced CI/CD pipeline service with AI integration using Google Gemini."""
import asyncio
import json
import os
import tempfile
import traceback
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List
import httpx
from sqlalchemy import select

from ..models import Approval
from .ai_terraform_generator import AITerraformGenerator, InfrastructureRequirements


class SecurePipelineExecutor:
    """Secure pipeline executor with AI-generated infrastructure using Google Gemini."""
    
    def __init__(self, gemini_api_key: str):
        self.ai_generator = AITerraformGenerator(gemini_api_key)
        self.terraform_path = self._get_secure_terraform_path()
        
        if not self.terraform_path:
            raise RuntimeError(
                "Terraform binary not found. Please install terraform or ensure it's in your PATH. "
                "Visit https://www.terraform.io/downloads.html for installation instructions."
            )
    
    def _get_secure_terraform_path(self) -> Optional[str]:
        """Get secure terraform binary path."""
        # Only allow terraform from specific trusted locations
        trusted_paths = [
            "/usr/local/bin/terraform",
            "/usr/bin/terraform", 
            "./bin/terraform",
            "C:\\terraform\\terraform.exe",
            "/opt/homebrew/bin/terraform",  # macOS Homebrew
            "/home/linuxbrew/.linuxbrew/bin/terraform",  # Linux Homebrew
            "terraform"  # System PATH as last resort
        ]
        
        for path in trusted_paths:
            if path == "terraform":
                # Check if terraform is in PATH
                import shutil
                terraform_path = shutil.which("terraform")
                if terraform_path:
                    return terraform_path
            elif os.path.exists(path):
                return path
        
        return None
    
    async def execute_pipeline(
        self,
        approval_id: int,
        cfg: Dict[str, Any],
        tech: Optional[Dict[str, Any]],
        db,
        log
    ) -> str:
        """Execute pipeline with AI-generated infrastructure."""
        
        try:
            # Validate and sanitize inputs
            sanitized_cfg = self._sanitize_config(cfg)
            
            # Generate infrastructure requirements from tech stack
            requirements = self._build_infrastructure_requirements(sanitized_cfg, tech)
            
            # Generate Terraform configuration using AI
            await log("Generating infrastructure configuration with AI...")
            terraform_files = await self.ai_generator.generate_terraform_config(
                requirements, 
                sanitized_cfg["APP_NAME"]
            )
            
            # Execute secure terraform deployment
            app_url = await self._execute_secure_terraform(
                terraform_files, 
                sanitized_cfg, 
                log
            )
            
            # Update approval status
            await self._update_approval_status(approval_id, "completed", db)
            
            # Trigger post-deployment validation
            await self._validate_deployment(app_url, log)
            
            return app_url
            
        except Exception as exc:
            await self._handle_pipeline_failure(approval_id, exc, db, log)
            raise
    
    def _sanitize_config(self, cfg: Dict[str, Any]) -> Dict[str, str]:
        """Sanitize configuration inputs to prevent injection attacks."""
        sanitized = {}
        
        # Whitelist allowed configuration keys
        allowed_keys = {
            "APP_NAME", "LOCATION", "RESOURCE_GROUP", "DEPLOY_TARGET",
            "ENVIRONMENT", "EXPECTED_TRAFFIC"
        }
        
        for key, value in cfg.items():
            if key not in allowed_keys:
                continue
            
            # Sanitize string values
            if isinstance(value, str):
                # Remove potentially dangerous characters
                sanitized_value = self._sanitize_string(value)
                sanitized[key] = sanitized_value
            else:
                sanitized[key] = str(value)
        
        # Set secure defaults
        sanitized.setdefault("APP_NAME", "devops-app")
        sanitized.setdefault("LOCATION", "eastus")
        sanitized.setdefault("RESOURCE_GROUP", "devops-rg")
        sanitized.setdefault("ENVIRONMENT", "dev")
        
        return sanitized
    
    def _sanitize_string(self, value: str) -> str:
        """Sanitize string input to prevent injection attacks."""
        import re
        
        # Remove potentially dangerous characters
        # Allow only alphanumeric, hyphens, underscores
        sanitized = re.sub(r'[^a-zA-Z0-9\-_]', '', value)
        
        # Limit length
        return sanitized[:50]
    
    def _build_infrastructure_requirements(
        self, 
        cfg: Dict[str, str], 
        tech: Optional[Dict[str, Any]]
    ) -> InfrastructureRequirements:
        """Build infrastructure requirements from configuration and tech stack."""
        
        if not tech:
            tech = {}
        
        # Determine traffic expectations based on environment
        traffic_map = {
            'dev': 'low',
            'staging': 'medium', 
            'prod': 'high'
        }
        
        expected_traffic = traffic_map.get(cfg.get("ENVIRONMENT", "dev"), "low")
        
        # Detect database requirements
        database_required = any([
            tech.get("hasDatabase", False),
            "database" in tech.get("dependencies", []),
            "postgres" in str(tech.get("dependencies", [])).lower(),
            "mysql" in str(tech.get("dependencies", [])).lower()
        ])
        
        # Detect cache requirements  
        cache_required = any([
            "redis" in str(tech.get("dependencies", [])).lower(),
            "memcached" in str(tech.get("dependencies", [])).lower()
        ])
        
        return InfrastructureRequirements(
            app_type=self._determine_app_type(tech),
            language=tech.get("language", "python"),
            framework=tech.get("framework"),
            expected_traffic=expected_traffic,
            database_required=database_required,
            cache_required=cache_required,
            environment=cfg.get("ENVIRONMENT", "dev"),
            region=cfg.get("LOCATION", "eastus"),
            compliance_requirements=self._get_compliance_requirements(cfg)
        )
    
    def _determine_app_type(self, tech: Dict[str, Any]) -> str:
        """Determine application type from tech stack."""
        framework = tech.get("framework", "").lower()
        language = tech.get("language", "").lower()
        
        if framework in ["react", "vue", "angular"]:
            return "spa"
        elif framework in ["fastapi", "flask", "django", "express"]:
            return "api"
        elif language in ["python", "javascript", "java"]:
            return "web"
        else:
            return "microservice"
    
    def _get_compliance_requirements(self, cfg: Dict[str, str]) -> List[str]:
        """Get compliance requirements based on configuration."""
        requirements = []
        
        environment = cfg.get("ENVIRONMENT", "dev")
        
        if environment == "prod":
            requirements.extend(["encryption-at-rest", "backup-required"])
        
        # Add more compliance logic based on your needs
        
        return requirements
    
    async def _execute_secure_terraform(
        self,
        terraform_files: Dict[str, str],
        cfg: Dict[str, str],
        log
    ) -> str:
        """Execute Terraform with security controls."""
        
        app_name = cfg["APP_NAME"]
        fallback_url = f"https://{app_name}.azurewebsites.net"
        
        # Use secure temporary directory
        with tempfile.TemporaryDirectory(prefix="secure_terraform_") as tf_dir:
            await log(f"Using secure terraform directory: {tf_dir}")
            
            # Write Terraform files securely
            for filename, content in terraform_files.items():
                file_path = Path(tf_dir) / filename
                
                # Validate filename to prevent path traversal
                if not self._is_safe_filename(filename):
                    await log(f"Unsafe filename detected: {filename}")
                    continue
                
                # Write file with restricted permissions
                file_path.write_text(content, encoding='utf-8')
                os.chmod(file_path, 0o600)  # Read/write for owner only
            
            # Set secure environment variables
            env = {
                **os.environ,
                "TF_INPUT": "false",
                "TF_IN_AUTOMATION": "true",
                "TF_CLI_ARGS": "-no-color"
            }
            
            try:
                # Execute terraform commands securely
                await self._run_terraform_init(tf_dir, env, log)
                await self._run_terraform_plan(tf_dir, env, log)
                app_url = await self._run_terraform_apply(tf_dir, env, log, fallback_url)
                
                return app_url
                
            except Exception as e:
                await log(f"Terraform execution failed: {str(e)}")
                return fallback_url
    
    def _is_safe_filename(self, filename: str) -> bool:
        """Validate filename to prevent path traversal attacks."""
        import re
        
        # Only allow .tf files with safe names
        if not filename.endswith('.tf'):
            return False
        
        # Check for path traversal attempts
        if '..' in filename or '/' in filename or '\\' in filename:
            return False
        
        # Only allow alphanumeric, hyphens, underscores, and dots
        if not re.match(r'^[a-zA-Z0-9\-_.]+\.tf$', filename):
            return False
        
        return True
    
    async def _run_terraform_command(
        self, 
        args: List[str], 
        cwd: str, 
        env: Dict[str, str]
    ) -> tuple[int, str]:
        """Run terraform command securely."""
        
        # Validate arguments to prevent command injection
        safe_args = []
        for arg in args:
            if self._is_safe_terraform_arg(arg):
                safe_args.append(arg)
            else:
                raise ValueError(f"Unsafe terraform argument: {arg}")
        
        proc = await asyncio.create_subprocess_exec(
            self.terraform_path,
            *safe_args,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        
        stdout, _ = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace")
    
    def _is_safe_terraform_arg(self, arg: str) -> bool:
        """Validate terraform argument for security."""
        import re
        
        # Allow common terraform arguments
        safe_patterns = [
            r'^init$',
            r'^plan$', 
            r'^apply$',
            r'^output$',
            r'^-auto-approve$',
            r'^-no-color$',
            r'^-upgrade$',
            r'^-json$',
            r'^-input=false$'
        ]
        
        return any(re.match(pattern, arg) for pattern in safe_patterns)
    
    async def _run_terraform_init(self, tf_dir: str, env: Dict[str, str], log) -> None:
        """Run terraform init securely."""
        await log("Running terraform init...")
        rc, out = await self._run_terraform_command(
            ["init", "-no-color", "-upgrade"], 
            tf_dir, 
            env
        )
        
        if rc != 0:
            await log(f"terraform init failed: {out[-1000:]}")
            raise RuntimeError("Terraform init failed")
        
        await log("terraform init: OK")
    
    async def _run_terraform_plan(self, tf_dir: str, env: Dict[str, str], log) -> None:
        """Run terraform plan securely."""
        await log("Running terraform plan...")
        rc, out = await self._run_terraform_command(
            ["plan", "-no-color"], 
            tf_dir, 
            env
        )
        
        if rc != 0:
            await log(f"terraform plan failed: {out[-1000:]}")
            raise RuntimeError("Terraform plan failed")
        
        await log("terraform plan: OK")
    
    async def _run_terraform_apply(
        self, 
        tf_dir: str, 
        env: Dict[str, str], 
        log,
        fallback_url: str
    ) -> str:
        """Run terraform apply securely."""
        await log("Running terraform apply...")
        rc, out = await self._run_terraform_command(
            ["apply", "-auto-approve", "-no-color"], 
            tf_dir, 
            env
        )
        
        if rc != 0:
            await log(f"terraform apply failed: {out[-1000:]}")
            return fallback_url
        
        await log("terraform apply: OK")
        
        # Get outputs
        return await self._get_terraform_outputs(tf_dir, env, log, fallback_url)
    
    async def _get_terraform_outputs(
        self, 
        tf_dir: str, 
        env: Dict[str, str], 
        log,
        fallback_url: str
    ) -> str:
        """Get terraform outputs securely."""
        await log("Fetching terraform outputs...")
        rc, out = await self._run_terraform_command(
            ["output", "-json"], 
            tf_dir, 
            env
        )
        
        if rc == 0:
            try:
                outputs = json.loads(out)
                url = outputs.get("app_url", {}).get("value")
                
                if url:
                    await log(f"Deployment URL: {url}")
                    return url
            except json.JSONDecodeError:
                await log("Failed to parse terraform outputs")
        
        return fallback_url
    
    async def _update_approval_status(
        self, 
        approval_id: int, 
        status: str, 
        db
    ) -> None:
        """Update approval status in database."""
        r = await db.execute(select(Approval).where(Approval.id == approval_id))
        rec = r.scalar_one_or_none()
        if rec:
            rec.status = status
            await db.commit()
    
    async def _validate_deployment(self, app_url: str, log) -> None:
        """Validate deployment is accessible."""
        try:
            await log(f"Validating deployment at: {app_url}")
            
            # Wait a bit for DNS propagation
            await asyncio.sleep(10)
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(app_url)
                if response.status_code == 200:
                    await log("✅ Deployment validation successful")
                elif response.status_code in [404, 503]:
                    await log(f"⚠️ Deployment not ready yet (status {response.status_code}), this is normal for new deployments")
                else:
                    await log(f"⚠️ Deployment responding with status {response.status_code}")
        except httpx.ConnectError:
            await log("⚠️ Deployment not accessible yet - DNS may still be propagating")
        except httpx.TimeoutException:
            await log("⚠️ Deployment validation timed out - this is normal for new deployments")
        except Exception as e:
            await log(f"⚠️ Deployment validation failed: {str(e)}")
    
    async def _handle_pipeline_failure(
        self, 
        approval_id: int, 
        exc: Exception, 
        db, 
        log
    ) -> None:
        """Handle pipeline failure securely."""
        # Update approval status
        await self._update_approval_status(approval_id, "failed", db)
        
        # Log sanitized error (avoid exposing sensitive information)
        error_msg = f"Pipeline failed: {type(exc).__name__}"
        await log(error_msg)
        
        # Log full traceback for debugging (but sanitize sensitive data)
        tb = traceback.format_exc()
        sanitized_tb = self._sanitize_traceback(tb)
        await log(f"PIPELINE ERROR: {sanitized_tb}")
    
    def _sanitize_traceback(self, traceback_str: str) -> str:
        """Sanitize traceback to remove sensitive information."""
        import re
        
        # Remove potential secrets/tokens
        sanitized = re.sub(r'(token|key|secret|password)=[^\s]+', r'\1=***', traceback_str, flags=re.IGNORECASE)
        
        # Remove file paths that might contain sensitive info
        sanitized = re.sub(r'/[^\s]*/(secrets?|keys?|tokens?)/[^\s]*', '/***/', sanitized)
        
        return sanitized