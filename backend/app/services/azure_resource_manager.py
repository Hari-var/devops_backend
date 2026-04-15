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
            )
            
            if log_func:
                await log_func(f"✅ Resource group '{resource_group_name}' already exists in {rg.location}")
            return True
            
        except ResourceNotFoundError:
            # Resource group doesn't exist, create it
            if log_func:
                await log_func(f"📝 Creating resource group '{resource_group_name}' in {location}")
            
            try:
                rg_params = {
                    'location': location,
                    'tags': tags or {
                        'CreatedBy': 'DevOps-Agent',
                        'Purpose': 'Automated-Deployment'
                    }
                }
                
                await asyncio.to_thread(
                    self.resource_client.resource_groups.create_or_update,
                    resource_group_name,
                    rg_params
                )
                
                if log_func:
                    await log_func(f"✅ Successfully created resource group '{resource_group_name}'")
                return True
                
            except Exception as e:
                if log_func:
                    await log_func(f"❌ Failed to create resource group '{resource_group_name}': {e}")
                logger.error(f"Failed to create resource group {resource_group_name}: {e}")
                return False
        
        except Exception as e:
            if log_func:
                await log_func(f"❌ Error checking resource group '{resource_group_name}': {e}")
            logger.error(f"Error checking resource group {resource_group_name}: {e}")
            return False\n    \n    async def list_resource_groups(self) -> list:
        """List all resource groups in the subscription."""
        try:
            rgs = await asyncio.to_thread(
                lambda: list(self.resource_client.resource_groups.list())
            )
            return [{'name': rg.name, 'location': rg.location, 'tags': rg.tags} for rg in rgs]
        except Exception as e:
            logger.error(f"Failed to list resource groups: {e}")
            return []
    
    async def delete_resource_group(self, resource_group_name: str, log_func=None) -> bool:
        """Delete a resource group (use with caution!)."""
        if log_func:
            await log_func(f"⚠️ Deleting resource group: {resource_group_name}")
        
        try:
            delete_operation = await asyncio.to_thread(
                self.resource_client.resource_groups.begin_delete,
                resource_group_name
            )
            
            # Wait for deletion to complete
            await asyncio.to_thread(delete_operation.wait)
            
            if log_func:
                await log_func(f"✅ Successfully deleted resource group '{resource_group_name}'")
            return True
            
        except ResourceNotFoundError:
            if log_func:
                await log_func(f"ℹ️ Resource group '{resource_group_name}' doesn't exist")
            return True
            
        except Exception as e:
            if log_func:
                await log_func(f"❌ Failed to delete resource group '{resource_group_name}': {e}")
            logger.error(f"Failed to delete resource group {resource_group_name}: {e}")
            return False
    
    async def get_resource_group_info(self, resource_group_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a resource group."""
        try:
            rg = await asyncio.to_thread(
                self.resource_client.resource_groups.get,
                resource_group_name
            )
            
            return {
                'name': rg.name,
                'location': rg.location,
                'tags': rg.tags or {},
                'provisioning_state': rg.provisioning_state,
                'id': rg.id
            }
            
        except ResourceNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Error getting resource group info for {resource_group_name}: {e}")
            return None
    
    def validate_resource_group_name(self, name: str) -> tuple[bool, str]:
        """Validate resource group name according to Azure rules."""
        import re
        
        # Azure resource group naming rules
        if not name:
            return False, "Resource group name cannot be empty"
        
        if len(name) > 90:
            return False, "Resource group name cannot exceed 90 characters"
        
        if not re.match(r'^[a-zA-Z0-9._\-()]+$', name):
            return False, "Resource group name can only contain letters, numbers, periods, underscores, hyphens, and parentheses"
        
        if name.endswith('.'):
            return False, "Resource group name cannot end with a period"
        
        return True, "Valid resource group name"
    
    def suggest_resource_group_name(self, base_name: str, environment: str = "dev") -> str:
        """Suggest a valid resource group name based on base name and environment."""
        import re
        
        # Clean base name (could be app name or original resource group name)
        clean_base_name = re.sub(r'[^a-zA-Z0-9\-]', '', base_name)[:50]
        clean_environment = re.sub(r'[^a-zA-Z0-9\-]', '', environment)[:10]
        
        # If the base name already looks like a resource group (ends with -rg), preserve it
        if clean_base_name.endswith('-rg'):
            suggested_name = clean_base_name
        else:
            # If it doesn't end with -rg, add it
            suggested_name = f"{clean_base_name}-{clean_environment}-rg"
        
        # Ensure it's valid
        is_valid, _ = self.validate_resource_group_name(suggested_name)
        if is_valid:
            return suggested_name
        else:
            # Fallback to a simple name
            return f"devops-{clean_environment}-rg"