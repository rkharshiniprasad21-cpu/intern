"""
tests/test_scraper.py
=====================
Unit tests for the scraper module.

We use the `responses` library to intercept HTTP calls so tests run
completely offline — no real API calls, no flakiness.

Run with:
    pytest tests/ -v
    pytest tests/ -v --cov=src --cov-report=term-missing
"""

import pytest
import responses as responses_lib   # mock HTTP library
import requests

from src.scraper import (
    _strip_html,
    _clean_text,
    _parse_epoch,
    _parse_tags,
    parse_job,
    collect_jobs,
)


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------

class TestStripHtml:
    def test_removes_tags(self):
        result = _strip_html("<p>Hello <b>world</b></p>")
        assert "Hello" in result and "world" in result
        assert "<" not in result

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_none_returns_empty(self):
        assert _strip_html(None) == ""

    def test_plain_text_unchanged(self):
        assert _strip_html("no html here") == "no html here"

    def test_decodes_entities(self):
        result = _strip_html("&amp; &lt; &gt;")
        assert "&" in result and "<" in result


# ---------------------------------------------------------------------------
# _clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_collapses_whitespace(self):
        assert _clean_text("hello   world") == "hello world"

    def test_strips_edges(self):
        assert _clean_text("  hi  ") == "hi"

    def test_none_returns_empty(self):
        assert _clean_text(None) == ""

    def test_integer_converted(self):
        assert _clean_text(42) == "42"


# ---------------------------------------------------------------------------
# _parse_epoch
# ---------------------------------------------------------------------------

class TestParseEpoch:
    def test_valid_epoch(self):
        # epoch 0 may fail on some systems due to negative local time; use a known positive epoch
        result = _parse_epoch(1718000000)
        assert result is not None
        assert "2024" in result or "T" in result

    def test_none_returns_none(self):
        assert _parse_epoch(None) is None

    def test_string_epoch(self):
        result = _parse_epoch("1700000000")
        assert result is not None
        assert "T" in result   # ISO-8601 contains a T

    def test_invalid_returns_none(self):
        assert _parse_epoch("not-a-number") is None


# ---------------------------------------------------------------------------
# _parse_tags
# ---------------------------------------------------------------------------

class TestParseTags:
    def test_normal_list(self):
        assert _parse_tags(["Python", "REMOTE"]) == ["python", "remote"]

    def test_deduplicates(self):
        assert _parse_tags(["go", "Go", "GO"]) == ["go"]

    def test_none_returns_empty(self):
        assert _parse_tags(None) == []

    def test_empty_list(self):
        assert _parse_tags([]) == []

    def test_non_list_returns_empty(self):
        assert _parse_tags("python") == []


# ---------------------------------------------------------------------------
# parse_job
# ---------------------------------------------------------------------------

VALID_RAW = {
    "id": "12345",
    "position": "Senior Python Developer",
    "company": "Acme Corp",
    "location": "Worldwide",
    "tags": ["python", "remote", "backend"],
    "epoch": 1718000000,
    "url": "https://remoteok.com/jobs/12345",
    "description": "<p>Great job with <b>good pay</b></p>",
    "salary": "$100k - $140k",
}


class TestParseJob:
    def test_valid_record(self):
        job = parse_job(VALID_RAW)
        assert job is not None
        assert job["job_id"] == "12345"
        assert job["position"] == "Senior Python Developer"
        assert job["company"] == "Acme Corp"
        assert "python" in job["tags"]
        assert "<" not in job["description"]   # HTML stripped

    def test_missing_id_returns_none(self):
        bad = {**VALID_RAW, "id": None, "slug": None}
        assert parse_job(bad) is None

    def test_missing_position_returns_none(self):
        bad = {**VALID_RAW, "position": ""}
        assert parse_job(bad) is None

    def test_location_defaults_to_worldwide(self):
        no_loc = {**VALID_RAW, "location": ""}
        job = parse_job(no_loc)
        assert job["location"] == "Worldwide"

    def test_description_capped_at_5000(self):
        long_desc = "x" * 10_000
        raw = {**VALID_RAW, "description": long_desc}
        job = parse_job(raw)
        assert len(job["description"]) <= 5000

    def test_fetched_at_present(self):
        job = parse_job(VALID_RAW)
        assert "fetched_at" in job
        assert job["fetched_at"] is not None


# ---------------------------------------------------------------------------
# collect_jobs (integration — mocked HTTP)
# ---------------------------------------------------------------------------

MOCK_API_RESPONSE = [
    # First element is always the legal notice (no 'id' key)
    {"legal": "This data is provided …"},
    # Actual job records
    {
        "id": "101",
        "position": "DevOps Engineer",
        "company": "StartupXYZ",
        "location": "Europe",
        "tags": ["devops", "aws"],
        "epoch": 1718000000,
        "url": "https://remoteok.com/jobs/101",
        "description": "Run our infra.",
        "salary": "",
    },
    {
        "id": "102",
        "position": "Data Scientist",
        "company": "DataCo",
        "location": "Worldwide",
        "tags": ["python", "ml"],
        "epoch": 1718100000,
        "url": "https://remoteok.com/jobs/102",
        "description": "Build ML models.",
        "salary": "$90k",
    },
]


class TestCollectJobs:
    @responses_lib.activate
    def test_collects_two_jobs(self):
        responses_lib.add(
            responses_lib.GET,
            "https://remoteok.com/api",
            json=MOCK_API_RESPONSE,
            status=200,
        )
        jobs = collect_jobs()
        assert len(jobs) == 2

    @responses_lib.activate
    def test_api_error_returns_empty(self):
        responses_lib.add(
            responses_lib.GET,
            "https://remoteok.com/api",
            status=500,
        )
        jobs = collect_jobs()
        assert jobs == []

    @responses_lib.activate
    def test_empty_api_returns_empty(self):
        responses_lib.add(
            responses_lib.GET,
            "https://remoteok.com/api",
            json=[{"legal": "notice"}],  # only the legal notice
            status=200,
        )
        jobs = collect_jobs()
        assert jobs == []
