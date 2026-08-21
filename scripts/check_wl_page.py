import re
import urllib.request

r = urllib.request.urlopen("http://127.0.0.1:3000/strong-monitor?tab=watchlist", timeout=90)
b = r.read().decode("utf-8", "replace")
print("status", r.status)
i = b.find("63D High")
print("rules", b[i : i + 100] if i >= 0 else "missing")
j = b.find("Qualifying")
print("qual line", b[j : j + 100].replace("\n", " ") if j >= 0 else "missing")
print("has >= 13", "COUNT20 >= 13" in b or "COUNT≥13" in b)
print("has >= 18", "COUNT20 >= 18" in b or "COUNT≥18" in b)
m = re.search(r'class="n">(\d+)</span>', b)
print("first total n", m.group(1) if m else None)
