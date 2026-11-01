"""SmartAlbum - selection-engine package (F03/F05/F06).

חושף את ה-API הציבורי מ-`selection_engine.api` - ראה שם ו-INTERFACES.md
סעיף 4.2 לחוזה המחייב.
"""

from __future__ import annotations

from selection_engine.api import (
    MIN_VALID_PHOTOS,
    MUST_LIMIT,
    VALID_FLAGS,
    InvalidFlagError,
    MustCountCheck,
    SelectionResult,
    check_must_limit,
    run_selection,
    set_flag,
    tag_event_stages,
)

__all__ = [
    "MIN_VALID_PHOTOS",
    "MUST_LIMIT",
    "VALID_FLAGS",
    "InvalidFlagError",
    "MustCountCheck",
    "SelectionResult",
    "check_must_limit",
    "run_selection",
    "set_flag",
    "tag_event_stages",
]
