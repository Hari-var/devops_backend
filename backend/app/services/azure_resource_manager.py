"""Azure Resource Group Manager - Ensures resource groups exist before deployment."""
import asyncio
import logging
import os
from typing import Optional, Dict, Any
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError

logger = logging.getLogger(__name__)


class AzureResourceGroupManager:
    """Manages Azure resource groups for DevOps deployments."""
    
    def __init__(self):
        self.credential = DefaultAzureCredential()
        self.subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        
        if not self.subscription_id:
            raise ValueError("AZURE_SUBSCRIPTION_ID environment variable is required")
        
        self.resource_client = ResourceManagementClient(
            self.credential, 
            self.subscription_id
        )
    
    async def ensure_resource_group_exists(
        self, 
        resource_group_name: str, 
        location: str = "eastus",
        tags: Optional[Dict[str, str]] = None,
        log_func=None
    ) -> bool:
        """Ensure a resource group exists, create if it doesn't."""
        
        if log_func:
            await log_func(f"🔍 Checking resource group: {resource_group_name}")
        
        try:
            # Check if resource group exists
            rg = await asyncio.to_thread(
                self.resource_client.resource_groups.get,
                resource_group_name
            )\n            \n            if log_func:\n                await log_func(f\"✅ Resource group '{resource_group_name}' already exists in {rg.location}\")\n            return True\n            \n        except ResourceNotFoundError:\n            # Resource group doesn't exist, create it\n            if log_func:\n                await log_func(f\"📝 Creating resource group '{resource_group_name}' in {location}\")\n            \n            try:\n                rg_params = {\n                    'location': location,\n                    'tags': tags or {\n                        'CreatedBy': 'DevOps-Agent',\n                        'Purpose': 'Automated-Deployment'\n                    }\n                }\n                \n                await asyncio.to_thread(\n                    self.resource_client.resource_groups.create_or_update,\n                    resource_group_name,\n                    rg_params\n                )\n                \n                if log_func:\n                    await log_func(f\"✅ Successfully created resource group '{resource_group_name}'\")\n                return True\n                \n            except Exception as e:\n                if log_func:\n                    await log_func(f\"❌ Failed to create resource group '{resource_group_name}': {e}\")\n                logger.error(f\"Failed to create resource group {resource_group_name}: {e}\")\n                return False\n        \n        except Exception as e:\n            if log_func:\n                await log_func(f\"❌ Error checking resource group '{resource_group_name}': {e}\")\n            logger.error(f\"Error checking resource group {resource_group_name}: {e}\")\n            return False\n    \n    async def list_resource_groups(self) -> list:\n        \"\"\"List all resource groups in the subscription.\"\"\"\n        try:\n            rgs = await asyncio.to_thread(\n                lambda: list(self.resource_client.resource_groups.list())\n            )\n            return [{'name': rg.name, 'location': rg.location, 'tags': rg.tags} for rg in rgs]\n        except Exception as e:\n            logger.error(f\"Failed to list resource groups: {e}\")\n            return []\n    \n    async def delete_resource_group(self, resource_group_name: str, log_func=None) -> bool:\n        \"\"\"Delete a resource group (use with caution!).\"\"\"\n        if log_func:\n            await log_func(f\"⚠️ Deleting resource group: {resource_group_name}\")\n        \n        try:\n            delete_operation = await asyncio.to_thread(\n                self.resource_client.resource_groups.begin_delete,\n                resource_group_name\n            )\n            \n            # Wait for deletion to complete\n            await asyncio.to_thread(delete_operation.wait)\n            \n            if log_func:\n                await log_func(f\"✅ Successfully deleted resource group '{resource_group_name}'\")\n            return True\n            \n        except ResourceNotFoundError:\n            if log_func:\n                await log_func(f\"ℹ️ Resource group '{resource_group_name}' doesn't exist\")\n            return True\n            \n        except Exception as e:\n            if log_func:\n                await log_func(f\"❌ Failed to delete resource group '{resource_group_name}': {e}\")\n            logger.error(f\"Failed to delete resource group {resource_group_name}: {e}\")\n            return False\n    \n    async def get_resource_group_info(self, resource_group_name: str) -> Optional[Dict[str, Any]]:\n        \"\"\"Get detailed information about a resource group.\"\"\"\n        try:\n            rg = await asyncio.to_thread(\n                self.resource_client.resource_groups.get,\n                resource_group_name\n            )\n            \n            return {\n                'name': rg.name,\n                'location': rg.location,\n                'tags': rg.tags or {},\n                'provisioning_state': rg.provisioning_state,\n                'id': rg.id\n            }\n            \n        except ResourceNotFoundError:\n            return None\n        except Exception as e:\n            logger.error(f\"Error getting resource group info for {resource_group_name}: {e}\")\n            return None\n    \n    def validate_resource_group_name(self, name: str) -> tuple[bool, str]:\n        \"\"\"Validate resource group name according to Azure rules.\"\"\"\n        import re\n        \n        # Azure resource group naming rules\n        if not name:\n            return False, \"Resource group name cannot be empty\"\n        \n        if len(name) > 90:\n            return False, \"Resource group name cannot exceed 90 characters\"\n        \n        if not re.match(r'^[a-zA-Z0-9._\\-()]+$', name):\n            return False, \"Resource group name can only contain letters, numbers, periods, underscores, hyphens, and parentheses\"\n        \n        if name.endswith('.'):\n            return False, \"Resource group name cannot end with a period\"\n        \n        return True, \"Valid resource group name\"\n    \n    def suggest_resource_group_name(self, app_name: str, environment: str = \"dev\") -> str:\n        \"\"\"Suggest a valid resource group name based on app name and environment.\"\"\"\n        import re\n        \n        # Clean app name\n        clean_app_name = re.sub(r'[^a-zA-Z0-9\\-]', '', app_name)[:50]\n        clean_environment = re.sub(r'[^a-zA-Z0-9\\-]', '', environment)[:10]\n        \n        suggested_name = f\"{clean_app_name}-{clean_environment}-rg\"\n        \n        # Ensure it's valid\n        is_valid, _ = self.validate_resource_group_name(suggested_name)\n        if is_valid:\n            return suggested_name\n        else:\n            # Fallback to a simple name\n            return f\"devops-{clean_environment}-rg\"