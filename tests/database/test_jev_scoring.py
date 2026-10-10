"""Jev scoring at ingest: attaches judgments, skips judged rows, never blocks ingest."""
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from artserve_shared import display_judgment as dj

from src.database.database import Database
from src.database.models import Artwork, Museum
from src.database.repository import ArtworkRepository
from src.download import jev_scoring
from src.museums.schemas import ArtworkMetadata


def make_metadata(i, **kw) -> ArtworkMetadata:
    return ArtworkMetadata(
        id=str(i),
        accession_number=f"acc-{i}",
        title=kw.pop("title", f"Work {i}"),
        artist=kw.pop("artist", "Some Artist"),
        **kw,
    )


def judgment(flat=0.9):
    return dj.DisplayJudgment(
        flat_display_p=flat, fragment_p=0.1, attribution="named_artist", appeal=4.0, model="jev-test"
    )


@pytest.fixture
def settings():
    return SimpleNamespace(
        jev_scoring_enabled=True, typesafe_api_key="k", jev_model="jev-test", jev_concurrency=5
    )


@pytest.fixture
def repo():
    db = Database(Path(tempfile.mkdtemp()) / "t.sqlite")
    db.create_tables()
    session = db.get_session()
    # Seed directly: Database.init_museums() needs a full museum config/.env.
    session.add_all([Museum(name="The Met", code="met"), Museum(name="AIC", code="aic")])
    session.commit()
    yield ArtworkRepository(session)
    session.close()


def batch_of(*ids, code="met"):
    return [(make_metadata(i), code, None) for i in ids]


def fake_judge(monkeypatch, fn):
    async def _run(rows, **kw):
        return [fn(r) for r in rows]

    monkeypatch.setattr(dj, "judge_artworks", _run)


def test_scores_every_unjudged_artwork_in_batch(repo, settings, monkeypatch):
    fake_judge(monkeypatch, lambda r: judgment())
    batch = batch_of(1, 2, 3)
    assert jev_scoring.score_batch(repo, batch, settings) == 3
    for metadata, _, _ in batch:
        assert metadata.jev_scored_at is not None
        assert metadata.jev_flat_display_p == 0.9 and metadata.jev_model == "jev-test"


def test_judgments_persist_through_bulk_upsert(repo, settings, monkeypatch):
    fake_judge(monkeypatch, lambda r: judgment())
    batch = batch_of(1, 2)
    jev_scoring.score_batch(repo, batch, settings)
    repo.bulk_create_or_update(batch, commit=True)
    rows = repo.session.query(Artwork).order_by(Artwork.original_id).all()
    assert [r.jev_scored_at is not None for r in rows] == [True, True]
    assert rows[0].jev_attribution == "named_artist" and rows[0].jev_appeal == 4.0


def test_already_judged_rows_are_not_rescored(repo, settings, monkeypatch):
    fake_judge(monkeypatch, lambda r: judgment())
    first = batch_of(1)
    jev_scoring.score_batch(repo, first, settings)
    repo.bulk_create_or_update(first, commit=True)

    calls = []
    fake_judge(monkeypatch, lambda r: calls.append(r["title"]) or judgment())
    again = batch_of(1, 2)  # 1 already judged in DB, 2 is new
    assert jev_scoring.score_batch(repo, again, settings) == 1
    assert calls == ["Work 2"]
    assert again[0][0].jev_scored_at is None


def test_redownload_does_not_wipe_existing_judgment(repo, settings, monkeypatch):
    fake_judge(monkeypatch, lambda r: judgment(flat=0.77))
    first = batch_of(1)
    jev_scoring.score_batch(repo, first, settings)
    repo.bulk_create_or_update(first, commit=True)

    repo.bulk_create_or_update(batch_of(1), commit=True)  # fresh, unjudged metadata
    row = repo.session.query(Artwork).one()
    assert row.jev_flat_display_p == 0.77 and row.jev_scored_at is not None

    repo.create_or_update_artwork(make_metadata(1), "met", None)  # single-item path too
    repo.session.refresh(row)
    assert row.jev_flat_display_p == 0.77


def test_partial_failures_leave_those_unjudged(repo, settings, monkeypatch):
    fake_judge(monkeypatch, lambda r: None if r["title"] == "Work 2" else judgment())
    batch = batch_of(1, 2, 3)
    assert jev_scoring.score_batch(repo, batch, settings) == 2
    assert [m.jev_scored_at is not None for m, _, _ in batch] == [True, False, True]


def test_disabled_without_api_key_makes_no_calls(repo, settings, monkeypatch):
    settings.typesafe_api_key = None
    monkeypatch.setattr(dj, "judge_artworks", lambda *a, **k: pytest.fail("must not call Jev"))
    assert jev_scoring.score_batch(repo, batch_of(1), settings) == 0


def test_disabled_by_flag(repo, settings, monkeypatch):
    settings.jev_scoring_enabled = False
    monkeypatch.setattr(dj, "judge_artworks", lambda *a, **k: pytest.fail("must not call Jev"))
    assert jev_scoring.score_batch(repo, batch_of(1), settings) == 0


def test_unexpected_error_never_propagates(repo, settings, monkeypatch):
    async def boom(rows, **kw):
        raise RuntimeError("sdk exploded")

    monkeypatch.setattr(dj, "judge_artworks", boom)
    batch = batch_of(1, 2)
    assert jev_scoring.score_batch(repo, batch, settings) == 0
    assert all(m.jev_scored_at is None for m, _, _ in batch)


def test_empty_batch_is_a_noop(repo, settings):
    assert jev_scoring.score_batch(repo, [], settings) == 0


def test_metadata_to_row_maps_fields():
    m = make_metadata(1, medium="Oil on canvas", artist_bio="b", description="d")
    row = jev_scoring.metadata_to_row(m, "met")
    assert row["museum_code"] == "met" and row["medium"] == "Oil on canvas"
    assert row["artist_bio"] == "b" and row["description"] == "d"
