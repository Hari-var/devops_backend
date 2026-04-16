# Deployment Troubleshooting Guide

## Error: HTTP 000 - Application Not Responding

### Symptoms
- Validation fails with HTTP 000
- Application URL returns no response
- Azure shows "Application Error"

### Common Causes & Solutions

#### 1. Application Failed to Start

**Check startup logs:**
```bash
az webapp log tail --name DevOpsAgent-Backend --resource-group <your-rg>
```

**Download all logs:**
```bash
az webapp log download --name DevOpsAgent-Backend --resource-group <your-rg> --log-file logs.zip
```

#### 2. Wrong Startup Command

**Current command:**
```bash
/home/site/wwwroot/startup.sh
```

**If startup.sh fails, try direct command:**
```bash
az webapp config set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --startup-file "gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:8000 --timeout 600 --access-logfile - --error-logfile -"
```

#### 3. Missing Dependencies

**Enable build during deployment:**
```bash
az webapp config appsettings set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true
```

#### 4. Port Binding Issues

Azure expects apps to listen on port **8000** or use the `PORT` environment variable.

**Fix in code (backend/app/main.py):**
```python
import os
port = int(os.getenv("PORT", 8000))
```

**Or in startup command:**
```bash
gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:$PORT
```

#### 5. Module Import Errors

**Check directory structure:**
```
/home/site/wwwroot/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   ├── services/
│   └── ...
├── requirements.txt
└── startup.sh
```

**If structure is wrong, fix deployment package in workflow:**
```yaml
- name: Create deployment package
  run: |
    mkdir -p deploy
    cp -r backend/app deploy/
    cp backend/requirements.txt deploy/
```

#### 6. Database Connection Issues

**For SQLite (development):**
```bash
az webapp config appsettings set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --settings DATABASE_URL="sqlite:////home/site/wwwroot/devops_agent.db"
```

**For PostgreSQL (production):**
```bash
# Create PostgreSQL
az postgres flexible-server create \
  --name devops-backend-db \
  --resource-group <your-rg> \
  --location eastus \
  --admin-user dbadmin \
  --admin-password "YourPassword123!" \
  --sku-name Standard_B1ms

# Update app settings
az webapp config appsettings set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --settings DATABASE_URL="postgresql://dbadmin:YourPassword123!@devops-backend-db.postgres.database.azure.com/postgres"
```

#### 7. Environment Variables Missing

**Required environment variables:**
```bash
az webapp config appsettings set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --settings \
    GITHUB_PERSONAL_ACCESS_TOKEN="<your-pat>" \
    GOOGLE_GEMINI_API_KEY="<your-key>" \
    AZURE_SUBSCRIPTION_ID="<sub-id>" \
    AZURE_TENANT_ID="<tenant-id>" \
    AZURE_CLIENT_ID="<client-id>" \
    AZURE_CLIENT_SECRET="<client-secret>" \
    FRONTEND_URL="https://your-frontend.com" \
    DEV_ALLOW_ALL_CORS="false"
```

## Debugging Steps

### Step 1: Check Application Logs
```bash
az webapp log tail --name DevOpsAgent-Backend --resource-group <your-rg>
```

Look for:
- `ModuleNotFoundError` - Missing dependencies
- `Address already in use` - Port conflict
- `Connection refused` - Database issues
- `ImportError` - Wrong directory structure

### Step 2: Test Locally
```bash
cd backend
pip install -r requirements.txt
pip install gunicorn
gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:8000
```

Visit: http://localhost:8000/api/docs

### Step 3: Check Azure Configuration
```bash
# View current settings
az webapp config show --name DevOpsAgent-Backend --resource-group <your-rg>

# View app settings
az webapp config appsettings list --name DevOpsAgent-Backend --resource-group <your-rg>
```

### Step 4: Enable Detailed Logging
```bash
az webapp log config \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --application-logging filesystem \
  --detailed-error-messages true \
  --failed-request-tracing true \
  --web-server-logging filesystem
```

### Step 5: SSH into Container (Advanced)
```bash
az webapp ssh --name DevOpsAgent-Backend --resource-group <your-rg>
```

Then inside container:
```bash
cd /home/site/wwwroot
ls -la
cat startup.sh
python -m app.main  # Test if app runs
```

## Quick Fixes

### Fix 1: Simplify Startup Command
```bash
az webapp config set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --startup-file "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

### Fix 2: Disable Oryx Build (if causing issues)
```bash
az webapp config appsettings set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=false \
    ENABLE_ORYX_BUILD=false
```

### Fix 3: Increase Timeout
```bash
az webapp config set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --startup-file "gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:8000 --timeout 600"
```

### Fix 4: Use Single Worker (for debugging)
```bash
az webapp config set \
  --name DevOpsAgent-Backend \
  --resource-group <your-rg> \
  --startup-file "gunicorn -w 1 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:8000 --log-level debug"
```

## Validation After Fixes

```bash
# Wait for restart
sleep 30

# Test root endpoint
curl https://DevOpsAgent-Backend.azurewebsites.net

# Test API docs
curl https://DevOpsAgent-Backend.azurewebsites.net/api/docs

# Test health endpoint (if exists)
curl https://DevOpsAgent-Backend.azurewebsites.net/api/health
```

## Still Not Working?

1. **Check Azure Service Health**: https://status.azure.com
2. **Review GitHub Actions logs**: Look for deployment errors
3. **Check resource quotas**: Ensure you haven't hit limits
4. **Try redeploying**: Sometimes a fresh deployment helps
5. **Contact support**: Azure support or open GitHub issue

## Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError: No module named 'app'` | Wrong directory structure | Fix deployment package structure |
| `Address already in use` | Port conflict | Change port or restart app |
| `Connection refused` | Database not accessible | Check DATABASE_URL and firewall |
| `Application Error` | App crashed on startup | Check logs for Python errors |
| `502 Bad Gateway` | App not responding | Check startup command and port |
| `503 Service Unavailable` | App still starting | Wait longer, increase timeout |
