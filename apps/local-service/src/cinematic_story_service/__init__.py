"""Cinematic Story Studio's private local service."""

from .app import create_app
from .config import ServiceSettings

__all__ = ["ServiceSettings", "create_app"]

__version__ = "0.1.0"
