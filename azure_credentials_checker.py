#!/usr/bin/env python3
"""
Azure Credentials Checker
Run this to verify your Azure credentials are properly configured.
"""

import os
import json


def check_azure_credentials():
    """Check Azure credentials from environment variables."""
    
    print("Azure Credentials Checker")
    print("=" * 40)
    
    # Check environment variables
    env_creds = {
        "AZURE_CLIENT_ID": os.getenv("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET": os.getenv("AZURE_CLIENT_SECRET"), 
        "AZURE_SUBSCRIPTION_ID": os.getenv("AZURE_SUBSCRIPTION_ID"),
        "AZURE_TENANT_ID": os.getenv("AZURE_TENANT_ID")
    }
    
    print("Environment Variables:")
    for key, value in env_creds.items():
        if value:
            print(f"{key}: {value[:8]}...")
        else:
            print(f"{key}: Not set")
    
    # Check if all required credentials are present
    missing_creds = [key for key, value in env_creds.items() if not value]
    
    if missing_creds:
        print(f"\nMissing credentials: {', '.join(missing_creds)}")
        print("\nTo fix this:")
        print("1. Create a service principal in Azure:")
        print("   az ad sp create-for-rbac --name 'devops-agent' --role contributor")
        print("\n2. Set environment variables:")
        for cred in missing_creds:
            print(f"   export {cred}=your_value_here")
        print("\n3. Or add them to your config.py file")
    else:
        print("\nAll Azure credentials are configured!")
        
        # Test creating AZURE_CREDENTIALS JSON
        try:
            azure_creds = {
                "clientId": env_creds["AZURE_CLIENT_ID"],
                "clientSecret": env_creds["AZURE_CLIENT_SECRET"],
                "subscriptionId": env_creds["AZURE_SUBSCRIPTION_ID"],
                "tenantId": env_creds["AZURE_TENANT_ID"]
            }
            
            azure_creds_json = json.dumps(azure_creds, indent=2)
            print("\nAZURE_CREDENTIALS JSON format:")
            print(azure_creds_json)
            
        except Exception as e:
            print(f"\nError creating AZURE_CREDENTIALS JSON: {e}")
    
    print("\n" + "=" * 40)
    print("Useful Links:")
    print("• Azure CLI: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli")
    print("• Service Principal: https://docs.microsoft.com/en-us/azure/active-directory/develop/howto-create-service-principal-portal")
    print("• GitHub Actions Azure: https://github.com/Azure/login")


if __name__ == "__main__":
    check_azure_credentials()