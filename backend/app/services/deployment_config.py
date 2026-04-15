"""Deployment configuration for managing Terraform execution strategies."""
import os
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass


class DeploymentStrategy(Enum):
    """Available deployment strategies."""
    GITHUB_ACTIONS = "github_actions"
    LOCAL_EXECUTION = "local_execution"
    AZURE_DEVOPS = "azure_devops"
    TERRAFORM_CLOUD = "terraform_cloud"


@dataclass
class DeploymentConfig:
    """Configuration for deployment strategy."""
    strategy: DeploymentStrategy
    enabled: bool = True
    fallback_strategy: Optional[DeploymentStrategy] = None
    config: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.config is None:
            self.config = {}


class DeploymentManager:
    """Manages deployment strategy selection and configuration."""
    
    def __init__(self):
        self.strategies = self._load_strategies()
        self.default_strategy = self._get_default_strategy()
    
    def _load_strategies(self) -> Dict[DeploymentStrategy, DeploymentConfig]:
        """Load available deployment strategies."""
        return {
            DeploymentStrategy.GITHUB_ACTIONS: DeploymentConfig(
                strategy=DeploymentStrategy.GITHUB_ACTIONS,
                enabled=True,
                fallback_strategy=DeploymentStrategy.LOCAL_EXECUTION,
                config={
                    "workflow_timeout_minutes": 30,
                    "terraform_version": "1.6.0",
                    "runner": "ubuntu-latest",
                    "enable_plan_review": True,
                    "auto_approve": False,  # Require manual approval for production
                    "state_backend": "azure_storage",
                    "enable_drift_detection": True
                }
            ),
            DeploymentStrategy.LOCAL_EXECUTION: DeploymentConfig(
                strategy=DeploymentStrategy.LOCAL_EXECUTION,
                enabled=True,  # Keep as fallback
                config={
                    "terraform_timeout_minutes": 20,
                    "terraform_version": "1.6.0",
                    "enable_validation": True,
                    "secure_execution": True
                }
            ),
            DeploymentStrategy.AZURE_DEVOPS: DeploymentConfig(
                strategy=DeploymentStrategy.AZURE_DEVOPS,
                enabled=False,  # Not implemented yet
                config={
                    "organization": os.getenv("AZURE_DEVOPS_ORG"),
                    "project": os.getenv("AZURE_DEVOPS_PROJECT"),
                    "pipeline_timeout_minutes": 45
                }
            ),
            DeploymentStrategy.TERRAFORM_CLOUD: DeploymentConfig(
                strategy=DeploymentStrategy.TERRAFORM_CLOUD,
                enabled=False,  # Not implemented yet
                config={
                    "organization": os.getenv("TFC_ORGANIZATION"),
                    "workspace_prefix": "devops-agent",
                    "execution_mode": "remote"
                }
            )
        }
    
    def _get_default_strategy(self) -> DeploymentStrategy:
        """Get the default deployment strategy based on environment."""
        # Check environment variable first
        strategy_env = os.getenv("DEPLOYMENT_STRATEGY", "").upper()
        if strategy_env:
            try:
                return DeploymentStrategy(strategy_env.lower())
            except ValueError:
                pass
        
        # Auto-detect based on available credentials/environment
        if self._is_github_actions_available():
            return DeploymentStrategy.GITHUB_ACTIONS
        elif self._is_azure_devops_available():
            return DeploymentStrategy.AZURE_DEVOPS
        elif self._is_terraform_cloud_available():
            return DeploymentStrategy.TERRAFORM_CLOUD
        else:
            return DeploymentStrategy.LOCAL_EXECUTION
    
    def _is_github_actions_available(self) -> bool:
        """Check if GitHub Actions is available and configured."""
        return bool(
            os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") and
            self.strategies[DeploymentStrategy.GITHUB_ACTIONS].enabled
        )
    
    def _is_azure_devops_available(self) -> bool:
        """Check if Azure DevOps is available and configured."""
        return bool(
            os.getenv("AZURE_DEVOPS_ORG") and
            os.getenv("AZURE_DEVOPS_TOKEN") and
            self.strategies[DeploymentStrategy.AZURE_DEVOPS].enabled
        )
    
    def _is_terraform_cloud_available(self) -> bool:
        """Check if Terraform Cloud is available and configured."""
        return bool(
            os.getenv("TFC_TOKEN") and
            os.getenv("TFC_ORGANIZATION") and
            self.strategies[DeploymentStrategy.TERRAFORM_CLOUD].enabled
        )
    
    def get_strategy(self, preferred: Optional[DeploymentStrategy] = None) -> DeploymentStrategy:
        """Get the deployment strategy to use."""
        if preferred and self.strategies[preferred].enabled:
            return preferred
        return self.default_strategy
    
    def get_config(self, strategy: DeploymentStrategy) -> Dict[str, Any]:
        """Get configuration for a deployment strategy."""
        return self.strategies[strategy].config
    
    def get_fallback_strategy(self, strategy: DeploymentStrategy) -> Optional[DeploymentStrategy]:
        """Get fallback strategy for a given strategy."""
        return self.strategies[strategy].fallback_strategy
    
    def is_strategy_available(self, strategy: DeploymentStrategy) -> bool:
        """Check if a deployment strategy is available."""
        if not self.strategies[strategy].enabled:
            return False
        
        if strategy == DeploymentStrategy.GITHUB_ACTIONS:
            return self._is_github_actions_available()
        elif strategy == DeploymentStrategy.AZURE_DEVOPS:
            return self._is_azure_devops_available()
        elif strategy == DeploymentStrategy.TERRAFORM_CLOUD:
            return self._is_terraform_cloud_available()
        elif strategy == DeploymentStrategy.LOCAL_EXECUTION:
            return True  # Always available as fallback
        
        return False
    
    def get_strategy_comparison(self) -> Dict[str, Dict[str, Any]]:
        """Get comparison of deployment strategies."""
        return {
            "github_actions": {
                "pros": [
                    "🔐 Secure credential management",
                    "📊 Full audit trail and logs",
                    "🔄 Consistent execution environment",
                    "👥 Team collaboration and visibility",
                    "💰 Free for public repos",
                    "🚀 Scalable and reliable",
                    "🔧 Easy integration with existing workflows"
                ],
                "cons": [
                    "⏱️ Slightly slower startup time",
                    "🌐 Requires internet connectivity",
                    "💳 Costs for private repos (minimal)"
                ],
                "best_for": "Production deployments, team environments, CI/CD integration",
                "security_score": 9,
                "reliability_score": 9,
                "performance_score": 7
            },
            "local_execution": {
                "pros": [
                    "⚡ Fast execution (no queue time)",
                    "🔧 Full control over environment",
                    "🔍 Easy debugging and troubleshooting"
                ],
                "cons": [
                    "🔓 Credentials stored locally",
                    "❌ No audit trail",
                    "🖥️ Environment inconsistencies",
                    "👤 Single point of failure",
                    "📊 No centralized logging"
                ],
                "best_for": "Development, testing, quick prototypes",
                "security_score": 4,
                "reliability_score": 5,
                "performance_score": 9
            },
            "azure_devops": {
                "pros": [
                    "🔐 Enterprise security features",
                    "📊 Advanced reporting and analytics",
                    "🔄 Tight Azure integration",
                    "👥 Enterprise collaboration tools"
                ],
                "cons": [
                    "💰 Higher cost",
                    "🔧 More complex setup",
                    "🏢 Primarily for Azure-centric organizations"
                ],
                "best_for": "Large enterprises using Azure ecosystem",
                "security_score": 9,
                "reliability_score": 8,
                "performance_score": 7
            },
            "terraform_cloud": {
                "pros": [
                    "🏗️ Purpose-built for Terraform",
                    "🔐 Advanced state management",
                    "📊 Policy as code (Sentinel)",
                    "🔄 Advanced workflow features"
                ],
                "cons": [
                    "💰 Subscription cost",
                    "🔧 Vendor lock-in",
                    "📚 Learning curve"
                ],
                "best_for": "Terraform-heavy organizations, compliance requirements",
                "security_score": 9,
                "reliability_score": 9,
                "performance_score": 8
            }
        }


# Global deployment manager instance
deployment_manager = DeploymentManager()


def get_deployment_strategy() -> DeploymentStrategy:
    """Get the current deployment strategy."""
    return deployment_manager.get_strategy()


def get_deployment_config(strategy: Optional[DeploymentStrategy] = None) -> Dict[str, Any]:
    """Get deployment configuration for the current or specified strategy."""
    if strategy is None:
        strategy = get_deployment_strategy()
    return deployment_manager.get_config(strategy)


def is_github_actions_recommended() -> bool:
    """Check if GitHub Actions is the recommended strategy."""
    return deployment_manager.is_strategy_available(DeploymentStrategy.GITHUB_ACTIONS)