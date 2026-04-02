# React App Deployment Guide - Render

## Quick Start Examples

### 1. Simple Vite React App (Most Common)
```python
# config.py
DEPLOY_TARGET = "render"
APP_NAME = "my-vite-app"
RENDER_SERVICE_TYPE = "static_site"
PUBLISH_PATH = "./dist"
REGION = "oregon"
```

**Your package.json should have:**
```json
{
  "scripts": {
    "build": "vite build"
  }
}
```

---

### 2. Create React App (CRA)
```python
# config.py
DEPLOY_TARGET = "render"
APP_NAME = "my-cra-app"
RENDER_SERVICE_TYPE = "static_site"
PUBLISH_PATH = "./build"
REGION = "oregon"
```

**Your package.json should have:**
```json
{
  "scripts": {
    "build": "react-scripts build"
  }
}
```

---

### 3. React App with Environment Variables
```python
# config.py
DEPLOY_TARGET = "render"
APP_NAME = "my-react-app"
RENDER_SERVICE_TYPE = "static_site"
PUBLISH_PATH = "./dist"
REGION = "oregon"

# Note: For static sites, env vars are baked into build
# They must be prefixed with VITE_ or REACT_APP_
ENV_VARS = {
    "VITE_API_URL": "https://api.example.com",
    "VITE_APP_TITLE": "My Awesome App",
    "VITE_ENABLE_ANALYTICS": "true",
}
```

**In your React code:**
```javascript
// Vite
const apiUrl = import.meta.env.VITE_API_URL;

// CRA
const apiUrl = process.env.REACT_APP_API_URL;
```

---

### 4. Next.js App (Static Export)
```python
# config.py
DEPLOY_TARGET = "render"
APP_NAME = "my-nextjs-static"
RENDER_SERVICE_TYPE = "static_site"
PUBLISH_PATH = "./out"
REGION = "oregon"
```

**Your next.config.js:**
```javascript
module.exports = {
  output: 'export',
  images: {
    unoptimized: true,
  },
}
```

**Your package.json:**
```json
{
  "scripts": {
    "build": "next build"
  }
}
```

---

### 5. Next.js App (Server-Side Rendering)
```python
# config.py
DEPLOY_TARGET = "render"
APP_NAME = "my-nextjs-ssr"
RENDER_SERVICE_TYPE = "web_service"
REGION = "oregon"
PLAN = "free"

ENV_VARS = {
    "NODE_ENV": "production",
    "NEXT_PUBLIC_API_URL": "https://api.example.com",
    "DATABASE_URL": "postgresql://user:pass@host:5432/db",
    "NEXTAUTH_SECRET": "your-secret-key",
    "NEXTAUTH_URL": "https://my-nextjs-ssr.onrender.com",
}
```

**Your package.json:**
```json
{
  "scripts": {
    "build": "next build",
    "start": "next start"
  }
}
```

---

### 6. React + Express Backend (Fullstack)
```python
# config.py
DEPLOY_TARGET = "render"
APP_NAME = "my-fullstack-app"
RENDER_SERVICE_TYPE = "web_service"
REGION = "oregon"
PLAN = "free"

ENV_VARS = {
    "NODE_ENV": "production",
    "PORT": "10000",
    "REACT_APP_API_URL": "/api",  # Relative URL since same server
    "DATABASE_URL": "postgresql://...",
    "JWT_SECRET": "your-jwt-secret",
    "SESSION_SECRET": "your-session-secret",
}
```

**Project structure:**
```
my-fullstack-app/
├── client/          # React app
│   ├── src/
│   ├── package.json
│   └── vite.config.js
├── server/          # Express backend
│   ├── index.js
│   └── routes/
├── package.json     # Root package.json
└── config.py
```

**Root package.json:**
```json
{
  "scripts": {
    "build": "cd client && npm install && npm run build",
    "start": "node server/index.js"
  }
}
```

**Express server (server/index.js):**
```javascript
const express = require('express');
const path = require('path');
const app = express();

// API routes
app.use('/api', require('./routes'));

// Serve React static files
app.use(express.static(path.join(__dirname, '../client/dist')));

// Catch-all for React Router
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, '../client/dist/index.html'));
});

const PORT = process.env.PORT || 10000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
```

---

## Environment Variable Prefixes

| Framework | Prefix | Example |
|-----------|--------|---------|
| Vite | `VITE_` | `VITE_API_URL` |
| CRA | `REACT_APP_` | `REACT_APP_API_URL` |
| Next.js | `NEXT_PUBLIC_` | `NEXT_PUBLIC_API_URL` |

**Important:** Only variables with these prefixes are exposed to the browser!

---

## Common Configurations

### API URL Configuration
```python
# Development API
ENV_VARS = {
    "VITE_API_URL": "http://localhost:3000/api",
}

# Production API (same domain)
ENV_VARS = {
    "VITE_API_URL": "/api",
}

# Production API (different domain)
ENV_VARS = {
    "VITE_API_URL": "https://api.myapp.com",
}
```

### Authentication
```python
ENV_VARS = {
    "VITE_AUTH0_DOMAIN": "myapp.auth0.com",
    "VITE_AUTH0_CLIENT_ID": "your-client-id",
    "VITE_SUPABASE_URL": "https://xxx.supabase.co",
    "VITE_SUPABASE_ANON_KEY": "your-anon-key",
}
```

### Analytics
```python
ENV_VARS = {
    "VITE_GA_TRACKING_ID": "G-XXXXXXXXXX",
    "VITE_SENTRY_DSN": "https://xxx@sentry.io/xxx",
}
```

---

## Troubleshooting

### Build fails with "command not found"
**Solution:** Ensure your package.json has the correct build script:
```json
{
  "scripts": {
    "build": "vite build"  // or "react-scripts build"
  }
}
```

### Environment variables are undefined
**Solution:** Check the prefix:
- Vite: Must start with `VITE_`
- CRA: Must start with `REACT_APP_`
- Next.js: Must start with `NEXT_PUBLIC_`

### 404 errors on refresh (React Router)
**Solution:** For static sites, Render automatically handles this. For web services, add catch-all route in Express (see example above).

### Build succeeds but site is blank
**Solution:** Check PUBLISH_PATH matches your build output:
- Vite: `./dist`
- CRA: `./build`
- Next.js: `./out`

---

## Testing Locally

Before deploying, test your build:

```bash
# Vite
npm run build
npm run preview

# CRA
npm run build
npx serve -s build

# Next.js
npm run build
npm start
```

Visit http://localhost:4173 (Vite) or http://localhost:3000 (others)
