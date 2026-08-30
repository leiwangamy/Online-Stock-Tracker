import matplotlib
matplotlib.use("Agg")  # non-GUI backend for servers

import os
import uuid
import time
import glob
import re
import json
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
import yfinance as yf
import matplotlib.pyplot as plt
from werkzeug.security import check_password_hash, generate_password_hash

from db import (
    build_watchlist_alert,
    dashboard_meta,
    etf_universe_count,
    get_alert_prices,
    get_all_settings,
    get_conn,
    get_dashboard_by_tickers,
    get_setting,
    get_universe_flags,
    init_db,
    list_dashboard,
    list_etf_dashboard,
    list_setup,
    list_low_target_ratio,
    list_low_63d_pos,
    list_universe,
    search_market_tickers,
    set_setting,
    universe_count,
    upsert_alert_price,
)
from rising_now import list_rising_now, rising_count_label, rising_rule_summary
from multi_signal import build_multi_signal
from market_data import (
    compute_ai_score,
    compute_row_mos,
    compute_target_proxy_mos,
    fetch_metrics_for_ticker,
    fund_qualifies_for_news,
    get_fund_cached_only,
    get_news_cached_only,
    get_signals,
    is_data_quality_error,
    make_news_skipped,
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
    import logging as _logging

    _logging.getLogger("leibot").exception("Failed to start in-app scheduler")


# ---------------------------------------------------------------------------
# Owner auth (single operator). Public site hides Est / MOS / CLV.
# ---------------------------------------------------------------------------
SESSION_OWNER_KEY = "owner_auth"
SESSION_PENDING_MINE_KEY = "pending_mine_tickers"


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
        asset_kind="stocks",
    )


@app.route("/dashboard/etf")
def etf_dashboard():
    """LeiBot ETF Universe — market data only (not AI BUY)."""
    from etf_universe import ETF_FILTERS, ensure_etf_universe, etf_category_counts

    ensure_etf_universe()
    settings = get_all_settings()
    category = (request.args.get("cat") or "ALL").strip().upper()
    allowed = {k for k, _ in ETF_FILTERS}
    if category not in allowed:
        category = "ALL"
    q = (request.args.get("q") or "").strip()
    order = (request.args.get("order") or "dist_asc").strip()
    rows = list_etf_dashboard(
        category=None if category == "ALL" else category,
        q=q or None,
        order=order,
    )
    filters = [
        {
            "key": key,
            "label": key.replace("_", " ") if key != "ALL" else "ALL",
            "active": key == category,
        }
        for key, _ in ETF_FILTERS
    ]
    priced = sum(1 for r in rows if r.get("price") is not None)
    return render_template(
        "etf_dashboard.html",
        rows=rows,
        filters=filters,
        category=category,
        q=q,
        order=order,
        etf_count=etf_universe_count(),
        us_count=etf_universe_count(market="US"),
        canada_count=etf_universe_count(market="CANADA"),
        priced_count=priced,
        category_counts=etf_category_counts(),
        sma_period=int(settings.get("sma_period", 25)),
        asset_kind="etf",
    )


@app.route("/dashboard/etf/refresh", methods=["POST"])
def refresh_etf_dashboard():
    from market_data import refresh_etf_dashboard_cache

    cat = (request.form.get("cat") or "ALL").strip().upper()
    try:
        result = refresh_etf_dashboard_cache(max_workers=4)
        flash(
            ngettext_format(
                "ETF prices refreshed: ok {ok} / errors {errors} (universe {universe})",
                ok=result.get("ok", 0),
                errors=result.get("errors", 0),
                universe=result.get("universe", 0),
            ),
            "ok",
        )
        failed = result.get("failed") or []
        if failed:
            flash(
                ngettext_format(
                    "ETF download failed: {tickers}",
                    tickers=", ".join(failed[:12]) + ("…" if len(failed) > 12 else ""),
                ),
                "warning",
            )
    except Exception as exc:
        flash(ngettext_format("ETF price refresh failed: {exc}", exc=exc), "warning")
    return redirect(url_for("etf_dashboard", cat=cat if cat != "ALL" else None))


@app.route("/dashboard/etf/reseed", methods=["POST"])
def reseed_etf_universe():
    from etf_universe import ensure_etf_universe

    try:
        out = ensure_etf_universe(force_seed=True)
        flash(
            ngettext_format(
                "ETF universe seeded: {n} tickers",
                n=out.get("count", 0),
            ),
            "ok",
        )
    except Exception as exc:
        flash(ngettext_format("ETF universe seed failed: {exc}", exc=exc), "warning")
    return redirect(url_for("etf_dashboard"))


@app.route("/api/market/search")
def api_market_search():
    q = (request.args.get("q") or "").strip()
    hits = search_market_tickers(q, limit=25)
    return jsonify({"q": q, "results": hits})


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
    Admin-only.
    """
    nxt = (request.form.get("next") or "").strip()
    # Only allow internal relative redirects
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = url_for("market_dashboard")
    if not is_owner():
        flash(gettext("Please sign in to refresh all prices"), "warning")
        return redirect(url_for("owner_login", next=nxt))
    try:
        from update_jobs import job_refresh_prices

        result = job_refresh_prices(max_workers=2)
        flash(
            ngettext_format(
                "All pools refreshed: ok {ok} / errors {errors} (universe {universe}) · "
                "Watchlist ok {watchlist_ok} / errors {watchlist_errors} · "
                "Research Strong {strong} · Rising {rising}",
                ok=result.get("ok"),
                errors=result.get("errors"),
                universe=result.get("universe"),
                watchlist_ok=result.get("watchlist_ok"),
                watchlist_errors=result.get("watchlist_errors"),
                strong=result.get("strong_active", "—"),
                rising=result.get("rising_count", "—"),
            ),
            "ok",
        )
    except Exception as exc:
        flash(ngettext_format("All pools / Watchlist refresh failed: {exc}", exc=exc), "warning")
    return redirect(nxt)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Settings: public read-only; Admin (owner) can change."""
    can_edit = is_owner()
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
        if not can_edit:
            flash(gettext("Please sign in to change Settings"), "warning")
            return redirect(url_for("owner_login", next=url_for("settings")))
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
            # Checkbox: absent from POST when unchecked.
            set_setting(
                "paper_auto_replace_on_exit",
                "1" if request.form.get("paper_auto_replace_on_exit") else "0",
            )
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
        can_edit=can_edit,
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
        paper_auto_replace_on_exit=str(
            settings_data.get("paper_auto_replace_on_exit", "1")
        ).strip().lower()
        not in ("0", "false", "off", "no", ""),
        scheduler=sched,
    )


# Group ③ — long-term saved names (see watchlist_config; shared with update_jobs).
from watchlist_config import (
    MY_WATCHLIST,
    add_growth_watchlist_ticker,
    add_my_watchlist_ticker,
    add_short_watchlist_ticker,
    add_trade_candidate,
    collect_watchlist_tickers,
    get_growth_watchlist,
    get_my_watchlist,
    get_short_watchlist,
    get_trade_candidates,
    is_fund_like,
    remove_growth_watchlist_ticker,
    remove_my_watchlist_ticker,
    remove_short_watchlist_ticker,
    remove_trade_candidate,
    validate_ticker_token,
)

MAX_TEMP_TICKERS = 20
MAX_AUTO_ROWS = 30  # live Yahoo enrich cap for Oversold; all matches still listed
# Progressive fill per page load; full Watchlist is warmed by batch_watchlist_valuations.py
# using the same ensure_valuations / ensure_clvs engines (single source of truth).
VALUATION_MAX_NEW_PER_REQUEST = 8
CLV_MAX_NEW_PER_REQUEST = 8


