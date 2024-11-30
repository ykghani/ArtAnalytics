from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import Dict, Set, Union, List, Any, Optional
import logging
from dataclasses import dataclass, field
from enum import Enum

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
                logging.info(f"Loaded progress file. {len(self.state.processed_ids)} items processed.")
            else:
                logging.info(f"No progress file found. Starting fresh")
                self._save_progress()
        except Exception as e:
            logging.error(f"Error loading progress file: {str(e)}. Starting fresh")
            self._save_progress()
    
    def _save_progress(self) -> None:
        '''Save progress to file'''
        try:
            self.progress_file.parent.mkdir(parents= True, exist_ok= True)
            
            temp_file = self.progress_file.with_suffix('.tmp')
            with temp_file.open('w') as f:
                json.dump(self.get_state_dict(), f, indent= 4)
            
            temp_file.replace(self.progress_file)
        
        except Exception as e: 
            logging.error(f"Error saving progress: {str(e)}")
    
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

@dataclass
class AICProgressState(ProgressState):
    last_page: int = 0
    total_pages: int = 0

class AICProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.state = AICProgressState()
        super().__init__(progress_file)
    
    def get_state_dict(self) -> Dict[str, Any]:
        return {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'last_page': self.state.last_page,
            'total_pages': self.state.total_pages
        }
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.last_page = data.get('last_page', 0)
        self.state.total_pages = data.get('total_pages', 0)
    
    def update_page(self, page: int) -> None: 
        '''Update last processed page numebr'''
        self.state.last_page = page
        self._save_progress()
    
    def get_last_page(self) -> int: 
        '''Get last processed page number'''
        return self.state.last_page

@dataclass
class CMAProgressState:
    """Separate state class for CMA progress tracking"""
    def __init__(self):
        self.processed_ids: Set[str] = set()
        self.success_ids: Set[str] = set()
        self.failed_ids: Set[str] = set()
        self.error_log: Dict[str, Dict[str, str]] = {}
        self.total_objects: int = 0

class CMAProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.progress_file = progress_file
        self.state = CMAProgressState()
        self._load_progress()
    
    def get_state_dict(self) -> Dict[str, Any]:
        return {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'total_objects': self.state.total_objects
        }
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.total_objects = data.get('total_objects', 0)

@dataclass 
class MetProgressState(ProgressState):
    total_objects: int = 0
    last_object_id: Optional[str] = None
    
    processed_ids: Set[str] = field(default_factory= set)
    success_ids: Set[str] = field(default_factory= set)
    failed_ids: Set[str] = field(default_factory= set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory= dict)

class MetProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.state = MetProgressState()
        super().__init__(progress_file)
    
    def get_state_dict(self) -> Dict[str, Any]:
        return {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'total_objects': self.state.total_objects,
            'last_object_id': self.state.last_object_id
        }
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.total_objects = data.get('total_objects', 0)
        self.state.last_object_id = data.get('last_object_id')