#!/usr/bin/env python3
"""
open-repo-view (orv)
────────────────────
GitHub traffic insights → interactive CLI + mini Flask/Chart.js dashboard.

Key features
============
• Works with classic *and* fine-grained PATs
• Prints exact permission you’re missing (via X-Accepted-GitHub-Permissions)
• Persists aggregated traffic (14-day rolling window) to SQLite + CSV
• Zero heavy deps – only `requests` & `flask`
"""

from __future__ import annotations

import os
import sys
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests
from flask import Flask, render_template_string

# ── Config ──────────────────────────────────────────────────────────────────

TOKEN = os.getenv("ORV_TOKEN") or os.getenv("GITHUB_TOKEN")
if not TOKEN:
    sys.exit("‼️  ORV_TOKEN (or GITHUB_TOKEN) not set – add it as a secret/env.")

API = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
}
LOOKBACK_DAYS = 14
DB_PATH = "traffic.db"
CSV_PATH = "github_traffic.csv"

# ── Detect user + token type ────────────────────────────────────────────────

try:
    resp = requests.get(f"{API}/user", headers=HEADERS)
    resp.raise_for_status()
    OWNER = resp.json()["login"]
    scopes_hdr = resp.headers.get("X-OAuth-Scopes")  # Only on classic PATs

    print(f"👤 Authenticated as: {OWNER}")
    if scopes_hdr:  # Classic PAT
        print(f"🔑 Token scopes: {scopes_hdr}")
        if "repo" not in [s.strip() for s in scopes_hdr.split(",")]:
            sys.exit("‼️  Token missing `repo` or `public_repo` scope required for traffic.")
    else:  # Fine-grained PAT
        print("🔑 Fine-grained PAT detected (no global scopes header).")
except Exception as exc:
    sys.exit(f"‼️  Failed token check → {exc}")

# ── HTTP helper that surfaces missing permissions ───────────────────────────


def _get(url: str, **params) -> requests.Response | None:
    r = requests.get(url, headers=HEADERS, **params)
    if r.status_code == 403:
        need = r.headers.get("X-Accepted-GitHub-Permissions")
        msg = r.json().get("message", "Forbidden")
        print(f"🚫 403 {url} – {msg}")
        if need:
            print(f"   🔐 Needs permissions → {need}")
            print("   Enable **Repository → Administration → Read (Traffic)** in token.")
        return None
    if r.status_code == 404:
        return None  # repo private to token / no traffic
    r.raise_for_status()
    return r


# ── GitHub API helpers ──────────────────────────────────────────────────────


def list_repos() -> List[str]:
    url = f"{API}/user/repos?affiliation=owner&per_page=100"
    repos: List[str] = []
    while url:
        r = _get(url)
        if r is None:
            break
        repos += [repo["name"] for repo in r.json() if not repo.get("fork")]
        url = r.links.get("next", {}).get("url")
    return repos


def fetch_traffic(kind: str, repo: str) -> List[Dict[str, Any]]:
    r = _get(f"{API}/repos/{OWNER}/{repo}/traffic/{kind}", params={"per": "day"})
    if r is None:
        return []
    payload = r.json()
    return payload.get(kind) or payload.get("views") or payload.get("clones") or []


def fetch_referrers(repo: str) -> List[Dict[str, Any]]:
    return _get(f"{API}/repos/{OWNER}/{repo}/traffic/popular/referrers") or []


def fetch_paths(repo: str) -> List[Dict[str, Any]]:
    return _get(f"{API}/repos/{OWNER}/{repo}/traffic/popular/paths") or []


def show_rate_limit() -> None:
    r = _get(f"{API}/rate_limit")
    if r is None:
        return
    core = r.json()["resources"]["core"]
    rst = datetime.fromtimestamp(core["reset"]).strftime("%Y-%m-%d %H:%M")
    print(f"🔋 Limit {core['limit']}/h | Remaining {core['remaining']} | Resets {rst} UTC")


