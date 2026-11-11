# Contributing

Thank you for helping. foxcast's value rests on honest measurement, so a few rules are stricter than usual.

## The rules that matter most

1. **The scorecard comes first.** [`SCORECARD.md`](SCORECARD.md) defines what the project must prove. A pass line changes only with a dated entry in its change log saying why.
2. **The model never writes a number.** Every figure in a dossier comes from a fetched fact, inserted by code.
3. **No lookahead.** Nothing may use data dated after the forecast time, and every fitted piece is refit from past data only.
4. **No scorecard number from a notebook.** Numbers that count come from `foxcast` commands.
5. **Ship code, never data.** Do not commit fetched data, and scrub keys from recorded test fixtures.

## Workflow

- Set up with `uv sync` and `uv run pre-commit install`; the hooks run lint, formatting, strict type checks and file checks on every commit. Run the tests with `uv run pytest`.
- Releases are published to PyPI from `.github/workflows/release.yml` by trusted publishing, only when a maintainer publishes a GitHub release and a reviewer approves the `pypi` environment. No PyPI token is stored anywhere.
- Write the test first. Coverage must stay at or above 80%.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`).
- Copied code needs an entry in [`THIRD_PARTY.md`](THIRD_PARTY.md) and its licence in `LICENSES/`.

## Conduct

See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
