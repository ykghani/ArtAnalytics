from unittest.mock import MagicMock, patch

import pytest

from src.museums.lacma import (
    LACMA_PER_PAGE,
    LACMAClient,
    LACMAProgressTracker,
)
from src.museums.schemas import LACMAArtworkFactory
from src.museums.museum_info import MuseumInfo


@pytest.fixture(autouse=True)
def _mock_image_dimensions():
    """LACMAArtworkFactory does a live header-read to get pixel dimensions
    (LACMA's API doesn't supply them). Mock it everywhere in this file so
    tests stay fast/deterministic and don't depend on lacma.org being up."""
    with patch(
        "src.museums.schemas.fetch_remote_image_dimensions", return_value=(800, 600)
    ):
        yield


def _make_museum_info(rate_limit: float = 0.0):
    return MuseumInfo(
        name="LACMA",
        base_url="https://collections.lacma.org/api/search",
        code="lacma",
        rate_limit=rate_limit,
    )


def _mock_response(json_data=None, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_ENTRY = {
    "id": 35024,
    "data": {
        "object": {
            "titles": [{"title": "Bust of a Woman", "titleType": "Primary Title", "displayOrder": 1}],
            "dated": "1791",
            "department": "European Painting and Sculpture",
            "classification": "Sculpture",
            "constituents": [
                {
                    "role": "Artist",
                    "displayName": "Augustin Pajou",
                    "displayBio": "French, 1730-1809",
                    "nationality": "French",
                    "beginDate": "1730",
                    "endDate": "1809",
                }
            ],
            "images": [
                {
                    "renditions": {
                        "access": "https://collections-images.lacma.org/images/35024/35024-1-print.tif",
                        "desktop": "https://collections-images.lacma.org/images/35024/35024-1-desktop.jpg",
                        "primary": "https://collections-images.lacma.org/images/35024/35024-1-primary.webp",
                        "thumbnail": "https://collections-images.lacma.org/images/35024/35024-1-thumbnail.webp",
                    },
                    "webCaption": "<p>Augustin Pajou, <i>Bust of a Woman</i></p>",
                    "displayOrder": 1,
                    "copyrightText": "",
                }
            ],
            "medium": "Plaster on painted wood socle and plinth",
            "dimensions": "68.6 x 45.7 x 30.5 cm",
            "creditLine": "Gift of The Ahmanson Foundation",
            "accessionNumber": "M.75.101",
            "placeMade": ["France"],
            "culture": None,
        }
    },
}


def _search_page(results, total):
    return {"total": total, "results": results, "facets": {}}


# ---------- factory ----------

def test_factory_creates_metadata():
    factory = LACMAArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_ENTRY)

    assert metadata is not None
    assert metadata.id == "35024"
    assert metadata.accession_number == "M.75.101"
    assert metadata.title == "Bust of a Woman"
    assert metadata.artist == "Augustin Pajou"
    assert metadata.artist_display == "French, 1730-1809"
    assert metadata.artist_nationality == "French"
    assert metadata.artist_birth_year == 1730
    assert metadata.artist_death_year == 1809
    assert metadata.date_display == "1791"
    assert metadata.department == "European Painting and Sculpture"
    assert metadata.artwork_type == "Sculpture"
    assert metadata.culture == ["France"]
    assert metadata.is_public_domain is True
    assert metadata.credit_line == "Gift of The Ahmanson Foundation"
    assert metadata.primary_image_url == (
        "https://collections-images.lacma.org/images/35024/35024-1-desktop.jpg"
    )
    assert metadata.image_urls["access"].endswith("-print.tif")
    assert metadata.image_pixel_width == 800
    assert metadata.image_pixel_height == 600


def test_factory_prefers_explicit_culture_over_place_made():
    entry = {
        **SAMPLE_ENTRY,
        "data": {
            "object": {
                **SAMPLE_ENTRY["data"]["object"],
                "culture": "Ming dynasty",
                "placeMade": ["China"],
            }
        },
    }
    metadata = LACMAArtworkFactory().create_metadata(entry)
    assert metadata.culture == ["Ming dynasty"]


