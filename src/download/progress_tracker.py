from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import Dict, Set, Union, List, Any, Optional
import logging
from dataclasses import dataclass, field
from enum import Enum
from collections import OrderedDict

from ..config import settings
from ..log_level import LogLevel
from ..utils import setup_logging


@dataclass
class ProgressState:
    """Base class for tracking museum download progress"""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)


class LRUCache:
    """Simple LRU cache for processed IDs to prevent memory leaks."""

    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self.cache: OrderedDict[str, bool] = OrderedDict()

    def add(self, key: str) -> None:
        """Add key to cache, evict oldest if at capacity."""
        if key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(key)
        else:
            self.cache[key] = True
            if len(self.cache) > self.max_size:
                # Remove oldest (first item)
                self.cache.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        """Check if key exists in cache."""
        return key in self.cache

    def __len__(self) -> int:
        return len(self.cache)

    def to_list(self) -> List[str]:
        """Convert cache to list for serialization."""
        return list(self.cache.keys())


class BaseProgressTracker(ABC):
    """Manages download progress tracking and reporting with memory-efficient caching."""

    def __init__(
        self,
        progress_file: Path,
        max_cache_size: int = 10000,
        save_batch_size: int = 100
    ):
        """
        Initialize progress tracker with LRU cache for memory efficiency.

        Args:
            progress_file: Path to the progress tracking file
            max_cache_size: Maximum number of IDs to keep in memory (default: 10000)
            save_batch_size: Number of items to process before saving (default: 100)
        """
        # Ensure we have a Path object
        self.progress_file = (
            Path(progress_file)
            if not isinstance(progress_file, Path)
            else progress_file
        )
        self.state = ProgressState()
        self.logger = setup_logging(
            self.progress_file.parent, settings.log_level, "progress"
        )

        # Memory-efficient caching
        self.processed_cache = LRUCache(max_size=max_cache_size)
        self.save_batch_size = save_batch_size
        self._pending_saves = 0

        self._load_progress()

    @abstractmethod
    def get_state_dict(self) -> Dict[str, Any]:
        """Convert current state to serializable dict"""
        pass

    @abstractmethod
    def restore_state(self, data: Dict[str, Any]) -> None:
        """Restore state from dict"""
        pass

    def _load_progress(self) -> None:
        """Load progress file with error handling and populate cache."""
        try:
            if self.progress_file.exists():
                with self.progress_file.open("r") as f:
                    data = json.load(f)
                self.restore_state(data)

                # Populate cache with most recent processed IDs
                # (up to max_cache_size, prioritizing more recent)
                for artwork_id in list(self.state.processed_ids)[-self.processed_cache.max_size:]:
                    self.processed_cache.add(artwork_id)

                self.logger.progress(
                    f"Loaded progress file. {len(self.state.processed_ids)} items processed, "
                    f"{len(self.processed_cache)} in cache."
                )
            else:
                self.logger.progress(f"No progress file found. Starting fresh")
                self._save_progress()
        except Exception as e:
            self.logger.error(f"Error loading progress file: {str(e)}. Starting fresh")
            self._save_progress()

    def _save_progress(self) -> None:
        """Save progress to file"""
        try:
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)

            temp_file = self.progress_file.with_suffix(".tmp")
            with temp_file.open("w") as f:
                json.dump(self.get_state_dict(), f, indent=4)

            temp_file.replace(self.progress_file)
        except Exception as e:
            self.logger.error(f"Error saving progress: {str(e)}")

    def log_status(
        self, artwork_id: str, status: str, error_message: str = None
    ) -> None:
        """
        Log artwork processing status with batched writes.

        Progress is saved every save_batch_size items instead of every item
        to reduce disk I/O.
        """
        artwork_id = str(artwork_id)

        if status == "success":
            self.state.success_ids.add(artwork_id)
        else:
            self.state.failed_ids.add(artwork_id)
            if error_message:
                if status not in self.state.error_log:
                    self.state.error_log[status] = {}
                self.state.error_log[status][artwork_id] = error_message

        # Use LRU cache for processed IDs to prevent unbounded growth
        self.processed_cache.add(artwork_id)
        self.state.processed_ids.add(artwork_id)

        # Batch writes to reduce disk I/O
        self._pending_saves += 1
        if self._pending_saves >= self.save_batch_size:
            self._save_progress()
            self._pending_saves = 0

    def force_save(self) -> None:
        """Force save progress immediately (e.g., at end of download)."""
        if self._pending_saves > 0:
            self._save_progress()
            self._pending_saves = 0

    def is_processed(self, artwork_id: str) -> bool:
        """
        Check if artwork has been processed.

        First checks the LRU cache (fast), then falls back to the full set.
        """
        artwork_id = str(artwork_id)
        # Check cache first (O(1) for recent items)
        if artwork_id in self.processed_cache:
            return True
        # Fall back to full set (for items evicted from cache)
        return artwork_id in self.state.processed_ids

    def get_statistics(self) -> Dict[str, int]:
        """Get processing stats"""
        return {
            "total_processed": len(self.state.processed_ids),
            "successful": len(self.state.success_ids),
            "failed": len(self.state.failed_ids),
            "error_count": sum(len(errors) for errors in self.state.error_log.values()),
        }
