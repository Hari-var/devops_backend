# Example config.py for Render deployment
# Place this file in the root of your GitHub repository

# ============================================================================
# RENDER DEPLOYMENT - Python FastAPI Example
# ============================================================================

DEPLOY_TARGET = "render"
APP_NAME = "my-fastapi-app"
REGION = "oregon"  # Options: oregon, frankfurt, singapore, ohio
PLAN = "free"  # Options: free, starter, standard, pro

# Optional: Environment variables for your app
ENV_VARS = {
    "DATABASE_URL": "postgresql://user:pass@host:5432/db",
    "SECRET_KEY": "your-secret-key-here",
    "DEBUG": "false",
}

# ============================================================================
# RENDER DEPLOYMENT - Node.js/Express Example
# ============================================================================

# DEPLOY_TARGET = "render"
# APP_NAME = "my-express-app"
# REGION = "oregon"
# PLAN = "free"

# ENV_VARS = {
#     "NODE_ENV": "production",
#     "API_KEY": "your-api-key",
# }

# ============================================================================
# RENDER DEPLOYMENT - React Static Site Example
# ============================================================================

# DEPLOY_TARGET = "render"
# APP_NAME = "my-react-app"
# RENDER_SERVICE_TYPE = "static_site"  # Force static site
# PUBLISH_PATH = "./build"  # or "./dist" for Vite
# REGION = "oregon"

# ============================================================================
# AZURE DEPLOYMENT (Original) - For comparison
# ============================================================================

# DEPLOY_TARGET = "app_service"  # or "vm", "aks"
# APP_NAME = "my-azure-app"
# RESOURCE_GROUP = "devops-rg"
# LOCATION = "eastus"
# APP_SERVICE_SKU = "B1"

# # Azure credentials (optional, can use env vars)
# TENANT_ID = "your-tenant-id"
# SUBSCRIPTION_ID = "your-subscription-id"
# AZURE_CLIENT_ID = "your-client-id"
# AZURE_CLIENT_SECRET = "your-client-secret"
