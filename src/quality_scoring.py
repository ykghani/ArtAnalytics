"""
Quality score calculation for artwork images.

Calculates display-specific quality scores based on image dimensions
and target display specifications.

⚠️ IMPORTANT: Quality scoring algorithm must stay in sync with
ArtServe-Backend/scripts/calculate_quality_scores.py to ensure consistency.
"""

from typing import Dict
from .display_specs import DISPLAY_SPECS, DISPLAY_TYPES


def calculate_quality_score_for_display(
    image_width: int,
    image_height: int,
    display_type: str
) -> int:
    """
    Calculate quality score (0-100) for artwork on specific display.

    The score is based on two equally-weighted factors:
    1. Resolution Score (0-50 points): How close is image resolution to display resolution?
    2. Aspect Ratio Score (0-50 points): How well does aspect ratio match display?

    Args:
        image_width: Image width in pixels
        image_height: Image height in pixels
        display_type: Target display type (e.g., "macbook_16", "firetv_4k")

    Returns:
        Quality score from 0-100, where:
        - 90-100: Excellent quality for this display
        - 75-89: Good quality
        - 50-74: Acceptable quality
        - 0-49: Poor quality

    Raises:
        ValueError: If display_type is not recognized
    """
    if display_type not in DISPLAY_SPECS:
        raise ValueError(f"Unknown display type: {display_type}")

    spec = DISPLAY_SPECS[display_type]

    # 1. Resolution Score (0-50 points)
    image_pixels = image_width * image_height
    display_pixels = spec["width"] * spec["height"]
    resolution_ratio = image_pixels / display_pixels

    if resolution_ratio >= 1.0:
        # Image meets or exceeds display resolution
        resolution_score = 50
    elif resolution_ratio >= 0.75:
        # Good resolution (75-100% of display)
        resolution_score = 40 + (resolution_ratio - 0.75) * 40  # 40-50
    elif resolution_ratio >= 0.5:
        # Acceptable resolution (50-75% of display)
        resolution_score = 25 + (resolution_ratio - 0.5) * 60  # 25-40
    else:
        # Poor resolution (<50% of display)
        resolution_score = resolution_ratio * 50  # 0-25

    # 2. Aspect Ratio Score (0-50 points)
    image_aspect = image_width / image_height
    aspect_diff = abs(image_aspect - spec["aspect"]) / spec["aspect"]

    if aspect_diff <= 0.05:
        # Perfect match (within 5%)
        aspect_score = 50
    elif aspect_diff <= 0.10:
        # Good match (5-10% difference)
        aspect_score = 40 + (1 - aspect_diff / 0.10) * 10  # 40-50
    elif aspect_diff <= 0.20:
        # Acceptable match (10-20% difference)
        aspect_score = 25 + (1 - (aspect_diff - 0.10) / 0.10) * 15  # 25-40
    else:
        # Poor match (>20% difference)
        aspect_score = max(0, 25 - (aspect_diff - 0.20) * 50)  # 0-25

    total_score = int(resolution_score + aspect_score)
    return max(0, min(100, total_score))


def calculate_quality_scores_for_all_displays(
    image_width: int,
    image_height: int
) -> Dict[str, int]:
    """
    Calculate quality scores for all supported display types.

    Args:
        image_width: Image width in pixels
        image_height: Image height in pixels

    Returns:
        Dictionary mapping display types to quality scores (0-100)
        Example: {"macbook_14": 85, "macbook_16": 90, "firetv_4k": 78, ...}
    """
    scores = {}
    for display_type in DISPLAY_TYPES:
        score = calculate_quality_score_for_display(image_width, image_height, display_type)
        scores[display_type] = score

    return scores


def calculate_average_quality_score(quality_scores: Dict[str, int]) -> int:
    """
    Calculate average quality score across all display types.

    Used for backward compatibility with the single quality_score column.

    Args:
        quality_scores: Dictionary of display-specific scores

    Returns:
        Average quality score (0-100)
    """
    if not quality_scores:
        return 0

    return int(sum(quality_scores.values()) / len(quality_scores))
