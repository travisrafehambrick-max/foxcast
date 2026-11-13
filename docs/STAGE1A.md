# Stage 1A operator guide: SEC ingest, point-in-time dossiers, and claim checking

> **Research, not investment advice.** foxcast produces research dossiers and probability forecasts for educational and research purposes only. Nothing it outputs is financial, investment or trading advice, or a recommendation to buy or sell any security. It places no orders.

This guide describes how to run the Stage 1A command-line workflow (`foxcast ingest`, `foxcast dossier`, and `foxcast check-claims`) as it exists on `main` (through Stage 1A.6).

## Scope on `main`

- **`foxcast ingest` (Stage 1A.3):** Fetches one company's SEC record as of a specified date into the local DuckDB point-in-time fact store (`facts.duckdb`) and content-addressed raw source archive (`archive_dir`).
- **`foxcast dossier` (Stage 1A.6):** Builds a 19-section **no-model** dossier (`model: none: built without a model`) from the local fact store and source archive as of a specified date, writing both Markdown (`.md`) and JSON (`.json`). Sections backed by the Stage 1A SEC record (header, business summary, management insider holdings, financial health, capital structure, Form 4 market activity, 8-K events, sources, and notice) are filled from stored facts or marked with explicit item gaps; sections that require the research step or later-stage data sources are marked **Unavailable** with their exact reason (scorecard row 1d).
- **`foxcast check-claims` (Stage 1A.5 / 1A.6):** Reviews and checks submitted claims from a JSON file (`--claims`) against the local point-in-time fact store and source archive as of `--as-of` (scorecard rows 1a and 1c). It runs the exact same review and `check_dossier` path as the dossier builder, optionally writes a JSON report (`--out`), and exits with code 1 if any claim is withheld.
- **Coming next (not in this CLI path yet):** Language-model research step integration, the MCP tool server, and the run cost meter arrive in **Stage 1A.7**. 10-K section splitting waits on `edgartools` 6.x until **2026-11-16** (until then, 10-K, 10-Q, and 8-K filings are archived and stored as full-text passages).

---

## Prerequisites and SEC contact

