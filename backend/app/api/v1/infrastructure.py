from __future__ import annotations

import asyncio
import logging
import os
import random
import string

from azure.core.exceptions import AzureError
from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    HardwareProfile,
    ImageReference,
    LinuxConfiguration,
    ManagedDiskParameters,
    NetworkInterfaceReference,
    NetworkProfile,
    OSDisk,
    OSProfile,
    StorageProfile,
    VirtualMachine,
)
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.containerservice.models import (
    ManagedCluster,
    ManagedClusterAgentPoolProfile,
)
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.network.models import (
    AddressSpace,
    NetworkInterface,
    NetworkInterfaceIPConfiguration,
    PublicIPAddress,
    Subnet,
    VirtualNetwork,
)
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.web import WebSiteManagementClient
from azure.mgmt.web.models import AppServicePlan, Site, SiteConfig, SkuDescription
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

_SKU_TIER_MAP: dict[str, str] = {
    "F1": "Free",
    "B1": "Basic", "B2": "Basic", "B3": "Basic",
    "S1": "Standard", "S2": "Standard", "S3": "Standard",
    "P1v3": "PremiumV3", "P2v3": "PremiumV3",
}


class InfrastructureProvisionRequest(BaseModel):
    repoFullName: str
    branch: str
    infrastructure: dict


def _get_azure_credential() -> tuple[ClientSecretCredential, str]:
    """Build Azure credential from environment variables or raise RuntimeError."""
    client_id = os.getenv("AZURE_CLIENT_ID", "")
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")
    tenant_id = os.getenv("AZURE_TENANT_ID", "")
    subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID", "")

    if not all([client_id, client_secret, tenant_id, subscription_id]):
        raise RuntimeError(
            "Azure credentials not configured. "
            "Set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID."
        )
    return ClientSecretCredential(tenant_id, client_id, client_secret), subscription_id


