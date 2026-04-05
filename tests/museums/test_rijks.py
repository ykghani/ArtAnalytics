from src.museums.rijks import RijksArtworkFactory

SAMPLE_OBJECT = {
    "objectNumber": "SK-A-4",
    "title": "The Milkmaid",
    "longTitle": "The Milkmaid, Johannes Vermeer, c. 1660",
    "principalOrFirstMaker": "Johannes Vermeer",
    "webImage": {
        "guid": "8ac17e34-da63-4e2e-8888-1234567890ab",
        "width": 2500,
        "height": 2789,
        "url": "https://lh3.googleusercontent.com/example_image_url",
    },
    "dating": {
        "presentingDate": "c. 1660",
        "yearEarly": 1658,
        "yearLate": 1662,
    },
    "productionPlaces": ["Delft"],
    "links": {
        "self": "https://www.rijksmuseum.nl/api/nl/collection/SK-A-4",
        "web": "https://www.rijksmuseum.nl/nl/collectie/SK-A-4",
    },
}

SAMPLE_DETAIL = {
    "artObject": {
        **SAMPLE_OBJECT,
        "physicalMedium": "oil on canvas",
        "dimensions": [
            {"unit": "cm", "type": "height", "value": "45.5"},
            {"unit": "cm", "type": "width", "value": "41"},
        ],
        "plaqueDescriptionEnglish": "This painting shows a milkmaid.",
        "acquisition": {"creditLine": "Purchased 1908"},
        "classification": {"objectNumbers": ["SK-A-4"]},
        "subTitle": "oil on canvas, 45.5 x 41 cm",
    }
}


def test_factory_creates_metadata_from_list_item():
    factory = RijksArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_OBJECT)

    assert metadata is not None
    assert metadata.id == "SK-A-4"
    assert metadata.title == "The Milkmaid"
    assert metadata.artist == "Johannes Vermeer"
    assert metadata.date_display == "c. 1660"
    assert metadata.date_start == "1658"
    assert metadata.date_end == "1662"
    assert metadata.is_public_domain is True
    assert metadata.primary_image_url == "https://lh3.googleusercontent.com/example_image_url=s0"


def test_factory_creates_metadata_from_detail():
    factory = RijksArtworkFactory()
    metadata = factory.create_metadata(SAMPLE_DETAIL["artObject"])

    assert metadata.medium == "oil on canvas"
    assert metadata.height_cm == 45.5
    assert metadata.width_cm == 41.0
    assert metadata.description == "This painting shows a milkmaid."


def test_factory_returns_none_without_image():
    factory = RijksArtworkFactory()
    no_img = {**SAMPLE_OBJECT, "webImage": None}
    assert factory.create_metadata(no_img) is None
