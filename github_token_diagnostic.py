#!/usr/bin/env python3
"""
GitHub Token Diagnostic Script
Run this to check your GitHub token permissions and repository access.
"""

import asyncio
import os
import httpx
from typing import Dict, List


async def check_github_token(token: str, repo: str = None) -> Dict:
    """Check GitHub token permissions and repository access."""
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    results = {
        "token_valid": False,
        "user_info": {},
        "scopes": [],
        "rate_limit": {},
        "repo_access": {},
        "actions_enabled": None,
        "secrets_access": None
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        # Check token validity and user info
        try:
            user_response = await client.get("https://api.github.com/user", headers=headers)
            
            if user_response.status_code == 200:
                results["token_valid"] = True
                results["user_info"] = user_response.json()
                
                # Get scopes from headers
                scopes = user_response.headers.get("x-oauth-scopes", "")
                results["scopes"] = [s.strip() for s in scopes.split(",") if s.strip()]
                
                print(f"Token valid for user: {results['user_info'].get('login')}")
                print(f"Available scopes: {', '.join(results['scopes']) or 'none'}")
                
            else:
                print(f"Token validation failed: HTTP {user_response.status_code}")
                return results
                
        except Exception as e:
            print(f"Error validating token: {e}")
            return results
        
        # Check rate limit
        try:
            rate_response = await client.get("https://api.github.com/rate_limit", headers=headers)
            if rate_response.status_code == 200:
                results["rate_limit"] = rate_response.json()
                core_limit = results["rate_limit"]["resources"]["core"]
                print(f"Rate limit: {core_limit['remaining']}/{core_limit['limit']} remaining")
        except Exception as e:
            print(f"Could not check rate limit: {e}")
        
        # Check repository access if provided
        if repo:
            try:
                repo_response = await client.get(f"https://api.github.com/repos/{repo}", headers=headers)
                
                if repo_response.status_code == 200:
                    repo_data = repo_response.json()
                    results["repo_access"] = {
                        "accessible": True,
                        "private": repo_data.get("private", False),
                        "permissions": repo_data.get("permissions", {}),
                        "fork": repo_data.get("fork", False)
                    }
                    
                    print(f"Repository access: {repo} ({'private' if repo_data.get('private') else 'public'})")
                    
                    permissions = repo_data.get("permissions", {})
                    print(f"Permissions: admin={permissions.get('admin', False)}, push={permissions.get('push', False)}, pull={permissions.get('pull', False)}")
                    
                    # Check Actions permissions
                    actions_response = await client.get(f"https://api.github.com/repos/{repo}/actions/permissions", headers=headers)
                    if actions_response.status_code == 200:
                        actions_data = actions_response.json()
                        results["actions_enabled"] = actions_data.get("enabled", True)
                        print(f"GitHub Actions: {'enabled' if results['actions_enabled'] else 'disabled'}")
                    
                    # Check secrets access
                    secrets_response = await client.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=headers)
                    if secrets_response.status_code == 200:
                        results["secrets_access"] = True
                        print("Secrets API: accessible")
                    elif secrets_response.status_code == 403:
                        results["secrets_access"] = False
                        print("Secrets API: access forbidden (insufficient permissions)")
                    elif secrets_response.status_code == 404:
                        results["secrets_access"] = False
                        print("Secrets API: not found (Actions may be disabled)")
                    else:
                        print(f"Secrets API: HTTP {secrets_response.status_code}")
                        
                elif repo_response.status_code == 404:
                    print(f"Repository not found: {repo}")
                    results["repo_access"] = {"accessible": False, "error": "not_found"}
                elif repo_response.status_code == 403:
                    print(f"Repository access forbidden: {repo}")
                    results["repo_access"] = {"accessible": False, "error": "forbidden"}
                else:
                    print(f"Repository access failed: HTTP {repo_response.status_code}")
                    results["repo_access"] = {"accessible": False, "error": f"http_{repo_response.status_code}"}
                    
            except Exception as e:
                print(f"Error checking repository: {e}")
                results["repo_access"] = {"accessible": False, "error": str(e)}
    
    return results


def analyze_results(results: Dict) -> List[str]:
    """Analyze results and provide recommendations."""
    recommendations = []
    
    if not results["token_valid"]:
        recommendations.append("Generate a new GitHub Personal Access Token at: https://github.com/settings/tokens")
        return recommendations
    
    # Check required scopes
    scopes = results["scopes"]
    required_scopes = ["repo", "workflow"]
    
    missing_scopes = []
    if "repo" not in scopes and "public_repo" not in scopes:
        missing_scopes.append("repo (or public_repo for public repos)")
    if "workflow" not in scopes:
        missing_scopes.append("workflow")
    
    if missing_scopes:
        recommendations.append(f"Add missing scopes to your token: {', '.join(missing_scopes)}")
        recommendations.append("   Go to: https://github.com/settings/tokens → Select your token → Update scopes")
    
    # Check repository access
    repo_access = results.get("repo_access", {})
    if repo_access.get("accessible") is False:
        error = repo_access.get("error", "unknown")
        if error == "not_found":
            recommendations.append("Verify repository name is correct (format: owner/repo-name)")
        elif error == "forbidden":
            recommendations.append("Token lacks repository access - ensure 'repo' scope is enabled")
        else:
            recommendations.append(f"Repository access issue: {error}")
    
    # Check Actions and secrets
    if results.get("actions_enabled") is False:
        recommendations.append("Enable GitHub Actions in repository settings")
    
    if results.get("secrets_access") is False:
        recommendations.append("Token lacks permission to manage repository secrets")
        recommendations.append("   Ensure token has 'repo' scope and you have admin/push access to the repository")
    
    if not recommendations:
        recommendations.append("All checks passed! Your token should work correctly.")
    
    return recommendations


async def main():
    """Main diagnostic function."""
    print("GitHub Token Diagnostic Tool")
    print("=" * 40)
    
    # Get token from environment
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        print("GITHUB_PERSONAL_ACCESS_TOKEN environment variable not set")
        print("Set your token: export GITHUB_PERSONAL_ACCESS_TOKEN=your_token_here")
        return
    
    # Get repository from user input or environment
    repo = input("Enter repository name (owner/repo) or press Enter to skip: ").strip()
    if not repo:
        repo = None
    
    print(f"\nChecking token: {token[:8]}...")
    if repo:
        print(f"Checking repository: {repo}")
    
    print("\n" + "=" * 40)
    
    # Run diagnostics
    results = await check_github_token(token, repo)
    
    print("\n" + "=" * 40)
    print("RECOMMENDATIONS:")
    
    recommendations = analyze_results(results)
    for rec in recommendations:
        print(rec)
    
    print("\n" + "=" * 40)
    print("Useful Links:")
    print("• Create token: https://github.com/settings/tokens")
    print("• Required scopes: repo, workflow")
    print("• GitHub Actions docs: https://docs.github.com/en/actions")


if __name__ == "__main__":
    asyncio.run(main())