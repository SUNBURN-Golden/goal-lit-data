import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def _isolate_inbox(tmp_path, monkeypatch):
    """테스트가 실제 수집함 DB(일일 카운터)를 읽지 않게 한다."""
    from glkg import config
    monkeypatch.setattr(config, "INBOX_DB_PATH", tmp_path / "no_such_inbox.db", raising=False)
