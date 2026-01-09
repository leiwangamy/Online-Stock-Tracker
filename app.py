import matplotlib
matplotlib.use("Agg")  # non-GUI backend for servers

import os
import uuid
import time
import glob
import re
from datetime import datetime, timezone

from flask import Flask, render_template, request
import yfinance as yf
import matplotlib.pyplot as plt


app = Flask(__name__)

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


@app.route("/", methods=["GET", "POST"])
def index():
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
