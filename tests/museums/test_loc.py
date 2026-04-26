from unittest.mock import MagicMock, patch

from src.museums.loc import LOCArtworkFactory, LOCClient, _extract_loc_iiif_url
from src.museums.museum_info import MuseumInfo


def _make_museum_info():
    return MuseumInfo(
        name="Library of Congress",
        base_url="https://www.loc.gov/pictures/",
        code="loc",
    )

SAMPLE_ITEM = {
    "id": "https://www.loc.gov/item/2002699540/",
    "title": "The Grand Canyon of the Yellowstone [Wyoming]",
    "contributor": ["Thomas Moran"],
    "date": "1876",
    "description": ["Chromolithograph"],
    "subject": ["Yellowstone National Park", "Landscapes"],
    "rights_advisory": "No known restrictions on publication.",
    "resources": [
        {
            "files": [
                [
                    {
                        "url": "https://tile.loc.gov/image-services/iiif/service:pnp:pga:03700:03793v/full/max/0/default.jpg",
                        "width": 4000,
                        "height": 2904,
                    }
                ]
            ],
        }
    ],
}


def test_extract_iiif_url():
    url = _extract_loc_iiif_url(SAMPLE_ITEM)
    assert url == (
        "https://tile.loc.gov/image-services/iiif/"
        "service:pnp:pga:03700:03793v/full/max/0/default.jpg"
    )


def test_factory_creates_metadata():
    factory = LOCArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_ITEM)

    assert metadata is not None
    assert metadata.id == "2002699540"
    assert metadata.title == "The Grand Canyon of the Yellowstone [Wyoming]"
    assert metadata.artist == "Thomas Moran"
    assert metadata.date_display == "1876"
    assert metadata.is_public_domain is True
    assert "iiif" in metadata.primary_image_url


def test_factory_skips_restricted_items():
    factory = LOCArtworkFactory()
    restricted = {**SAMPLE_ITEM, "rights_advisory": "Rights may apply."}
    assert factory.create_metadata(restricted) is None


def test_factory_skips_missing_image():
    factory = LOCArtworkFactory()
    no_image = {**SAMPLE_ITEM, "resources": []}
    assert factory.create_metadata(no_image) is None


# ---------- LOCClient format_filter tests ----------

class TestLOCClientFormatFilter:
    def test_default_format_filter_is_none(self):
        client = LOCClient(museum_info=_make_museum_info())
        assert client.format_filter is None

    def test_format_filter_stored(self):
        client = LOCClient(museum_info=_make_museum_info(), format_filter="poster")
        assert client.format_filter == "poster"

    def test_get_collection_info_includes_fa_param_when_filter_set(self):
        client = LOCClient(museum_info=_make_museum_info(), format_filter="poster")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"pagination": {"total": 42}}
        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            client.get_collection_info()
            call_params = mock_get.call_args[1]["params"]
            assert call_params.get("fa") == "original-format:poster"

    def test_get_collection_info_omits_fa_param_when_no_filter(self):
        client = LOCClient(museum_info=_make_museum_info())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"pagination": {"total": 0}}
        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            client.get_collection_info()
            call_params = mock_get.call_args[1]["params"]
            assert "fa" not in call_params

    def test_iter_collection_includes_fa_param_when_filter_set(self):
        client = LOCClient(museum_info=_make_museum_info(), format_filter="poster")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [],
            "pagination": {"total": 0, "next": None},
        }
        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            list(client._iter_collection_impl())
            call_params = mock_get.call_args[1]["params"]
            assert call_params.get("fa") == "original-format:poster"

    def test_iter_collection_omits_fa_param_when_no_filter(self):
        client = LOCClient(museum_info=_make_museum_info())
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [],
            "pagination": {"total": 0, "next": None},
        }
        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            list(client._iter_collection_impl())
            call_params = mock_get.call_args[1]["params"]
            assert "fa" not in call_params
