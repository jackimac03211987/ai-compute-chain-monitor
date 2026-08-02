# Contributing

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-runtime.lock
python -m unittest discover -s tests -v
```

## Pull Requests

1. Create a focused branch.
2. Keep behavioral changes scoped and add tests for security-sensitive or user-facing behavior.
3. Run the full unit test suite before opening a pull request.
4. Explain the motivation, behavior change, operational impact, and validation performed.

Do not commit generated market snapshots, user databases, tenant files, logs, exports, backup archives, spreadsheets, API keys, tokens, private endpoints, or machine-specific deployment files. Use synthetic fixtures in tests.

## Code Style

- Support Python 3.10 or newer.
- Prefer standard-library functionality and existing project helpers.
- Use atomic writes and existing data locks for shared JSON state.
- Preserve tenant isolation and server-derived workspace paths.
- Keep interface-probe targets subject to the default SSRF policy.
- Avoid adding new browser dependencies without documenting their license and vendoring strategy.

## Market Data

Do not submit captured or redistributed quote datasets. New providers must document authentication, rate limits, delay semantics, licensing assumptions, and failure behavior. Tests must use local fixtures or mocked responses.
