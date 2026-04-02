# ============================================================================
# RENDER DEPLOYMENT - React App (Vite) Example
# ============================================================================

DEPLOY_TARGET = "render"
APP_NAME = "my-react-app"
RENDER_SERVICE_TYPE = "static_site"  # Force static site for React
PUBLISH_PATH = "./dist"  # Vite builds to dist/
REGION = "oregon"  # Options: oregon, frankfurt, singapore, ohio

# No ENV_VARS needed for static sites (they're built at build-time)
# Use VITE_ prefix for environment variables in Vite apps


# ============================================================================
# RENDER DEPLOYMENT - React App (Create React App) Example
# ============================================================================

# DEPLOY_TARGET = "render"
# APP_NAME = "my-cra-app"
# RENDER_SERVICE_TYPE = "static_site"
# PUBLISH_PATH = "./build"  # CRA builds to build/
# REGION = "oregon"


# ============================================================================
# RENDER DEPLOYMENT - React App (Next.js Static Export) Example
# ============================================================================

# DEPLOY_TARGET = "render"
# APP_NAME = "my-nextjs-app"
# RENDER_SERVICE_TYPE = "static_site"
# PUBLISH_PATH = "./out"  # Next.js static export to out/
# REGION = "oregon"


# ============================================================================
# RENDER DEPLOYMENT - React App with Backend (Next.js SSR) Example
# ============================================================================

# DEPLOY_TARGET = "render"
# APP_NAME = "my-nextjs-ssr-app"
# RENDER_SERVICE_TYPE = "web_service"  # Use web service for SSR
# REGION = "oregon"
# PLAN = "free"

# ENV_VARS = {
#     "NODE_ENV": "production",
#     "NEXT_PUBLIC_API_URL": "https://api.example.com",
#     "DATABASE_URL": "postgresql://...",
# }


# ============================================================================
# RENDER DEPLOYMENT - React + Express Backend Example
# ============================================================================

# DEPLOY_TARGET = "render"
# APP_NAME = "my-fullstack-app"
# RENDER_SERVICE_TYPE = "web_service"
# REGION = "oregon"
# PLAN = "free"

# ENV_VARS = {
#     "NODE_ENV": "production",
#     "PORT": "10000",
#     "REACT_APP_API_URL": "https://my-fullstack-app.onrender.com/api",
#     "DATABASE_URL": "postgresql://...",
#     "JWT_SECRET": "your-secret-key",
# }


# ============================================================================
# IMPORTANT NOTES FOR REACT APPS
# ============================================================================

# 1. Static Sites (Vite, CRA, Next.js Static):
#    - Use RENDER_SERVICE_TYPE = "static_site"
#    - Set correct PUBLISH_PATH (dist/, build/, or out/)
#    - Environment variables must be prefixed:
#      * Vite: VITE_API_URL
#      * CRA: REACT_APP_API_URL
#      * Next.js: NEXT_PUBLIC_API_URL
#    - These are baked into the build at build-time

# 2. Server-Side Rendering (Next.js, Remix):
#    - Use RENDER_SERVICE_TYPE = "web_service"
#    - Add PLAN = "free" or higher
#    - ENV_VARS are available at runtime

# 3. Build Commands (auto-detected by Render):
#    - Vite: npm install && npm run build
#    - CRA: npm install && npm run build
#    - Next.js: npm install && npm run build

# 4. Start Commands (for web services):
#    - Next.js: npm start
#    - Express: node server.js or npm start

# 5. Package.json Requirements:
#    Your package.json must have:
#    {
#      "scripts": {
#        "build": "vite build",  // or "react-scripts build"
#        "start": "vite preview" // for static preview (optional)
#      }
#    }
