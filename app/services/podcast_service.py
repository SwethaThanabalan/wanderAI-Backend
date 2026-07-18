"""Podcast service — script generation is handled by app.agents.podcast_editor.

This module is kept as a thin re-export for backward compatibility.
"""

from app.agents.podcast_editor import run_podcast_editor

__all__ = ["run_podcast_editor"]
