"""Turning GitHub API items into rows, and the full extract with a fake API."""

import csv
import json
from datetime import date, datetime, timezone

import pytest

from devstats import extract
from devstats.extract import Window


def ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


WINDOW = Window(date(2026, 5, 4), date(2026, 5, 17))


def issue_item(number, created, closed=None, *, login="alice", pr=False, merged=None, labels=()):
    item = {
        "number": number,
        "title": f"Item {number}\nwith a line break",
        "state": "closed" if closed else "open",
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "labels": [{"name": name} for name in labels],
        "created_at": created,
        "closed_at": closed,
        "comments": 2,
    }
    if pr:
        item["pull_request"] = {"merged_at": merged}
        item["draft"] = False
    else:
        item["state_reason"] = "completed" if closed else None
    return item


def test_window_boundaries():
    assert WINDOW.contains(ts("2026-05-04T00:00:00Z"))
    assert WINDOW.contains(ts("2026-05-17T23:59:59Z"))
    assert not WINDOW.contains(ts("2026-05-18T00:00:00Z"))
    assert not WINDOW.contains(None)
    with pytest.raises(ValueError):
        Window(date(2026, 5, 2), date(2026, 5, 1))


def test_split_pull_request():
    item = issue_item(
        5, "2026-05-04T10:00:00Z", "2026-05-05T10:00:00Z",
        pr=True, merged="2026-05-05T10:00:00Z", labels=["bug", "ci"],
    )
    kind, row, labels = extract.split_issue_item(item)
    assert kind == "pr"
    assert row["pr_number"] == 5
    assert row["title"] == "Item 5 with a line break"
    assert row["created_at"] == "2026-05-04 10:00:00"
    assert row["merged_at"] == "2026-05-05 10:00:00"
    assert row["author_is_bot"] == 0
    assert labels == [
        {"item_type": "pr", "number": 5, "label": "bug"},
        {"item_type": "pr", "number": 5, "label": "ci"},
    ]


def test_split_issue_and_bot():
    kind, row, _ = extract.split_issue_item(
        issue_item(9, "2026-05-06T00:00:00Z", login="github-actions[bot]")
    )
    assert kind == "issue"
    assert row["issue_number"] == 9
    assert row["closed_at"] == ""
    assert row["author_is_bot"] == 1


def test_first_review_skips_author_and_pending():
    reviews = [
        {"user": {"login": "alice"}, "state": "COMMENTED", "submitted_at": "2026-05-04T11:00:00Z"},
        {"user": {"login": "bob"}, "state": "PENDING", "submitted_at": None},
        {"user": {"login": "carol"}, "state": "APPROVED", "submitted_at": "2026-05-04T15:00:00Z"},
        {"user": {"login": "bob"}, "state": "CHANGES_REQUESTED", "submitted_at": "2026-05-04T13:00:00Z"},
    ]
    assert extract.first_review(reviews, "alice") == (ts("2026-05-04T13:00:00Z"), "bob")
    assert extract.first_review([], "alice") == (None, "")


def test_is_relevant():
    inside = issue_item(1, "2026-05-05T00:00:00Z")
    closed_inside = issue_item(2, "2026-04-01T00:00:00Z", "2026-05-10T00:00:00Z")
    outside = issue_item(3, "2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z")
    merged_inside = issue_item(
        4, "2026-04-01T00:00:00Z", "2026-05-20T00:00:00Z", pr=True, merged="2026-05-10T00:00:00Z"
    )
    assert extract.is_relevant(inside, WINDOW)
    assert extract.is_relevant(closed_inside, WINDOW)
    assert not extract.is_relevant(outside, WINDOW)
    assert extract.is_relevant(merged_inside, WINDOW)


def test_date_chunks():
    chunks = extract.date_chunks(date(2026, 5, 1), date(2026, 5, 16), days=7)
    assert chunks == [
        (date(2026, 5, 1), date(2026, 5, 7)),
        (date(2026, 5, 8), date(2026, 5, 14)),
        (date(2026, 5, 15), date(2026, 5, 16)),
    ]
    assert extract.date_chunks(date(2026, 5, 1), date(2026, 5, 1)) == [
        (date(2026, 5, 1), date(2026, 5, 1))
    ]


