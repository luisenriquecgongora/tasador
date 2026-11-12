# Engineering Standards & Observability Contract

### 1. Pre-Change Invariant Manifest & Autonomous Execution
- **Autonomous Bias for Action:** Execute tasks end-to-end without pausing for conversational confirmation. Do NOT use interactive question tools or ask clarifying questions for routine technical decisions, naming, library choices, or architectural trade-offs. Apply the **"Convention over Consultation"** rule: pick the standard, simplest idiom, implement it, and document assumptions in commit messages.
- **Inspect Before Inquiring:** Never ask a question that can be answered by searching the repository, reading existing tests, or checking config files.
- **The Escalation Boundary (Approval Required):** Stop and request user approval ONLY if:
  1. An action is **irreversible or destructive** (e.g., dropping databases/tables, deleting files or directories, destructive git commands like `push --force` or `reset --hard`, or purging untracked data). You **MUST analyze the impact, explain the blast radius, and require explicit confirmation** before running any such command.
  2. Requirements are fundamentally contradictory with zero sensible default after inspecting the codebase.
- **Pre-Change Invariant Manifest:** Before executing code modifications touching more than one file, output a compact change manifest table and **immediately proceed to implementation in the same turn without waiting for confirmation**:

| Target File / Module | Nature of Change | Breaking / Invariant Impact |
| :--- | :--- | :--- |
| `path/to/file` | `[refactor / feat / fix / rename]` | `None / Exposes new method / Renames prop` |

Keep the manifest itself to the table: no narrative summary or conversational setup around it. Diagrams belong in the docs and the pull request (section 11).

### 2. Diff Noise & Spurious Edit Invariants
- **No Unsolicited Formatting:** Do NOT reformat, re-indent, or adjust comments on lines unrelated to your assigned task. Leave untouched code exactly as it is.
- **Import Hygiene:** Do not randomly shuffle or re-order existing import statements unless adding or removing an active dependency.
- **Isolate Mass Renames:** If a task requires renaming an identifier across multiple files, isolate that rename into a dedicated `refactor(<scope>): rename X to Y` commit with ZERO other logic changes.

### 3. Conventional Commit Standards
All commits must follow:
`<type>(<scope>): <short imperative subject under 50 chars>`

- **Allowed Types:** `feat`, `fix`, `refactor`, `perf`, `chore`, `test`, `docs`, `build`, `ci` (add `model`, `data`, `eval`, `exp` for ML pipelines).
- **Allowed Scopes:** [`engine`, `vision`, `search`, `pricing`, `desktop`, `client`, `deploy`, `config`, `deps`, `hooks`, `ci`, `docs`]
- **Rule:** Never mention AI, LLMs, or prompt tools in commit messages.

### 4. Commit Granularity & Invariants
- **Atomic Commits:** Do not bundle refactorings, feature code, and config tweaks into a single commit.
- **Commit Sequence:**
  1. `refactor(<scope>): ...` (Structural prep or renames only)
  2. `feat(<scope>): ...` or `fix(<scope>): ...` (Logic changes)
  3. `test(<scope>): ...` (Specs/tests)
- **Blast-Radius Cap:** A single commit must not exceed 5 staged files (excluding lockfiles/documentation).
- **Bisect Safety:** Every individual commit must compile and pass tests.

### 5. Definition of Done
- Run `python scripts/verify.py` before reporting a task complete. It runs exactly what CI runs: ruff and pytest for `services/tasador-engine`, and a secret scan when `gitleaks` is installed. Skipped checks are listed; "NOT COMPLETE" means you are not done.
- Narrow it while iterating: `--only python`, `--only secrets`.
- Python: uses `services/tasador-engine/.venv`; set `VERIFY_PYTHON` to override (e.g. in a git worktree).
- A task that changes behavior adds or updates a test in `services/tasador-engine/tests/`. Tests never call a real model or search API: providers are faked (`tests/conftest.py`) or run against mocked HTTP (`tests/test_providers.py`).

