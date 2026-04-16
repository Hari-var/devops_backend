#!/usr/bin/env python3
"""
Azure Resource Import Helper
This script helps import existing Azure resources into Terraform state to avoid conflicts.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path


async def import_existing_resources():
    """Import existing Azure resources into Terraform state."""
    
    print("Azure Resource Import Helper")
    print("=" * 50)
    
    # Check if we're in a directory with Terraform files
    terraform_dir = Path("./terraform")
    if not terraform_dir.exists():
        print("No ./terraform directory found")
        print("Run this script from your project root directory")
        return
    
    # Check for main.tf
    main_tf = terraform_dir / "main.tf"
    if not main_tf.exists():
        print("No main.tf found in ./terraform directory")
        return
    
    print("Found Terraform configuration")
    
    # Check Azure CLI
    try:
        result = subprocess.run(["az", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            print("Azure CLI not found or not working")
            print("Install Azure CLI: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli")
            return
        print("Azure CLI is available")
    except FileNotFoundError:
        print("Azure CLI not found")
        print("Install Azure CLI: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli")
        return
    
    # Check if logged into Azure
    try:
        result = subprocess.run(["az", "account", "show"], capture_output=True, text=True)
        if result.returncode != 0:
            print("Not logged into Azure")
            print("Run: az login")
            return
        print("Logged into Azure")
    except Exception:
        print("Error checking Azure login status")
        return
    
    # Get resource group name from user
    rg_name = input("\nEnter the existing resource group name: ").strip()
    if not rg_name:
        print("Resource group name is required")
        return
    
    # Check if resource group exists
    try:
        result = subprocess.run(
            ["az", "group", "show", "--name", rg_name], 
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"Resource group '{rg_name}' not found in Azure")
            return
        print(f"Found resource group '{rg_name}' in Azure")
    except Exception as e:
        print(f"Error checking resource group: {e}")
        return
    
    # Get subscription ID
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"], 
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("Could not get subscription ID")
            return
        subscription_id = result.stdout.strip()
        print(f"Using subscription: {subscription_id}")
    except Exception as e:
        print(f"Error getting subscription ID: {e}")
        return
    
    # Change to terraform directory
    os.chdir(terraform_dir)
    
    # Initialize Terraform if needed
    print("\nInitializing Terraform...")
    try:
        result = subprocess.run(["terraform", "init"], capture_output=True, text=True)
        if result.returncode != 0:
            print("Terraform init failed:")
            print(result.stdout)
            print(result.stderr)
            return
        print("Terraform initialized")
    except FileNotFoundError:
        print("Terraform not found")
        print("Install Terraform: https://www.terraform.io/downloads.html")
        return
    except Exception as e:
        print(f"Error running terraform init: {e}")
        return
    
    # Import the resource group
    resource_id = f"/subscriptions/{subscription_id}/resourceGroups/{rg_name}"
    
    print(f"\nImporting resource group into Terraform state...")
    print(f"Resource ID: {resource_id}")
    
    try:
        result = subprocess.run([
            "terraform", "import", 
            "azurerm_resource_group.main", 
            resource_id
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("Resource group imported successfully!")
            print("\nYou can now run 'terraform plan' and 'terraform apply' without conflicts")
            
            # Show next steps
            print("\nNext Steps:")
            print("1. Run 'terraform plan' to see what will be created")
            print("2. Run 'terraform apply' to create remaining resources")
            print("3. The existing resource group will be managed by Terraform")
            
        else:
            print("Import failed:")
            print(result.stdout)
            print(result.stderr)
            
            # Check if already imported
            if "already managed" in result.stderr.lower():
                print("\nResource group is already in Terraform state")
                print("You can proceed with 'terraform plan' and 'terraform apply'")
            else:
                print("\nPossible solutions:")
                print("• Check that the resource group name is correct")
                print("• Ensure you have permissions to the resource group")
                print("• Verify the Terraform resource name matches (azurerm_resource_group.main)")
                
    except Exception as e:
        print(f"Error during import: {e}")
        return


if __name__ == "__main__":
    asyncio.run(import_existing_resources())