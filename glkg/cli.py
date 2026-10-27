"""명령줄: python -m glkg <명령>
  run <단계|all>          단계 1회 실행 (all = 9단계 순서대로)
  daemon <단계> [--interval 초 --offset 초]   루프 실행(pm2용). 락·상한·재시도는 run_stage 가 처리
  status                  단계별 체크포인트·최근 실행·실패 큐·일일 사용량
  migrate-legacy          수집함 → 지식그래프 이관(추출→식별→검증→이력→재계산→점검 1회)
  ack-change <event_id>   출처 변경을 편집자가 확인했음을 기록(이후 재확정 가능)
  pause/resume <단계>     단계 일시 정지
"""
import argparse
import json
import signal
import sys
import time

from . import config, db
from .pipeline import run_stage
from .registry import sync_to_db
from .stages import ORDER, STAGES

_STOP = False


def _sig(*_):
    global _STOP
    _STOP = True


def make_stage(name, **kw):
    return STAGES[name](**kw)


def open_conn():
    conn = db.open_kg()
    sync_to_db(conn)
    return conn


def cmd_run(args):
    conn = open_conn()
    names = ORDER if args.stage == "all" else [args.stage]
    out = [run_stage(make_stage(n), conn) for n in names]
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if all(o["status"] in ("ok", "partial", "skipped_locked", "skipped_budget", "skipped_paused") for o in out) else 1


def cmd_daemon(args):
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    for _ in range(args.offset):  # 단계별 시작 시차(동시 기동 분산)
        if _STOP:
            return 0
        time.sleep(1)
    while not _STOP:
        conn = open_conn()
        try:
            res = run_stage(make_stage(args.stage), conn)
            print(f"[{args.stage}] {res['status']} processed={res.get('processed')} failed={res.get('failed')}", flush=True)
        finally:
            conn.close()
        for _ in range(args.interval):
            if _STOP:
                break
            time.sleep(1)
    return 0


def cmd_status(args):
    conn = open_conn()
    rows = []
    for n in ORDER:
        st = conn.execute("SELECT * FROM stage_state WHERE stage=?", (n,)).fetchone()
        fq = conn.execute("SELECT COUNT(*) FROM failed_queue WHERE stage=? AND state='pending'", (n,)).fetchone()[0]
        dead = conn.execute("SELECT COUNT(*) FROM failed_queue WHERE stage=? AND state='dead'", (n,)).fetchone()[0]
        use = {r["metric"]: f"{r['used']}/{r['cap']}" for r in conn.execute("SELECT * FROM daily_usage WHERE stage=? AND day=?", (n, db_today()))}
        rows.append({"stage": n, "last_status": st["last_status"] if st else "never_run", "cursor": (st["cursor"] if st else ""),
                     "last_finished_at": st["last_finished_at"] if st else None, "failures_in_row": st["consecutive_failures"] if st else 0,
                     "retry_pending": fq, "dead": dead, "today_usage": use, "paused": bool(st and st["paused"])})
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


def db_today():
    from .timeutil import today
    return today()


def cmd_migrate(args):
    conn = open_conn()
    out = []
    for n in ("extract", "resolve", "verify", "history", "recompute", "quality"):
        out.append(run_stage(make_stage(n), conn))
    # 상태 반영이 한 번 더 필요한 경우(충돌 등록 → 재검증 → 강등)를 위해 검증·이력을 한 번 더
    for n in ("verify", "history", "recompute"):
        out.append(run_stage(make_stage(n), conn))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_ack(args):
    conn = open_conn()
    conn.execute("UPDATE change_event SET state='handled' WHERE event_id=?", (args.event_id,))
    conn.commit()
    return 0


def cmd_pause(args, v):
    conn = open_conn()
    conn.execute("INSERT OR IGNORE INTO stage_state(stage) VALUES(?)", (args.stage,))
    conn.execute("UPDATE stage_state SET paused=? WHERE stage=?", (v, args.stage))
    conn.commit()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="glkg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("stage", choices=ORDER + ["all"])
    p = sub.add_parser("daemon"); p.add_argument("stage", choices=ORDER); p.add_argument("--interval", type=int, default=600); p.add_argument("--offset", type=int, default=0)
    sub.add_parser("status")
    sub.add_parser("migrate-legacy")
    p = sub.add_parser("ack-change"); p.add_argument("event_id", type=int)
    for c in ("pause", "resume"):
        p = sub.add_parser(c); p.add_argument("stage", choices=ORDER)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "daemon": cmd_daemon, "status": cmd_status, "migrate-legacy": cmd_migrate, "ack-change": cmd_ack,
            "pause": lambda x: cmd_pause(x, 1), "resume": lambda x: cmd_pause(x, 0)}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
