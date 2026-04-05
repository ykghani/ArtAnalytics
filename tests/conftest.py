"""Test configuration: initialize settings paths before any test runs."""
from pathlib import Path
import pytest
from src.config import settings


def pytest_configure(config):
    """Initialize settings paths using the worktree root as project root."""
    project_root = Path(__file__).parent.parent
    settings.initialize_paths(project_root)
