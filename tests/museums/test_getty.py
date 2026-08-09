from unittest.mock import MagicMock, patch

from src.museums.getty import (
    GETTY_SPARQL_PAGE_SIZE,
    GETTYClient,
    GETTYProgressTracker,
)
from src.museums.schemas import GETTYArtworkFactory
from src.museums.museum_info import MuseumInfo


def _make_museum_info(rate_limit: float = 0.0):
    return MuseumInfo(
        name="J. Paul Getty Museum",
        base_url="https://data.getty.edu/museum/collection",
        code="getty",
        rate_limit=rate_limit,
    )


def _mock_response(json_data=None, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_OBJECT = {
    "id": "https://data.getty.edu/museum/collection/object/e099dc86-c28e-4ad0-8b02-d7d258caec56",
    "type": "HumanMadeObject",
    "identified_by": [
        {
            "type": "Name",
            "content": "Irises",
            "classified_as": [
                {
                    "id": "https://data.getty.edu/local/thesaurus/object-title-primary",
                    "_label": "Primary Title",
                }
            ],
        },
        {
            "type": "Identifier",
            "content": "90.PA.20",
            "_label": "Accession Number",
        },
    ],
    "produced_by": {
        "referred_to_by": [
            {"_label": "Artist/Maker (Producer) Name", "content": "Vincent van Gogh"},
            {"_label": "Artist/Maker (Producer) Description", "content": "Dutch, 1853 - 1890"},
        ]
    },
    "dimension": [
        {"_label": "Height", "value": 74.3, "unit": {"_label": "cm"}},
        {"_label": "Width", "value": 94.3, "unit": {"_label": "cm"}},
    ],
    "shows": [
        {
            "type": "VisualItem",
            "id": "https://data.getty.edu/media/image/abc123",
            "classified_as": [
                {"id": "http://vocab.getty.edu/aat/300215302", "_label": "Digital Image"}
            ],
        }
    ],
    "subject_to": [
        {
            "_label": "License for Collection Metadata",
            "classified_as": [
                {"id": "http://creativecommons.org/publicdomain/zero/1.0/", "_label": "CC0"}
            ],
        }
    ],
}

SAMPLE_IMAGE = {
    "id": "https://data.getty.edu/media/image/abc123",
    "subject_to": [
        {
            "classified_as": [
                {"id": "http://creativecommons.org/publicdomain/zero/1.0/", "_label": "CC0"}
            ]
        }
    ],
    "digitally_shown_by": [
        {
            "access_point": [
                {"id": "https://media.getty.edu/iiif/image/abc123", "_label": "iiif-image"},
                {
                    "id": "https://media.getty.edu/iiif/image/abc123/full/max/0/default.jpg",
                    "_label": "full-resolution",
                },
                {
                    "id": "https://media.getty.edu/iiif/image/abc123/full/!600,600/0/default.jpg",
                    "_label": "thumbnail",
                },
            ]
        }
    ],
}


def _sparql_bindings(uris):
    return {"results": {"bindings": [{"obj": {"value": uri}} for uri in uris]}}


# ---------- factory ----------


def test_factory_creates_metadata():
    factory = GETTYArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_OBJECT, SAMPLE_IMAGE)

    assert metadata is not None
    assert metadata.id == "e099dc86-c28e-4ad0-8b02-d7d258caec56"
    assert metadata.accession_number == "90.PA.20"
    assert metadata.title == "Irises"
    assert metadata.artist == "Vincent van Gogh"
    assert metadata.artist_display == "Dutch, 1853 - 1890"
    assert metadata.height_cm == 74.3
    assert metadata.width_cm == 94.3
    assert metadata.is_public_domain is True
    assert metadata.credit_line == "Courtesy of the J. Paul Getty Museum, Los Angeles"
    assert metadata.primary_image_url == (
        "https://media.getty.edu/iiif/image/abc123/full/max/0/default.jpg"
    )
    assert metadata.image_urls["thumbnail"] == (
        "https://media.getty.edu/iiif/image/abc123/full/!600,600/0/default.jpg"
    )


def test_factory_requires_both_metadata_and_image_cc0():
    non_cc0_image = {
        **SAMPLE_IMAGE,
        "subject_to": [{"classified_as": [{"id": "https://rightsstatements.org/vocab/InC/1.0/"}]}],
    }
    metadata = GETTYArtworkFactory().create_metadata(SAMPLE_OBJECT, non_cc0_image)
    assert metadata is not None
    assert metadata.is_public_domain is False


def test_factory_metadata_not_cc0_is_not_public_domain():
    non_cc0_object = {
        **SAMPLE_OBJECT,
        "subject_to": [
            {
                "_label": "License for Collection Metadata",
                "classified_as": [{"id": "https://rightsstatements.org/vocab/InC/1.0/"}],
            }
        ],
    }
    metadata = GETTYArtworkFactory().create_metadata(non_cc0_object, SAMPLE_IMAGE)
    assert metadata is not None
    assert metadata.is_public_domain is False


