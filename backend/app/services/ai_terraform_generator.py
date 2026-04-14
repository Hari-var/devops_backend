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
        """Generate Terraform configuration using Google Gemini."""
        
        prompt = self._build_prompt(requirements, app_name)
        
        try:
            # Generate content using Gemini
            response = await self._generate_async(prompt)
            
            # Extract and validate Terraform configuration
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
        """Build AI prompt for Terraform generation."""
        return f"""
You are an expert DevOps engineer specializing in Terraform and Azure infrastructure. Generate secure, production-ready Terraform configurations.

Generate a complete Terraform configuration for deploying a {requirements.app_type} application with the following requirements:

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

Generate the following files:
1. main.tf - Main infrastructure resources
2. variables.tf - Input variables
3. outputs.tf - Output values
4. versions.tf - Provider versions

Requirements:
- Use Azure provider
- Include appropriate resource sizing based on traffic expectations
- Add monitoring and logging
- Include security best practices
- Add tags for resource management
- Use data sources where appropriate
- Include conditional resources based on requirements

Format the response as:
```hcl
// main.tf
[main.tf content]

// variables.tf  
[variables.tf content]

// outputs.tf
[outputs.tf content]

// versions.tf
[versions.tf content]
```
"""
    
    def _parse_terraform_response(self, response: str) -> Dict[str, str]:
        """Parse AI response into separate Terraform files."""
        files = {}
        
        # Extract code blocks
        pattern = r'// (\w+\.tf)\s*\n```(?:hcl)?\s*\n(.*?)\n```'
        matches = re.findall(pattern, response, re.DOTALL)
        
        for filename, content in matches:
            files[filename] = content.strip()
        
        # Ensure we have at least main.tf
        if 'main.tf' not in files:
            files['main.tf'] = self._extract_main_tf_fallback(response)
        
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
        
        main_tf = f"""
terraform {{
  required_providers {{
    azurerm = {{
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }}
    random = {{
      source  = "hashicorp/random"
      version = "~> 3.1"
    }}
  }}
}}

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
  
  # This will fail if RG doesn't exist, which is fine
  count = 0  # We'll create it anyway
}}

# Always create the resource group (idempotent)
resource "azurerm_resource_group" "main" {{
  name     = var.resource_group_name
  location = var.location
  
  tags = var.common_tags
  
  lifecycle {{
    # Prevent accidental deletion
    prevent_destroy = {str(requirements.environment == 'prod').lower()}
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
    
    application_stack {{
      {"python_version = \"3.11\"" if requirements.language == "python" else ""}
      {"node_version = \"18-lts\"" if requirements.language == "javascript" else ""}
      {"java_version = \"17\"" if requirements.language == "java" else ""}
    }}
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
        
        return {
            'main.tf': main_tf,
            'variables.tf': variables_tf,
            'outputs.tf': outputs_tf
        }
    
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