def _unique_name(base: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{base}-{suffix}"


def _ensure_resource_group(
    credential: ClientSecretCredential,
    subscription_id: str,
    rg_name: str,
    location: str,
) -> None:
    rg_client = ResourceManagementClient(credential, subscription_id)
    logger.info("Ensuring resource group '%s' in '%s'", rg_name, location)
    rg_client.resource_groups.create_or_update(rg_name, {"location": location})
    logger.info("Resource group '%s' ready", rg_name)


def _provision_web_app_sync(
    credential: ClientSecretCredential,
    subscription_id: str,
    rg_name: str,
    name: str,
    location: str,
    sku: str,
    linux_fx_version: str = "NODE|20-lts",
) -> dict:
    web_client = WebSiteManagementClient(credential, subscription_id)
    plan_name = f"{name}-plan"
    sku_code = sku.split()[0] if " " in sku else sku
    tier = _SKU_TIER_MAP.get(sku_code, "Basic")

    logger.info("Creating App Service Plan '%s' sku=%s tier=%s", plan_name, sku_code, tier)
    plan = web_client.app_service_plans.begin_create_or_update(
        rg_name,
        plan_name,
        AppServicePlan(
            location=location,
            kind="linux",
            sku=SkuDescription(name=sku_code, tier=tier),
            reserved=True,
        ),
    ).result()
    logger.info("App Service Plan '%s' created", plan.name)

    app_name = name
    try:
        existing = web_client.web_apps.get(rg_name, app_name)
        logger.info("Web App '%s' already exists, skipping creation", app_name)
        return {
            "status": "existing",
            "infrastructure_type": "azure-web-app",
            "resource_name": app_name,
            "resource_group": rg_name,
            "region": location,
            "url": f"https://{existing.default_host_name}",
            "app_service_plan": plan_name,
        }
    except Exception:
        pass

    for attempt in range(3):
        try:
            logger.info("Creating Web App '%s' (attempt %d) fx=%s", app_name, attempt + 1, linux_fx_version)
            app = web_client.web_apps.begin_create_or_update(
                rg_name,
                app_name,
                Site(
                    location=location,
                    server_farm_id=plan.id,
                    site_config=SiteConfig(linux_fx_version=linux_fx_version),
                ),
            ).result()
            break
        except AzureError as exc:
            if "already exists" in str(exc) or "Conflict" in str(exc):
                app_name = _unique_name(name)
                logger.warning("Name conflict — retrying with '%s'", app_name)
            else:
                raise
    else:
        raise AzureError("Could not create Web App after 3 attempts — all names taken.")

    logger.info("Web App '%s' created at %s", app_name, app.default_host_name)
    return {
        "status": "created",
        "infrastructure_type": "azure-web-app",
        "resource_name": app_name,
        "resource_group": rg_name,
        "region": location,
        "url": f"https://{app.default_host_name}",
        "app_service_plan": plan_name,
    }


def _provision_aks_sync(
    credential: ClientSecretCredential,
    subscription_id: str,
    rg_name: str,
    name: str,
    location: str,
    node_count: int,
    node_size: str,
) -> dict:
    aks_client = ContainerServiceClient(credential, subscription_id)
    cluster_name = _unique_name(f"{name}-aks")

    logger.info("Creating AKS cluster '%s' nodes=%d size=%s", cluster_name, node_count, node_size)
    cluster = aks_client.managed_clusters.begin_create_or_update(
        rg_name,
        cluster_name,
        ManagedCluster(
            location=location,
            dns_prefix=name,
            agent_pool_profiles=[
                ManagedClusterAgentPoolProfile(
                    name="nodepool1",
                    count=node_count,
                    vm_size=node_size,
                    mode="System",
                )
            ],
            identity={"type": "SystemAssigned"},
        ),
    ).result()
    logger.info("AKS cluster '%s' created", cluster.name)

    return {
        "status": "created",
        "infrastructure_type": "aks",
        "resource_name": cluster_name,
        "resource_group": rg_name,
        "region": location,
        "node_count": node_count,
        "node_size": node_size,
        "fqdn": cluster.fqdn or "",
    }


def _provision_vm_sync(
    credential: ClientSecretCredential,
    subscription_id: str,
    rg_name: str,
    name: str,
    location: str,
    vm_size: str,
    admin_user: str,
) -> dict:
    network_client = NetworkManagementClient(credential, subscription_id)
    compute_client = ComputeManagementClient(credential, subscription_id)

    vnet_name = f"{name}-vnet"
    subnet_name = f"{name}-subnet"
    pip_name = f"{name}-pip"
    nic_name = f"{name}-nic"
    vm_name = f"{name}-vm"

    logger.info("Creating VNet '%s'", vnet_name)
    network_client.virtual_networks.begin_create_or_update(
        rg_name, vnet_name,
        VirtualNetwork(location=location, address_space=AddressSpace(address_prefixes=["10.0.0.0/16"])),
    ).result()

    subnet = network_client.subnets.begin_create_or_update(
        rg_name, vnet_name, subnet_name, Subnet(address_prefix="10.0.1.0/24")
    ).result()

    pip = network_client.public_ip_addresses.begin_create_or_update(
        rg_name, pip_name,
        PublicIPAddress(location=location, public_ip_allocation_method="Static", sku={"name": "Standard"}),
    ).result()

    nic = network_client.network_interfaces.begin_create_or_update(
        rg_name, nic_name,
        NetworkInterface(
            location=location,
            ip_configurations=[
                NetworkInterfaceIPConfiguration(
                    name="ipconfig1",
                    subnet={"id": subnet.id},
                    public_ip_address={"id": pip.id},
                )
            ],
        ),
    ).result()

    admin_password = os.getenv("VM_ADMIN_PASSWORD", "")
    if not admin_password:
        raise RuntimeError("VM_ADMIN_PASSWORD environment variable is not set.")

    logger.info("Creating VM '%s' size=%s", vm_name, vm_size)
    compute_client.virtual_machines.begin_create_or_update(
        rg_name, vm_name,
        VirtualMachine(
            location=location,
            hardware_profile=HardwareProfile(vm_size=vm_size),
            storage_profile=StorageProfile(
                image_reference=ImageReference(
                    publisher="Canonical",
                    offer="0001-com-ubuntu-server-jammy",
                    sku="22_04-lts",
                    version="latest",
                ),
                os_disk=OSDisk(
                    create_option="FromImage",
                    managed_disk=ManagedDiskParameters(storage_account_type="Standard_LRS"),
                ),
            ),
            os_profile=OSProfile(
                computer_name=vm_name,
                admin_username=admin_user,
                linux_configuration=LinuxConfiguration(disable_password_authentication=False),
                admin_password=admin_password,
            ),
            network_profile=NetworkProfile(
                network_interfaces=[NetworkInterfaceReference(id=nic.id, primary=True)]
            ),
        ),
    ).result()
    logger.info("VM '%s' created", vm_name)

    pip_result = network_client.public_ip_addresses.get(rg_name, pip_name)
    return {
        "status": "created",
        "infrastructure_type": "vm",
        "resource_name": vm_name,
        "resource_group": rg_name,
        "region": location,
        "public_ip": pip_result.ip_address or "pending",
        "admin_user": admin_user,
        "vm_size": vm_size,
    }


@router.post("/provision", summary="Provision Azure infrastructure")
async def provision_infrastructure(
    payload: InfrastructureProvisionRequest,
    gh_token: str | None = Cookie(default=None),
) -> dict:
    """
    Provision Azure infrastructure based on the requested type.

    Supported types:
    - `azure-web-app`: Azure App Service (Linux)
    - `aks`: Azure Kubernetes Service
    - `vm`: Azure Virtual Machine (Ubuntu 22.04)

    Required fields in `infrastructure`:
    - `type`: one of the above
    - `resourceGroup`: Azure resource group name
    - `name`: resource name
    - `region`: Azure region (default: eastus)
    """
    if not gh_token:
        raise HTTPException(status_code=401, detail="Not authenticated with GitHub.")

    config = payload.infrastructure
    infra_type: str = config.get("type", "azure-web-app")
    resource_group: str = config.get("resourceGroup", "").strip()
    name: str = config.get("name", "").strip()
    location: str = config.get("region", "eastus")

    safe_rg = resource_group.replace("\n", "").replace("\r", "")[:100]
    safe_name = name.replace("\n", "").replace("\r", "")[:100]
    safe_loc = location.replace("\n", "").replace("\r", "")[:50]

    if not resource_group or not name:
        raise HTTPException(status_code=400, detail="'resourceGroup' and 'name' are required.")

    try:
        credential, subscription_id = _get_azure_credential()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        await asyncio.to_thread(_ensure_resource_group, credential, subscription_id, safe_rg, safe_loc)

        if infra_type == "azure-web-app":
            sku: str = config.get("sku", "B1")
            linux_fx: str = config.get("linux_fx_version", "NODE|20-lts")
            return await asyncio.to_thread(
                _provision_web_app_sync, credential, subscription_id, safe_rg, safe_name, safe_loc, sku, linux_fx
            )

        if infra_type == "aks":
            node_count = int(config.get("nodeCount", 2))
            node_size: str = config.get("nodeSize", "Standard_D2s_v3")
            return await asyncio.to_thread(
                _provision_aks_sync, credential, subscription_id, safe_rg, safe_name, safe_loc, node_count, node_size
            )

        if infra_type == "vm":
            vm_size: str = config.get("size", "Standard_B2s")
            admin_user: str = config.get("adminUser", "azureuser")
            return await asyncio.to_thread(
                _provision_vm_sync, credential, subscription_id, safe_rg, safe_name, safe_loc, vm_size, admin_user
            )

        raise HTTPException(status_code=400, detail=f"Unknown infrastructure type: '{infra_type}'.")

    except HTTPException:
        raise
    except AzureError as exc:
        logger.exception("Azure error during provisioning")
        raise HTTPException(status_code=502, detail=f"Azure error: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error during provisioning")
        raise HTTPException(status_code=500, detail=f"Provisioning failed: {exc}") from exc
