"""AI service — analyse pipeline failures using Google Gemini."""
from __future__ import annotations

import logging
import os
import re

import google.generativeai as genai

logger = logging.getLogger(__name__)

_MAX_LOG_CHARS = 8_000
_DEFAULT_MODEL = "gemini-2.5-flash"

_PROMPT_TEMPLATE = """\
You are a DevOps expert analyzing a CI/CD pipeline failure.

Technology Stack:
- Language: {language}
- Framework: {framework}
- Build Tool: {build_tool}
- Has Dockerfile: {has_dockerfile}
- Has Helm: {has_helm}
- Has Terraform: {has_terraform}

Error Logs:
```
{error_logs}
```

Provide:
1. A clear, concise reason for the failure (2-3 sentences).
2. A step-by-step resolution (3-5 steps).

Format:
REASON: <reason>
RESOLUTION: <steps>
"""


def analyze_pipeline_error(error_logs: str, tech_stack: dict) -> dict[str, str]:
    """
    Analyse a pipeline failure with Google Gemini and return reason + resolution.

    Args:
        error_logs: Raw log output from the failed pipeline run.
        tech_stack: Detected technology stack dict.

    Returns:
        Dict with keys ``reason`` and ``resolution``.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — skipping AI analysis.")
        return {
            "reason": "AI analysis not configured. Set the GEMINI_API_KEY environment variable.",
            "resolution": "Review the error logs manually and check the pipeline configuration.",
        }

    # Truncate from the tail — errors appear at the end of logs
    if len(error_logs) > _MAX_LOG_CHARS:
        error_logs = error_logs[-_MAX_LOG_CHARS:]

    # Sanitize logs — strip ANSI codes and non-printable chars (CWE-94)
    sanitized_logs = re.sub(r"\x1b\[[0-9;]*m", "", error_logs)
    sanitized_logs = re.sub(r"[^\x09\x0a\x0d\x20-\x7e]", "", sanitized_logs)

    prompt = _PROMPT_TEMPLATE.format(
        language=tech_stack.get("language", "unknown"),
        framework=tech_stack.get("framework") or "none",
        build_tool=tech_stack.get("buildTool") or "unknown",
        has_dockerfile=tech_stack.get("hasDockerfile", False),
        has_helm=tech_stack.get("hasHelm", False),
        has_terraform=tech_stack.get("hasTerraform", False),
        error_logs=sanitized_logs,
    )

    try:
        genai.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 1000},
        )
        return _parse_response(response.text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gemini AI analysis failed")
        return {
            "reason": f"AI analysis failed: {exc}",
            "resolution": (
                "Review the error logs manually. Common causes: dependency failures, "
                "test failures, configuration errors, or infrastructure issues."
            ),
        }


def _parse_response(content: str) -> dict[str, str]:
    """Extract REASON and RESOLUTION sections from the model response."""
    reason = "Unable to parse AI response."
    resolution = "Please review the error logs manually."

    if "REASON:" in content and "RESOLUTION:" in content:
        parts = content.split("RESOLUTION:", 1)
        reason = parts[0].replace("REASON:", "").strip()
        resolution = parts[1].strip()
    elif "REASON:" in content:
        reason = content.split("REASON:", 1)[1].strip()
    else:
        reason = content[:200]
        resolution = content[200:] if len(content) > 200 else "See reason above."

    return {"reason": reason, "resolution": resolution}
