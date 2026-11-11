# Development Process Analytics

**How fast does an open-source team turn ideas into merged code, where does work wait, and how often does the build break?**

Work in progress. The goal is a Qlik Sense dashboard that combines three
sources from a public GitHub repository (issues, pull requests and GitHub
Actions CI runs) on one shared calendar, with every dashboard KPI recomputed
independently in Python to validate the numbers.

## Status

- [x] Project setup
- [x] GitHub API client with pagination, rate-limit handling and retries
- [x] Extract issues, pull requests and CI runs to CSV
- [ ] KPI calculations in Python
- [ ] Qlik Sense data model and dashboard
- [ ] Power BI version of the dashboard (data model and DAX measures ready, see [`powerbi/`](powerbi/))
- [ ] Validation: Qlik vs Python

## Extract the data

```bash
export GITHUB_TOKEN=...   # Windows PowerShell: $env:GITHUB_TOKEN="..."
python -m devstats.extract --repo pydata/xarray --since 2026-05-01 --until 2026-10-31
```

Writes `pull_requests.csv`, `issues.csv`, `labels.csv`, `ci_runs.csv`,
`window.csv` and `meta.json` to `data/`. All timestamps are UTC. Options:
`--skip-reviews`, `--ci-branch main`, `--ci-event push`, `--out folder`.

## Development

```bash
pip install -r requirements.txt
python -m pytest
```

## License

MIT
