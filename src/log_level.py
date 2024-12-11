from enum import Enum

class LogLevel(str, Enum):
    """Log level settings for application"""
    NONE = "none"           # No logging
    ERRORS_ONLY = "errors"  # Only log errors
    PROGRESS = "progress"   # Only progress updates
    ARTWORK = "artwork"     # Artwork + progress updates
    DEBUG = "debug"         # All logging including debug

# log_level = LogLevel("debug")