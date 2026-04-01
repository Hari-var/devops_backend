from __future__ import annotations
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    commit_message: Mapped[str] = mapped_column(Text, default="")
    committed_by: Mapped[str] = mapped_column(String(255), default="")
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_files: Mapped[list] = mapped_column(JSON, default=list)

    # Config parsed from config.py in the repo
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    # Filled after Stage 1 (tech detection)
    detected_tech: Mapped[dict] = mapped_column(JSON, default=dict)

    # Pipeline progress: 0=pending 1=tech 2=terraform 3=cicd 4=monitoring 5=done
    pipeline_stage: Mapped[int] = mapped_column(Integer, default=0)

    # Per-stage logs: {"1": ["line1","line2"], "2": [...], ...}
    stage_logs: Mapped[dict] = mapped_column(JSON, default=dict)

    # Overall status
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # Legacy flat log list (kept for backwards compat with SSE replay)
    logs: Mapped[list] = mapped_column(JSON, default=list)

    # URLs captured after pipeline completes
    terraform_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions_run_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
