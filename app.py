import matplotlib
matplotlib.use("Agg")  # non-GUI backend for servers

import os
import uuid
import time
import glob
import re
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import yfinance as yf
import matplotlib.pyplot as plt
from werkzeug.security import check_password_hash, generate_password_hash

from db import (
    alert_status,
    dashboard_meta,
    get_alert_prices,
    get_all_settings,
    get_dashboard_by_tickers,
    get_setting,
    get_universe_flags,
    init_db,
    list_dashboard,
    list_setup,
    list_low_target_ratio,
    list_low_63d_pos,
    set_setting,
    universe_count,
    upsert_alert_price,
)
from market_data import (
    compute_ai_score,
    compute_row_mos,
    compute_target_proxy_mos,
    fetch_metrics_for_ticker,
    fund_qualifies_for_news,
    get_fund_cached_only,
    get_news_cached_only,
    get_signals,
    refresh_dashboard_cache,
)
from i18n import (
    format_ui_date,
    format_ui_datetime,
    format_ui_time,
    get_lang,
    gettext,
    ngettext_format,
    set_lang,
    tab_description,
)
from universe import refresh_universe as rebuild_universe


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-online-stock-tracker")
# Pick up template edits without a full server restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

init_db()

# Background auto-refresh (weekly names + weekday EOD prices) while the app runs.
try:
    from scheduler import start_scheduler

    start_scheduler()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Owner auth (single operator). Public site hides Est / MOS / CLV.
# ---------------------------------------------------------------------------
SESSION_OWNER_KEY = "owner_auth"


def is_owner() -> bool:
    return bool(session.get(SESSION_OWNER_KEY))


def owner_password_configured() -> bool:
    h = get_setting("owner_password_hash", None)
    return isinstance(h, str) and len(h) > 20


def _bootstrap_owner_password_from_env() -> bool:
    """If hash missing and LEIBOT_OWNER_PASSWORD is set, store hash once."""
    if owner_password_configured():
        return True
    raw = (os.environ.get("LEIBOT_OWNER_PASSWORD") or "").strip()
    if len(raw) < 6:
        return False
    set_setting("owner_password_hash", generate_password_hash(raw))
    return True


@app.context_processor
def _inject_owner_flags():
    return {
        "is_owner": is_owner(),
        "owner_password_configured": owner_password_configured(),
        "_": gettext,
        "_f": ngettext_format,
        "lang": get_lang(),
    }


@app.template_filter("format_ui_datetime")
def _format_ui_datetime_filter(value):
    """Human-readable local date + time for templates."""
    return format_ui_datetime(value)


@app.template_filter("format_ui_date")
def _format_ui_date_filter(value):
    return format_ui_date(value)


@app.template_filter("format_ui_time")
def _format_ui_time_filter(value):
    return format_ui_time(value)


@app.route("/lang/<code>")
def set_language(code: str):
    set_lang(code)
    ref = request.referrer
    if ref and ref.startswith(request.host_url):
        return redirect(ref)
    return redirect(url_for("home"))

DEFAULT_USD_STOCKS = ["MSFT"]
DEFAULT_CAD_STOCKS = ["TD.TO"]

USD_STOCK_EXAMPLES = ["MSFT", "AAPL", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "NFLX"]
CAD_STOCK_EXAMPLES = ["TD.TO", "RY.TO", "SHOP.TO", "CNR.TO", "CP.TO", "BNS.TO", "BMO.TO", "CM.TO"]

TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-]{1,12}$")


def parse_ticker_input(text: str) -> list[str]:
    if not text:
        return []
    return [p.strip().upper() for p in text.split(",") if p.strip()]


def validate_ticker_format(ticker: str) -> bool:
    """Format-only validation (does NOT guarantee it exists)."""
    if not ticker:
        return False
    return bool(TICKER_PATTERN.match(ticker.strip().upper()))


def cleanup_old_charts(max_files=30, max_age_hours=24):
    """
    Cleanup chart_*.png in ./static
    - delete older than max_age_hours
    - then keep only newest max_files (prevents explosion)
    """
    static_dir = os.path.join(app.root_path, "static")
    os.makedirs(static_dir, exist_ok=True)

    pattern = os.path.join(static_dir, "chart_*.png")
    files = glob.glob(pattern)

    now = time.time()
    max_age_sec = max_age_hours * 3600

    # 1) delete by age
    for f in files:
        try:
            if now - os.path.getmtime(f) > max_age_sec:
                os.remove(f)
        except Exception:
            pass

    # 2) enforce max_files by newest
    files = glob.glob(pattern)
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for f in files[max_files:]:
        try:
            os.remove(f)
        except Exception:
            pass


def safe_static_path(filename: str) -> str:
    static_dir = os.path.join(app.root_path, "static")
    os.makedirs(static_dir, exist_ok=True)
    return os.path.join(static_dir, filename)


