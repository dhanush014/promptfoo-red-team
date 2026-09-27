import pytest

import db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own freshly seeded SQLite file."""
    monkeypatch.setenv("WARDEN_DB", str(tmp_path / "test.db"))
    db.init_db()
