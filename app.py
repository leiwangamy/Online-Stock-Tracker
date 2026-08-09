import matplotlib
matplotlib.use("Agg")  # non-GUI backend for servers

import os
import uuid
import time
import glob
import re
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, url_for, flash, session
import yfinance as yf
import matplotlib.pyplot as plt

from db import (
    dashboard_meta,
    get_all_settings,
    get_dashboard_by_tickers,
    get_setting,
    get_universe_flags,
    init_db,
    list_dashboard,
    list_oversold,
    list_pullback,
    set_setting,
    universe_count,
)
from market_data import (
    compute_ai_score,
    fetch_metrics_for_ticker,
    get_signals,
    refresh_dashboard_cache,
)
from universe import refresh_universe as rebuild_universe


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-online-stock-tracker")
# Pick up template edits without a full server restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

init_db()

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
    return render_template("home.html")


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
            "label": GROUP_LABELS[key],
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
        group_label=GROUP_LABELS[group],
        tabs=tabs,
    )


@app.route("/dashboard/refresh-universe", methods=["POST"])
def refresh_universe():
    group = _normalize_group(request.form.get("group"))
    try:
        result = rebuild_universe()
        flash(
            f"股票池已更新：S&P500 {result['sp500']} + Nasdaq100 {result['ndx100']} "
            f"+ S&P400 {result['sp400']} + S&P600 {result['sp600']} "
            f"+ TSX {result['tsx']} → 去重后 {result['unique']} 只",
            "ok",
        )
    except Exception as exc:
        flash(f"更新股票池失败：{exc}", "warning")
    return redirect(url_for("market_dashboard", group=group))


@app.route("/dashboard/refresh", methods=["POST"])
def refresh_dashboard():
    group = _normalize_group(request.form.get("group"))
    try:
        if universe_count() == 0:
            rebuild_universe()
        result = refresh_dashboard_cache(group=group)
        flash(
            f"行情已刷新（{GROUP_LABELS[group]}）：成功 {result['ok']} / 失败 {result['errors']} "
            f"（SMA{result['sma_period']}，本组 {result['universe']} 只）",
            "ok",
        )
    except Exception as exc:
        flash(f"刷新行情失败：{exc}", "warning")
    return redirect(url_for("market_dashboard", group=group))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        try:
            sma_period = int(request.form.get("sma_period", 25))
            rebound_lookback = int(request.form.get("rebound_lookback", sma_period))
            if sma_period < 5 or sma_period > 250:
                raise ValueError("平均周期需在 5–250 之间")
            if rebound_lookback < 5 or rebound_lookback > 250:
                raise ValueError("反弹回看天数需在 5–250 之间")
            set_setting("sma_period", sma_period)
            set_setting("rebound_lookback", rebound_lookback)
            set_setting("data_source", "yahoo")
            flash(
                f"已保存：SMA={sma_period}，反弹回看={rebound_lookback}。请到 Market Dashboard 重新刷新行情。",
                "ok",
            )
            return redirect(url_for("settings"))
        except Exception as exc:
            flash(f"保存失败：{exc}", "warning")

    settings_data = get_all_settings()
    return render_template(
        "settings.html",
        sma_period=int(settings_data.get("sma_period", 25)),
        rebound_lookback=int(settings_data.get("rebound_lookback", 25)),
        presets=settings_data.get("sma_presets", [25, 50, 63, 90]),
    )


# Group ③ — long-term saved names (hardcoded until per-user accounts exist).
MY_WATCHLIST = ["AAPL", "NVDA"]
MAX_TEMP_TICKERS = 20
MAX_AUTO_ROWS = 30  # cap oversold/pullback rows we enrich live (bounds page latency)


def _pools_label(row: dict) -> str:
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
    return " / ".join(labels) if labels else "—"


def _enrich(row: dict) -> dict:
    row = dict(row)
    row["pools"] = _pools_label(row)
    return row


def _rows_for_tickers(tickers: list[str]) -> list[dict]:
    """Build watchlist rows for explicit tickers: prefer cache, else fetch live."""
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

    out = []
    for t in clean:
        if t in cached:
            out.append(_enrich(cached[t]))
            continue
        meta = flags.get(t, {})
        metrics = fetch_metrics_for_ticker(t, sma_period=sma, rebound_lookback=reb, meta=meta)
        if metrics:
            merged = dict(metrics)
            merged.update({k: meta.get(k) for k in ("in_sp500", "in_ndx100", "in_sp400", "in_sp600", "in_tsx")})
            out.append(_enrich(merged))
        else:
            out.append({"ticker": t, "pools": "—", "not_found": True})
    return out


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
        return redirect(url_for("watchlist"))

    settings_data = get_all_settings()
    sma_period = int(settings_data.get("sma_period", 25))
    temp_tickers = session.get("temp_watchlist", [])

    tab = request.args.get("tab", "oversold")
    if tab not in ("oversold", "pullback", "mine", "temp"):
        tab = "oversold"

    # Cheap cache-only lists (no live fetch) — used for data and tab counts.
    oversold = [_enrich(r) for r in list_oversold(-20.0)]
    pullback = [_enrich(r) for r in list_pullback(-3.0)]

    # Live-fetch groups only build rows for the active tab (they hit Yahoo).
    rows = []
    if tab == "oversold":
        rows = oversold[:MAX_AUTO_ROWS]
    elif tab == "pullback":
        rows = pullback[:MAX_AUTO_ROWS]
    elif tab == "mine":
        rows = _rows_for_tickers(MY_WATCHLIST)
    elif tab == "temp":
        rows = _rows_for_tickers(temp_tickers)

    # Enrich only the rows we actually show with fundamentals (财报) + news (新闻).
    signals = get_signals([r["ticker"] for r in rows if r.get("ticker")])
    for r in rows:
        sig = signals.get(r.get("ticker"))
        if sig:
            r["fund"] = sig.get("fund")
            r["news"] = sig.get("news")
        if not r.get("not_found"):
            r["ai"] = compute_ai_score(r)

    tabs = [
        {"key": "oversold", "label": "🔻 超卖建议", "count": len(oversold)},
        {"key": "pullback", "label": "🟢 强势回调", "count": len(pullback)},
        {"key": "mine", "label": "⭐ 我的自选", "count": len(MY_WATCHLIST)},
        {"key": "temp", "label": "🕒 临时", "count": len(temp_tickers)},
    ]

    return render_template(
        "watchlist.html",
        sma_period=sma_period,
        tab=tab,
        tabs=tabs,
        rows=rows,
        temp_tickers=temp_tickers,
        max_temp=MAX_TEMP_TICKERS,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
