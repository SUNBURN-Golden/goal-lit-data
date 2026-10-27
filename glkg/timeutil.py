import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))


def now_iso() -> str:
    return dt.datetime.now(KST).isoformat(timespec="seconds")


def today() -> str:
    return dt.datetime.now(KST).strftime("%Y-%m-%d")


def add_seconds(iso: str, sec: int) -> str:
    return (dt.datetime.fromisoformat(iso) + dt.timedelta(seconds=sec)).isoformat(timespec="seconds")
