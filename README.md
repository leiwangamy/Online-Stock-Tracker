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

**Settings**
- SMA period presets: 25 / 50 / 63 / 90 (also manually editable)
- Rebound lookback configurable

**Platform architecture**
- Shared SQLite database: `data/leibot.db`
- Yahoo Finance now; IBKR upgrade path later
- Responsive UI: desktop for analysis, mobile for quick daily checks

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

**Frontend**
- HTML5
- CSS3 (Responsive design with media queries)
- JavaScript (Auto-scroll, dynamic content)

**Infrastructure & Deployment**
- AWS EC2 (Ubuntu server)
- Nginx (Reverse proxy & static file serving)
- Let's Encrypt + Certbot (HTTPS, auto-renewal)
- GitHub (Version control)

📁 Project Structure
```
Online-Stock-Tracker/
│
├── app.py               # Flask application entry point
├── requirements.txt     # Python dependencies
├── Procfile             # Production server configuration (Gunicorn)
├── templates/
│   └── index.html       # Main UI template with responsive design
├── static/              # Static assets (CSS, images, dynamically generated charts)
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

Then open your browser and navigate to:
```
http://127.0.0.1:5000
```

🔐 Production Deployment Notes

**Deployment Architecture**
- **Server**: AWS EC2 (Ubuntu)
- **Application Server**: Gunicorn (multi-worker WSGI server)
- **Web Server**: Nginx (reverse proxy, static file serving, SSL termination)
- **SSL**: Let's Encrypt certificates with automatic renewal via Certbot
- **Process Management**: Systemd service for Gunicorn

**Key Production Features**
- HTTPS/SSL encryption for secure data transmission
- Automatic SSL certificate renewal (no downtime)
- Optimized static file serving through Nginx
- Automatic chart cleanup to manage disk space
- Error handling and graceful degradation
- Responsive design for all device types

This deployment follows real-world production best practices, ensuring reliability, security, and performance.

📊 Data Source

- **Stock Data**: Yahoo Finance via yFinance Python library
- **Chart Period**: 1-year historical price data
- **Update Frequency**: Real-time data fetched on each request
- **Data Availability**: Subject to Yahoo Finance API availability

📌 Disclaimer

Stock data is provided by Yahoo Finance via yFinance. This tool is for educational and informational purposes only and does not constitute financial advice. Always conduct your own research and consult with a qualified financial advisor before making investment decisions.

🙌 Author

Lei Wang
Python / Flask / AWS
GitHub: https://github.com/leiwangamy