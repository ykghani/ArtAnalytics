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
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <responseDate>2026-04-05T18:52:36Z</responseDate>
          <ListRecords>
            {record_xml}
            {token_el}
          </ListRecords>
        </OAI-PMH>
    """)


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