def fetch_stock_info(symbol: str) -> dict:
    """
    Fetch stock information from yfinance.
    Returns a dictionary with all analysis data, using 'N/A' for unavailable fields.
    """
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        def safe_get(key, default="N/A", formatter=None):
            """Safely get value from info dict, apply formatter if provided."""
            value = info.get(key)
            if value is None or value == "":
                return default
            if formatter:
                try:
                    return formatter(value)
                except:
                    return default
            return value
        
        def format_currency(value):
            """Format large numbers as currency with appropriate suffix."""
            if value is None or value == "N/A":
                return "N/A"
            try:
                if abs(value) >= 1e12:
                    return f"${value/1e12:.2f}T"
                elif abs(value) >= 1e9:
                    return f"${value/1e9:.2f}B"
                elif abs(value) >= 1e6:
                    return f"${value/1e6:.2f}M"
                elif abs(value) >= 1e3:
                    return f"${value/1e3:.2f}K"
                else:
                    return f"${value:.2f}"
            except:
                return "N/A"
        
        def format_number(value, decimals=2):
            """Format number with specified decimals."""
            if value is None or value == "N/A":
                return "N/A"
            try:
                return f"{float(value):.{decimals}f}"
            except:
                return "N/A"
        
        def format_percent(value):
            """Format as percentage. Handles both decimal (0.025) and already percentage (2.5) formats."""
            if value is None or value == "N/A":
                return "N/A"
            try:
                v = float(value)
                # If value is > 1, assume it's already a percentage; otherwise multiply by 100
                if abs(v) > 1:
                    return f"{v:.2f}%"
                else:
                    return f"{v * 100:.2f}%"
            except:
                return "N/A"
        
        def format_volume(value):
            """Format volume with M/K suffix."""
            if value is None or value == "N/A":
                return "N/A"
            try:
                v = float(value)
                if v >= 1e9:
                    return f"{v/1e9:.2f}B"
                elif v >= 1e6:
                    return f"{v/1e6:.2f}M"
                elif v >= 1e3:
                    return f"{v/1e3:.2f}K"
                else:
                    return f"{int(v):,}"
            except:
                return "N/A"
        
        # Table 1: Facts
        current_price_raw = safe_get('currentPrice', default=None)
        if current_price_raw == "N/A" or current_price_raw is None:
            current_price_raw = safe_get('regularMarketPrice', default=None)
        
        if current_price_raw and current_price_raw != "N/A":
            try:
                current_price = f"${float(current_price_raw):.2f}"
                current_price_val = float(current_price_raw)
            except:
                current_price = "N/A"
                current_price_val = None
        else:
            current_price = "N/A"
            current_price_val = None
        
        change_dollar_raw = safe_get('regularMarketChange', default=None)
        if change_dollar_raw and change_dollar_raw != "N/A":
            try:
                change_dollar_val = float(change_dollar_raw)
                change_dollar = f"${change_dollar_val:.2f}"
            except:
                change_dollar = "N/A"
                change_dollar_val = None
        else:
            change_dollar = "N/A"
            change_dollar_val = None
        
        # Calculate percentage change from dollar change and current price
        if change_dollar_val is not None and current_price_val is not None and current_price_val != 0:
            previous_price = current_price_val - change_dollar_val
            if previous_price != 0:
                change_percent_val = (change_dollar_val / previous_price) * 100
                change_percent = f"{change_percent_val:.2f}%"
            else:
                change_percent = "N/A"
        else:
            # Fallback to yfinance value if calculation not possible
            change_percent = safe_get('regularMarketChangePercent', formatter=format_percent)
        
        change_str = f"{change_dollar} / {change_percent}" if change_dollar != "N/A" and change_percent != "N/A" else "N/A"
        
        day_high = safe_get('dayHigh', formatter=lambda x: f"${float(x):.2f}")
        day_low = safe_get('dayLow', formatter=lambda x: f"${float(x):.2f}")
        day_range = f"{day_high} / {day_low}" if day_high != "N/A" and day_low != "N/A" else "N/A"
        
        week_52_high = safe_get('fiftyTwoWeekHigh', formatter=lambda x: f"${float(x):.2f}")
        week_52_low = safe_get('fiftyTwoWeekLow', formatter=lambda x: f"${float(x):.2f}")
        week_52_range = f"{week_52_high} / {week_52_low}" if week_52_high != "N/A" and week_52_low != "N/A" else "N/A"
        
        volume = safe_get('volume', formatter=format_volume)
        avg_volume_10d = safe_get('averageVolume10days', formatter=format_volume)
        if avg_volume_10d == "N/A":
            avg_volume_10d = safe_get('averageVolume', formatter=format_volume)
        
        # Table 2: Decision Making
        market_cap = safe_get('marketCap', formatter=format_currency)
        pe_ratio = safe_get('trailingPE', formatter=format_number)
        forward_pe = safe_get('forwardPE', formatter=format_number)
        eps = safe_get('trailingEps', formatter=lambda x: f"${float(x):.2f}")
        beta = safe_get('beta', formatter=format_number)
        # Dividend yield: yfinance typically returns as decimal (0.0076 = 0.76%)
        # But values like 0.76, 0.40, 0.26 are being incorrectly shown as 76%, 40%, 26%
        # So if value > 0.1, it's likely already in percentage form (0.76 = 0.76%), don't multiply
        # If value < 0.1, it's decimal format (0.0076 = 0.76%), multiply by 100
        dividend_yield_raw = safe_get('dividendYield', default=None)
        if dividend_yield_raw and dividend_yield_raw != "N/A":
            try:
                div_val = float(dividend_yield_raw)
                # Dividend yields are typically 0-10%
                # If value > 1, definitely wrong, divide by 100
                # If value is 0.1-1, it might be percentage already (0.76 = 0.76%), use as-is
                # If value < 0.1, it's decimal (0.0076 = 0.76%), multiply by 100
                if abs(div_val) > 1:
                    # Value > 1 is wrong, divide by 100 to normalize
                    div_val = div_val / 100
                    dividend_yield = f"{div_val:.2f}%"
                elif abs(div_val) >= 0.1:
                    # Value 0.1-1, treat as percentage already (0.76 = 0.76%)
                    dividend_yield = f"{div_val:.2f}%"
                else:
                    # Value < 0.1, treat as decimal (0.0076 = 0.76%)
                    dividend_yield = f"{div_val * 100:.2f}%"
            except:
                dividend_yield = "N/A"
        else:
            dividend_yield = "N/A"
        
        # Additional overview metrics
        price_to_book = safe_get('priceToBook', formatter=format_number)
        
        # 52-week change percentage
        week_52_change = safe_get('52WeekChange', formatter=format_percent)
        
        # Revenue growth (year over year)
        revenue_growth = safe_get('revenueGrowth', formatter=format_percent)
        
        # Profit margin
        profit_margin = safe_get('profitMargins', formatter=format_percent)
        
        return {
            # Table 1: Facts
            'current_price': current_price,
            'change': change_str,
            'day_high': day_high,
            'day_low': day_low,
            'week_52_high': week_52_high,
            'week_52_low': week_52_low,
            'week_52_change': week_52_change,  # Added for overview
            'volume': volume,
            'avg_volume_10d': avg_volume_10d,
            # Table 2: Decision Making
            'market_cap': market_cap,
            'pe_ratio': pe_ratio,
            'forward_pe': forward_pe,  # Added for comparison
            'eps': eps,
            'beta': beta,
            'dividend_yield': dividend_yield,
            # Additional Overview Metrics
            'price_to_book': price_to_book,
            'revenue_growth': revenue_growth,
            'profit_margin': profit_margin,
        }
    except Exception as e:
        print(f"Error fetching info for {symbol}: {e}")
        return {
            'current_price': 'N/A',
            'change': 'N/A',
            'day_high': 'N/A',
            'day_low': 'N/A',
            'week_52_high': 'N/A',
            'week_52_low': 'N/A',
            'week_52_change': 'N/A',
            'volume': 'N/A',
            'avg_volume_10d': 'N/A',
            'market_cap': 'N/A',
            'pe_ratio': 'N/A',
            'forward_pe': 'N/A',
            'eps': 'N/A',
            'beta': 'N/A',
            'dividend_yield': 'N/A',
            'price_to_book': 'N/A',
            'revenue_growth': 'N/A',
            'profit_margin': 'N/A',
        }


