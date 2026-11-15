# Dhurjati Sharma — AI-curated GitHub portfolio

A lightweight, animated portfolio for **[github.com/DHURJATI-JS](https://github.com/DHURJATI-JS)**.
An AI reads every repository's README, groups the project as **Social impact** or **Industry & startup**,
and writes a polished summary. Visitors open any project in an MDBootstrap modal (AI overview + the full README).

FastAPI · Jinja2 · MDBootstrap 9 (CDN) · Icons8 (3D icons + Line Awesome) · 7 languages · dark/light theme

---

## Quick start (Windows)

The virtual environment `.venv` is already created and filled (Python 3.12, same as Vercel).

```powershell
.venv\Scripts\python main.py
```

Open **http://127.0.0.1:8765** (if the port is busy the next free one is used and printed).
Admin panel: **http://127.0.0.1:8765/admin** — username/password are `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `.env`.

Re-create the venv from scratch if needed:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

---

## How it works

### One JSON file is the single source of truth
`data/portfolio.json` holds **everything** the site shows: repo details, sanitised README HTML,
AI groups + summaries, issues, the activity feed and your profile (bio, age, links…).
Public pages and the public API only read this file (cached in memory) — they never call GitHub or the AI,
so pages are fast and can't hit rate limits.

### The AI (reads each README)
1. **Primary:** your OpenAI-compatible gateway — `AI_PRIMARY_URL=http://192.168.0.226:8000/v1`, model `gemma`.
2. **Failsafe:** local Docker **Gemma 3 12B** (Ollama) — `AI_FALLBACK_URL=http://127.0.0.1:11434/v1`, model `gemma3:12b`.
3. If both are down, visitors see a keyword-based *preview*; it is **never saved**, so the repo stays “new”
   and the AI analyses it on the next run.

Answers are forced into a strict JSON schema (group, confidence, title, summary, problem, key points,
tech stack, audience). Once saved, a repo's group is **permanent**.

### Admin options (`/admin`)
| Option | What happens |
|---|---|
| **1 · Sync GitHub details** | Refresh repo details, READMEs, issues and activity. No AI, nothing re-grouped. |
| **2 · Categorise NEW repos only** *(recommended)* | Sync, then the AI reads only repos **not yet in the JSON**. Old groups stay. |
| **3 · Re-verify & re-categorise ALL** | Sync, then the AI re-reads every README (optionally overriding your manual choices). |
| Per repo | Change the group (locks it), **Featured**, **Hidden**, re-run AI, or “forget” the result (→ new again). |
| Profile | Name, headline, biography, age or birth date (age auto-updates), location, education, skills, links… |
| Data | Download the JSON. |

### Automation for new repositories
* **Self-hosted / local:** a background loop syncs GitHub and categorises *new* repos every
  `AUTO_SYNC_MINUTES` (default 60).
* **CLI** (no server needed): `.venv\Scripts\python scripts\sync.py` (new only) · `--all` · `--no-ai`.
* **Vercel:** a daily cron job calls `/api/cron/sync` (see below).

### Failsafe AI (Docker)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-fallback-ai.ps1
```
Starts Ollama (GPU if available, otherwise CPU) and downloads `gemma3:12b` once (~8 GB).
Start Docker Desktop first.

---

## ⚠️ Deploying to Vercel — read this first

1. **Import the repo in Vercel.** FastAPI is auto-detected from `main.py`; Python 3.12 comes from
   `.python-version`; dependencies from `requirements.txt` (only 4 small packages).
   `.vercelignore` keeps `.venv`, `.env` and local scripts out of the upload.
2. **Set environment variables** in *Project → Settings → Environment Variables* — `.env` is **not** deployed:
   `ADMIN_USERNAME`, `ADMIN_PASSWORD` (or `ADMIN_PASSWORD_HASH` from `scripts/hash_password.py`),
   `SECRET_KEY`, `GITHUB_TOKEN` (recommended), `CRON_SECRET`, plus anything else from `.env.example`.
3. **Vercel's disk is read-only.** Changes made in `/admin` on Vercel go to temporary storage and disappear.
   Pick one:
   * **Recommended:** categorise **locally** (where your gateway is reachable), then commit & push
     `data/portfolio.json` — `python scripts\sync.py` → `git add data/portfolio.json` → `git push`.
   * Or set `STORAGE_BACKEND=gist` + `GIST_ID` + `GIST_TOKEN` (a token with the *gist* scope) so admin
     changes are saved to a GitHub Gist and persist.
4. **Your AI gateway is a LAN address (192.168.0.226).** Vercel's servers can't reach it (or `localhost`).
   Run the AI locally as in step 3, or expose the gateway over HTTPS (e.g. Cloudflare Tunnel) and set
   `AI_PRIMARY_URL` on Vercel. Each AI request handles one repo, so it fits the 60 s function limit
   (`maxDuration` in `vercel.json`).
5. **Cron:** `vercel.json` calls `/api/cron/sync` daily at 03:00 UTC (the Hobby plan allows daily crons).
   It only runs when `CRON_SECRET` is set (Vercel sends it as a Bearer token). It syncs GitHub and
   categorises new repos if an AI endpoint is reachable.
6. **Rate limits** use in-memory counters per serverless instance. For one global limit, point
   `RATELIMIT_STORAGE_URI` at Redis (e.g. Upstash `rediss://…`) and add `redis` to `requirements.txt`.
7. **Static files** (`public/static`) are served by Vercel's CDN with long-term caching; URLs carry
   `?v=<commit>` so every deploy refreshes them.
8. **Library changes for Vercel:** no `httpx`/`requests` (stdlib `http.client`), no `python-dotenv` (built-in
   `.env` loader), no Markdown renderer (GitHub renders READMEs, `nh3` sanitises them), and `uvicorn` is
   only in `requirements-dev.txt`.
9. A GitHub token is strongly recommended: without one GitHub allows only 60 API requests/hour per IP,
   and Vercel IPs are shared.

---

## Security
* **Talisman-style middleware** (`portfolio/security.py`) — Flask-Talisman is Flask-only, so this is a
  FastAPI port of it: fresh **CSP nonce per request**, no `unsafe-inline`, `object-src 'none'`,
  `base-uri 'none'`, HSTS on HTTPS, `nosniff`, Referrer-Policy, Permissions-Policy, COOP.
* **iframes & CORS:** public pages may be embedded (`FRAME_ANCESTORS`, default `*`) and the public JSON API
  allows `CORS_ORIGINS` (default `*`, GET only, no credentials). `/admin` is never frameable.
* **Rate limiting** on every route with slowapi's `@limiter.limit(...)` (login: 5/minute, 30/hour).
* **Admin:** credentials from env, constant-time checks, optional PBKDF2 hash, HMAC-signed HttpOnly
  SameSite=Strict session cookie (changing the password logs everyone out), CSRF tokens + Origin check.
* **README/issue HTML** is sanitised with nh3 (scripts, event handlers, styles, iframes and `javascript:`
  links removed) and rendered under the strict CSP. MDB is loaded with Subresource Integrity hashes.

## Navbar extras
* **Live AI badge** — “● AI online / offline” next to the theme button. The server pings the gateway
  (then the Docker failsafe) via `/api/ai-status`, caches the answer for 45 s and never exposes internal
  IPs/URLs. The browser refreshes it every minute while the tab is visible.
* **Login** button — becomes **Dashboard** while you are signed in.

## Public JSON API
`/api/projects` · `/api/projects/{name}` (incl. README HTML) · `/api/profile` · `/api/profile-readme` ·
`/api/ai-status` · `/healthz`

## Project layout
```
main.py                 entrypoint (Vercel + local)
portfolio/              app: config, store (JSON), github sync, ai, security, i18n, views, routes
portfolio/templates/    Jinja2 pages (home, issues, disclaimer, admin)
public/static/          app.css, app.js, admin.js, icons.svg (Line Awesome by Icons8), favicon
data/portfolio.json     THE data file (commit it)
scripts/                sync.py (CLI), build_icons.py, hash_password.py, start-fallback-ai.ps1
```

## Auto-update on Vercel with a free Cloudflare tunnel

Vercel can't reach your LAN gateway (`192.168.0.226`), so a free **Cloudflare quick tunnel** gives it a
public `https://xxxx.trycloudflare.com` address. That address changes every time the tunnel restarts,
so the tunnel script writes the current one into the data Gist and the site reads it from there.

1. **Token:** GitHub → Settings → Developer settings → *Fine-grained tokens* → Generate:
   *Public repositories* (read-only) + Account permission **Gists: Read and write**. Put it in `.env` as `GITHUB_TOKEN`.
2. **Gist + Vercel variables:** `python scripts/setup_gist.py --vercel-env https://<your-site>.vercel.app`
   creates a secret Gist with `data/portfolio.json`, saves `GIST_ID` to `.env` and writes `.env.vercel`.
   Paste `.env.vercel` into Vercel → *Settings → Environment Variables* (paste into the Key field to import all),
   redeploy, then delete `.env.vercel`.
3. **Tunnel (on a PC on the same network as the gateway):**
   `winget install --id Cloudflare.cloudflared`, then
   `powershell -ExecutionPolicy Bypass -File scripts\install-tunnel-task.ps1` — starts
   `scripts\start-tunnel.ps1` now and at every logon. It restarts cloudflared if it stops, republishes the new URL,
   and asks the site to sync + categorise **new** repos every `AUTO_SYNC_MINUTES` (Vercel's own cron also runs daily).
   Log: `%LOCALAPPDATA%\portfolio-tunnel\tunnel.log`. Remove with `install-tunnel-task.ps1 -Remove`.

Quick tunnels are meant for testing (no uptime guarantee, ~200 requests in flight). The AI only works while that
PC and the gateway are on; otherwise new repos show a keyword preview and get categorised on the next run.
Set a real `AI_PRIMARY_KEY` on your gateway — the tunnel URL is public.

## Customising
* **Languages:** English, हिन्दी, मराठी, Español, Français, Deutsch, 日本語 — edit/add in `portfolio/i18n.py`.
* **Icons:** add a Line Awesome name to `scripts/build_icons.py` and re-run it; 3D icons come from
  `img.icons8.com/3d-fluency`. Icons8 attribution is in the footer — keep it (free-licence requirement).
* **Look & feel:** colour tokens at the top of `public/static/app.css` (dark and light themes).

## Disclaimer
Every project is provided “as is”, without warranty of any kind, and without liability or responsibility —
see `/disclaimer` on the site. The disclaimer text is a general template, not legal advice.

## License
Released under the [MIT License](LICENSE) © 2026 DHURJATI-JS.
Third-party assets (Icons8 3D icons, Line Awesome, MDBootstrap via CDN) remain under their own licences.
