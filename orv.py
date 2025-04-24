#!/usr/bin/env python3
"""
open-repo-view (orv)
────────────────────────────────────────────
automatically generate repo traffic report (totals, averages, best day)

"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

import requests
from flask import Flask
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TimeElapsedColumn,
)
from rich.table import Table

# ── CLI flags ────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(prog="orv", add_help=False)
parser.add_argument(
    "-v", "--verbose", action="store_true", help="print each repo as it's processed"
)
args, _ = parser.parse_known_args()

# ── Config ───────────────────────────────────────────────────────────────────
TOKEN = os.getenv("ORV_TOKEN") or os.getenv("GITHUB_TOKEN")
if not TOKEN:
    sys.exit("‼️  ORV_TOKEN / GITHUB_TOKEN missing.")

API = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}",
           "Accept": "application/vnd.github+json"}
LOOKBACK = 14  # days
DB_PATH = "traffic.db"
CSV_PATH = "github_traffic.csv"
console = Console()

# ── Auth check ───────────────────────────────────────────────────────────────


def who_am_i() -> str:
    r = requests.get(f"{API}/user", headers=HEADERS)
    r.raise_for_status()
    login = r.json()["login"]
    scopes = r.headers.get("X-OAuth-Scopes")
    console.print(f"👤 [bold]{login}[/] authenticated.")
    if scopes:
        console.print(f"🔑 scopes: {scopes}")
        if "repo" not in [s.strip() for s in scopes.split(",")]:
            sys.exit("‼️  token lacks `repo`/`public_repo` scope.")
    else:
        console.print("🔑 fine-grained PAT detected.")
    return login


OWNER = who_am_i()

# ── Helper that surfaces missing permissions ────────────────────────────────


def _get(url: str, **kw):
    r = requests.get(url, headers=HEADERS, **kw)
    if r.status_code == 403:
        perms = r.headers.get("X-Accepted-GitHub-Permissions")
        msg = r.json().get("message", "Forbidden")
        console.print(f"🚫 403 {url} – {msg}")
        if perms:
            console.print(
                f"   needs → {perms}\n   enable Repository → Administration → Read (Traffic)"
            )
        return None
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r


# ── API helpers ──────────────────────────────────────────────────────────────
def list_repos() -> List[str]:
    url = f"{API}/user/repos?affiliation=owner&per_page=100"
    out: List[str] = []
    while url:
        r = _get(url)
        if r is None:
            break
        out += [repo["name"] for repo in r.json() if not repo.get("fork")]
        url = r.links.get("next", {}).get("url")
    return out


def fetch_traffic(kind: str, repo: str):
    r = _get(f"{API}/repos/{OWNER}/{repo}/traffic/{kind}",
             params={"per": "day"})
    if r is None:
        return []
    blob = r.json()
    return blob.get(kind) or blob.get("views") or blob.get("clones") or []


# ── DB + CSV helpers ─────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS traffic(
            date TEXT PRIMARY KEY,
            views INT, unique_views INT,
            clones INT, unique_clones INT
        )"""
    )
    return conn


def upsert(conn: sqlite3.Connection, day: str, s: Dict[str, int]):
    conn.execute(
        """INSERT INTO traffic VALUES (?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             views=excluded.views, unique_views=excluded.unique_views,
             clones=excluded.clones, unique_clones=excluded.unique_clones""",
        (day, s["views"], s["unique_views"], s["clones"], s["unique_clones"]),
    )
    conn.commit()


def write_csv(totals: Dict[str, Dict[str, int]]):
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Date", "DayOfWeek", "Views", "UniqueViews", "Clones", "UniqueClones"]
        )
        for d in sorted(totals):
            s = totals[d]
            dow = datetime.fromisoformat(d).strftime("%a")
            writer.writerow(
                [d, dow, s["views"], s["unique_views"], s["clones"], s["unique_clones"]])