- **uv:** [uv](https://docs.astral.sh/uv/) installed.
- **Python:** Python 3.12 or 3.13.
- **SEC contact (`FOXCAST_SEC_CONTACT`):** The SEC fair-access policy requires every automated client to identify itself in the `User-Agent` header with a name and email address. Set `FOXCAST_SEC_CONTACT` in your environment before running `doctor`, `ingest`, `dossier`, or `check-claims`:
  ```bash
  # POSIX shells (bash / zsh)
  export FOXCAST_SEC_CONTACT="Your Name you@example.com"
  ```
  ```powershell
  # PowerShell
  $env:FOXCAST_SEC_CONTACT = "Your Name you@example.com"
  ```
  - **Validation:** `foxcast` checks that `FOXCAST_SEC_CONTACT` is non-empty and contains an email address (`name@domain.tld`). Missing or malformed values fail immediately with exit code 1.
  - **When required:** Always required by `foxcast doctor` and `foxcast ingest`. Also required by `foxcast dossier` and `foxcast check-claims` when `--cik` is omitted, because resolving a ticker symbol to a CIK fetches `https://www.sec.gov/files/company_tickers.json` from the SEC. Passing `--cik <CIK>` to `foxcast dossier` or `foxcast check-claims` skips the SEC ticker lookup and runs entirely offline against local storage.
- **Keys in environment, never in config:** Never place secret keys (such as `FOXCAST_FRED_API_KEY`) in `foxcast.toml`; `foxcast` refuses to load a config file that sets secret fields.

---

## Source archive outside git (`archive_dir`) and the home-`.git` pitfall

Every fetched SEC payload (submissions index JSON, 10-K/10-Q/8-K filing HTML, Form 4 XML, and XBRL concept JSON) is saved under its SHA-256 digest in `archive_dir` so citations can be verified against the exact copy fetched at ingest time (scorecard row 1c).

Because raw SEC filings run to several megabytes and contain third-party text, **raw sources must never enter a git repository** (honesty rule 5: *Ship code, never data*).

### Default layout

`foxcast init` configures `archive_dir` **beside** `data_dir`, not inside it:

```toml
data_dir = "/home/you/.foxcast/data"
archive_dir = "/home/you/.foxcast/archive"
watchlist_size = 10
```

You can override `archive_dir` in `~/.foxcast/foxcast.toml` or via the `FOXCAST_ARCHIVE_DIR` environment variable.

### How the git guard works (`SourceArchive.outside_git`)

`foxcast ingest`, `foxcast dossier`, and `foxcast check-claims` all open the archive via `SourceArchive.outside_git(settings.archive_dir)`:

1. It walks `archive_dir` and every parent directory looking for a `.git` folder (or `.git` worktree file).
2. If an enclosing git work tree is found, it runs `git -C <repo> check-ignore --quiet <archive_dir>/` (with a trailing slash, ignoring inherited `GIT_*` variables).
3. If the directory is inside a git work tree and git does not ignore the directory itself, the command aborts with exit code 1:
   ```text
   the source archive <archive_dir> is inside the git work tree <repo> and git does not ignore it; raw filings must stay out of git (set archive_dir to a folder outside any repository, or ignore it there)
   ```

### Pitfall: dotfiles repository in `$HOME` (`~/.git`)

If you track your home directory in git (for example, a dotfiles repository with `~/.git`), then the default `~/.foxcast/archive` sits inside that git work tree. Unless your home repository explicitly ignores the archive folder, `foxcast ingest`, `foxcast dossier`, and `foxcast check-claims` will refuse to run.

> [!WARNING]
> **Ignoring individual files or shards is not enough.** Rules such as `*.bin`, `.foxcast/archive/*.bin`, or `.foxcast/archive/00/` are refused by `SourceArchive.outside_git` because git still traverses the archive directory. The **directory itself** must be ignored.

**How to fix a home-`.git` conflict (choose one):**

1. **Ignore the archive directory in `~/.gitignore`:** Add the directory path with a trailing slash to `~/.gitignore`:
   ```gitignore
   .foxcast/archive/
   ```
2. **Move `archive_dir` outside the work tree:** Set `archive_dir` in `~/.foxcast/foxcast.toml` (or export `FOXCAST_ARCHIVE_DIR`) to a directory on local disk that has no `.git` in any parent directory.

> [!NOTE]
> **If you version `data_dir` in git:** Keep `archive_dir` outside `data_dir` (never set `archive_dir` to an unignored path like `~/.foxcast/data/sources`). If a data repository is published, only a content-free hash index (`SourceArchive.write_index`, listing `<sha256> <bytes>`) and the SHA-256 hashes carried by facts may be committed.

---

## Point-in-time `--as-of` semantics

`foxcast ingest`, `foxcast dossier`, and `foxcast check-claims` all require `--as-of YYYY-MM-DD` to enforce point-in-time discipline (honesty rule 3: *No lookahead*).

### During `foxcast ingest`

`foxcast ingest <TICKER> --as-of YYYY-MM-DD` fetches the company's SEC record and discards anything made public after `YYYY-MM-DD` (`public_on <= as_of` and `filed <= as_of`):

- **Filing index & 8-K item codes:** Submissions index from the SEC API; 8-K item codes filed on or before `as_of` are stored as text facts.
- **Filing documents (`10-K`, `10-Q`, `8-K`):** Fetches the latest `10-K` and latest `10-Q` filed on or before `as_of`, plus up to 10 `8-K` filings filed within the 365 days ending on `as_of`. Raw files are saved in `archive_dir` and extracted paragraphs are stored as passages in `facts.duckdb`.
- **Form 4 insider trades:** Fetches up to the 20 newest Form 4 ownership filings filed on or before `as_of`, storing transaction and holding figures as numbers and text facts.
- **XBRL financial concepts:** Fetches 18 core concepts (revenue, cost of revenue, operating income, net income, diluted EPS, assets, liabilities, equity, cash, operating cash flow, capex, long-term debt, diluted shares, buybacks, stock-based compensation, dividends) and keeps only facts with `public_on <= as_of`.
- **Idempotent & non-fatal:** Re-running `ingest` for the same company and `--as-of` date adds nothing already stored. If a specific concept or filing is unavailable (e.g., HTTP 404 when a company does not report a given XBRL tag), `ingest` records the reason under `unavailable:` rather than aborting.

### During `foxcast dossier` and `foxcast check-claims`

Both `foxcast dossier <TICKER> --as-of YYYY-MM-DD` and `foxcast check-claims <TICKER> --as-of YYYY-MM-DD --claims <PATH>` read `facts.duckdb` solely through point-in-time `as_of` queries and `FactStore.at(as_of)` (`FactView`). Even if you later ingest newer filings into the same `facts.duckdb`, building a dossier or checking claims with an earlier `--as-of` date only sees facts and filings that were public on or before that `--as-of` date.

---

## Command sequence

Run these commands from the repository root:

1. **Sync dependencies**
   ```bash
   uv sync
   ```

2. **Initialize configuration (first time only)**
   ```bash
   uv run foxcast init
   ```
   *Writes `~/.foxcast/foxcast.toml` with default `data_dir` (`~/.foxcast/data`) and `archive_dir` (`~/.foxcast/archive`). Refuses to overwrite an existing config file.*

3. **Run pre-flight diagnostics**
   ```bash
   uv run foxcast doctor
   ```
   *Verifies Python 3.12/3.13, `foxcast.toml`, `FOXCAST_SEC_CONTACT`, at least 5 GB of free disk space, and live SEC reachability.*

4. **Ingest a company's SEC record as of a date**
   ```bash
   uv run foxcast ingest GOOGL --as-of 2026-11-12
   ```
   *Optional flags:*
   - `--cik 1652044`: Provide the SEC CIK directly to skip the live ticker-to-CIK lookup.
   - `--config /path/to/foxcast.toml`: Use a non-default config file.

   *Example output:*
   ```text
   CIK 1652044 as of 2026-11-12: 142 passages, 38 text facts, 215 numbers; 6 documents.
     unavailable: XBRL LongTermDebtNoncurrent: HTTP 404
   ```

5. **Build the point-in-time dossier (Markdown + JSON)**
   ```bash
   uv run foxcast dossier GOOGL --as-of 2026-11-12 --company "Alphabet Inc."
   ```
   *Optional flags:*
   - `--cik 1652044`: Provide the SEC CIK directly; skips contacting the SEC to resolve `GOOGL`.
   - `--out /path/to/output`: Write the `.md` and `.json` files to a custom directory instead of `<data_dir>/dossiers`.
   - `--company "Company Name"`: Short plain-text company name (up to 80 characters) shown in section 1 (`Header`).
   - `--holding holds|none|not-declared`: Operator position disclosure shown in section 1 (defaults to `not-declared`).
   - `--config /path/to/foxcast.toml`: Use a non-default config file.

   *Example output:*
   ```text
   GOOGL as of 2026-11-12: 9 sections filled, 10 unavailable; wrote ~/.foxcast/data/dossiers/GOOGL-2026-11-12.md and ~/.foxcast/data/dossiers/GOOGL-2026-11-12.json.
   ```

6. **Review and verify submitted claims (`foxcast check-claims`)**
   ```bash
   uv run foxcast check-claims GOOGL --as-of 2026-11-12 --claims claims.json --company "Alphabet Inc."
   ```
   *Required and optional flags:*
   - `--claims <PATH>`: Path to a JSON file containing either a list of submitted claims (`[{"section": "financial_health", "type": "fact", "text": "Revenue was {f_0123456789abcdef}.", "cites": ["f_0123456789abcdef"]}, ...]`) or an object `{"claims": [...], "companies": {"Alphabet": 1652044}}` supplying additional company-to-CIK entries for the words check (the CLI `<TICKER>` and `--company` always take precedence if keys collide).
   - `--cik 1652044`: Provide the SEC CIK directly; skips contacting the SEC to resolve `GOOGL`.
   - `--company "Company Name"`: Adds the company name mapped to the subject CIK in the words-check map.
   - `--out /path/to/report.json`: Write a JSON verification report (`counted`, `verified`, `withheld`, `rate`, `shown`, and `withheld_claims` with redacted reasons).
   - `--config /path/to/foxcast.toml`: Use a non-default config file.
   - **Exit status:** Exits `0` if every submitted claim is verified; exits `1` if any claim is withheld (printing each withheld claim ID and redacted reason).

---

## Output paths

| Artifact | Default path | Override | Notes |
|---|---|---|---|
| Configuration | `~/.foxcast/foxcast.toml` | `--config <PATH>` | Sets `data_dir`, `archive_dir`, and `watchlist_size`. Never holds secret keys. |
| Point-in-time fact store | `~/.foxcast/data/facts.duckdb` | `data_dir` in `foxcast.toml` or `FOXCAST_DATA_DIR` | DuckDB database storing numeric facts, text facts, and filing passages with `public_on`, `fetched_at`, and `source_sha256`. |
| Content-addressed source archive | `~/.foxcast/archive/<sha256[:2]>/<sha256>.bin` | `archive_dir` in `foxcast.toml` or `FOXCAST_ARCHIVE_DIR` | Raw fetched SEC payloads, written atomically and checked against their SHA-256 hash. Must stay outside git. |
| Dossier (Markdown) | `~/.foxcast/data/dossiers/<TICKER>-<YYYY-MM-DD>.md` | `--out <DIR>` | Human-readable 19-section dossier. Third-party text is single-line and Markdown-escaped; every figure cites its fact ID (`f_...`). |
| Dossier (JSON) | `~/.foxcast/data/dossiers/<TICKER>-<YYYY-MM-DD>.json` | `--out <DIR>` | Machine-readable dossier (`"schema": "foxcast.dossier/1"`). Written atomically alongside the `.md` file via `.tmp` staging files. |
| Claim check report (JSON) | *(stdout only by default)* | `--out <FILE>` | Optional JSON summary written by `foxcast check-claims` listing verified and withheld claim IDs and redacted reasons. |

---

## Stop conditions and common errors

1. **`set FOXCAST_SEC_CONTACT to 'Your Name you@example.com'` / `FOXCAST_SEC_CONTACT must include an email address` (exit code 1):**
   Export a valid `FOXCAST_SEC_CONTACT` containing both a name and an email address before running `doctor`, `ingest`, or `dossier` / `check-claims` (without `--cik`).
2. **`the source archive ... is inside the git work tree ... and git does not ignore it` (exit code 1):**
   `archive_dir` is inside a git repository (often a `~/.git` dotfiles repo or a git-tracked `data_dir`) that does not ignore the archive directory as a whole. Add `.foxcast/archive/` to that repository's `.gitignore` or point `archive_dir` / `FOXCAST_ARCHIVE_DIR` to a folder outside any git work tree.
3. **`No fact store at .../facts.duckdb. Run foxcast ingest for this ticker first.` (exit code 1):**
   `foxcast dossier` and `foxcast check-claims` require an existing local fact store. Run `foxcast ingest <TICKER> --as-of <YYYY-MM-DD>` first.
4. **`--as-of must be YYYY-MM-DD` (exit code 1):**
   Pass an ISO-8601 calendar date such as `2026-11-12`.
5. **`ticker '...' is not in the SEC company list` (exit code 1):**
   The symbol was not found in the SEC's current `company_tickers.json` mapping (for example, a delisted or renamed ticker). Pass `--cik <CIK>` explicitly to ingest, build a dossier, or check claims by SEC CIK.
6. **Withheld claims or invalid `--claims` JSON in `foxcast check-claims` (exit code 1):**
   `foxcast check-claims` exits with code 1 if `--claims` cannot be parsed or if any submitted claim fails review or verification (such as an unverified citation, numbers-rule violation, non-target section, or lookahead).
