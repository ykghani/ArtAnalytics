from src.museums.tepapa import TePapaArtworkFactory

SAMPLE_OBJECT = {
    "id": 39481,
    "type": "Object",
    "title": "Kahu huruhuru (feather cloak)",
    "additionalType": ["Clothing"],
    "date": "c. 1840",
    "production": [
        {
            "contributor": {
                "title": "Unknown Māori artist",
                "type": "Person"
            },
            "role": {"value": "maker"},
        }
    ],
    "description": "A feather cloak made of native bird feathers.",
    "subject": [{"value": "Māori"}, {"value": "Textile"}],
    "hasRepresentation": [
        {
            "rights": {
                "allowsDownload": True,
                "rightsType": {"value": "CC BY 4.0"},
            },
            "media": {
                "contentUrl": "https://collections.tepapa.govt.nz/Media/1234/preview",
                "width": 4000,
                "height": 3000,
            },
        }
    ],
}


def test_factory_creates_metadata():
    factory = TePapaArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_OBJECT)

    assert metadata is not None
    assert metadata.id == "39481"
    assert metadata.title == "Kahu huruhuru (feather cloak)"
    assert metadata.artist == "Unknown Māori artist"
    assert metadata.date_display == "c. 1840"
    assert metadata.primary_image_url == (
        "https://collections.tepapa.govt.nz/Media/1234/preview"
    )
    assert metadata.artwork_type == "Object"


def test_factory_skips_non_downloadable():
    factory = TePapaArtworkFactory()
    restricted = {
        **SAMPLE_OBJECT,
        "hasRepresentation": [
            {
                "rights": {"allowsDownload": False},
                "media": {"contentUrl": "https://example.com/img.jpg"},
            }
        ],
    }
    assert factory.create_metadata(restricted) is None


def test_factory_returns_none_on_empty():
    factory = TePapaArtworkFactory()
    assert factory.create_metadata({}) is None
