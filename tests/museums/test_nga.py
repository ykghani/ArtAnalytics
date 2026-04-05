import pytest
from src.museums.nga import NGAArtworkFactory


SAMPLE_ROW = {
    "objectid": "46538",
    "accessionnumber": "1941.4.1",
    "attribution": "Vincent van Gogh",
    "title": "Self-Portrait",
    "displaydate": "1889",
    "beginyear": "1889",
    "endyear": "1889",
    "medium": "Oil on canvas",
    "dimensions": "57.79 × 44.45 cm (22 3/4 × 17 1/2 in.)",
    "classification": "Painting",
    "subclassification": "French paintings",
    "custodian": "Collection of Mr. and Mrs. Paul Mellon",
    "uuid": "d80ed4d3-5c53-4d5a-a9c3-333cae4fb0b6",
}


def test_factory_creates_metadata():
    factory = NGAArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_ROW)

    assert metadata is not None
    assert metadata.id == "46538"
    assert metadata.accession_number == "1941.4.1"
    assert metadata.title == "Self-Portrait"
    assert metadata.artist == "Vincent van Gogh"
    assert metadata.date_display == "1889"
    assert metadata.date_start == "1889"
    assert metadata.medium == "Oil on canvas"
    assert metadata.artwork_type == "Painting"
    assert metadata.is_public_domain is True
    assert metadata.primary_image_url == (
        "https://api.nga.gov/iiif/d80ed4d3-5c53-4d5a-a9c3-333cae4fb0b6"
        "/full/!2000,2000/0/default.jpg"
    )
    assert metadata.image_urls == {
        "iiif": "https://api.nga.gov/iiif/d80ed4d3-5c53-4d5a-a9c3-333cae4fb0b6"
                "/full/!2000,2000/0/default.jpg"
    }


def test_factory_handles_missing_uuid():
    factory = NGAArtworkFactory()
    row = {**SAMPLE_ROW, "uuid": ""}
    metadata = factory.create_metadata(row)
    assert metadata is None


def test_factory_handles_missing_objectid():
    factory = NGAArtworkFactory()
    metadata = factory.create_metadata({})
    assert metadata is None
