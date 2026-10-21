"""Headless-browser capture with render stabilisation."""
from .browser import CaptureError, CaptureResult, CaptureSession
from .stability import discover_volatile_regions
from .stabilize import CaptureSpec, Stabilize

__all__ = ["CaptureError", "CaptureResult", "CaptureSession", "CaptureSpec", "Stabilize", "discover_volatile_regions"]
