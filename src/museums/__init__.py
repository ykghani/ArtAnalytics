def __getattr__(name):
    if name in ('MetClient', 'MetImageProcessor', 'MetProgressTracker'):
        from .met import MetClient, MetImageProcessor, MetProgressTracker
        return locals()[name]
    elif name in ('AICClient', 'AICImageProcessor', 'AICProgressTracker'):
        from .aic import AICClient, AICImageProcessor, AICProgressTracker
        return locals()[name]
    elif name in ('CMAClient', 'CMAImageProcessor', 'CMAProgressTracker'):
        from .cma import CMAClient, CMAImageProcessor, CMAProgressTracker
        return locals()[name]
    elif name in ('MuseumAPIClient', 'MuseumImageProcessor'):
        from .base import MuseumAPIClient, MuseumImageProcessor
        return locals()[name]
    elif name in ('ArtworkMetadata', 'MuseumInfo'):
        from .schemas import ArtworkMetadata, MuseumInfo
        return locals()[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = [
    'MetClient', 'MetImageProcessor', 'MetProgressTracker',
    'AICClient', 'AICImageProcessor', 'AICProgressTracker',
    'CMAClient', 'CMAImageProcessor', 'CMAProgressTracker',
    'MuseumAPIClient', 'MuseumImageProcessor',
    'ArtworkMetadata', 'MuseumInfo'
]