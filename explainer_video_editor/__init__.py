"""Public manifest-driven explainer video editor runtime."""

from .builder import build
from .manifest import load_project, validate_project
from .pronunciation import rewrite_narration, rewrite_text
from .verify import verify_project

__all__ = [
    "build",
    "load_project",
    "rewrite_narration",
    "rewrite_text",
    "validate_project",
    "verify_project",
]
