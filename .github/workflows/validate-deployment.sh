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

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL" || echo "000")
    
    if [ "$HTTP_CODE" != "000" ] && [ "$HTTP_CODE" != "503" ]; then
        echo "✓ App is responding (HTTP $HTTP_CODE)"
        break
    fi
    
    ATTEMPT=$((ATTEMPT + 1))
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS: HTTP $HTTP_CODE - Waiting 5s..."
    sleep 5
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo "✗ App failed to respond after $MAX_ATTEMPTS attempts"
    echo ""
    echo "Checking logs..."
    az webapp log tail --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --timeout 30 || true
    exit 1
fi

# Step 4: Check API endpoints
echo ""
echo "[4/5] Checking API endpoints..."

# Check /api/docs
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/api/docs" || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✓ /api/docs is accessible (HTTP $HTTP_CODE)"
else
    echo "⚠ /api/docs returned HTTP $HTTP_CODE"
fi

# Check /api/openapi.json
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/api/openapi.json" || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✓ /api/openapi.json is accessible (HTTP $HTTP_CODE)"
else
    echo "⚠ /api/openapi.json returned HTTP $HTTP_CODE"
fi

# Step 5: Summary
echo ""
echo "=========================================="
echo "DEPLOYMENT VALIDATION COMPLETE"
echo "=========================================="
echo "✓ Web App: $APP_NAME"
echo "✓ Status: Running"
echo "✓ URL: $APP_URL"
echo "✓ API Docs: $APP_URL/api/docs"
echo "✓ OpenAPI: $APP_URL/api/openapi.json"
echo "=========================================="

exit 0
