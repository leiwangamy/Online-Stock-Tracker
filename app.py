import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend for servers

from flask import Flask, render_template
import yfinance as yf
import matplotlib.pyplot as plt
import os
from datetime import datetime

app = Flask(__name__)

def generate_chart():
    msft = yf.Ticker("MSFT").history(period="30d")
    td = yf.Ticker("TD.TO").history(period="30d")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(msft.index, msft["Close"], color="blue", label="MSFT (USD)")
    ax1.set_ylabel("MSFT Price (USD)")
    ax1.legend()
    ax1.grid(True)

    ax2.plot(td.index, td["Close"], color="green", label="TD.TO (CAD)")
    ax2.set_ylabel("TD Price (CAD)")
    ax2.legend()
    ax2.grid(True)

    plt.suptitle("30-Day Stock Prices: Microsoft vs TD Bank")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join("static", "chart.png"))
    plt.close()

    # Write the current timestamp to static/last_updated.txt
    with open(os.path.join("static", "last_updated.txt"), "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    return msft, td

@app.route('/')
def index():
    msft, td = generate_chart()

    # Read the last updated timestamp
    try:
        with open(os.path.join("static", "last_updated.txt")) as f:
            last_updated = f.read()
    except:
        last_updated = "Unknown"

    # Prepare table data (latest 7 days)
    table_data = []
    for date in msft.tail(7).index:
        row = {
            "date": date.strftime("%Y-%m-%d"),
            "msft": f"{msft.loc[date]['Close']:.2f}",
            "td": f"{td.loc[date]['Close']:.2f}" if date in td.index else "-"
        }
        table_data.append(row)

    msft_avg = round(msft["Close"].mean(), 2)
    td_avg = round(td["Close"].mean(), 2)

    return render_template("index.html", table_data=table_data, msft_avg=msft_avg, td_avg=td_avg, last_updated=last_updated)

@app.route('/update')
def update():
    try:
        generate_chart()
        return "Chart updated!"
    except Exception as e:
        return f"Update failed: {e}", 500

if __name__ == '__main__':
    app.run(debug=True)
