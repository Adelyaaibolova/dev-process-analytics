"""Extract issues, pull requests and CI runs from GitHub into CSV files.

Usage:
    python -m devstats.extract --repo pydata/xarray --since 2026-05-01 --until 2026-10-31

Writes to ``data/``:
    pull_requests.csv  one row per pull request (with first review)
    issues.csv         one row per issue (pull requests excluded)
    labels.csv         label assignments for issues and pull requests
    ci_runs.csv        one row per GitHub Actions workflow run
    window.csv         the analysis window, read by the Qlik load script
    meta.json          repository, window, options and row counts

All timestamps are UTC, formatted ``YYYY-MM-DD hh:mm:ss`` for Qlik.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .github import GitHubClient, find_token

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
CI_RESULT_CAP = 1000  # GitHub returns at most 1,000 runs per filtered query

PR_FIELDS = [
    "pr_number", "title", "state", "author", "author_is_bot", "draft",
    "created_at", "closed_at", "merged_at", "first_review_at", "first_reviewer",
    "comments",
]
ISSUE_FIELDS = [
    "issue_number", "title", "state", "state_reason", "author", "author_is_bot",
    "created_at", "closed_at", "comments",
]
LABEL_FIELDS = ["item_type", "number", "label"]
RUN_FIELDS = [
    "run_id", "workflow", "event", "head_branch", "status", "conclusion",
    "created_at", "run_started_at", "updated_at", "run_attempt",
]


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Window:
    """Inclusive date range of the analysis, in UTC."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("--until must not be earlier than --since")

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.start, datetime.min.time(), tzinfo=timezone.utc)

    @property
    def end_dt_exclusive(self) -> datetime:
        return self.start_dt + timedelta(days=(self.end - self.start).days + 1)

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.start_dt <= moment < self.end_dt_exclusive


