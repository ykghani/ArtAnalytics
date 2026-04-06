"""Tests for the Rijksmuseum OAI-PMH client refactor."""
import json
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

NS = {
    'oai':     'http://www.openarchives.org/OAI/2.0/',
    'dc':      'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
    'edm':     'http://www.europeana.eu/schemas/edm/',
    'ore':     'http://www.openarchives.org/ore/terms/',
    'rdf':     'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'skos':    'http://www.w3.org/2004/02/skos/core#',
}

SAMPLE_RECORD_XML = textwrap.dedent("""\
    <record xmlns="http://www.openarchives.org/OAI/2.0/">
      <header>
        <identifier>https://id.rijksmuseum.nl/200064126</identifier>
        <datestamp>2026-04-05T18:52:36Z</datestamp>
      </header>
      <metadata>
        <rdf:RDF
          xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
          xmlns:ore="http://www.openarchives.org/ore/terms/"
          xmlns:edm="http://www.europeana.eu/schemas/edm/"
          xmlns:dc="http://purl.org/dc/elements/1.1/"
          xmlns:dcterms="http://purl.org/dc/terms/"
          xmlns:skos="http://www.w3.org/2004/02/skos/core#">
          <ore:Aggregation>
            <edm:isShownBy rdf:resource="https://iiif.micr.io/ABCDEF/full/max/0/default.jpg"/>
            <edm:rights rdf:resource="https://creativecommons.org/publicdomain/zero/1.0/"/>
          </ore:Aggregation>
          <edm:ProvidedCHO>
            <dc:identifier>SK-A-3262</dc:identifier>
            <dc:title>Night Watch</dc:title>
            <dc:creator rdf:resource="https://id.rijksmuseum.nl/21029638"/>
            <dcterms:created>1642</dcterms:created>
            <dcterms:medium rdf:resource="https://id.rijksmuseum.nl/medium/oil"/>
            <dcterms:extent>height 363 cm \u00d7 width 437 cm</dcterms:extent>
            <dc:description>Famous Dutch painting.</dc:description>
            <dc:type>painting</dc:type>
          </edm:ProvidedCHO>
          <edm:Agent rdf:about="https://id.rijksmuseum.nl/21029638">
            <skos:prefLabel>Rembrandt van Rijn</skos:prefLabel>
          </edm:Agent>
        </rdf:RDF>
      </metadata>
    </record>
""")

SAMPLE_RECORD_RESTRICTED_XML = SAMPLE_RECORD_XML.replace(
    "https://creativecommons.org/publicdomain/zero/1.0/",
    "https://creativecommons.org/licenses/by/4.0/",
)

SAMPLE_RECORD_NO_IMAGE_XML = SAMPLE_RECORD_XML.replace(
    '<edm:isShownBy rdf:resource="https://iiif.micr.io/ABCDEF/full/max/0/default.jpg"/>',
    "",
)


def _wrap_list_records(record_xml: str, token: str = "", complete_size: int = 1) -> str:
    token_el = (
        f'<resumptionToken completeListSize="{complete_size}">{token}</resumptionToken>'
        if token
        else f'<resumptionToken completeListSize="{complete_size}"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">\n'
        '  <responseDate>2024-01-01T00:00:00Z</responseDate>\n'
        f'  <ListRecords>\n{record_xml}    {token_el}\n  </ListRecords>\n'
        '</OAI-PMH>\n'
    )


# ── Task 1: _parse_dimensions_edm ─────────────────────────────────────────────

from src.museums.rijks import _parse_dimensions_edm


def test_parse_dimensions_standard():
    h, w = _parse_dimensions_edm("height 363 cm \u00d7 width 437 cm")
    assert h == 363.0
    assert w == 437.0


def test_parse_dimensions_only_height():
    h, w = _parse_dimensions_edm("height 45.5 cm")
    assert h == 45.5
    assert w is None


def test_parse_dimensions_no_match():
    h, w = _parse_dimensions_edm("some random string")
    assert h is None
    assert w is None


def test_parse_dimensions_none_input():
    h, w = _parse_dimensions_edm(None)
    assert h is None
    assert w is None


def test_parse_dimensions_mm_units():
    # Only 'cm' is recognised; 'mm' returns (None, None)
    h, w = _parse_dimensions_edm("height 100 mm \u00d7 width 200 mm")
    assert h is None
    assert w is None


# ── Task 3: _is_public_domain_rights  and  _xml_record_to_dict ───────────────

from src.museums.rijks import _is_public_domain_rights, _xml_record_to_dict


def test_is_public_domain_zero():
    assert _is_public_domain_rights("https://creativecommons.org/publicdomain/zero/1.0/") is True


