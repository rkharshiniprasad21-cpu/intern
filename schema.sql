-- ============================================================
-- sql/schema.sql
-- ============================================================
-- Run this ONCE in the Supabase SQL Editor to set up your table.
-- Path: Dashboard → SQL Editor → New query → paste → Run
--
-- What this script does:
--   1. Creates the `remote_jobs` table with the exact columns our
--      Python code writes.
--   2. Adds indexes for the columns we will filter / sort by most.
--   3. Enables Row Level Security (RLS) and adds a policy so that
--      only authenticated service-role calls can write data, while
--      the public can read (useful if you build a frontend later).
--   4. Creates a helper view that shows today's jobs for quick checks.
-- ============================================================


-- ------------------------------------------------------------
-- 1. MAIN TABLE
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.remote_jobs (

    -- Primary key: RemoteOK's own identifier (string slug like "104932")
    -- Using the source's ID means upsert naturally deduplicates.
    job_id          TEXT            PRIMARY KEY,

    -- Core job fields
    position        TEXT            NOT NULL,
    company         TEXT            NOT NULL,
    location        TEXT            NOT NULL DEFAULT 'Worldwide',
    salary          TEXT            NOT NULL DEFAULT '',

    -- tags is a PostgreSQL text array, e.g. '{python,remote,backend}'
    -- Stored as TEXT[] instead of JSONB because it's simpler to query
    -- with the @> (contains) operator:
    --   SELECT * FROM remote_jobs WHERE tags @> '{python}';
    tags            TEXT[]          NOT NULL DEFAULT '{}',

    -- Full plain-text description (HTML stripped by Python)
    description     TEXT            NOT NULL DEFAULT '',

    -- Direct link to the job on RemoteOK
    url             TEXT            NOT NULL DEFAULT '',

    -- Timestamps
    -- date_posted: when the job was posted on RemoteOK (from API epoch)
    date_posted     TIMESTAMPTZ,

    -- fetched_at: when OUR pipeline retrieved this record
    fetched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- updated_at: automatically updated by a trigger (see below)
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()

);

-- Human-readable comment visible in the Supabase dashboard
COMMENT ON TABLE public.remote_jobs IS
    'Remote job listings collected daily from the RemoteOK public API.';


-- ------------------------------------------------------------
-- 2. AUTO-UPDATE updated_at TRIGGER
-- ------------------------------------------------------------
-- PostgreSQL does not update columns automatically on UPDATE.
-- This trigger fires BEFORE every update and sets updated_at to NOW().

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- Drop if exists first so re-running the script is idempotent
DROP TRIGGER IF EXISTS trg_remote_jobs_updated_at ON public.remote_jobs;

CREATE TRIGGER trg_remote_jobs_updated_at
    BEFORE UPDATE ON public.remote_jobs
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();


-- ------------------------------------------------------------
-- 3. INDEXES
-- ------------------------------------------------------------
-- Indexes speed up the WHERE clauses and ORDER BY queries you'll
-- run most often. They trade a small INSERT/UPDATE cost for much
-- faster SELECT performance.

-- Filter jobs by company name
CREATE INDEX IF NOT EXISTS idx_remote_jobs_company
    ON public.remote_jobs (company);

-- Sort / filter by posting date (most common: ORDER BY date_posted DESC)
CREATE INDEX IF NOT EXISTS idx_remote_jobs_date_posted
    ON public.remote_jobs (date_posted DESC);

-- Filter by when our pipeline fetched the record
CREATE INDEX IF NOT EXISTS idx_remote_jobs_fetched_at
    ON public.remote_jobs (fetched_at DESC);

-- GIN index on the tags array — enables fast @> (contains) queries:
--   SELECT * FROM remote_jobs WHERE tags @> '{python}';
CREATE INDEX IF NOT EXISTS idx_remote_jobs_tags
    ON public.remote_jobs USING GIN (tags);

-- Full-text search index on position + company for keyword search
CREATE INDEX IF NOT EXISTS idx_remote_jobs_fts
    ON public.remote_jobs
    USING GIN (to_tsvector('english', position || ' ' || company));


-- ------------------------------------------------------------
-- 4. ROW LEVEL SECURITY (RLS)
-- ------------------------------------------------------------
-- RLS lets you control who can SELECT, INSERT, UPDATE, DELETE
-- at the row level. We enable it and add two policies:
--   • Public  : can only read (SELECT)
--   • Authenticated (service_role): can do anything (used by pipeline)

ALTER TABLE public.remote_jobs ENABLE ROW LEVEL SECURITY;

-- Allow anyone (even unauthenticated / anon) to read jobs
-- This is safe — it's just public job listing data.
DROP POLICY IF EXISTS "Allow public read" ON public.remote_jobs;
CREATE POLICY "Allow public read"
    ON public.remote_jobs
    FOR SELECT
    TO public          -- 'public' = all roles including anon
    USING (true);      -- no row restriction — see all rows

-- Allow only authenticated service_role (our pipeline) to write
DROP POLICY IF EXISTS "Allow service_role write" ON public.remote_jobs;
CREATE POLICY "Allow service_role write"
    ON public.remote_jobs
    FOR ALL            -- INSERT, UPDATE, DELETE
    TO authenticated   -- service_role inherits 'authenticated'
    USING (true)
    WITH CHECK (true);


-- ------------------------------------------------------------
-- 5. HELPER VIEW — today's jobs
-- ------------------------------------------------------------
-- A convenience view for quick dashboard checks.
-- Usage:  SELECT * FROM todays_jobs LIMIT 20;

CREATE OR REPLACE VIEW public.todays_jobs AS
SELECT
    job_id,
    position,
    company,
    location,
    tags,
    salary,
    date_posted,
    url
FROM public.remote_jobs
WHERE fetched_at >= CURRENT_DATE
ORDER BY date_posted DESC;

COMMENT ON VIEW public.todays_jobs IS
    'Jobs fetched during today''s pipeline run. Refreshes automatically.';


-- ------------------------------------------------------------
-- 6. QUICK SANITY CHECK
-- ------------------------------------------------------------
-- After running the schema, execute this to confirm the table exists:
--   SELECT COUNT(*) FROM remote_jobs;
-- Expected: 0 rows (empty until the pipeline runs for the first time)

SELECT
    table_name,
    pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) AS size
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'remote_jobs';