def generate_charts(usd_stocks, cad_stocks, chart_filename: str):
    """
    Returns: (usd_data, cad_data, not_found_tickers)
    - not_found_tickers: tickers that passed format check but returned no data from Yahoo
    """
    usd_data = {}
    cad_data = {}
    not_found_tickers = []

    def fetch_history(symbol: str):
        # You can tweak these to reduce "empty" results:
        # auto_adjust=False keeps raw prices; interval default is 1d.
        t = yf.Ticker(symbol)
        return t.history(period="1y")

    # Fetch USD
    for symbol in usd_stocks:
        try:
            hist = fetch_history(symbol)
            if hist is not None and (not hist.empty) and ("Close" in hist.columns):
                usd_data[symbol] = hist
            else:
                not_found_tickers.append(symbol)
        except Exception as e:
            print(f"[USD] Error fetching {symbol}: {e}")
            not_found_tickers.append(symbol)

    # Fetch CAD
    for symbol in cad_stocks:
        try:
            hist = fetch_history(symbol)
            if hist is not None and (not hist.empty) and ("Close" in hist.columns):
                cad_data[symbol] = hist
            else:
                not_found_tickers.append(symbol)
        except Exception as e:
            print(f"[CAD] Error fetching {symbol}: {e}")
            not_found_tickers.append(symbol)

    # If nothing valid at all, return Nones (but still return the invalid list)
    if not usd_data and not cad_data:
        return None, None, sorted(set(not_found_tickers))

    # ---------- Plotting ----------
    if usd_data and cad_data:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False, dpi=100)
        axes = axes.flatten()

        # USD left
        ax = axes[0]
        for symbol, data in usd_data.items():
            ax.plot(data.index, data["Close"], label=symbol, linewidth=2.5)
        ax.set_ylabel("Price (USD)", fontsize=12, fontweight="bold")
        ax.set_xlabel("Date", fontsize=12, fontweight="bold")
        ax.set_title("USD Stocks - 1-Year", fontsize=14, fontweight="bold", pad=10)
        ax.legend(loc="best", fontsize=10, framealpha=0.9)
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.tick_params(axis="both", which="major", labelsize=9)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")

        # CAD right
        ax = axes[1]
        for symbol, data in cad_data.items():
            ax.plot(data.index, data["Close"], label=symbol, linewidth=2.5)
        ax.set_ylabel("Price (CAD)", fontsize=12, fontweight="bold")
        ax.set_xlabel("Date", fontsize=12, fontweight="bold")
        ax.set_title("CAD Stocks - 1-Year", fontsize=14, fontweight="bold", pad=10)
        ax.legend(loc="best", fontsize=10, framealpha=0.9)
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.tick_params(axis="both", which="major", labelsize=9)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")

        fig.suptitle("1-Year Stock Prices: USD vs CAD", fontsize=16, fontweight="bold", y=0.98)

    elif usd_data:
        fig, ax = plt.subplots(1, 1, figsize=(8, 5), dpi=100)
        for symbol, data in usd_data.items():
            ax.plot(data.index, data["Close"], label=symbol, linewidth=2.5)
        ax.set_ylabel("Price (USD)", fontsize=12, fontweight="bold")
        ax.set_xlabel("Date", fontsize=12, fontweight="bold")
        ax.set_title("USD Stocks - 1-Year Prices", fontsize=14, fontweight="bold", pad=10)
        ax.legend(loc="best", fontsize=10, framealpha=0.9)
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.tick_params(axis="both", which="major", labelsize=9)
        fig.autofmt_xdate(rotation=45, ha="right")
        fig.suptitle("1-Year Stock Prices", fontsize=16, fontweight="bold", y=0.98)

    else:  # cad_data only
        fig, ax = plt.subplots(1, 1, figsize=(8, 5), dpi=100)
        for symbol, data in cad_data.items():
            ax.plot(data.index, data["Close"], label=symbol, linewidth=2.5)
        ax.set_ylabel("Price (CAD)", fontsize=12, fontweight="bold")
        ax.set_xlabel("Date", fontsize=12, fontweight="bold")
        ax.set_title("CAD Stocks - 1-Year Prices", fontsize=14, fontweight="bold", pad=10)
        ax.legend(loc="best", fontsize=10, framealpha=0.9)
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.tick_params(axis="both", which="major", labelsize=9)
        fig.autofmt_xdate(rotation=45, ha="right")
        fig.suptitle("1-Year Stock Prices", fontsize=16, fontweight="bold", y=0.98)

    fig.tight_layout(rect=[0, 0, 1, 0.95])

    out_path = safe_static_path(chart_filename)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    return usd_data, cad_data, sorted(set(not_found_tickers))


@app.route("/tracker", methods=["GET", "POST"])
def stock_tracker():
    error_msg = None
    chart_file = None
    usd_data = None
    cad_data = None

    # We'll show these warnings on the page
    warning_invalid = []   # bad format
    warning_not_found = [] # good format but Yahoo returned no data
    warning_excess = []    # removed due to limit

    # Defaults
    usd_stocks = DEFAULT_USD_STOCKS[:]
    cad_stocks = DEFAULT_CAD_STOCKS[:]

    try:
        if request.method == "POST":
            usd_checked = request.form.getlist("usd_checked")
            cad_checked = request.form.getlist("cad_checked")
            usd_input = request.form.get("usd_stocks", "").strip()
            cad_input = request.form.get("cad_stocks", "").strip()
        else:
            usd_checked = request.args.getlist("usd_checked") or []
            cad_checked = request.args.getlist("cad_checked") or []
            usd_input = request.args.get("usd_stocks", "").strip()
            cad_input = request.args.get("cad_stocks", "").strip()

        # Keep order (DON'T use set, it randomizes order)
        def unique_keep_order(items):
            seen = set()
            out = []
            for x in items:
                x = (x or "").strip().upper()
                if x and x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        usd_raw = unique_keep_order(usd_checked + parse_ticker_input(usd_input))
        cad_raw = unique_keep_order(cad_checked + parse_ticker_input(cad_input))

        # Format validation
        valid_usd = []
        valid_cad = []

        for t in usd_raw:
            (valid_usd if validate_ticker_format(t) else warning_invalid).append(t)
        for t in cad_raw:
            (valid_cad if validate_ticker_format(t) else warning_invalid).append(t)

        # Limits
        MAX_USD, MAX_CAD, MAX_TOTAL = 5, 5, 10

        if len(valid_usd) > MAX_USD:
            warning_excess += valid_usd[MAX_USD:]
            valid_usd = valid_usd[:MAX_USD]

        if len(valid_cad) > MAX_CAD:
            warning_excess += valid_cad[MAX_CAD:]
            valid_cad = valid_cad[:MAX_CAD]

        # total limit (remove from CAD first)
        total = len(valid_usd) + len(valid_cad)
        if total > MAX_TOTAL:
            extra = total - MAX_TOTAL
            take = min(extra, len(valid_cad))
            warning_excess += valid_cad[-take:]
            valid_cad = valid_cad[:-take]
            extra -= take
            if extra > 0:
                warning_excess += valid_usd[-extra:]
                valid_usd = valid_usd[:-extra]

        usd_stocks = valid_usd or DEFAULT_USD_STOCKS
        cad_stocks = valid_cad or DEFAULT_CAD_STOCKS

        # Only generate on POST, or GET with explicit params
        should_generate = (
            request.method == "POST"
            or bool(usd_input or cad_input or usd_checked or cad_checked)
        )

        if should_generate:
            cleanup_old_charts(max_files=30, max_age_hours=24)
            chart_file = f"chart_{uuid.uuid4().hex}.png"

            usd_data, cad_data, not_found = generate_charts(usd_stocks, cad_stocks, chart_file)
            warning_not_found = not_found or []

            # If chart generation returned nothing, remove chart_file
            if usd_data is None and cad_data is None:
                chart_file = None

        else:
            usd_data = None
            cad_data = None
            chart_file = None

    except Exception as e:
        error_msg = f"Error processing request: {str(e)}"
        print(f"ERROR in index(): {error_msg}")
        import traceback
        traceback.print_exc()

    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Determine if we have any valid data to show
    has_data = bool(usd_data) or bool(cad_data)
    if (not has_data) and (chart_file is not None):
        # If no data but we generated chart file, remove it
        chart_file = None

    # If user tried and got nothing, show error but still show warnings lists
    if should_generate and not has_data and not error_msg:
        error_msg = "No valid stock data found. Please check your stock symbols."

    # --- Build table data if we have data ---
    table_data = []
    usd_averages = {}
    cad_averages = {}
    stock_info = {}  # Dictionary to store analysis data for each stock

    if has_data:
        all_data = {}
        if usd_data:
            all_data.update(usd_data)
        if cad_data:
            all_data.update(cad_data)

        # recent 7 trading dates across all tickers
        all_dates = set()
        for df in all_data.values():
            all_dates.update(list(df.index))
        recent_dates = sorted(all_dates)[-7:] if all_dates else []

        all_stocks = usd_stocks + cad_stocks
        for date in recent_dates:
            row = {"date": date.strftime("%Y-%m-%d")}
            for symbol in all_stocks:
                if symbol in all_data and date in all_data[symbol].index:
                    row[symbol] = f"{all_data[symbol].loc[date]['Close']:.2f}"
                else:
                    row[symbol] = "-"
            table_data.append(row)

        for symbol in usd_stocks:
            if usd_data and symbol in usd_data:
                usd_averages[symbol] = round(float(usd_data[symbol]["Close"].mean()), 2)
            # Fetch analysis data for each stock
            stock_info[symbol] = fetch_stock_info(symbol)

        for symbol in cad_stocks:
            if cad_data and symbol in cad_data:
                cad_averages[symbol] = round(float(cad_data[symbol]["Close"].mean()), 2)
            # Fetch analysis data for each stock
            stock_info[symbol] = fetch_stock_info(symbol)

    # Combine invalid lists for display convenience
    # (format invalid + not found)
    combined_invalid = sorted(set((warning_invalid or []) + (warning_not_found or []))) or None
    warning_excess = sorted(set(warning_excess)) or None

    return render_template(
        "index.html",
        usd_stocks=usd_stocks,
        cad_stocks=cad_stocks,
        usd_examples=USD_STOCK_EXAMPLES,
        cad_examples=CAD_STOCK_EXAMPLES,
        table_data=table_data,
        usd_averages=usd_averages,
        cad_averages=cad_averages,
        stock_info=stock_info,  # Analysis data for each stock
        last_updated=last_updated,
        chart_file=chart_file,
        error=error_msg,
        warning_invalid=combined_invalid,   # show on page
        warning_excess=warning_excess,      # show on page
    )


