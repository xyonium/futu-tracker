# Docker Test Run & GitHub Repo Setup — Design

**Date:** 2026-07-01
**Scope:** Verify `futu-tracker` runs via Docker locally on port 5000, and add standard GitHub repository files (CI, Dependabot, templates, LICENSE, etc.). No remote repo is created — files are prepared locally only.

## 1. Docker Test Run

- Use the existing `docker-compose.yml` (builds from `Dockerfile`, exposes `5000:5000`, mounts `./data`).
- Generate a random value for `FLASK_SECRET_KEY` and store it in a local `.env` (gitignored). `docker-compose.yml` already reads it via `${FLASK_SECRET_KEY:-...}`.
- Verify: `docker compose build` succeeds, `docker compose up -d` starts the container, and `curl http://localhost:5000/` returns HTTP 200 (or a redirect to login).

## 2. Repo Hygiene Files

| File | Purpose |
| --- | --- |
| `.dockerignore` | Exclude `.git`, `__pycache__`, `data/`, `.env`, `docs/`, `*.md` (except README) from build context. Keeps image lean and avoids leaking local state. |
| `.gitignore` (expand) | Python artifacts (`__pycache__`, `*.pyc`, `.pytest_cache`), venvs (`.venv`, `venv`), IDE (`.vscode`, `.idea`), runtime data (`data/`, `*.db`, `*.sqlite`), env (`.env`), logs (`*.log`). |
| `LICENSE` | MIT license, holder = "eli". |
| `CONTRIBUTING.md` | Local dev setup (venv + docker), branch/PR conventions, style hint (ruff). |
| `SECURITY.md` | Private disclosure instructions and supported versions. |
| `.github/ISSUE_TEMPLATE/bug_report.md` | Structured bug report. |
| `.github/ISSUE_TEMPLATE/feature_request.md` | Structured feature request. |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR summary + checklist. |

## 3. GitHub Actions CI — `.github/workflows/ci.yml`

Triggers: `push` to `main`, `pull_request` targeting `main`.

Jobs (Ubuntu-latest, Python 3.11 only):

1. **lint** — install `ruff`, run `ruff check .` (non-blocking config; project has no existing ruff config so we use defaults).
2. **docker-build** — `docker build -t futu-tracker:ci .` to catch Dockerfile regressions. No registry push.

## 4. Dependabot — `.github/dependabot.yml`

Weekly schedule for:
- `pip` (root `requirements.txt`)
- `docker` (root `Dockerfile`)
- `github-actions` (`.github/workflows/`)

## Out of Scope

- Creating a remote GitHub repo (user chose "Skip creating remote").
- Publishing images to a registry.
- Adding unit tests (project currently has none; CI only lints and builds).
- Python version matrix (3.11 only, matching the Dockerfile).
