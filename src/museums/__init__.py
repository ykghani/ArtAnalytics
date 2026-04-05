def __getattr__(name):
    if name in ("MetClient", "MetImageProcessor", "MetProgressTracker"):
        from .met import MetClient, MetImageProcessor, MetProgressTracker

        return locals()[name]
    elif name in ("AICClient", "AICImageProcessor", "AICProgressTracker"):
        from .aic import AICClient, AICImageProcessor, AICProgressTracker

        return locals()[name]
    elif name in ("CMAClient", "CMAImageProcessor", "CMAProgressTracker"):
        from .cma import CMAClient, CMAImageProcessor, CMAProgressTracker

        return locals()[name]
    elif name in ("MIAClient", "MIAImageProcessor", "MIAProgressTracker"):
        from .mia import MIAClient, MIAImageProcessor, MIAProgressTracker

        return locals()[name]
    elif name in ("SMKClient", "SMKImageProcessor", "SMKProgressTracker"):
        from .smk import SMKClient, SMKImageProcessor, SMKProgressTracker

        return locals()[name]
    elif name in ("NGAClient", "NGAImageProcessor", "NGAProgressTracker"):
        from .nga import NGAClient, NGAImageProcessor, NGAProgressTracker

        return locals()[name]
    elif name in ("WellcomeClient", "WellcomeImageProcessor", "WellcomeProgressTracker"):
        from .wellcome import WellcomeClient, WellcomeImageProcessor, WellcomeProgressTracker

        return locals()[name]
    elif name in ("LOCClient", "LOCImageProcessor", "LOCProgressTracker"):
        from .loc import LOCClient, LOCImageProcessor, LOCProgressTracker

        return locals()[name]
    elif name in ("RijksClient", "RijksImageProcessor", "RijksProgressTracker"):
        from .rijks import RijksClient, RijksImageProcessor, RijksProgressTracker

        return locals()[name]
    elif name in ("TePapaClient", "TePapaImageProcessor", "TePapaProgressTracker"):
        from .tepapa import TePapaClient, TePapaImageProcessor, TePapaProgressTracker

        return locals()[name]
    elif name in ("MuseumAPIClient", "MuseumImageProcessor"):
        from .base import MuseumAPIClient, MuseumImageProcessor

        return locals()[name]
    elif name in ("ArtworkMetadata", "MuseumInfo"):
        from .schemas import ArtworkMetadata, MuseumInfo

        return locals()[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "MetClient",
    "MetImageProcessor",
    "MetProgressTracker",
    "AICClient",
    "AICImageProcessor",
    "AICProgressTracker",
    "CMAClient",
    "CMAImageProcessor",
    "CMAProgressTracker",
    "MIAClient",
    "MIAImageProcessor",
    "MIAProgressTracker",
    "SMKClient",
    "SMKImageProcessor",
    "SMKProgressTracker",
    "NGAClient",
    "NGAImageProcessor",
    "NGAProgressTracker",
    "WellcomeClient",
    "WellcomeImageProcessor",
    "WellcomeProgressTracker",
    "LOCClient",
    "LOCImageProcessor",
    "LOCProgressTracker",
    "RijksClient",
    "RijksImageProcessor",
    "RijksProgressTracker",
    "TePapaClient",
    "TePapaImageProcessor",
    "TePapaProgressTracker",
    "MuseumAPIClient",
    "MuseumImageProcessor",
    "ArtworkMetadata",
    "MuseumInfo",
]
