# 📈 Online Stock Tracker | 实时股票追踪工具

A web-based app built with Flask and yFinance to track real-time stock data and display price history with interactive charts.

🔗 **Live App:** [online-stock-tracker-leiwangamy.replit.app](https://online-stock-tracker-leiwangamy.replit.app)

---

## 📁 Project Structure

- 📄 `app.py` – Flask app that fetches and plots stock data  
- 📄 `requirements.txt` – Required Python packages for the app  
- 📂 `templates/` – HTML templates (`index.html`)  
- 📂 `static/` – Optional CSS or chart images (if any)

---

## 🛠️ Technologies Used

- Python + Flask  
- yFinance (Yahoo Finance API)  
- Matplotlib (for stock price charts)  
- HTML + CSS (for layout)  
- Replit (deployment)  
- GitHub (version control)

---

## 🚀 How to Use

1. Enter the stock symbol (e.g. `MSFT`, `TD.TO`)  
2. Choose a date range or default to recent 30 days  
3. Click **Track** to view the stock price chart  
4. Optionally download or screenshot the graph

---

## ⚙️ Installation (for local use)

```bash
pip install -r requirements.txt
python app.py
