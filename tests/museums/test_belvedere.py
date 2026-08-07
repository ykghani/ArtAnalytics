from unittest.mock import MagicMock, patch

from src.museums.belvedere import (
    BELVEDERE_LISTING_URL,
    BELVEDERE_MANIFEST_URL_TMPL,
    BelvedereArtworkFactory,
    BelvedereClient,
    BelvedereProgressTracker,
    _extract_manifest_image,
    _extract_object_ids,
    _extract_total_count,
    _is_rights_restricted,
    _parse_canvas_label,
)
from src.museums.museum_info import MuseumInfo


def _make_museum_info(rate_limit: float = 0.0):
    return MuseumInfo(
        name="Belvedere",
        base_url="https://sammlung.belvedere.at",
        code="belvedere",
        rate_limit=rate_limit,
    )


def _mock_response(text: str = "", json_data=None, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_MANIFEST = {
    "label": "null",
    "metadata": [],
    "description": [],
    "attribution": "null",
    "sequences": [
        {
            "label": "default",
            "canvases": [
                {
                    "label": (
                        "Franz Xaver Messerschmidt, \"Charakterkopf\" Nr. 27, 1964, "
                        "Gipsabguss, graphitiert, 43 cm, Belvedere, Wien, Inv.-Nr. 5667b"
                    ),
                    "width": 2835,
                    "height": 3508,
                    "images": [
                        {
                            "resource": {
                                "@id": "https://sammlung.belvedere.at/apis/iiif/image/v2/122400/full/full/0/default.jpg",
                                "format": "image/jpeg",
                            }
                        }
                    ],
                }
            ],
        }
    ],
}

LISTING_HTML = """
<html><body>
<a href="/objects/10726/charakterkopf-nr-27;jsessionid=ABC?ctx=x&idx=5">thumb</a>
<a href="/objects/10726/charakterkopf-nr-27;jsessionid=ABC?ctx=x&idx=5">title</a>
<a href="/objects/4417/charakterkopf-nr-2;jsessionid=ABC?ctx=x&idx=0">thumb</a>
<div>Showing 1-12 of 5,897</div>
</body></html>
"""

EMPTY_LISTING_HTML = "<html><body><div>No results</div></body></html>"


# ---------- pure helper functions ----------

def test_extract_object_ids_dedupes_and_preserves_order():
    ids = _extract_object_ids(LISTING_HTML)
    assert ids == ["10726", "4417"]


def test_extract_object_ids_empty_page():
    assert _extract_object_ids(EMPTY_LISTING_HTML) == []


def test_extract_total_count():
    assert _extract_total_count(LISTING_HTML) == 5897
    assert _extract_total_count(EMPTY_LISTING_HTML) is None


def test_is_rights_restricted():
    assert _is_rights_restricted("null") is False
    assert _is_rights_restricted("") is False
    assert _is_rights_restricted(None) is False
    assert _is_rights_restricted("© Fria Elfen-Frenken") is True


def test_parse_canvas_label():
    artist, title, date_display = _parse_canvas_label(
        "Franz Xaver Messerschmidt, \"Charakterkopf\" Nr. 27, 1964, "
        "Gipsabguss, graphitiert, 43 cm, Belvedere, Wien, Inv.-Nr. 5667b"
    )
    assert artist == "Franz Xaver Messerschmidt"
    assert title == '"Charakterkopf" Nr. 27'
    assert date_display == "1964"


def test_parse_canvas_label_falls_back_to_whole_label_without_quotes():
    artist, title, date_display = _parse_canvas_label(
        "Klaus Basset, 1968, Papier, Blattmaße: 41,3 x 30,3 cm, Belvedere, Wien, Inv.-Nr. 11686/17"
    )
    assert artist == "Klaus Basset"
    assert date_display == "1968"
    assert title == (
        "Klaus Basset, 1968, Papier, Blattmaße: 41,3 x 30,3 cm, "
        "Belvedere, Wien, Inv.-Nr. 11686/17"
    )


def test_extract_manifest_image():
    label, image_url = _extract_manifest_image(SAMPLE_MANIFEST)
    assert "Messerschmidt" in label
    assert image_url == "https://sammlung.belvedere.at/apis/iiif/image/v2/122400/full/full/0/default.jpg"


def test_extract_manifest_image_missing_sequences():
    assert _extract_manifest_image({"sequences": []}) is None


# ---------- factory ----------

def test_factory_creates_metadata():
    factory = BelvedereArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_MANIFEST, "10726")

    assert metadata is not None
    assert metadata.id == "10726"
    assert metadata.accession_number == "5667b"
    assert metadata.artist == "Franz Xaver Messerschmidt"
    assert metadata.title == '"Charakterkopf" Nr. 27'
    assert metadata.date_display == "1964"
    assert metadata.is_public_domain is True
    assert metadata.credit_line == "Belvedere, Wien"
    assert metadata.primary_image_url.endswith("/default.jpg")
    assert metadata.image_urls["iiif"] == metadata.primary_image_url


