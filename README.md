# Development Process Analytics

**How fast does an open-source team turn ideas into merged code, where does work wait, and how often does the build break?**

Work in progress. The goal is a Qlik Sense dashboard that combines three
sources from a public GitHub repository (issues, pull requests and GitHub
Actions CI runs) on one shared calendar, with every dashboard KPI recomputed
independently in Python to validate the numbers.

## Status

- [x] Project setup
- [ ] GitHub API client with pagination, rate-limit handling and retries
- [ ] Extract issues, pull requests and CI runs to CSV
- [ ] KPI calculations in Python
- [ ] Qlik Sense data model and dashboard
- [ ] Validation: Qlik vs Python

## Development

```bash
pip install -r requirements.txt
python -m pytest
```

## License

MIT
