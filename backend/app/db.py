from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.orm import  DeclarativeBase
from fastapi import Depends #type: ignore
from typing import Annotated
from dotenv import load_dotenv

load_dotenv()

_DATABASE_URL = os.getenv("cdb", "sqlite+aiosqlite:///./devops_agent.db")

# Convert postgresql:// to postgresql+asyncpg:// for async support
if _DATABASE_URL.startswith("postgresql://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

_is_sqlite = "sqlite" in _DATABASE_URL
_connect_args = {"check_same_thread": False} if _is_sqlite else {"ssl": "require"}

engine = create_async_engine(
    _DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

db_dependency = Annotated[Session, Depends(get_db)]
async def create_tables() -> None:
    """Create all tables on startup (idempotent)."""
    async with engine.begin() as conn:
        from . import models as _  # noqa: F401 — ensure models are imported before create_all
        await conn.run_sync(Base.metadata.create_all)
        # Ensure new columns added to existing SQLite DB (SQLite doesn't alter tables with create_all)
        from sqlalchemy import text

        def _ensure_columns(sync_conn):
            # Fetch existing columns — works for both SQLite and PostgreSQL
            if _is_sqlite:
                res = sync_conn.execute(text("PRAGMA table_info('approvals')")).fetchall()
                existing = [r[1] for r in res]
            else:
                res = sync_conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='approvals'"
                )).fetchall()
                existing = [r[0] for r in res]

            expected: dict[str, tuple[str, str]] = {
                'changed_files': ('TEXT', "'[]'"),
                'config': ('TEXT', "'{}'"),
                'detected_tech': ('TEXT', "'{}'"),
                'pipeline_stage': ('INTEGER', '0'),
                'stage_logs': ('TEXT', "'{}'"),
                'status': ('TEXT', "'pending'"),
                'logs': ('TEXT', "'[]'"),
                'terraform_url': ('TEXT', 'NULL'),
                'deployed_url': ('TEXT', 'NULL'),
                'actions_run_url': ('TEXT', 'NULL'),
                'created_at': ('REAL' if _is_sqlite else 'DOUBLE PRECISION', '0'),
            }

            for col, (col_type, default_val) in expected.items():
                if col in existing:
                    continue
                try:
                    if default_val == 'NULL':
                        sync_conn.execute(text(f"ALTER TABLE approvals ADD COLUMN {col} {col_type}"))
                    else:
                        sync_conn.execute(text(f"ALTER TABLE approvals ADD COLUMN {col} {col_type} DEFAULT {default_val}"))
                except Exception:
                    pass

        await conn.run_sync(_ensure_columns)
