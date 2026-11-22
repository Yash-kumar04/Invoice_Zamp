"""Configuration — load rules/thresholds and env vars."""

import json
import os
from datetime import date
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.json"


def load_rules() -> dict:
    """Load threshold config from config/rules.json."""
    with open(_RULES_PATH) as f:
        return json.load(f)


def get_as_of_date() -> date:
    """Return AS_OF_DATE from env (or today if unset)."""
    raw = os.environ.get("AS_OF_DATE")
    return date.fromisoformat(raw) if raw else date.today()