@app.route("/")
def home():
    """Public home — product entrances + live Today's LeiBot status."""
    today = {
        "universe_count": None,
        "ai_candidates": None,
        "open_positions": None,
        "paper_equity": None,
        "today_pnl": None,
        "updated_at": None,
    }
    try:
        today["universe_count"] = int(universe_count() or 0)
    except Exception:
        pass
    try:
        from paper_trading import list_candidates, portfolio_summary

        cands = list_candidates()
        today["ai_candidates"] = len(cands) if cands is not None else None
        summary = portfolio_summary()
        today["open_positions"] = summary.get("open_trades")
        today["paper_equity"] = summary.get("current_equity")
        today["today_pnl"] = summary.get("today_pnl")
        today["updated_at"] = (
            summary.get("last_daily_update")
            or summary.get("updated_at")
            or get_setting("paper_candidates_updated_at")
        )
    except Exception:
        pass
    if not today["updated_at"]:
        try:
            meta = dashboard_meta()
            today["updated_at"] = meta.get("updated_at") if meta else None
        except Exception:
            pass
    return render_template("home.html", today=today)


# Market Dashboard tabs (index groups). Order controls the tab order.
DASHBOARD_GROUPS = ["core", "sp400", "sp600", "tsx"]
GROUP_LABELS = {
    "core": "S&P500 + Nasdaq100",
    "sp400": "Mid Cap · S&P 400",
    "sp600": "Small Cap · S&P 600",
    "tsx": "Canada · S&P/TSX Composite",
}


def _normalize_group(value: str | None) -> str:
    return value if value in DASHBOARD_GROUPS else "core"


@app.route("/dashboard")
def market_dashboard():
    settings = get_all_settings()
    group = _normalize_group(request.args.get("group"))
    rows = list_dashboard(order="dist_asc", group=group)
    tabs = [
        {
            "key": key,
            "label": gettext(GROUP_LABELS[key]),
            "count": universe_count(group=key),
        }
        for key in DASHBOARD_GROUPS
    ]
    return render_template(
        "dashboard.html",
        rows=rows,
        meta=dashboard_meta(group=group),
        universe_count=universe_count(group=group),
        sma_period=int(settings.get("sma_period", 25)),
        group=group,
        group_label=gettext(GROUP_LABELS[group]),
        tabs=tabs,
    )


@app.route("/dashboard/refresh-universe", methods=["POST"])
def refresh_universe():
    group = _normalize_group(request.form.get("group"))
    try:
        result = rebuild_universe()
        flash(
            ngettext_format(
                "Universe updated: S&P500 {sp500} + Nasdaq100 {ndx100} + S&P400 {sp400} "
                "+ S&P600 {sp600} + TSX {tsx} → {unique} unique",
                sp500=result["sp500"],
                ndx100=result["ndx100"],
                sp400=result["sp400"],
                sp600=result["sp600"],
                tsx=result["tsx"],
                unique=result["unique"],
            ),
            "ok",
        )
    except Exception as exc:
        flash(ngettext_format("Universe update failed: {exc}", exc=exc), "warning")
    return redirect(url_for("market_dashboard", group=group))


@app.route("/dashboard/refresh", methods=["POST"])
def refresh_dashboard():
    group = _normalize_group(request.form.get("group"))
    try:
        if universe_count() == 0:
            rebuild_universe()
        result = refresh_dashboard_cache(group=group)
        flash(
            ngettext_format(
                "Prices refreshed ({group}): ok {ok} / errors {errors} (SMA{sma}, universe {universe})",
                group=gettext(GROUP_LABELS[group]),
                ok=result["ok"],
                errors=result["errors"],
                sma=result["sma_period"],
                universe=result["universe"],
            ),
            "ok",
        )
    except Exception as exc:
        flash(ngettext_format("Price refresh failed: {exc}", exc=exc), "warning")
    return redirect(url_for("market_dashboard", group=group))


