from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from typing import Any, Dict, Optional
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

    def init_museums(self, session: Session, museums: Optional[Dict[str, Any]] = None):
        """Initialize museum entries if they don't exist.

        Derived from settings.museums (src/config.py) rather than a separate
        hardcoded list — the two used to drift (cma and then lacma both shipped
        downloaders whose museum code was never added here, so every artwork
        write silently failed with "Museum with code X not found" for the
        entire run). Sourcing from the same config the downloaders themselves
        register against means a new museum is seeded as soon as its config
        entry exists, with no separate list to remember to update.

        `museums` overrides the source registry (code -> object with a `.name`
        attribute) — used by scripts/verify_museum.py's DB-writable check so it
        seeds from whatever museum config the verifier itself resolved, not a
        fresh import of the global settings singleton.
        """
        from .models import Museum

        if museums is None:
            from ..config import settings
            museums = settings.museums

        for code, museum_config in museums.items():
            if not session.query(Museum).filter_by(code=code).first():
                name = getattr(museum_config, "name", None) or f"{code.upper()} Museum"
                session.add(Museum(code=code, name=name))

        session.commit()