# ── Rich helpers ─────────────────────────────────────────────────────────────
def print_report(summary: Dict[str, Dict[str, int]]) -> None:
    total_views = sum(v["views"] for v in summary.values())
    total_clones = sum(v["clones"] for v in summary.values())
    best_day_v = max(summary.items(), key=lambda x: x[1]["views"])
    best_day_c = max(summary.items(), key=lambda x: x[1]["clones"])

    table = Table(title="📊 GitHub Traffic Report", box=None)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold")

    table.add_row("Window", f"{LOOKBACK} days")
    table.add_row("Total views", f"{total_views:,}")
    table.add_row("Total clones", f"{total_clones:,}")
    table.add_row("Avg views/day", f"{total_views/LOOKBACK:.1f}")
    table.add_row("Avg clones/day", f"{total_clones/LOOKBACK:.1f}")
    table.add_row("Best view day",
                  f"{best_day_v[0]} ({best_day_v[1]['views']})")
    table.add_row("Best clone day",
                  f"{best_day_c[0]} ({best_day_c[1]['clones']})")
    console.print(table)


# ── Main fetch routine ───────────────────────────────────────────────────────
def fetch_daily():
    cutoff = (datetime.utcnow() - timedelta(days=LOOKBACK)).date().isoformat()
    totals: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    repos = list_repos()
    console.print(f"⏳ Pulling {LOOKBACK}-day traffic for {len(repos)} repos…")

    progress_cm = (
        Progress(
            SpinnerColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        )
        if not args.verbose
        else None
    )

    iterator = progress_cm.track(
        repos, description="Fetching") if progress_cm else repos
    if progress_cm:
        progress_cm.start()

    for repo in iterator:
        if args.verbose:
            console.print(f"• {repo}")
        for kind in ("views", "clones"):
            for e in fetch_traffic(kind, repo):
                day = e["timestamp"][:10]
                if day < cutoff:
                    continue
                t = totals[day]
                if kind == "views":
                    t["views"] += e["count"]
                    t["unique_views"] += e["uniques"]
                else:
                    t["clones"] += e["count"]
                    t["unique_clones"] += e["uniques"]

    if progress_cm:
        progress_cm.stop()

    conn = init_db()
    for d, stat in totals.items():
        upsert(conn, d, stat)

    write_csv(totals)
    console.print(f"✅ Saved → {CSV_PATH} & {DB_PATH}")
    print_report(totals)


# ── Dashboard (Flask) ────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def dashboard():
    rows = init_db().execute(
        "SELECT date, views, clones FROM traffic ORDER BY date"
    ).fetchall()
    dates, views, clones = zip(*rows) if rows else ([], [], [])
    return f"""
<!doctype html><html><head><meta charset="utf-8">
<title>{OWNER} Traffic</title><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>body{{font:16px/1.5 system-ui;margin:2rem}}</style></head><body>
<h1>{OWNER} <small style="font-size:0.6em">last {LOOKBACK} days</small></h1>
<canvas id="c"></canvas>
<script>
new Chart(c,{{
  type:'line',
  data:{{labels:{list(dates)},
        datasets:[{{label:'Views',data:{list(views)},borderColor:'royalblue',fill:false}},
                  {{label:'Clones',data:{list(clones)},borderColor:'seagreen',fill:false}}]}}
}});
</script></body></html>"""


def launch_dashboard():
    console.print("🌐  Dashboard → http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)


# ── Drill-downs ──────────────────────────────────────────────────────────────
def drill(which: str):
    repo = console.input("Repo name: ").strip()
    rows = (
        _get(f"{API}/repos/{OWNER}/{repo}/traffic/popular/{which}") or [])[:10]
    if not rows:
        console.print("ℹ️  No data.")
        return
    tab = Table(title=f"Top {which} – {repo}", box=None)
    tab.add_column(which.capitalize(), justify="left")
    tab.add_column("Hits", justify="right")
    tab.add_column("Unique", justify="right")
    for r in rows:
        tab.add_row(r[which[:-1]], str(r["count"]), str(r["uniques"]))
    console.print(tab)


# ── Menu ─────────────────────────────────────────────────────────────────────
def menu():
    while True:
        console.print(
            "\n[bold]1[/]) Fetch & report  "
            "[bold]2[/]) Referrers  "
            "[bold]3[/]) Paths  "
            "[bold]4[/]) Dashboard  "
            "[bold]5[/]) Rate-limit  "
            "[bold]6[/]) Quit"
        )
        choice = console.input("Select: ").strip()
        if choice == "1":
            fetch_daily()
        elif choice == "2":
            drill("referrers")
        elif choice == "3":
            drill("paths")
        elif choice == "4":
            threading.Thread(target=launch_dashboard, daemon=True).start()
        elif choice == "5":
            _get(f"{API}/rate_limit")
        elif choice == "6":
            break
        else:
            console.print("❓ Invalid choice")


if __name__ == "__main__":
    menu()