@app.route("/refresh/all-prices", methods=["POST"])
def refresh_all_prices():
    """
    Manual: refresh ALL index-pool dashboard prices + Watchlist (incl. MANUAL).
    Same job as weekday EOD schedule (update_jobs.job_refresh_prices).
    """
    nxt = (request.form.get("next") or "").strip()
    # Only allow internal relative redirects
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = url_for("market_dashboard")
    try:
        from update_jobs import job_refresh_prices

        result = job_refresh_prices(max_workers=4)
        flash(
            ngettext_format(
                "All pools refreshed: ok {ok} / errors {errors} (universe {universe}) · "
                "Watchlist ok {watchlist_ok} / errors {watchlist_errors}",
                ok=result.get("ok"),
                errors=result.get("errors"),
                universe=result.get("universe"),
                watchlist_ok=result.get("watchlist_ok"),
                watchlist_errors=result.get("watchlist_errors"),
            ),
            "ok",
        )
    except Exception as exc:
        flash(ngettext_format("All pools / Watchlist refresh failed: {exc}", exc=exc), "warning")
    return redirect(nxt)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    weekdays = [
        ("mon", gettext("Mon")),
        ("tue", gettext("Tue")),
        ("wed", gettext("Wed")),
        ("thu", gettext("Thu")),
        ("fri", gettext("Fri")),
        ("sat", gettext("Sat")),
        ("sun", gettext("Sun")),
    ]
    if request.method == "POST":
        try:
            sma_period = int(request.form.get("sma_period", 25))
            rebound_lookback = int(request.form.get("rebound_lookback", sma_period))
            if sma_period < 5 or sma_period > 250:
                raise ValueError(gettext("SMA period must be between 5 and 250"))
            if rebound_lookback < 5 or rebound_lookback > 250:
                raise ValueError(gettext("Rebound lookback must be between 5 and 250"))
            set_setting("sma_period", sma_period)
            set_setting("rebound_lookback", rebound_lookback)
            set_setting("data_source", "yahoo")

            weekday = (request.form.get("schedule_universe_weekday") or "sun").lower()
            if weekday not in {w[0] for w in weekdays}:
                raise ValueError(gettext("Invalid universe weekday"))
            u_hour = int(request.form.get("schedule_universe_hour", 10))
            u_min = int(request.form.get("schedule_universe_minute", 0))
            p_hour = int(request.form.get("schedule_price_hour", 13))
            p_min = int(request.form.get("schedule_price_minute", 15))
            for label, h, m in (
                (gettext("Universe update time"), u_hour, u_min),
                (gettext("Price update time"), p_hour, p_min),
            ):
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError(ngettext_format("Invalid {label}", label=label))
            set_setting("schedule_universe_weekday", weekday)
            set_setting("schedule_universe_hour", u_hour)
            set_setting("schedule_universe_minute", u_min)
            set_setting("schedule_price_hour", p_hour)
            set_setting("schedule_price_minute", p_min)

            stop_pct = float(request.form.get("paper_stop_loss_pct", 5))
            take_pct = float(request.form.get("paper_take_profit_pct", 10))
            if not (0.5 <= stop_pct <= 50):
                raise ValueError(gettext("Stop Loss % must be between 0.5 and 50"))
            if not (0.5 <= take_pct <= 100):
                raise ValueError(gettext("Take Profit % must be between 0.5 and 100"))
            set_setting("paper_stop_loss_pct", stop_pct)
            set_setting("paper_take_profit_pct", take_pct)
            try:
                from paper_trading import sync_portfolio_limits_from_settings

                sync_portfolio_limits_from_settings()
            except Exception:
                pass

            flash(
                ngettext_format(
                    "Saved: SMA={sma}, rebound lookback={rebound}. Auto: universe weekly "
                    "{weekday} {uh:02d}:{um:02d} PT; prices weekdays {ph:02d}:{pm:02d} PT "
                    "after US close. Paper SL −{stop}% / TP +{take}%. Restart app for in-app "
                    "schedule; Windows tasks use install-time values.",
                    sma=sma_period,
                    rebound=rebound_lookback,
                    weekday=dict(weekdays)[weekday],
                    uh=u_hour,
                    um=u_min,
                    ph=p_hour,
                    pm=p_min,
                    stop=stop_pct,
                    take=take_pct,
                ),
                "ok",
            )
            return redirect(url_for("settings"))
        except Exception as exc:
            flash(ngettext_format("Save failed: {exc}", exc=exc), "warning")

    settings_data = get_all_settings()
    try:
        from scheduler import scheduler_status

        sched = scheduler_status()
    except Exception:
        sched = {"enabled": False, "running": False, "jobs": []}
    return render_template(
        "settings.html",
        sma_period=int(settings_data.get("sma_period", 25)),
        rebound_lookback=int(settings_data.get("rebound_lookback", 25)),
        presets=settings_data.get("sma_presets", [25, 50, 63, 90]),
        weekdays=weekdays,
        schedule_universe_weekday=str(settings_data.get("schedule_universe_weekday", "sun")),
        schedule_universe_hour=int(settings_data.get("schedule_universe_hour", 10)),
        schedule_universe_minute=int(settings_data.get("schedule_universe_minute", 0)),
        schedule_price_hour=int(settings_data.get("schedule_price_hour", 13)),
        schedule_price_minute=int(settings_data.get("schedule_price_minute", 15)),
        paper_stop_loss_pct=float(settings_data.get("paper_stop_loss_pct", 5.0)),
        paper_take_profit_pct=float(settings_data.get("paper_take_profit_pct", 10.0)),
        scheduler=sched,
    )


# Group ③ — long-term saved names (see watchlist_config; shared with update_jobs).
from watchlist_config import (
    MY_WATCHLIST,
    add_my_watchlist_ticker,
    collect_watchlist_tickers,
    get_my_watchlist,
    remove_my_watchlist_ticker,
    validate_ticker_token,
)

MAX_TEMP_TICKERS = 20
MAX_AUTO_ROWS = 30  # live Yahoo enrich cap for Oversold; all matches still listed
# Progressive fill per page load; full Watchlist is warmed by batch_watchlist_valuations.py
# using the same ensure_valuations / ensure_clvs engines (single source of truth).
VALUATION_MAX_NEW_PER_REQUEST = 8
CLV_MAX_NEW_PER_REQUEST = 8


def _pools_label(row: dict) -> str:
    """Index membership for display. Tickers outside our universe pools → MANUAL."""
    labels = []
    if row.get("in_sp500"):
        labels.append("S&P500")
    if row.get("in_ndx100"):
        labels.append("Nasdaq100")
    if row.get("in_sp400"):
        labels.append("S&P400")
    if row.get("in_sp600"):
        labels.append("S&P600")
    if row.get("in_tsx"):
        labels.append("TSX")
    return " / ".join(labels) if labels else "MANUAL"


def _enrich(row: dict) -> dict:
    row = dict(row)
    row["pools"] = _pools_label(row)
    return row


def _rows_for_tickers(tickers: list[str]) -> list[dict]:
    """Build watchlist rows for explicit tickers: prefer fresh cache, else fetch live."""
    import valuation_config as cfg
    from db import save_dashboard_rows
    from market_data import resolve_watchlist_mos_price

    clean = []
    seen = set()
    for t in tickers:
        t = (t or "").strip().upper()
        if t and t not in seen and validate_ticker_format(t):
            seen.add(t)
            clean.append(t)
    if not clean:
        return []

    cached = get_dashboard_by_tickers(clean)
    flags = get_universe_flags(clean)
    settings_data = get_all_settings()
    sma = int(settings_data.get("sma_period", 25))
    reb = int(settings_data.get("rebound_lookback", sma))
    stale_limit = float(getattr(cfg, "MOS_PRICE_STALE_HOURS", 72))

    out = []
    refreshed: list[dict] = []
    for t in clean:
        row = cached.get(t)
        use_cache = False
        if row and row.get("price") is not None:
            mos_px = resolve_watchlist_mos_price(row, stale_hours=stale_limit)
            use_cache = not mos_px.get("stale")
        if use_cache:
            enriched = _enrich(row)
            enriched.setdefault("price_source", "dashboard_cache")
            out.append(enriched)
            continue
        meta = flags.get(t, {})
        metrics = fetch_metrics_for_ticker(t, sma_period=sma, rebound_lookback=reb, meta=meta)
        if metrics:
            merged = dict(metrics)
            merged.update(
                {k: meta.get(k) for k in ("in_sp500", "in_ndx100", "in_sp400", "in_sp600", "in_tsx")}
            )
            merged["price_source"] = "live_yahoo"
            out.append(_enrich(merged))
            refreshed.append(merged)
        elif row:
            # Live fetch failed — keep stale cache rather than blanking the row
            enriched = _enrich(row)
            enriched.setdefault("price_source", "dashboard_cache_stale")
            out.append(enriched)
        else:
            out.append({"ticker": t, "pools": "MANUAL", "not_found": True})
    if refreshed:
        try:
            save_dashboard_rows(refreshed, replace_all=False)
        except Exception:
            pass
    return out


@app.route("/login", methods=["GET", "POST"])
def owner_login():
    _bootstrap_owner_password_from_env()
    need_setup = not owner_password_configured()
    nxt = (request.values.get("next") or "").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = url_for("watchlist", tab="mine")

    if request.method == "POST":
        pw = request.form.get("password") or ""
        if need_setup:
            pw2 = request.form.get("password2") or ""
            if len(pw) < 6:
                flash(gettext("Password must be at least 6 characters"), "warning")
            elif pw != pw2:
                flash(gettext("Passwords do not match"), "warning")
            else:
                set_setting("owner_password_hash", generate_password_hash(pw))
                session[SESSION_OWNER_KEY] = True
                flash(gettext("Password saved — you are signed in"), "ok")
                return redirect(nxt)
        else:
            stored = get_setting("owner_password_hash", "")
            if isinstance(stored, str) and check_password_hash(stored, pw):
                session[SESSION_OWNER_KEY] = True
                flash(gettext("Signed in"), "ok")
                return redirect(nxt)
            flash(gettext("Wrong password"), "warning")

    return render_template(
        "login.html",
        need_setup=need_setup,
        next=nxt,
    )


