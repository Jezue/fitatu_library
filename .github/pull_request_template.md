## What and why

<!-- What changes for a user of the library, the tools or fitatu-mcp, and why. Link the issue. -->

## Checklist

- [ ] `ruff check .`, `mypy src demo.py example.py catering_sync.py`, `pytest -q` and `pre-commit run --all-files` pass
- [ ] Tests run offline (fake server or mocks); nothing in the diff calls the real API
- [ ] No personal data: no session files, tokens, e-mails, names, or your own meals or weights
- [ ] `CHANGELOG.md` (under `## [Unreleased]`) and the docs the change touches (README, docs/AGENTS.md, docs/API_REFERENCE.md, docs/PLANNER.md) are updated
- [ ] A changed agent tool: the docs/AGENTS.md table and `tests/fixtures/tool_contract.json` are updated, and docs/RELEASING.md's compatibility policy says which version part it bumps
