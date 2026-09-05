from leibot_mode import is_lite, lite_endpoint_allowed, LITE_FORBIDDEN_ENDPOINTS
from update_jobs import job_paper_trading_daily, job_paper_intraday_mark
import app as A

assert is_lite()
for ep in LITE_FORBIDDEN_ENDPOINTS:
    assert not lite_endpoint_allowed(ep), ep
assert lite_endpoint_allowed("watchlist")
r = job_paper_trading_daily()
assert r.get("skipped") and r.get("reason") == "lite", r
r2 = job_paper_intraday_mark()
assert r2.get("skipped"), r2
c = A.app.test_client()
assert c.get("/ai-trading", follow_redirects=False).status_code == 302
assert c.get("/dashboard", follow_redirects=False).status_code == 302
assert c.post("/api/market/ibkr-sync", json={}).status_code == 404
assert c.get("/api/trading/orders/pending").status_code == 404
assert c.get("/watchlist?tab=mine").status_code == 200
assert c.get("/tracker").status_code == 200
assert c.get("/strong-monitor?tab=rotation").status_code == 200
assert b"AI Paper Trading" not in c.get("/settings").data
assert b"/ai-trading" not in c.get("/").data
print("lite guards OK")
