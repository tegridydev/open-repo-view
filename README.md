# open-repo-view (orv) | ~~tegridydev

**Open‑source** GitHub traffic insights for Codespaces, local dev, or CI

---

## 🚀 Whatisit

- 🔢 Interactive CLI (`python orv.py`)
- 📊 Fetch & aggregate **views** / **clones** for the last 14 days (all your repos)
- 📝 Instant text report (totals • averages • best days)
- 💾 Persist to **SQLite** *and*  **CSV**&#x20;
- 🌐 Flask + Chart.js dashboard
- 🚦 Show GitHub API rate‑limit
- 🔐 Fine‑grained PAT helper (shows missing permissions)

---

## ⚡ Quickstart


1. **Fork this repo** to your account.
2. In your fork, open **Settings ▸ Secrets & variables ▸ Codespaces ▸ New secret** and create `ORV_TOKEN` (leave the value blank for now).
3. In another tab, generate a Personal Access Token with either:
   - **Classic PAT** → tick `repo` scope, or
   - **Fine‑grained PAT** → enable **Repository ▸ Administration ▸ Read (Traffic)**.
     Copy the token and paste it into the `ORV_TOKEN` secret you created in step 2.
4. Back on the repo home page, click **Code ▸ Codespaces ▸ “Create codespace”** (or the badge above).\
   The dev‑container auto‑installs Python + deps.
5. When the Codespace is ready, run:
   ```bash
   python orv.py    # open the interactive menu
   ```
   Use `-v` for verbose per‑repo logging or choose option **4** to pop the dashboard.

---

## 🤖 Automate with GitHub Actions

```yaml
- run: |
    python orv.py
    git add github_traffic.csv traffic.db
    git commit -m "chore: update traffic" || echo "no changes"
    git push
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## 📡 GitHub REST API endpoints

```
GET /repos/{owner}/{repo}/traffic/views?per=day
GET /repos/{owner}/{repo}/traffic/clones?per=day
GET /repos/{owner}/{repo}/traffic/popular/referrers
GET /repos/{owner}/{repo}/traffic/popular/paths
GET /rate_limit
```

---

Built with ❤️ by [@tegridydev](https://github.com/tegridydev)

