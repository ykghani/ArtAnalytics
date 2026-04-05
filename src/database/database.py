from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from typing import Optional
from pathlib import Path
from contextlib import contextmanager

from .models import Base


class Database:
    def __init__(self, db_path: Path):
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._SessionFactory = sessionmaker(bind=self.engine)

    def create_tables(self):
        """Create all tables in the database"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        """Get a new database session"""
        return self._SessionFactory()

    @contextmanager
    def session_scope(self):
        """
        Provide a transactional scope around a series of operations.

        Usage:
            with db.session_scope() as session:
                # do work
                pass
            # session automatically committed and closed
        """
        session = self._SessionFactory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def init_museums(self, session: Session):
        """Initialize museum entries if they don't exist"""
        from .models import Museum

        museums = [
            {"code": "met", "name": "Metropolitan Museum of Art"},
            {"code": "aic", "name": "Art Institute of Chicago"},
            {"code": "cma", "name": "Cleveland Museum of Art"},
            {"code": "mia", "name": "Minneapolis Institute of Art"},
            {"code": "smk", "name": "Statens Museum for Kunst"},
        ]

        for museum_data in museums:
            if not session.query(Museum).filter_by(code=museum_data["code"]).first():
                museum = Museum(**museum_data)
                session.add(museum)

        session.commit()
