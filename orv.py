#!/usr/bin/env python3
"""
open-repo-view (orv): GitHub traffic insights + mini web dashboard + interactive CLI.
"""

import os
import sys
import sqlite3
import threading
import requests
from flask import Flask, render_template_string
from datetime import datetime, timedelta
from collections import defaultdict

# ─── Config ────────────────────────────────────────────────────────────────────

# Read ORV_TOKEN (Codespaces) or fall back to GITHUB_TOKEN (Actions)
TOKEN = os.getenv("ORV_TOKEN") or os.getenv("GITHUB_TOKEN")
if not TOKEN:
    print("‼️ ERROR: ORV_TOKEN (Codespaces) or GITHUB_TOKEN (Actions) missing", file=sys.stderr)
    sys.exit(1)

API     = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}

# auto-detect owner
try:
    r = requests.get(f"{API}/user", headers=HEADERS); r.raise_for_status()
    OWNER = r.json()["login"]
except Exception as e:
    print(f"‼️ Failed to detect owner: {e}", file=sys.stderr)
    sys.exit(1)

# files & lookback
DB       = "traffic.db"
CSV      = "github_traffic.csv"
LOOKBACK = 14  # days

# ─── API Helpers ───────────────────────────────────────────────────────────────

def list_repos():
    """List non-fork repos you own."""
    url   = f"{API}/user/repos?affiliation=owner&per_page=100"
    names = []
    while url:
        r = requests.get(url, headers=HEADERS)
        if r.status_code != 200:
            print(f"⚠️ list_repos got {r.status_code}", file=sys.stderr)
            break
        for item in r.json():
            if not item.get("fork", False):
                names.append(item["name"])
        url = r.links.get("next", {}).get("url")
    return names

def fetch(kind, repo, per="day"):
    """Fetch 'views' or 'clones'; skip 403/404."""
    url = f"{API}/repos/{OWNER}/{repo}/traffic/{kind}"
    r   = requests.get(url, headers=HEADERS, params={"per": per})
    if r.status_code in (403, 404):
        print(f"⚠️ Skipping {repo}/{kind}: {r.status_code} {r.reason}")
        return []
    r.raise_for_status()
    data = r.json()
    return data.get(kind, data.get("views", data.get("clones", [])))

def fetch_referrers(repo):
    url = f"{API}/repos/{OWNER}/{repo}/traffic/popular/referrers"
    r   = requests.get(url, headers=HEADERS)
    if r.status_code in (403, 404):
        print(f"⚠️ Skipping referrers for {repo}: {r.status_code}")
        return []
    r.raise_for_status()
    return r.json()

def fetch_paths(repo):
    url = f"{API}/repos/{OWNER}/{repo}/traffic/popular/paths"
    r   = requests.get(url, headers=HEADERS)
    if r.status_code in (403, 404):
        print(f"⚠️ Skipping paths for {repo}: {r.status_code}")
        return []
    r.raise_for_status()
    return r.json()

def show_rate_limit():
    r = requests.get(f"{API}/rate_limit", headers=HEADERS); r.raise_for_status()
    core = r.json()["resources"]["core"]
    reset = datetime.fromtimestamp(core["reset"]).strftime("%Y-%m-%d %H:%M")
    print(f"🔋 Limit:     {core['limit']} / hr")
    print(f"⚡ Remaining: {core['remaining']}")
    print(f"⏳ Resets at: {reset} UTC")

