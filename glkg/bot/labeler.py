"""문서 라벨(서지/인터뷰/서평/비평/해석/참고문헌) + 출처 성격(홍보/독립/미상). 규칙 기반이며 AI를 쓰지 않는다.
홍보물(출판사·서점·플랫폼 소개문, 보도자료성 문구)과 독립 비평(학술 논문·독립 매체의 서평·비평)은 origin_class 로 분리한다."""
import re
from typing import Any, Dict, List, Tuple

PROMO_REG_KINDS = {"official_entry", "catalog"}          # 출판사·서점·플랫폼 공식 소개 (nlk 는 서지 DB라 아래에서 따로)
PROMO_WORDS = re.compile(r"보도자료|신간|출간\s*기념|이벤트|할인|추천사|전격|화제의|베스트셀러|예약\s*판매|굿즈|사은품|promo|press release", re.I)
INTERVIEW = re.compile(r"인터뷰|대담|좌담|작가와의\s*만남|Q\s*&\s*A|interview", re.I)
REVIEW = re.compile(r"서평|리뷰|독후감|책\s*읽기|북리뷰|book review|\breview\b", re.I)
CRITIC = re.compile(r"비평|평론|논평|critique|criticism|해체|연구|고찰|분석", re.I)
INTERPRET = re.compile(r"해석|재해석|독법|읽기|의미|상징|interpret", re.I)
INDEP_KEYS = {"openalex_api", "crossref_api", "kci_journal"}
BIB_KEYS = {"wikidata_api", "nlk_catalog", "doi_resolver"}


def label_doc(kind: str, fields: Dict[str, List[str]], registry_key: str, reg_kind: str) -> List[Tuple[str, str, str]]:
    """[(label, origin_class, basis)]"""
    title = (fields.get("title") or [""])[0]
    out: List[Tuple[str, str, str]] = []
    doc_kind = (fields.get("doc_kind") or [""])[0]
    promo_text = bool(PROMO_WORDS.search(title))
    if doc_kind == "book_record" or (fields.get("isbn") and fields.get("publisher") and title):
        if registry_key in BIB_KEYS:
            oc = "independent" if registry_key in ("wikidata_api", "nlk_catalog") else "unknown"
        elif reg_kind in PROMO_REG_KINDS or promo_text:
            oc = "promo"
        else:
            oc = "unknown"
        out.append(("서지", oc, f"doc_kind={doc_kind or 'isbn+publisher'};registry={registry_key}"))
    if doc_kind == "paper" or registry_key in INDEP_KEYS:
        out.append(("비평", "independent", f"학술 메타데이터({registry_key})"))
        if INTERPRET.search(title):
            out.append(("해석", "independent", "제목 키워드(해석/읽기/독법)"))
        if fields.get("cites") or fields.get("cites_doi"):
            out.append(("참고문헌", "independent", "인용 목록 보유"))
        return out
    if INTERVIEW.search(title):
        out.append(("인터뷰", "promo" if (promo_text or reg_kind in PROMO_REG_KINDS) else "unknown", "제목 키워드(인터뷰/대담)"))
    if REVIEW.search(title):
        out.append(("서평", "promo" if (promo_text or reg_kind in PROMO_REG_KINDS) else ("independent" if reg_kind == "article" else "unknown"), "제목 키워드(서평/리뷰)"))
    if CRITIC.search(title) and not any(l[0] in ("서평", "인터뷰") for l in out):
        out.append(("비평", "unknown", "제목 키워드(비평/평론)"))
    if INTERPRET.search(title) and doc_kind != "book_record" and not any(l[0] == "해석" for l in out):
        out.append(("해석", "unknown", "제목 키워드(해석)"))
    if (fields.get("doi") or fields.get("isbn")) and fields.get("page_text_hash") and not out:
        out.append(("참고문헌", "unknown", "PDF/이미지에서 식별자 패턴만 추출"))
    return out
