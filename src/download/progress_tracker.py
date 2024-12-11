from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import Dict, Set, Union, List, Any, Optional
import logging
from dataclasses import dataclass, field
from enum import Enum

from ..config import settings 
from ..log_level import LogLevel
from ..utils import setup_logging

@dataclass
class ProgressState:
    '''Base class for tracking museum download progress'''
    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)

class BaseProgressTracker(ABC):
    """Manages download progress tracking and reporting."""
    
    def __init__(self, progress_file: Path):
        """
        Initialize progress tracker.
        Args: progress_file (Path): Path to the progress tracking file
        """
        # Ensure we have a Path object
        self.progress_file = Path(progress_file) if not isinstance(progress_file, Path) else progress_file
        self.state = ProgressState()
        self.logger = setup_logging(self.progress_file.parent, settings.log_level, 'progress')
        self._load_progress()

    
    @abstractmethod
    def get_state_dict(self) -> Dict[str, Any]:
        '''Convert current state to serializable dict'''
        pass
    
    @abstractmethod
    def restore_state(self, data: Dict[str, Any]) -> None:
        '''Restore state from dict'''
        pass
    
    def _load_progress(self) -> None:
        '''Load progress file with error handling'''
        try:
            if self.progress_file.exists():
                with self.progress_file.open('r') as f:
                    data = json.load(f)
                self.restore_state(data)
                self.logger.progress(f"Loaded progress file. {len(self.state.processed_ids)} items processed.")
            else:
                self.logger.progress(f"No progress file found. Starting fresh")
                self._save_progress()
        except Exception as e:
            self.logger.error(f"Error loading progress file: {str(e)}. Starting fresh")
            self._save_progress()
    
    def _save_progress(self) -> None:
        '''Save progress to file'''
        try:
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            
            temp_file = self.progress_file.with_suffix('.tmp')
            with temp_file.open('w') as f:
                json.dump(self.get_state_dict(), f, indent=4)
            
            temp_file.replace(self.progress_file)
        except Exception as e:
            self.logger.error(f"Error saving progress: {str(e)}")
    
    def log_status(self, artwork_id: str, status: str, error_message: str = None) -> None:
        '''Log artwork processing status'''
        artwork_id = str(artwork_id)
        
        if status == 'success':
            self.state.success_ids.add(artwork_id)
        else:
            self.state.failed_ids.add(artwork_id)
            if error_message:
                if status not in self.state.error_log:
                    self.state.error_log[status] = {}
                self.state.error_log[status][artwork_id] = error_message
        
        self.state.processed_ids.add(artwork_id)
        self._save_progress()
        
    def is_processed(self, artwork_id: str) -> bool: 
        '''Check if artwork has been processed'''
        return str(artwork_id) in self.state.processed_ids
    
    def get_statistics(self) -> Dict[str, int]:
        '''Get processing stats'''
        return {
            'total_processed': len(self.state.processed_ids),
            'successful': len(self.state.success_ids),
            'failed': len(self.state.failed_ids),
            'error_count': sum(len(errors) for errors in self.state.error_log.values())
        }