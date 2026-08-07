"""
Unit tests for scripts/verify_museum.py.

These tests exercise the verifier's checks in isolation — no real network calls.
They mock the museum client and HTTP layer so the verifier can be tested offline.
"""

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# Make the project root importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_museum import verify, _detect_format, _url_ok


# ---------------------------------------------------------------------------
# Helpers — build synthetic artwork metadata and image bytes
# ---------------------------------------------------------------------------

def _make_jpeg_bytes(width: int = 800, height: int = 600) -> bytes:
    """Return minimal valid JPEG bytes."""
    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_metadata(
    id_: str = "1",
    title: str = "Test",
    artist: str = "Artist",
    is_public_domain: bool = True,
    image_url: str = "https://example-museum.org/image/1.jpg",
):
    m = MagicMock()
    m.id = id_
    m.title = title
    m.artist = artist
    m.is_public_domain = is_public_domain
    m.primary_image_url = image_url
    return m


def _make_metadata_list(count: int = 15, **kwargs) -> list:
    return [_make_metadata(id_=str(i), **kwargs) for i in range(count)]


def _make_client(artworks: list, collection_info: dict = None) -> MagicMock:
    client = MagicMock()
    client.iter_collection.return_value = iter(artworks)
    client.get_collection_info.return_value = collection_info or {"total_objects": len(artworks)}
    return client


def _fake_museum_config(client: MagicMock, slug: str = "testmuseum") -> dict:
    museum_info = MagicMock()
    museum_info.code = slug
    museum_info.name = "Test Museum"

    client_class = MagicMock(return_value=client)

    return {
        "museum_info": museum_info,
        "client_class": client_class,
        "params": {},
    }


# ---------------------------------------------------------------------------
# Tests for helpers
# ---------------------------------------------------------------------------

def test_detect_format_jpeg():
    data = _make_jpeg_bytes()
    assert _detect_format(data) == "JPEG"


def test_detect_format_png():
    img = Image.new("RGB", (100, 100))
    buf = BytesIO()
    img.save(buf, format="PNG")
    assert _detect_format(buf.getvalue()) == "PNG"


def test_detect_format_unknown():
    assert _detect_format(b"<html>not an image</html>") is None


def test_url_ok_valid():
    assert _url_ok("https://museum.org/image/123.jpg") is True


def test_url_ok_empty():
    assert _url_ok("") is False
    assert _url_ok(None) is False


def test_url_ok_no_scheme():
    assert _url_ok("museum.org/image.jpg") is False


def test_url_ok_data_uri():
    assert _url_ok("data:image/jpeg;base64,/9j") is False


def test_url_ok_example_host():
    assert _url_ok("https://example.com/img.jpg") is False


# ---------------------------------------------------------------------------
# Full verify() tests — mock get_museum_config and HTTP
# ---------------------------------------------------------------------------

SETTINGS_PATH = "scripts.verify_museum.settings"
MUSEUM_CONFIG_PATH = "scripts.verify_museum.get_museum_config"


def _run_verify(slug: str, artworks: list, http_responses: dict = None, *, api_key: str = "") -> dict:
    """
    Run verify() with mocked museum config and HTTP fetching.

    http_responses: {url: bytes | None}  — None means network error.
    If http_responses is omitted, each URL gets a uniquely-sized valid JPEG
    (varying dimensions avoid triggering the uniformity check).
    """
    client = _make_client(artworks)
    config = _fake_museum_config(client, slug)

    settings_mock = MagicMock()
    settings_mock.initialize_paths = MagicMock()
    settings_mock.museums = {slug: MagicMock(api_key=api_key)}

    _call_count = [0]

    def fake_fetch(url, extra_headers):
        if http_responses is not None:
            return http_responses.get(url)
        # Return uniquely-sized images (800+i × 600+i) to avoid uniformity trigger
        idx = _call_count[0]
        _call_count[0] += 1
        return _make_jpeg_bytes(width=800 + idx * 10, height=600 + idx * 7)

    with (
        patch(SETTINGS_PATH, settings_mock),
        patch(MUSEUM_CONFIG_PATH, return_value=config),
        patch("scripts.verify_museum._fetch_bytes", side_effect=fake_fetch),
    ):
        return verify(slug)


# ---------------------------------------------------------------------------
# Check A: non-empty
# ---------------------------------------------------------------------------

