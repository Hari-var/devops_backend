# Environment Configuration Guide

## Required Environment Variables

### Google Gemini API (for AI-powered Terraform generation)
```bash
# Get your API key from: https://makersuite.google.com/app/apikey
GOOGLE_GEMINI_API_KEY=your_gemini_api_key_here

# Optional: AI configuration
AI_ENABLED=true
AI_FALLBACK_ON_ERROR=true
```

### GitHub Integration
```bash
# Personal Access Token with repo and workflow permissions
# Get from: https://github.com/settings/tokens
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Optional: Deployment strategy
DEPLOYMENT_STRATEGY=github_actions
```

### Azure Credentials
```bash
# Azure Service Principal credentials
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Database Configuration
```bash
# Database connection string
cdb=postgresql://user:password@host:port/database
# or for SQLite (development)
cdb=sqlite+aiosqlite:///./devops_agent.db
```

## API Key Renewal Instructions

### Google Gemini API Key Expired
If you see "API key expired" errors:

1. **Visit Google AI Studio**: https://makersuite.google.com/app/apikey
2. **Create New API Key**: Click "Create API Key"
3. **Copy the Key**: Copy the generated key (starts with `AIza...`)
4. **Update Environment**: Set `GOOGLE_GEMINI_API_KEY=your_new_key`
5. **Restart Application**: Restart your DevOps Agent

### GitHub Token Issues
If you see GitHub authentication errors:

1. **Visit GitHub Settings**: https://github.com/settings/tokens
2. **Generate New Token**: Click "Generate new token (classic)"
3. **Set Permissions**: Enable `repo`, `workflow`, `admin:repo_hook`
4. **Copy Token**: Copy the generated token (starts with `ghp_`)
5. **Update Environment**: Set `GITHUB_PERSONAL_ACCESS_TOKEN=your_new_token`

## Troubleshooting Common Issues

### Issue: "API key expired"
**Solution**: Renew your Google Gemini API key (see above)

### Issue: "Terraform binary not found"
**Solution**: The system will auto-install Terraform, or install manually:
- **Windows**: `choco install terraform`
- **macOS**: `brew install terraform`
- **Linux**: Download from https://www.terraform.io/downloads.html

### Issue: "Frontend button stuck loading"
**Causes**:
- API timeouts (Google Gemini overloaded)
- Network connectivity issues
- Invalid API keys

**Solutions**:
1. Check browser console for errors
2. Verify API keys are valid and not expired
3. Check network connectivity
4. Use the status endpoint: `GET /api/approvals/{id}/status`

### Issue: "GitHub Actions workflow failed"
**Common Causes**:
- Missing Azure secrets in GitHub repository
- Invalid Terraform configuration
- Azure resource naming conflicts

**Solutions**:
1. Check GitHub Actions logs in your repository
2. Verify Azure credentials are set as GitHub secrets
3. Ensure resource names are unique

## Environment File Example (.env)
```bash
# Copy this to your .env file and fill in your values

# Google Gemini API
GOOGLE_GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AI_ENABLED=true
AI_FALLBACK_ON_ERROR=true

# GitHub Integration  
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEPLOYMENT_STRATEGY=github_actions

# Azure Credentials
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Database
cdb=sqlite+aiosqlite:///./devops_agent.db

# Optional: CORS for development
DEV_ALLOW_ALL_CORS=true
```

## Testing Your Configuration

### 1. Test Google Gemini API
```bash
curl -H "Content-Type: application/json" \
     -d '{"contents":[{"parts":[{"text":"Hello"}]}]}' \
     "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key=YOUR_API_KEY"
```

### 2. Test GitHub API
```bash
curl -H "Authorization: Bearer YOUR_GITHUB_TOKEN" \
     https://api.github.com/user
```

### 3. Test Azure CLI
```bash
az login --service-principal \
  --username $AZURE_CLIENT_ID \
  --password $AZURE_CLIENT_SECRET \
  --tenant $AZURE_TENANT_ID
```

## Security Best Practices

1. **Never commit API keys** to version control
2. **Use environment variables** or secure secret management
3. **Rotate keys regularly** (every 90 days recommended)
4. **Use least privilege** - only grant necessary permissions
5. **Monitor usage** - watch for unexpected API calls
6. **Use GitHub Secrets** for CI/CD credentials