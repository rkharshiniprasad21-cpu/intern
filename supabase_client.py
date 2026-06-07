"""
supabase_client.py
==================
Handles everything related to Supabase:
  • Creating the client connection
  • Upserting (insert-or-update) job records in batches
  • Reporting statistics back to the caller

Why upsert instead of insert?
  The pipeline runs daily. The same job might still be live after a week.
  Upserting on `job_id` means we update existing records (e.g. salary
  changes) instead of creating duplicates.

Why batch writes?
  Supabase's REST API accepts up to ~1 000 records per request, but
  sending 500 jobs in one payload is safer and avoids timeouts.
  We default to 100 per batch (config.BATCH_SIZE).
"""

import logging
from typing import Any

from supabase import create_client, Client

from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_client() -> Client:
    """
    Create and return a Supabase Python client.

    The client is authenticated with the service_role key which bypasses
    Row Level Security (RLS) — appropriate for a server-side pipeline.
    Never expose the service_role key to a frontend or public environment.
    """
    try:
        client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        logger.debug("Supabase client created for project: %s", settings.SUPABASE_URL)
        return client
    except Exception as exc:
        logger.critical("Failed to create Supabase client: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Upsert logic
# ---------------------------------------------------------------------------

def _prepare_record(job: dict) -> dict:
    """
    Convert Python types to JSON-serialisable values accepted by Supabase.

    Supabase's REST layer is fussy about Python lists — we serialise the
    `tags` list to a PostgreSQL text array literal string because supabase-py
    v2 handles that conversion correctly when you pass a plain list.

    Actually supabase-py v2 handles lists natively for array columns,
    so we just ensure the tags field is a list (not None).
    """
    record = dict(job)   # shallow copy — don't mutate the original
    if record.get("tags") is None:
        record["tags"] = []
    return record


def upsert_jobs(jobs: list[dict]) -> dict[str, Any]:
    """
    Insert or update a list of job records into the Supabase table.

    Parameters
    ----------
    jobs : list[dict]
        Clean job dicts as returned by scraper.collect_jobs().

    Returns
    -------
    dict with keys:
        total     – number of records attempted
        success   – number successfully upserted
        failed    – number that raised an error
        batches   – number of HTTP calls made

    Algorithm
    ---------
    1. Split `jobs` into chunks of settings.BATCH_SIZE.
    2. For each chunk call supabase.table(...).upsert(...).execute()
       with on_conflict="job_id" so duplicates are updated not rejected.
    3. Accumulate counts and return a summary.
    """
    if not jobs:
        logger.warning("upsert_jobs called with empty list — nothing to do.")
        return {"total": 0, "success": 0, "failed": 0, "batches": 0}

    client = get_client()
    table = settings.SUPABASE_TABLE
    batch_size = settings.BATCH_SIZE

    total = len(jobs)
    success = 0
    failed = 0
    batch_count = 0

    # Chunk the list into batches
    for batch_start in range(0, total, batch_size):
        batch = jobs[batch_start : batch_start + batch_size]
        records = [_prepare_record(j) for j in batch]
        batch_count += 1

        try:
            response = (
                client.table(table)
                .upsert(records, on_conflict="job_id")   # upsert key = job_id
                .execute()
            )

            # supabase-py v2 raises an exception on error, so reaching here
            # means the batch succeeded.
            batch_success = len(records)
            success += batch_success
            logger.info(
                "Batch %d/%d: upserted %d records (rows %d–%d).",
                batch_count,
                -(-total // batch_size),   # ceiling division
                batch_success,
                batch_start + 1,
                batch_start + batch_success,
            )

        except Exception as exc:  # noqa: BLE001
            failed += len(records)
            logger.error(
                "Batch %d failed (%d records): %s",
                batch_count,
                len(records),
                exc,
            )

    logger.info(
        "Upsert complete — total: %d | success: %d | failed: %d | batches: %d",
        total,
        success,
        failed,
        batch_count,
    )
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "batches": batch_count,
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def ping_supabase() -> bool:
    """
    Verify that the Supabase connection is working by doing a lightweight
    SELECT 1 against our table.

    Returns True on success, False on failure.
    Useful as a pre-flight check before a long scraping run.
    """
    try:
        client = get_client()
        client.table(settings.SUPABASE_TABLE).select("job_id").limit(1).execute()
        logger.info("Supabase ping: OK")
        return True
    except Exception as exc:
        logger.error("Supabase ping failed: %s", exc)
        return False
