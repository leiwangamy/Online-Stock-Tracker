📈 LeiBot 3.0 — Online Stock Tracker

A portfolio-ready Flask platform for stock research and market screening.
Built for single-name analysis and large-universe ranking on one shared database (`leibot.db`).

🔗 Live App (HTTPS):
👉 https://stock.lwsoc.com

✨ Key Features (LeiBot 3.0)

**Stock Tracker — single-name research**
- US (USD) and Canadian (CAD) tickers
- 1-year charts + market facts + decision metrics (PE, EPS, margins, etc.)

**Market Dashboard — ~520 name ranking**
- S&P 500 ∪ Nasdaq-100 deduplicated universe
- Rank by distance from configurable SMA
- Rebound rate vs recent low
- **Earnings Night Review date** — earnings column shows date only, for evening news review before overnight moves

**Research / Watchlist / AI Discovery**
- My Watchlist with layout that matches the full nav
- AI Discovery harvest + News History (non-★ auto-purge after 7 calendar days from `created_at`)

**AI Paper Trading (simulation only — no brokerage orders)**
- Ranked candidates, Stop / Take Profit, Priority Buy, daily OHLC settle
- Excel export + Reset Trading (does not clear Discovery / Watchlist / settings)
- **After Stop/Take:** auto-buy the highest-ranked AI name **not yet used** in this experiment (1:1 with exits; toggle in Settings)

**Settings**
- SMA period presets: 25 / 50 / 63 / 90 (also manually editable)
- Rebound lookback configurable
- Paper Stop / Take % and auto-replace-after-exit toggle

**Platform architecture**
- Shared SQLite database: `data/leibot.db`
- Yahoo Finance now; IBKR upgrade path later
- Responsive UI: desktop for analysis, mobile for quick daily checks
- In-app scheduler (weekdays after US close for prices + paper update; Sunday universe refresh)

**Private Order Request API V0 (Admin → Local Agent)**
- Admin creates internal PAPER Order Requests (no IBKR / no brokerage orders)
- Local Trading Agent authenticates with `Authorization: Bearer <LEIBOT_PRIVATE_AGENT_API_KEY>`
- Agent may only: list pending requests, read one request, update processing status (`PENDING` / `RECEIVED` / `REPORTED` / `ERROR`)
- Separate from public AI Paper Trading simulator

---

✨ Original Tracker Features

📊 **Multi-Stock Tracking**
- Track US (USD) and Canadian (CAD) stocks simultaneously
- Separate charts for USD and CAD stocks
- Responsive design: side-by-side on desktop, stacked on mobile

✅ **Easy Stock Selection**
- Quick-pick checkboxes for popular symbols (MSFT, AAPL, NVDA, TD.TO, RY.TO, etc.)
- Manual ticker input with comma-separated values
- Automatic duplicate removal

📈 **Historical Price Charts**
- Auto-generated 1-year historical price charts
- Dynamic chart generation with automatic cleanup (24-hour retention, max 30 files)
- High-quality visualizations with clear legends

📊 **Stock Analysis Tables**
- **Market Facts Table**: Current price, daily change, day high/low, 52-week high/low, volume metrics
- **Decision Making Table**: Market cap, P/E ratio, EPS, Beta, Dividend yield, Revenue growth, Profit margin
- Color-coded metrics: red for negative, green for significant growth
- Side-by-side comparison format for easy analysis

🔒 **Input Validation & Limits**
- Format validation: letters, numbers, dot, dash only (max 12 characters)
- Request limits: Max 5 USD stocks, Max 5 CAD stocks, Max 10 total
- Clear warnings for invalid or excess tickers
- Graceful error handling (charts display even if some stocks have errors)

🔐 **Production-Ready Deployment**
- Secure HTTPS deployment with auto-renewing SSL
- Optimized for performance and reliability

🏗️ Tech Stack 

**Backend**
- Python 3.x
- Flask (Web framework)
- yFinance (Yahoo Finance data API)
- Matplotlib (Chart generation)
- Gunicorn (Production WSGI server)
- APScheduler (in-container jobs)

**Frontend**
- HTML5
- CSS3 (Responsive design with media queries)
- JavaScript (Auto-scroll, dynamic content)

**Infrastructure & Deployment**
- AWS EC2 (Ubuntu)
- Docker Compose (`docker-compose.yml` + `docker-compose.prod.yml`)
- Nginx reverse proxy → `127.0.0.1:8001`
- Let's Encrypt + Certbot (HTTPS)
- Persistent data: host `./data` → container `/app/data`
- Secrets only on server: `/etc/leibot/prod.env` (never commit)
- GitHub (Version control)

