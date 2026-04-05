from src.museums.wellcome import WellcomeArtworkFactory

SAMPLE_IMAGE = {
    "id": "abc123img",
    "locations": [
        {
            "license": {
                "id": "cc-by",
                "label": "Attribution 4.0 International (CC BY 4.0)",
            }
        }
    ],
    "source": {
        "id": "work789",
        "title": "The nervous system of the frog",
        "contributors": [
            {"agent": {"label": "Thomas Henry Huxley"}}
        ],
        "dates": [{"label": "1871"}],
        "description": "An illustration showing nerve anatomy.",
        "subjects": [{"label": "Anatomy"}, {"label": "Frogs"}],
    },
}


def test_factory_creates_metadata():
    factory = WellcomeArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_IMAGE)

    assert metadata is not None
    assert metadata.id == "abc123img"
    assert metadata.title == "The nervous system of the frog"
    assert metadata.artist == "Thomas Henry Huxley"
    assert metadata.date_display == "1871"
    assert metadata.description == "An illustration showing nerve anatomy."
    assert metadata.keywords == ["Anatomy", "Frogs"]
    assert metadata.is_public_domain is False  # CC-BY, not CC0
    assert metadata.primary_image_url == (
        "https://iiif.wellcomecollection.org/image/abc123img/full/max/0/default.jpg"
    )


def test_factory_handles_no_contributors():
    factory = WellcomeArtworkFactory()
    data = {**SAMPLE_IMAGE, "source": {**SAMPLE_IMAGE["source"], "contributors": []}}
    metadata = factory.create_metadata(data)
    assert metadata.artist == "Unknown Artist"


def test_factory_returns_none_on_empty():
    factory = WellcomeArtworkFactory()
    assert factory.create_metadata({}) is None
