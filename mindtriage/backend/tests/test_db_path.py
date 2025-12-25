import os
from pathlib import Path

from mindtriage.backend.app import main


def test_resolve_db_path_stable_across_cwd():
    original = os.getcwd()
    expected = Path(main.__file__).resolve().parents[3] / "mindtriage.db"
    env_backup = os.environ.pop("MINDTRIAGE_DB_PATH", None)
    env_backup_alt = os.environ.pop("DB_PATH", None)
    try:
        os.chdir(Path(main.__file__).resolve().parents[2])
        resolved = Path(main.resolve_db_path())
        assert resolved == expected
    finally:
        os.chdir(original)
        if env_backup is not None:
            os.environ["MINDTRIAGE_DB_PATH"] = env_backup
        if env_backup_alt is not None:
            os.environ["DB_PATH"] = env_backup_alt
