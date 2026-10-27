"""설정. 경로는 환경변수로 덮어쓸 수 있다(테스트·운영 분리). DB·원문은 리포 밖에 둔다."""
import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GLKG_DATA_DIR", "/workspace/lit-hub/collector/data"))
KG_DB_PATH = Path(os.environ.get("GLKG_KG_DB", str(DATA_DIR / "goallit_kg.db")))
INBOX_DB_PATH = Path(os.environ.get("GLKG_INBOX_DB", str(DATA_DIR / "goallit_inbox.db")))
LOCK_DIR = Path(os.environ.get("GLKG_LOCK_DIR", str(DATA_DIR / "kg_locks")))
EXPORT_DIR = Path(os.environ.get("GLKG_EXPORT_DIR", str(DATA_DIR / "kg_export")))
WORKS_JSON = Path(os.environ.get("GLKG_WORKS_JSON", "/workspace/lit-hub/site/content/works.json"))
LEGACY_COLLECTOR_DIR = Path(os.environ.get("GLKG_LEGACY_DIR", "/workspace/lit-hub/collector"))
MIGRATIONS_DIR = REPO_DIR / "schema" / "migrations"
REGISTRY_PATH = REPO_DIR / "sources" / "registry.json"

# 기존 규칙 유지 (준태님 확정 10/23): 도메인 동시 1 · 5초 · 일 500/200 · 링크 깊이 2 · 자동게시 off
MAX_DAILY_DISCOVERED = 500
MAX_DAILY_FETCHED = 200
MAX_CONCURRENT_PER_DOMAIN = 1
MIN_DOMAIN_INTERVAL_SEC = 5.0
MAX_LINK_DEPTH = 2
AUTO_PUBLISH_TO_SITE = False

# 파이프라인 단계별 일일 상한(요청·비용). AI(LLM) 호출은 기본 0 — 규칙 기반 추출만 쓴다.
STAGE_DAILY_CAPS = {
    "discover": {"requests": 100},
    "fetch": {"requests": MAX_DAILY_FETCHED, "discovered": MAX_DAILY_DISCOVERED},
    "detect": {"requests": 200},
    "extract": {"rows": 5000},
    "resolve": {"rows": 5000},
    "verify": {"rows": 50000},
    "history": {"rows": 5000},
    "recompute": {"rows": 50000},
    "quality": {"rows": 50000},
    "parse": {"rows": 50000},
    "seek": {"rows": 100000},     # 질의 생성은 DB 행만 추가(네트워크 없음). 후보 상한은 Budget.discovered 가 센다
    "assess": {"rows": 100000},   # 요청은 Budget(bot:discovery) 쪽에서 센다
    "harvest": {"rows": 100000},  # 요청은 Budget(bot:*) 쪽에서 센다
}
AI_CALLS_DAILY_CAP = 0  # 비용 상한: 현재 0 (AI 추론 자동 생성 안 함)

MAX_QUOTE_CHARS = 12
MAX_OVERLAP_CHARS = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SEC = (60, 300, 1800)
LEASE_SEC = 900