@app.route("/logout", methods=["POST", "GET"])
def owner_logout():
    session.pop(SESSION_OWNER_KEY, None)
    flash(gettext("Signed out"), "ok")
    return redirect(url_for("watchlist"))


@app.route("/watchlist", methods=["GET", "POST"])
def watchlist():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_temp":
            temp = list(session.get("temp_watchlist", []))
            for t in parse_ticker_input(request.form.get("temp_tickers", "")):
                t = t.upper()
                if validate_ticker_format(t) and t not in temp:
                    temp.append(t)
            session["temp_watchlist"] = temp[:MAX_TEMP_TICKERS]
        elif action == "clear_temp":
            session.pop("temp_watchlist", None)
        elif action in ("add_mine", "remove_mine"):
            if not is_owner():
                flash(gettext("Please sign in to edit My Watchlist"), "warning")
                return redirect(url_for("owner_login", next=url_for("watchlist", tab="mine")))
            try:
                if action == "add_mine":
                    for t in parse_ticker_input(request.form.get("mine_tickers", "")):
                        if validate_ticker_token(t):
                            add_my_watchlist_ticker(t)
                    flash(gettext("My Watchlist updated"), "ok")
                else:
                    remove_my_watchlist_ticker(request.form.get("ticker", ""))
                    flash(gettext("Removed from My Watchlist"), "ok")
            except ValueError as exc:
                flash(str(exc), "warning")
            return redirect(url_for("watchlist", tab="mine"))
        return redirect(url_for("watchlist", tab=request.args.get("tab") or "temp"))

    settings_data = get_all_settings()
    sma_period = int(settings_data.get("sma_period", 25))
    temp_tickers = session.get("temp_watchlist", [])
    mine_list = get_my_watchlist()
    show_valuation = is_owner()

    tab = request.args.get("tab", "setup")
    # Legacy bookmarks: oversold / pullback → merged setup tab
    if tab in ("oversold", "pullback"):
        tab = "setup"
    if tab not in ("setup", "low_target", "low_63d", "mine", "temp"):
        tab = "setup"

    # Cheap cache-only lists (no live fetch) — used for data and tab counts.
    setup = [_enrich(r) for r in list_setup(-10.0)]
    for r in setup:
        r.setdefault("price_source", "dashboard_cache")
    low_target = [_enrich(r) for r in list_low_target_ratio(0.8)]
    for r in low_target:
        r.setdefault("price_source", "dashboard_cache")
    low_63d = [_enrich(r) for r in list_low_63d_pos(25.0)]
    for r in low_63d:
        r.setdefault("price_source", "dashboard_cache")

    # Live-fetch groups only build rows for the active tab (they hit Yahoo).
    rows = []
    skip_heavy = False  # 新闻 / DCF / CLV / AI（及 live 财报抓取）
    fund_cache_only = False
    if tab == "setup":
        # Show every match so tab count == table rows. Live Yahoo enrich is
        # still capped (MAX_AUTO_ROWS); overflow uses shared fund/news cache.
        rows = setup
    elif tab == "low_target":
        # Lightweight: all ratio hits; 财报 from existing cache only (no refetch).
        rows = low_target
        skip_heavy = True
        fund_cache_only = True
    elif tab == "low_63d":
        # 63D Position < 25%; fund from shared cache; news only if pass rate >= 60%.
        rows = low_63d
        skip_heavy = True
        fund_cache_only = True
    elif tab == "mine":
        rows = _rows_for_tickers(mine_list)
    elif tab == "temp":
        rows = _rows_for_tickers(temp_tickers)

    signals = {}
    iv_results = {}
    clv_results = {}
    fund_cache_hits = 0
    fund_cache_total = len(rows)
    low_target_ms = None
    if fund_cache_only:
        import time as _time

        t0 = _time.perf_counter()
        tickers = [r["ticker"] for r in rows if r.get("ticker")]
        fund_map = get_fund_cached_only(tickers)
        news_tickers = [t for t in tickers if fund_qualifies_for_news(fund_map.get(t))]
        news_map = get_news_cached_only(news_tickers) if news_tickers else {}
        for r in rows:
            f = fund_map.get((r.get("ticker") or "").upper())
            r["fund"] = f
            if f and f.get("health") != "unknown":
                fund_cache_hits += 1
            # News only when Financial Pass Rate >= 60% (ok / total_known).
            if fund_qualifies_for_news(f):
                r["news"] = news_map.get((r.get("ticker") or "").upper())
            else:
                r["news"] = None
            r["est_value"] = None
            r["bear_value"] = None
            r["bull_value"] = None
            r["mos_pct"] = None
            r["est_tooltip"] = "本页暂不计算估值（DCF）"
            r["clv"] = None
            r["clv_pct_price"] = None
            r["clv_tooltip"] = "本页暂不计算 CLV"
            r["dcf_below_clv"] = False
            r["ai"] = None
        low_target_ms = int((_time.perf_counter() - t0) * 1000)
    elif not skip_heavy:
        # Live enrich a bounded prefix (keeps Oversold page latency in check).
        live_rows = rows[:MAX_AUTO_ROWS] if tab == "setup" else rows
        overflow_rows = rows[MAX_AUTO_ROWS:] if tab == "setup" else []

        signals = get_signals([r["ticker"] for r in live_rows if r.get("ticker")])
        tickers_shown = [r["ticker"] for r in live_rows if r.get("ticker") and not r.get("not_found")]
        # Est / MOS / CLV only for logged-in owner (methods still under development).
        if show_valuation:
            try:
                from valuation_engine import ensure_valuations

                iv_results = ensure_valuations(
                    tickers_shown, force=False, max_new=VALUATION_MAX_NEW_PER_REQUEST
                )
            except Exception:
                iv_results = {}
            try:
                from clv_engine import ensure_clvs

                clv_results = ensure_clvs(
                    tickers_shown, force=False, max_new=CLV_MAX_NEW_PER_REQUEST
                )
            except Exception:
                clv_results = {}

        # Overflow matches: fund/news from shared cache so AI / Financial still populate.
        if overflow_rows:
            otickers = [r["ticker"] for r in overflow_rows if r.get("ticker")]
            fund_map = get_fund_cached_only(otickers)
            news_tickers = [t for t in otickers if fund_qualifies_for_news(fund_map.get(t))]
            news_map = get_news_cached_only(news_tickers) if news_tickers else {}
            for r in overflow_rows:
                f = fund_map.get((r.get("ticker") or "").upper())
                r["fund"] = f
                if fund_qualifies_for_news(f):
                    r["news"] = news_map.get((r.get("ticker") or "").upper())
                else:
                    r["news"] = None

    for r in rows:
        if skip_heavy:
            continue

        sig = signals.get(r.get("ticker"))
        if sig:
            r["fund"] = sig.get("fund")
            r["news"] = sig.get("news")
        vr = iv_results.get(r.get("ticker") or "")
        # Est.Value from slow valuation cache; MOS always from this row's Current Price
        mos_info = compute_row_mos(
            getattr(vr, "est_value", None) if (vr is not None and getattr(vr, "ok", False)) else None,
            r,
        )
        r["mos_price"] = mos_info["price"]
        r["mos_price_source"] = mos_info["source"]
        r["mos_price_as_of"] = mos_info["as_of"]
        r["mos_price_age_hours"] = mos_info.get("age_hours")
        r["mos_stale"] = bool(mos_info.get("stale"))
        r["mos_stale_reason"] = mos_info.get("stale_reason")
        if vr is not None and getattr(vr, "ok", False):
            r["est_value"] = getattr(vr, "est_value", None)
            r["bear_value"] = getattr(vr, "bear_value", None)
            r["bull_value"] = getattr(vr, "bull_value", None)
            r["mos_pct"] = mos_info["mos_pct"]  # None when price stale
            try:
                r["est_tooltip"] = vr.tooltip() if hasattr(vr, "tooltip") else ""
            except Exception:
                r["est_tooltip"] = "Valuation available"
        else:
            reason = getattr(vr, "failure_reason", None) if vr is not None else None
            r["est_value"] = None
            r["bear_value"] = None
            r["bull_value"] = None
            r["mos_pct"] = None
            if vr is not None and hasattr(vr, "tooltip"):
                try:
                    r["est_tooltip"] = vr.tooltip()
                except Exception:
                    r["est_tooltip"] = reason or "Valuation unavailable"
            else:
                r["est_tooltip"] = reason or "Valuation unavailable"

        # CLV (independent of DCF) + cross-check warning only
        cr = clv_results.get(r.get("ticker") or "")
        r["clv"] = None
        r["clv_pct_price"] = None
        r["clv_tooltip"] = "CLV unavailable"
        r["dcf_below_clv"] = False
        if cr is not None:
            try:
                px = r.get("price")
                r["clv_tooltip"] = cr.tooltip(px if isinstance(px, (int, float)) else None)
            except Exception:
                r["clv_tooltip"] = cr.failure_reason or "CLV unavailable"
            if getattr(cr, "ok", False) and cr.clv_per_share is not None:
                r["clv"] = cr.clv_per_share
                if r.get("price") and r["price"] > 0:
                    r["clv_pct_price"] = round(cr.clv_per_share / float(r["price"]) * 100, 1)
                if r.get("est_value") is not None and r["est_value"] < cr.clv_per_share:
                    r["dcf_below_clv"] = True
                    warn = "DCF below conservative asset floor — review valuation assumptions"
                    r["est_tooltip"] = (r.get("est_tooltip") or "") + "\n" + warn
                    r["clv_tooltip"] = (r.get("clv_tooltip") or "") + "\n" + warn
            elif cr.failure_reason:
                r["clv_tooltip"] = cr.tooltip()

        # Public MOS T before AI so Score V1 can use target-based factor only.
        if not r.get("not_found"):
            r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))
            r["ai"] = compute_ai_score(r)

        if not show_valuation:
            r["est_value"] = None
            r["bear_value"] = None
            r["bull_value"] = None
            r["mos_pct"] = None
            r["est_tooltip"] = "登录后可见（估值方法开发中）"
            r["clv"] = None
            r["clv_pct_price"] = None
            r["clv_tooltip"] = "登录后可见（估值方法开发中）"
            r["dcf_below_clv"] = False

    # Attach manual Alert Prices (independent of valuation / AI).
    alert_map = get_alert_prices([r.get("ticker") for r in rows if r.get("ticker")])
    for r in rows:
        t = (r.get("ticker") or "").upper()
        ap = alert_map.get(t)
        r["alert_price"] = ap
        r["alert"] = alert_status(r.get("price"), ap)
        # Ensure MOS T on cache-only tabs (AI skipped there) and any rows missed above.
        if r.get("mos_t") is None and not r.get("not_found"):
            r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))

    tabs = [
        {"key": "setup", "label": "🔻 " + gettext("Oversold pullback"), "count": len(setup)},
        {"key": "low_target", "label": "🎯 " + gettext("Target Ratio < 80%"), "count": len(low_target)},
        {"key": "low_63d", "label": "📉 " + gettext("63D Position < 25%"), "count": len(low_63d)},
        {"key": "mine", "label": "⭐ " + gettext("My Watchlist"), "count": len(mine_list)},
        {"key": "temp", "label": "🕒 " + gettext("Temp"), "count": len(temp_tickers)},
    ]

    desc = tab_description(
        tab,
        mine_list_label="、".join(mine_list) if get_lang() == "zh" else ", ".join(mine_list),
        can_edit_mine=is_owner(),
    )

    # Newest price timestamp across visible rows (for compact status line).
    wl_updated_at = None
    for r in rows:
        ts = r.get("updated_at") or r.get("price_as_of")
        if ts and (wl_updated_at is None or str(ts) > str(wl_updated_at)):
            wl_updated_at = ts

    group_label = next((t["label"] for t in tabs if t["key"] == tab), tab)

    return render_template(
        "watchlist.html",
        sma_period=sma_period,
        tab=tab,
        tabs=tabs,
        rows=rows,
        temp_tickers=temp_tickers,
        max_temp=MAX_TEMP_TICKERS,
        mine_list=mine_list,
        mine_list_label="、".join(mine_list) if get_lang() == "zh" else ", ".join(mine_list),
        fund_cache_hits=fund_cache_hits,
        fund_cache_total=fund_cache_total,
        fund_cache_only=fund_cache_only,
        low_target_ms=low_target_ms,
        show_alert=(tab == "mine"),
        show_valuation=show_valuation,
        can_edit_mine=is_owner(),
        tab_desc=desc,
        wl_updated_at=wl_updated_at,
        group_label=group_label,
    )


