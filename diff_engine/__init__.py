"""Tolerant pixel-diff engine for visual regression testing."""
from .config import ConfigError, DiffConfig
from .engine import DiffResult, compare_images, load_image
from .report import write_html_report, write_page_artifacts

__all__ = [
    "ConfigError",
    "DiffConfig",
    "DiffResult",
    "compare_images",
    "load_image",
    "write_html_report",
    "write_page_artifacts",
]
