"""One-shot full Strong Stock Monitor backfill."""
from strong_stocks import list_active_strong_watchlist, run_backfill

r = run_backfill()
print("RESULT", r)
d = list_active_strong_watchlist()
print("ACTIVE", d["count"], "as_of", d["as_of"])
print("TOP15")
for x in d["rows"][:15]:
    print(
        f"{x['rank']:3} {x['symbol']:8} c20={x['count20']:2} "
        f"pos={x['range_63d_pos']} rem={x['days_remaining']}"
    )