@app.route("/watchlist/alert-price", methods=["POST"])
def watchlist_alert_price():
    """Inline save/clear for manual Alert Price (我的自选). Owner only."""
    if not is_owner():
        return jsonify({"ok": False, "error": "login required"}), 401
    data = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or request.form.get("ticker") or "").strip().upper()
    raw = data.get("alert_price", request.form.get("alert_price", ""))
    if isinstance(raw, str):
        raw = raw.strip().replace("$", "").replace(",", "")
    try:
        if raw is None or raw == "":
            price = None
        else:
            price = float(raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid alert_price"}), 400
    try:
        stored = upsert_alert_price(ticker, price)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    # Optional live status if client sent current price
    cur = data.get("price")
    try:
        cur_f = float(cur) if cur is not None and cur != "" else None
    except (TypeError, ValueError):
        cur_f = None
    st = alert_status(cur_f, stored)
    return jsonify(
        {
            "ok": True,
            "ticker": ticker,
            "alert_price": stored,
            "alert": st,
        }
    )


@app.route("/ai-trading", methods=["GET", "POST"])
def ai_trading():
    """
    Public AI Paper Trading (simulation only).
    Never connects to IBKR / never places real brokerage orders.
    Admin-only actions: create orders, priority, daily update, manual exit.
    """
    from paper_trading import (
        build_candidates,
        clear_priority,
        create_paper_orders_from_candidates,
        ensure_portfolio,
        history_report,
        list_candidates,
        list_closed_trades,
        list_open_trades,
        list_priority_tickers,
        manual_close_trade,
        portfolio_summary,
        run_daily_update,
        set_priority,
        trading_day_pt,
    )

    ensure_portfolio()
    tab = (request.args.get("tab") or request.form.get("tab") or "today").strip().lower()
    if tab not in ("today", "open", "history"):
        tab = "today"

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if not is_owner():
            flash(gettext("Please sign in to manage Paper Trading"), "warning")
            return redirect(url_for("owner_login", next=url_for("ai_trading", tab=tab)))
        try:
            if action == "refresh_candidates":
                rows = build_candidates(persist=True)
                flash(
                    ngettext_format(
                        "AI Candidates refreshed: {n} names for {day}",
                        n=len(rows),
                        day=trading_day_pt(),
                    ),
                    "ok",
                )
            elif action == "create_orders":
                result = create_paper_orders_from_candidates()
                flash(
                    ngettext_format(
                        "Paper orders created: {n} · skipped {s}",
                        n=len(result.get("created") or []),
                        s=len(result.get("skipped") or []),
                    ),
                    "ok",
                )
            elif action == "daily_update":
                result = run_daily_update(refresh_candidates=True)
                flash(
                    ngettext_format(
                        "Daily paper update done: closed {c}, marked {m}, candidates {n}",
                        c=len(result.get("closed") or []),
                        m=result.get("marked"),
                        n=result.get("candidates"),
                    ),
                    "ok",
                )
            elif action == "add_priority":
                raw = request.form.get("priority_tickers") or ""
                added = []
                for part in raw.replace(";", ",").split(","):
                    t = part.strip().upper()
                    if t and validate_ticker_format(t):
                        set_priority(t)
                        added.append(t)
                if not added:
                    raise ValueError(gettext("Enter valid tickers"))
                flash(
                    ngettext_format("Priority marked: {tickers}", tickers=", ".join(added)),
                    "ok",
                )
            elif action == "clear_priority":
                t = (request.form.get("ticker") or "").strip().upper()
                clear_priority(t)
                flash(ngettext_format("Priority cleared: {ticker}", ticker=t), "ok")
            elif action == "manual_exit":
                tid = int(request.form.get("trade_id") or 0)
                result = manual_close_trade(tid)
                flash(
                    ngettext_format(
                        "Manual exit: {ticker} · P&L {pnl}",
                        ticker=result.get("ticker"),
                        pnl=result.get("realized_pnl"),
                    ),
                    "ok",
                )
            else:
                flash(gettext("Unknown action"), "warning")
        except Exception as exc:
            flash(ngettext_format("Paper Trading action failed: {exc}", exc=exc), "warning")
        return redirect(url_for("ai_trading", tab=tab))

    candidates = list_candidates()
    if not candidates:
        try:
            candidates = build_candidates(persist=True)
        except Exception:
            candidates = []

    summary = portfolio_summary()
    opens = list_open_trades()
    history = list_closed_trades(limit=300)
    priority = list_priority_tickers()
    range_key = (request.args.get("range") or "ALL").strip().upper()
    hist_report = None
    if tab == "history":
        hist_report = history_report(range_key=range_key)

    return render_template(
        "ai_trading.html",
        tab=tab,
        summary=summary,
        candidates=candidates,
        opens=opens,
        history=history,
        hist_report=hist_report,
        range_key=range_key if tab == "history" else "ALL",
        priority=priority,
        can_manage=is_owner(),
        stop_pct=float(get_all_settings().get("paper_stop_loss_pct", 5.0)),
        take_pct=float(get_all_settings().get("paper_take_profit_pct", 10.0)),
    )


# ---------------------------------------------------------------------------
# Admin Order Requests + private Local Trading Agent API (V0, no IBKR)
# ---------------------------------------------------------------------------


def _require_private_agent_auth():
    """Return None if Bearer auth OK; otherwise (jsonify response, status)."""
    from trading_orders import verify_bearer_token

    if verify_bearer_token(request.headers.get("Authorization")):
        return None
    return jsonify({"error": "unauthorized"}), 401


@app.route("/admin/order-requests", methods=["GET", "POST"])
def admin_order_requests():
    """Admin-only UI to create/list Order Requests for the Local Agent."""
    from trading_orders import (
        api_key_configured,
        create_order_request,
        list_order_requests,
    )

    if not is_owner():
        flash(gettext("Please sign in to manage Order Requests"), "warning")
        return redirect(
            url_for("owner_login", next=url_for("admin_order_requests"))
        )

    prefill: dict = {}
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "create":
            try:
                payload = {
                    "symbol": request.form.get("symbol"),
                    "action": request.form.get("ord_action") or request.form.get("order_action"),
                    "quantity": request.form.get("quantity"),
                    "expected_price": request.form.get("expected_price"),
                    "allocation_amount": request.form.get("allocation_amount"),
                    "stop_price": request.form.get("stop_price"),
                    "take_profit_price": request.form.get("take_profit_price"),
                    "ai_score": request.form.get("ai_score"),
                    "mos_t": request.form.get("mos_t"),
                    "source": request.form.get("source"),
                    "mode": request.form.get("mode") or "PAPER",
                }
                created = create_order_request(payload)
                flash(
                    ngettext_format(
                        "Order Request #{id} created ({symbol}, PENDING)",
                        id=created["request_id"],
                        symbol=created["symbol"],
                    ),
                    "ok",
                )
                return redirect(url_for("admin_order_requests"))
            except ValueError as exc:
                flash(str(exc), "warning")
                prefill = {
                    "symbol": (request.form.get("symbol") or "").strip().upper(),
                    "action": (request.form.get("ord_action") or "BUY").strip().upper(),
                    "quantity": request.form.get("quantity") or "",
                    "expected_price": request.form.get("expected_price") or "",
                    "allocation_amount": request.form.get("allocation_amount") or "",
                    "stop_price": request.form.get("stop_price") or "",
                    "take_profit_price": request.form.get("take_profit_price") or "",
                    "ai_score": request.form.get("ai_score") or "",
                    "mos_t": request.form.get("mos_t") or "",
                    "source": request.form.get("source") or "",
                }

    orders = list_order_requests(limit=100)
    return render_template(
        "admin_order_requests.html",
        orders=orders,
        prefill=prefill,
        api_key_configured=api_key_configured(),
    )


@app.route("/api/trading/orders/pending", methods=["GET"])
def api_trading_orders_pending():
    """Private agent: list PENDING Paper Order Requests."""
    from trading_orders import list_pending_order_requests

    denied = _require_private_agent_auth()
    if denied is not None:
        return denied
    orders = list_pending_order_requests(limit=100)
    app.logger.info("trading API: listed %s pending order(s)", len(orders))
    return jsonify({"orders": orders})


@app.route("/api/trading/orders/<int:request_id>", methods=["GET"])
def api_trading_order_get(request_id: int):
    """Private agent: fetch one Order Request by id."""
    from trading_orders import get_order_request

    denied = _require_private_agent_auth()
    if denied is not None:
        return denied
    order = get_order_request(request_id)
    if not order:
        app.logger.info("trading API: order %s not found", request_id)
        return jsonify({"error": "not found"}), 404
    app.logger.info("trading API: read order %s status=%s", request_id, order.get("status"))
    return jsonify({"order": order})


@app.route("/api/trading/orders/<int:request_id>/status", methods=["POST"])
def api_trading_order_status(request_id: int):
    """Private agent: update processing status only (no create / no score edits)."""
    from trading_orders import update_order_status

    denied = _require_private_agent_auth()
    if denied is not None:
        return denied

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        app.logger.warning("trading API: malformed status body for id=%s", request_id)
        return jsonify({"error": "JSON body required"}), 400

    status = body.get("status")
    message = body.get("message")
    try:
        updated = update_order_status(request_id, status=status, message=message)
    except LookupError:
        return jsonify({"error": "not found"}), 404
    except ValueError as exc:
        app.logger.warning(
            "trading API: status update rejected id=%s: %s", request_id, exc
        )
        return jsonify({"error": str(exc)}), 400

    app.logger.info(
        "trading API: status update id=%s -> %s", request_id, updated.get("status")
    )
    return jsonify({"order": updated})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
