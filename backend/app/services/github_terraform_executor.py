"""GitHub Actions-based Terraform executor for secure infrastructure deployment."""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
import httpx
import base64

from .ai_terraform_generator import AITerraformGenerator, InfrastructureRequirements


class GitHubTerraformExecutor:
    """Execute Terraform via GitHub Actions for secure, scalable deployments."""
    
    def __init__(self, gemini_api_key: str):
        self.ai_generator = AITerraformGenerator(gemini_api_key)
        self.github_api = "https://api.github.com"
    
    async def execute_pipeline(
        self,
        approval_id: str,
        cfg: Dict[str, Any],
        tech: Optional[Dict[str, Any]],
        repo: str,
        branch: str,
        gh_token: str,
        log
    ) -> str:
        """Execute Terraform pipeline via GitHub Actions."""
        
        try:
            # Validate GitHub token and repository access first
            await self._validate_github_access(repo, gh_token, log)
            
            # Generate infrastructure requirements
            requirements = self._build_infrastructure_requirements(cfg, tech)
            
            # Generate Terraform configuration using AI
            await log("Generating infrastructure configuration with AI...")
            terraform_files = await self.ai_generator.generate_terraform_config(
                requirements, 
                cfg.get("APP_NAME", "devops-app")
            )
            
            # Extract AI-generated workflow
            workflow_content = terraform_files.pop("terraform-deploy.yml", None)
            if not workflow_content:
                await log("Using fallback AI...")
                workflow_content = self._create_fallback_workflow(cfg, terraform_files)
            else:
                await log("Using AI-generated GitHub Actions workflow")
            
            # Commit Terraform files and workflow to repository
            await log("Committing Terraform files to repository...")
            await self._commit_terraform_files(
                repo, branch, terraform_files, workflow_content, gh_token
            )
            
            # Push Azure secrets to GitHub repository
            await log("Configuring Azure secrets...")
            await self._setup_azure_secrets(repo, cfg, gh_token)
            
            # Trigger the workflow
            await log("Triggering Terraform deployment workflow...")
            workflow_run_url = await self._trigger_terraform_workflow(
                repo, branch, gh_token, log
            )
            
            # Monitor workflow execution
            await log("Monitoring deployment progress...")
            app_url = await self._monitor_terraform_workflow(
                repo, workflow_run_url, gh_token, log
            )
            
            return app_url
            
        except Exception as exc:
            await log(f"GitHub Terraform execution failed: {str(exc)}")
            raise
    
    def _build_infrastructure_requirements(
        self, 
        cfg: Dict[str, Any], 
        tech: Optional[Dict[str, Any]]
    ) -> InfrastructureRequirements:
        """Build infrastructure requirements from configuration."""
        if not tech:
            tech = {}
        
        # Determine traffic expectations
        traffic_map = {'dev': 'low', 'staging': 'medium', 'prod': 'high'}
        expected_traffic = traffic_map.get(cfg.get("ENVIRONMENT", "dev"), "low")
        
        # Detect requirements
        database_required = any([
            tech.get("hasDatabase", False),
            "database" in tech.get("dependencies", []),
            any(db in str(tech.get("dependencies", [])).lower() 
                for db in ["postgres", "mysql", "mongodb"])
        ])
        
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
    
    def _get_compliance_requirements(self, cfg: Dict[str, Any]) -> List[str]:
        """Get compliance requirements based on configuration."""
        requirements = []
        environment = cfg.get("ENVIRONMENT", "dev")
        
        if environment == "prod":
            requirements.extend(["encryption-at-rest", "backup-required"])
        
        return requirements
    
    def _create_fallback_workflow(
        self, 
        cfg: Dict[str, Any], 
        terraform_files: Dict[str, str]
    ) -> str:
        """Create fallback GitHub Actions workflow if AI generation fails."""
        
        app_name = cfg.get("APP_NAME", "devops-app")
        environment = cfg.get("ENVIRONMENT", "dev")
        
        # Simple fallback workflow
        workflow_yaml = f"""
name: Terraform Infrastructure Deployment
on:
  workflow_dispatch:
    inputs:
      approval_id:
        description: Approval ID for tracking
        required: true
        type: string
env:
  TF_VERSION: 1.6.0
  ARM_CLIENT_ID: ${{{{ secrets.AZURE_CLIENT_ID }}}}
  ARM_CLIENT_SECRET: ${{{{ secrets.AZURE_CLIENT_SECRET }}}}
  ARM_SUBSCRIPTION_ID: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
  ARM_TENANT_ID: ${{{{ secrets.AZURE_TENANT_ID }}}}
jobs:
  terraform:
    name: Deploy Infrastructure
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
    - name: Checkout Code
      uses: actions/checkout@v4
    - name: Setup Terraform
      uses: hashicorp/setup-terraform@v3
      with:
        terraform_version: ${{{{ env.TF_VERSION }}}}
        terraform_wrapper: false
    - name: Azure Login
      uses: azure/login@v1
      with:
        creds: ${{{{ secrets.AZURE_CREDENTIALS }}}}
    - name: Terraform Init
      run: terraform init
      working-directory: ./terraform
    - name: Check for Existing Resources
      run: |
        # Try to import existing resource group if it exists
        RG_NAME="{app_name}-rg"
        if az group show --name $RG_NAME --output none 2>/dev/null; then
          echo "Resource group $RG_NAME exists, attempting to import..."
          terraform import azurerm_resource_group.main "/subscriptions/${{{{ env.ARM_SUBSCRIPTION_ID }}}}/resourceGroups/$RG_NAME" || echo "Import failed or resource already managed"
        else
          echo "Resource group $RG_NAME does not exist, will be created"
        fi
      working-directory: ./terraform
    - name: Terraform Plan
      run: terraform plan -var="app_name={app_name}" -var="environment={environment}" -out=tfplan
      working-directory: ./terraform
    - name: Terraform Apply
      run: terraform apply -auto-approve tfplan
      working-directory: ./terraform
    - name: Export Outputs
      id: terraform-outputs
      run: |
        APP_URL=$(terraform output -raw app_url)
        echo "app_url=$APP_URL" >> $GITHUB_OUTPUT
        echo "APP_URL=$APP_URL" >> $GITHUB_ENV
        echo "App URL: $APP_URL"
      working-directory: ./terraform
    - name: Deployment Summary
      run: |
        echo "## Deployment Successful!" >> $GITHUB_STEP_SUMMARY
        echo "- **App URL**: ${{{{ steps.terraform-outputs.outputs.app_url }}}}" >> $GITHUB_STEP_SUMMARY
        echo "- **Environment**: {environment}" >> $GITHUB_STEP_SUMMARY
        echo "DEPLOYMENT_SUCCESS: ${{{{ steps.terraform-outputs.outputs.app_url }}}}"
"""
        
        return workflow_yaml.strip()
    
    async def _commit_terraform_files(
        self,
        repo: str,
        branch: str,
        terraform_files: Dict[str, str],
        workflow_content: str,
        gh_token: str
    ) -> None:
        """Commit Terraform files and workflow to repository."""
        
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # Create terraform directory structure
        files_to_commit = {
            "terraform/main.tf": terraform_files.get("main.tf", ""),
            "terraform/variables.tf": terraform_files.get("variables.tf", ""),
            "terraform/outputs.tf": terraform_files.get("outputs.tf", ""),
            "terraform/versions.tf": terraform_files.get("versions.tf", ""),
            ".github/workflows/terraform-deploy.yml": workflow_content
        }
        
        async with httpx.AsyncClient(timeout=30) as client:
            for file_path, content in files_to_commit.items():
                if not content:
                    continue
                
                # Check if file exists
                url = f"{self.github_api}/repos/{repo}/contents/{file_path}"
                response = await client.get(url, headers=headers, params={"ref": branch})
                
                # Prepare commit data
                commit_data = {
                    "message": f"feat: add {file_path} via DevOps Agent",
                    "content": base64.b64encode(content.encode()).decode(),
                    "branch": branch
                }
                
                # Add SHA if file exists (for updates)
                if response.status_code == 200:
                    existing_file = response.json()
                    commit_data["sha"] = existing_file["sha"]
                
                # Commit file
                await client.put(url, headers=headers, json=commit_data)
    
    async def _setup_azure_secrets(
        self, 
        repo: str, 
        cfg: Dict[str, Any], 
        gh_token: str
    ) -> None:
        """Setup Azure secrets in GitHub repository."""
        
        # Get Azure credentials from config or environment
        # Check multiple possible key names for flexibility
        azure_secrets = {
            "AZURE_CLIENT_ID": (
                cfg.get("AZURE_CLIENT_ID") or 
                cfg.get("CLIENT_ID") or 
                os.getenv("AZURE_CLIENT_ID", "")
            ),
            "AZURE_CLIENT_SECRET": (
                cfg.get("AZURE_CLIENT_SECRET") or 
                cfg.get("CLIENT_SECRET") or 
                os.getenv("AZURE_CLIENT_SECRET", "")
            ),
            "AZURE_SUBSCRIPTION_ID": (
                cfg.get("AZURE_SUBSCRIPTION_ID") or 
                cfg.get("SUBSCRIPTION_ID") or 
                os.getenv("AZURE_SUBSCRIPTION_ID", "")
            ),
            "AZURE_TENANT_ID": (
                cfg.get("AZURE_TENANT_ID") or 
                cfg.get("TENANT_ID") or 
                os.getenv("AZURE_TENANT_ID", "")
            ),
            "GH_TOKEN": (
                cfg.get("GITHUB_PERSONAL_ACCESS_TOKEN") or 
                os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
            ),
            "APP_NAME": cfg.get("APP_NAME", "devops-app"),
            "AZURE_LOCATION": cfg.get("LOCATION", "eastus")
        }
        
        # Log which credentials were found (without exposing values)
        missing_creds = []
        for key, value in azure_secrets.items():
            if key.startswith("AZURE_") and key != "AZURE_LOCATION" and not value:
                missing_creds.append(key)
        
        if missing_creds:
            raise RuntimeError(
                f"Missing Azure credentials: {', '.join(missing_creds)}. "
                "Please set these in your config.py or environment variables."
            )
        
        # Create AZURE_CREDENTIALS JSON for Azure CLI login
        azure_creds = {
            "clientId": azure_secrets["AZURE_CLIENT_ID"],
            "clientSecret": azure_secrets["AZURE_CLIENT_SECRET"],
            "subscriptionId": azure_secrets["AZURE_SUBSCRIPTION_ID"],
            "tenantId": azure_secrets["AZURE_TENANT_ID"]
        }
        azure_secrets["AZURE_CREDENTIALS"] = json.dumps(azure_creds)
        
        # Setup Terraform state storage secrets (optional)
        azure_secrets.update({
            "TF_STATE_RESOURCE_GROUP": cfg.get("TF_STATE_RESOURCE_GROUP", "terraform-state-rg"),
            "TF_STATE_STORAGE_ACCOUNT": cfg.get("TF_STATE_STORAGE_ACCOUNT", "tfstate" + cfg.get("APP_NAME", "app")[:10])
        })
        
        # Log what secrets will be set (without values)
        secret_names = [name for name, value in azure_secrets.items() if value]
        print(f"Setting GitHub secrets: {', '.join(secret_names)}")
        
        # Push secrets to GitHub
        await self._set_github_secrets(repo, azure_secrets, gh_token)
    
    async def _set_github_secrets(
        self, 
        repo: str, 
        secrets: Dict[str, str], 
        gh_token: str
    ) -> None:
        """Set multiple GitHub repository secrets."""
        
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        async with httpx.AsyncClient(timeout=30) as client:
            # First, verify repository access
            repo_url = f"{self.github_api}/repos/{repo}"
            repo_response = await client.get(repo_url, headers=headers)
            
            if repo_response.status_code == 404:
                raise RuntimeError(f"Repository '{repo}' not found or token lacks access. Check repository name and token permissions.")
            elif repo_response.status_code == 403:
                raise RuntimeError(f"Access forbidden to repository '{repo}'. Token may lack required permissions.")
            elif repo_response.status_code != 200:
                raise RuntimeError(f"Failed to access repository '{repo}': HTTP {repo_response.status_code}")
            
            # Check if repository allows secrets (not a fork without write access)
            repo_data = repo_response.json()
            if repo_data.get("fork", False):
                # For forks, check if we have write access
                permissions = repo_data.get("permissions", {})
                if not permissions.get("admin", False) and not permissions.get("push", False):
                    raise RuntimeError(f"Cannot set secrets on fork '{repo}' without write access. Use the upstream repository or ensure you have push permissions.")
            
            # Get repository public key for encryption
            key_url = f"{self.github_api}/repos/{repo}/actions/secrets/public-key"
            key_response = await client.get(key_url, headers=headers)
            
            if key_response.status_code == 404:
                raise RuntimeError(f"Repository '{repo}' does not have GitHub Actions enabled or secrets API is not accessible.")
            elif key_response.status_code == 403:
                raise RuntimeError(f"Token lacks 'repo' scope or admin access to set secrets in '{repo}'. Required scopes: 'repo' or 'public_repo' + admin access.")
            elif key_response.status_code != 200:
                error_detail = key_response.text if key_response.text else f"HTTP {key_response.status_code}"
                raise RuntimeError(f"Failed to get repository public key: {error_detail}")
            
            try:
                public_key_data = key_response.json()
                public_key = public_key_data["key"]
                key_id = public_key_data["key_id"]
            except (KeyError, ValueError) as e:
                raise RuntimeError(f"Invalid public key response format: {e}")
            
            # Encrypt and set each secret
            for secret_name, secret_value in secrets.items():
                if not secret_value:
                    print(f"Skipping empty secret: {secret_name}")
                    continue
                
                try:
                    # Encrypt secret value
                    encrypted_value = self._encrypt_secret(secret_value, public_key)
                    
                    # Set secret
                    secret_url = f"{self.github_api}/repos/{repo}/actions/secrets/{secret_name}"
                    secret_data = {
                        "encrypted_value": encrypted_value,
                        "key_id": key_id
                    }
                    
                    secret_response = await client.put(secret_url, headers=headers, json=secret_data)
                    
                    if secret_response.status_code not in [201, 204]:
                        error_detail = secret_response.text if secret_response.text else f"HTTP {secret_response.status_code}"
                        raise RuntimeError(f"Failed to set secret '{secret_name}': {error_detail}")
                    else:
                        print(f"Successfully set secret: {secret_name}")
                        
                except Exception as e:
                    raise RuntimeError(f"Failed to set secret '{secret_name}': {str(e)}")
    
    def _encrypt_secret(self, secret_value: str, public_key: str) -> str:
        """Encrypt secret value using repository public key."""
        try:
            from nacl import encoding, public
        except ImportError:
            raise RuntimeError("PyNaCl library required for secret encryption. Install with: pip install PyNaCl")
        
        try:
            # Convert the public key from base64
            public_key_bytes = base64.b64decode(public_key)
            public_key_obj = public.PublicKey(public_key_bytes)
            
            # Encrypt the secret
            sealed_box = public.SealedBox(public_key_obj)
            encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
            
            # Return base64 encoded encrypted value
            return base64.b64encode(encrypted).decode("utf-8")
        except Exception as e:
            raise RuntimeError(f"Failed to encrypt secret: {str(e)}")
    
    async def _trigger_terraform_workflow(
        self, 
        repo: str, 
        branch: str, 
        gh_token: str,
        log
    ) -> str:
        """Trigger the Terraform deployment workflow."""
        
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # Trigger workflow dispatch
        workflow_url = f"{self.github_api}/repos/{repo}/actions/workflows/terraform-deploy.yml/dispatches"
        dispatch_data = {
            "ref": branch,
            "inputs": {
                "approval_id": f"approval-{int(time.time())}"
            }
        }
        
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(workflow_url, headers=headers, json=dispatch_data)
            
            if response.status_code != 204:
                raise RuntimeError(f"Failed to trigger workflow: {response.status_code}")
            
            # Wait a moment for workflow to start
            await asyncio.sleep(5)
            
            # Get the latest workflow run
            runs_url = f"{self.github_api}/repos/{repo}/actions/workflows/terraform-deploy.yml/runs"
            runs_response = await client.get(runs_url, headers=headers, params={"per_page": 1})
            
            if runs_response.status_code == 200:
                runs_data = runs_response.json()
                if runs_data["workflow_runs"]:
                    run_url = runs_data["workflow_runs"][0]["html_url"]
                    await log(f"🔗 Workflow URL: {run_url}")
                    return run_url
            
            return f"https://github.com/{repo}/actions"
    
    async def _monitor_terraform_workflow(
        self, 
        repo: str, 
        workflow_run_url: str, 
        gh_token: str,
        log
    ) -> str:
        """Monitor Terraform workflow execution and return app URL."""
        
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # Extract run ID from URL or get latest run
        runs_url = f"{self.github_api}/repos/{repo}/actions/workflows/terraform-deploy.yml/runs"
        
        async with httpx.AsyncClient(timeout=30) as client:
            # Monitor workflow for up to 20 minutes
            for attempt in range(120):  # 120 * 10s = 20 minutes
                runs_response = await client.get(runs_url, headers=headers, params={"per_page": 1})
                
                if runs_response.status_code == 200:
                    runs_data = runs_response.json()
                    if runs_data["workflow_runs"]:
                        run = runs_data["workflow_runs"][0]
                        status = run["status"]
                        conclusion = run.get("conclusion")
                        
                        await log(f"Workflow status: {status.upper()}")
                        
                        if status == "completed":
                            if conclusion == "success":
                                await log("Terraform deployment completed successfully!")
                                
                                # Get actual app URL from Terraform outputs
                                app_url = await self._get_terraform_outputs(repo, gh_token, client)
                                return app_url
                            else:
                                raise RuntimeError(f"Terraform workflow failed: {conclusion}")
                        
                        elif status in ["queued", "in_progress"]:
                            await log(f"Workflow {status}, waiting...")
                            await asyncio.sleep(10)
                        else:
                            raise RuntimeError(f"Unexpected workflow status: {status}")
                    else:
                        await log("Waiting for workflow to start...")
                        await asyncio.sleep(10)
                else:
                    await log("Failed to check workflow status")
                    await asyncio.sleep(10)
            
            raise RuntimeError("Terraform workflow monitoring timeout (20 minutes)")
    
    async def _get_terraform_outputs(
        self, 
        repo: str, 
        gh_token: str, 
        client: httpx.AsyncClient
    ) -> str:
        """Get app URL from Terraform outputs via GitHub API."""
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # Get the latest successful workflow run
        runs_url = f"{self.github_api}/repos/{repo}/actions/workflows/terraform-deploy.yml/runs"
        runs_response = await client.get(runs_url, headers=headers, params={"status": "success", "per_page": 1})
        
        print(f"Checking workflow runs: {runs_url}")
        
        if runs_response.status_code == 200:
            runs_data = runs_response.json()
            print(f"Found {len(runs_data.get('workflow_runs', []))} successful runs")
            print(f"Runs response status: {runs_response.status_code}")
            
            if runs_data["workflow_runs"]:
                run_id = runs_data["workflow_runs"][0]["id"]
                run_url = runs_data["workflow_runs"][0]["html_url"]
                run_status = runs_data["workflow_runs"][0].get("status")
                run_conclusion = runs_data["workflow_runs"][0].get("conclusion")
                print(f"Checking run {run_id}: {run_url}")
                print(f"Run status: {run_status}, conclusion: {run_conclusion}")
                
                # Get jobs for this run
                jobs_url = f"{self.github_api}/repos/{repo}/actions/runs/{run_id}/jobs"
                jobs_response = await client.get(jobs_url, headers=headers)
                
                print(f"Getting jobs: {jobs_url}")
                print(f"Jobs response status: {jobs_response.status_code}")
                
                if jobs_response.status_code == 200:
                    jobs_data = jobs_response.json()
                    print(f"Found {len(jobs_data.get('jobs', []))} jobs")
                    
                    for job in jobs_data["jobs"]:
                        print(f"Job: '{job['name']}' - Status: {job.get('status')} - Conclusion: {job.get('conclusion')}")
                        
                        if job["name"] == "Deploy Infrastructure":
                            print(f"Found target job: {job['id']}")
                            
                            # Get job logs
                            logs_url = f"{self.github_api}/repos/{repo}/actions/jobs/{job['id']}/logs"
                            logs_response = await client.get(logs_url, headers=headers, follow_redirects=True)
                            
                            print(f"Getting logs: {logs_url}")
                            print(f"Logs response status: {logs_response.status_code}")
                            print(f"Logs response headers: {dict(logs_response.headers)}")
                            
                            if logs_response.status_code == 200:
                                logs_text = logs_response.text
                                print(f"Raw logs length: {len(logs_text)} characters")
                                print(f"Raw logs sample (first 200 chars): {repr(logs_text[:200])}")
                                
                                # Decode HTML entities that might be in the logs
                                import html
                                logs_text = html.unescape(logs_text)
                                print(f"Decoded logs length: {len(logs_text)} characters")
                                print(f"Decoded logs sample (first 200 chars): {repr(logs_text[:200])}")
                                
                                # Debug: Look for key patterns in logs
                                print(f"Searching for URL patterns in logs...")
                                if "DEPLOYMENT_SUCCESS:" in logs_text:
                                    print("Found DEPLOYMENT_SUCCESS marker in logs")
                                if "App URL:" in logs_text:
                                    print("Found App URL marker in logs")
                                if "azurewebsites.net" in logs_text:
                                    print("Found Azure websites URL in logs")
                                
                                # Look for deployment success marker
                                import re
                                success_match = re.search(r'DEPLOYMENT_SUCCESS: ([^\s\[&]+)', logs_text)
                                if success_match:
                                    url = success_match.group(1)
                                    # Clean up any HTML entities and ANSI codes
                                    url = re.sub(r'&quot;', '', url)
                                    url = re.sub(r'\[\d+m', '', url)
                                    # Decode any remaining HTML entities
                                    url = html.unescape(url)
                                    print(f"Found DEPLOYMENT_SUCCESS: {url}")
                                    return url
                                
                                # Fallback: look for app_url output
                                url_match = re.search(r'app_url=([^\s\[&]+)', logs_text)
                                if url_match:
                                    url = url_match.group(1)
                                    url = re.sub(r'&quot;', '', url)
                                    url = re.sub(r'\[\d+m', '', url)
                                    # Decode any remaining HTML entities
                                    url = html.unescape(url)
                                    print(f"Found app_url: {url}")
                                    return url
                                
                                # Fallback: look for App URL in summary
                                summary_match = re.search(r'App URL: ([^\s\[&]+)', logs_text)
                                if summary_match:
                                    url = summary_match.group(1)
                                    url = re.sub(r'&quot;', '', url)
                                    url = re.sub(r'\[\d+m', '', url)
                                    # Decode any remaining HTML entities
                                    url = html.unescape(url)
                                    print(f"Found App URL: {url}")
                                    return url
                                
                                # Fallback: look for terraform output command result
                                terraform_output_match = re.search(r'terraform output -raw app_url[^\n]*\n([^\s\[&]+)', logs_text)
                                if terraform_output_match:
                                    url = terraform_output_match.group(1)
                                    url = re.sub(r'&quot;', '', url)
                                    url = re.sub(r'\[\d+m', '', url)
                                    # Decode any remaining HTML entities
                                    url = html.unescape(url)
                                    print(f"Found terraform output: {url}")
                                    return url
                                
                                # Fallback: look for any https://...azurewebsites.net URL
                                azure_url_match = re.search(r'(https://[^\s\[&]+\.azurewebsites\.net)', logs_text)
                                if azure_url_match:
                                    url = azure_url_match.group(1)
                                    url = re.sub(r'&quot;', '', url)
                                    url = re.sub(r'\[\d+m', '', url)
                                    # Decode any remaining HTML entities
                                    url = html.unescape(url)
                                    print(f"Found Azure URL: {url}")
                                    return url
                                
                                # Debug: Show a sample of the logs to understand the format
                                if 'DEPLOYMENT_SUCCESS' in logs_text:
                                    success_pos = logs_text.find('DEPLOYMENT_SUCCESS')
                                    sample = logs_text[max(0, success_pos-100):success_pos+200]
                                    print(f"Sample around DEPLOYMENT_SUCCESS: {repr(sample)}")
                                elif 'App URL:' in logs_text:
                                    url_pos = logs_text.find('App URL:')
                                    sample = logs_text[max(0, url_pos-50):url_pos+150]
                                    print(f"Sample around App URL: {repr(sample)}")
                                elif 'azurewebsites.net' in logs_text:
                                    azure_pos = logs_text.find('azurewebsites.net')
                                    sample = logs_text[max(0, azure_pos-50):azure_pos+100]
                                    print(f"Sample around azurewebsites.net: {repr(sample)}")
                                else:
                                    print("No key markers found in logs")
                                
                                print("No URL patterns found in logs")
                            else:
                                print(f"Failed to get logs: HTTP {logs_response.status_code}")
                                if logs_response.text:
                                    print(f"Error response: {logs_response.text[:500]}")
                else:
                    print(f"Failed to get jobs: HTTP {jobs_response.status_code}")
                    if jobs_response.text:
                        print(f"Jobs error response: {jobs_response.text[:500]}")
            else:
                print("No successful workflow runs found")
                print(f"Available runs: {[{'id': run['id'], 'status': run['status'], 'conclusion': run.get('conclusion')} for run in runs_data.get('workflow_runs', [])[:3]]}")
        else:
            print(f"Failed to get workflow runs: HTTP {runs_response.status_code}")
            if runs_response.text:
                print(f"Runs error response: {runs_response.text[:500]}")
        
        raise RuntimeError("Failed to extract app URL from Terraform outputs")
    
    async def _validate_github_access(self, repo: str, gh_token: str, log) -> None:
        """Validate GitHub token has required permissions for the repository."""
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        async with httpx.AsyncClient(timeout=15) as client:
            # Check token scopes
            user_url = f"{self.github_api}/user"
            user_response = await client.get(user_url, headers=headers)
            
            if user_response.status_code == 401:
                raise RuntimeError("GitHub token is invalid or expired. Please check your GITHUB_PERSONAL_ACCESS_TOKEN.")
            elif user_response.status_code != 200:
                raise RuntimeError(f"Failed to validate GitHub token: HTTP {user_response.status_code}")
            
            # Check token scopes from headers
            scopes = user_response.headers.get("x-oauth-scopes", "")
            await log(f"GitHub token scopes: {scopes or 'none'}")
            
            required_scopes = ["repo", "workflow"]
            available_scopes = [s.strip() for s in scopes.split(",") if s.strip()]
            
            missing_scopes = []
            for scope in required_scopes:
                if scope not in available_scopes and "repo" not in available_scopes:
                    missing_scopes.append(scope)
            
            if missing_scopes:
                await log(f"Missing GitHub token scopes: {', '.join(missing_scopes)}")
                await log("Required scopes: 'repo' and 'workflow' for private repos, or 'public_repo' and 'workflow' for public repos")
            
            # Check repository access
            repo_url = f"{self.github_api}/repos/{repo}"
            repo_response = await client.get(repo_url, headers=headers)
            
            if repo_response.status_code == 404:
                raise RuntimeError(f"Repository '{repo}' not found or token lacks read access. Verify repository name and token permissions.")
            elif repo_response.status_code == 403:
                raise RuntimeError(f"Access forbidden to repository '{repo}'. Token may lack required scopes.")
            elif repo_response.status_code != 200:
                raise RuntimeError(f"Failed to access repository '{repo}': HTTP {repo_response.status_code}")
            
            repo_data = repo_response.json()
            await log(f"Repository access validated: {repo} ({'private' if repo_data.get('private') else 'public'})")
            
            # Check if Actions are enabled
            actions_url = f"{self.github_api}/repos/{repo}/actions/permissions"
            actions_response = await client.get(actions_url, headers=headers)
            
            if actions_response.status_code == 200:
                actions_data = actions_response.json()
                if not actions_data.get("enabled", True):
                    raise RuntimeError(f"GitHub Actions are disabled for repository '{repo}'. Please enable Actions in repository settings.")
                await log("GitHub Actions are enabled")
            else:
                await log("Could not verify GitHub Actions status (this is normal for some repositories)")