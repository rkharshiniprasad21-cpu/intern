# Technical Documentation
## RemoteOK Automated Data Pipeline

**Version:** 1.0.0
**Author:** Data Engineering Internship Candidate
**Stack:** Python 3.12 · Supabase (PostgreSQL) · GitHub Actions

---

## Table of Contents

1. [System Design Overview](#1-system-design-overview)
2. [Module Reference](#2-module-reference)
3. [Database Schema Deep Dive](#3-database-schema-deep-dive)
4. [API Response Contract](#4-api-response-contract)
5. [Data Cleaning Pipeline](#5-data-cleaning-pipeline)
6. [Error Handling Strategy](#6-error-handling-strategy)
7. [Automation Deep Dive](#7-automation-deep-dive)
8. [Security Considerations](#8-security-considerations)
9. [Performance Characteristics](#9-performance-characteristics)
10. [Testing Strategy](#10-testing-strategy)
11. [Deployment Step-by-Step](#11-deployment-step-by-step)
12. [Interview Preparation Notes](#12-interview-preparation-notes)

---

## 1. System Design Overview

### Design Principles

This system was built around three core engineering principles:

**1. Separation of Concerns**
Each Python module has exactly one responsibility:
- `config.py` → configuration only
- `scraper.py` → data collection only
- `supabase_client.py` → storage only
- `main.py` → orchestration only

This means a change to the RemoteOK API format only requires editing `scraper.py`. A change to the database schema only requires editing `supabase_client.py`.

**2. Fail-Safe Design**
The pipeline never raises an unhandled exception. Every operation that can fail (HTTP requests, JSON parsing, database writes) is wrapped in try/except with appropriate logging. The worst outcome is a logged warning — not a crashed GitHub Actions run with an unintelligible error.

**3. Idempotency**
Running the pipeline twice with the same source data produces the same database state. Upsert-on-conflict ensures this. You can safely re-run after a failure without worrying about duplicate records.

### ETL Architecture

```
EXTRACT                 TRANSFORM                LOAD
─────────────────────   ──────────────────────   ────────────────────
RemoteOK JSON API  →   parse_job():              Supabase upsert
                        • strip HTML              on_conflict=job_id
Single GET request      • normalise whitespace
returns ~500 jobs       • convert epoch→ISO
                        • lowercase tags
                        • default location
                        • cap description
```

---

## 2. Module Reference

### `src/config.py`

**Pattern:** Singleton Settings object using pydantic-settings v2.

**Why pydantic-settings?**
Plain `os.environ.get("SUPABASE_URL")` returns `None` silently if the variable is missing. Pydantic raises a `ValidationError` at startup with a clear message listing exactly which variables are absent. This saves hours of debugging.

**Key attributes:**

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `API_URL` | str | `https://remoteok.com/api` | RemoteOK endpoint |
| `USER_AGENT` | str | Browser-like string | Prevents 403 responses |
| `REQUEST_TIMEOUT` | int | 30 | Seconds before abandoning request |
| `MAX_RETRIES` | int | 3 | Retry attempts on failure |
| `SUPABASE_URL` | str | **required** | Project URL |
| `SUPABASE_KEY` | str | **required** | service_role key |
| `SUPABASE_TABLE` | str | `remote_jobs` | Target table name |
| `LOG_LEVEL` | str | `INFO` | Logging verbosity |
| `BATCH_SIZE` | int | 100 | Records per upsert call |

---

### `src/scraper.py`

**Public API:** One function → `collect_jobs() → list[dict]`

**Internal helpers:**

```
fetch_jobs()     → calls HTTP, returns raw list[dict]
parse_job()      → transforms one raw dict into clean dict
_strip_html()    → removes HTML tags from strings
_clean_text()    → normalises whitespace
_parse_epoch()   → Unix int → ISO-8601 UTC string
_parse_tags()    → list[any] → deduplicated lowercase list[str]
```

**The single underscore prefix** on helper functions (`_strip_html` etc.) is a Python convention meaning "private — don't import this from outside the module." It is not enforced by the language but communicates intent to other developers.

---

### `src/supabase_client.py`

**Public API:**
- `ping_supabase() → bool` — health check
- `upsert_jobs(jobs: list[dict]) → dict` — main write operation

**The upsert call explained:**

```python
client.table("remote_jobs")
      .upsert(records, on_conflict="job_id")
      .execute()
```

- `.table("remote_jobs")` → selects which table to write to
- `.upsert(records, ...)` → sends the list of dicts as JSON body
- `on_conflict="job_id"` → maps to PostgreSQL `ON CONFLICT (job_id) DO UPDATE`
- `.execute()` → fires the HTTP request and returns the response

**Supabase client authentication:**
The `create_client(url, key)` function authenticates every request by attaching the key as an `apikey` and `Authorization: Bearer <key>` header automatically. You never manage tokens manually.

---

### `src/main.py`

**Responsibility:** Orchestrate the three phases and exit with the correct code.

**Exit codes:**
- `0` → pipeline succeeded (GitHub Actions marks step ✓)
- `1` → pipeline failed (GitHub Actions marks step ✗, sends notification if configured)

**Failure threshold:** If > 50% of records fail to upsert, the run is marked failed. This threshold is a business decision — if even one record fails you might want `failed > 0`. 50% is a reasonable starting point that tolerates a few bad records without false alarms.

---

## 3. Database Schema Deep Dive

### Column type choices

| Column | PostgreSQL Type | Reasoning |
|--------|----------------|-----------|
| `job_id` | `TEXT` | RemoteOK IDs are string slugs like `"104932-senior-python"` |
| `tags` | `TEXT[]` | Native array enables `@>` contains queries without JSON parsing |
| `date_posted` | `TIMESTAMPTZ` | Timezone-aware — all values stored as UTC, displayed in user's TZ |
| `fetched_at` | `TIMESTAMPTZ DEFAULT NOW()` | Automatic server-side timestamp on insert |
| `description` | `TEXT` | Unlimited length; we cap at 5000 chars in Python before inserting |

### Index strategy

```sql
-- B-tree index for equality/range on company name
CREATE INDEX idx_remote_jobs_company ON remote_jobs (company);

-- B-tree index for date sorting (DESC = newest first)
CREATE INDEX idx_remote_jobs_date_posted ON remote_jobs (date_posted DESC);

-- GIN index for array containment queries
-- Enables: WHERE tags @> '{python}' (fast even with 100k rows)
CREATE INDEX idx_remote_jobs_tags ON remote_jobs USING GIN (tags);

-- GIN full-text search index
-- Enables: WHERE to_tsvector('english', position || ' ' || company) @@ plainto_tsquery('engineer')
CREATE INDEX idx_remote_jobs_fts ON remote_jobs
    USING GIN (to_tsvector('english', position || ' ' || company));
```

**Why GIN for arrays?**
GIN (Generalised Inverted Index) is optimised for columns containing multiple values (arrays, JSONB, text vectors). A regular B-tree index on an array column would not accelerate `@>` queries.

### Row Level Security

```
anonymous role  → SELECT only (public job data is safe to read)
service_role    → ALL operations (our pipeline uses this)
```

This design allows you to later build a public-facing search API using the anonymous key without any risk of data modification.

---

## 4. API Response Contract

**Endpoint:** `GET https://remoteok.com/api`
**Authentication:** None
**Rate limit:** Not officially documented; ~1 req/s is polite

**Response structure:**

```json
[
  {
    "legal": "The data is provided for ...",
    "apiVersion": "..."
  },
  {
    "id": "104932",
    "slug": "104932-senior-python-developer-acme",
    "company": "Acme Corp",
    "company_logo": "https://remoteok.com/assets/img/jobs/...",
    "position": "Senior Python Developer",
    "tags": ["python", "remote", "backend", "django"],
    "description": "<p>We are looking for ...</p>",
    "location": "Worldwide",
    "salary": "$100k - $140k",
    "url": "https://remoteok.com/remote-jobs/104932-...",
    "epoch": 1718000000,
    "date": "2024-06-10T00:00:00+00:00"
  }
]
```

**Key observations:**
- Element `[0]` is always the legal notice — skipped by checking for `"id"` key
- `epoch` (Unix int) is more reliable than `date` (sometimes missing or malformed)
- `description` always contains HTML markup — must be stripped
- `location` can be `null`, `""`, or a genuine location — default to `"Worldwide"`
- `tags` can be `null` or an empty array — handled gracefully

---

## 5. Data Cleaning Pipeline

Every raw record goes through this transformation chain:

```
raw API dict
    │
    ▼
parse_job()
    │
    ├── job_id   = _clean_text(raw["id"])
    │                  └── str(value).strip() + collapse whitespace
    │
    ├── position = _clean_text(raw["position"])
    │
    ├── company  = _clean_text(raw["company"])
    │
    ├── location = _clean_text(raw["location"]) or "Worldwide"
    │
    ├── tags     = _parse_tags(raw["tags"])
    │                  ├── filter non-strings
    │                  ├── lowercase each tag
    │                  └── deduplicate preserving order
    │
    ├── date_posted = _parse_epoch(raw["epoch"])
    │                    └── datetime.fromtimestamp(ts, tz=UTC).isoformat()
    │
    ├── url      = _clean_text(raw["url"])
    │
    ├── description = _strip_html(raw["description"])[:5000]
    │                    └── BeautifulSoup → get_text()
    │
    ├── salary   = _clean_text(raw["salary"])
    │
    └── fetched_at = datetime.now(UTC).isoformat()   ← generated, not from API
```

---

## 6. Error Handling Strategy

### Three levels of error handling

**Level 1 — Field level** (`parse_job`)
Individual field parsing failures are caught and logged as DEBUG. The record is still returned with a default/empty value for that field.

**Level 2 — Record level** (`collect_jobs`)
If an entire record fails to parse, it is logged as WARNING and skipped. The pipeline continues with all other records.

**Level 3 — Batch level** (`upsert_jobs`)
If a Supabase batch fails, the error is logged, the failure counter increments, and the next batch is attempted. One bad batch never kills the entire run.

**Level 4 — Run level** (`main.py`)
If > 50% of records fail, the run exits with code 1, signalling failure to GitHub Actions. If < 50% fail, the run exits 0 — partial success is still success.

### Retry logic

HTTP retries use **exponential back-off**:

```
Attempt 1 → fails → wait 1s  (2^0)
Attempt 2 → fails → wait 2s  (2^1)
Attempt 3 → fails → pipeline gives up
```

This is the industry-standard approach because:
- Immediate retries during an overload event make the situation worse
- Exponential spacing gives the upstream service time to recover
- 3 attempts covers almost all transient network blips

---

## 7. Automation Deep Dive

### GitHub Actions execution model

```
GitHub scheduler
    → triggers workflow at cron time
    → GitHub allocates a fresh ubuntu-latest runner (VM)
    → Runner executes steps sequentially
    → Runner uploads artifacts if configured
    → Runner shuts down (all state is lost)
    → Results visible in repo → Actions tab
```

**Key points:**
- Each run starts from a completely clean environment
- There is no state between runs — the database IS the persistent state
- Concurrency guard (`concurrency: group: remoteok-pipeline`) prevents two simultaneous runs from racing to write the database

### Caching

```yaml
cache: "pip"
cache-dependency-path: requirements.txt
```

GitHub caches the pip download cache keyed by the hash of `requirements.txt`. If dependencies haven't changed, install time drops from ~30s to ~5s.

### Manual trigger

`workflow_dispatch` allows clicking "Run workflow" in the GitHub UI. The `log_level` input lets you get DEBUG output without editing code.

---

## 8. Security Considerations

### What we protect

| Secret | Risk if exposed | Protection |
|--------|----------------|------------|
| `SUPABASE_KEY` (service_role) | Full database read/write by anyone | GitHub Secrets, never in code |
| Supabase URL | Low risk alone (needs key too) | GitHub Secrets |

### What we do NOT do

- ❌ Log credential values (even in DEBUG mode)
- ❌ Commit `.env` to git (`.gitignore` excludes it)
- ❌ Use `anon` key on the server (service_role bypasses RLS correctly)
- ❌ Expose the service_role key in any frontend code

### What the service_role key can do

The `service_role` key bypasses Row Level Security. This is intentional for a server-side pipeline (we need to write regardless of RLS policies). **Never** use this key in a browser or mobile app.

---

## 9. Performance Characteristics

### Typical run metrics

| Metric | Typical value |
|--------|--------------|
| API response time | 1–3 seconds |
| Records returned | 400–600 jobs |
| Parse time (all records) | < 0.1 seconds |
| Supabase upsert (100 records/batch) | 0.5–1.5 seconds/batch |
| Total pipeline wall time | 8–15 seconds |
| GitHub Actions total (incl. setup) | 60–90 seconds |

### Memory footprint

The entire job list (~600 records × ~2 KB each) = ~1.2 MB in memory. Well within the 7 GB available on GitHub's ubuntu-latest runner. No streaming or chunked processing is needed at this scale.

### When to add streaming

If the job count exceeds ~50,000 records, consider:
1. Using `ijson` for streaming JSON parsing instead of loading the full response
2. Increasing `BATCH_SIZE` to 500 to reduce round trips
3. Using async writes with `asyncio` + `aiohttp`

---

## 10. Testing Strategy

### Test categories

**Unit tests** (`tests/test_scraper.py`)
Test individual pure functions in isolation. No HTTP calls, no database.
- 15 tests covering all helper functions and `parse_job`
- Uses the `responses` library to mock HTTP for `collect_jobs`

**Running tests:**

```bash
# Basic run
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=src --cov-report=term-missing

# Run a single test
pytest tests/test_scraper.py::TestParseJob::test_valid_record -v
```

### Coverage targets

| Module | Target |
|--------|--------|
| `scraper.py` | 90%+ |
| `config.py` | 70%+ (hard to test env var loading) |
| `supabase_client.py` | 60%+ (requires mocking supabase-py) |
| `main.py` | 50%+ |

### What we do NOT test

- The Supabase connection itself (integration test, needs real credentials)
- The GitHub Actions workflow YAML (tested by pushing and observing)
- The real RemoteOK API (can change at any time — not under our control)

---

## 11. Deployment Step-by-Step

### Step 1: Create Supabase project

1. Go to [supabase.com](https://supabase.com) → **New Project**
2. Choose a name (e.g. `remoteok-pipeline`) and a strong database password
3. Select the region closest to your GitHub Actions runner (US East is a good default)
4. Wait ~2 minutes for provisioning

### Step 2: Run the SQL schema

1. In your Supabase project → **SQL Editor** → **New query**
2. Paste the entire contents of `sql/schema.sql`
3. Click **Run** (green button)
4. Confirm: `SELECT COUNT(*) FROM remote_jobs;` returns `0`

### Step 3: Get your credentials

1. Supabase Dashboard → **Settings** → **API**
2. Copy **Project URL** → this is `SUPABASE_URL`
3. Under **Project API Keys** copy **service_role** (click reveal) → this is `SUPABASE_KEY`

### Step 4: Push to GitHub

```bash
git init
git add .
git commit -m "feat: initial RemoteOK pipeline"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/remoteok-pipeline.git
git push -u origin main
```

### Step 5: Add GitHub Secrets

1. GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add `SUPABASE_URL` with your Supabase project URL
4. Add `SUPABASE_KEY` with your service_role key

### Step 6: Test the workflow

1. GitHub repo → **Actions** → **RemoteOK Daily Pipeline**
2. Click **Run workflow** → **Run workflow**
3. Click the running job to watch live logs
4. Confirm it ends with ✓ (green checkmark)

### Step 7: Verify data in Supabase

1. Supabase Dashboard → **Table Editor** → `remote_jobs`
2. You should see hundreds of rows
3. Try: `SELECT COUNT(*), MAX(fetched_at) FROM remote_jobs;`

---

## 12. Interview Preparation Notes

### "What is an ETL pipeline?"

**Answer:** ETL stands for Extract, Transform, Load. Extract means retrieving raw data from a source (here: the RemoteOK API). Transform means cleaning and restructuring the data into a consistent format (stripping HTML, converting timestamps, normalising tags). Load means writing the cleaned data to a destination store (here: Supabase/PostgreSQL). This project implements a complete ETL pipeline in about 200 lines of Python.

### "Why did you choose API integration over web scraping?"

**Answer:** RemoteOK provides a public, unauthenticated JSON API. Using it is more reliable than scraping HTML because: (1) the API format is stable — the HTML layout changes with every redesign; (2) it is faster — one request vs hundreds of page requests; (3) it is respectful — the API is the intended programmatic access method; (4) it avoids anti-bot measures like CAPTCHAs or IP blocking.

### "What is upsert and why did you use it?"

**Answer:** Upsert is a combination of INSERT and UPDATE — "insert if new, update if exists." I used it because the pipeline runs daily and many jobs remain active for weeks. Without upsert, re-running would either fail with a primary key violation or create duplicate records. With upsert on `job_id`, we always have exactly one row per job, and any field changes (like salary updates) are automatically reflected.

### "How does GitHub Actions scheduling work?"

**Answer:** GitHub Actions uses cron syntax in the workflow YAML to define a schedule. `0 6 * * *` means "at minute 0 of hour 6, every day." GitHub's infrastructure runs the workflow at that time on a fresh virtual machine — no server to maintain. The credentials are stored as encrypted GitHub Secrets, injected as environment variables at runtime.

### "What is Row Level Security?"

**Answer:** RLS is a PostgreSQL feature that lets you define which rows each database role can see or modify. I enabled it so that unauthenticated users (using the `anon` key) can only read data — they cannot insert, update or delete. The pipeline uses the `service_role` key, which bypasses RLS and has full access. This means if I later build a public-facing search API, I can safely use the `anon` key without worrying about data corruption.

### "What would you do if the RemoteOK API went down?"

**Answer:** The current retry logic (3 attempts with exponential back-off) handles brief outages. For a production system I would: (1) Add a dead-letter notification (Slack/email) when all retries fail; (2) Cache the last successful response so analysis can continue even without fresh data; (3) Consider a secondary source (We Work Remotely, LinkedIn) as a fallback; (4) Add a data freshness metric — alert if `MAX(fetched_at)` is more than 25 hours old.

### "How would you scale this to 10 million records?"

**Answer:** Current design works for ~600 records/day. At scale: (1) Switch to async HTTP with `aiohttp` for parallel fetches from multiple sources; (2) Use PostgreSQL COPY command instead of REST upserts for bulk loads; (3) Add partitioning to the table (partition by month on `date_posted`); (4) Use a message queue (Redis/SQS) between scrape and store so both can scale independently; (5) Consider a columnar store (BigQuery, Redshift) for analytics queries.
