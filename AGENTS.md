# Agent Instructions

## Domain and Architecture

- **Domain Glossary:** See [GLOSSARY.md](GLOSSARY.md).
- **Architecture Decision Records (ADRs):** See [docs/adr/](docs/adr/).

## Repository Map

- `src/github_intelligence/`: Core backend and dashboard logic (`dashboard.py`, `dashboard_coordination.py`, `dashboard_data.py`).
- `src/github_intelligence/dashboard_assets/`: Frontend assets (`index.html`, `app.css`, `app.js`, and `modules/loader.js`, `modules/sidebar.js`, `modules/history.js`).
- `tests/unit/`: Fast Python unit tests (no external services or database required).
- `tests/integration/`: Python integration tests (require the native PostgreSQL test harness).
- `tests/js/`: Node.js tests for frontend behavior and navigation (`*.test.cjs`).
- `scripts/`: Operational scripts organized into `scripts/db/` and `scripts/branding/`.
- `config/`: Local configuration files (e.g. `dashboard.local.json`, `database.local.json`).
- `docs/`: Architecture decision records (`docs/adr/`), agent documentation (`docs/agents/`), and archive (`docs/archive/`).

## Development & Quality Checks

Run these commands to validate changes:

- **Python unit tests:** `uv run pytest tests/unit`
- **Python integration tests:** `uv run pytest tests/integration` (requires native PostgreSQL 17 test cluster; see `scripts/db/prepare_test_postgres.py`)
- **Full test suite:** `uv run pytest`
- **JavaScript tests:** `node --test "tests/js/*.test.cjs"`
- **Linter:** `uv run ruff check src tests scripts`
- **Type checker:** `uv run mypy`

Continuous Integration is configured in `.github/workflows/ci.yml` and runs automatically on pull requests and pushes to `main` (unit tests, JS tests, Ruff check, and mypy).

## Shell and Execution Guidelines

- When running commands in Windows PowerShell, **avoid inline multiline scripts or complex quotes** (e.g. `python -c "..."` or `gh pr create --body "..."`), which frequently fail due to shell string interpolation and escaping issues.
- Instead, write a temporary file to disk (such as in a scratch directory or temporary `.py`/`.md` file) and execute via file reference (`python script.py` or `gh pr create --body-file file.md`).

## Agent skills

### Issue tracker

GitHub issues tracked via `gh` CLI (`Fabrycio13/github-intelligence`). See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage roles mapped 1:1. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (`GLOSSARY.md` + `docs/adr/`). See `docs/agents/domain.md`.
