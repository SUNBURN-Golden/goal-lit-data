"""소스 레지스트리 로더. 소스별 수집 가능 필드·권한·갱신주기·보관조건·미지원 범위를 DB로 올리고 URL을 분류한다."""
import json
import datetime as dt
import os
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .timeutil import now_iso

ROBOTS_SUMMARY = Path(os.environ.get("GLKG_ROBOTS_SUMMARY", "/workspace/lit-hub/research/robots/_summary.json"))
OK_STATUSES = ("ok", "no-robots-rules")


def load_json(path: Optional[Path] = None) -> Dict[str, Any]:
    return json.loads(Path(path or config.REGISTRY_PATH).read_text(encoding="utf-8"))


def _audit(robots_path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(robots_path or ROBOTS_SUMMARY)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _norm_host(h: str) -> str:
    h = (h or "").lower()
    return h[4:] if h.startswith("www.") else h


class Registry:
    def __init__(self, data: Optional[Dict[str, Any]] = None, robots_path: Optional[Path] = None):
        self.data = data or load_json()
        self.sources: List[Dict[str, Any]] = self.data["sources"]
        audit = _audit(robots_path)
        self._host_map: Dict[str, str] = {}
        # 우선순위: 정책 차단·미지원 목록이 먼저, 그 다음 지원 목록 (같은 호스트가 겹치면 보수적으로 막는다)
        order = sorted(self.sources, key=lambda s: 0 if s["support_level"] == "unsupported" else 1)
        for s in order:
            hosts = list(s.get("domains", []))
            sel = s.get("domains_from_audit")
            if sel == "ok":
                hosts += [h for h, v in audit.items() if v.get("status") in OK_STATUSES and not v.get("star_blocks_all")]
            elif sel:
                hosts += [h for h, v in audit.items() if v.get("status") == sel]
            for h in hosts:
                if h == "*":
                    continue
                self._host_map.setdefault(_norm_host(h), s["key"])
        self.by_key = {s["key"]: s for s in self.sources}

    def classify_host(self, host: str) -> str:
        h = _norm_host(host)
        parts = h.split(".")
        for i in range(len(parts) - 1):  # 정확히 일치 → 상위 도메인 순으로 (예: gall.dcinside.com → dcinside.com)
            key = self._host_map.get(".".join(parts[i:]))
            if key:
                return key
        return "unregistered"

    def classify_url(self, url: str) -> str:
        try:
            host = urllib.parse.urlsplit(url).netloc.split(":")[0]
        except Exception:
            host = ""
        return self.classify_host(host)

    def get(self, key: str) -> Dict[str, Any]:
        return self.by_key[key]

    def policy_for_url(self, url: str) -> Dict[str, Any]:
        s = self.get(self.classify_url(url))
        return {
            "registry_key": s["key"],
            "support_level": s["support_level"],
            "usage_basis": s["usage_basis"],
            "retention_days": s.get("retention_days"),
            "refresh_hours": s.get("refresh_hours"),
            "default_access_level": s.get("default_access_level", "press_article"),
            "is_external": 0 if s["key"] == "internal_site_records" else 1,
            "may_collect": s["support_level"] in ("supported", "limited"),
        }

    def retention_until(self, key: str, collected_at: str) -> Optional[str]:
        days = self.get(key).get("retention_days")
        if days is None:
            return None
        return (dt.datetime.fromisoformat(collected_at) + dt.timedelta(days=days)).date().isoformat()

    def unsupported_report(self) -> List[Dict[str, Any]]:
        return [{"key": s["key"], "name": s["name"], "scope": s["unsupported_scope"], "credential_status": s.get("credential_status", "")}
                for s in self.sources if s["unsupported_scope"]]


def sync_to_db(conn, reg: Optional[Registry] = None) -> int:
    reg = reg or Registry()
    now = now_iso()
    for s in reg.sources:
        conn.execute(
            """INSERT INTO source_registry(registry_key,name,kind,support_level,domains_json,fields_json,usage_basis,refresh_hours,retention_days,unsupported_scope,notes,loaded_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(registry_key) DO UPDATE SET name=excluded.name, kind=excluded.kind, support_level=excluded.support_level,
                 domains_json=excluded.domains_json, fields_json=excluded.fields_json, usage_basis=excluded.usage_basis,
                 refresh_hours=excluded.refresh_hours, retention_days=excluded.retention_days, unsupported_scope=excluded.unsupported_scope,
                 notes=excluded.notes, loaded_at=excluded.loaded_at""",
            (s["key"], s["name"], s["kind"], s["support_level"], json.dumps(s.get("domains", []), ensure_ascii=False),
             json.dumps(s.get("collectable_fields", []), ensure_ascii=False), s["usage_basis"], s.get("refresh_hours"),
             s.get("retention_days"), s.get("unsupported_scope", ""), s.get("access_method", ""), now),
        )
    conn.commit()
    return len(reg.sources)
