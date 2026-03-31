from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, CheckConstraint, DateTime, JSON
from datetime import datetime
from app.db import Base

class Repo_status(Base):
    __tablename__ = "repo_status"

    id = Column(Integer, primary_key=True, index=True)
    repo_name = Column(String, index=True)
    branch = Column(String, index=True)
    infrastructure = Column(JSON)
    status = Column(String, index=True)
    commit_id = Column(String, index=True)
    commit_message = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.now())
    provision_response = Column(JSON)
    techstack= Column(JSON)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved')", name="status_check"),
    )
