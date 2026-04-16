# GitHub Actions CI/CD Setup Guide

## Overview
This repository contains three GitHub Actions workflows for deploying the DevOps Backend to Azure Web Apps:

1. **ci.yml** - Continuous Integration (build, test, create artifact)
2. **cd.yml** - Continuous Deployment (deploy artifact to Azure)
3. **deploy.yml** - Combined CI/CD in single workflow

## Prerequisites

### 1. Azure Resources
Create an Azure Web App for Python:

```bash
# Login to Azure
az login

# Create resource group
az group create --name devops-backend-rg --location eastus

# Create App Service Plan (Linux)
az appservice plan create \
  --name devops-backend-plan \
  --resource-group devops-backend-rg \
  --sku B1 \
  --is-linux

# Create Web App
az webapp create \
  --name devops-backend-app \
  --resource-group devops-backend-rg \
  --plan devops-backend-plan \
  --runtime "PYTHON:3.11"
```

### 2. Azure Service Principal
Create credentials for GitHub Actions:

```bash
# Create service principal and get credentials
az ad sp create-for-rbac \
  --name "github-actions-devops-backend" \
  --role contributor \
  --scopes /subscriptions/{subscription-id}/resourceGroups/devops-backend-rg \
  --sdk-auth
```

This outputs JSON like:
```json
{
  "clientId": "xxx",
  "clientSecret": "xxx",
  "subscriptionId": "xxx",
  "tenantId": "xxx",
  "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
  "resourceManagerEndpointUrl": "https://management.azure.com/",
  "activeDirectoryGraphResourceId": "https://graph.windows.net/",
  "sqlManagementEndpointUrl": "https://management.core.windows.net:8443/",
  "galleryEndpointUrl": "https://gallery.azure.com/",
  "managementEndpointUrl": "https://management.core.windows.net/"
}
```

### 3. GitHub Secrets
Add these secrets to your GitHub repository (Settings → Secrets and variables → Actions):

| Secret Name | Value | Description |
|-------------|-------|-------------|
| `AZURE_CREDENTIALS` | Full JSON output from step 2 | Azure service principal credentials |
| `AZURE_RESOURCE_GROUP` | `devops-backend-rg` | Azure resource group name |

### 4. Update Workflow Variables
Edit the workflow files and update:

```yaml
env:
  AZURE_WEBAPP_NAME: devops-backend-app  # Change to your app name
  PYTHON_VERSION: '3.11'
```

## Workflow Options

### Option 1: Separate CI/CD (Recommended for production)
- **ci.yml** runs on every push/PR
- **cd.yml** runs only when CI succeeds on main branch
- Better separation of concerns
- Faster feedback on PRs (CI only)

### Option 2: Combined CI/CD
- **deploy.yml** runs build and deploy in one workflow
- Simpler setup
- Good for smaller projects
- Only runs on main branch

## Environment Variables

The application requires these environment variables in Azure:

```bash
# Set via Azure Portal or CLI
az webapp config appsettings set \
  --name devops-backend-app \
  --resource-group devops-backend-rg \
  --settings \
    GITHUB_PERSONAL_ACCESS_TOKEN="your_github_pat" \
    GOOGLE_GEMINI_API_KEY="your_gemini_key" \
    AZURE_SUBSCRIPTION_ID="your_subscription_id" \
    AZURE_TENANT_ID="your_tenant_id" \
    AZURE_CLIENT_ID="your_client_id" \
    AZURE_CLIENT_SECRET="your_client_secret" \
    DATABASE_URL="sqlite:///./devops_agent.db" \
    FRONTEND_URL="https://your-frontend.com" \
    DEV_ALLOW_ALL_CORS="false"
```

## Startup Command

The workflows configure this startup command:
```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000
```

If you need to change it:
```bash
az webapp config set \
  --name devops-backend-app \
  --resource-group devops-backend-rg \
  --startup-file "your-custom-command"
```

## Deployment Validation

All workflows include validation steps that:
1. Wait for deployment to complete (30-45 seconds)
2. Test the `/api/docs` endpoint
3. Retry up to 10-12 times
4. Report success/failure

## Monitoring Deployment

### View logs in real-time:
```bash
az webapp log tail \
  --name devops-backend-app \
  --resource-group devops-backend-rg
```

### Check deployment status:
```bash
az webapp deployment list \
  --name devops-backend-app \
  --resource-group devops-backend-rg
```

### Access your application:
- **API Docs**: https://devops-backend-app.azurewebsites.net/api/docs
- **OpenAPI**: https://devops-backend-app.azurewebsites.net/api/openapi.json

## Troubleshooting

### Deployment fails with "Application Error"
Check startup logs:
```bash
az webapp log download \
  --name devops-backend-app \
  --resource-group devops-backend-rg \
  --log-file logs.zip
```

### Module import errors
Ensure `requirements.txt` includes all dependencies and `gunicorn` is installed.

### Database issues
For production, use PostgreSQL instead of SQLite:
```bash
# Create PostgreSQL
az postgres flexible-server create \
  --name devops-backend-db \
  --resource-group devops-backend-rg \
  --location eastus \
  --admin-user dbadmin \
  --admin-password "YourPassword123!" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --version 14

# Update DATABASE_URL
DATABASE_URL="postgresql://dbadmin:YourPassword123!@devops-backend-db.postgres.database.azure.com/postgres"
```

### CORS issues
Update `FRONTEND_URL` in Azure App Settings or enable `DEV_ALLOW_ALL_CORS=true` for testing.

## Cost Optimization

- **B1 Plan**: ~$13/month (suitable for development)
- **F1 Free Plan**: $0/month (limited, good for testing)
- **P1V2 Plan**: ~$73/month (production-ready)

Change plan:
```bash
az appservice plan update \
  --name devops-backend-plan \
  --resource-group devops-backend-rg \
  --sku F1  # or B1, P1V2, etc.
```

## Security Best Practices

1. **Never commit secrets** - Use GitHub Secrets and Azure Key Vault
2. **Rotate credentials** - Update service principal secrets regularly
3. **Limit CORS** - Set specific `FRONTEND_URL`, disable `DEV_ALLOW_ALL_CORS`
4. **Enable HTTPS only** - Azure Web Apps enforce this by default
5. **Use managed identities** - For Azure resource access (advanced)

## Next Steps

1. Push code to GitHub
2. Workflows run automatically
3. Monitor in Actions tab
4. Access deployed app at Azure URL
5. Configure custom domain (optional)
6. Set up Application Insights for monitoring (optional)
