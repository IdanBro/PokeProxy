# M7-CWD — The test suite only works if you happen to run it from `app/`

**Severity:** Medium · **Wave:** 5 · **Status:** Fixed
**Files:** `app/tests/conftest.py` (new)

## Problem

`POKEPROXY_CONFIG=config/rules.json` — used by both `.env.example` and every test — is a relative path. `rules.py`'s `load_rules()` resolves it via `Path(config_path).read_text()`, which resolves against the process's current working directory. Nothing anchors it to the app's own location. Run `pytest` from the repo root instead of `app/`, and the app can't find `config/rules.json` at all.

## Production Impact

None directly — a container always sets a fixed `WORKDIR`, so this exact failure mode cannot occur in a real deployment; the path resolves correctly every time because the working directory is never ambiguous there. The real impact is local-dev and CI ergonomics: **35 of 94 tests fail** when run from the repo root (re-measured today), because after H1 made a missing rules file a hard startup `SystemExit` instead of a per-request read, every test that starts the app via `TestClient(app)` now inherits the failure — not just the handful that call `load_rules()` directly. That number was 3 of 48 before H1, and 25 of 73 at the last audit; it grows every time a new test starts the app, because nothing pins the working directory. Part 3's CI runner has no reason to default to `app/`, so an unaddressed version of this would make a perfectly correct commit look like a broken build.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where to fix it | anchor `POKEPROXY_CONFIG` resolution inside the app itself (e.g., resolve relative paths against the package location) · **fix the test suite's own CWD assumption** | **test suite** — the app's path resolution is correct for every real deployment scenario (container `WORKDIR`, or `cd app && uvicorn ...` locally); rearchitecting it would be solving a problem production doesn't actually have |
| How to pin it | a shell wrapper/Makefile that always `cd`s before invoking pytest · **a `conftest.py` that pins the working directory itself** | **`conftest.py`** — works no matter how or from where pytest is invoked (an IDE test runner, a bare `pytest` command, CI), rather than depending on every invocation remembering to `cd` first |

## Decision

New `app/tests/conftest.py`, four lines: a `pytest_configure()` hook that `chdir`s to the directory containing the test files' own parent (`app/`), computed from `Path(__file__)` rather than assumed from the invoking shell. This is the standard pytest pattern for a test suite whose correctness depends on working directory — it makes `pytest` behave identically regardless of where it's invoked from, which is exactly the invariant Part 3's CI needs.

No application code changed. `POKEPROXY_CONFIG` stays a relative path in `.env.example`/production config — that's correct and unrelated to this fix.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13), from three different working directories with the identical command:

| Working directory | Result |
|---|---|
| `app/` (previous baseline) | **94 passed** |
| repo root | **94 passed** (was 59 passed / 35 failed before this fix) |
| `/tmp` (fully unrelated directory) | **94 passed** — proves the fix isn't coincidentally working for two directories that happen to share a relationship to the repo |

`ruff check .` clean.

No new pytest test was added for this fix specifically — a test that verifies "pytest works correctly" would be testing the test harness with the test harness, which is circular. The regression proof here is the direct multi-CWD invocation above, matching how other infrastructure-level fixes in this project (e.g., C1's "the documented Quick Start now reaches `Application startup complete`") were verified by direct execution rather than a unit test.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| `conftest.py` uses `os.chdir`, global process state | Standard, well-understood pytest idiom for this exact problem; this project doesn't use `pytest-xdist` or parallel test workers where a shared working directory could race, so no isolation concern applies |
| Production/`.env.example` still uses a relative `POKEPROXY_CONFIG` | Deliberate — correct and sufficient given every real deployment path (container `WORKDIR`, documented local dev flow) already has a well-defined working directory; not a gap this fix needed to touch |
