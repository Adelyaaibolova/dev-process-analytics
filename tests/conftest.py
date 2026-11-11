"""A tiny hand-made dataset whose KPIs are worked out by hand in test_kpis.py.

Window: Monday 2026-05-04 to Sunday 2026-05-17 (two full weeks).
"""

from pathlib import Path

import pytest

PULL_REQUESTS = """\
pr_number,title,state,author,author_is_bot,draft,created_at,closed_at,merged_at,first_review_at,first_reviewer,comments
1,Fast merge,closed,alice,0,0,2026-05-04 10:00:00,2026-05-05 10:00:00,2026-05-05 10:00:00,2026-05-04 12:00:00,bob,3
2,Rejected idea,closed,carol,0,0,2026-05-05 00:00:00,2026-05-06 00:00:00,,,,1
3,Reviewed after a day,closed,dave,0,0,2026-05-11 08:00:00,2026-05-13 08:00:00,2026-05-13 08:00:00,2026-05-12 08:00:00,alice,5
4,Bump dependency,closed,dependabot[bot],1,0,2026-05-06 00:00:00,2026-05-06 01:00:00,2026-05-06 01:00:00,,,0
5,Old branch finally merged,closed,erin,0,0,2026-04-28 00:00:00,2026-05-12 00:00:00,2026-05-12 00:00:00,2026-04-29 00:00:00,bob,9
6,Still open,open,frank,0,1,2026-05-16 00:00:00,,,,,0
"""

ISSUES = """\
issue_number,title,state,state_reason,author,author_is_bot,created_at,closed_at,comments
10,Crash on load,closed,completed,gina,0,2026-05-04 00:00:00,2026-05-06 00:00:00,2
11,Docs typo,closed,completed,hana,0,2026-05-07 00:00:00,2026-05-14 00:00:00,1
12,Feature request,open,,ivan,0,2026-05-12 00:00:00,,4
13,Old bug,closed,not_planned,jun,0,2026-04-20 00:00:00,2026-05-15 00:00:00,7
14,Automated report,closed,completed,github-actions[bot],1,2026-05-08 00:00:00,2026-05-08 06:00:00,0
"""

LABELS = """\
item_type,number,label
issue,10,bug
issue,13,bug
pr,1,enhancement
"""

CI_RUNS = """\
run_id,workflow,event,head_branch,status,conclusion,created_at,run_started_at,updated_at,run_attempt
100,CI,push,main,completed,success,2026-05-04 09:00:00,2026-05-04 09:00:00,2026-05-04 09:10:00,1
101,CI,pull_request,feature,completed,failure,2026-05-05 09:00:00,2026-05-05 09:00:00,2026-05-05 09:06:00,1
102,CI,pull_request,feature,completed,cancelled,2026-05-06 09:00:00,2026-05-06 09:00:00,2026-05-06 09:01:00,1
103,Docs,push,main,completed,timed_out,2026-05-12 09:00:00,2026-05-12 09:00:00,2026-05-12 09:30:00,1
104,CI,push,main,completed,success,2026-05-13 09:00:00,2026-05-13 09:00:00,2026-05-13 09:08:00,1
105,CI,push,main,completed,success,2026-05-18 00:00:00,2026-05-18 00:00:00,2026-05-18 00:05:00,1
106,CI,push,main,completed,success,2026-05-14 09:00:00,,2026-05-14 09:05:00,1
"""

WINDOW = "window_start,window_end\n2026-05-04,2026-05-17\n"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    files = {
        "pull_requests.csv": PULL_REQUESTS,
        "issues.csv": ISSUES,
        "labels.csv": LABELS,
        "ci_runs.csv": CI_RUNS,
        "window.csv": WINDOW,
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path
