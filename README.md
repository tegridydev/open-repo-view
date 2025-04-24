# open-repo-view (orv)

**Minimalist**, secure, and interactive GitHub traffic insights—designed for Codespaces and GitHub Actions.

---

## 🚀 Features

- 🔢 Interactive CLI with numbered menu
- 📊 Fetch daily traffic (views/clones) for the last 14 days
- 🔍 Analyze top referrers and most popular paths
- 💾 Persist data to both SQLite and CSV
- 🚦 Display GitHub API rate-limit status
- 🌐 Launch a lightweight Flask dashboard (Chart.js-powered)

---

## ⚡ Quickstart

### 1. Clone & Launch in Codespaces
```bash
gh repo clone tegridydev/open-repo-view
gh codespace create --repo tegridydev/open-repo-view
gh codespace open
```

### 2. Set Up Token
Codespaces auto-prompts GITHUB_TOKEN permission. For local dev:
```bash
export GITHUB_TOKEN=your_token_here
```
Or create a `.env` file with:
```env
GITHUB_TOKEN=your_token_here
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Tool
```bash
python orv.py
```

Menu Options:
```
1: Fetch & save traffic data (CSV + DB)
2: View top referrers
3: View popular paths
4: Launch dashboard (http://localhost:5000)
5: Show rate-limit status
6: Quit
```

---

## 🤖 Automate with GitHub Actions
Use GitHub's built-in token—no secrets required:
```yaml
- run: |
    python orv.py
    git add github_traffic.csv traffic.db
    git commit -m "update traffic"
    git push
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## 📡 GitHub REST API Reference

- `GET /repos/{owner}/{repo}/traffic/views?per=day|week`
- `GET /repos/{owner}/{repo}/traffic/clones?per=day|week`
- `GET /repos/{owner}/{repo}/traffic/popular/referrers`
- `GET /repos/{owner}/{repo}/traffic/popular/paths`
- `GET /rate_limit` — to monitor API usage

---

Built with ❤️ for devs who love simple, transparent tooling.

> Maintained by [@tegridydev](https://github.com/tegridydev)