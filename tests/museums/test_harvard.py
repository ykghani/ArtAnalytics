from unittest.mock import MagicMock, patch

from src.museums.harvard import (
    HARVARD_PAGE_SIZE,
    HARVARDClient,
    HARVARDProgressTracker,
)
from src.museums.schemas import HARVARDArtworkFactory
from src.museums.museum_info import MuseumInfo


def _make_museum_info(rate_limit: float = 0.0):
    return MuseumInfo(
        name="Harvard Art Museums",
        base_url="https://api.harvardartmuseums.org",
        code="harvard",
        rate_limit=rate_limit,
    )


def _mock_response(json_data=None, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_RECORD = {
    "objectid": 12345,
    "objectnumber": "1943.100",
    "title": "Self-Portrait",
    "people": [
        {
            "name": "Rembrandt van Rijn",
            "role": "Artist",
            "displaydate": "born 1606, died 1669",
            "culture": "Dutch",
        }
    ],
    "dated": "1629",
    "datebegin": 1629,
    "dateend": 1629,
    "medium": "Oil on panel",
    "dimensions": "15.5 x 12.7 cm",
    "department": "Division of European and American Art",
    "classification": "Paintings",
    "culture": "Dutch",
    "style": "Baroque",
    "creditline": "Harvard Art Museums/Fogg Museum",
    "description": "An early self-portrait.",
    "provenance": "Purchased 1943.",
    "imagepermissionlevel": 0,
    "primaryimageurl": "https://nrs.harvard.edu/urn-3:HUAM:INV12345_dynmc",
    "images": [
        {
            "baseimageurl": "https://ids.lib.harvard.edu/ids/iiif/12345",
            "width": 4000,
            "height": 3200,
        }
    ],
}


def _object_page(records, page=1, pages=1, total=None):
    total = total if total is not None else len(records)
    return {
        "info": {"totalrecordsperquery": len(records), "totalrecords": total, "pages": pages, "page": page},
        "records": records,
    }


# ---------- factory ----------

def test_factory_creates_metadata():
    factory = HARVARDArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_RECORD)

    assert metadata is not None
    assert metadata.id == "12345"
    assert metadata.accession_number == "1943.100"
    assert metadata.title == "Self-Portrait"
    assert metadata.artist == "Rembrandt van Rijn"
    assert metadata.artist_display == "born 1606, died 1669"
    assert metadata.artist_nationality == "Dutch"
    assert metadata.artist_birth_year == 1606
    assert metadata.artist_death_year == 1669
    assert metadata.date_display == "1629"
    assert metadata.date_start == "1629"
    assert metadata.date_end == "1629"
    assert metadata.medium == "Oil on panel"
    assert metadata.department == "Division of European and American Art"
    assert metadata.artwork_type == "Paintings"
    assert metadata.culture == ["Dutch"]
    assert metadata.style == "Baroque"
    assert metadata.is_public_domain is True
    assert metadata.credit_line == "Harvard Art Museums/Fogg Museum"
    assert metadata.primary_image_url == "https://nrs.harvard.edu/urn-3:HUAM:INV12345_dynmc"
    assert metadata.image_urls["primary"] == "https://nrs.harvard.edu/urn-3:HUAM:INV12345_dynmc"
    assert metadata.image_urls["full"] == (
        "https://ids.lib.harvard.edu/ids/iiif/12345/full/full/0/default.jpg"
    )


def test_factory_marks_restricted_images_as_not_public_domain():
    entry = {**SAMPLE_RECORD, "imagepermissionlevel": 1}
    metadata = HARVARDArtworkFactory().create_metadata(entry)
    assert metadata is not None
    assert metadata.is_public_domain is False


def test_factory_falls_back_to_iiif_full_url_when_no_primary_image():
    entry = {**SAMPLE_RECORD, "primaryimageurl": None}
    metadata = HARVARDArtworkFactory().create_metadata(entry)
    assert metadata.primary_image_url == (
        "https://ids.lib.harvard.edu/ids/iiif/12345/full/full/0/default.jpg"
    )


def test_factory_skips_missing_image():
    entry = {**SAMPLE_RECORD, "primaryimageurl": None, "images": []}
    assert HARVARDArtworkFactory().create_metadata(entry) is None


def test_factory_returns_none_on_empty():
    assert HARVARDArtworkFactory().create_metadata({}) is None


def test_factory_defaults_title_and_artist_when_missing():
    entry = {
        "objectid": 1,
        "people": [],
        "images": SAMPLE_RECORD["images"],
        "imagepermissionlevel": 0,
    }
    metadata = HARVARDArtworkFactory().create_metadata(entry)
    assert metadata.title == "Untitled"
    assert metadata.artist == "Unknown"


# ---------- client ----------

class TestHARVARDClient:
    def test_api_key_attached_as_session_param_not_header(self):
        client = HARVARDClient(museum_info=_make_museum_info(), api_key="SECRET")
        assert client.session.params == {"apikey": "SECRET"}
        assert "Authorization" not in client.session.headers

    def test_iter_collection_paginates_until_last_page(self):
        client = HARVARDClient(museum_info=_make_museum_info())
        page1 = _object_page([SAMPLE_RECORD], page=1, pages=2, total=2)
        page2 = _object_page([SAMPLE_RECORD], page=2, pages=2, total=2)
        with patch.object(client.session, "get", side_effect=[
            _mock_response(page1), _mock_response(page2),
        ]) as mock_get:
            results = list(client._iter_collection_impl(hasimage=1, q="imagepermissionlevel:0"))

        assert len(results) == 2
        assert mock_get.call_count == 2
        first_params = mock_get.call_args_list[0][1]["params"]
        assert first_params["page"] == 1
        assert first_params["size"] == HARVARD_PAGE_SIZE
        assert first_params["hasimage"] == 1
        assert first_params["q"] == "imagepermissionlevel:0"
        second_params = mock_get.call_args_list[1][1]["params"]
        assert second_params["page"] == 2

    def test_iter_collection_stops_on_empty_records(self):
        client = HARVARDClient(museum_info=_make_museum_info())
        empty_page = _object_page([], page=1, pages=1, total=0)
        with patch.object(client.session, "get", return_value=_mock_response(empty_page)) as mock_get:
            results = list(client._iter_collection_impl())

        assert results == []
        assert mock_get.call_count == 1

    def test_iter_collection_resumes_from_saved_page(self, tmp_path):
        tracker = HARVARDProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.last_page = 5
        client = HARVARDClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        page5 = _object_page([], page=5, pages=5, total=0)
        with patch.object(client.session, "get", return_value=_mock_response(page5)) as mock_get:
            list(client._iter_collection_impl())

        first_params = mock_get.call_args_list[0][1]["params"]
        assert first_params["page"] == 5

    def test_get_artwork_details(self):
        client = HARVARDClient(museum_info=_make_museum_info())
        with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_RECORD)):
            metadata = client._get_artwork_details_impl("12345")

        assert metadata is not None
        assert metadata.id == "12345"

    def test_get_collection_info(self):
        client = HARVARDClient(museum_info=_make_museum_info())
        info_response = {"info": {"totalrecords": 224111}}
        with patch.object(client.session, "get", return_value=_mock_response(info_response)):
            info = client.get_collection_info()

        assert info == {"total_objects": 224111}
