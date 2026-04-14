"""Configuration utility for AI-powered DevOps backend."""
import os
from typing import Optional


class AIConfig:
    """Configuration manager for AI settings."""
    
    @staticmethod
    def is_ai_enabled() -> bool:
        """Check if AI terraform generation is enabled."""
        return os.getenv("AI_TERRAFORM_ENABLED", "true").lower() == "true"
    
    @staticmethod
    def should_fallback_on_error() -> bool:
        """Check if fallback should be used on AI errors."""
        return os.getenv("AI_FALLBACK_ON_ERROR", "true").lower() == "true"
    
    @staticmethod
    def get_ai_model() -> str:
        """Get the AI model to use."""
        return os.getenv("AI_MODEL", "gemini-1.5-flash")
    
    @staticmethod
    def get_ai_temperature() -> float:
        """Get the AI temperature setting."""
        try:
            return float(os.getenv("AI_TEMPERATURE", "0.1"))
        except ValueError:
            return 0.1
    
    @staticmethod
    def get_gemini_api_key() -> Optional[str]:
        """Get the Gemini API key."""
        return os.getenv("GEMINI_API_KEY")
    
    @staticmethod
    def get_max_output_tokens() -> int:
        """Get maximum output tokens for AI generation."""
        try:
            return int(os.getenv("AI_MAX_OUTPUT_TOKENS", "8000"))
        except ValueError:
            return 8000