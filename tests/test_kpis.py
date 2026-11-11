"""KPI definitions checked against values worked out by hand (see conftest.py)."""

import pandas as pd
import pytest

from devstats import kpis


@pytest.fixture
def data(data_dir):
    return kpis.load(data_dir)


def test_summary_matches_hand_calculation(data):
    values = kpis.summary(data)
    # PRs 1, 2, 3, 6 opened in the window; 4 is a bot, 5 opened in April.
    assert values["prs_opened"] == 4
    # Merged in window: 1 (24 h), 3 (48 h), 5 (336 h). Median = 48.
    assert values["prs_merged"] == 3
    assert values["median_pr_lead_time_h"] == pytest.approx(48.0)
    # Closed in window: 1, 2, 3, 5; three of them merged.
    assert values["prs_closed"] == 4
    assert values["merge_rate"] == pytest.approx(0.75)
    # First reviews among opened PRs: 2 h (PR 1) and 24 h (PR 3).
    assert values["median_hours_to_first_review"] == pytest.approx(13.0)
    # 2 of 4 opened PRs reviewed within 24 h (24 h counts as within).
    assert values["share_reviewed_within_24h"] == pytest.approx(0.5)
    # Issues 10, 11, 12 opened; 10 (2 d), 11 (7 d), 13 (25 d) closed.
    assert values["issues_opened"] == 3
    assert values["issues_closed"] == 3
    assert values["median_days_to_close_issue"] == pytest.approx(7.0)
    # Runs 100, 101, 103, 104, 106 completed; 102 cancelled, 105 after window.
    assert values["ci_runs_completed"] == 5
    # 101 failure + 103 timed_out.
    assert values["ci_failure_rate"] == pytest.approx(0.4)
    # Durations 10, 6, 30, 8 (106 has no start time).
    assert values["median_ci_duration_min"] == pytest.approx(9.0)


def test_bots_can_be_included(data):
    values = kpis.summary(data, include_bots=True)
    assert values["prs_merged"] == 4
    assert values["issues_opened"] == 4


def test_weekly_breakdown(data):
    weekly = kpis.weekly(data).set_index("week_start")
    assert list(weekly.index) == ["2026-05-04", "2026-05-11"]

    first = weekly.loc["2026-05-04"]
    assert first["prs_opened"] == 2
    assert first["prs_merged"] == 1
    assert first["merge_rate"] == pytest.approx(0.5)
    assert first["median_hours_to_first_review"] == pytest.approx(2.0)
    assert first["issues_closed"] == 1
    assert first["ci_failure_rate"] == pytest.approx(0.5)

    second = weekly.loc["2026-05-11"]
    assert second["median_pr_lead_time_h"] == pytest.approx(192.0)
    assert second["merge_rate"] == pytest.approx(1.0)
    assert second["median_days_to_close_issue"] == pytest.approx(16.0)
    assert second["ci_failure_rate"] == pytest.approx(1 / 3)
    assert second["median_ci_duration_min"] == pytest.approx(19.0)


def test_weekly_counts_add_up_to_summary(data):
    weekly = kpis.weekly(data)
    values = kpis.summary(data)
    for name in ("prs_opened", "prs_merged", "issues_closed", "ci_runs_completed"):
        assert weekly[name].sum() == values[name]


def test_week_start_is_monday():
    moments = pd.Series(pd.to_datetime(["2026-05-04 23:59:59", "2026-05-10 12:00:00"]))
    assert list(kpis.week_start(moments).dt.strftime("%Y-%m-%d")) == ["2026-05-04"] * 2


@pytest.mark.parametrize(
    "conclusion, outcome",
    [
        ("success", "pass"),
        ("failure", "fail"),
        ("timed_out", "fail"),
        ("startup_failure", "fail"),
        ("cancelled", "other"),
        ("skipped", "other"),
        ("", "other"),
    ],
)
def test_ci_outcome(conclusion, outcome):
    assert kpis.ci_outcome(conclusion) == outcome


def test_write_results(data, tmp_path):
    out = tmp_path / "results"
    kpis.write_results(data, out)
    summary = pd.read_csv(out / "kpi_summary.csv")
    assert set(summary["kpi"]) == set(kpis.KPIS)
    report = (out / "kpi_summary.md").read_text(encoding="utf-8")
    assert "| CI failure rate | 40.0% |" in report


def test_empty_window_gives_no_crash(data_dir):
    (data_dir / "window.csv").write_text("window_start,window_end\n2025-01-01,2025-01-07\n", encoding="utf-8")
    values = kpis.summary(kpis.load(data_dir))
    assert values["prs_opened"] == 0
    assert values["merge_rate"] is None
    assert values["median_pr_lead_time_h"] is None


def test_review_share_is_unknown_without_review_data(data_dir):
    path = data_dir / "pull_requests.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], [line.split(",") for line in lines[1:]]
    column = header.split(",").index("first_review_at")
    for row in rows:
        row[column] = ""
    path.write_text("\n".join([header] + [",".join(r) for r in rows]) + "\n", encoding="utf-8")
    values = kpis.summary(kpis.load(data_dir))
    assert values["share_reviewed_within_24h"] is None
    assert values["median_hours_to_first_review"] is None
