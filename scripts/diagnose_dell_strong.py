"""Diagnose DELL Strong Monitor status."""
from db import get_conn, init_db
from strong_stocks import (
    STRONG_63D_POSITION_THRESHOLD,
    STRONG_COUNT_THRESHOLD,
    STRONG_COUNT_WINDOW,
    STRONG_RETENTION_DAYS,
    _latest_trading_dates,
    build_daily_strong_stocks,
    list_active_strong_watchlist,
    list_count20_ranking,
)

init_db()
print(
    "rules",
    STRONG_63D_POSITION_THRESHOLD,
    STRONG_COUNT_THRESHOLD,
    STRONG_RETENTION_DAYS,
)
dates = _latest_trading_dates(STRONG_COUNT_WINDOW)
print(
    "window",
    dates[-1] if dates else None,
    "->",
    dates[0] if dates else None,
    "n",
    len(dates),
)

with get_conn() as conn:
    mem = conn.execute(
        "SELECT * FROM strong_membership WHERE symbol=?", ("DELL",)
    ).fetchone()
    print("membership", dict(mem) if mem else None)
    rows = conn.execute(
        """
        SELECT as_of_date, range_63d_pos, is_strong, count20
        FROM strong_daily WHERE symbol=?
        ORDER BY as_of_date DESC LIMIT 30
        """,
        ("DELL",),
    ).fetchall()
    print("recent strong_daily", len(rows))
    for r in rows:
        d = dict(r)
        mark = "*" if d["as_of_date"] in dates else " "
        print(
            f"{mark} {d['as_of_date']} pos={d['range_63d_pos']} "
            f"strong={d['is_strong']} c20={d['count20']}"
        )

    if dates:
        ph = ",".join("?" * len(dates))
        n = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM strong_daily
            WHERE symbol=? AND as_of_date IN ({ph}) AND is_strong=1
            """,
            ["DELL", *dates],
        ).fetchone()["n"]
        print("manual COUNT20 in window", n)
        print("window detail (oldest->newest):")
        wrows = conn.execute(
            f"""
            SELECT as_of_date, range_63d_pos, is_strong, count20
            FROM strong_daily
            WHERE symbol=? AND as_of_date IN ({ph})
            ORDER BY as_of_date
            """,
            ["DELL", *dates],
        ).fetchall()
        for r in wrows:
            print(" ", dict(r))

        # How many days pos>=80 / >=85 / >=90 in window
        for thr in (80, 85, 90):
            c = sum(
                1
                for r in wrows
                if r["range_63d_pos"] is not None
                and float(r["range_63d_pos"]) >= thr
            )
            print(f"days pos>={thr} in window", c)

rk = list_count20_ranking()
hit = next((r for r in rk["rows"] if r["symbol"] == "DELL"), None)
print("in ranking", hit)
wl = list_active_strong_watchlist()
whit = next((r for r in wl["rows"] if r["symbol"] == "DELL"), None)
print("in watchlist", whit)
daily = build_daily_strong_stocks()
if daily["columns"]:
    col0 = daily["columns"][0]
    in_day = any(s["symbol"] == "DELL" for s in col0["stocks"])
    print("in latest daily", col0["date"], in_day, "n", col0["count"])
    # find DELL pos if in any of last 5 daily columns
    for col in daily["columns"][:5]:
        for s in col["stocks"]:
            if s["symbol"] == "DELL":
                print(" daily col", col["date"], s)
                break
