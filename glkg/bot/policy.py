"""수집 허용 판단: 기본 레지스트리 + 평가기가 등록한 새 출처(candidate_source)."""
import re
from typing import Any, Dict, Optional

from ..registry import Registry
from .http import host_of


def effective_policy(conn, reg: Registry, url: str) -> Dict[str, Any]:
    pol = dict(reg.policy_for_url(url))
    pol["trial_allowed"] = False
    pol["via"] = "registry"
    if pol["registry_key"] != "unregistered":
        return pol
    host = host_of(url)
    row = None
    parts = host.split(".")
    for i in range(len(parts) - 1):
        row = conn.execute("SELECT * FROM candidate_source WHERE domain=?", (".".join(parts[i:]),)).fetchone()
        if row:
            break
    if row is None:
        pol["trial_allowed"] = True   # 처음 보는 도메인: 평가기의 시험 수집(robots 통과 시)만 허용
        pol["via"] = "unknown_domain"
        return pol
    if row["state"] == "registered":
        pol.update(registry_key=f"discovered:{row['domain']}", support_level="limited", usage_basis=row["usage_basis"], may_collect=True, via="evaluator_registered",
                   retention_days=365, refresh_hours=168, is_external=1)
    elif row["state"] in ("new", "trial_done"):
        pol["trial_allowed"] = row["state"] == "new"
        pol["via"] = f"candidate_{row['state']}"
    else:
        pol["via"] = f"candidate_{row['state']}"
    return pol


def storable_body(pol: Dict[str, Any]) -> bool:
    """원문 사본을 비공개 저장소에 둘 수 있는가. link_only/fulltext_forbidden 은 해시와 추출 필드만."""
    return pol.get("usage_basis") in ("metadata_only", "quote_le12", "own_work") and pol.get("registry_key") not in ("unregistered",)
