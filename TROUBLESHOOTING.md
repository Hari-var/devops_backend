# Troubleshooting: Repository Access Issues

## Error: "Repository not found or inaccessible"

This error occurs when the GitHub Personal Access Token (PAT) cannot access the repository.

## Quick Fix

The system now automatically falls back to using your OAuth token if the PAT fails. However, for write operations (committing files, setting secrets), you need a valid PAT.

## Solution 1: Check PAT Permissions

1. Go to https://github.com/settings/tokens
2. Find your token (or create a new one)
3. Ensure it has these scopes:
   - ✅ `repo` (Full control of private repositories)
   - ✅ `workflow` (Update GitHub Action workflows)
   - ✅ `read:org` (Read org and team membership)

4. If you created a new token, update your `.env`:
   ```bash
   GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxx
   ```

5. Restart your backend

## Solution 2: Check Repository Access

### For Private Repos:
- The PAT must belong to a user who has access to the repo
- If it's an organization repo, the PAT user must be a member

### For Public Repos:
- PAT should work automatically
- If not, check if the repo actually exists: https://github.com/Hari-var/dum_insurance_app

## Solution 3: Use Debug Endpoint

Visit: `https://your-backend.onrender.com/api/v1/approvals/debug`

This will show:
```json
{
  "token_set": true,
  "token_preview": "ghp_xxxx...",
  "pat_scopes": ["repo", "workflow"],  // ← Check this!
  "github_user": "your-username",
  "repos_visible": ["user/repo1", "user/repo2"],  // ← Is your repo here?
  "repos_with_config_py": ["user/repo1"]
}
```

**What to check:**
1. `pat_scopes` includes `"repo"` and `"workflow"`
2. `repos_visible` includes your target repo
3. `github_user` matches the repo owner or has access

## Solution 4: Regenerate PAT

If the token is old or has wrong permissions:

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Name it: "DevOps Agent"
4. Select scopes:
   - ✅ repo
   - ✅ workflow
   - ✅ read:org (optional, for org repos)
5. Click "Generate token"
6. Copy the token (starts with `ghp_`)
7. Update `.env`:
   ```bash
   GITHUB_PERSONAL_ACCESS_TOKEN=ghp_NEW_TOKEN_HERE
   ```
8. Restart backend

## Solution 5: Verify Repository Exists

Check if the repo exists and you have access:
```bash
curl -H "Authorization: Bearer YOUR_PAT" \
  https://api.github.com/repos/Hari-var/dum_insurance_app
```

If you get 404, the repo doesn't exist or you don't have access.

## How the Fallback Works

The system now tries:
1. **First**: Use PAT for all operations
2. **If PAT fails**: Use OAuth token for read operations
3. **For write operations**: Still requires PAT

This means:
- ✅ Tech detection will work with OAuth token
- ✅ Reading config.py will work with OAuth token
- ❌ Committing CI/CD files requires PAT
- ❌ Setting GitHub secrets requires PAT

## Best Practice

Use a PAT from the repository owner's account:
- For personal repos: Use your own PAT
- For org repos: Use a PAT from an org admin or create a bot account

## Still Having Issues?

Check the backend logs for detailed error messages:
```bash
# On Render
View logs in Render dashboard

# Locally
Check console output where backend is running
```

Look for lines like:
```
ERROR | backend.app.api.v1.approvals | Pipeline failed
```

The traceback will show the exact error.
