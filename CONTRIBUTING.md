# Contributing

Thanks for your interest in improving futu-tracker.

## Local development

Two ways to run the app while iterating:

**Docker (matches CI):**
```bash
echo "FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')" > .env
docker compose up --build
```
App at http://localhost:5000.

**Bare Python:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
python app.py
```

## Style

- Lint with `ruff check .` before opening a PR (CI runs the same command).
- Match the surrounding code: comment density, naming, and imports.

## Pull requests

1. Branch off `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep PRs focused — one topic per PR.
3. Fill in the PR template. Note any user-visible or config changes.
4. CI must pass (lint + docker build).

## Reporting issues

- Bugs: use the Bug Report template. Include reproduction steps and logs.
- Features: use the Feature Request template. Explain the problem before proposing a solution.
- Security: **do not** open a public issue — see [SECURITY.md](SECURITY.md).
