"""AI-powered Terraform configuration generator."""
import json
import re
from typing import Dict, Any, Optional
import google.generativeai as genai
from pydantic import BaseModel, Field
from .ai_config import AIConfig


class InfrastructureRequirements(BaseModel):
    """Infrastructure requirements model."""
    app_type: str = Field(..., description="Application type (web, api, microservice, etc.)")
    language: str = Field(..., description="Programming language")
    framework: Optional[str] = Field(None, description="Framework used")
    expected_traffic: str = Field("low", description="Expected traffic (low, medium, high)")
    database_required: bool = Field(False, description="Whether database is required")
    cache_required: bool = Field(False, description="Whether caching is required")
    environment: str = Field("dev", description="Environment (dev, staging, prod)")
    region: str = Field("eastus", description="Azure region")
    compliance_requirements: list[str] = Field(default_factory=list, description="Compliance requirements")


class AITerraformGenerator:
    """AI-powered Terraform configuration generator using Google Gemini."""
    
    def __init__(self, gemini_api_key: str, model_name: Optional[str] = None, temperature: Optional[float] = None):
        genai.configure(api_key=gemini_api_key)
        self.model_name = model_name or AIConfig.get_ai_model()
        self.temperature = temperature or AIConfig.get_ai_temperature()
        self.model = genai.GenerativeModel(self.model_name)
    
    async def generate_terraform_config(
        self, 
        requirements: InfrastructureRequirements,
        app_name: str
    ) -> Dict[str, str]:
        """Generate Terraform configuration AND GitHub Actions workflow using Google Gemini."""
        
        prompt = self._build_prompt(requirements, app_name)
        
        try:
            # Generate content using Gemini
            response = await self._generate_async(prompt)
            
            # Extract and validate Terraform configuration + workflow
            config_files = self._parse_terraform_response(response.text)
            
            # Add security and compliance configurations
            config_files = self._enhance_security(config_files, requirements)
            
            return config_files
            
        except Exception as e:
            # Fallback to template-based generation
            return self._generate_fallback_config(requirements, app_name)
    
    async def _generate_async(self, prompt: str):
        """Generate content asynchronously using Gemini."""
        import asyncio
        
        # Run the synchronous generate_content in a thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            lambda: self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=self.temperature,
                    max_output_tokens=AIConfig.get_max_output_tokens(),
                    candidate_count=1
                )
            )
        )
    
    def _build_prompt(self, requirements: InfrastructureRequirements, app_name: str) -> str:
        """Build AI prompt for Terraform and GitHub Actions workflow generation."""
        return f"""
You are an expert DevOps engineer specializing in Terraform, Azure infrastructure, and GitHub Actions CI/CD pipelines. Generate a complete infrastructure-as-code solution.

Generate a complete solution for deploying a {requirements.app_type} application with the following requirements:

Application Details:
- Name: {app_name}
- Language: {requirements.language}
- Framework: {requirements.framework or 'None'}
- Environment: {requirements.environment}
- Region: {requirements.region}

Infrastructure Requirements:
- Expected Traffic: {requirements.expected_traffic}
- Database Required: {requirements.database_required}
- Cache Required: {requirements.cache_required}
- Compliance: {', '.join(requirements.compliance_requirements) or 'None'}

Generate EXACTLY 5 files:

1. **main.tf** - Main infrastructure resources
2. **variables.tf** - Input variables with defaults
3. **outputs.tf** - Output values (must include app_url)
4. **versions.tf** - Provider versions
5. **terraform-deploy.yml** - GitHub Actions workflow for deployment

IMPORTANT REQUIREMENTS:

For Terraform files:
- Use Azure provider version ~> 3.0
- Include app_url output that returns the application URL
- Use variables for all configurable values
- Add appropriate resource sizing based on traffic expectations ({requirements.expected_traffic})
- Include monitoring and logging resources
- Add security best practices
- Use consistent naming with app_name variable
- Include tags for resource management
- Add conditional resources based on database/cache requirements
- CRITICAL: Use data sources to reference existing resources when possible
- CRITICAL: Add lifecycle rules to prevent destruction of important resources
- CRITICAL: Use try() function for optional outputs to handle missing resources gracefully
- CRITICAL: Include import blocks for resources that might already exist
- CRITICAL: Add appropriate app_command_line INSIDE site_config block based on app type:
  * Static React/Vue/Angular: app_command_line = "npx serve -s ."
  * Node.js Express: app_command_line = "npm start"
  * Python FastAPI: app_command_line = "uvicorn app:app --host 0.0.0.0 --port 8000"
  * Python Flask: app_command_line = "python app.py"
  * Python Django: app_command_line = "python manage.py runserver 0.0.0.0:8000"
  * Java: app_command_line = "java -jar app.jar"
- CRITICAL: Set correct application_stack (node_version, python_version, java_version) INSIDE site_config block
- CRITICAL: Add app_settings block with environment variables and build settings:
  * Python: SCM_DO_BUILD_DURING_DEPLOYMENT = "true", ENABLE_ORYX_BUILD = "true"
  * Node.js: WEBSITE_NODE_DEFAULT_VERSION = "18-lts", WEBSITE_RUN_FROM_PACKAGE = "1"
  * All: WEBSITES_ENABLE_APP_SERVICE_STORAGE = "false", WEBSITE_HTTPLOGGING_RETENTION_DAYS = "7"

For resource group handling:
- Use data source to check if resource group exists first
- Only create resource group if it doesn't exist
- Use lifecycle prevent_destroy for production environments

For GitHub Actions workflow:
- Name: "🏗️ Terraform Infrastructure Deployment"
- Trigger: workflow_dispatch with approval_id input
- Use ubuntu-latest runner
- Include these environment variables:
  - TF_VERSION: "1.6.0"
  - ARM_CLIENT_ID: ${{{{ secrets.AZURE_CLIENT_ID }}}}
  - ARM_CLIENT_SECRET: ${{{{ secrets.AZURE_CLIENT_SECRET }}}}
  - ARM_SUBSCRIPTION_ID: ${{{{ secrets.AZURE_SUBSCRIPTION_ID }}}}
  - ARM_TENANT_ID: ${{{{ secrets.AZURE_TENANT_ID }}}}
- Include these steps in order:
  1. Checkout code (actions/checkout@v4)
  2. Setup Terraform (hashicorp/setup-terraform@v3)
  3. Azure Login (azure/login@v1)
  4. Terraform Format Check
  5. Terraform Validate
  6. Terraform Init (with backend config if needed)
  7. Terraform Plan (using variables from variables.tf)
  8. Terraform Apply
  9. Export Outputs (based on outputs.tf)
  10. Deployment Summary
- Use working-directory: ./terraform for all terraform commands
- Include proper error handling and status reporting
- Use emojis in step names for better UX
- Add step to handle existing resources (terraform import if needed)

Format the response EXACTLY as follows:

```hcl
// main.tf
[Complete main.tf content here with data sources and lifecycle rules]

// variables.tf  
[Complete variables.tf content here]

// outputs.tf
[Complete outputs.tf content here with try() functions]

// versions.tf
[Complete versions.tf content here]
```

```yaml
// terraform-deploy.yml
[Complete GitHub Actions workflow YAML content here with import handling]
```

Ensure all files are complete, production-ready, handle existing resources gracefully, and work together seamlessly.
"""
    
    def _parse_terraform_response(self, response: str) -> Dict[str, str]:
        """Parse AI response into separate Terraform files and GitHub Actions workflow."""
        files = {}
        
        # Extract HCL code blocks (Terraform files)
        hcl_pattern = r'// (\w+\.tf)\s*\n```(?:hcl)?\s*\n(.*?)\n```'
        hcl_matches = re.findall(hcl_pattern, response, re.DOTALL)
        
        for filename, content in hcl_matches:
            files[filename] = content.strip()
        
        # Extract YAML code blocks (GitHub Actions workflow)
        yaml_pattern = r'// ([\w-]+\.yml)\s*\n```(?:yaml)?\s*\n(.*?)\n```'
        yaml_matches = re.findall(yaml_pattern, response, re.DOTALL)
        
        for filename, content in yaml_matches:
            files[filename] = content.strip()
        
        # Alternative patterns if the above don't work
        if not files:
            # Try broader patterns
            patterns = [
                r'// (\w+\.(?:tf|yml))\s*\n(.*?)(?=\n// \w+\.(?:tf|yml)|$)',  # Alternative pattern
                r'\*\*(\w+\.(?:tf|yml))\*\*[^\n]*\n```(?:hcl|yaml)?\s*\n(.*?)\n```'  # Bold filename pattern
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, response, re.DOTALL)
                for filename, content in matches:
                    if filename not in files:  # Don't overwrite if already found
                        files[filename] = content.strip()
        
        # If still no files found, try to extract from simple code blocks
        if not files:
            # Look for any code blocks and try to identify by content
            all_blocks = re.findall(r'```(?:hcl|yaml|terraform)?\s*\n(.*?)\n```', response, re.DOTALL)
            
            for i, block in enumerate(all_blocks):
                block = block.strip()
                if not block:
                    continue
                    
                # Try to identify file type by content
                if block.startswith('name:') and ('on:' in block or 'jobs:' in block):
                    files['terraform-deploy.yml'] = block
                elif 'terraform {' in block and 'required_providers' in block:
                    files['versions.tf'] = block
                elif 'variable "' in block:
                    files['variables.tf'] = block
                elif 'output "' in block:
                    files['outputs.tf'] = block
                elif 'resource "' in block or 'data "' in block:
                    files['main.tf'] = block
        
        # Ensure we have at least main.tf
        if 'main.tf' not in files:
            files['main.tf'] = self._extract_main_tf_fallback(response)
        
        # Validate and clean up files
        for filename, content in files.items():
            if content:
                # Remove any remaining comment markers
                content = re.sub(r'^\s*//.*?\n', '', content, flags=re.MULTILINE)
                files[filename] = content.strip()
        
        return files
    
    def _enhance_security(
        self, 
        config_files: Dict[str, str], 
        requirements: InfrastructureRequirements
    ) -> Dict[str, str]:
        """Add security enhancements to Terraform configuration."""
        
        # Add security.tf with common security resources
        security_config = f"""
# Security configurations
resource "azurerm_key_vault" "{requirements.app_type}_kv" {{
  name                = "${{var.app_name}}-kv-${{random_string.suffix.result}}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"
  
  purge_protection_enabled = {str(requirements.environment == 'prod').lower()}
  
  tags = var.common_tags
}}

resource "azurerm_log_analytics_workspace" "main" {{
  name                = "${{var.app_name}}-logs"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = {30 if requirements.environment == 'dev' else 90}
  
  tags = var.common_tags
}}

resource "random_string" "suffix" {{
  length  = 8
  special = false
  upper   = false
}}

data "azurerm_client_config" "current" {{}}
"""
        
        config_files['security.tf'] = security_config
        
        return config_files
    
    def _generate_fallback_config(
        self, 
        requirements: InfrastructureRequirements, 
        app_name: str
    ) -> Dict[str, str]:
        """Generate fallback Terraform configuration."""
        
        # Determine resource sizing based on traffic
        sku_map = {
            'low': 'B1',
            'medium': 'S1', 
            'high': 'P1v2'
        }
        
        sku = sku_map.get(requirements.expected_traffic, 'B1')
        
        # Determine startup command based on app type and language
        startup_command = self._get_startup_command(requirements)
        
        # Determine app settings based on language and framework
        app_settings = self._get_app_settings(requirements)
        
        main_tf = f"""
provider "azurerm" {{
  features {{}}
}}

# Generate unique suffix for resource names
resource "random_string" "suffix" {{
  length  = 8
  special = false
  upper   = false
}}

# Data source to check if resource group exists
data "azurerm_resource_group" "existing" {{
  name = var.resource_group_name
  
  # This will return null if RG doesn't exist
  count = 0  # We'll handle this with try() in locals
}}

# Local values for conditional resource creation
locals {{
  # Check if resource group exists
  rg_exists = can(data.azurerm_resource_group.existing)
  
  # Use existing RG if it exists, otherwise create new one
  resource_group_name = var.resource_group_name
  location = var.location
}}

# Create resource group only if it doesn't exist
resource "azurerm_resource_group" "main" {{
  name     = local.resource_group_name
  location = local.location
  
  tags = var.common_tags
  
  lifecycle {{
    # Prevent accidental deletion in production
    prevent_destroy = {str(requirements.environment == 'prod').lower()}
    # Ignore changes to tags that might be added externally
    ignore_changes = [tags]
  }}
}}

resource "azurerm_service_plan" "main" {{
  name                = "${{var.app_name}}-plan-${{random_string.suffix.result}}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "{sku}"
  
  tags = var.common_tags
}}

resource "azurerm_linux_web_app" "main" {{
  name                = "${{var.app_name}}-${{random_string.suffix.result}}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_service_plan.main.location
  service_plan_id     = azurerm_service_plan.main.id
  
  site_config {{
    always_on = {str(requirements.environment != 'dev').lower()}
    
    {startup_command}
    
    application_stack {{
      {"python_version = \"3.11\"" if requirements.language == "python" else ""}
      {"node_version = \"18-lts\"" if requirements.language in ["javascript", "typescript"] else ""}
      {"java_version = \"17\"" if requirements.language == "java" else ""}
    }}
  }}
  
  app_settings = {{
    {app_settings}
  }}
  
  tags = var.common_tags
}}
"""
        
        variables_tf = f"""
variable "app_name" {{
  description = "Application name"
  type        = string
  default     = "{app_name}"
  
  validation {{
    condition     = can(regex("^[a-zA-Z0-9-]{{1,60}}$", var.app_name))
    error_message = "App name must be 1-60 characters and contain only letters, numbers, and hyphens."
  }}
}}

variable "resource_group_name" {{
  description = "Resource group name"
  type        = string
  default     = "{app_name}-rg"
  
  validation {{
    condition     = can(regex("^[a-zA-Z0-9._()-]{{1,90}}$", var.resource_group_name))
    error_message = "Resource group name must be 1-90 characters."
  }}
}}

variable "location" {{
  description = "Azure region"
  type        = string
  default     = "{requirements.region}"
  
  validation {{
    condition = contains([
      "eastus", "eastus2", "westus", "westus2", "westus3",
      "centralus", "northcentralus", "southcentralus",
      "westcentralus", "canadacentral", "canadaeast",
      "brazilsouth", "northeurope", "westeurope",
      "uksouth", "ukwest", "francecentral", "germanywestcentral",
      "norwayeast", "switzerlandnorth", "swedencentral",
      "australiaeast", "australiasoutheast", "southeastasia",
      "eastasia", "japaneast", "japanwest", "koreacentral",
      "southindia", "centralindia", "westindia"
    ], var.location)
    error_message = "Location must be a valid Azure region."
  }}
}}

variable "environment" {{
  description = "Environment name"
  type        = string
  default     = "{requirements.environment}"
  
  validation {{
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }}
}}

variable "common_tags" {{
  description = "Common tags for all resources"
  type        = map(string)
  default = {{
    Environment = "{requirements.environment}"
    Application = "{app_name}"
    ManagedBy   = "Terraform"
    CreatedBy   = "DevOps-Agent"
  }}
}}
"""
        
        outputs_tf = """
output "app_url" {
  description = "Application URL"
  value       = "https://${azurerm_linux_web_app.main.default_hostname}"
}

output "resource_group_name" {
  description = "Resource group name"
  value       = azurerm_resource_group.main.name
}

output "resource_group_id" {
  description = "Resource group ID"
  value       = azurerm_resource_group.main.id
}

output "app_name" {
  description = "Application name with suffix"
  value       = azurerm_linux_web_app.main.name
}

output "service_plan_name" {
  description = "Service plan name"
  value       = azurerm_service_plan.main.name
}
"""
        
        versions_tf = """
terraform {
  required_version = ">= 1.0"
  
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.1"
    }
  }
}
"""
        
        return {
            'main.tf': main_tf,
            'variables.tf': variables_tf,
            'outputs.tf': outputs_tf,
            'versions.tf': versions_tf
        }
    
    def _get_startup_command(self, requirements: InfrastructureRequirements) -> str:
        """Get appropriate startup command based on app type and language."""
        
        # Static frontend apps (React, Vue, Angular)
        if (requirements.app_type in ["spa", "web"] and 
            requirements.language in ["javascript", "typescript"] and
            requirements.framework in ["react", "vue", "angular", "vite", None]):
            return 'app_command_line = "npx serve -s ."'
        
        # Node.js applications
        elif requirements.language in ["javascript", "typescript"]:
            if requirements.framework == "express":
                return 'app_command_line = "npm start"'
            elif requirements.framework == "next":
                return 'app_command_line = "npm run start"'
            else:
                return 'app_command_line = "npm start"'
        
        # Python applications
        elif requirements.language == "python":
            if requirements.framework == "fastapi":
                return 'app_command_line = "uvicorn app:app --host 0.0.0.0 --port 8000"'
            elif requirements.framework == "flask":
                return 'app_command_line = "python app.py"'
            elif requirements.framework == "django":
                return 'app_command_line = "python manage.py runserver 0.0.0.0:8000"'
            else:
                return 'app_command_line = "python app.py"'
        
        # Java applications
        elif requirements.language == "java":
            return 'app_command_line = "java -jar app.jar"'
        
        # Default - let Azure auto-detect
        else:
            return '# app_command_line = "auto-detect"'
    
    def _get_app_settings(self, requirements: InfrastructureRequirements) -> str:
        """Get app settings based on language and framework."""
        
        settings = []
        
        # Common settings for all apps
        settings.extend([
            'WEBSITE_NODE_DEFAULT_VERSION = "18-lts"',
            'WEBSITES_ENABLE_APP_SERVICE_STORAGE = "false"',
            'WEBSITE_HTTPLOGGING_RETENTION_DAYS = "7"'
        ])
        
        # Python-specific settings
        if requirements.language == "python":
            settings.extend([
                'SCM_DO_BUILD_DURING_DEPLOYMENT = "true"',
                'ENABLE_ORYX_BUILD = "true"',
                'POST_BUILD_SCRIPT_PATH = ""',
                'PRE_BUILD_SCRIPT_PATH = ""'
            ])
            
            # Framework-specific settings
            if requirements.framework == "django":
                settings.extend([
                    'DJANGO_SETTINGS_MODULE = "myproject.settings"',
                    'PYTHONPATH = "/home/site/wwwroot"'
                ])
            elif requirements.framework == "flask":
                settings.extend([
                    'FLASK_APP = "app.py"',
                    'FLASK_ENV = "production"'
                ])
            elif requirements.framework == "fastapi":
                settings.extend([
                    'PYTHONPATH = "/home/site/wwwroot"'
                ])
        
        # Node.js specific settings
        elif requirements.language in ["javascript", "typescript"]:
            settings.extend([
                'WEBSITE_NODE_DEFAULT_VERSION = "18-lts"',
                'NPM_CONFIG_PRODUCTION = "false"',
                'WEBSITE_RUN_FROM_PACKAGE = "1"'
            ])
            
            # Framework-specific settings
            if requirements.framework == "next":
                settings.extend([
                    'NEXTJS_BUILD_COMMAND = "npm run build"',
                    'NEXTJS_START_COMMAND = "npm run start"'
                ])
        
        # Java specific settings
        elif requirements.language == "java":
            settings.extend([
                'JAVA_OPTS = "-Dserver.port=80"',
                'WEBSITES_PORT = "80"'
            ])
        
        # Environment-specific settings
        if requirements.environment == "prod":
            settings.extend([
                'WEBSITE_HTTPLOGGING_RETENTION_DAYS = "30"',
                'WEBSITES_ENABLE_APP_SERVICE_STORAGE = "true"'
            ])
        
        # Join settings with proper formatting
        return '\n    '.join(settings)
    
    def _extract_main_tf_fallback(self, response: str) -> str:
        """Extract main.tf content as fallback."""
        # Simple extraction of HCL code blocks
        hcl_pattern = r'```(?:hcl|terraform)?\s*\n(.*?)\n```'
        matches = re.findall(hcl_pattern, response, re.DOTALL)
        
        if matches:
            return matches[0].strip()
        
        return """
# Fallback configuration
resource "azurerm_resource_group" "main" {
  name     = var.app_name
  location = var.location
}
"""