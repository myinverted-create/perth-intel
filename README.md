# WA Commercial Construction — Recruitment Intelligence Dashboard

A self-updating private dashboard that pulls daily commercial-construction lead intelligence
for Western Australia using **xAI Grok** with live web search.

- Runs every weekday at 6am AWST via GitHub Actions
- Free hosting on GitHub Pages
- Real shareable URL — no app to deploy, no server to maintain
- Per-viewer "Mark contacted" state via browser localStorage
- Full archive of every brief, browseable in the UI
- Watchlist of named WA commercial builders that Grok specifically tracks

## Cost

| Item | Cost |
|---|---|
| GitHub Actions | Free (public or private repo within free tier) |
| GitHub Pages | Free |
| xAI Grok API | ~$1–4/month for one daily brief |
| **Total** | **~$1–4/month** |

---

## 5-Minute Setup

### 1. Create a new GitHub repo

1. Go to <https://github.com/new>
2. Name it whatever you like — e.g. `wa-recruit-intel`. **Public** is fine (the data is not sensitive and the URL is the obscure GitHub Pages one). Choose **Private** if you prefer; GitHub Pages on private repos requires a paid plan, so make it public unless you've upgraded.
3. Don't initialize with anything — leave it empty.

### 2. Push these files to the repo

From this folder:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

(If you'd rather not use the command line: drag-and-drop these files into the repo via the GitHub web upload at `https://github.com/YOUR_USERNAME/YOUR_REPO/upload/main`.)

### 3. Add your xAI API key as a secret

1. Get your key at <https://console.x.ai/> → API Keys
2. In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**
3. Name: `XAI_API_KEY` · Value: paste your key
4. Save

### 4. Enable GitHub Pages

1. Repo **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` · Folder: `/ (root)`
4. Save. Wait ~1 minute. Your URL appears at the top of the Pages settings page — typically `https://YOUR_USERNAME.github.io/YOUR_REPO/`.

### 5. Run the brief for the first time

1. Repo **Actions** tab → **Daily WA Commercial Construction Brief** workflow
2. Click **Run workflow** → **Run workflow** (this triggers it manually)
3. Wait ~1 minute. When the run is green, the dashboard will have data.
4. Visit your GitHub Pages URL. Done.

> **Note on sample data:** The repo ships with realistic *sample* data in `data/latest.json` so you can preview the dashboard immediately after deploying — without waiting for the first run. The first workflow run overwrites it with real Grok output. If you want a clean slate before pushing, replace `data/latest.json` with `{"date":"","top_3_today":[],"hot_projects":[],"hiring_signals":[],"watchlist_status":[]}`.

---

## Sharing the URL

Send anyone the GitHub Pages URL — they don't need a GitHub account, no signup, no password. The dashboard is read-only and the URL is unguessable. The HTML includes `<meta name="robots" content="noindex,nofollow">` so search engines won't crawl it.

If you ever need to rotate the URL (e.g. it leaks), rename the repo. The new URL will be different and the old one will 404.

If you need stronger access control later, the cleanest paid upgrade is Cloudflare Pages with Cloudflare Access (free for up to 50 users) sitting in front of the same files.

---

## Daily Operation

- **6am AWST Mon–Fri**: GitHub Action fires automatically. Grok generates the brief, the action commits the new data, GitHub Pages serves it within seconds.
- **You open the URL**: see Today's Brief with Top 3, Hot Projects, Hiring Signals.
- **You take action**: click "Mark contacted" on each card after reaching out. State is stored in your browser, persists across reloads.
- **Browse the archive**: the Archive tab shows every prior brief. Useful for spotting patterns ("Multiplex has been hot 5 mornings in a row → real hiring push").

### Manually re-running the brief

Actions tab → workflow → **Run workflow**. Useful if you want a fresh pull mid-day.

### Editing the watchlist

Edit `data/watchlist.json` and push the change. The next run will use the new list. Recommended size: 15–30 builders. Fewer is too narrow; more eats Grok tokens with diminishing return.

### Tuning the prompt

The intelligence prompt lives in `prompts/daily_brief.md`. Edit and push. The next run picks it up automatically.

---

## Troubleshooting

**The action runs but the dashboard shows "No brief yet"**
→ Check the Actions log. Most common cause: `XAI_API_KEY` secret not set, or the model name is wrong. xAI occasionally renames models — you can override via repo **Settings → Secrets and variables → Actions → Variables → New variable** named `XAI_MODEL` (e.g. `grok-2-latest`).

**Dashboard shows "⚠ No live search" / "Live web search unavailable"**
→ xAI changed their Live Search schema. The script tries three modes in order:

1. `search_parameters` block (current xAI Live Search standard)
2. `tools=[{"type":"live_search"}]` (agent-tools form)
3. No live search (training data only — flagged in dashboard)

If all three fail, check the Actions log for the exact 422 error from xAI, then override with one of these repo Variables (Settings → Secrets and variables → Actions → Variables):

- `XAI_SEARCH_PARAMETERS_JSON` — full JSON for the `search_parameters` block, e.g. `{"mode":"on","sources":[{"type":"web"}]}`
- `XAI_TOOLS_JSON` — full JSON for the `tools` array, e.g. `[{"type":"live_search"}]`

**The Action says "Permission denied" when committing**
→ Repo **Settings → Actions → General → Workflow permissions** → set to **Read and write permissions** → Save. Re-run the workflow.

**Dashboard layout is broken**
→ Open browser dev tools (F12) and check the console. Most likely the JSON wasn't well-formed. Re-running the workflow normally fixes it.

**Costs creeping up**
→ Each daily run is one Grok call with live search. If costs feel off, check usage at <https://console.x.ai/>. To reduce: switch to a cheaper model via `XAI_MODEL`, or change the cron in `.github/workflows/daily-brief.yml` to run fewer days.

---

## File Layout

```
wa-recruit-intel/
├── index.html                      ← the dashboard (single page)
├── README.md                       ← this file
├── .github/workflows/
│   └── daily-brief.yml             ← GitHub Actions cron job
├── scripts/
│   └── fetch_brief.py              ← calls Grok, writes JSON
├── prompts/
│   └── daily_brief.md              ← the Grok prompt template
└── data/
    ├── latest.json                 ← today's brief
    ├── watchlist.json              ← YOUR watchlist of builders ← edit this
    ├── history-index.json          ← list of all archived dates
    └── history/
        └── YYYY-MM-DD.json         ← archived briefs (one per day)
```

---

## What to do in week one

1. Push these files, run the workflow once, confirm the dashboard renders.
2. Edit `data/watchlist.json` to match the WA commercial builders YOU recruit for. Push.
3. Run the workflow again. Confirm those builders appear in the Watchlist tab.
4. Use the dashboard each morning. After 5 days you'll have spotted what's noisy and what's gold.
5. Tune `prompts/daily_brief.md` based on what you wish was sharper. Common edits: ask Grok to explicitly include LinkedIn URLs of likely contacts, or add a "Don't bother with" exclusion list of builders you've already covered.

---

## Future enhancements (when you're ready)

- **Slack alert** for the Top 3 each morning — add a step to the GitHub Action that posts to a Slack webhook.
- **Email digest** to teammates — same idea via SendGrid free tier or just SMTP through GitHub Actions.
- **Custom domain** (e.g. `intel.yourbusiness.com`) — pointed at the GitHub Pages URL via a CNAME.
- **Cloudflare Access** in front for proper SSO-protected sharing if you ever need it.
- **Multi-region** — clone the repo for QLD/NSW/VIC variants with different watchlists.
