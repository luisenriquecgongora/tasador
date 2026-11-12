# tasador-engine

FastAPI service: photos of a physical item in, identification and prices out. See the repository [README](../../README.md) for the API and [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for how it works.

```bash
uv venv && uv pip install -e ".[ocr,barcode,heic,dev]"
uv run uvicorn tasador_engine.main:app_from_env --factory --reload --port 8000
uv run pytest -q
```
