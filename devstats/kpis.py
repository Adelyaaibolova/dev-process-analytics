"""Recompute the dashboard KPIs in Python to validate the Qlik Sense app.

Usage:
    python -m devstats.kpis --data data --out results

Every definition here mirrors a master measure in ``qlik/master_items.md``:
same window (inclusive dates, UTC), same week start (Monday), same bot rule.
If Qlik and this script disagree, one of them has a bug.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
HOUR = pd.Timedelta(hours=1)
DAY = pd.Timedelta(days=1)
MINUTE = pd.Timedelta(minutes=1)

# name -> (label shown in Qlik, unit)
KPIS: dict[str, tuple[str, str]] = {
    "prs_opened": ("PRs opened", "count"),
    "prs_merged": ("PRs merged", "count"),
    "prs_closed": ("PRs closed", "count"),
    "merge_rate": ("Merge rate", "share"),
    "median_pr_lead_time_h": ("Median PR lead time (h)", "hours"),
    "median_hours_to_first_review": ("Median time to first review (h)", "hours"),
    "share_reviewed_within_24h": ("Reviewed within 24 h", "share"),
    "issues_opened": ("Issues opened", "count"),
    "issues_closed": ("Issues closed", "count"),
    "median_days_to_close_issue": ("Median days to close an issue", "days"),
    "ci_runs_completed": ("CI runs completed", "count"),
    "ci_failure_rate": ("CI failure rate", "share"),
    "median_ci_duration_min": ("Median CI duration (min)", "minutes"),
}

FAIL_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}


def ci_outcome(conclusion: str) -> str:
    """``pass`` / ``fail`` / ``other`` (cancelled, skipped, still running ...)."""
    if conclusion == "success":
        return "pass"
    if conclusion in FAIL_CONCLUSIONS:
        return "fail"
    return "other"


@dataclass
class Data:
    prs: pd.DataFrame
    issues: pd.DataFrame
    runs: pd.DataFrame
    start: pd.Timestamp
    end_exclusive: pd.Timestamp


def _read(path: Path, timestamps: list[str], integers: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    for column in timestamps:
        frame[column] = pd.to_datetime(
            frame[column].where(frame[column] != ""), format=TS_FORMAT, errors="raise"
        )
    for column in integers:
        frame[column] = pd.to_numeric(frame[column]).astype("int64")
    return frame


def load(data_dir: Path) -> Data:
    """Load the CSV files written by ``devstats.extract``."""
    data_dir = Path(data_dir)
    window = pd.read_csv(data_dir / "window.csv", dtype=str).iloc[0]
    start = pd.Timestamp(window["window_start"])
    end_exclusive = pd.Timestamp(window["window_end"]) + DAY
    prs = _read(
        data_dir / "pull_requests.csv",
        ["created_at", "closed_at", "merged_at", "first_review_at"],
        ["pr_number", "author_is_bot"],
    )
    issues = _read(
        data_dir / "issues.csv", ["created_at", "closed_at"], ["issue_number", "author_is_bot"]
    )
    runs = _read(
        data_dir / "ci_runs.csv", ["created_at", "run_started_at", "updated_at"], ["run_id"]
    )
    runs["outcome"] = runs["conclusion"].map(ci_outcome)
    return Data(prs, issues, runs, start, end_exclusive)


def week_start(moments: pd.Series) -> pd.Series:
    """Monday of the week, as a date (matches Qlik ``WeekStart(date, 0, 0)``)."""
    days = moments.dt.normalize()
    return days - pd.to_timedelta(days.dt.dayofweek, unit="D")


def _inside(moments: pd.Series, data: Data) -> pd.Series:
    return (moments >= data.start) & (moments < data.end_exclusive)


def _median(values: pd.Series) -> float | None:
    values = values.dropna()
    return float(values.median()) if len(values) else None


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


@dataclass
class Events:
    """The rows behind each KPI, each tagged with the moment it happened."""

    pr_opened: pd.DataFrame
    pr_merged: pd.DataFrame
    pr_closed: pd.DataFrame
    issue_opened: pd.DataFrame
    issue_closed: pd.DataFrame
    ci_completed: pd.DataFrame


def events(data: Data, include_bots: bool = False) -> Events:
    prs = data.prs if include_bots else data.prs[data.prs["author_is_bot"] == 0]
    issues = data.issues if include_bots else data.issues[data.issues["author_is_bot"] == 0]

    pr_opened = prs[_inside(prs["created_at"], data)].assign(moment=lambda f: f["created_at"])
    pr_opened = pr_opened.assign(
        hours_to_first_review=(pr_opened["first_review_at"] - pr_opened["created_at"]) / HOUR
    )
    pr_merged = prs[_inside(prs["merged_at"], data)].assign(moment=lambda f: f["merged_at"])
    pr_merged = pr_merged.assign(
        lead_time_h=(pr_merged["merged_at"] - pr_merged["created_at"]) / HOUR
    )
    pr_closed = prs[_inside(prs["closed_at"], data)].assign(moment=lambda f: f["closed_at"])

    issue_opened = issues[_inside(issues["created_at"], data)].assign(
        moment=lambda f: f["created_at"]
    )
    issue_closed = issues[_inside(issues["closed_at"], data)].assign(
        moment=lambda f: f["closed_at"]
    )
    issue_closed = issue_closed.assign(
        days_to_close=(issue_closed["closed_at"] - issue_closed["created_at"]) / DAY
    )

    runs = data.runs[_inside(data.runs["created_at"], data)]
    ci_completed = runs[runs["outcome"].isin(["pass", "fail"])].assign(
        moment=lambda f: f["created_at"]
    )
    ci_completed = ci_completed.assign(
        duration_min=(ci_completed["updated_at"] - ci_completed["run_started_at"]) / MINUTE
    )
    return Events(pr_opened, pr_merged, pr_closed, issue_opened, issue_closed, ci_completed)


def _kpis_for(ev: Events) -> dict[str, float | int | None]:
    reviewed_fast = (ev.pr_opened["hours_to_first_review"] <= 24).sum()
    # Without review data (extract --skip-reviews) the share is unknown, not zero.
    has_reviews = bool(ev.pr_opened["first_review_at"].notna().any())
    failed = (ev.ci_completed["outcome"] == "fail").sum()
    return {
        "prs_opened": len(ev.pr_opened),
        "prs_merged": len(ev.pr_merged),
        "prs_closed": len(ev.pr_closed),
        "merge_rate": _share(int(ev.pr_closed["merged_at"].notna().sum()), len(ev.pr_closed)),
        "median_pr_lead_time_h": _median(ev.pr_merged["lead_time_h"]),
        "median_hours_to_first_review": _median(ev.pr_opened["hours_to_first_review"]),
        "share_reviewed_within_24h": (
            _share(int(reviewed_fast), len(ev.pr_opened)) if has_reviews else None
        ),
        "issues_opened": len(ev.issue_opened),
        "issues_closed": len(ev.issue_closed),
        "median_days_to_close_issue": _median(ev.issue_closed["days_to_close"]),
        "ci_runs_completed": len(ev.ci_completed),
        "ci_failure_rate": _share(int(failed), len(ev.ci_completed)),
        "median_ci_duration_min": _median(ev.ci_completed["duration_min"]),
    }


def summary(data: Data, include_bots: bool = False) -> dict[str, float | int | None]:
    """All KPIs over the whole window."""
    return _kpis_for(events(data, include_bots))


def weekly(data: Data, include_bots: bool = False) -> pd.DataFrame:
    """All KPIs per week (Monday start). Weeks without events are kept."""
    ev = events(data, include_bots)
    first = week_start(pd.Series([data.start])).iloc[0]
    last = week_start(pd.Series([data.end_exclusive - DAY])).iloc[0]
    weeks = pd.date_range(first, last, freq="7D")

    def only(frame: pd.DataFrame, week: pd.Timestamp) -> pd.DataFrame:
        return frame[week_start(frame["moment"]) == week] if len(frame) else frame

    rows = []
    for week in weeks:
        part = Events(*(only(getattr(ev, name), week) for name in Events.__dataclass_fields__))
        rows.append({"week_start": week.date().isoformat(), **_kpis_for(part)})
    return pd.DataFrame(rows)


def _display(name: str, value: float | int | None) -> str:
    if value is None:
        return "n/a"
    unit = KPIS[name][1]
    if unit == "share":
        return f"{value:.1%}"
    if unit == "count":
        return f"{int(value):,}"
    return f"{value:,.2f}"


def summary_markdown(values: dict[str, float | int | None], data: Data) -> str:
    end = (data.end_exclusive - DAY).date().isoformat()
    lines = [
        f"KPIs for {data.start.date().isoformat()} to {end} (UTC, bot authors excluded)",
        "",
        "| KPI | Value |",
        "| --- | ---: |",
    ]
    lines += [f"| {KPIS[name][0]} | {_display(name, value)} |" for name, value in values.items()]
    return "\n".join(lines) + "\n"


def write_results(data: Data, out_dir: Path, include_bots: bool = False) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    values = summary(data, include_bots)
    pd.DataFrame(
        [
            {"kpi": name, "label": KPIS[name][0], "unit": KPIS[name][1], "value": value}
            for name, value in values.items()
        ]
    ).to_csv(out_dir / "kpi_summary.csv", index=False)
    weekly(data, include_bots).to_csv(out_dir / "kpi_weekly.csv", index=False)
    (out_dir / "kpi_summary.md").write_text(summary_markdown(values, data), encoding="utf-8")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute dashboard KPIs for validation.")
    parser.add_argument("--data", default="data", type=Path, help="folder with the CSV files")
    parser.add_argument("--out", default="results", type=Path, help="output folder")
    parser.add_argument("--include-bots", action="store_true", help="keep bot-authored items")
    args = parser.parse_args(argv)
    data = load(args.data)
    values = write_results(data, args.out, args.include_bots)
    sys.stdout.write(summary_markdown(values, data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
