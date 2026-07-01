# Security Policy

## Supported versions

Only the `main` branch receives security fixes.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Instead, use GitHub's private vulnerability reporting:
1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Include:
   - Affected version / commit SHA
   - Reproduction steps
   - Impact assessment
   - Suggested fix, if you have one

You should get an initial acknowledgement within a few days. A fix and coordinated disclosure will follow.

## Scope

In scope:
- Credential handling (Futu API keys, session secrets)
- SQLite data-at-rest handling
- Auth / session logic in `app.py`
- Docker image configuration

Out of scope:
- Vulnerabilities in the Futu OpenD binary itself — report those to Futu.
- Attacks that require an already-compromised host.
