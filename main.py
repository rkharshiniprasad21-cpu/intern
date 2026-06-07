"""
main.py
=======
Orchestrator — ties together scraper and Supabase client.

This is the script that GitHub Actions (and you locally) will call:

    python src/main.py

It runs the full pipeline:
  1. Pre-flight Supabase ping
  2. Collect & clean jobs from RemoteOK
  3. Upsert into Supabase
  4. Print a run summary
  5. Exit with code 0 (success) or 1 (failure) so GitHub Actions
     knows whether the job passed or failed.

Keeping this file thin means each concern (scraping, storage) is
individually testable. main.py just calls them in the right order.
"""

import logging
import sys
import time
from datetime import datetime, timezone

from config import settings
from scraper import collect_jobs
from supabase_client import ping_supabase, upsert_jobs

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def run_pipeline() -> int:
    """
    Execute the full ETL pipeline.

    Returns
    -------
    int
        0 = success, 1 = failure (passed to sys.exit so CI can react).
    """
    start_time = time.monotonic()
    run_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logger.info("=" * 60)
    logger.info("RemoteOK Pipeline — run started at %s", run_ts)
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Step 1: Verify Supabase is reachable before we bother scraping
    # ------------------------------------------------------------------
    logger.info("Step 1/3  Pre-flight check …")
    if not ping_supabase():
        logger.critical(
            "Cannot reach Supabase. Check SUPABASE_URL and SUPABASE_KEY."
        )
        return 1
    logger.info("Step 1/3  ✓ Supabase reachable.")

    # ------------------------------------------------------------------
    # Step 2: Collect jobs from the RemoteOK API
    # ------------------------------------------------------------------
    logger.info("Step 2/3  Collecting jobs from RemoteOK API …")
    jobs = collect_jobs()

    if not jobs:
        logger.warning(
            "Step 2/3  No jobs collected. Pipeline will exit without writing."
        )
        # Not a hard failure — API might be temporarily empty
        return 0

    logger.info("Step 2/3  ✓ %d jobs collected and cleaned.", len(jobs))

    # ------------------------------------------------------------------
    # Step 3: Upsert into Supabase
    # ------------------------------------------------------------------
    logger.info("Step 3/3  Upserting into Supabase table '%s' …", settings.SUPABASE_TABLE)
    stats = upsert_jobs(jobs)

    elapsed = time.monotonic() - start_time

    logger.info("=" * 60)
    logger.info("Pipeline complete in %.1f s", elapsed)
    logger.info("  Records attempted : %d", stats["total"])
    logger.info("  Records succeeded : %d", stats["success"])
    logger.info("  Records failed    : %d", stats["failed"])
    logger.info("  Supabase batches  : %d", stats["batches"])
    logger.info("=" * 60)

    # If more than half the records failed, treat the run as a failure
    if stats["total"] > 0 and stats["failed"] / stats["total"] > 0.5:
        logger.error("More than 50 %% of records failed — marking run as FAILED.")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = run_pipeline()
    sys.exit(exit_code)