def test_is_public_domain_mark():
    assert _is_public_domain_rights("https://creativecommons.org/publicdomain/mark/1.0/") is True


def test_is_public_domain_cc_by():
    assert _is_public_domain_rights("https://creativecommons.org/licenses/by/4.0/") is False


def test_is_public_domain_empty():
    assert _is_public_domain_rights("") is False


def test_is_public_domain_none():
    assert _is_public_domain_rights(None) is False


def test_xml_record_to_dict_full():
    root = ET.fromstring(SAMPLE_RECORD_XML)
    d = _xml_record_to_dict(root)

    assert d["oai_identifier"] == "https://id.rijksmuseum.nl/200064126"
    assert d["accession_number"] == "SK-A-3262"
    assert d["title"] == "Night Watch"
    assert d["artist"] == "Rembrandt van Rijn"
    assert d["date_display"] == "1642"
    assert d["image_url"] == "https://iiif.micr.io/ABCDEF/full/max/0/default.jpg"
    assert d["rights_uri"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert d["is_public_domain"] is True
    assert d["height_cm"] == 363.0
    assert d["width_cm"] == 437.0
    assert d["description"] == "Famous Dutch painting."
    assert d["artwork_type"] == "painting"


def test_xml_record_to_dict_no_artist_agent():
    xml = SAMPLE_RECORD_XML.replace(
        '      <edm:Agent rdf:about="https://id.rijksmuseum.nl/21029638">\n'
        '        <skos:prefLabel>Rembrandt van Rijn</skos:prefLabel>\n'
        '      </edm:Agent>',
        "",
    )
    root = ET.fromstring(xml)
    d = _xml_record_to_dict(root)
    assert d["artist"] == ""


def test_xml_record_to_dict_no_image():
    root = ET.fromstring(SAMPLE_RECORD_NO_IMAGE_XML)
    d = _xml_record_to_dict(root)
    assert d["image_url"] is None


def test_xml_record_to_dict_restricted():
    root = ET.fromstring(SAMPLE_RECORD_RESTRICTED_XML)
    d = _xml_record_to_dict(root)
    assert d["is_public_domain"] is False


# ── Task 5: RijksArtworkFactory ───────────────────────────────────────────────

from src.museums.rijks import RijksArtworkFactory

SAMPLE_DICT = {
    "oai_identifier":   "https://id.rijksmuseum.nl/200064126",
    "accession_number": "SK-A-3262",
    "title":            "Night Watch",
    "artist":           "Rembrandt van Rijn",
    "date_display":     "1642",
    "description":      "Famous Dutch painting.",
    "artwork_type":     "painting",
    "image_url":        "https://iiif.micr.io/ABCDEF/full/max/0/default.jpg",
    "rights_uri":       "https://creativecommons.org/publicdomain/zero/1.0/",
    "is_public_domain": True,
    "height_cm":        363.0,
    "width_cm":         437.0,
}


def test_factory_creates_metadata():
    factory = RijksArtworkFactory()
    m = factory.create_metadata(SAMPLE_DICT)

    assert m is not None
    assert m.id == "SK-A-3262"
    assert m.accession_number == "SK-A-3262"
    assert m.title == "Night Watch"
    assert m.artist == "Rembrandt van Rijn"
    assert m.date_display == "1642"
    assert m.is_public_domain is True
    assert m.primary_image_url == "https://iiif.micr.io/ABCDEF/full/max/0/default.jpg"
    assert m.image_urls == {"full": "https://iiif.micr.io/ABCDEF/full/max/0/default.jpg"}
    assert m.height_cm == 363.0
    assert m.width_cm == 437.0
    assert m.description == "Famous Dutch painting."
    assert m.artwork_type == "painting"


def test_factory_returns_none_without_accession():
    factory = RijksArtworkFactory()
    assert factory.create_metadata({**SAMPLE_DICT, "accession_number": ""}) is None


def test_factory_returns_none_without_image():
    factory = RijksArtworkFactory()
    assert factory.create_metadata({**SAMPLE_DICT, "image_url": None}) is None


def test_factory_not_public_domain_returns_none():
    factory = RijksArtworkFactory()
    assert factory.create_metadata({**SAMPLE_DICT, "is_public_domain": False}) is None


def test_factory_fallback_artist():
    factory = RijksArtworkFactory()
    m = factory.create_metadata({**SAMPLE_DICT, "artist": ""})
    assert m.artist == "Unknown Artist"


def test_factory_fallback_title():
    factory = RijksArtworkFactory()
    m = factory.create_metadata({**SAMPLE_DICT, "title": ""})
    assert m.title == "Untitled"
