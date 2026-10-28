#!/usr/bin/env python3
"""증명 실행 지표(읽기 전용). 사용: python3 ops/proof_metrics.py [--save 파일] [--diff 파일]  — 원문·비밀값은 출력하지 않는다."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from glkg import config  # noqa: E402
from glkg.bot.gaps import coverage_summary  # noqa: E402


def q(c, sql, *a):
    return [dict(r) for r in c.execute(sql, a)]


def collect_metrics():
    c = sqlite3.connect(f"file:{config.KG_DB_PATH}?mode=ro", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    m = {}
    m["works_total"] = c.execute("SELECT count(*) FROM work").fetchone()[0]
    m["works_bot_found"] = c.execute("SELECT count(DISTINCT known_work_id) FROM work_candidate WHERE state='promoted' AND known_work_id IS NOT NULL").fetchone()[0]
    m["works_legacy"] = m["works_total"] - m["works_bot_found"]
    m["bot_found_titles"] = [r[0] for r in c.execute("SELECT w.canonical_title FROM work w JOIN work_candidate k ON k.known_work_id=w.work_id AND k.state='promoted' GROUP BY w.work_id LIMIT 30")]
    m["authors"] = c.execute("SELECT count(*) FROM author").fetchone()[0]
    m["editions"] = c.execute("SELECT count(*) FROM edition").fetchone()[0]
    m["work_candidates"] = {r["state"]: r["n"] for r in q(c, "SELECT state, count(*) n FROM work_candidate GROUP BY state")}
    m["review_title_match"] = c.execute("SELECT count(*) FROM review_queue WHERE kind='title_match'").fetchone()[0]
    m["candidate_sources"] = {f'{r["state"]}{"(seed)" if r["in_seed"] else ""}': r["n"] for r in q(c, "SELECT state,in_seed,count(*) n FROM candidate_source GROUP BY state,in_seed")}
    m["new_registered_sources"] = q(c, "SELECT domain,usage_basis,round(score_total,2) score,decision_reason,first_seen_via FROM candidate_source WHERE in_seed=0 AND state='registered'")
    m["documents"] = c.execute("SELECT count(*) FROM raw_document").fetchone()[0]
    m["documents_by_domain"] = {r["domain"]: r["n"] for r in q(c, "SELECT domain,count(*) n FROM raw_document GROUP BY domain ORDER BY n DESC LIMIT 12")}
    m["labels"] = {f'{r["label"]}/{r["origin_class"]}': r["n"] for r in q(c, "SELECT label,origin_class,count(*) n FROM doc_label GROUP BY 1,2")}
    m["queries"] = {r["state"]: r["n"] for r in q(c, "SELECT state,count(*) n FROM discovery_query GROUP BY state")}
    m["frontier"] = {r["state"]: r["n"] for r in q(c, "SELECT state,count(*) n FROM crawl_frontier GROUP BY state")}
    m["fetch_outcomes"] = {r["outcome"]: r["n"] for r in q(c, "SELECT outcome,count(*) n FROM fetch_log GROUP BY outcome")}
    m["coverage"] = coverage_summary(c)
    m["dups"] = {
        "raw_document(canonical_url,content_hash)": c.execute("SELECT count(*) FROM (SELECT 1 FROM raw_document GROUP BY canonical_url,content_hash HAVING count(*)>1)").fetchone()[0],
        "work(canonical_title) 중복": c.execute("SELECT count(*) FROM (SELECT 1 FROM work GROUP BY canonical_title HAVING count(*)>1)").fetchone()[0],
        "edition(isbn) 중복": c.execute("SELECT count(*) FROM (SELECT 1 FROM edition WHERE isbn IS NOT NULL AND isbn<>'' GROUP BY isbn HAVING count(*)>1)").fetchone()[0],
        "bib_fact 동일(subject,predicate,value,source_url) 중복": c.execute("SELECT count(*) FROM (SELECT 1 FROM bib_fact WHERE recorded_to IS NULL GROUP BY subject_type,subject_id,predicate,value_text,source_url HAVING count(*)>1)").fetchone()[0],
    }
    m["row_counts"] = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("work", "author", "edition", "bib_fact", "raw_document", "extraction", "doc_label", "conflict", "gap", "work_candidate", "review_queue", "discovery_query", "crawl_frontier", "candidate_source", "evidence_passage")}
    m["budget_today"] = q(c, "SELECT stage,counter,used,cap FROM daily_usage WHERE day=date('now','localtime')") if 0 else {}
    return m


if __name__ == "__main__":
    a = sys.argv[1:]
    m = collect_metrics()
    if a[:1] == ["--save"]:
        Path(a[1]).write_text(json.dumps(m, ensure_ascii=False, indent=1))
    if a[:1] == ["--diff"]:
        old = json.loads(Path(a[1]).read_text())
        m = {"before": {k: old[k] for k in ("works_total", "works_bot_found", "documents", "row_counts", "coverage")}, "after": m}
    print(json.dumps(m, ensure_ascii=False, indent=1, default=str))
