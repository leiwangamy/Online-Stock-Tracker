"""Debug why DELL is missing from strong_membership despite COUNT20>=12."""
from db import get_conn, get_setting, init_db
from strong_stocks import (
    STRONG_COUNT_THRESHOLD,
    STRONG_META_AS_OF,
    STRONG_RETENTION_DAYS,
    _membership_from_metrics,
    rebuild_membership_only,
)

init_db()
as_of = get_setting(STRONG_META_AS_OF, "") or ""
print("as_of", as_of, "threshold", STRONG_COUNT_THRESHOLD, "retention", STRONG_RETENTION_DAYS)

with get_conn() as conn:
    rows = conn.execute(
        """
        SELECT as_of_date, range_63d_pos, is_strong, count20
        FROM strong_daily WHERE symbol='DELL'
        ORDER BY as_of_date
        """
    ).fetchall()
    cal_rows = conn.execute(
        """
        SELECT DISTINCT as_of_date FROM strong_daily
        WHERE as_of_date <= ?
        ORDER BY as_of_date
        """,
        (as_of,),
    ).fetchall()
calendar = [r["as_of_date"] for r in cal_rows]
metrics = [
    (r["as_of_date"], r["range_63d_pos"], int(r["is_strong"] or 0), int(r["count20"] or 0))
    for r in rows
    if r["as_of_date"] <= as_of
]
print("metrics n", len(metrics), "last", metrics[-1] if metrics else None)
print("days with count>=12 in last 30:", [(d, c) for d, _, _, c in metrics[-30:] if c >= 12])

mem = _membership_from_metrics(metrics, as_of, calendar=calendar)
print("membership_from_metrics", mem)

# Is DELL's last date on calendar?
if metrics:
    last_d = metrics[-1][0]
    print("last metric date on calendar?", last_d in {d: i for i, d in enumerate(calendar)})
    # find last qualify date
    last_q = None
    for d, _, _, c in metrics:
        if c >= STRONG_COUNT_THRESHOLD:
            last_q = d
    print("last_q", last_q)
    if last_q:
        idx = {d: i for i, d in enumerate(calendar)}
        print("last_q idx", idx.get(last_q), "as_of idx", idx.get(as_of), "expiry", idx.get(last_q, -1) + STRONG_RETENTION_DAYS)

# Force rebuild and check
out = rebuild_membership_only()
print("rebuild", out)
with get_conn() as conn:
    row = conn.execute("SELECT * FROM strong_membership WHERE symbol='DELL'").fetchone()
    print("after rebuild DELL", dict(row) if row else None)
    # sample nearby
    near = conn.execute(
        "SELECT symbol FROM strong_membership WHERE symbol>='DCI' AND symbol<='DFY.TO' ORDER BY symbol"
    ).fetchall()
    print("near", [r["symbol"] for r in near])
