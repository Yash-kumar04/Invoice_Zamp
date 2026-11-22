"""Shared test fixtures."""

import os

# Freeze AS_OF_DATE for deterministic tests
os.environ["AS_OF_DATE"] = "2026-11-22"

import pytest  # noqa: E402

from app.db import get_connection, create_tables  # noqa: E402
from app.seed import seed_all  # noqa: E402


@pytest.fixture
def seeded_db():
    """Fresh in-memory DB with all master data and history seeded."""
    conn = get_connection(":memory:")
    create_tables(conn)
    seed_all(conn)
    yield conn
    conn.close()