class FakeClient:
    """Answers the three endpoints the extractor uses from in-memory data."""

    def __init__(self, items, reviews, runs, cap=extract.CI_RESULT_CAP):
        self.items = items
        self.reviews = reviews
        self.runs = runs
        self.cap = cap
        self.requests_made = 0
        self.run_queries = []

    def paginate(self, url, params=None, item_key=None):
        self.requests_made += 1
        if url.endswith("/issues"):
            return iter(self.items)
        if "/reviews" in url:
            number = int(url.split("/")[-2])
            return iter(self.reviews.get(number, []))
        raise AssertionError(url)

    def pages(self, url, params=None):
        self.requests_made += 1
        start, end = params["created"].split("..")
        self.run_queries.append((start, end))
        runs = [run for run in self.runs if start <= run["created_at"][:10] <= end]
        yield {"total_count": len(runs) * self.cap if len(runs) > 1 and start != end else len(runs),
               "workflow_runs": runs}


def make_run(run_id, created, conclusion="success"):
    return {
        "id": run_id, "name": "CI", "event": "push", "head_branch": "main",
        "status": "completed", "conclusion": conclusion, "created_at": created,
        "run_started_at": created, "updated_at": created.replace("09:00", "09:10"),
        "run_attempt": 1,
    }


def test_ci_runs_split_large_ranges_and_deduplicate():
    runs = [make_run(1, "2026-05-04T09:00:00Z"), make_run(2, "2026-05-06T09:00:00Z")]
    client = FakeClient([], {}, runs + [runs[0]])
    found = extract.fetch_ci_runs(client, "o/r", Window(date(2026, 5, 4), date(2026, 5, 10)))
    assert [run["id"] for run in found] == [1, 2]
    # The 7-day chunk looked too big, so it was split down to single days.
    assert ("2026-05-04", "2026-05-04") in client.run_queries


def test_full_extract_writes_all_files(tmp_path):
    items = [
        issue_item(1, "2026-05-04T10:00:00Z", "2026-05-05T10:00:00Z", pr=True,
                   merged="2026-05-05T10:00:00Z", labels=["enhancement"]),
        issue_item(2, "2026-05-05T00:00:00Z", "2026-05-06T00:00:00Z", login="carol", pr=True),
        issue_item(10, "2026-05-04T00:00:00Z", "2026-05-06T00:00:00Z", login="gina", labels=["bug"]),
        issue_item(99, "2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z"),
    ]
    reviews = {1: [{"user": {"login": "bob"}, "state": "APPROVED", "submitted_at": "2026-05-04T12:00:00Z"}]}
    runs = [make_run(100, "2026-05-04T09:00:00Z"), make_run(101, "2026-05-05T09:00:00Z", "failure")]
    client = FakeClient(items, reviews, runs, cap=1)

    meta = extract.extract(client, "o/r", WINDOW, tmp_path)

    assert meta["rows"] == {"pull_requests": 2, "issues": 1, "labels": 2, "ci_runs": 2}
    with (tmp_path / "pull_requests.csv").open(encoding="utf-8") as handle:
        rows = {row["pr_number"]: row for row in csv.DictReader(handle)}
    assert rows["1"]["first_review_at"] == "2026-05-04 12:00:00"
    assert rows["1"]["first_reviewer"] == "bob"
    assert rows["2"]["first_review_at"] == ""
    assert json.loads((tmp_path / "meta.json").read_text())["repository"] == "o/r"

    with (tmp_path / "ci_runs.csv").open(encoding="utf-8") as handle:
        conclusions = [row["conclusion"] for row in csv.DictReader(handle)]
    assert conclusions == ["success", "failure"]
    assert (tmp_path / "window.csv").read_text(encoding="utf-8").splitlines() == [
        "window_start,window_end", "2026-05-04,2026-05-17"]


def test_cli_rejects_bad_repo(capsys):
    assert extract.main(["--repo", "no-slash", "--since", "2026-05-01", "--until", "2026-05-02"]) == 2
