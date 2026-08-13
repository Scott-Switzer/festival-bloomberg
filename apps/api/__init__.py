"""
Festival Bloomberg API application.

This is the consolidated FastAPI application that provides REST endpoints for:
- Artist search and retrieval
- Festival information and comparison
- Artist factors and analytics
- Expected billing predictions
- Relative value analysis
- Portfolio analytics
- Point-in-time data queries
"""

from .main import app, get_app

__all__ = ["app", "get_app"]