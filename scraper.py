"""
scraper.py
==========
The heart of this project. This module:
  1. Calls the RemoteOK public API
  2. Parses and extracts the fields we need
  3. Cleans the data (strips HTML, normalises dates, etc.)
  4. Returns a list of clean job dictionaries ready for Supabase

Why a separate module?
  Separating concerns means you can test the scraper independently
  from the database layer. If RemoteOK changes its API format you
  only edit this file.
"""

import re
import time
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from config import settings

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
# We use Python's built-in logging instead of print() so that log level,
# format and destination can all be configured from one place (config.py).
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _strip_html(raw: str) -> str:
    """
    Remove every HTML tag from a string and decode HTML entities.

    RemoteOK returns job descriptions full of <p>, <ul>, <strong> etc.
    We want plain text for storage and display.

    Example:
        _strip_html("<p>Hello <b>world</b></p>")  →  "Hello world"
    """
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return soup.get_text(separator=" ").strip()


def _clean_text(value: Any) -> str:
    """
    Convert any value to a clean, single-line string.

    Steps:
      1. Cast to str
      2. Collapse multiple whitespace characters into one space
      3. Strip leading / trailing whitespace
    """
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_epoch(epoch_value: Any) -> str | None:
    """
    Convert a Unix timestamp (integer or string) to an ISO-8601 UTC string.

    RemoteOK returns dates as Unix epoch integers, e.g. 1718000000.
    Supabase's timestamptz column expects ISO-8601, e.g. "2024-06-10T12:00:00+00:00".

    Returns None if the value cannot be parsed so we don't crash on bad data.
    """
    if not epoch_value:
        return None
    try:
        ts = int(epoch_value)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError, OSError) as exc:
        logger.warning("Could not parse epoch value %r: %s", epoch_value, exc)
        return None


def _parse_tags(raw_tags: Any) -> list[str]:
    """
    Normalise the tags field into a plain Python list of strings.

    RemoteOK sends tags as a JSON array, e.g. ["python", "remote", "backend"].
    We lowercase each tag and deduplicate, keeping insertion order.
    """
    if not raw_tags or not isinstance(raw_tags, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for tag in raw_tags:
        normalised = _clean_text(tag).lower()
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result


# ---------------------------------------------------------------------------
# Core fetching logic
# ---------------------------------------------------------------------------

def fetch_jobs() -> list[dict]:
    """
    Call the RemoteOK public JSON API and return a list of raw job dicts.

    The API contract:
      • URL  : https://remoteok.com/api
      • Auth : None (public, no API key required)
      • Rate : ~1 request per second is polite; we add a 1 s sleep
      • Format: JSON array; the FIRST element is a legal notice dict,
                NOT a job — we skip it with [1:]

    Retry logic:
      We retry up to settings.MAX_RETRIES times with exponential back-off
      (1s, 2s, 4s …) so transient network hiccups don't kill the run.
    """
    headers = {
        # RemoteOK blocks requests with no User-Agent header.
        # A realistic browser UA avoids a 403 response.
        "User-Agent": settings.USER_AGENT,
        "Accept": "application/json",
    }

    for attempt in range(1, settings.MAX_RETRIES + 1):
        try:
            logger.info(
                "Fetching RemoteOK API (attempt %d/%d) …",
                attempt,
                settings.MAX_RETRIES,
            )
            response = requests.get(
                settings.API_URL,
                headers=headers,
                timeout=settings.REQUEST_TIMEOUT,
            )
            response.raise_for_status()   # raises HTTPError for 4xx/5xx

            raw_data: list = response.json()

            # First element is always a meta/legal notice — skip it.
            jobs = [item for item in raw_data if isinstance(item, dict) and "id" in item]

            logger.info("Fetched %d raw job records.", len(jobs))
            return jobs

        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error on attempt %d: %s", attempt, exc)
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error on attempt %d: %s", attempt, exc)
        except requests.exceptions.Timeout:
            logger.error("Request timed out on attempt %d.", attempt)
        except ValueError as exc:
            logger.error("JSON decode failed on attempt %d: %s", attempt, exc)

        if attempt < settings.MAX_RETRIES:
            wait = 2 ** (attempt - 1)          # 1 s, 2 s, 4 s …
            logger.info("Retrying in %d second(s) …", wait)
            time.sleep(wait)

    logger.critical("All %d fetch attempts failed. Returning empty list.", settings.MAX_RETRIES)
    return []


# ---------------------------------------------------------------------------
# Data extraction and cleaning
# ---------------------------------------------------------------------------

def parse_job(raw: dict) -> dict | None:
    """
    Transform a single raw API record into our clean schema.

    Returns None for records that lack required fields so the caller
    can filter them out easily.

    Fields we extract
    -----------------
    job_id      : str   – RemoteOK's own unique identifier
    position    : str   – Job title / role name
    company     : str   – Company posting the job
    location    : str   – Location string (usually "Worldwide" or region)
    tags        : list  – Technology / domain tags
    date_posted : str   – ISO-8601 UTC timestamp
    url         : str   – Direct link to the job listing
    description : str   – Plain-text job description (HTML stripped)
    salary      : str   – Salary range if provided, else empty string
    """
    try:
        job_id = _clean_text(raw.get("id") or raw.get("slug"))
        position = _clean_text(raw.get("position"))
        company = _clean_text(raw.get("company"))

        # Guard: skip records with no meaningful identity
        if not job_id or not position:
            logger.debug("Skipping record with missing id/position: %r", raw)
            return None

        location = _clean_text(raw.get("location")) or "Worldwide"
        tags = _parse_tags(raw.get("tags"))
        date_posted = _parse_epoch(raw.get("epoch"))
        url = _clean_text(raw.get("url"))
        description = _strip_html(raw.get("description", ""))
        salary = _clean_text(raw.get("salary"))

        return {
            "job_id": job_id,
            "position": position,
            "company": company,
            "location": location,
            "tags": tags,
            "date_posted": date_posted,
            "url": url,
            "description": description[:5000],   # cap at 5 000 chars
            "salary": salary,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    except Exception as exc:   # noqa: BLE001  (broad catch intentional)
        logger.warning("Unexpected error parsing record %r: %s", raw.get("id"), exc)
        return None


def collect_jobs() -> list[dict]:
    """
    Public entry-point.

    Orchestrates fetch → parse → filter and returns a clean list of
    job dicts. This is the only function that other modules need to call.

    Returns an empty list (never raises) so the caller can decide how
    to handle a failed run.
    """
    raw_jobs = fetch_jobs()
    if not raw_jobs:
        logger.warning("No raw jobs to process.")
        return []

    parsed: list[dict] = []
    skipped = 0

    for raw in raw_jobs:
        job = parse_job(raw)
        if job:
            parsed.append(job)
        else:
            skipped += 1

    logger.info(
        "Parsing complete: %d valid jobs, %d skipped.",
        len(parsed),
        skipped,
    )
    return parsed


# ---------------------------------------------------------------------------
# Allow running this module directly for quick smoke-tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    jobs = collect_jobs()
    if jobs:
        import json
        print(json.dumps(jobs[0], indent=2, default=str))
        print(f"\nTotal jobs collected: {len(jobs)}")
    else:
        print("No jobs collected.")
