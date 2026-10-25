from datetime import date
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def omie_file():
    """Return the text of a real OMIE file saved in tests/fixtures."""

    def load(d: date) -> str:
        return (FIXTURES / f"marginalpdbcpt_{d:%Y%m%d}.1").read_bytes().decode("latin-1")

    return load
