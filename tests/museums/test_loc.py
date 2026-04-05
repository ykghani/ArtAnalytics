from src.museums.loc import LOCArtworkFactory, _extract_loc_iiif_url

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