def parse_ts(value: str | None) -> datetime | None:
    """Parse a GitHub ISO timestamp such as ``2026-05-03T12:00:00Z``."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_ts(moment: datetime | None) -> str:
    return moment.strftime(TS_FORMAT) if moment else ""


def is_bot(user: dict | None) -> bool:
    if not user:
        return False
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def clean_text(text: str | None) -> str:
    """Single-line text: CSV- and Qlik-friendly."""
    return " ".join((text or "").split())


def first_review(reviews: Iterable[dict], author: str) -> tuple[datetime | None, str]:
    """Earliest submitted review by someone other than the PR author."""
    best: tuple[datetime | None, str] = (None, "")
    for review in reviews:
        submitted = parse_ts(review.get("submitted_at"))
        reviewer = (review.get("user") or {}).get("login", "")
        if submitted is None or review.get("state") == "PENDING" or reviewer == author:
            continue
        if best[0] is None or submitted < best[0]:
            best = (submitted, reviewer)
    return best


def split_issue_item(item: dict) -> tuple[str, dict, list[dict]]:
    """Turn one item of the issues endpoint into ``(kind, row, label rows)``.

    The issues endpoint returns pull requests too; they carry a
    ``pull_request`` object with ``merged_at``.
    """
    user = item.get("user") or {}
    labels = [label.get("name", "") for label in item.get("labels") or []]
    common = {
        "title": clean_text(item.get("title")),
        "state": item.get("state", ""),
        "author": user.get("login", ""),
        "author_is_bot": int(is_bot(user)),
        "created_at": fmt_ts(parse_ts(item.get("created_at"))),
        "closed_at": fmt_ts(parse_ts(item.get("closed_at"))),
        "comments": item.get("comments", 0),
    }
    number = item["number"]
    if "pull_request" in item:
        row = {
            "pr_number": number,
            **common,
            "draft": int(bool(item.get("draft"))),
            "merged_at": fmt_ts(parse_ts((item.get("pull_request") or {}).get("merged_at"))),
            "first_review_at": "",
            "first_reviewer": "",
        }
        kind = "pr"
    else:
        row = {"issue_number": number, **common, "state_reason": item.get("state_reason") or ""}
        kind = "issue"
    label_rows = [{"item_type": kind, "number": number, "label": name} for name in labels if name]
    return kind, row, label_rows


def is_relevant(item: dict, window: Window) -> bool:
    """Keep items opened, closed or merged inside the window."""
    merged = (item.get("pull_request") or {}).get("merged_at")
    return any(
        window.contains(parse_ts(value))
        for value in (item.get("created_at"), item.get("closed_at"), merged)
    )


def run_row(run: dict) -> dict:
    return {
        "run_id": run["id"],
        "workflow": clean_text(run.get("name")),
        "event": run.get("event", ""),
        "head_branch": run.get("head_branch") or "",
        "status": run.get("status", ""),
        "conclusion": run.get("conclusion") or "",
        "created_at": fmt_ts(parse_ts(run.get("created_at"))),
        "run_started_at": fmt_ts(parse_ts(run.get("run_started_at"))),
        "updated_at": fmt_ts(parse_ts(run.get("updated_at"))),
        "run_attempt": run.get("run_attempt", 1),
    }


def date_chunks(start: date, end: date, days: int = 7) -> list[tuple[date, date]]:
    """Split an inclusive date range into consecutive inclusive chunks."""
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def fetch_issue_items(client: GitHubClient, repo: str, window: Window) -> list[dict]:
    """All issues and PRs opened, closed or merged in the window.

    ``since`` filters on ``updated_at``; anything opened or closed inside the
    window was necessarily updated after the window start, so nothing is lost.
    """
    params = {
        "state": "all",
        "since": window.start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sort": "created",
        "direction": "asc",
    }
    kept = []
    for seen, item in enumerate(client.paginate(f"/repos/{repo}/issues", params), start=1):
        if is_relevant(item, window):
            kept.append(item)
        if seen % 500 == 0:
            _log(f"  issues and PRs scanned: {seen}")
    return kept


def fetch_reviews(client: GitHubClient, repo: str, number: int) -> list[dict]:
    return list(client.paginate(f"/repos/{repo}/pulls/{number}/reviews"))


def _runs_for_range(
    client: GitHubClient, repo: str, start: date, end: date, extra: dict
) -> list[dict]:
    params = {"created": f"{start.isoformat()}..{end.isoformat()}", **extra}
    pages = client.pages(f"/repos/{repo}/actions/runs", params)
    first = next(pages)
    total = first.get("total_count", 0)
    if total > CI_RESULT_CAP and start < end:
        middle = start + timedelta(days=(end - start).days // 2)
        return _runs_for_range(client, repo, start, middle, extra) + _runs_for_range(
            client, repo, middle + timedelta(days=1), end, extra
        )
    if total > CI_RESULT_CAP:
        _log(f"  warning: {total} runs on {start}; GitHub returns only the first {CI_RESULT_CAP}")
    runs = list(first.get("workflow_runs", []))
    for page in pages:
        runs.extend(page.get("workflow_runs", []))
    return runs


def fetch_ci_runs(
    client: GitHubClient,
    repo: str,
    window: Window,
    branch: str | None = None,
    event: str | None = None,
) -> list[dict]:
    extra = {key: value for key, value in (("branch", branch), ("event", event)) if value}
    by_id: dict[int, dict] = {}
    for start, end in date_chunks(window.start, window.end):
        for run in _runs_for_range(client, repo, start, end, extra):
            by_id[run["id"]] = run
        _log(f"  CI runs through {end}: {len(by_id)}")
    return sorted(by_id.values(), key=lambda run: run.get("created_at", ""))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def extract(
    client: GitHubClient,
    repo: str,
    window: Window,
    out_dir: Path,
    *,
    skip_reviews: bool = False,
    ci_branch: str | None = None,
    ci_event: str | None = None,
) -> dict:
    """Run the full extraction and write all files. Returns the metadata."""
    out_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Fetching issues and pull requests of {repo} ...")
    prs, issues, labels = [], [], []
    for item in fetch_issue_items(client, repo, window):
        kind, row, label_rows = split_issue_item(item)
        (prs if kind == "pr" else issues).append(row)
        labels.extend(label_rows)

    if not skip_reviews:
        to_review = [row for row in prs if window.contains(_parse_out(row["created_at"]))]
        _log(f"Fetching reviews for {len(to_review)} pull requests opened in the window ...")
        for done, row in enumerate(to_review, start=1):
            reviewed_at, reviewer = first_review(
                fetch_reviews(client, repo, row["pr_number"]), row["author"]
            )
            row["first_review_at"] = fmt_ts(reviewed_at)
            row["first_reviewer"] = reviewer
            if done % 100 == 0:
                _log(f"  reviews fetched: {done}/{len(to_review)}")

    _log("Fetching GitHub Actions runs ...")
    runs = [run_row(run) for run in fetch_ci_runs(client, repo, window, ci_branch, ci_event)]

    write_csv(out_dir / "pull_requests.csv", prs, PR_FIELDS)
    write_csv(out_dir / "issues.csv", issues, ISSUE_FIELDS)
    write_csv(out_dir / "labels.csv", labels, LABEL_FIELDS)
    write_csv(out_dir / "ci_runs.csv", runs, RUN_FIELDS)
    write_csv(
        out_dir / "window.csv",
        [{"window_start": window.start.isoformat(), "window_end": window.end.isoformat()}],
        ["window_start", "window_end"],
    )
    meta = {
        "repository": repo,
        "window_start": window.start.isoformat(),
        "window_end": window.end.isoformat(),
        "extracted_at": datetime.now(timezone.utc).strftime(TS_FORMAT),
        "timezone": "UTC",
        "options": {
            "skip_reviews": skip_reviews,
            "ci_branch": ci_branch,
            "ci_event": ci_event,
        },
        "rows": {
            "pull_requests": len(prs),
            "issues": len(issues),
            "labels": len(labels),
            "ci_runs": len(runs),
        },
        "api_requests": client.requests_made,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def _parse_out(value: str) -> datetime | None:
    """Parse a timestamp in the output format back to an aware datetime."""
    if not value:
        return None
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from err


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="owner/name, e.g. pydata/xarray")
    parser.add_argument("--since", required=True, type=_date_arg, help="first day (UTC)")
    parser.add_argument("--until", required=True, type=_date_arg, help="last day (UTC)")
    parser.add_argument("--out", default="data", type=Path, help="output folder")
    parser.add_argument("--skip-reviews", action="store_true", help="faster: no review data")
    parser.add_argument("--ci-branch", help="only CI runs on this branch, e.g. main")
    parser.add_argument("--ci-event", help="only CI runs of this event, e.g. push")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repo.count("/") != 1:
        _log("--repo must look like owner/name")
        return 2
    token = find_token()
    if not token:
        _log("No GITHUB_TOKEN found: unauthenticated requests are limited to 60 per hour.")
    client = GitHubClient(token)
    meta = extract(
        client,
        args.repo,
        Window(args.since, args.until),
        args.out,
        skip_reviews=args.skip_reviews,
        ci_branch=args.ci_branch,
        ci_event=args.ci_event,
    )
    _log(f"Done: {meta['rows']} written to {args.out} ({meta['api_requests']} API requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
