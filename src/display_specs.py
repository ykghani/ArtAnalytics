"""
Display specifications for various device types.

⚠️ IMPORTANT: This file must stay in sync with ArtServe-Backend/src/api/display_specs.py
If you modify display specifications, update both repositories.

Each display has width, height, and calculated aspect ratio.
Used for quality score calculations during artwork download.
"""

# Display specifications with calculated aspect ratios
# MUST match ArtServe-Backend/src/api/display_specs.py
DISPLAY_SPECS = {
    "macbook_14": {
        "width": 3024,
        "height": 1964,
        "aspect": 3024 / 1964,  # ~1.54
    },
    "macbook_16": {
        "width": 3456,
        "height": 2234,
        "aspect": 3456 / 2234,  # ~1.55
    },
    "monitor": {
        "width": 2560,
        "height": 1440,
        "aspect": 2560 / 1440,  # ~1.78 (16:9)
    },
    "imac_24": {
        "width": 4480,
        "height": 2520,
        "aspect": 4480 / 2520,  # ~1.78 (16:9)
    },
    "imac_27": {
        "width": 5120,
        "height": 2880,
        "aspect": 5120 / 2880,  # ~1.78 (16:9)
    },
    "firetv_1080p": {
        "width": 1920,
        "height": 1080,
        "aspect": 1920 / 1080,  # ~1.78 (16:9)
    },
    "firetv_4k": {
        "width": 3840,
        "height": 2160,
        "aspect": 3840 / 2160,  # ~1.78 (16:9)
    },
    "tv_1080p": {
        "width": 1920,
        "height": 1080,
        "aspect": 1920 / 1080,  # ~1.78 (16:9)
    },
    "tv_4k": {
        "width": 3840,
        "height": 2160,
        "aspect": 3840 / 2160,  # ~1.78 (16:9)
    },
}

# List of all supported display types
DISPLAY_TYPES = list(DISPLAY_SPECS.keys())