### 6. Repository Map
| Path | What lives there |
| :--- | :--- |
| `services/tasador-engine/src/tasador_engine/main.py` | FastAPI app: `GET /health`, `GET /v1/capabilities`, `POST /v1/identify` (multipart or JSON) |
| `.../schemas.py` | The public contract (version "1"). Adding optional fields is fine; renaming or removing one is breaking |
| `.../pipeline.py` | Runs the stages in order; turns provider failures into `warnings` |
| `.../images.py` | Decode, resize, strip EXIF; image URL allowlist and download |
| `.../local_readers.py` | Barcodes (zxing-cpp) and printed text (RapidOCR); both optional extras |
| `.../providers/vision.py` | `AnthropicVision` (Claude SDK) and `OpenAICompatibleVision` (every other provider and local server, via presets in `config.VISION_PRESETS`), output schema and prompt |
| `.../providers/search.py` | `AnthropicWebSearch`, `SearxngSearch`: goal queries in, raw `SearchHit`s out |
| `.../product_key.py` | GTIN checksum, product key (GTIN > brand + model > name), one search query per goal and language |
| `.../links.py` | Sorts hits into official / reference / purchase; shop and marketplace domain lists |
| `.../net.py` | Outbound HTTPS settings: every client uses the OS trust store (`truststore`). New HTTP clients must use `tls_context()` / `anthropic_client()` |
| `.../settings_ui.py`, `.../static/settings.html` | Local settings page (`/`, `/ui/state`, `/ui/settings`): only with `TASADOR_SETTINGS_UI`, loopback + localhost Host + per-start key; swaps `app.state.pipeline` live |
| `.../ollama_models.py` | Lists installed Ollama models that read images; used by the launcher and the settings page |
| `.../pages.py` | Opens result pages (robots.txt, private-address guard) and reads schema.org Product/Offer data to verify and price them |
| `.../pricing.py`, `.../matching.py` | Price parsing and ranges; similarity to the caller's `known_items` |
| `docs/ARCHITECTURE.md` | Colored map of the stages and deployments. Start here |
| `docs/RESEARCH.md` | How Google Lens, Amazon, eBay and price comparison sites do it; the target pipeline and its status |

Dependency direction: `schemas`/`config` <- `images`, `local_readers`, `pricing`, `matching`, `product_key` <- `providers` <- `links`, `pages` <- `pipeline` <- `main`. Providers never import the pipeline. Tests never open real web pages: use `FakeFetcher` (`tests/conftest.py`) or `httpx.MockTransport`.

### 7. Commands
- Python setup: `cd services/tasador-engine && uv venv && uv pip install -e ".[ocr,barcode,heic,dev]"` (on this machine add `--native-tls`)
- Run: `uv run uvicorn tasador_engine.main:app_from_env --factory --reload --port 8000`
- Fully local stack: `docker compose --profile local up`
- Settings: every environment variable is listed in `.env.example` and read in `config.py`.

### 8. Gotchas
- The Anthropic SDK 2.x runs on `httpx2`, not `httpx`: mock its transport with `httpx2.MockTransport`. The other providers use plain `httpx`.
- Claude calls use the beta endpoint for `fallbacks="default"` (refusal fallback); keep `betas=["server-side-fallback-2026-07-01"]` with it.
- The OCR extra pulls in OpenCV and needs ~450 MB RAM: leave it out on a 512 MB host (see `render.yaml`).
- Settings are generic (`TASADOR_VISION_*`, `TASADOR_SEARCH_*`); the old `TASADOR_OPENAI_*` / `TASADOR_ANTHROPIC_*` names are read as fallbacks in `config.py`. Add a provider by adding a preset (OpenAI-compatible) or a class with the same method as the others.
- SearXNG must have the JSON format enabled (`deploy/searxng/settings.yml`).
- Ruff rules are pinned in `services/tasador-engine/pyproject.toml`.

### 9. Never
- Never read, print or commit `.env` files or API keys.
- Never force-push or rewrite published history.
- Never commit `.venv/`, `dist/`, `node_modules/` or `src-tauri/target/`.
- Never send real photos or API keys in tests or fixtures.

### 10. Supporting another coding agent
Instructions live only in this file and skills only in `.agents/skills/`; every agent tool gets them from there.
- **Tool reads `AGENTS.md` and `.agents/skills/`** (Codex, OpenCode, Cursor, Copilot, Antigravity): nothing to do. For Antigravity CLI (`agy`), run with `--mode auto` or configure `"agentMode": "auto"` in `~/.gemini/antigravity-cli/settings.json` to prevent interactive tool approval prompts.
- **Tool reads only its own files** (like Claude Code: `CLAUDE.md`, `.claude/skills/`):
  1. Add a one-line pointer file to `AGENTS.md` in the tool's format (see `CLAUDE.md`).
  2. Add its skills folder to `SKILL_MIRRORS` in `scripts/verify.py`, then run `python scripts/verify.py --only skills --fix` to create the symlinks.
  3. Confirm the tool sees the skills: ask it to list its available skills (e.g. `opencode debug skill`, `codex exec`, `claude -p`, `agy -p`).
- Never copy a skill into a tool's folder; link it, so there is one copy to keep current.

### 11. Documentation & Traceability
Code changes arrive faster than people can read them, so a person must be able to follow every change from the docs and the history alone.
- **Explain visually:** describe structure and flows with text plus a colored Mermaid diagram (GitHub renders it) and a legend for the colors; use the palette in `docs/ARCHITECTURE.md`. Write for an engineer new to the repo: define terms and say why, not only what.
- **Keep the map current:** `docs/ARCHITECTURE.md` shows the repo's parts and how data flows between them. A change that adds, removes or rewires a part updates that diagram in the same pull request.
- **Explain every pull request:** what changed and why, how it was verified, and, when a structure or flow changed, a small diagram of the new shape.
- **One intent per commit:** someone reading the history commit by commit must be able to tell what each one did and why (see section 4).