📁 Project Structure
```
Online-Stock-Tracker/
│
├── app.py                  # Flask application entry point
├── paper_trading.py        # AI Paper Trading (simulation)
├── docker-compose.prod.yml # Production overlay (data mount + env_file)
├── requirements.txt
├── templates/
├── scripts/                # Deploy / ops helpers
└── README.md
```

🚀 How to Use 

1. **Select Stocks**
   - Use checkboxes to quickly select popular stocks
   - Or manually enter ticker symbols (comma-separated)
   - Examples: `MSFT, AAPL` or `TD.TO, RY.TO`

2. **Understand Limits**
   - Max 5 USD stocks per request
   - Max 5 CAD stocks per request
   - Max 10 total symbols per request
   - Ticker format: letters, numbers, dot (.), dash (-) only
   - Max ticker length: 12 characters

3. **View Results**
   - Click "Track Stocks" to generate charts
   - View 1-year historical price trends
   - Compare stock metrics in analysis tables
   - Page auto-scrolls to charts after submission

4. **Chart Features**
   - Charts are generated dynamically on each request
   - Old charts are automatically cleaned up (24-hour retention)
   - Responsive layout adapts to your screen size

⚙️ Local Installation 

```bash
# Clone the repository
git clone https://github.com/leiwangamy/Online-Stock-Tracker.git
cd Online-Stock-Tracker

# Create and activate virtual environment
python -m venv venv

# On Linux/Mac:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

Optional environment variables (do not commit real secrets):

| Variable | Purpose |
|---|---|
| `FLASK_SECRET_KEY` | Flask session secret |
| `LEIBOT_OWNER_PASSWORD` | Bootstrap owner password (first run) |
| `LEIBOT_PRIVATE_AGENT_API_KEY` | Bearer token for private Local Trading Agent API (min 16 chars). Used only by `/api/trading/orders/*` — never expose in HTML/JS. |

Then open your browser and navigate to:
```
http://127.0.0.1:3000
```

### Notes — Order Request API V0

Architecture:

`LeiBot Admin` → Create Order Request → `Private Trading API` → `Local Trading Agent` → Local report

| Piece | Path / detail |
|---|---|
| Admin UI | `/admin/order-requests` (owner login required) |
| Pending list | `GET /api/trading/orders/pending` |
| One request | `GET /api/trading/orders/<request_id>` |
| Status update | `POST /api/trading/orders/<request_id>/status` with JSON `{"status":"REPORTED","message":"..."}` |
| Auth | Bearer token from env `LEIBOT_PRIVATE_AGENT_API_KEY` (min 16 chars); missing/wrong → HTTP 401 |
| Smoke script | `python scripts/local_agent_v0_smoke.py --base-url http://127.0.0.1:3000` |

**Out of scope for V0:** IBKR / TWS / IB Gateway, Paper or Live brokerage execution, exposing `leibot.db` remotely.

**Keep separate:** public `/ai-trading` Paper Trading vs Admin Order Requests for the Local Agent.

🔐 Production Deployment Notes

**Live:** https://stock.lwsoc.com (Docker container `stock_web_prod` on EC2)

- Compose: `docker compose -f docker-compose.yml -f docker-compose.prod.yml`
- **Online mode:** `LEIBOT_MODE=lite` — Watchlist (My + Nasdaq-100, full technicals), Stock Tracker (news/charts), Sector Rotation only.
- **Local-only (FULL):** AI Trading, Paper Trading, AI Discovery, strategy experiments, IBKR sync, Market Dashboard, full Research — never required online.
- Local FULL: leave `LEIBOT_MODE` unset (or `full`); desktop **LeiBot Full** uses `Online-Stock-Tracker-Full` on port 3001.
- Gunicorn `--timeout 600` (long Yahoo refresh jobs)
- Nginx terminates SSL; proxies to `127.0.0.1:8001`
- App data persists under `/var/www/leibot/data`
- Secrets in `/etc/leibot/prod.env` only (not in git)

📊 Data Source

- **Stock Data**: Yahoo Finance via yFinance Python library
- **Chart Period**: 1-year historical price data
- **Update Frequency**: Real-time data fetched on each request; scheduled weekday close refresh in production
- **Data Availability**: Subject to Yahoo Finance API availability

📌 Disclaimer

Stock data is provided by Yahoo Finance via yFinance. This tool is for educational and informational purposes only and does not constitute financial advice. Paper trading is simulation only. Always conduct your own research and consult with a qualified financial advisor before making investment decisions.

🙌 Author

Lei Wang
Python / Flask / AWS
GitHub: https://github.com/leiwangamy