class TestCheckA:
    def test_enough_metadata_passes(self):
        artworks = _make_metadata_list(15)
        result = _run_verify("test", artworks)
        assert result["checks"]["A_non_empty"] is True

    def test_too_few_metadata_fails(self):
        artworks = _make_metadata_list(5)
        result = _run_verify("test", artworks)
        assert result["checks"]["A_non_empty"] is False
        assert result["status"] == "FAIL"

    def test_zero_metadata_with_error_is_inconclusive(self):
        client = MagicMock()
        client.iter_collection.side_effect = ConnectionError("network down")
        config = _fake_museum_config(client, "test")
        settings_mock = MagicMock()
        settings_mock.museums = {"test": MagicMock(api_key="")}

        with (
            patch(SETTINGS_PATH, settings_mock),
            patch(MUSEUM_CONFIG_PATH, return_value=config),
            patch("scripts.verify_museum._fetch_bytes", return_value=_make_jpeg_bytes()),
        ):
            result = verify("test")

        assert result["status"] == "INCONCLUSIVE"
        assert result["checks"]["A_non_empty"] is False


# ---------------------------------------------------------------------------
# Check B: licence filter
# ---------------------------------------------------------------------------

class TestCheckB:
    def test_all_public_domain_passes(self):
        artworks = _make_metadata_list(15, is_public_domain=True)
        result = _run_verify("test", artworks)
        assert result["checks"]["B_licence_filter"] is True

    def test_non_public_domain_fails(self):
        artworks = _make_metadata_list(12, is_public_domain=True)
        # Inject one non-public-domain item
        artworks.insert(3, _make_metadata(id_="bad", is_public_domain=False))
        result = _run_verify("test", artworks)
        assert result["checks"]["B_licence_filter"] is False
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Check C: URL well-formed
# ---------------------------------------------------------------------------

class TestCheckC:
    def test_valid_urls_pass(self):
        artworks = _make_metadata_list(15, image_url="https://museum.org/img/1.jpg")
        result = _run_verify("test", artworks)
        assert result["checks"]["C_url_well_formed"] is True

    def test_empty_url_fails(self):
        artworks = _make_metadata_list(15, image_url="")
        result = _run_verify("test", artworks)
        assert result["checks"]["C_url_well_formed"] is False

    def test_placeholder_host_fails(self):
        artworks = _make_metadata_list(15, image_url="https://example.com/img.jpg")
        result = _run_verify("test", artworks)
        assert result["checks"]["C_url_well_formed"] is False


# ---------------------------------------------------------------------------
# Check D: images are real
# ---------------------------------------------------------------------------

class TestCheckD:
    def test_real_images_pass(self):
        artworks = _make_metadata_list(15, image_url="https://museum.org/img/{i}.jpg")
        # Patch image URLs to be unique
        for i, a in enumerate(artworks):
            a.primary_image_url = f"https://museum.org/img/{i}.jpg"
        result = _run_verify("test", artworks)
        assert result["checks"]["D_images_real"] is True
        assert result["status"] == "PASS"

    def test_html_served_as_200_fails(self):
        artworks = _make_metadata_list(15)
        for i, a in enumerate(artworks):
            a.primary_image_url = f"https://museum.org/img/{i}.jpg"

        html_response = b"<html><body>Error 403</body></html>"
        responses = {f"https://museum.org/img/{i}.jpg": html_response for i in range(15)}

        result = _run_verify("test", artworks, http_responses=responses)
        assert result["checks"]["D_images_real"] is False

    def test_all_network_failures_is_inconclusive(self):
        artworks = _make_metadata_list(15)
        for i, a in enumerate(artworks):
            a.primary_image_url = f"https://museum.org/img/{i}.jpg"

        # All image fetches fail (None = network error)
        responses = {f"https://museum.org/img/{i}.jpg": None for i in range(15)}

        result = _run_verify("test", artworks, http_responses=responses)
        assert result["status"] == "INCONCLUSIVE"

    def test_tiny_uniform_images_fail(self):
        """All images are byte-identical 1x1 pixel — placeholder."""
        artworks = _make_metadata_list(15)
        for i, a in enumerate(artworks):
            a.primary_image_url = f"https://museum.org/img/{i}.jpg"

        tiny_img = Image.new("RGB", (1, 1))
        buf = BytesIO()
        tiny_img.save(buf, format="JPEG")
        tiny_bytes = buf.getvalue()

        # Same response for all URLs
        responses = {f"https://museum.org/img/{i}.jpg": tiny_bytes for i in range(15)}

        result = _run_verify("test", artworks, http_responses=responses)
        assert result["status"] == "FAIL"
        assert result["checks"]["D_images_real"] is False

    def test_images_too_small_fails(self):
        """Images that decode but are below MIN_DIM threshold."""
        artworks = _make_metadata_list(15)
        for i, a in enumerate(artworks):
            a.primary_image_url = f"https://museum.org/img/{i}.jpg"

        small_bytes = _make_jpeg_bytes(width=100, height=100)
        responses = {f"https://museum.org/img/{i}.jpg": small_bytes for i in range(15)}

        result = _run_verify("test", artworks, http_responses=responses)
        # More than half are too small → D fails
        assert result["checks"]["D_images_real"] is False