# ─── Storage ───────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS traffic (
        date TEXT PRIMARY KEY,
        views INTEGER, unique_views INTEGER,
        clones INTEGER, unique_clones INTEGER
      )
    """)
    return conn

def save_day(conn, date, stats):
    conn.execute("""
      INSERT INTO traffic(date,views,unique_views,clones,unique_clones)
      VALUES (?,?,?,?,?)
      ON CONFLICT(date) DO UPDATE SET
        views=excluded.views,
        unique_views=excluded.unique_views,
        clones=excluded.clones,
        unique_clones=excluded.unique_clones
    """, (date, stats["views"], stats["unique_views"],
          stats["clones"], stats["unique_clones"]))
    conn.commit()

# ─── Web Dashboard ────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def dashboard():
    conn = init_db()
    rows = conn.execute("SELECT date,views,clones FROM traffic ORDER BY date").fetchall()
    if rows:
        dates, views, clones = zip(*rows)
    else:
        dates, views, clones = [], [], []

    tpl = """
    <!doctype html>
    <html><head>
      <title>{{owner}} Traffic</title>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head><body>
      <h1>{{owner}} (last {{lookback}} days)</h1>
      <canvas id="chart"></canvas>
      <script>
        new Chart(document.getElementById('chart'), {
          type: 'line',
          data: {
            labels: {{dates}},
            datasets: [
              { label: 'Views', data: {{views}}, borderColor: 'blue', fill: false },
              { label: 'Clones', data: {{clones}}, borderColor: 'green', fill: false }
            ]
          }
        });
      </script>
    </body></html>
    """
    return render_template_string(
        tpl,
        owner=OWNER,
        lookback=LOOKBACK,
        dates=list(dates),
        views=list(views),
        clones=list(clones)
    )

def run_dashboard():
    print("🔗 Dashboard: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)

# ─── Interactive CLI ──────────────────────────────────────────────────────────

MENU = """
1) Fetch daily views & clones
2) Show top referrers for a repo
3) Show top content paths for a repo
4) Launch web dashboard
5) Show API rate-limit
6) Quit
"""

def fetch_daily():
    print(f"⏳ Fetching last {LOOKBACK} days…")
    cutoff = (datetime.utcnow() - timedelta(days=LOOKBACK)).strftime("%Y-%m-%d")
    conn   = init_db()
    totals = defaultdict(lambda: {"views":0,"unique_views":0,"clones":0,"unique_clones":0})

    for repo in list_repos():
        for e in fetch("views", repo):
            d = e["timestamp"][:10]
            if d < cutoff: continue
            totals[d]["views"]       += e["count"]
            totals[d]["unique_views"]+= e["uniques"]
        for e in fetch("clones", repo):
            d = e["timestamp"][:10]
            if d < cutoff: continue
            totals[d]["clones"]      += e["count"]
            totals[d]["unique_clones"]+= e["uniques"]

    for day, stats in totals.items():
        save_day(conn, day, stats)

    with open(CSV, "w") as f:
        f.write("Date,Views,UniqViews,Clones,UniqClones\n")
        for day in sorted(totals):
            st = totals[day]
            f.write(f"{day},{st['views']},{st['unique_views']},"
                    f"{st['clones']},{st['unique_clones']}\n")
    print(f"✅ Saved: {CSV}, {DB}")

def drill_referrers():
    repo = input("Repo name: ").strip()
    for r in sorted(fetch_referrers(repo), key=lambda x: x["count"], reverse=True)[:10]:
        print(f"{r['referrer']}: {r['count']} hits, {r['uniques']} uniques")

def drill_paths():
    repo = input("Repo name: ").strip()
    for p in sorted(fetch_paths(repo), key=lambda x: x["count"], reverse=True)[:10]:
        print(f"{p['path']}: {p['count']} hits, {p['uniques']} uniques")

def main():
    while True:
        print(MENU)
        choice = input("Choose [1-6]: ").strip()
        if choice == "1":
            fetch_daily()
        elif choice == "2":
            drill_referrers()
        elif choice == "3":
            drill_paths()
        elif choice == "4":
            threading.Thread(target=run_dashboard, daemon=True).start()
        elif choice == "5":
            show_rate_limit()
        elif choice == "6":
            break
        else:
            print("❓ Invalid choice")

if __name__ == "__main__":
    from datetime import datetime, timedelta
    from collections import defaultdict
    main()
