from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class DisplayRatios:
    """Common display aspect ratios"""
    IPHONE_14 = 0.46  # 9:19.5
    IPAD = 0.75       # 3:4
    MACBOOK = 1.6     # 16:10
    MONITOR = 1.77    # 16:9
    
    # Tolerance ranges for good display fit
    RATIO_RANGES = {
        'phone': (0.45, 0.48),    # iPhone-like
        'tablet': (0.70, 0.80),   # iPad-like
        'laptop': (1.5, 1.7),     # MacBook-like
        'monitor': (1.6, 1.8)     # Standard monitors
    }
    
    @staticmethod
    def get_suitable_displays(aspect_ratio: float, tolerance: float = 0.1) -> List[str]:
        """Get list of suitable displays for a given aspect ratio"""
        suitable = []
        for display, (min_ratio, max_ratio) in DisplayRatios.RATIO_RANGES.items():
            if min_ratio - tolerance <= aspect_ratio <= max_ratio + tolerance:
                suitable.append(display)
        return suitable