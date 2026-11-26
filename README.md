# QueryMind

**AI-Powered Natural Language to Read-Only SQL Assistant**

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-REST%20API-009688?logo=fastapi&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-Frontend-FF4B4B?logo=streamlit&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Read--Only-003B57?logo=sqlite&logoColor=white)
![SQLGlot](https://img.shields.io/badge/SQLGlot-AST%20Validator-4B5563)
![Ruff](https://img.shields.io/badge/Ruff-Lint%20%26%20Format-D7FF64?logo=ruff&logoColor=black)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

QueryMind is a local-first Natural Language to SQL assistant. Users upload their own SQLite databases, inspect discovered schemas automatically, ask questions in plain English, have generated SQL checked by an AST validator, execute approved read-only queries, and inspect results alongside plain-English explanations.

---

## Problem Statement

Connecting natural-language interfaces or LLMs directly to SQL databases introduces three common engineering problems:

- **Unsafe SQL Generation:** Prompt injection or model hallucinations can produce destructive statements (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `PRAGMA`, `ATTACH`, stacked queries) or reference nonexistent tables and ambiguous columns.
- **Credential & Data Leakage:** Unfiltered error messages or naive prompts risk leaking API keys, local filesystem paths, or raw database rows to external services.
- **Rigid Demo Setups:** Many NL-to-SQL prototypes hardcode a single bundled database instead of letting users upload, validate, and switch across their own SQLite files.

QueryMind addresses these issues through a decoupled FastAPI + Streamlit architecture combining **SQLGlot AST validation**, **dual-engine SQLite authorizer isolation**, **parameterized query execution**, **metadata-only LLM prompts**, and a **multi-file SQLite upload and Database Library workflow**.

---

## Key Features

- **No Bundled Sample Database:** Starts clean with a welcome screen requiring users to upload their own SQLite databases.
- **Single & Multi-File SQLite Upload:** Upload 1–15 `.db`, `.sqlite`, or `.sqlite3` files simultaneously via drag-and-drop or file browser (max 25 MB per file, max 100 MB per batch).
- **Independent Per-File Validation:** Checks file extensions, filename safety/path traversal, 16-byte SQLite 3 headers, database readability, and presence of at least one user table—one invalid file never blocks valid files in the same batch.
- **Database Library & Live Switching:** View uploaded databases (filename, status, size, table count), select an active database, switch between uploaded databases without restarting, disconnect, or refresh schemas.
- **Automatic Schema Discovery:** Discovers tables, column names, types, nullability, primary keys, and foreign keys via SQLAlchemy.
- **Dual NL-to-SQL Generation Modes:**
  - **Google Gemini (`google-genai`):** Uses structural schema metadata only (never database row contents) to generate SQL and explanations.
  - **Deterministic Fallback Mode:** Works offline with zero API keys for `SELECT`, `COUNT`/`SUM`/`AVG`, parameterized `WHERE` filters, `ORDER BY` + `LIMIT`, `GROUP BY`, and foreign-key `JOIN`s.
- **Read-Only Safeguards:** SQLGlot AST allowlist + literal parameterization + SQLite `?mode=ro` URI + `PRAGMA query_only = ON` + default-deny `sqlite3.set_authorizer()` + query timeouts + row limits.
- **Results & Export:** Interactive Pandas table, execution metrics, clause-by-clause SQL explanation, and CSV download.

---

## Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | Streamlit, Pandas, `httpx` |
| **Backend API** | FastAPI, Uvicorn, Pydantic v2, `pydantic-settings` |
| **Database & SQL Safety** | SQLite3 (`mode=ro`, `set_authorizer`), SQLAlchemy 2, SQLGlot |
| **AI / NL-to-SQL** | Google Gemini (`google-genai`), SQLGlot Deterministic Fallback |
| **Testing & Quality** | `pytest`, `pytest-cov`, Ruff (lint + format) |

---

## Architecture Overview

QueryMind separates the presentation layer, API layer, orchestration service, AST validator, and dual SQLite engines:

```text
[Streamlit UI] ──HTTP──▶ [FastAPI API (:8001)] ──▶ [DatabaseManager] / [QueryService]
                                                             │
   ┌─────────────────────────┬───────────────────────────────┼───────────────────────┐
   ▼                         ▼                               ▼                       ▼
[SchemaInspector]     [Gemini / Fallback]             [SQLValidator (AST)]    [SQLExplainer]
   │                                                         │
   │ (Trusted Inspection Engine)                             ▼ (ValidatedQuery)
   └──────────────────▶ [SQLite File] ◀────────────── [QueryExecutor (Restricted Execution Engine)]
```

For full component descriptions and a Mermaid diagram, see **[docs/architecture.md](docs/architecture.md)**.

---

## How QueryMind Works

### Database Upload & Management Workflow

1. **Welcome Screen:** On startup with no database connected, Streamlit displays the upload interface (`Welcome to QueryMind`).
2. **Upload & Validation (`DatabaseManager`):**
   - Accepts 1 file (via `/api/v1/databases/upload`, connecting immediately when no DB is active) or up to **15 files** in one batch (via `/api/v1/databases/upload-batch`).
   - Enforces backend limits: **15 files/batch**, **25 MB/file**, **100 MB total/batch**.
   - Rejects unsafe filenames (`..`, `/`, `\`, `:`, null bytes) and extensions outside `.db`, `.sqlite`, `.sqlite3`.
   - Verifies the `SQLite format 3\x00` magic header, stores each valid file in `data/uploads/` under a unique collision-safe filename (preserving original display names without overwriting existing uploads), and verifies readability and `>= 1` user table via `SchemaInspector`.
   - Failed uploads are deleted immediately and never overwrite or disturb a currently connected database.
3. **Library & Switching:** Uploaded databases appear in the **Database Library** where users can connect, switch active databases, refresh schemas, or disconnect. Uploaded files persist in `data/uploads/` until manually deleted.

### Natural-Language-to-SQL Workflow

1. **Schema Context:** `SchemaInspector` inspects the active database's structure (`SchemaSnapshot`) using the trusted read-only inspection engine.
2. **SQL Generation:**
   - **Gemini Mode:** When `QUERYMIND_GEMINI_API_KEY` is set (and `QUERYMIND_LLM_PROVIDER` is `auto` or `gemini`), `PromptBuilder` sends only structural schema metadata and the user's question to Gemini.
   - **Deterministic Fallback Mode:** When no API key is configured (or `QUERYMIND_LLM_PROVIDER=fallback`), `DeterministicFallbackProvider` maps supported English patterns into parameterized SQLGlot AST queries, failing explicitly on unsupported phrasing.
3. **Validation & Parameterization (`SQLValidator`):** Parses SQL with SQLGlot, enforces read-only AST rules and lexical column scoping, injects/clamps `LIMIT`, extracts scalar literals into `:p_N` bind parameters, and produces an immutable `ValidatedQuery`.
4. **Execution & Explanation:** `QueryExecutor` runs `ValidatedQuery` on the restricted read-only execution engine and formats results with Pandas; `SQLExplainer` provides a human-readable explanation.

---

## SQL Validation & Security Model

### Safeguards Implemented
- **Dual-Engine SQLite Isolation:** `create_inspection_engine()` allows only read-only schema introspection pragmas and `sqlite_master`/`sqlite_schema`, while `create_execution_engine()` denies **all** `PRAGMA` statements, writes, DDL, `ATTACH`/`DETACH`, system catalogs, and unapproved functions via `?mode=ro`, `PRAGMA query_only = ON`, and default-deny `sqlite3.set_authorizer()`.
- **SQLGlot AST Allowlist:** Permits only single-statement `SELECT`, non-recursive `WITH ... SELECT`, and set operations (`UNION`/`INTERSECT`/`EXCEPT`). Rejects `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`, `ATTACH`, `PRAGMA`, `WITH RECURSIVE`, `sqlite_*` tables, and dangerous functions.
- **Context-Aware Column Scoping:** Validates columns against `FROM`, `JOIN`, CTE projections, derived subqueries, and SQLite `SELECT` alias rules (`GROUP BY`, `HAVING`, `ORDER BY`), rejecting unknown or ambiguous references.
- **Parameterized Execution & Limits:** `ValidatedQuery` freezes parameter mappings in `MappingProxyType` and enforces 1:1 placeholder matching. `QueryExecutor` enforces `set_progress_handler` timeouts and `fetchmany(limit + 1)` row caps.
- **Metadata-Only Prompts & Sanitized Errors:** DB rows are never sent to Gemini by default; API errors and logs scrub secrets (`SecretStr`) and local file paths.

### Security Limitations
- SQLGlot validation is **not** a complete substitute for SQLite's own parser and execution protections, and does not guarantee semantic correctness.
- SQLite `set_authorizer()` protections operate at SQLite statement preparation time and do **not** provide row-level or column-level access control.
- Query timeouts and result limits reduce but do **not** eliminate all possible resource-exhaustion vectors (for example, expensive cross-joins or complex schemas during upload inspection).
- The application currently lacks authentication, rate limiting, and multi-user session isolation; backend state is process-global. Do **not** expose it directly on a public or shared network.
- **External LLM Data Transfer:** When Gemini mode is enabled, the user's natural-language question and structural database metadata are sent to Google's Gemini API. Database row contents are not included in prompts by default. Avoid including sensitive information in questions sent to external LLM providers.
- Uploaded database files in `data/uploads/` persist on disk until manually removed.

---

## Installation Instructions (Windows PowerShell)

1. **Clone the repository and enter the project directory:**

   ```powershell
   git clone https://github.com/krishav83/QueryMind.git
   cd QueryMind
   ```

2. **Create and activate a Python 3.11+ virtual environment:**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks script execution, run:

   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   ```

   Then activate the virtual environment again.

3. **Install QueryMind and its dependencies:**

   **With Gemini LLM support and development tools:**

   ```powershell
   python -m pip install -e ".[llm,dev]"
   ```

   **Without Gemini SDK (deterministic fallback mode):**

   ```powershell
   python -m pip install -e ".[dev]"
   ```

---

## Environment Configuration

Copy `.env.example` to `.env` (optional—defaults work out of the box in deterministic fallback mode):

```powershell
Copy-Item .env.example .env
```

| Variable | Default | Description |
| :--- | :--- | :--- |
| `QUERYMIND_LLM_PROVIDER` | `auto` | `auto` (Gemini if key set, else fallback), `fallback`, or `gemini` |
| `QUERYMIND_GEMINI_API_KEY` | *(empty)* | Google Gemini API key (leave blank for deterministic fallback) |
| `QUERYMIND_GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `QUERYMIND_MAX_ROWS` | `500` | Maximum rows returned per query (`1–5000`) |
| `QUERYMIND_QUERY_TIMEOUT_SECONDS` | `5.0` | SQLite execution timeout in seconds |
| `QUERYMIND_MAX_UPLOAD_SIZE_MB` | `25` | Max size per uploaded SQLite file |
| `QUERYMIND_MAX_UPLOAD_FILES_PER_BATCH` | `15` | Max files allowed per batch upload |
| `QUERYMIND_MAX_TOTAL_UPLOAD_BATCH_MB` | `100` | Max combined size per batch upload |
| `QUERYMIND_API_HOST` | `127.0.0.1` | FastAPI bind host |
| `QUERYMIND_API_PORT` | `8001` | FastAPI bind port (kept on `8001` so `8000` stays free) |
| `QUERYMIND_API_URL` | `http://127.0.0.1:8001` | Backend URL used by the Streamlit frontend |
### Enabling Google Gemini

To enable AI-powered SQL generation using Google Gemini, configure the following values in your `.env` file:

```env
QUERYMIND_LLM_PROVIDER=gemini
QUERYMIND_GEMINI_API_KEY=your_gemini_api_key
```

Replace `your_gemini_api_key` with your own API key. Keep your `.env` file private and never commit it to GitHub.

If no Gemini API key is configured, QueryMind can operate using its deterministic fallback provider.

> **Windows Tip:** Never commit `.env` to Git. If specifying file paths in `.env` on Windows, use forward slashes (`data/uploads`) or unquoted paths rather than double-quoted backslashes.

## Starting the Application (Windows PowerShell)

Open **two** PowerShell terminals in the `QueryMind` root directory:

**Terminal 1 — Start FastAPI Backend (port 8001):**
```powershell
.\.venv\Scripts\python.exe -m uvicorn querymind.api.app:app --host 127.0.0.1 --port 8001
```

**Terminal 2 — Start Streamlit Frontend:**
```powershell
.\.venv\Scripts\python.exe -m streamlit run src\querymind\ui\streamlit_app.py
```

Open the local URL displayed by Streamlit (typically `http://localhost:8501`), upload one or more `.db`, `.sqlite`, or `.sqlite3` files on the welcome screen, click **Connect** (or select a database from the **Database Library**), and start asking questions.

---

## Example Questions

Adapt table and column names to match your uploaded SQLite schema:

- `"Show all <table_name>"`
- `"How many <table_name> are there?"`
- `"Count <table_name> by <category_column>"`
- `"What is the total <amount_column> by <group_column>?"`
- `"Top 5 <table_name> by <numeric_column>"`
- `"Show <table_name> where <numeric_column> > 100"`
- `"Show total <amount_column> of <child_table> per <parent_table> <name_column>"` *(for tables linked by a foreign key; Gemini mode supports broader SQL phrasing)*

---

## API Endpoints

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Backend readiness, active database name/status, and provider mode |
| `GET` | `/api/v1/databases` | List uploaded databases managed in the current session (no file paths exposed) |
| `POST` | `/api/v1/databases/upload` | Upload, validate, and connect a single SQLite database |
| `POST` | `/api/v1/databases/upload-batch` | Upload and independently validate up to 15 SQLite databases |
| `POST` | `/api/v1/databases/select` | Switch active connection to a previously uploaded database |
| `POST` | `/api/v1/databases/disconnect` | Disconnect the active database |
| `GET` | `/api/v1/schema` | Return discovered tables, columns, primary keys, and foreign keys |
| `POST` | `/api/v1/schema/refresh` | Force re-inspection of the active database schema |
| `POST` | `/api/v1/query` | Generate, validate, execute (read-only), and explain SQL for a question |

---

## Testing Instructions

All automated tests use isolated temporary directories and temporary SQLite databases:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=src/querymind --cov-report=term-missing -v
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
```

---

## Known Limitations

- **Single-User Local State:** Active database selection and uploaded database metadata live in global process memory (`app.state`) without per-user session isolation or authentication.
- **No Upload Deletion Endpoint / Auto-Cleanup:** Files in `data/uploads/` remain on disk across restarts until removed manually.
- **No Inspection Progress Timeout:** `SchemaInspector` does not attach `set_progress_handler` during upload schema discovery.
- **No Pre-Parse HTTP Body-Size Middleware:** Multipart uploads are parsed by Starlette/FastAPI before `DatabaseManager` checks batch limits.
- **SQLite Only:** Other SQL dialects (PostgreSQL, MySQL) are not yet supported.
- **OpenAI-Compatible Adapter Deferred:** Config placeholders exist in `Settings`, while only `DeterministicFallbackProvider` and `GeminiProvider` are enabled.

---

## Future Improvements

- Per-session database isolation, authentication, and rate limiting for multi-user deployment.
- Individual database delete endpoints, automatic `data/uploads/` retention/cleanup, and progress-handler timeout during schema inspection.
- Pre-multipart HTTP request body size middleware and streaming temp-file staging for large batches.
- Additional LLM adapters (OpenAI-compatible, Anthropic, Ollama) behind `BaseLLMProvider`.
- PostgreSQL and MySQL support using server-side read-only database roles.
- Automated Streamlit UI tests via `streamlit.testing.v1.AppTest`.

---

## License

QueryMind is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for the complete license text.

Third-party dependencies installed via `pyproject.toml` retain their respective licenses. The MIT License grants software usage permissions without warranty and is not a security certification.
