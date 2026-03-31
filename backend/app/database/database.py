from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session
from fastapi import Depends #type: ignore
from typing import Annotated
import os
from dotenv import load_dotenv

load_dotenv()

cloud_db = os.environ["DATABASE_URL"]





engine = create_engine(
    cloud_db,
    echo=True
)

sessionlocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
class Base(DeclarativeBase):
    pass

def get_db():
    db = sessionlocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]