def test_factory_skips_missing_image():
    entry = {
        **SAMPLE_ENTRY,
        "data": {"object": {**SAMPLE_ENTRY["data"]["object"], "images": []}},
    }
    assert LACMAArtworkFactory().create_metadata(entry) is None


def test_factory_returns_none_on_empty():
    assert LACMAArtworkFactory().create_metadata({}) is None


def test_factory_defaults_title_and_artist_when_missing():
    entry = {
        "id": 1,
        "data": {
            "object": {
                "titles": [],
                "constituents": [],
                "images": SAMPLE_ENTRY["data"]["object"]["images"],
            }
        },
    }
    metadata = LACMAArtworkFactory().create_metadata(entry)
    assert metadata.title == "Untitled"
    assert metadata.artist == "Unknown"


# ---------- client pagination / cap / resume ----------

class TestLACMAClientPagination:
    def test_iter_department_paginates_until_short_page(self):
        client = LACMAClient(museum_info=_make_museum_info())
        page1 = _search_page([SAMPLE_ENTRY] * LACMA_PER_PAGE, total=150)
        page2 = _search_page([SAMPLE_ENTRY] * 50, total=150)
        with patch.object(client.session, "post", side_effect=[
            _mock_response(page1), _mock_response(page2),
        ]) as mock_post:
            results = list(client._iter_department("Prints and Drawings", dept_idx=0))

        assert len(results) == LACMA_PER_PAGE + 50
        assert mock_post.call_count == 2
        first_body = mock_post.call_args_list[0][1]["json"]
        assert first_body["department"] == ["Prints and Drawings"]
        assert first_body["hasImage"] is True
        assert first_body["publicDomain"] is True
        assert first_body["page"] == 1
        second_body = mock_post.call_args_list[1][1]["json"]
        assert second_body["page"] == 2

    def test_iter_department_stops_at_10000_result_window(self):
        """Server clamps/repeats pages past the 10k window — must not loop forever."""
        client = LACMAClient(museum_info=_make_museum_info())
        full_page = _search_page([SAMPLE_ENTRY] * LACMA_PER_PAGE, total=50000)
        with patch.object(client.session, "post", return_value=_mock_response(full_page)) as mock_post:
            results = list(client._iter_department("Prints and Drawings", dept_idx=0))

        max_pages = 10000 // LACMA_PER_PAGE
        assert mock_post.call_count == max_pages
        assert len(results) == max_pages * LACMA_PER_PAGE

    def test_iter_collection_uses_discovered_departments(self):
        client = LACMAClient(museum_info=_make_museum_info())
        facets_response = {
            "total": 2,
            "results": [],
            "facets": {"department": [{"value": "Photography", "label": "Photography", "count": 1}]},
        }
        dept_page = _search_page([SAMPLE_ENTRY], total=1)
        empty_final = _search_page([], total=1)
        with patch.object(client.session, "post", side_effect=[
            _mock_response(facets_response),  # discovery
            _mock_response(dept_page),  # department page 1
        ]):
            results = list(client._iter_collection_impl())

        assert [m.id for m in results] == ["35024"]

    def test_iter_collection_skips_already_processed_ids(self, tmp_path):
        tracker = LACMAProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.processed_ids.add("35024")
        client = LACMAClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        dept_page = _search_page([SAMPLE_ENTRY], total=1)
        with patch.object(client.session, "post", return_value=_mock_response(dept_page)):
            results = list(client._iter_department("Photography", dept_idx=0))

        assert results == []

    def test_iter_collection_resumes_from_saved_department_and_page(self, tmp_path):
        tracker = LACMAProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.department_index = 1
        tracker.state.last_page = 3
        client = LACMAClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        empty = _search_page([], total=0)
        with patch.object(client.session, "post", return_value=_mock_response(empty)) as mock_post:
            list(client._iter_collection_impl(departments=["Egyptian Art", "Photography"]))

        # First department resumed is index 1 ("Photography"), starting at page 3.
        first_body = mock_post.call_args_list[0][1]["json"]
        assert first_body["department"] == ["Photography"]
        assert first_body["page"] == 3

    def test_get_artwork_details_not_supported(self):
        client = LACMAClient(museum_info=_make_museum_info())
        assert client._get_artwork_details_impl("35024") is None
