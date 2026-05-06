from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def create_db_engine(db_url: str):
    return create_engine(db_url, future=True)


def create_session(engine) -> Session:
    return Session(engine)
