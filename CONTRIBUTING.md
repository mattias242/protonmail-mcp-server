# Contributing to protonmail-mcp-server

Thank you for your interest in contributing!

## Development setup

```bash
git clone https://github.com/mattias242/protonmail-mcp-server
cd protonmail-mcp-server
uv sync
cp .env.example .env  # fill in your Bridge credentials
```

## Running tests

```bash
# Unit + BDD tests (no Bridge required)
uv run pytest tests/unit/ tests/features/ -v

# Integration tests (requires running ProtonMail Bridge)
uv run pytest tests/integration/ -m integration -v
```

## Workflow

This project follows a **RED → GREEN → BLUE** cycle:

1. **RED** — Write a failing test first
2. **GREEN** — Write the minimum code to make it pass, commit
3. **BLUE** — Refactor for clarity, commit

Please follow this cycle for any new functionality.

## Pull requests

- One logical change per PR
- Include tests for new behaviour
- Keep commits focused and descriptive
- Update `README.md` if you add or change a tool

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include:
- Your OS and ProtonMail Bridge version
- Steps to reproduce
- Expected vs actual behaviour

## Security issues

Do **not** open a public issue for security vulnerabilities. Email the maintainer directly instead.

## Code style

- Follow existing conventions (no formatter enforced yet)
- No hardcoded credentials — always use environment variables
- Log recipient counts, never addresses (BCC privacy)
