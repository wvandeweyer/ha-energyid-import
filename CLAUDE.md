# Working rules for Claude Code

- Read `docs/SPEC.md` and `docs/api-notes.md` before every task. If the API differs
  from the spec, update `api-notes.md` and the spec first.
- Work per phase on its own branch with a pull request; CI must be green.
- Write tests together with the code; a phase is not done without its tests.
- Never commit an API key or a non-anonymised API response.
- Code, comments, commits and README are in English.
- Never bypass a gate from section 9: no `--no-verify`, no suppressions, no
  relaxing `pyproject.toml` without Wim's agreement.
- If a rule cannot be met, stop and present the conflict instead of working
  around it.

## Commands

```sh
uv sync                                   # install the locked toolchain
uv run pre-commit run --all-files         # every local gate
uv run pytest                             # tests with 100% branch coverage
uv run python scripts/discover.py         # phase 0, needs ENERGYID_API_KEY
```

## Notes

- `typings/` holds type stubs for `pytest-homeassistant-custom-component`, which
  ships without `py.typed`. Extend a stub when a test needs more of its API.
- Setup and the config flow use a fail-fast client (`retry_backoff=()`); only
  periodic syncs retry. HA already retries `ConfigEntryNotReady`.
