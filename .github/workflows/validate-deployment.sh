#!/bin/bash
# Azure Web App Deployment Validation Script

APP_NAME=$1
RESOURCE_GROUP=$2

if [ -z "$APP_NAME" ] || [ -z "$RESOURCE_GROUP" ]; then
    echo "Usage: $0 <app-name> <resource-group>"
    exit 1
fi

APP_URL="https://${APP_NAME}.azurewebsites.net"

echo "=========================================="
echo "Validating Azure Web App Deployment"
echo "=========================================="
echo "App Name: $APP_NAME"
echo "Resource Group: $RESOURCE_GROUP"
echo "App URL: $APP_URL"
echo ""

# Step 1: Check if app exists
echo "[1/5] Checking if Web App exists..."
if az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
    echo "✓ Web App exists"
else
    echo "✗ Web App not found"
    exit 1
fi

# Step 2: Check app state
echo ""
echo "[2/5] Checking Web App state..."
STATE=$(az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --query "state" -o tsv)
echo "State: $STATE"
if [ "$STATE" != "Running" ]; then
    echo "⚠ Web App is not running. Starting..."
    az webapp start --name "$APP_NAME" --resource-group "$RESOURCE_GROUP"
    sleep 10
fi

# Step 3: Wait for app to be accessible
echo ""
echo "[3/5] Waiting for app to respond..."
MAX_ATTEMPTS=20
ATTEMPT=0
APP_RESPONDING=false

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL" 2>/dev/null || echo "000")
    
    if [ "$HTTP_CODE" != "000" ] && [ "$HTTP_CODE" != "503" ]; then
        echo "✓ App is responding (HTTP $HTTP_CODE)"
        APP_RESPONDING=true
        break
    fi
    
    ATTEMPT=$((ATTEMPT + 1))
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS: HTTP $HTTP_CODE - Waiting 5s..."
    sleep 5
done

if [ "$APP_RESPONDING" = "false" ]; then
    echo "✗ App failed to respond after $MAX_ATTEMPTS attempts"
    echo ""
    echo "Checking application logs..."
    az webapp log tail --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --timeout 30 || true
    echo ""
    echo "Download full logs with:"
    echo "az webapp log download --name $APP_NAME --resource-group $RESOURCE_GROUP"
    exit 1
fi

# Step 4: Check API endpoints
echo ""
echo "[4/5] Checking API endpoints..."
API_SUCCESS=false

# Check /api/docs
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/api/docs" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✓ /api/docs is accessible (HTTP $HTTP_CODE)"
    API_SUCCESS=true
else
    echo "✗ /api/docs returned HTTP $HTTP_CODE (expected 200)"
fi

# Check /api/openapi.json
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/api/openapi.json" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✓ /api/openapi.json is accessible (HTTP $HTTP_CODE)"
    API_SUCCESS=true
else
    echo "✗ /api/openapi.json returned HTTP $HTTP_CODE (expected 200)"
fi

# Step 5: Final validation
echo ""
if [ "$API_SUCCESS" = "true" ]; then
    echo "=========================================="
    echo "DEPLOYMENT VALIDATION SUCCESSFUL"
    echo "=========================================="
    echo "✓ Web App: $APP_NAME"
    echo "✓ Status: Running"
    echo "✓ URL: $APP_URL"
    echo "✓ API Docs: $APP_URL/api/docs"
    echo "✓ OpenAPI: $APP_URL/api/openapi.json"
    echo "=========================================="
    exit 0
else
    echo "=========================================="
    echo "DEPLOYMENT VALIDATION FAILED"
    echo "=========================================="
    echo "✗ API endpoints are not accessible"
    echo ""
    echo "Troubleshooting steps:"
    echo "1. Check application logs:"
    echo "   az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP"
    echo ""
    echo "2. Check startup command:"
    echo "   az webapp config show --name $APP_NAME --resource-group $RESOURCE_GROUP --query 'linuxFxVersion'"
    echo ""
    echo "3. SSH into container:"
    echo "   az webapp ssh --name $APP_NAME --resource-group $RESOURCE_GROUP"
    echo ""
    echo "4. Check app settings:"
    echo "   az webapp config appsettings list --name $APP_NAME --resource-group $RESOURCE_GROUP"
    echo "=========================================="
    exit 1
fi
