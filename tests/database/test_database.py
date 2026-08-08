"""Tests for src/database/database.py's museum seeding.

Regression coverage for the bug where 'lacma' (and, before it, 'cma' — see
src/database/add_cma.py) was missing from the `museums` table because
init_museums() kept its own hardcoded list instead of reading the same
registry (settings.museums) the downloaders are configured from. Every write
during LACMA's full 25,135-item crawl silently failed with
"Museum with code lacma not found" as a result.
"""
import tempfile
from pathlib import Path

from src.config import settings
from src.database.database import Database
from src.database.models import Museum


def _fresh_db() -> Database:
    tmp_dir = tempfile.mkdtemp()
    db = Database(Path(tmp_dir) / "test.sqlite")
    db.create_tables()
    return db


def test_init_museums_seeds_every_configured_museum():
    db = _fresh_db()
    with db.session_scope() as session:
        db.init_museums(session)
        seeded_codes = {m.code for m in session.query(Museum).all()}

    assert seeded_codes == set(settings.museums.keys())


def test_init_museums_includes_lacma():
    db = _fresh_db()
    with db.session_scope() as session:
        db.init_museums(session)
        assert session.query(Museum).filter_by(code="lacma").first() is not None


def test_init_museums_is_idempotent():
    db = _fresh_db()
    with db.session_scope() as session:
        db.init_museums(session)
        db.init_museums(session)
        count = session.query(Museum).filter_by(code="lacma").count()
    assert count == 1