def test_factory_skips_missing_image():
    assert GETTYArtworkFactory().create_metadata(SAMPLE_OBJECT, None) is None


def test_factory_returns_none_on_empty():
    assert GETTYArtworkFactory().create_metadata({}) is None


def test_factory_defaults_title_and_artist_when_missing():
    entry = {
        "id": "https://data.getty.edu/museum/collection/object/xyz",
        "identified_by": [],
        "produced_by": {},
        "shows": SAMPLE_OBJECT["shows"],
    }
    metadata = GETTYArtworkFactory().create_metadata(entry, SAMPLE_IMAGE)
    assert metadata.title == "Untitled"
    assert metadata.artist == "Unknown"


# ---------- client ----------


class TestGETTYClient:
    def test_iter_collection_paginates_via_sparql_offset(self):
        client = GETTYClient(museum_info=_make_museum_info())
        uris = [SAMPLE_OBJECT["id"]] * GETTY_SPARQL_PAGE_SIZE
        page1 = _sparql_bindings(uris)
        page2 = _sparql_bindings([SAMPLE_OBJECT["id"]])
        empty = _sparql_bindings([])

        with patch.object(
            client, "_sparql_query", side_effect=[page1, page2, empty]
        ) as mock_sparql, patch.object(
            client, "_get_artwork_details_impl", return_value=None
        ) as mock_details:
            list(client._iter_collection_impl())

        # short page (page2 has 1 < GETTY_SPARQL_PAGE_SIZE) stops the loop without a 3rd query
        assert mock_sparql.call_count == 2
        assert mock_details.call_count == GETTY_SPARQL_PAGE_SIZE + 1

    def test_iter_collection_stops_on_empty_page(self):
        client = GETTYClient(museum_info=_make_museum_info())
        with patch.object(client, "_sparql_query", return_value=_sparql_bindings([])) as mock_sparql:
            results = list(client._iter_collection_impl())

        assert results == []
        assert mock_sparql.call_count == 1

    def test_iter_collection_resumes_from_saved_offset(self, tmp_path):
        tracker = GETTYProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.last_offset = 400
        client = GETTYClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        with patch.object(client, "_sparql_query", return_value=_sparql_bindings([])) as mock_sparql:
            list(client._iter_collection_impl())

        query_used = mock_sparql.call_args[0][0]
        assert "OFFSET 400" in query_used

    def test_iter_collection_skips_already_processed_ids(self, tmp_path):
        tracker = GETTYProgressTracker(progress_file=tmp_path / "progress.json")
        tracker.state.processed_ids.add("e099dc86-c28e-4ad0-8b02-d7d258caec56")
        client = GETTYClient(museum_info=_make_museum_info(), progress_tracker=tracker)

        page = _sparql_bindings([SAMPLE_OBJECT["id"]])
        empty = _sparql_bindings([])
        with patch.object(client, "_sparql_query", side_effect=[page, empty]), patch.object(
            client, "_get_artwork_details_impl"
        ) as mock_details:
            list(client._iter_collection_impl())

        mock_details.assert_not_called()

    def test_get_artwork_details_fetches_object_then_image(self):
        client = GETTYClient(museum_info=_make_museum_info())
        with patch.object(
            client.session,
            "get",
            side_effect=[_mock_response(SAMPLE_OBJECT), _mock_response(SAMPLE_IMAGE)],
        ) as mock_get:
            metadata = client._get_artwork_details_impl("e099dc86-c28e-4ad0-8b02-d7d258caec56")

        assert metadata is not None
        assert metadata.id == "e099dc86-c28e-4ad0-8b02-d7d258caec56"
        assert mock_get.call_count == 2
        first_url = mock_get.call_args_list[0][0][0]
        assert first_url == (
            "https://data.getty.edu/museum/collection/object/e099dc86-c28e-4ad0-8b02-d7d258caec56"
        )
        second_url = mock_get.call_args_list[1][0][0]
        assert second_url == "https://data.getty.edu/media/image/abc123"

    def test_get_artwork_details_handles_image_fetch_failure(self):
        client = GETTYClient(museum_info=_make_museum_info())
        image_resp = _mock_response(status_code=500)
        image_resp.raise_for_status.side_effect = Exception("server error")
        with patch.object(
            client.session, "get", side_effect=[_mock_response(SAMPLE_OBJECT), image_resp]
        ):
            metadata = client._get_artwork_details_impl("e099dc86-c28e-4ad0-8b02-d7d258caec56")

        # No usable image data means the factory can't build a primary image URL.
        assert metadata is None

    def test_get_collection_info(self):
        client = GETTYClient(museum_info=_make_museum_info())
        count_response = {"results": {"bindings": [{"count": {"value": "93380"}}]}}
        with patch.object(client, "_sparql_query", return_value=count_response):
            info = client.get_collection_info()

        assert info == {"total_objects": 93380}
