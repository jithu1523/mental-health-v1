import sqlite3
from pathlib import Path

from mindtriage.backend.app import main


def test_migrate_legacy_db_when_canonical_empty(tmp_path):
    canonical = tmp_path / "mindtriage.db"
    legacy = tmp_path / "legacy.db"

    canonical_conn = sqlite3.connect(canonical)
    canonical_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    canonical_conn.commit()
    canonical_conn.close()

    legacy_conn = sqlite3.connect(legacy)
    legacy_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    legacy_conn.execute("INSERT INTO users (id, email) VALUES (1, 'test@example.com')")
    legacy_conn.commit()
    legacy_conn.close()

    status = main.migrate_legacy_db(str(canonical), str(legacy))
    assert status["status"] == "migrated"
    assert status["migrated_rows"].get("users") == 1

    check_conn = sqlite3.connect(canonical)
    count = check_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    check_conn.close()
    assert count == 1


def test_migrate_when_legacy_has_more_rows(tmp_path):
    canonical = tmp_path / "mindtriage.db"
    legacy = tmp_path / "legacy.db"

    canonical_conn = sqlite3.connect(canonical)
    canonical_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    canonical_conn.execute("CREATE TABLE journal_entries (id INTEGER PRIMARY KEY, content TEXT)")
    canonical_conn.execute("INSERT INTO users (id, email) VALUES (1, 'test@example.com')")
    canonical_conn.commit()
    canonical_conn.close()

    legacy_conn = sqlite3.connect(legacy)
    legacy_conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    legacy_conn.execute("CREATE TABLE journal_entries (id INTEGER PRIMARY KEY, content TEXT)")
    legacy_conn.execute("INSERT INTO users (id, email) VALUES (1, 'test@example.com')")
    legacy_conn.execute("INSERT INTO journal_entries (id, content) VALUES (1, 'entry')")
    legacy_conn.commit()
    legacy_conn.close()

    status = main.migrate_legacy_db(str(canonical), str(legacy))
    assert status["status"] == "migrated"
    assert status["migrated_rows"].get("journal_entries") == 1