def _queue_pending_mine_tickers(tickers: list[str]) -> list[str]:
    """Remember tickers across the login redirect. Returns cleaned list."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            cleaned.append(u)
    session[SESSION_PENDING_MINE_KEY] = cleaned
    return cleaned


def _consume_pending_mine_tickers() -> tuple[list[str], list[str]]:
    """Apply queued My Watchlist adds after sign-in. Returns (added, already_present)."""
    if not is_owner():
        return [], []
    pending = session.pop(SESSION_PENDING_MINE_KEY, None) or []
    if not isinstance(pending, list) or not pending:
        return [], []
    added: list[str] = []
    existed: list[str] = []
    for t in pending:
        u = (str(t) if t is not None else "").strip().upper()
        if not validate_ticker_token(u):
            continue
        cur = get_my_watchlist()
        if u in cur:
            existed.append(u)
            continue
        add_my_watchlist_ticker(u)
        added.append(u)
    if added:
        _force_refresh_mine_tickers(added)
    return added, existed


def _force_refresh_mine_tickers(tickers: list[str]) -> list[str]:
    """
    Live-fetch Yahoo metrics for newly added My Watchlist names and upsert
    into dashboard_cache so the next page load shows Price / SMA / AI immediately.
    """
    from db import save_dashboard_rows

    clean: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in seen and validate_ticker_format(u):
            seen.add(u)
            clean.append(u)
    if not clean:
        return []
    settings_data = get_all_settings()
    sma = int(settings_data.get("sma_period", 25))
    reb = int(settings_data.get("rebound_lookback", sma))
    flags = get_universe_flags(clean)
    refreshed: list[dict] = []
    ok: list[str] = []
    for t in clean:
        meta = flags.get(t, {})
        try:
            metrics = fetch_metrics_for_ticker(
                t, sma_period=sma, rebound_lookback=reb, meta=meta
            )
        except Exception:
            app.logger.exception("force refresh failed for %s", t)
            metrics = None
        if not metrics:
            continue
        merged = dict(metrics)
        merged.update(
            {k: meta.get(k) for k in ("in_sp500", "in_ndx100", "in_sp400", "in_sp600", "in_tsx")}
        )
        merged["price_source"] = "live_yahoo"
        refreshed.append(merged)
        ok.append(t)
    if refreshed:
        try:
            save_dashboard_rows(refreshed, replace_all=False)
        except Exception:
            app.logger.exception("save_dashboard_rows after mine add failed")
    # Warm fund/news caches so AI Score has Financial + News on first paint.
    try:
        from market_data import ensure_fund_cache, ensure_news_cache

        ensure_fund_cache(ok, max_workers=2, force=False)
        ensure_news_cache(ok, max_workers=2, force=False)
    except Exception:
        app.logger.exception("fund/news warm after mine add failed")
    return ok


def _flash_mine_add_result(added: list[str], existed: list[str], invalid: list[str]) -> None:
    parts: list[str] = []
    if added:
        parts.append(ngettext_format("Added: {tickers}", tickers=", ".join(added)))
    if existed:
        parts.append(
            ngettext_format("Already on list: {tickers}", tickers=", ".join(existed))
        )
    if invalid:
        parts.append(
            ngettext_format("Invalid: {tickers}", tickers=", ".join(invalid))
        )
    if parts:
        flash(" · ".join(parts), "ok" if added else "warning")
    else:
        flash(gettext("No tickers to add"), "warning")


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
    for _k in (
        "name",
        "price",
        "change_pct",
        "range_63d_pos",
        "target_1y",
        "sma",
        "dist_pct",
        "up_days_5",
        "return_5d_pct",
    ):
        row.setdefault(_k, None)
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
        if row and row.get("price") is not None and not is_data_quality_error(row):
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
                added, existed = _consume_pending_mine_tickers()
                if added or existed:
                    _flash_mine_add_result(added, existed, [])
                return redirect(nxt)
        else:
            stored = get_setting("owner_password_hash", "")
            if isinstance(stored, str) and check_password_hash(stored, pw):
                session[SESSION_OWNER_KEY] = True
                flash(gettext("Signed in"), "ok")
                added, existed = _consume_pending_mine_tickers()
                if added or existed:
                    _flash_mine_add_result(added, existed, [])
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
        elif action in (
            "add_mine",
            "remove_mine",
            "approve_to_mine",
            "add_growth",
            "remove_growth",
            "add_short",
            "remove_short",
        ):
            pool_tab = "mine"
            if action in ("add_growth", "remove_growth"):
                pool_tab = "growth"
            elif action in ("add_short", "remove_short"):
                pool_tab = "short"
            if not is_owner():
                raw_pending = (
                    request.form.get("mine_tickers", "")
                    or request.form.get("pool_tickers", "")
                    or request.form.get("ticker", "")
                )
                if action in ("add_mine", "approve_to_mine", "add_growth", "add_short"):
                    queued = _queue_pending_mine_tickers(parse_ticker_input(raw_pending))
                    if queued and action in ("add_mine", "approve_to_mine"):
                        flash(
                            ngettext_format(
                                "Sign in to save {n} ticker(s) to My Watchlist",
                                n=len(queued),
                            ),
                            "warning",
                        )
                    else:
                        flash(gettext("Please sign in to edit Watchlist pools"), "warning")
                else:
                    flash(gettext("Please sign in to edit Watchlist pools"), "warning")
                return redirect(
                    url_for("owner_login", next=url_for("watchlist", tab=pool_tab))
                )
            try:
                if action in ("add_mine", "approve_to_mine"):
                    raw = (
                        request.form.get("mine_tickers", "")
                        or request.form.get("ticker", "")
                    )
                    raw_parts = parse_ticker_input(raw)
                    if not raw_parts and (request.form.get("ticker") or "").strip():
                        raw_parts = [(request.form.get("ticker") or "").strip().upper()]
                    added: list[str] = []
                    existed: list[str] = []
                    invalid: list[str] = []
                    cur = set(get_my_watchlist())
                    for t in raw_parts:
                        t = (t or "").strip().upper()
                        if not validate_ticker_token(t):
                            invalid.append(t)
                            continue
                        if t in cur:
                            existed.append(t)
                            continue
                        add_my_watchlist_ticker(t)
                        cur.add(t)
                        added.append(t)
                    if added:
                        refreshed = _force_refresh_mine_tickers(added)
                        if refreshed:
                            flash(
                                ngettext_format(
                                    "Live data refreshed for: {tickers}",
                                    tickers=", ".join(refreshed),
                                ),
                                "ok",
                            )
                    _flash_mine_add_result(added, existed, invalid)
                elif action == "remove_mine":
                    remove_my_watchlist_ticker(request.form.get("ticker", ""))
                    flash(gettext("Removed from My Watchlist"), "ok")
                elif action == "add_growth":
                    raw = (
                        request.form.get("pool_tickers", "")
                        or request.form.get("mine_tickers", "")
                        or request.form.get("ticker", "")
                    )
                    added, existed, invalid = [], [], []
                    cur = set(get_growth_watchlist())
                    for t in parse_ticker_input(raw):
                        t = (t or "").strip().upper()
                        if not validate_ticker_token(t):
                            invalid.append(t)
                            continue
                        if t in cur:
                            existed.append(t)
                            continue
                        add_growth_watchlist_ticker(t)
                        cur.add(t)
                        added.append(t)
                    if added:
                        refreshed = _force_refresh_mine_tickers(added)
                        if refreshed:
                            flash(
                                ngettext_format(
                                    "Live data refreshed for: {tickers}",
                                    tickers=", ".join(refreshed),
                                ),
                                "ok",
                            )
                        flash(
                            ngettext_format(
                                "Added to GROWTH: {tickers}",
                                tickers=", ".join(added),
                            ),
                            "ok",
                        )
                    if existed:
                        flash(
                            ngettext_format(
                                "Already in GROWTH: {tickers}",
                                tickers=", ".join(existed),
                            ),
                            "warning",
                        )
                    if invalid:
                        flash(
                            ngettext_format(
                                "Invalid tickers: {tickers}",
                                tickers=", ".join(invalid),
                            ),
                            "warning",
                        )
                elif action == "remove_growth":
                    remove_growth_watchlist_ticker(request.form.get("ticker", ""))
                    flash(gettext("Removed from GROWTH"), "ok")
                elif action == "add_short":
                    raw = (
                        request.form.get("pool_tickers", "")
                        or request.form.get("mine_tickers", "")
                        or request.form.get("ticker", "")
                    )
                    added, existed, invalid = [], [], []
                    cur = set(get_short_watchlist())
                    for t in parse_ticker_input(raw):
                        t = (t or "").strip().upper()
                        if not validate_ticker_token(t):
                            invalid.append(t)
                            continue
                        if t in cur:
                            existed.append(t)
                            continue
                        add_short_watchlist_ticker(t)
                        cur.add(t)
                        added.append(t)
                    if added:
                        refreshed = _force_refresh_mine_tickers(added)
                        if refreshed:
                            flash(
                                ngettext_format(
                                    "Live data refreshed for: {tickers}",
                                    tickers=", ".join(refreshed),
                                ),
                                "ok",
                            )
                        flash(
                            ngettext_format(
                                "Added to SHORT: {tickers}",
                                tickers=", ".join(added),
                            ),
                            "ok",
                        )
                    if existed:
                        flash(
                            ngettext_format(
                                "Already in SHORT: {tickers}",
                                tickers=", ".join(existed),
                            ),
                            "warning",
                        )
                    if invalid:
                        flash(
                            ngettext_format(
                                "Invalid tickers: {tickers}",
                                tickers=", ".join(invalid),
                            ),
                            "warning",
                        )
                elif action == "remove_short":
                    remove_short_watchlist_ticker(request.form.get("ticker", ""))
                    flash(gettext("Removed from SHORT"), "ok")
            except ValueError as exc:
                flash(str(exc), "warning")
            # APPROVAL always lands on My Watchlist so the add is visible.
            if action == "approve_to_mine":
                next_tab = "mine"
            else:
                next_tab = (
                    request.form.get("next_tab") or request.args.get("tab") or pool_tab
                ).strip()
            if next_tab not in (
                "mine",
                "growth",
                "short",
                "ai_approved",
                "core_universe",
                "ndx100",
                "ai_discovery",
                "ai_news",
                "setup",
                "low_target",
                "low_63d",
                "temp",
            ):
                next_tab = pool_tab
            return redirect(url_for("watchlist", tab=next_tab))
        elif action in (
            "approve_ai",
            "reject_ai",
            "remove_ai_approved",
            "refresh_ai_select",
            "refresh_core_universe",
            "core_add",
            "core_keep",
            "core_remove",
            "core_ignore",
        ):
            # Handled below after imports / tab context.
            pass
        elif action.startswith("discovery_") or action in (
            "news_priority_toggle",
            "news_history_delete",
        ):
            handled = handle_ai_news_post()
            if handled is not None:
                return handled
            return redirect(url_for("watchlist", tab="ai_news"))
        else:
            return redirect(url_for("watchlist", tab=request.args.get("tab") or "temp"))

    settings_data = get_all_settings()
    sma_period = int(settings_data.get("sma_period", 25))
    temp_tickers = session.get("temp_watchlist", [])
    # Finish any My Watchlist adds queued before sign-in.
    if is_owner():
        added, existed = _consume_pending_mine_tickers()
        if added or existed:
            _flash_mine_add_result(added, existed, [])
    mine_list = get_my_watchlist()
    growth_list = get_growth_watchlist()
    short_list = get_short_watchlist()
    show_valuation = is_owner()

    tab = request.args.get("tab", "mine")
    # Legacy bookmarks: oversold / pullback → research screen (kept for features)
    if tab in ("oversold", "pullback"):
        tab = "setup"
    # Rising Now / Multi-Signal live under Research
    if tab in ("rising_now", "multi_signal"):
        return redirect(url_for("strong_stock_monitor", tab=tab))
    if tab not in (
        "mine",
        "growth",
        "short",
        "ai_approved",
        "ai_discovery",
        "ai_news",
        "core_universe",
        "ndx100",
        "ai_select",  # legacy alias → redirect below
        "setup",
        "low_target",
        "low_63d",
        "temp",
    ):
        tab = "mine"
    if tab == "ai_select":
        return redirect(url_for("watchlist", tab="core_universe"))

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

    from ai_select import (
        approve_ticker,
        list_ai_approved_rows,
        list_ai_approved_tickers,
        membership_flags,
        reject_ticker,
        remove_ai_approved,
    )
    from core_universe import load_latest_run, run_core_universe_filter

    core_run = None
    core_view = (request.args.get("core_view") or "qualified").strip().lower()
    if core_view not in ("qualified", "newly", "no_longer", "all"):
        core_view = "qualified"

    # Owner Core Universe / AI Approved actions
    if request.method == "POST" and is_owner():
        action = (request.form.get("action") or "").strip()
        if action in (
            "approve_ai",
            "reject_ai",
            "remove_ai_approved",
            "refresh_core_universe",
            "core_add",
            "core_keep",
            "core_remove",
            "core_ignore",
            "refresh_ai_select",
        ):
            try:
                if action in ("refresh_core_universe", "refresh_ai_select"):
                    built = run_core_universe_filter(persist=True)
                    flash(
                        ngettext_format(
                            "Core Universe Filter: {n} qualified (raw {raw})",
                            n=built.get("qualified_count", 0),
                            raw=built.get("raw_count", 0),
                        ),
                        "ok",
                    )
                    return redirect(url_for("watchlist", tab="core_universe"))
                tkr = (request.form.get("ticker") or "").strip().upper()
                if action in ("approve_ai", "core_add") and tkr:
                    src = (request.form.get("approve_source") or "CORE_UNIVERSE").strip().upper()
                    if src not in ("CORE_UNIVERSE", "AI_DISCOVERY", "MANUAL"):
                        src = "CORE_UNIVERSE"
                    approve_ticker(tkr, source=src)
                    flash(ngettext_format("Added {ticker} → AI APPROVED / Core Watch", ticker=tkr), "ok")
                    next_tab = (request.form.get("next_tab") or "").strip()
                    if next_tab == "ai_discovery":
                        return redirect(url_for("watchlist", tab="ai_discovery"))
                    return redirect(
                        url_for(
                            "watchlist",
                            tab="core_universe",
                            core_view=request.args.get("core_view") or "qualified",
                        )
                    )
                if action == "reject_ai" and tkr:
                    reject_ticker(tkr)
                    flash(ngettext_format("Rejected {ticker}", ticker=tkr), "ok")
                    return redirect(url_for("watchlist", tab="core_universe"))
                if action in ("remove_ai_approved", "core_remove") and tkr:
                    remove_ai_approved(tkr)
                    flash(ngettext_format("Removed {ticker} from AI APPROVED", ticker=tkr), "ok")
                    return redirect(url_for("watchlist", tab="ai_approved"))
                if action == "core_keep" and tkr:
                    flash(
                        ngettext_format(
                            "Keeping {ticker} in Core Watch (Owner override despite filter fail)",
                            ticker=tkr,
                        ),
                        "ok",
                    )
                    return redirect(url_for("watchlist", tab="core_universe", core_view="no_longer"))
                if action == "core_ignore" and tkr:
                    flash(ngettext_format("Ignored newly qualified {ticker}", ticker=tkr), "ok")
                    return redirect(url_for("watchlist", tab="core_universe", core_view="newly"))
            except Exception as exc:
                flash(str(exc), "warning")
                return redirect(url_for("watchlist", tab=tab))

    approved_list = list_ai_approved_tickers()
    ndx100_list = [
        (r.get("ticker") or "").upper()
        for r in list_universe(group="ndx100")
        if r.get("ticker")
    ]
    try:
        core_run = load_latest_run(qualified_only=False)
    except Exception:
        app.logger.exception("load core universe failed")
        core_run = None

    # Live-fetch groups only build rows for the active tab (they hit Yahoo).
    rows = []
    skip_heavy = False  # 新闻 / DCF / CLV / AI（及 live 财报抓取）
    fund_cache_only = False
    if tab == "setup":
        rows = setup
    elif tab == "low_target":
        rows = low_target
        skip_heavy = True
        fund_cache_only = True
    elif tab == "low_63d":
        rows = low_63d
        skip_heavy = True
        fund_cache_only = True
    elif tab == "mine":
        rows = _rows_for_tickers(mine_list)
    elif tab == "growth":
        rows = _rows_for_tickers(growth_list)
    elif tab == "short":
        rows = _rows_for_tickers(short_list)
    elif tab == "ai_approved":
        rows = _rows_for_tickers(approved_list)
        by_ap = {r["ticker"]: r for r in list_ai_approved_rows()}
        for r in rows:
            ap = by_ap.get((r.get("ticker") or "").upper()) or {}
            r["core_score"] = ap.get("core_score")
            r["ai_sources"] = ap.get("sources_json")
            r["review_flag"] = bool(ap.get("review_flag"))
    elif tab == "ndx100":
        # Independent Nasdaq-100 observation pool (parallel to My / AI Approved).
        # Prefer dashboard cache for ~100 names; live-fill gaps like My Watchlist.
        rows = _rows_for_tickers(ndx100_list)
        skip_heavy = True
        fund_cache_only = True
    elif tab == "core_universe":
        # Dedicated Core Universe UI — skip heavy wl_table fetch
        rows = []
        skip_heavy = True
        fund_cache_only = True
    elif tab == "ai_news":
        # AI News pool UI (ex-AI Trading Discovery) — dedicated panel, no wl_table
        rows = []
        skip_heavy = True
        fund_cache_only = True
    elif tab == "ai_discovery":
        try:
            from ai_discovery import list_discovery_candidates

            disc = list_discovery_candidates(
                limit=150, recent_only=True, exclude_negative=False, history_mode=False
            )
        except Exception:
            disc = []
        rows = _rows_for_tickers(
            list({(d.get("ticker") or "").upper() for d in disc if d.get("ticker")})
        )
        by_d = {}
        for d in disc:
            t = (d.get("ticker") or "").upper()
            if t and t not in by_d:
                by_d[t] = d
        for r in rows:
            d = by_d.get((r.get("ticker") or "").upper()) or {}
            r["discovery_status"] = d.get("status")
            r["discovery_event"] = d.get("event_summary") or d.get("event_category")
    elif tab == "temp":
        rows = _rows_for_tickers(temp_tickers)

    # Membership badges on all watchlist rows
    try:
        flags = membership_flags([r.get("ticker") for r in rows if r.get("ticker")])
        for r in rows:
            fl = flags.get((r.get("ticker") or "").upper()) or {}
            r["in_my_watchlist"] = bool(fl.get("in_my_watchlist"))
            r["ai_approved"] = bool(fl.get("ai_approved"))
    except Exception:
        for r in rows:
            r.setdefault("in_my_watchlist", False)
            r.setdefault("ai_approved", False)

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
            # Below gate → SKIPPED (no API); not a buy condition.
            if fund_qualifies_for_news(f):
                r["news"] = news_map.get((r.get("ticker") or "").upper())
            else:
                r["news"] = make_news_skipped()
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
        # My Watchlist: always fetch Financial + News for resolvable names (regular holdings).
        # GROWTH/SHORT: funds/ETFs skip Financial + News; equities behave like My Watchlist.
        live_rows = rows[:MAX_AUTO_ROWS] if tab == "setup" else rows
        overflow_rows = rows[MAX_AUTO_ROWS:] if tab == "setup" else []

        if tab in ("growth", "short"):
            for r in live_rows:
                if r.get("not_found"):
                    continue
                if is_fund_like(r.get("ticker") or "", r):
                    r["_skip_fund_news"] = True
                    r["fund"] = None
                    r["news"] = make_news_skipped(
                        reason="fund/ETF — Financial & News not loaded"
                    )
            live_tickers = [
                r["ticker"]
                for r in live_rows
                if r.get("ticker")
                and not r.get("not_found")
                and not r.get("_skip_fund_news")
            ]
        else:
            live_tickers = [
                r["ticker"]
                for r in live_rows
                if r.get("ticker") and not r.get("not_found")
            ]
        signals = get_signals(
            live_tickers,
            force_news=(tab in ("mine", "growth", "short")),
        )
        tickers_shown = list(live_tickers)
        # Est / MOS / CLV only for logged-in owner (methods still under development).
        # Skip valuation for funds on GROWTH/SHORT.
        if show_valuation:
            val_tickers = tickers_shown
            try:
                from valuation_engine import ensure_valuations

                iv_results = ensure_valuations(
                    val_tickers, force=False, max_new=VALUATION_MAX_NEW_PER_REQUEST
                )
            except Exception:
                iv_results = {}
            try:
                from clv_engine import ensure_clvs

                clv_results = ensure_clvs(
                    val_tickers, force=False, max_new=CLV_MAX_NEW_PER_REQUEST
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
                    r["news"] = make_news_skipped()

    for r in rows:
        if skip_heavy:
            continue
        if r.get("_skip_fund_news"):
            # Funds on GROWTH/SHORT: keep skipped news; no DCF/CLV/AI invent from empty fund.
            r["est_value"] = None
            r["bear_value"] = None
            r["bull_value"] = None
            r["mos_pct"] = None
            r["est_tooltip"] = "Fund/ETF — valuation skipped"
            r["clv"] = None
            r["clv_pct_price"] = None
            r["clv_tooltip"] = "Fund/ETF — CLV skipped"
            r["dcf_below_clv"] = False
            if not r.get("not_found"):
                r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))
                r["ai"] = compute_ai_score(r)
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

    # Knife Risk (independent of AI Score) — downside velocity + relative weakness.
    try:
        from knife_risk import attach_knife_risk

        attach_knife_risk(rows, ensure_bench=True)
    except Exception:
        app.logger.exception("knife risk attach failed")
        for r in rows:
            r["knife"] = None

    # My Watchlist SMA alerts (research zones only — never auto-buy).
    # Manual override from DB; Default/Deep/Active computed from current SMA.
    alert_map = get_alert_prices([r.get("ticker") for r in rows if r.get("ticker")])
    for r in rows:
        t = (r.get("ticker") or "").upper()
        bundle = build_watchlist_alert(r.get("price"), r.get("sma"), alert_map.get(t))
        r["manual_alert"] = bundle["manual_alert"]
        r["default_alert"] = bundle["default_alert"]
        r["deep_alert"] = bundle["deep_alert"]
        r["active_alert"] = bundle["active_alert"]
        r["alert_source"] = bundle["alert_source"]
        r["alert"] = bundle["alert"]
        r["alert_price"] = bundle["manual_alert"]  # legacy alias for Manual
        # Ensure MOS T on cache-only tabs (AI skipped there) and any rows missed above.
        if r.get("mos_t") is None and not r.get("not_found"):
            r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))

    tabs = [
        {"key": "mine", "label": "★ " + gettext("My Watchlist"), "count": len(mine_list), "group": "main", "row": 1},
        {"key": "ndx100", "label": "📗 " + gettext("Nasdaq-100"), "count": len(ndx100_list), "group": "main", "row": 1},
        {
            "key": "ai_news",
            "label": "📰 " + gettext("AI News"),
            "count": 0,
            "group": "ai_select",
            "row": 1,
        },
        {
            "key": "ai_discovery",
            "label": "🔭 " + gettext("AI Discovery"),
            "count": 0,
            "group": "ai_select",
            "row": 1,
        },
        {
            "key": "core_universe",
            "label": "📐 " + gettext("Core Universe"),
            "count": int((core_run or {}).get("qualified_count") or 0),
            "group": "ai_select",
            "row": 1,
        },
        {
            "key": "ai_approved",
            "label": "🤖 " + gettext("AI Approved"),
            "count": len(approved_list),
            "group": "ai_select",
            "row": 1,
        },
        {"key": "setup", "label": "🔻 " + gettext("Oversold Pullback"), "count": len(setup), "group": "screens", "row": 2},
        {"key": "growth", "label": "📈 " + gettext("Growth"), "count": len(growth_list), "group": "screens", "row": 2},
        {"key": "short", "label": "📉 " + gettext("Short"), "count": len(short_list), "group": "screens", "row": 2},
        {"key": "low_target", "label": "🎯 " + gettext("Target Ratio < 80%"), "count": len(low_target), "group": "screens", "row": 2},
        {"key": "low_63d", "label": "📉 " + gettext("63D Position < 25%"), "count": len(low_63d), "group": "screens", "row": 2},
        {"key": "temp", "label": "🕒 " + gettext("Temp"), "count": len(temp_tickers), "group": "scratch", "row": 2},
    ]
    # AI News + Discovery badge counts
    try:
        from ai_discovery import discovery_pool_counts, get_min_event_score_display, list_discovery_candidates

        _ms = get_min_event_score_display()
        tabs[2]["count"] = int(
            (discovery_pool_counts(min_event_score=_ms) or {}).get("qualifying_events") or 0
        )
    except Exception:
        tabs[2]["count"] = 0
    if tab == "ai_discovery":
        tabs[3]["count"] = len(rows)
    else:
        try:
            from ai_discovery import list_discovery_candidates

            tabs[3]["count"] = len(
                list_discovery_candidates(
                    limit=150, recent_only=True, exclude_negative=False, history_mode=False
                )
            )
        except Exception:
            tabs[3]["count"] = 0

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

    # Core Universe change lists for Owner actions.
    # Display / ADD focus: qualified ∩ NOT (My Watchlist ∪ Nasdaq-100).
    core_newly = []
    core_no_longer = []
    core_still = []
    if core_run:
        try:
            from core_universe import filter_focus_qualified, observation_exclude_tickers

            exclude = observation_exclude_tickers()
            all_rows = core_run.get("rows") or []
            focus_rows = filter_focus_qualified(all_rows, exclude=exclude)
            qset_all = {
                (r.get("ticker") or "").upper()
                for r in all_rows
                if r.get("qualified")
            }
            qset_focus = {(r.get("ticker") or "").upper() for r in focus_rows}
            pool = set(approved_list)
            core_newly = sorted(qset_focus - pool)
            core_still = sorted(qset_focus & pool)
            # Leave AI Approved when numeric filter fails (not merely because in NDX/Mine).
            core_no_longer = sorted(pool - qset_all)
            by_t = {(r.get("ticker") or "").upper(): r for r in all_rows}
            core_run["rows"] = all_rows
            core_run["focus_rows"] = focus_rows
            core_run["qualified_count"] = len(focus_rows)
            core_run["qualified_count_all"] = len(qset_all)
            core_run["excluded_overlap"] = len(qset_all - qset_focus)
            core_run["newly_qualified"] = [
                by_t.get(t) or {"ticker": t, "qualified": True} for t in core_newly
            ]
            core_run["no_longer_qualified"] = [
                by_t.get(t) or {"ticker": t, "qualified": False} for t in core_no_longer
            ]
            core_run["still_qualified"] = core_still
            # Attach latest dashboard prices for Core Universe table display.
            try:
                from db import get_dashboard_by_tickers

                price_tickers = sorted(
                    {
                        (r.get("ticker") or "").upper()
                        for r in (focus_rows + core_run["newly_qualified"] + core_run["no_longer_qualified"])
                        if r.get("ticker")
                    }
                )
                dash_px = get_dashboard_by_tickers(price_tickers) if price_tickers else {}
                for r in focus_rows + core_run["newly_qualified"] + core_run["no_longer_qualified"]:
                    t = (r.get("ticker") or "").upper()
                    d = dash_px.get(t) or {}
                    if d.get("price") is not None:
                        r["price"] = d.get("price")
                    if d.get("change_pct") is not None:
                        r["change_pct"] = d.get("change_pct")
            except Exception:
                app.logger.exception("core universe price attach failed")
            # Tab badge uses focus count
            for t in tabs:
                if t.get("key") == "core_universe":
                    t["count"] = len(focus_rows)
                    break
        except Exception:
            app.logger.exception("core universe diff failed")

    ai_news_ctx = load_ai_news_context() if tab == "ai_news" else {}
    if tab == "ai_news" and ai_news_ctx.get("discovery_count") is not None:
        for t in tabs:
            if t.get("key") == "ai_news":
                t["count"] = int(ai_news_ctx.get("discovery_count") or 0)
                break

    return render_template(
        "watchlist.html",
        sma_period=sma_period,
        tab=tab,
        tabs=tabs,
        rows=rows,
        temp_tickers=temp_tickers,
        max_temp=MAX_TEMP_TICKERS,
        mine_list=mine_list,
        growth_list=growth_list,
        short_list=short_list,
        mine_list_label="、".join(mine_list) if get_lang() == "zh" else ", ".join(mine_list),
        fund_cache_hits=fund_cache_hits,
        fund_cache_total=fund_cache_total,
        fund_cache_only=fund_cache_only,
        low_target_ms=low_target_ms,
        show_alert=(tab in ("mine", "ndx100", "ai_approved")),
        show_valuation=show_valuation,
        can_edit_mine=is_owner(),
        can_edit_pool=is_owner(),
        can_edit_alert=(is_owner() and tab in ("mine", "ndx100", "ai_approved")),
        show_ai_select_actions=(tab == "ai_discovery" and is_owner()),
        show_ai_approved_actions=(tab == "ai_approved" and is_owner()),
        architecture_v2=True,
        tab_desc=desc,
        wl_updated_at=wl_updated_at,
        group_label=group_label,
        core_run=core_run,
        core_view=core_view,
        core_newly=core_newly,
        core_still=core_still,
        core_no_longer=core_no_longer,
        approved_list=approved_list,
        can_manage=is_owner(),
        **ai_news_ctx,
    )


@app.route("/watchlist/alert-price", methods=["POST"])
def watchlist_alert_price():
    """Save / clear / reset Manual Alert (我的自选). Owner only. Never places orders."""
    if not is_owner():
        return jsonify({"ok": False, "error": "login required"}), 401
    data = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or request.form.get("ticker") or "").strip().upper()
    reset = bool(data.get("reset") or request.form.get("reset"))
    raw = data.get("alert_price", request.form.get("alert_price", ""))
    if isinstance(raw, str):
        raw = raw.strip().replace("$", "").replace(",", "")
    try:
        if reset or raw is None or raw == "":
            price = None
        else:
            price = float(raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid alert_price"}), 400
    try:
        stored = upsert_alert_price(ticker, price)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    def _f(key: str) -> float | None:
        v = data.get(key)
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    bundle = build_watchlist_alert(_f("price"), _f("sma"), stored)
    return jsonify(
        {
            "ok": True,
            "ticker": ticker,
            "manual_alert": bundle["manual_alert"],
            "default_alert": bundle["default_alert"],
            "deep_alert": bundle["deep_alert"],
            "active_alert": bundle["active_alert"],
            "alert_source": bundle["alert_source"],
            "alert_price": bundle["manual_alert"],
            "alert": bundle["alert"],
        }
    )


@app.route("/candidate-analysis", methods=["GET", "POST"])
def candidate_analysis():
    """Backward-compatible URL → Research Financial 6/6 analysis."""
    return redirect(url_for("strong_stock_monitor", tab="fin6"))


@app.route("/ai-trading/levels", methods=["POST"])
def ai_trading_levels():
    """
    Admin-only: set / reset Stop Loss or Take Profit, or edit open-position shares.
    - Candidates (Watchlist): ticker + field stop|take
    - Open positions: trade_id + field stop|take|shares
    Public may view; only owner can edit. Does not bypass Knife Risk gates.
    """
    if not is_owner():
        return jsonify({"ok": False, "error": "login required"}), 401
    from paper_trading import (
        _cfg,
        apply_level_override_to_row,
        stop_take_prices,
        trading_day_pt,
        update_open_trade_levels,
        update_open_trade_shares,
        upsert_level_override,
        validate_long_levels,
    )

    data = request.get_json(silent=True) or {}
    field = (data.get("field") or "").strip().lower()
    reset = bool(data.get("reset"))
    if field not in ("stop", "take", "shares"):
        return jsonify({"ok": False, "error": "field=stop|take|shares required"}), 400

    raw = data.get("value")
    if isinstance(raw, str):
        raw = raw.strip().replace("$", "").replace(",", "")

    # ── Open position path ──────────────────────────────────────────────
    trade_id_raw = data.get("trade_id")
    if trade_id_raw not in (None, ""):
        try:
            trade_id = int(trade_id_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid trade_id"}), 400

        if field == "shares":
            try:
                if raw is None or raw == "":
                    raise ValueError("shares required")
                row = update_open_trade_shares(trade_id, float(raw))
            except (TypeError, ValueError) as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            return jsonify(
                {
                    "ok": True,
                    "scope": "trade",
                    "field": "shares",
                    "trade_id": trade_id,
                    "ticker": row.get("ticker"),
                    "shares": row.get("shares"),
                    "cost": row.get("cost"),
                    "market_value": row.get("market_value"),
                    "unrealized_pnl": row.get("unrealized_pnl"),
                    "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
                    "cash_delta": row.get("cash_delta"),
                    "cash_after": row.get("cash_after"),
                }
            )

        try:
            if reset or raw is None or raw == "":
                row = update_open_trade_levels(
                    trade_id,
                    reset_stop=(field == "stop"),
                    reset_take=(field == "take"),
                )
            else:
                val = float(raw)
                if val <= 0:
                    raise ValueError("price must be > 0")
                if field == "stop":
                    row = update_open_trade_levels(trade_id, manual_stop=val)
                else:
                    row = update_open_trade_levels(trade_id, manual_take=val)
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        warn = None
        try:
            warn = validate_long_levels(
                float(row["entry_price"]),
                float(row["stop_price"]),
                float(row["take_profit_price"]),
            )
        except (TypeError, ValueError, KeyError):
            warn = None
        return jsonify(
            {
                "ok": True,
                "scope": "trade",
                "trade_id": trade_id,
                "ticker": row.get("ticker"),
                "price": row.get("entry_price"),
                "default_stop": row.get("default_stop"),
                "default_take": row.get("default_take"),
                "stop_price": row.get("stop_price"),
                "take_profit_price": row.get("take_profit_price"),
                "stop_source": row.get("stop_source"),
                "take_source": row.get("take_source"),
                "stop_risk_pct": row.get("stop_risk_pct"),
                "reward_pct": row.get("reward_pct"),
                "rr_ratio": row.get("rr_ratio"),
                "levels_valid": row.get("levels_valid"),
                "warning": warn,
            }
        )

    if field == "shares":
        return jsonify({"ok": False, "error": "shares requires trade_id"}), 400

    # ── Candidate Watchlist path ────────────────────────────────────────
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker or trade_id required"}), 400

    try:
        if reset or raw is None or raw == "":
            ov = upsert_level_override(
                ticker,
                reset_stop=(field == "stop"),
                reset_take=(field == "take"),
            )
        else:
            val = float(raw)
            if val <= 0:
                raise ValueError("price must be > 0")
            if field == "stop":
                ov = upsert_level_override(ticker, manual_stop=val)
            else:
                ov = upsert_level_override(ticker, manual_take=val)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    def _f(key: str) -> float | None:
        v = data.get(key)
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    price = _f("price")
    cfg = _cfg()
    stop_pct = float(data.get("stop_pct") or cfg["stop_loss_pct"])
    take_pct = float(data.get("take_profit_pct") or cfg["take_profit_pct"])
    if price is not None and price > 0:
        d_stop, d_take = stop_take_prices(price, stop_pct, take_pct)
    else:
        d_stop, d_take = _f("default_stop"), _f("default_take")

    row = {
        "ticker": ticker,
        "price": price,
        "stop_price": d_stop,
        "take_profit_price": d_take,
    }
    apply_level_override_to_row(row, ov, default_stop=d_stop, default_take=d_take)

    try:
        day = get_setting("paper_candidates_as_of") or trading_day_pt()
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT meta_json FROM paper_candidates WHERE as_of_date = ? AND ticker = ?",
                (day, ticker),
            ).fetchone()
            if cur:
                try:
                    meta = json.loads(cur["meta_json"] or "{}")
                except Exception:
                    meta = {}
                meta["default_stop"] = row.get("default_stop")
                meta["default_take"] = row.get("default_take")
                meta["manual_stop"] = row.get("manual_stop")
                meta["manual_take"] = row.get("manual_take")
                meta["stop_source"] = row.get("stop_source")
                meta["take_source"] = row.get("take_source")
                conn.execute(
                    """
                    UPDATE paper_candidates
                    SET stop_price = ?, take_profit_price = ?, meta_json = ?, updated_at = ?
                    WHERE as_of_date = ? AND ticker = ?
                    """,
                    (
                        row.get("stop_price"),
                        row.get("take_profit_price"),
                        json.dumps(meta),
                        datetime.now(timezone.utc).isoformat(),
                        day,
                        ticker,
                    ),
                )
    except Exception:
        app.logger.exception("sync candidate levels failed")

    warn = None
    if (
        price is not None
        and row.get("stop_price") is not None
        and row.get("take_profit_price") is not None
    ):
        warn = validate_long_levels(
            float(price), float(row["stop_price"]), float(row["take_profit_price"])
        )

    return jsonify(
        {
            "ok": True,
            "scope": "candidate",
            "ticker": ticker,
            "price": price,
            "default_stop": row.get("default_stop"),
            "default_take": row.get("default_take"),
            "manual_stop": row.get("manual_stop"),
            "manual_take": row.get("manual_take"),
            "stop_price": row.get("stop_price"),
            "take_profit_price": row.get("take_profit_price"),
            "stop_source": row.get("stop_source"),
            "take_source": row.get("take_source"),
            "stop_risk_pct": row.get("stop_risk_pct"),
            "reward_pct": row.get("reward_pct"),
            "rr_ratio": row.get("rr_ratio"),
            "levels_valid": row.get("levels_valid"),
            "warning": warn,
        }
    )


def _normalize_discovery_channel_stats(stats: dict) -> dict:
    """Fill display gaps for older harvest payloads (pre Broad / admitted_today)."""
    if not isinstance(stats, dict) or not stats:
        return {}
    out = dict(stats)
    if "admitted_today" not in out and "admitted" in out:
        out["admitted_today"] = out.get("admitted")
    cc = dict(out.get("channel_counts") or {})
    # Official-only runs omitted broad; show 0 so radar is not blank.
    if cc and "broad" not in cc:
        cc["broad"] = 0
    for key in ("usaspending", "dod", "sec", "fda", "gov_transactions"):
        if key not in cc and cc:
            cc[key] = 0
    out["channel_counts"] = cc
    return out


def _ai_news_layer() -> str:
    layer = (request.form.get("layer") or request.args.get("layer") or "official").strip().lower()
    return layer if layer in ("broad", "official") else "official"


def _ai_news_redirect(*, news_hist: bool = False, fragment: str | None = None):
    kw = {"tab": "ai_news", "layer": _ai_news_layer()}
    if news_hist or request.args.get("news_hist"):
        kw["news_hist"] = 1
    url = url_for("watchlist", **kw)
    if fragment:
        url += fragment
    return redirect(url)


def load_ai_news_context() -> dict:
    """Shared AI News (ex-AI Trading Discovery) template payload."""
    discovery_rows = []
    discovery_broad_rows = []
    discovery_official_rows = []
    discovery_perf = None
    discovery_unresolved = []
    discovery_unresolved_count = 0
    discovery_resolved_today = 0
    discovery_min_score = 70.0
    discovery_channel_stats = {}
    discovery_count = 0
    news_history_rows = []
    news_history_count = 0
    discovery_layer = _ai_news_layer()
    try:
        from ai_discovery import (
            count_news_history,
            count_resolved_unresolved_today,
            count_unresolved_discoveries,
            discovery_performance,
            discovery_pool_counts,
            get_min_event_score_display,
            list_discovery_candidates,
            list_unresolved_discoveries,
            maybe_retry_unresolved,
            partition_discovery_by_layer,
        )
        from db import get_setting as _gs

        discovery_min_score = get_min_event_score_display()
        pool0 = discovery_pool_counts(min_event_score=discovery_min_score)
        discovery_count = int(pool0.get("qualifying_events") or 0)
        news_history_count = count_news_history(min_event_score=discovery_min_score)
        discovery_channel_stats = _normalize_discovery_channel_stats(
            _gs("ai_discovery_last_channel_stats", {}) or {}
        )
        try:
            maybe_retry_unresolved(force=False)
        except Exception:
            app.logger.exception("unresolved retry failed")
        discovery_rows = list_discovery_candidates(limit=300, exclude_negative=False)
        parts = partition_discovery_by_layer(discovery_rows)
        discovery_broad_rows = parts.get("broad") or []
        discovery_official_rows = parts.get("official") or []
        discovery_count = len(discovery_rows)
        discovery_perf = discovery_performance()
        discovery_unresolved = list_unresolved_discoveries(limit=80)
        discovery_unresolved_count = count_unresolved_discoveries()
        discovery_resolved_today = count_resolved_unresolved_today()
        discovery_channel_stats = _normalize_discovery_channel_stats(
            _gs("ai_discovery_last_channel_stats", {}) or {}
        )
        news_history_rows = list_discovery_candidates(
            limit=500,
            recent_only=False,
            exclude_negative=False,
            history_mode=True,
        )
        news_history_count = len(news_history_rows)
    except Exception:
        app.logger.exception("AI News load failed")
    news_history_priority_rows = [
        r for r in news_history_rows if int(r.get("is_news_priority") or 0)
    ]
    news_history_archive_rows = [
        r for r in news_history_rows if not int(r.get("is_news_priority") or 0)
    ]
    return {
        "discovery_rows": discovery_rows,
        "discovery_broad_rows": discovery_broad_rows,
        "discovery_official_rows": discovery_official_rows,
        "discovery_perf": discovery_perf,
        "discovery_unresolved": discovery_unresolved,
        "discovery_unresolved_count": discovery_unresolved_count,
        "discovery_resolved_today": discovery_resolved_today,
        "discovery_min_score": discovery_min_score,
        "discovery_channel_stats": discovery_channel_stats,
        "discovery_count": discovery_count,
        "discovery_layer": discovery_layer,
        "news_history_rows": news_history_rows,
        "news_history_count": news_history_count,
        "news_history_priority_rows": news_history_priority_rows,
        "news_history_archive_rows": news_history_archive_rows,
    }


def handle_ai_news_post():
    """Owner AI News / Discovery POSTs. Returns a redirect response or None."""
    action = (request.form.get("action") or "").strip()
    if not action.startswith("discovery_") and action not in (
        "news_priority_toggle",
        "news_history_delete",
    ):
        return None
    if not is_owner():
        flash(gettext("Please sign in to manage Paper Trading"), "warning")
        return redirect(url_for("owner_login", next=url_for("watchlist", tab="ai_news")))
    try:
        if action == "discovery_set_min_score":
            from ai_discovery import (
                discovery_pool_counts,
                set_min_event_score_display,
            )

            raw = (request.form.get("min_event_score") or "70").strip()
            try:
                score = float(raw)
            except ValueError:
                score = 70.0
            score = set_min_event_score_display(score)
            pool = discovery_pool_counts(min_event_score=score)
            flash(
                ngettext_format(
                    "Min Event Score {score} · Qualifying Events {e} · Unique Stocks {s}",
                    score=int(score) if score == int(score) else score,
                    e=pool.get("qualifying_events"),
                    s=pool.get("unique_stocks"),
                ),
                "ok",
            )
            return _ai_news_redirect()
        if action == "discovery_run":
            from ai_discovery import run_discovery_cycle

            result = run_discovery_cycle(create_orders=False)
            h = result.get("harvest") or {}
            cc = h.get("channel_counts") or {}
            flash(
                ngettext_format(
                    "Discovery: Broad {b} · USA {u} · DoD {d} · SEC {s} · FDA {f} · Gov {g} · raw {r} · today {t} · unresolved {x}",
                    b=cc.get("broad"),
                    u=cc.get("usaspending"),
                    d=cc.get("dod"),
                    s=cc.get("sec"),
                    f=cc.get("fda"),
                    g=cc.get("gov_transactions"),
                    r=h.get("raw_total"),
                    t=h.get("admitted_today"),
                    x=h.get("unresolved"),
                ),
                "ok",
            )
            return _ai_news_redirect()
        if action == "discovery_add_event":
            from ai_discovery import add_manual_discovery_event

            ticker = (request.form.get("ticker") or "").strip().upper()
            summary = (request.form.get("event_summary") or "").strip()
            result = add_manual_discovery_event(ticker=ticker, summary=summary)
            flash(
                ngettext_format(
                    "Discovery event added: {ticker} · Event Score {score}",
                    ticker=ticker,
                    score=(result.get("event") or {}).get("event_score"),
                ),
                "ok",
            )
            return _ai_news_redirect()
        if action == "discovery_analyze":
            from ai_discovery import analyze_discovery_candidate

            cid = int(request.form.get("candidate_id") or 0)
            row = analyze_discovery_candidate(cid)
            flash(
                ngettext_format(
                    "Analyzed {ticker}: {status}",
                    ticker=row.get("ticker"),
                    status=row.get("status"),
                ),
                "ok",
            )
            return _ai_news_redirect(fragment=f"#disc-row-{cid}")
        if action == "news_priority_toggle":
            from ai_discovery import set_news_priority

            cid = int(request.form.get("candidate_id") or 0)
            on = (request.form.get("on") or "1") == "1"
            row = set_news_priority(cid, on=on)
            flash(
                ngettext_format(
                    "News Priority {state}: {ticker}",
                    state=gettext("on") if on else gettext("off"),
                    ticker=row.get("ticker"),
                ),
                "ok",
            )
            return _ai_news_redirect(fragment=f"#disc-row-{cid}")
        if action == "news_history_delete":
            from ai_discovery import delete_news_history_candidate

            cid = int(request.form.get("candidate_id") or 0)
            row = delete_news_history_candidate(cid)
            if row.get("mode") == "blocked_retain":
                flash(
                    ngettext_format(
                        "News History keeps items for {days} full days — delete is disabled until then ({ticker}, day {age}).",
                        days=row.get("retain_days") or 7,
                        ticker=row.get("ticker"),
                        age=row.get("news_age_days") or 0,
                    ),
                    "warning",
                )
            else:
                flash(
                    ngettext_format(
                        "Removed from News History: {ticker}",
                        ticker=row.get("ticker"),
                    ),
                    "ok",
                )
            return _ai_news_redirect(news_hist=True, fragment="#news-history-dock")
        if action == "discovery_create_orders":
            from ai_discovery import create_discovery_paper_orders

            result = create_discovery_paper_orders(auto_only=True)
            flash(
                ngettext_format(
                    "Discovery paper orders: created {n} · skipped {s}",
                    n=result.get("count"),
                    s=len(result.get("skipped") or []),
                ),
                "ok",
            )
            return _ai_news_redirect()
    except Exception as exc:
        flash(ngettext_format("Paper Trading action failed: {exc}", exc=exc), "warning")
        return _ai_news_redirect()
    return None


@app.route("/ai-trading/export.xlsx", methods=["GET"])
def ai_trading_export_xlsx():
    """Admin: download AI Trading experiment snapshot (.xlsx). Does not modify data."""
    if not is_owner():
        flash(gettext("Please sign in to manage Paper Trading"), "warning")
        return redirect(
            url_for("owner_login", next=url_for("ai_trading_export_xlsx"))
        )
    try:
        from ai_trading_export import build_ai_trading_workbook

        data = build_ai_trading_workbook()
    except Exception as exc:
        app.logger.exception("AI Trading Excel export failed")
        flash(
            ngettext_format("Excel export failed: {exc}", exc=exc),
            "warning",
        )
        return redirect(url_for("ai_trading", tab="today"))
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stamp = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y%m%d_%H%M")
    fname = f"AI_Trading_Data_{stamp}.xlsx"
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


@app.route("/ai-trading", methods=["GET", "POST"])
def ai_trading():
    """
    Public AI Paper Trading (simulation only).
    Never connects to IBKR / never places real brokerage orders.
    Admin-only actions: create orders, priority, daily update, manual exit.
    """
    from paper_trading import (
        _ai_auto_thresholds,
        all_strategies_dashboard,
        build_candidates,
        clear_priority,
        create_paper_orders_from_ai_buy,
        create_paper_orders_from_deep_recovery,
        create_paper_orders_from_stable_growth,
        create_paper_orders_from_safe_margin,
        create_paper_orders_from_short_sell,
        create_paper_orders_from_candidates,
        ensure_portfolio,
        ensure_strategy_accounts,
        get_strategy_account,
        history_report,
        list_candidates,
        list_closed_trades,
        list_open_trades,
        list_priority_tickers,
        list_rebuy_candidates,
        manual_buy_candidate,
        manual_close_trade,
        maybe_auto_refresh_ai_trading,
        auto_buy_on_refresh,
        auto_replace_stable_growth_exits,
        auto_replace_safe_margin_exits,
        auto_replace_short_sell_exits,
        portfolio_summary,
        portfolio_summary_for_strategy,
        rebuy_from_closed_trade,
        run_daily_update,
        set_priority,
        strategy_portfolio_summary,
        trading_day_pt,
    )
    from knife_risk import KNIFE_AUTO_BLOCK_THRESHOLD
    from strategies import (
        STRATEGY_ALERT_BUY,
        STRATEGY_DEEP_RECOVERY,
        STRATEGY_SAFE_MARGIN,
        STRATEGY_SHORT_SELL,
        STRATEGY_STABLE_GROWTH,
        STRATEGY_IDS,
        STRATEGY_META,
        normalize_strategy_id,
        strategy_label,
    )

    ensure_portfolio()
    ensure_strategy_accounts()
    tab = (request.args.get("tab") or request.form.get("tab") or "overview").strip().lower()
    if tab in ("today", "legacy"):
        tab = "buy"
    if tab in ("alert", "alert_buy", "ai_buy"):
        tab = "buy"
    if tab == "news_history":
        return redirect(
            url_for("watchlist", tab="ai_news", news_hist=1) + "#news-history-dock"
        )
    if tab == "discovery":
        # AI Discovery pool moved to Watchlist → AI News
        layer = (request.args.get("layer") or "official").strip().lower()
        if layer not in ("broad", "official"):
            layer = "official"
        kw = {"tab": "ai_news", "layer": layer}
        if request.args.get("news_hist"):
            kw["news_hist"] = 1
        frag = ""
        if request.args.get("news_hist"):
            frag = "#news-history-dock"
        return redirect(url_for("watchlist", **kw) + frag)
    _strategy_tabs = {
        "deep": "deep_recovery",
        "deep_recovery": "deep_recovery",
        "stable": "stable_growth",
        "stable_growth": "stable_growth",
        "safe": "safe_margin",
        "safe_margin": "safe_margin",
        "short": "short_sell",
        "short_sell": "short_sell",
    }
    if tab in _strategy_tabs:
        tab = _strategy_tabs[tab]
    if tab not in (
        "overview",
        "buy",
        "deep_recovery",
        "stable_growth",
        "safe_margin",
        "short_sell",
        "open",
        "history",
        "select",
    ):
        tab = "overview"

    # Map UI tab → strategy_id for shell / filtered views
    _tab_strategy = {
        "buy": STRATEGY_ALERT_BUY,
        "deep_recovery": STRATEGY_DEEP_RECOVERY,
        "stable_growth": STRATEGY_STABLE_GROWTH,
        "safe_margin": STRATEGY_SAFE_MARGIN,
        "short_sell": STRATEGY_SHORT_SELL,
    }
    active_strategy_id = _tab_strategy.get(tab)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if not is_owner():
            flash(gettext("Please sign in to manage Paper Trading"), "warning")
            return redirect(url_for("owner_login", next=url_for("ai_trading", tab=tab)))
        try:
            if action == "refresh_ai_buy":
                from ai_buy import build_ai_buy_snapshot

                built = build_ai_buy_snapshot(persist=True)
                fill = auto_buy_on_refresh()
                created = fill.get("created") or []
                flash(
                    ngettext_format(
                        "AI BUY refreshed: Alert-marked {n} (pool {p}) · READY {r}",
                        n=built.get("universe_count", 0),
                        p=built.get("pool_count", 0),
                        r=(built.get("counts") or {}).get("READY", 0),
                    ),
                    "ok",
                )
                if created:
                    flash(
                        ngettext_format(
                            "Auto-buy on refresh: opened {n} · cash left {cash}",
                            n=len(created),
                            cash=f"{float(fill.get('cash') or 0):.2f}",
                        ),
                        "ok",
                    )
                elif fill.get("no_funds"):
                    flash(
                        gettext("Auto-buy skipped — no fund / trading-limit room"),
                        "warning",
                    )
                return redirect(url_for("ai_trading", tab="buy"))
            if action == "refresh_deep_recovery":
                from deep_recovery import build_deep_recovery_snapshot

                built = build_deep_recovery_snapshot(persist=True)
                flash(
                    ngettext_format(
                        "Deep Recovery refreshed: top {n} of Oversold pool {p} · READY {r}",
                        n=built.get("universe_count", 0),
                        p=built.get("pool_count", 0),
                        r=(built.get("counts") or {}).get("READY", 0),
                    ),
                    "ok",
                )
                return redirect(url_for("ai_trading", tab="deep_recovery"))
            if action == "refresh_stable_growth":
                from stable_growth import build_stable_growth_snapshot

                built = build_stable_growth_snapshot(persist=True)
                flash(
                    ngettext_format(
                        "Stable Growth refreshed: Dist ASC top {n} of GROWTH pool {p} · READY {r}",
                        n=built.get("universe_count", 0),
                        p=built.get("pool_count", 0),
                        r=(built.get("counts") or {}).get("READY", 0),
                    ),
                    "ok",
                )
                return redirect(url_for("ai_trading", tab="stable_growth"))
            if action == "refresh_safe_margin":
                from safe_margin import build_safe_margin_snapshot

                built = build_safe_margin_snapshot(persist=True)
                flash(
                    ngettext_format(
                        "Safe Margin refreshed: Target ASC top {n} "
                        "(pool {p}, risk-passed {passed}) · READY {r}",
                        n=built.get("universe_count", 0),
                        p=built.get("pool_count", 0),
                        passed=built.get("passed_count", 0),
                        r=(built.get("counts") or {}).get("READY", 0),
                    ),
                    "ok",
                )
                return redirect(url_for("ai_trading", tab="safe_margin"))
            if action == "refresh_short_sell":
                from short_sell import build_short_sell_snapshot

                built = build_short_sell_snapshot(persist=True)
                flash(
                    ngettext_format(
                        "Short Sell refreshed: Dist DESC top {n} "
                        "(pool {p}, candidates {passed}) · READY {r}",
                        n=built.get("universe_count", 0),
                        p=built.get("pool_count", 0),
                        passed=built.get("passed_count", 0),
                        r=(built.get("counts") or {}).get("READY", 0),
                    ),
                    "ok",
                )
                return redirect(url_for("ai_trading", tab="short_sell"))
            if action == "check_data":
                from db import get_dashboard_by_tickers
                from market_data_validator import (
                    format_data_check_text,
                    validate_buy_data,
                )

                tkr = (request.form.get("ticker") or "").strip().upper()
                if not tkr or not validate_ticker_format(tkr):
                    raise ValueError(gettext("Enter a valid ticker"))
                live = (request.form.get("live") or "").strip() in ("1", "true", "on")
                row = get_dashboard_by_tickers([tkr]).get(tkr) or {"ticker": tkr}
                report = validate_buy_data(
                    tkr, row, require_live_history=live
                )
                session["mdv_last_report"] = format_data_check_text(report)
                session["mdv_last_ticker"] = tkr
                if report.get("data_block"):
                    flash(
                        ngettext_format(
                            "DATA CHECK {ticker}: {status} — BUY BLOCKED",
                            ticker=tkr,
                            status=report.get("data_quality_status"),
                        ),
                        "warning",
                    )
                else:
                    flash(
                        ngettext_format(
                            "DATA CHECK {ticker}: {status}",
                            ticker=tkr,
                            status=report.get("data_quality_status"),
                        ),
                        "ok",
                    )
                return redirect(url_for("ai_trading", tab="buy", data_check=tkr))
            if action == "validate_market_data_batch":
                from ai_buy import buy_observation_tickers
                from db import get_dashboard_by_tickers
                from market_data_validator import validate_rows_batch

                tickers = buy_observation_tickers()
                dash = get_dashboard_by_tickers(tickers) if tickers else {}
                rows = [dash.get(t) or {"ticker": t} for t in tickers]
                batch = validate_rows_batch(rows)
                session["mdv_batch_report"] = batch
                c = batch.get("counts") or {}
                flash(
                    ngettext_format(
                        "Market Data Validation: checked {n} · PASS {p} · WARN {w} · ERROR {e} · INSUFF {i} · STALE {s}",
                        n=batch.get("checked", 0),
                        p=c.get("PASS", 0),
                        w=c.get("WARNING", 0),
                        e=c.get("ERROR", 0),
                        i=c.get("INSUFFICIENT_DATA", 0),
                        s=c.get("STALE_DATA", 0),
                    ),
                    "ok" if not c.get("ERROR") else "warning",
                )
                return redirect(url_for("ai_trading", tab="buy"))
            if action == "refresh_ai_select":
                from core_universe import run_core_universe_filter

                built = run_core_universe_filter(persist=True)
                flash(
                    ngettext_format(
                        "Core Universe Filter: {n} qualified (raw {raw})",
                        n=built.get("qualified_count", 0),
                        raw=built.get("raw_count", 0),
                    ),
                    "ok",
                )
                return redirect(url_for("watchlist", tab="core_universe"))
            if action == "refresh_candidates":
                rows = build_candidates(persist=True)
                flash(
                    ngettext_format(
                        "Legacy AI Candidates refreshed: {n} names for {day} (deprecated Top-10 path)",
                        n=len(rows),
                        day=trading_day_pt(),
                    ),
                    "warning",
                )
            elif action == "create_orders":
                # AI BUY: READY top→bottom ladder (paper only).
                result = create_paper_orders_from_ai_buy()
                created = result.get("created") or []
                skipped = result.get("skipped") or []
                if created:
                    flash(
                        ngettext_format(
                            "Paper orders created: {n} · skipped {s}",
                            n=len(created),
                            s=len(skipped),
                        ),
                        "ok",
                    )
                else:
                    flash(
                        ngettext_format(
                            "No paper orders created · skipped {s}. Need READY/STABILIZING (STALE ok; DATA ERROR blocked).",
                            s=len(skipped),
                        ),
                        "warning",
                    )

                def _skip_flash(reason: str, msg_key: str) -> None:
                    rows = [s for s in skipped if s.get("reason") == reason]
                    if not rows:
                        return
                    detail = "; ".join(
                        (
                            f"{s.get('ticker')}"
                            + (f" ({s.get('detail')})" if s.get("detail") else "")
                        )
                        for s in rows[:8]
                    )
                    if len(rows) > 8:
                        detail += f" …(+{len(rows) - 8})"
                    flash(
                        ngettext_format(msg_key, detail=detail, n=len(rows)),
                        "warning",
                    )

                _skip_flash(
                    "insufficient_cash",
                    "Blocked — insufficient cash (cannot open): {detail}",
                )
                _skip_flash(
                    "trading_limit",
                    "Blocked — trading limit reached (cannot open): {detail}",
                )
                _skip_flash(
                    "already_open",
                    "Blocked — already have an open position: {detail}",
                )
                _skip_flash(
                    "invalid_levels",
                    "Blocked invalid Stop/Take (LONG requires Stop < Entry < Take): {detail}",
                )
                _skip_flash(
                    "no_allocation",
                    "Skipped — no suggested allocation ($0): {detail}",
                )
                if created and tab == "buy":
                    return redirect(url_for("ai_trading", tab="open"))
            elif action == "create_deep_recovery_orders":
                result = create_paper_orders_from_deep_recovery()
                created = result.get("created") or []
                skipped = result.get("skipped") or []
                if created:
                    flash(
                        ngettext_format(
                            "Deep Recovery paper orders: {n} · skipped {s}",
                            n=len(created),
                            s=len(skipped),
                        ),
                        "ok",
                    )
                else:
                    flash(
                        ngettext_format(
                            "No Deep Recovery orders · skipped {s}. Need READY/STABILIZING on Oversold top-N.",
                            s=len(skipped),
                        ),
                        "warning",
                    )
                return redirect(url_for("ai_trading", tab="deep_recovery"))
            elif action == "create_stable_growth_orders":
                result = create_paper_orders_from_stable_growth()
                created = result.get("created") or []
                skipped = result.get("skipped") or []
                if created:
                    flash(
                        ngettext_format(
                            "Stable Growth paper orders: {n} · skipped {s} · Stop −3% · no Take",
                            n=len(created),
                            s=len(skipped),
                        ),
                        "ok",
                    )
                else:
                    flash(
                        ngettext_format(
                            "No Stable Growth orders · skipped {s}. Need READY on GROWTH Dist queue.",
                            s=len(skipped),
                        ),
                        "warning",
                    )
                return redirect(url_for("ai_trading", tab="stable_growth"))
            elif action == "create_safe_margin_orders":
                result = create_paper_orders_from_safe_margin()
                created = result.get("created") or []
                skipped = result.get("skipped") or []
                if created:
                    flash(
                        ngettext_format(
                            "Safe Margin paper orders: {n} · skipped {s} · "
                            "10% trailing stop · no Take",
                            n=len(created),
                            s=len(skipped),
                        ),
                        "ok",
                    )
                else:
                    flash(
                        ngettext_format(
                            "No Safe Margin orders · skipped {s}. "
                            "Need READY on Target risk-filtered queue.",
                            s=len(skipped),
                        ),
                        "warning",
                    )
                return redirect(url_for("ai_trading", tab="safe_margin"))
            elif action == "create_short_sell_orders":
                result = create_paper_orders_from_short_sell()
                created = result.get("created") or []
                skipped = result.get("skipped") or []
                if created:
                    flash(
                        ngettext_format(
                            "Short Sell paper orders: {n} · skipped {s} · "
                            "SELL SHORT · 5% trailing cover · no Take",
                            n=len(created),
                            s=len(skipped),
                        ),
                        "ok",
                    )
                else:
                    flash(
                        ngettext_format(
                            "No Short Sell orders · skipped {s}. "
                            "Need READY on SHORT Dist DESC queue "
                            "(63D>80% · Day%<0).",
                            s=len(skipped),
                        ),
                        "warning",
                    )
                return redirect(url_for("ai_trading", tab="short_sell"))
            elif action == "daily_update":
                result = run_daily_update(refresh_candidates=True)
                auto_n = len(result.get("auto_created") or [])
                flash(
                    ngettext_format(
                        "Daily paper update done: closed {c}, marked {m}, candidates {n}, auto-bought {a}",
                        c=len(result.get("closed") or []),
                        m=result.get("marked"),
                        n=result.get("candidates"),
                        a=auto_n,
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
                    ngettext_format("Priority Buy marked: {tickers}", tickers=", ".join(added)),
                    "ok",
                )
            elif action == "clear_priority":
                t = (request.form.get("ticker") or "").strip().upper()
                clear_priority(t)
                flash(ngettext_format("Priority Buy cleared: {ticker}", ticker=t), "ok")
            elif action == "manual_buy":
                ticker = (request.form.get("ticker") or "").strip().upper()
                amount_raw = (request.form.get("amount") or "").strip()
                shares_raw = (request.form.get("shares") or "").strip()
                amount = float(amount_raw) if amount_raw else None
                shares = float(shares_raw) if shares_raw else None
                result = manual_buy_candidate(ticker, amount=amount, shares=shares)
                if result.get("mode") == "add":
                    flash(
                        ngettext_format(
                            "Added to position: {ticker} +{shares} sh @ {price} · cost +{cost}",
                            ticker=result.get("ticker"),
                            shares=result.get("shares_added"),
                            price=result.get("fill_price"),
                            cost=result.get("cost_added"),
                        ),
                        "ok",
                    )
                else:
                    flash(
                        ngettext_format(
                            "Manual buy opened: {ticker} · {shares} sh @ {price} · cost {cost}",
                            ticker=result.get("ticker"),
                            shares=result.get("shares"),
                            price=result.get("entry_price"),
                            cost=result.get("cost"),
                        ),
                        "ok",
                    )
                return redirect(url_for("ai_trading", tab="open"))
            elif action == "manual_exit":
                tid = int(request.form.get("trade_id") or 0)
                # Capture strategy before close for book-scoped auto-replace.
                pre = next(
                    (t for t in list_open_trades() if int(t.get("id") or 0) == tid),
                    None,
                )
                result = manual_close_trade(tid)
                flash(
                    ngettext_format(
                        "Manual exit: {ticker} · P&L {pnl}",
                        ticker=result.get("ticker"),
                        pnl=result.get("realized_pnl"),
                    ),
                    "ok",
                )
                try:
                    from strategies import (
                        STRATEGY_SAFE_MARGIN,
                        STRATEGY_SHORT_SELL,
                        STRATEGY_STABLE_GROWTH,
                        normalize_strategy_id,
                    )

                    sid = normalize_strategy_id(
                        (pre or {}).get("strategy_id") or result.get("strategy_id")
                    )
                    if sid == STRATEGY_STABLE_GROWTH:
                        rep = auto_replace_stable_growth_exits(max_new=1)
                        got = rep.get("created") or []
                        if got:
                            flash(
                                ngettext_format(
                                    "Stable Growth auto-buy after EXIT: {ticker}",
                                    ticker=got[0].get("ticker"),
                                ),
                                "ok",
                            )
                    elif sid == STRATEGY_SAFE_MARGIN:
                        rep = auto_replace_safe_margin_exits(max_new=1)
                        got = rep.get("created") or []
                        if got:
                            flash(
                                ngettext_format(
                                    "Safe Margin auto-buy after EXIT: {ticker}",
                                    ticker=got[0].get("ticker"),
                                ),
                                "ok",
                            )
                    elif sid == STRATEGY_SHORT_SELL:
                        rep = auto_replace_short_sell_exits(max_new=1)
                        got = rep.get("created") or []
                        if got:
                            flash(
                                ngettext_format(
                                    "Short Sell auto-short after EXIT: {ticker}",
                                    ticker=got[0].get("ticker"),
                                ),
                                "ok",
                            )
                except Exception:
                    app.logger.exception("strategy auto-replace after manual exit")
                flash(
                    ngettext_format(
                        "Re-entry available: use Re-enter for {ticker} under Add / Re-entry.",
                        ticker=result.get("ticker"),
                    ),
                    "ok",
                )
                return redirect(
                    url_for(
                        "ai_trading",
                        tab="open",
                        rebuy=result.get("id"),
                    )
                )
            elif action == "rebuy":
                tid = int(request.form.get("trade_id") or 0)
                result = rebuy_from_closed_trade(tid)
                flash(
                    ngettext_format(
                        "Re-entry opened: {ticker} · {shares} sh @ {price} · cost {cost}",
                        ticker=result.get("ticker"),
                        shares=result.get("shares"),
                        price=result.get("entry_price"),
                        cost=result.get("cost"),
                    ),
                    "ok",
                )
                return redirect(url_for("ai_trading", tab="open"))
            elif action == "discovery_set_min_score":
                from ai_discovery import (
                    discovery_pool_counts,
                    discovery_threshold_counts,
                    set_min_event_score_display,
                )

                raw = (request.form.get("min_event_score") or "70").strip()
                try:
                    score = float(raw)
                except ValueError:
                    score = 70.0
                score = set_min_event_score_display(score)
                pool = discovery_pool_counts(min_event_score=score)
                flash(
                    ngettext_format(
                        "Min Event Score {score} · Qualifying Events {e} · Unique Stocks {s}",
                        score=int(score) if score == int(score) else score,
                        e=pool.get("qualifying_events"),
                        s=pool.get("unique_stocks"),
                    ),
                    "ok",
                )
                return redirect(url_for("watchlist", tab="ai_news"))
            elif action == "discovery_run":
                from ai_discovery import run_discovery_cycle

                # Discovery harvest/analyze only — never auto-create paper orders from this UI.
                result = run_discovery_cycle(create_orders=False)
                h = result.get("harvest") or {}
                cc = h.get("channel_counts") or {}
                flash(
                    ngettext_format(
                        "Discovery: Broad {b} · USA {u} · DoD {d} · SEC {s} · FDA {f} · Gov {g} · raw {r} · today {t} · unresolved {x}",
                        b=cc.get("broad"),
                        u=cc.get("usaspending"),
                        d=cc.get("dod"),
                        s=cc.get("sec"),
                        f=cc.get("fda"),
                        g=cc.get("gov_transactions"),
                        r=h.get("raw_total"),
                        t=h.get("admitted_today"),
                        x=h.get("unresolved"),
                    ),
                    "ok",
                )
                return redirect(url_for("watchlist", tab="ai_news"))
            elif action == "discovery_add_event":
                from ai_discovery import add_manual_discovery_event

                ticker = (request.form.get("ticker") or "").strip().upper()
                summary = (request.form.get("event_summary") or "").strip()
                result = add_manual_discovery_event(ticker=ticker, summary=summary)
                flash(
                    ngettext_format(
                        "Discovery event added: {ticker} · Event Score {score}",
                        ticker=ticker,
                        score=(result.get("event") or {}).get("event_score"),
                    ),
                    "ok",
                )
                return redirect(url_for("watchlist", tab="ai_news"))
            elif action == "discovery_analyze":
                from ai_discovery import analyze_discovery_candidate

                cid = int(request.form.get("candidate_id") or 0)
                row = analyze_discovery_candidate(cid)
                flash(
                    ngettext_format(
                        "Analyzed {ticker}: {status}",
                        ticker=row.get("ticker"),
                        status=row.get("status"),
                    ),
                    "ok",
                )
                _layer = (
                    request.form.get("layer") or request.args.get("layer") or "official"
                ).strip().lower()
                if _layer not in ("broad", "official"):
                    _layer = "official"
                return redirect(
                    url_for("watchlist", tab="ai_news", layer=_layer)
                    + f"#disc-row-{cid}"
                )
            elif action == "news_priority_toggle":
                from ai_discovery import set_news_priority

                cid = int(request.form.get("candidate_id") or 0)
                on = (request.form.get("on") or "1") == "1"
                row = set_news_priority(cid, on=on)
                flash(
                    ngettext_format(
                        "News Priority {state}: {ticker}",
                        state=gettext("on") if on else gettext("off"),
                        ticker=row.get("ticker"),
                    ),
                    "ok",
                )
                layer = (request.form.get("layer") or request.args.get("layer") or "official").strip().lower()
                if layer not in ("broad", "official"):
                    layer = "official"
                return redirect(
                    url_for("watchlist", tab="ai_news", layer=layer)
                    + f"#disc-row-{cid}"
                )
            elif action == "news_history_delete":
                from ai_discovery import delete_news_history_candidate

                cid = int(request.form.get("candidate_id") or 0)
                row = delete_news_history_candidate(cid)
                if row.get("mode") == "blocked_retain":
                    flash(
                        ngettext_format(
                            "News History keeps items for {days} full days — delete is disabled until then ({ticker}, day {age}).",
                            days=row.get("retain_days") or 7,
                            ticker=row.get("ticker"),
                            age=row.get("news_age_days") or 0,
                        ),
                        "warning",
                    )
                else:
                    flash(
                        ngettext_format(
                            "Removed from News History: {ticker}",
                            ticker=row.get("ticker"),
                        ),
                        "ok",
                    )
                layer = (request.form.get("layer") or "official").strip().lower()
                if layer not in ("broad", "official"):
                    layer = "official"
                return redirect(
                    url_for(
                        "watchlist",
                        tab="ai_news",
                        layer=layer,
                        news_hist=1,
                    )
                    + "#news-history-dock"
                )
            elif action == "reset_ai_trading":
                from ai_trading_export import reset_ai_trading

                result = reset_ai_trading(archive_first=True)
                flash(
                    ngettext_format(
                        "AI Trading reset: trades {t} · priority {p} · cash restored ${c:.2f}. Discovery / Saved News kept.",
                        t=result.get("trades_deleted"),
                        p=result.get("priority_cleared"),
                        c=float(result.get("cash_restored") or 0),
                    ),
                    "ok",
                )
                return redirect(url_for("ai_trading", tab="today"))
            elif action == "discovery_create_orders":
                from ai_discovery import create_discovery_paper_orders

                result = create_discovery_paper_orders(auto_only=True)
                flash(
                    ngettext_format(
                        "Discovery paper orders: created {n} · skipped {s}",
                        n=result.get("count"),
                        s=len(result.get("skipped") or []),
                    ),
                    "ok",
                )
                return redirect(url_for("watchlist", tab="ai_news"))
            else:
                flash(gettext("Unknown action"), "warning")
        except Exception as exc:
            flash(ngettext_format("Paper Trading action failed: {exc}", exc=exc), "warning")
        return redirect(url_for("ai_trading", tab=tab))

    # Auto mark open P&L / rebuild AI BUY when the trading day (or mark age) is stale.
    try:
        maybe_auto_refresh_ai_trading()
    except Exception:
        app.logger.exception("AI Trading auto-refresh failed")

    candidates = list_candidates()
    # Prefer AI BUY snapshot as primary view (new architecture).
    ai_buy_view: dict = {
        "as_of": "",
        "universe_count": 0,
        "counts": {},
        "rows": [],
    }
    try:
        from ai_buy import load_ai_buy_view

        # Rebuild each visit so Dist / HOLDING / READY stay current with prices.
        ai_buy_view = load_ai_buy_view(recompute=True)
    except Exception:
        app.logger.exception("AI BUY view failed")

    deep_recovery_view: dict = {
        "as_of": "",
        "universe_count": 0,
        "pool_count": 0,
        "top_n": 15,
        "counts": {},
        "rows": [],
    }
    if tab == "deep_recovery":
        try:
            from deep_recovery import load_deep_recovery_view

            deep_recovery_view = load_deep_recovery_view(recompute=True)
        except Exception:
            app.logger.exception("Deep Recovery view failed")

    stable_growth_view: dict = {
        "as_of": "",
        "universe_count": 0,
        "pool_count": 0,
        "top_n": 15,
        "counts": {},
        "rows": [],
        "stop_loss_pct": 3.0,
        "take_profit_pct": None,
    }
    if tab == "stable_growth":
        try:
            from stable_growth import load_stable_growth_view

            stable_growth_view = load_stable_growth_view(recompute=True)
        except Exception:
            app.logger.exception("Stable Growth view failed")

    safe_margin_view: dict = {
        "as_of": "",
        "universe_count": 0,
        "pool_count": 0,
        "passed_count": 0,
        "top_n": 15,
        "counts": {},
        "rows": [],
        "stop_loss_pct": 10.0,
        "trailing_stop": True,
        "take_profit_pct": None,
    }
    if tab == "safe_margin":
        try:
            from safe_margin import load_safe_margin_view

            safe_margin_view = load_safe_margin_view(recompute=True)
        except Exception:
            app.logger.exception("Safe Margin view failed")

    short_sell_view: dict = {
        "as_of": "",
        "universe_count": 0,
        "pool_count": 0,
        "passed_count": 0,
        "top_n": 15,
        "counts": {},
        "rows": [],
        "stop_loss_pct": 5.0,
        "trailing_stop": True,
        "take_profit_pct": None,
        "side": "short",
    }
    if tab == "short_sell":
        try:
            from short_sell import load_short_sell_view

            short_sell_view = load_short_sell_view(recompute=True)
        except Exception:
            app.logger.exception("Short Sell view failed")

    if not candidates and tab not in (
        "buy",
        "select",
        "deep_recovery",
        "stable_growth",
        "safe_margin",
        "short_sell",
    ):
        try:
            candidates = build_candidates(persist=True)
        except Exception:
            app.logger.exception("build_candidates failed on AI Trading page")
            candidates = []

    from candidate_analysis import enrich_ai_trading_watchlist_rows

    try:
        candidates = enrich_ai_trading_watchlist_rows(candidates)
    except Exception:
        app.logger.exception("enrich_ai_trading_watchlist_rows failed")

    trade_candidates = []

    summary = portfolio_summary()
    # Overview + shell pages: per-strategy accounts (layout first; calc later).
    strategy_dashboard = []
    strategy_shell = None
    try:
        strategy_dashboard = all_strategies_dashboard()
    except Exception:
        app.logger.exception("strategy dashboard failed")
    if active_strategy_id:
        try:
            strategy_shell = {
                "strategy_id": active_strategy_id,
                "label": strategy_label(active_strategy_id),
                "meta": STRATEGY_META.get(active_strategy_id) or {},
                "account": get_strategy_account(active_strategy_id),
                "summary": strategy_portfolio_summary(active_strategy_id),
            }
            # Alert Buy / strategy pages: KPI strip matches that book only.
            if tab == "buy":
                summary = portfolio_summary_for_strategy(STRATEGY_ALERT_BUY)
        except Exception:
            app.logger.exception("strategy shell load failed for %s", active_strategy_id)

    strategy_pipeline = None
    if active_strategy_id:
        try:
            from strategy_pools import strategy_source_pipeline

            strategy_pipeline = strategy_source_pipeline(active_strategy_id)
        except Exception:
            app.logger.exception("strategy pipeline header failed")
            strategy_pipeline = {
                "source": (STRATEGY_META.get(active_strategy_id) or {}).get(
                    "source_pool_label"
                ),
                "filter": "—",
                "rank": (STRATEGY_META.get(active_strategy_id) or {}).get(
                    "primary_metric_label"
                ),
                "block": "Data / News / Knife / strategy gates",
                "member_count": None,
            }

    # Open/history: strategy pages filter to that book; overview/open show all.
    if active_strategy_id and tab != "buy":
        opens = list_open_trades(strategy_id=active_strategy_id)
        history = list_closed_trades(strategy_id=active_strategy_id, limit=300)
    elif tab == "buy":
        opens = list_open_trades(strategy_id=STRATEGY_ALERT_BUY)
        history = list_closed_trades(strategy_id=STRATEGY_ALERT_BUY, limit=300)
    else:
        opens = list_open_trades()
        history = list_closed_trades(limit=300)
    try:
        open_count_all = len(list_open_trades())
    except Exception:
        open_count_all = len(opens)
    priority = list_priority_tickers()
    if is_owner():
        rebuy_pool = list_rebuy_candidates(top_n=8, lookback_trading_days=63)
    else:
        rebuy_pool = {
            "all": [],
            "top": [],
            "total": 0,
            "top_n": 8,
            "lookback_trading_days": 63,
            "cutoff_date": "",
        }
    rebuy_candidates = rebuy_pool.get("all") or []
    discovery_rows = []
    discovery_broad_rows = []
    discovery_official_rows = []
    discovery_perf = None
    discovery_unresolved = []
    discovery_unresolved_count = 0
    discovery_resolved_today = 0
    discovery_min_score = 70.0
    discovery_channel_stats = {}
    discovery_count = 0
    news_history_rows = []
    news_history_count = 0
    discovery_layer = (request.args.get("layer") or "official").strip().lower()
    if discovery_layer not in ("broad", "official"):
        discovery_layer = "official"
    # Always load badge count + last radar stats (so Today tab is not stuck at 0).
    try:
        from ai_discovery import (
            count_news_history,
            discovery_pool_counts,
            get_min_event_score_display,
        )
        from db import get_setting as _gs

        discovery_min_score = get_min_event_score_display()
        pool0 = discovery_pool_counts(min_event_score=discovery_min_score)
        discovery_count = int(pool0.get("qualifying_events") or 0)
        news_history_count = count_news_history(min_event_score=discovery_min_score)
        discovery_channel_stats = _gs("ai_discovery_last_channel_stats", {}) or {}
        if not isinstance(discovery_channel_stats, dict):
            discovery_channel_stats = {}
        discovery_channel_stats = _normalize_discovery_channel_stats(
            discovery_channel_stats
        )
    except Exception:
        app.logger.exception("AI Discovery badge/stats load failed")
        discovery_count = 0
        news_history_count = 0
        discovery_channel_stats = {}

    if tab == "discovery":
        try:
            from ai_discovery import (
                count_resolved_unresolved_today,
                count_unresolved_discoveries,
                discovery_performance,
                list_discovery_candidates,
                list_unresolved_discoveries,
                maybe_retry_unresolved,
                partition_discovery_by_layer,
            )

            # Background ticker re-resolution (throttled); never guesses low-confidence matches.
            try:
                maybe_retry_unresolved(force=False)
            except Exception:
                app.logger.exception("unresolved retry failed")

            # Include NEGATIVE so UI can show 差; trade gates unchanged.
            discovery_rows = list_discovery_candidates(
                limit=300, exclude_negative=False
            )
            parts = partition_discovery_by_layer(discovery_rows)
            discovery_broad_rows = parts.get("broad") or []
            discovery_official_rows = parts.get("official") or []
            discovery_count = len(discovery_rows)
            discovery_perf = discovery_performance()
            discovery_unresolved = list_unresolved_discoveries(limit=80)
            discovery_unresolved_count = count_unresolved_discoveries()
            discovery_resolved_today = count_resolved_unresolved_today()
            from db import get_setting as _gs2

            discovery_channel_stats = _gs2("ai_discovery_last_channel_stats", {}) or {}
            if not isinstance(discovery_channel_stats, dict):
                discovery_channel_stats = {}
            discovery_channel_stats = _normalize_discovery_channel_stats(
                discovery_channel_stats
            )
        except Exception:
            app.logger.exception("AI Discovery load failed")
            discovery_rows = []
            discovery_broad_rows = []
            discovery_official_rows = []
            discovery_perf = None
            discovery_unresolved = []
            discovery_unresolved_count = 0
            discovery_resolved_today = 0

    # News History archive lives under AI Discovery (not a top tab).
    if tab == "discovery":
        try:
            from ai_discovery import list_discovery_candidates

            news_history_rows = list_discovery_candidates(
                limit=500,
                recent_only=False,
                exclude_negative=False,
                history_mode=True,
            )
            news_history_count = len(news_history_rows)
        except Exception:
            app.logger.exception("News History load failed")
            news_history_rows = []

    news_history_priority_rows = [
        r for r in news_history_rows if int(r.get("is_news_priority") or 0)
    ]
    news_history_archive_rows = [
        r for r in news_history_rows if not int(r.get("is_news_priority") or 0)
    ]

    highlight_rebuy_id = None
    try:
        if request.args.get("rebuy"):
            highlight_rebuy_id = int(request.args.get("rebuy"))
    except (TypeError, ValueError):
        highlight_rebuy_id = None
    range_key = (request.args.get("range") or "ALL").strip().upper()
    hist_strategy_id = STRATEGY_ALERT_BUY
    hist_report = None
    if tab == "history":
        raw_hist_sid = (
            request.args.get("strategy")
            or request.form.get("strategy")
            or STRATEGY_ALERT_BUY
        )
        hist_strategy_id = normalize_strategy_id(raw_hist_sid)
        hist_report = history_report(
            range_key=range_key, strategy_id=hist_strategy_id
        )
        try:
            summary = portfolio_summary_for_strategy(hist_strategy_id)
        except Exception:
            app.logger.exception("history strategy summary failed")
        history = list_closed_trades(strategy_id=hist_strategy_id, limit=300)

    try:
        _auto_thr = _ai_auto_thresholds()
    except Exception:
        _auto_thr = {"sma25_dist": -20.0, "target_ratio": 0.70, "pos_63d": 10.0}
    try:
        _knife_raw = get_all_settings().get(
            "knife_auto_block_threshold", KNIFE_AUTO_BLOCK_THRESHOLD
        )
        _knife_block = int(_knife_raw if _knife_raw is not None else KNIFE_AUTO_BLOCK_THRESHOLD)
    except (TypeError, ValueError):
        _knife_block = int(KNIFE_AUTO_BLOCK_THRESHOLD)

    try:
        return render_template(
            "ai_trading.html",
            tab=tab,
            summary=summary,
            strategy_dashboard=strategy_dashboard,
            strategy_shell=strategy_shell,
            strategy_pipeline=strategy_pipeline,
            active_strategy_id=active_strategy_id,
            strategy_meta=STRATEGY_META,
            ai_buy_view=ai_buy_view,
            deep_recovery_view=deep_recovery_view,
            stable_growth_view=stable_growth_view,
            safe_margin_view=safe_margin_view,
            short_sell_view=short_sell_view,
            candidates=candidates,
            trade_candidates=trade_candidates,
            opens=opens,
            open_count_all=open_count_all,
            history=history,
            hist_report=hist_report,
            hist_strategy_id=hist_strategy_id if tab == "history" else None,
            strategy_ids=STRATEGY_IDS,
            rebuy_pool=rebuy_pool,
            rebuy_candidates=rebuy_candidates,
            highlight_rebuy_id=highlight_rebuy_id,
            open_ticker_set={str(t.get("ticker") or "").upper() for t in opens},
            range_key=range_key if tab == "history" else "ALL",
            priority=priority,
            can_manage=is_owner(),
            stop_pct=float(get_all_settings().get("paper_stop_loss_pct", 5.0)),
            take_pct=float(get_all_settings().get("paper_take_profit_pct", 10.0)),
            ai_auto_sma25_dist=float(_auto_thr.get("sma25_dist", -20.0)),
            ai_auto_target_ratio=float(_auto_thr.get("target_ratio", 0.70)),
            ai_auto_63d_pos=float(_auto_thr.get("pos_63d", 10.0)),
            ai_auto_knife_block=_knife_block,
            discovery_rows=discovery_rows,
            discovery_broad_rows=discovery_broad_rows,
            discovery_official_rows=discovery_official_rows,
            discovery_layer=discovery_layer,
            discovery_perf=discovery_perf,
            discovery_unresolved=discovery_unresolved,
            discovery_unresolved_count=discovery_unresolved_count,
            discovery_resolved_today=discovery_resolved_today,
            discovery_min_score=discovery_min_score,
            discovery_channel_stats=discovery_channel_stats,
            discovery_count=discovery_count,
            news_history_rows=news_history_rows,
            news_history_count=news_history_count,
            news_history_priority_rows=news_history_priority_rows,
            news_history_archive_rows=news_history_archive_rows,
            open_news_history=bool(request.args.get("news_hist")),
            mdv_last_report=session.get("mdv_last_report"),
            mdv_last_ticker=session.get("mdv_last_ticker"),
            mdv_batch_report=session.get("mdv_batch_report"),
        )
    except Exception:
        app.logger.exception("ai_trading render failed")
        raise


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


@app.route("/strong-monitor", methods=["GET", "POST"])
def strong_stock_monitor():
    """
    Research / 研究中心:
    Candidate Analysis (default) | Daily Strong | COUNT20 | Strong Watchlist |
    Rising Now | Multi-Signal.
    """
    from strong_stocks import load_strong_monitor_page, run_backfill
    from watchlist_config import (
        add_my_watchlist_ticker,
        add_trade_candidate,
        remove_my_watchlist_ticker,
        remove_trade_candidate,
    )
    from candidate_analysis import build_candidate_analysis
    from market_data import ensure_fund_cache, ensure_news_cache

    raw_tab = (
        request.args.get("tab") or request.form.get("tab") or "daily"
    ).strip().lower()
    # Backward-compatible aliases from earlier UI revisions
    if raw_tab in ("matrix", "strong"):
        return redirect(url_for("strong_stock_monitor", tab="daily"))
    if raw_tab in ("candidate", "candidate_analysis", "analysis", "candidates"):
        # Full Candidate Analysis retired — keep Financial quality screens only
        return redirect(url_for("strong_stock_monitor", tab="fin6"))
    if raw_tab in ("financial_6", "fin_6", "financial6"):
        raw_tab = "fin6"
    if raw_tab in ("financial_ge5", "fin_ge5", "fin5", "financial5"):
        raw_tab = "fin5"
    tab = raw_tab
    if tab not in (
        "daily",
        "ranking",
        "watchlist",
        "rising_now",
        "rotation",
        "rotation_detail",
        "multi_signal",
        "fin6",
        "fin5",
        "discovery",
    ):
        tab = "daily"

    if tab == "discovery":
        # Legacy Research bookmark → Watchlist AI News
        return redirect(url_for("watchlist", tab="ai_news"))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if not is_owner():
            flash(gettext("Please sign in to manage Research"), "warning")
            return redirect(
                url_for("owner_login", next=url_for("strong_stock_monitor", tab=tab))
            )
        ticker = (request.form.get("ticker") or "").strip().upper()
        try:
            if action == "backfill":
                result = run_backfill()
                flash(
                    ngettext_format(
                        "Strong Watchlist rebuilt: {n} active (as of {day})",
                        n=result.get("active_members", 0),
                        day=result.get("as_of") or "—",
                    ),
                    "ok",
                )
            elif action == "add_mine":
                add_my_watchlist_ticker(ticker)
                refreshed = _force_refresh_mine_tickers([ticker]) if ticker else []
                flash(
                    ngettext_format("Added {ticker} to My Watchlist", ticker=ticker),
                    "ok",
                )
                if refreshed:
                    flash(
                        ngettext_format(
                            "Live data refreshed for: {tickers}",
                            tickers=", ".join(refreshed),
                        ),
                        "ok",
                    )
            elif action == "remove_mine":
                remove_my_watchlist_ticker(ticker)
                flash(gettext("Removed from My Watchlist"), "ok")
            elif action == "add_trade":
                add_trade_candidate(ticker)
                flash(gettext("Marked as Trade Candidate"), "ok")
            elif action == "remove_trade":
                remove_trade_candidate(ticker)
                flash(gettext("Removed Trade Candidate flag"), "ok")
            elif action == "ensure_research":
                data_ca = build_candidate_analysis(fill_ai=False)
                tickers = [r["ticker"] for r in data_ca["rows"] if r.get("ticker")]
                batch = tickers[:120]
                fr = ensure_fund_cache(batch, max_workers=3, force=False)
                fund_map = get_fund_cached_only(batch)
                mine = {str(t).strip().upper() for t in (get_my_watchlist() or [])}
                news_batch = [
                    t
                    for t in batch
                    if t in mine or fund_qualifies_for_news(fund_map.get(t))
                ]
                nr = ensure_news_cache(news_batch[:80], max_workers=3, force=False)
                flash(
                    ngettext_format(
                        "Candidate research refreshed: fund ok_new {f} · news ok_new {n} (bounded batch)",
                        f=(fr or {}).get("ok_new", 0),
                        n=(nr or {}).get("ok_new", 0),
                    ),
                    "ok",
                )
                return redirect(
                    url_for(
                        "strong_stock_monitor",
                        tab=tab if tab in ("fin6", "fin5") else "fin6",
                    )
                )
            elif action == "refresh_rotation":
                from sector_rotation import job_sector_rotation_update

                result = job_sector_rotation_update(force=True)
                flash(
                    ngettext_format(
                        "Sector Rotation updated: {n} sectors (as of {day})",
                        n=result.get("sectors", 0),
                        day=result.get("as_of") or "—",
                    ),
                    "ok",
                )
                return redirect(url_for("strong_stock_monitor", tab="rotation"))
            else:
                flash(gettext("Unknown action"), "warning")
        except Exception as exc:
            flash(
                ngettext_format("Research action failed: {exc}", exc=exc),
                "warning",
            )
        return redirect(url_for("strong_stock_monitor", tab=tab))

    # Rising Now / Multi-Signal badges (skip heavy rebuild when on Candidate Analysis —
    # that builder already computes the same underlying lists).
    rising_rows: list = []
    multi_rows: list = []
    multi_summary: dict = {}
    ca_rows: list = []
    ca_counts: dict = {}
    badge_candidates = 0
    badge_fin6 = 0
    badge_fin5 = 0
    rotation_data: dict = {}
    rotation_detail: dict = {}

    def _row_fin6(r: dict) -> bool:
        return r.get("financial_ok") == 6 and r.get("financial_known") == 6

    def _row_fin_ge5(r: dict) -> bool:
        ok = r.get("financial_ok")
        known = r.get("financial_known")
        if ok is None or not known:
            return False
        try:
            return (float(ok) / float(known)) >= (5 / 6)
        except (TypeError, ValueError, ZeroDivisionError):
            return False

    if tab in ("fin6", "fin5"):
        try:
            ca = build_candidate_analysis(fill_ai=True)
            all_ca = ca.get("rows") or []
            ca_counts = ca.get("counts") or {}
            badge_fin6 = int(ca_counts.get("fin_6") or 0)
            badge_fin5 = int(ca_counts.get("fin_ge5") or 0)
            badge_candidates = int(ca_counts.get("total") or 0)
            if tab == "fin6":
                ca_rows = [r for r in all_ca if _row_fin6(r)]
            else:
                ca_rows = [r for r in all_ca if _row_fin_ge5(r)]
            sizes = ca.get("source_sizes") or {}
            data_badge_rising = int(sizes.get("rising") or 0)
            data_badge_multi = int(sizes.get("multi") or 0)
        except Exception:
            app.logger.exception("strong-monitor financial analysis failed")
            data_badge_rising = 0
            data_badge_multi = 0
    elif tab in ("rotation", "rotation_detail"):
        try:
            from sector_rotation import build_sector_detail, load_latest_sector_rotation

            rotation_data = load_latest_sector_rotation(recompute_if_missing=True)
            if tab == "rotation_detail":
                sector_q = (request.args.get("sector") or "").strip()
                if sector_q:
                    rotation_detail = build_sector_detail(sector_q)
                else:
                    tab = "rotation"
        except Exception:
            app.logger.exception("strong-monitor sector rotation failed")
            flash(gettext("Sector Rotation failed to load. Try Refresh Rotation."), "warning")
            rotation_data = {
                "as_of": "",
                "rows": [],
                "summary": {
                    "leading": [],
                    "rotating_in": [],
                    "weakening": [],
                    "falling": [],
                },
                "rules": "",
            }
        try:
            rising_rows = [_enrich(r) for r in list_rising_now()]
        except Exception:
            rising_rows = []
        data_badge_rising = len(rising_rows)
        data_badge_multi = 0
    else:
        rising_rows = []
        multi_rows = []
        multi_summary = {}
        try:
            rising_rows = [_enrich(r) for r in list_rising_now()]
            setup_src = [dict(r) for r in list_setup(-10.0)]
            target_src = [dict(r) for r in list_low_target_ratio(0.8)]
            low63_src = [dict(r) for r in list_low_63d_pos(25.0)]
            multi_rows, multi_summary = build_multi_signal(
                setup_src, target_src, low63_src, rising_rows
            )
            multi_rows = [_enrich(r) for r in multi_rows]
        except Exception:
            app.logger.exception("strong-monitor rising/multi failed")
        data_badge_rising = len(rising_rows)
        data_badge_multi = len(multi_rows)

        # Attach AI from cache for Rising Now / Multi-Signal compact tabs.
        if tab in ("rising_now", "multi_signal"):
            active = rising_rows if tab == "rising_now" else multi_rows
            tickers = [r["ticker"] for r in active if r.get("ticker")]
            fund_map = get_fund_cached_only(tickers)
            news_map = get_news_cached_only(tickers) if tickers else {}
            for r in active:
                t = (r.get("ticker") or "").upper()
                r["fund"] = fund_map.get(t)
                r["news"] = news_map.get(t)
                r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))
                r["ai"] = compute_ai_score(r)
                r["fund"] = None
                r["news"] = None
            try:
                from knife_risk import attach_knife_risk

                attach_knife_risk(active, ensure_bench=True)
            except Exception:
                for r in active:
                    r.setdefault("knife", None)
            try:
                from rising_score import attach_rising_score

                attach_rising_score(active, ensure_bench=False)
            except Exception:
                for r in active:
                    r.setdefault("rising", None)
            if tab == "rising_now":
                # Rank by Rising Score (strength), then 5D return.
                active.sort(
                    key=lambda r: (
                        -(
                            (r.get("rising") or {}).get("score")
                            if (r.get("rising") or {}).get("score") is not None
                            else -1
                        ),
                        -(
                            r.get("return_5d_pct")
                            if r.get("return_5d_pct") is not None
                            else float("-inf")
                        ),
                        r.get("ticker") or "",
                    )
                )

    strong_tab = tab if tab in ("daily", "ranking", "watchlist") else "meta"
    try:
        data = load_strong_monitor_page(tab=strong_tab)
    except Exception:
        app.logger.exception("strong-monitor page data failed")
        flash(gettext("Research failed to load data. Try Rebuild / Backfill."), "warning")
        data = {
            "as_of": "",
            "built_at": "",
            "rules": "",
            "daily": {"columns": [], "max_rows": 0},
            "ranking": {"count": 0, "rows": [], "distribution_line": "", "n_ge_threshold": 0},
            "watchlist": {"count": 0, "count_qualifying": 0, "count_retention": 0, "rows": []},
            "badge_daily": 0,
            "badge_ranking": 0,
            "badge_watchlist": 0,
        }

    data["badge_rising"] = data_badge_rising
    data["badge_multi"] = data_badge_multi
    data["badge_candidates"] = badge_candidates
    data["badge_fin6"] = badge_fin6
    data["badge_fin5"] = badge_fin5
    data["rising_rows"] = rising_rows
    data["multi_rows"] = multi_rows
    data["multi_summary"] = multi_summary
    data["ca_rows"] = ca_rows
    data["ca_counts"] = ca_counts
    data["rotation"] = rotation_data
    data["rotation_detail"] = rotation_detail
    data["badge_rotation"] = len((rotation_data or {}).get("rows") or [])
    lang = get_lang()
    data["rising_headline"] = rising_count_label(data_badge_rising, lang=lang)
    data["rising_rules"] = rising_rule_summary(lang=lang)

    return render_template(
        "strong_monitor.html",
        data=data,
        tab=tab,
        can_manage=is_owner(),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
