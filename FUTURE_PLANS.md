# Tasador: future plans

What is built today is the engine (`services/tasador-engine`). These are the next pieces, in order.

| # | Piece | Why |
| :--- | :--- | :--- |
| 1 | **Desktop app installer** (`apps/desktop`) | The first version is built: a Tauri window with status, port and settings that runs the engine from this folder. Next: bundle the engine with PyInstaller as a sidecar (like Emiliano), an NSIS installer, and auto-update from the personal Cloudflare R2 bucket, so others can install it without Python. |
| 2 | **Client SDKs** (`packages/client-ts`, `clients/python`) | Small, no ML dependencies: `identify(photos, context)`, plus `pickEndpoint([local, hosted])` that calls `/v1/capabilities` and prefers a local copy when one answers. |
| 3 | **Crop item and label** | Find the object and its nameplate in the photo before reading, so OCR and small local models see the right region (see `docs/RESEARCH.md`). |
| 3b | **Visual search provider** | Optional Lens-style search for items with no readable label. |
| 3c | **Affiliate link rewriting** | Opt-in step that turns purchase links into referral links for networks the operator belongs to. |
| 3d | **Marketplace price APIs** | Allegro REST API (needs a verified application) and eBay Browse API (used prices) as extra purchase sources. Structured prices beat prices read from snippets. |
| 4 | **Image similarity for duplicates** | SigLIP image embeddings so `known_items` can match by photo, not only by brand and model text. |
| 5 | **Async jobs** | `POST /v1/jobs` + polling for slow price searches (15-30 s with web search), so callers behind short HTTP timeouts don't wait on one request. |
| 6 | **Eval set** | 50-100 labelled photos (nameplate visible / not, branded / generic) to compare providers and effort levels by accuracy and cost per item. |
