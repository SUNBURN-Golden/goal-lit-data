"""검증 규칙 (순수 함수). 근거 검증 단계가 사용하고, 단위 테스트로 고정한다.

원칙
- 확정(confirmed_fact)은 '외부 출처 + 근거 위치 + 수집 시각 + 열린 충돌 없음 + 편집 검증 완료' 모두 필요.
- 해석·주제·반대 관계는 확정 불가(최대 source_claim).
- 12자 초과 인용 금지, 출처와 15자 이상 겹침 금지.
- 판정(verdict)과 상태 변경은 분리한다: 여기선 판정만 돌려준다.
"""
from typing import Any, Dict, List, Optional, Tuple

from . import config

CONFIRMABLE_TABLES = ("bib_fact", "author_statement", "work_author", "edition")
NEVER_CONFIRMED_TABLES = ("interpretation", "work_theme", "interpretation_relation")
EDITORIAL_OK = ("verified_ready", "publish_pending")


def longest_common_substring_len(a: str, b: str, cap: int = 64) -> int:
    """a, b의 가장 긴 공통 부분문자열 길이(공백 무시). cap에 닿으면 조기 종료."""
    a = "".join((a or "").split())
    b = "".join((b or "").split())
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    best = 0
    # 길이 L의 공통 조각 존재 여부를 이분 탐색하지 않고, 작은 코퍼스용 해시셋 방식으로 증가 탐색
    L = 1
    while L <= min(len(a), cap):
        grams = {a[i:i + L] for i in range(len(a) - L + 1)}
        if any(b[i:i + L] in grams for i in range(len(b) - L + 1)):
            best = L
            L += 1
        else:
            break
    return best


def check_quote_len(snippet: str) -> bool:
    return len(snippet or "") <= config.MAX_QUOTE_CHARS


def check_overlap(summary: str, source_texts: List[str]) -> int:
    """우리 문장과 출처 원문 사이 최대 겹침 길이."""
    return max([longest_common_substring_len(summary, t) for t in source_texts] or [0])


def evaluate(
    claim_table: str,
    claim: Dict[str, Any],
    source: Optional[Dict[str, Any]],
    evidence: Optional[Dict[str, Any]],
    inbox_status: Optional[str],
    has_open_conflict: bool,
    overlap_len: int = 0,
    source_changed: bool = False,
) -> Tuple[str, List[str]]:
    """(verdict, reasons). verdict ∈ confirm / keep_claim / keep_unverified / violation"""
    reasons: List[str] = []
    if overlap_len >= config.MAX_OVERLAP_CHARS:
        return "violation", [f"overlap_{overlap_len}_chars_with_source"]
    if evidence and not check_quote_len(evidence.get("quote_snippet", "")):
        return "violation", ["quote_over_12_chars"]
    if claim.get("epistemic_status") == "ai_inference":
        return "keep_claim", ["ai_inference_never_confirmed"]
    if claim_table in NEVER_CONFIRMED_TABLES:
        return ("keep_unverified" if claim.get("epistemic_status") == "unverified" else "keep_claim"), ["interpretation_never_confirmed"]
    if claim.get("epistemic_status") == "unverified" and (not source or not source.get("is_external")):
        return "keep_unverified", ["no_external_source_states_this"]
    if claim_table not in CONFIRMABLE_TABLES:
        return "keep_claim", ["table_not_confirmable"]
    if not source:
        reasons.append("no_source")
    elif not source.get("is_external"):
        reasons.append("internal_source_only")
    if not evidence:
        reasons.append("no_evidence_locator")
    if not claim.get("collected_at"):
        reasons.append("no_collected_at")
    if has_open_conflict:
        reasons.append("open_conflict")
    if source_changed:
        reasons.append("source_changed_unreviewed")
    if inbox_status not in EDITORIAL_OK:
        reasons.append("no_editorial_verification")
    if reasons:
        return "keep_claim", reasons
    if evidence and evidence.get("locator_quality") == "legacy_pointer":
        return "confirm", ["legacy_pointer_locator"]
    return "confirm", []