def test_factory_skips_rights_restricted_attribution():
    factory = BelvedereArtworkFactory()
    restricted = {**SAMPLE_MANIFEST, "attribution": "© Fria Elfen-Frenken"}
    assert factory.create_metadata(restricted, "108127") is None


def test_factory_skips_missing_image():
    factory = BelvedereArtworkFactory()
    no_image = {**SAMPLE_MANIFEST, "sequences": []}
    assert factory.create_metadata(no_image, "10726") is None


def test_factory_accession_falls_back_to_object_id():
    factory = BelvedereArtworkFactory()
    manifest = {
        **SAMPLE_MANIFEST,
        "sequences": [
            {
                "canvases": [
                    {
                        "label": "Unknown Artist, undated",
                        "images": SAMPLE_MANIFEST["sequences"][0]["canvases"][0]["images"],
                    }
                ]
            }
        ],
    }
    metadata = factory.create_metadata(manifest, "99999")
    assert metadata.accession_number == "BELVEDERE-99999"


# ---------- client pagination / resume ----------

class TestBelvedereClientPagination:
    def test_iter_collection_scrapes_listing_then_fetches_manifests(self):
        client = BelvedereClient(museum_info=_make_museum_info())
        responses = [
            _mock_response(text=LISTING_HTML),
            _mock_response(json_data=SAMPLE_MANIFEST),
            _mock_response(json_data=SAMPLE_MANIFEST),
            _mock_response(text=EMPTY_LISTING_HTML),
        ]
        with patch.object(client.session, "get", side_effect=responses) as mock_get:
            results = list(client._iter_collection_impl())

        assert [m.id for m in results] == ["10726", "4417"]

        # page 1 listing request
        assert mock_get.call_args_list[0][0][0] == BELVEDERE_LISTING_URL
        assert mock_get.call_args_list[0][1]["params"] == {"page": 1}
        # manifest requests for each discovered id
        assert mock_get.call_args_list[1][0][0] == BELVEDERE_MANIFEST_URL_TMPL.format(object_id="10726")
        assert mock_get.call_args_list[2][0][0] == BELVEDERE_MANIFEST_URL_TMPL.format(object_id="4417")
        # page 2 listing request (terminates the loop)
        assert mock_get.call_args_list[3][1]["params"] == {"page": 2}

    def test_iter_collection_skips_already_processed_ids(self, tmp_path):
        tracker = BelvedereProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.processed_ids.add("10726")
        client = BelvedereClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        responses = [
            _mock_response(text=LISTING_HTML),
            _mock_response(json_data=SAMPLE_MANIFEST),  # only for id "4417"
            _mock_response(text=EMPTY_LISTING_HTML),
        ]
        with patch.object(client.session, "get", side_effect=responses) as mock_get:
            results = list(client._iter_collection_impl())

        assert [m.id for m in results] == ["4417"]
        assert mock_get.call_args_list[1][0][0] == BELVEDERE_MANIFEST_URL_TMPL.format(object_id="4417")

    def test_iter_collection_resumes_from_last_page(self, tmp_path):
        tracker = BelvedereProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.last_page = 3
        client = BelvedereClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        with patch.object(client.session, "get", return_value=_mock_response(text=EMPTY_LISTING_HTML)) as mock_get:
            list(client._iter_collection_impl())

        assert mock_get.call_args_list[0][1]["params"] == {"page": 3}

    def test_iter_collection_advances_and_persists_last_page(self, tmp_path):
        tracker = BelvedereProgressTracker(progress_file=tmp_path / "progress.json")
        client = BelvedereClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        responses = [
            _mock_response(text=LISTING_HTML),
            _mock_response(json_data=SAMPLE_MANIFEST),
            _mock_response(json_data=SAMPLE_MANIFEST),
            _mock_response(text=EMPTY_LISTING_HTML),
        ]
        with patch.object(client.session, "get", side_effect=responses):
            list(client._iter_collection_impl())

        assert tracker.state.last_page == 2
