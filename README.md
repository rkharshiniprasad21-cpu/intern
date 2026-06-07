# RemoteOK Automated Data Pipeline

> **Internship Screening Project** — Automated Data Collection System
> Fetches remote job listings from the RemoteOK public API and stores them in Supabase on a daily schedule using GitHub Actions.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Folder Structure](#3-folder-structure)
4. [Phase 1 — Source Analysis](#4-phase-1--source-analysis)
5. [Phase 2 — Data Collection Method Comparison](#5-phase-2--data-collection-method-comparison)
6. [Phase 3 — Data Collection Code](#6-phase-3--data-collection-code)
7. [Phase 4 — Data Storage](#7-phase-4--data-storage)
8. [Phase 5 — Automation](#8-phase-5--automation)
9. [Quick Start (Local Setup)](#9-quick-start-local-setup)
10. [Deployment Guide](#10-deployment-guide)
11. [Challenges and Solutions](#11-challenges-and-solutions)
12. [Future Improvements](#12-future-improvements)

---

## 1. Project Overview

This project is an **ETL (Extract → Transform → Load) pipeline** that:

| Step | What happens |
|------|-------------|
| **Extract** | Calls `https://remoteok.com/api` once per day |
| **Transform** | Strips HTML, normalises dates, deduplicates tags |
| **Load** | Upserts cleaned records into a Supabase PostgreSQL table |

The entire pipeline runs on **GitHub Actions** — zero servers, zero cost.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  GITHUB ACTIONS (cron)                  │
│   Triggers every day at 06:00 UTC                       │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐  │
│  │ scraper  │───▶│  main   │───▶│ supabase_client  │  │
│  │  .py     │    │  .py    │    │      .py          │  │
│  └──────────┘    └──────────┘    └──────────────────┘  │
│       │                                    │            │
│  RemoteOK API                       Supabase DB         │
│  (HTTP GET)                         (upsert)            │
└─────────────────────────────────────────────────────────┘
```

**Data flow in plain English:**
1. GitHub's scheduler triggers the workflow at 6 AM UTC.
2. `main.py` pings Supabase to confirm connectivity.
3. `scraper.py` fetches the full job list from RemoteOK, cleans every record.
4. `supabase_client.py` upserts records in batches of 100.
5. GitHub Actions logs the result; you get a ✓ or ✗ in your repository.

---

## 3. Folder Structure

```
remoteok-pipeline/
│
├── src/                        # All Python source code
│   ├── __init__.py             # Makes src/ a Python package
│   ├── config.py               # Centralised configuration (env vars)
│   ├── scraper.py              # RemoteOK API fetch + data cleaning
│   ├── supabase_client.py      # Supabase connection + upsert logic
│   └── main.py                 # Orchestrator — runs the full pipeline
│
├── tests/                      # Automated tests (pytest)
│   ├── __init__.py
│   └── test_scraper.py         # 15 unit tests for scraper functions
│
├── sql/
│   └── schema.sql              # CREATE TABLE + indexes + RLS policies
│
├── docs/
│   └── technical_documentation.md   # Deep-dive technical reference
│
├── .github/
│   └── workflows/
│       └── daily_pipeline.yml  # GitHub Actions YAML workflow
│
├── .env.example                # Template — copy to .env, fill in secrets
├── .gitignore                  # Excludes .env, __pycache__, etc.
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

---

## 4. Phase 1 — Source Analysis

### What is RemoteOK?

RemoteOK (`remoteok.com`) is a **remote-only job board** founded in 2015. It aggregates remote software engineering, design, marketing and management roles from companies worldwide and makes them searchable in one place.

### Why is it useful for recruitment / employment products?

| Use Case | Explanation |
|----------|-------------|
| **Talent matching** | Tag-based job data enables matching candidates to relevant roles by skill |
| **Market intelligence** | Track which technologies are most in-demand week-over-week |
| **Salary benchmarking** | RemoteOK includes salary ranges allowing compensation analysis |
| **Trend detection** | Monitor which companies are hiring remotely and at what volume |
| **Training data** | Cleaned job descriptions are training data for NLP / classification models |

### How does RemoteOK deliver data?

RemoteOK provides a **public, unauthenticated JSON API** at `https://remoteok.com/api`. A single HTTP GET returns the full current job list as a JSON array. There is no pagination — one request gives everything.

### Investigation process (how we discovered this)

1. **Opened** `remoteok.com` in Chrome DevTools → Network tab
2. **Observed** XHR calls — the page loads jobs via a single JSON endpoint
3. **Navigated** to `https://remoteok.com/api` directly in the browser
4. **Confirmed** the response is clean JSON with no auth headers required
5. **Checked** `robots.txt` at `remoteok.com/robots.txt` — API access is permitted
6. **Inspected** response structure to identify field names

### Tools used in investigation

| Tool | Purpose |
|------|---------|
| Chrome DevTools (Network tab) | Watch actual HTTP calls the page makes |
| `curl` | Test the API from the terminal: `curl https://remoteok.com/api` |
| `jq` | Pretty-print JSON: `curl https://remoteok.com/api \| jq '.[1]'` |
| Python `requests` | Prototype fetch in a Jupyter notebook |
| Postman / Insomnia | Optional — GUI for exploring API responses |

### APIs, cookies, authentication, sessions, dynamic loading

| Concept | RemoteOK behaviour |
|---------|-------------------|
| **API** | ✅ Public JSON API exists — no key needed |
| **Cookies** | ❌ Not required for API access |
| **Authentication** | ❌ None — fully public endpoint |
| **Sessions** | ❌ Stateless — each request is independent |
| **Dynamic loading** | ⚠️ The *website* uses JavaScript rendering, but the *API* returns complete data in one response, bypassing the need to execute JS |

---

## 5. Phase 2 — Data Collection Method Comparison

| Method | How it works | Pros | Cons | Score |
|--------|-------------|------|------|-------|
| **Web Scraping** | Parse the rendered HTML of `remoteok.com` using BeautifulSoup or similar | Works when no API exists | Fragile (breaks on HTML changes), potentially violates ToS, requires browser rendering | ⭐⭐ |
| **API Integration** | Call `remoteok.com/api` directly with `requests` | Fast, stable, structured data, officially supported | Requires an API to exist | ⭐⭐⭐⭐⭐ |
| **Browser Automation** | Drive a real browser with Playwright/Selenium to simulate a user | Handles heavy JS, can log in, click buttons | Slow, resource-intensive, high maintenance | ⭐⭐ |
| **Hybrid Approach** | Use the API where possible, fall back to scraping for missing fields | Best coverage | Highest complexity to maintain | ⭐⭐⭐ |

### ✅ Selected Method: API Integration

**Justification:**

1. **Stability** — The API format changes far less frequently than HTML structure.
2. **Efficiency** — One HTTP request returns all data. No DOM traversal, no JS execution.
3. **Respect** — Using the provided API is what the platform *intends* for programmatic access.
4. **Speed** — Fetching 500 jobs takes < 2 seconds vs 30+ seconds with browser automation.
5. **Reliability** — No risk of being blocked by bot-detection systems (Cloudflare, CAPTCHA).
6. **Data quality** — Structured JSON means no messy HTML parsing to extract values.

---

## 6. Phase 3 — Data Collection Code

The scraper (`src/scraper.py`) does five things:

### 6.1 HTTP fetch with retry

```python
response = requests.get(settings.API_URL, headers={"User-Agent": "..."}, timeout=30)
response.raise_for_status()
raw_data = response.json()
```

If this fails, exponential back-off retries up to 3 times (waits 1s, then 2s, then 4s).

### 6.2 Skip the legal notice

The first element of the API array is always a legal notice dict, not a job.
We filter it out by checking for the presence of `"id"`:

```python
jobs = [item for item in raw_data if isinstance(item, dict) and "id" in item]
```

### 6.3 Field extraction

For each raw job dict we extract:

| Field | Source key | Cleaning applied |
|-------|-----------|-----------------|
| `job_id` | `id` or `slug` | Stripped whitespace |
| `position` | `position` | Stripped whitespace |
| `company` | `company` | Stripped whitespace |
| `location` | `location` | Default `"Worldwide"` if empty |
| `tags` | `tags` | Lowercase, deduplicated list |
| `date_posted` | `epoch` | Unix int → ISO-8601 UTC string |
| `url` | `url` | Stripped whitespace |
| `description` | `description` | HTML stripped, capped at 5 000 chars |
| `salary` | `salary` | Stripped whitespace |
| `fetched_at` | *(generated)* | Current UTC timestamp |

### 6.4 HTML stripping

```python
from bs4 import BeautifulSoup
soup = BeautifulSoup(raw_html, "html.parser")
plain_text = soup.get_text(separator=" ").strip()
```

### 6.5 Error handling

Every `parse_job()` call is wrapped in `try/except`. Bad records are logged and skipped — they never crash the pipeline.

---

## 7. Phase 4 — Data Storage

### Supabase table schema (simplified)

```sql
CREATE TABLE public.remote_jobs (
    job_id      TEXT PRIMARY KEY,      -- RemoteOK unique ID
    position    TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT 'Worldwide',
    tags        TEXT[] NOT NULL DEFAULT '{}',   -- PostgreSQL array
    salary      TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    date_posted TIMESTAMPTZ,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Full schema with indexes, RLS policies and auto-update trigger: [`sql/schema.sql`](sql/schema.sql)

### Why upsert instead of insert?

```python
client.table("remote_jobs").upsert(records, on_conflict="job_id").execute()
```

`UPSERT` = INSERT if new, UPDATE if `job_id` already exists.
This means re-running the pipeline never creates duplicates, and changed fields (like salary) are updated automatically.

### Why batch writes?

Sending 500 records in one HTTP request can time out. We split into chunks of 100 (`BATCH_SIZE`) and call Supabase once per chunk. If one batch fails, the others still succeed.

---

## 8. Phase 5 — Automation

### How GitHub Actions works

1. You push this repository to GitHub.
2. GitHub reads `.github/workflows/daily_pipeline.yml`.
3. At 06:00 UTC every day, GitHub starts a fresh Ubuntu virtual machine.
4. The VM checks out your code, installs Python 3.12, installs requirements.
5. It runs `python -m src.main`.
6. The VM records success ✓ or failure ✗ and shuts down.
7. You see the result in the **Actions** tab of your repository.

### Cron schedule

```yaml
schedule:
  - cron: "0 6 * * *"
```

`0 6 * * *` = minute 0, hour 6, any day-of-month, any month, any day-of-week = **daily at 06:00 UTC**.

### Secrets

| Secret Name | Where to get it |
|-------------|----------------|
| `SUPABASE_URL` | Supabase Dashboard → Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase Dashboard → Settings → API → service_role secret |

Add them at: GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

---

## 9. Quick Start (Local Setup)

```bash
# 1. Clone the repo
git clone https://github.com/your-username/remoteok-pipeline.git
cd remoteok-pipeline

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp .env.example .env
# Open .env in your editor and fill in SUPABASE_URL and SUPABASE_KEY

# 5. Set up the Supabase table
# → Open Supabase Dashboard → SQL Editor → paste sql/schema.sql → Run

# 6. Run the pipeline
python -m src.main

# 7. Run the tests
pytest tests/ -v
```

---

## 10. Deployment Guide

See [`docs/technical_documentation.md`](docs/technical_documentation.md) for step-by-step deployment instructions.

**Summary:**
1. Create a free Supabase project at [supabase.com](https://supabase.com)
2. Run `sql/schema.sql` in the SQL Editor
3. Push this repository to GitHub
4. Add `SUPABASE_URL` and `SUPABASE_KEY` as GitHub Secrets
5. Push any commit to trigger a test run, or wait for the 06:00 UTC schedule

---

## 11. Challenges and Solutions

| Challenge | Solution |
|-----------|----------|
| RemoteOK returns HTML in description fields | Used BeautifulSoup to strip all tags before storage |
| First API array element is a legal notice, not a job | Filter by presence of `"id"` key |
| API may be temporarily unavailable | Exponential back-off retry (3 attempts: 1s, 2s, 4s) |
| No `User-Agent` header → 403 response | Send a realistic browser User-Agent string |
| Duplicate jobs accumulate across daily runs | Upsert on `job_id` — inserts new, updates existing |
| Large payloads may time out | Batch writes of 100 records per request |
| Supabase credentials must stay private | GitHub Secrets, `.env` excluded from git |
| Tags field is a Python list but needs PostgreSQL array | supabase-py v2 handles `list → TEXT[]` natively |

---

## 12. Future Improvements

| Improvement | Priority | Description |
|-------------|----------|-------------|
| **Slack / Email alerts** | High | Notify on pipeline failure via webhook |
| **Duplicate description detection** | Medium | Hash descriptions to skip re-processing unchanged jobs |
| **Multi-source ingestion** | Medium | Add We Work Remotely, LinkedIn, HN Who's Hiring |
| **Analytics dashboard** | Medium | Grafana or Metabase connected to Supabase for visualisation |
| **Tag normalisation** | Low | Map `"node.js"`, `"nodejs"`, `"node"` to a canonical form |
| **Full-text search API** | Low | Expose a FastAPI endpoint on top of Supabase's FTS index |
| **Data quality scoring** | Low | Flag jobs missing salary or description for manual review |

---

## License

MIT — free to use, modify and distribute.