# ── SQLite persistence ──────────────────────────────────────────────────────


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS traffic(
           date TEXT PRIMARY KEY,
           views INTEGER, unique_views INTEGER,
           clones INTEGER, unique_clones INTEGER
        )"""
    )
    return conn


def upsert(conn: sqlite3.Connection, day: str, stats: Dict[str, int]) -> None:
    conn.execute(
        """INSERT INTO traffic VALUES (?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             views=excluded.views,
             unique_views=excluded.unique_views,
             clones=excluded.clones,
             unique_clones=excluded.unique_clones""",
        (
            day,
            stats["views"],
            stats["unique_views"],
            stats["clones"],
            stats["unique_clones"],
        ),
    )
    conn.commit()


# ── Dashboard (Flask) ────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def dashboard():
    rows = init_db().execute(
        "SELECT date, views, clones FROM traffic ORDER BY date"
    ).fetchall()
    dates, views, clones = zip(*rows) if rows else ([], [], [])
    tmpl = """
<!doctype html><html><head><meta charset="utf-8">
<title>{{owner}} Traffic</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{font:16px/1.5 system-ui;margin:2rem}
canvas{max-width:880px}
</style></head><body>
<h1>{{owner}} <small style="font-size:0.6em">last {{lookback}} days</small></h1>
<canvas id="ch"></canvas>
<script>
new Chart(ch,{
  type:'line',
  data:{labels:{{dates}},
        datasets:[
          {label:'Views', data:{{views}}, borderColor:'royalblue', fill:false},
          {label:'Clones',data:{{clones}},borderColor:'seagreen', fill:false}
        ]},
  options:{responsive:true, interaction:{mode:'index',intersect:false}}
});
</script>
</body></html>"""
    return render_template_string(
        tmpl,
        owner=OWNER,
        lookback=LOOKBACK_DAYS,
        dates=list(dates),
        views=list(views),
        clones=list(clones),
    )


def launch_dashboard() -> None:
    print("🌐  Dashboard → http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)


# ── CLI actions ──────────────────────────────────────────────────────────────


def fetch_daily() -> None:
    """Aggregate last LOOKBACK_DAYS traffic across *all* repos."""
    print(f"⏳ Pulling last {LOOKBACK_DAYS} days …")
    cutoff = (datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    conn = init_db()
    totals: Dict[str, Dict[str, int]] = defaultdict(
        lambda: defaultdict(int)  # views / clones keyed by day
    )

    for repo in list_repos():
        for entry in fetch_traffic("views", repo):
            day = entry["timestamp"][:10]
            if day >= cutoff:
                st = totals[day]
                st["views"] += entry["count"]
                st["unique_views"] += entry["uniques"]
        for entry in fetch_traffic("clones", repo):
            day = entry["timestamp"][:10]
            if day >= cutoff:
                st = totals[day]
                st["clones"] += entry["count"]
                st["unique_clones"] += entry["uniques"]

    # persist & CSV
    for d, s in totals.items():
        upsert(conn, d, s)

    with open(CSV_PATH, "w", newline="") as fh:
        fh.write("Date,Views,UniqViews,Clones,UniqClones\n")
        for d in sorted(tals := totals):  # noqa: E501
            s = tals[d]
            fh.write(f"{d},{s['views']},{s['unique_views']},{s['clones']},{s['unique_clones']}\n")

    print(f"✅ {len(tals)} days saved → {CSV_PATH}  |  SQLite → {DB_PATH}")


def drill(getter, key: str) -> None:  # helper for referrers / paths
    repo = input("Repo name: ").strip()
    rows = getter(repo)[:10]
    if not rows:
        print("ℹ️  No data (repo may not have enough traffic).")
    for row in rows:
        print(f"{row[key]} – {row['count']} hits ({row['uniques']} uniq)")


def cli() -> None:
    menu = """
1) Fetch daily views & clones
2) Top referrers for a repo
3) Top content paths for a repo
4) Launch web dashboard
5) Show API rate-limit
6) Quit
"""
    while True:
        print(menu)
        choice = input("Choose [1-6]: ").strip()
        if choice == "1":
            fetch_daily()
        elif choice == "2":
            drill(fetch_referrers, "referrer")
        elif choice == "3":
            drill(fetch_paths, "path")
        elif choice == "4":
            threading.Thread(target=launch_dashboard, daemon=True).start()
        elif choice == "5":
            show_rate_limit()
        elif choice == "6":
            break
        else:
            print("❓ Invalid choice")


if __name__ == "__main__":
    cli()
