"""Activity tracking, execution step logging, and pipeline history.

Maintains an in-memory ring buffer of recent identification pipeline executions,
recording inputs, intermediate step inputs/outputs (ZXing barcodes, RapidOCR lines,
Vision AI reasoning, Web search hits & price verification), final outputs,
and cached raw images for one-click pipeline re-runs.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .images import PreparedImage
from .schemas import IdentifyContext, IdentifyResponse


def make_thumbnail_b64(img: Image.Image, max_size: int = 512, quality: int = 80) -> str:
    """Create a high-quality base64 JPEG thumbnail for document viewer and UI previews."""
    try:
        thumb = img.copy()
        thumb.thumbnail((max_size, max_size))
        buf = io.BytesIO()
        thumb.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:
        return ""


@dataclass
class StepTrace:
    step_id: str
    title: str
    status: str  # "ok", "warning", "error", "skipped"
    duration_ms: int
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "warnings": self.warnings,
            "details": self.details,
        }


@dataclass
class ActivityRun:
    id: str
    created_at: str
    status: str  # "running", "completed", "failed"
    duration_ms: int = 0
    error: str | None = None
    input_summary: dict[str, Any] = field(default_factory=dict)
    thumbnails: list[str] = field(default_factory=list)
    steps: list[StepTrace] = field(default_factory=list)
    active_step: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None

    # Stored for one-click re-run capability
    _cached_images: list[PreparedImage] = field(default_factory=list, repr=False)
    _cached_context: IdentifyContext | None = field(default=None, repr=False)

    def to_summary(self) -> dict[str, Any]:
        """Compact summary for history list cards without heavy step payloads."""
        best = None
        if self.output and "best" in self.output:
            b = self.output["best"]
            best = {
                "name": b.get("name"),
                "brand": b.get("brand"),
                "model": b.get("model"),
                "gtin": b.get("gtin"),
                "confidence": b.get("confidence", 0.0),
                "type": b.get("type"),
            }
        price_summary = None
        if self.output and "price" in self.output:
            p = self.output["price"]
            price_summary = {
                "currency": p.get("currency"),
                "new": p.get("new"),
                "used": p.get("used"),
                "verified_only": p.get("verified_only", False),
            }

        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "active_step": self.active_step,
            "image_count": len(self.thumbnails),
            "thumbnail": self.thumbnails[0] if self.thumbnails else None,
            "best": best,
            "price": price_summary,
            "provider": self.provider,
            "can_rerun": bool(self._cached_images and self._cached_context),
        }

    def to_detail(self) -> dict[str, Any]:
        """Full execution record including step in/out artifacts."""
        summary = self.to_summary()
        summary.update({
            "input_summary": self.input_summary,
            "thumbnails": self.thumbnails,
            "steps": [s.to_dict() for s in self.steps],
            "active_step": self.active_step,
            "output": self.output,
        })
        return summary


def default_db_path() -> Path:
    env_p = os.environ.get("TASADOR_DB_PATH")
    if env_p:
        return Path(env_p)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        app_dir = Path(local_app_data) / "com.luisenriquecgongora.tasador"
        if app_dir.exists():
            return app_dir / "runs.db"
    home_dir = Path.home() / ".tasador"
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        return home_dir / "runs.db"
    except Exception:
        return Path("tasador_runs.db")


class ActivityTracker:
    def __init__(self, max_runs: int = 100, db_path: str | Path | None = None):
        self._max_runs = max_runs
        self._runs: deque[ActivityRun] = deque(maxlen=max_runs)
        self._by_id: dict[str, ActivityRun] = {}
        self._lock = threading.Lock()
        self._db_path = Path(db_path) if db_path else default_db_path()
        self._init_db()
        self._load_from_db()

    def configure_db(self, db_path: str | Path) -> None:
        with self._lock:
            self._db_path = Path(db_path)
            self._init_db()
            self._runs.clear()
            self._by_id.clear()
            self._load_from_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        duration_ms INTEGER DEFAULT 0,
                        error TEXT,
                        best_name TEXT,
                        best_brand TEXT,
                        best_model TEXT,
                        best_confidence REAL DEFAULT 0.0,
                        thumbnails_json TEXT,
                        input_summary_json TEXT,
                        output_json TEXT,
                        provider_json TEXT,
                        steps_json TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC)")
        except Exception:
            pass

    def _save_run_to_db(self, run: ActivityRun) -> None:
        try:
            best = (run.output and run.output.get("best")) or {}
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runs (
                        id, created_at, status, duration_ms, error,
                        best_name, best_brand, best_model, best_confidence,
                        thumbnails_json, input_summary_json, output_json,
                        provider_json, steps_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.created_at,
                        run.status,
                        run.duration_ms,
                        run.error,
                        best.get("name"),
                        best.get("brand"),
                        best.get("model"),
                        float(best.get("confidence") or 0.0),
                        json.dumps(run.thumbnails),
                        json.dumps(run.input_summary),
                        json.dumps(run.output) if run.output else None,
                        json.dumps(run.provider) if run.provider else None,
                        json.dumps([s.to_dict() for s in run.steps]),
                    ),
                )
        except Exception:
            pass

    def _load_from_db(self) -> None:
        try:
            if not self._db_path.exists():
                return
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (self._max_runs,)
                )
                rows = cursor.fetchall()
                for r in reversed(rows):
                    steps_data = json.loads(r["steps_json"] or "[]")
                    steps = [
                        StepTrace(
                            step_id=s.get("step_id", ""),
                            title=s.get("title", ""),
                            status=s.get("status", "ok"),
                            duration_ms=s.get("duration_ms", 0),
                            inputs=s.get("inputs", {}),
                            outputs=s.get("outputs", {}),
                            warnings=s.get("warnings", []),
                            details=s.get("details"),
                        )
                        for s in steps_data
                    ]
                    run = ActivityRun(
                        id=r["id"],
                        created_at=r["created_at"],
                        status=r["status"],
                        duration_ms=r["duration_ms"] or 0,
                        error=r["error"],
                        input_summary=json.loads(r["input_summary_json"] or "{}"),
                        thumbnails=json.loads(r["thumbnails_json"] or "[]"),
                        steps=steps,
                        output=json.loads(r["output_json"]) if r["output_json"] else None,
                        provider=json.loads(r["provider_json"]) if r["provider_json"] else None,
                    )
                    self._runs.append(run)
                    self._by_id[run.id] = run
        except Exception:
            pass

    def start_run(self, images: list[PreparedImage], context: IdentifyContext) -> ActivityRun:
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()

        # Generate lightweight thumbnails for quick UI inspection
        thumbnails = [make_thumbnail_b64(img.image) for img in images]

        input_summary = {
            "image_count": len(images),
            "images": [
                {
                    "index": i,
                    "width": img.image.width,
                    "height": img.image.height,
                    "size_bytes": len(img.jpeg),
                }
                for i, img in enumerate(images)
            ],
            "currency": context.currency,
            "country": context.country,
            "locale": context.locale,
            "search_prices": context.search_prices,
            "task_hint": context.hint,
            "allowed_types": context.allowed_types,
            "known_items_count": len(context.known_items),
        }

        run = ActivityRun(
            id=run_id,
            created_at=now,
            status="running",
            input_summary=input_summary,
            thumbnails=thumbnails,
            _cached_images=list(images),
            _cached_context=context.model_copy(),
        )

        with self._lock:
            # If ring buffer is full, remove oldest from _by_id
            if len(self._runs) >= self._max_runs:
                oldest = self._runs[0]
                self._by_id.pop(oldest.id, None)
            self._runs.append(run)
            self._by_id[run_id] = run
            self._save_run_to_db(run)

        return run

    def add_step(
        self,
        run_id: str,
        step_id: str,
        title: str,
        status: str,
        duration_ms: int,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        warnings: list[str] | None = None,
        details: str | None = None,
    ) -> None:
        trace = StepTrace(
            step_id=step_id,
            title=title,
            status=status,
            duration_ms=duration_ms,
            inputs=inputs,
            outputs=outputs,
            warnings=warnings or [],
            details=details,
        )
        with self._lock:
            run = self._by_id.get(run_id)
            if run:
                run.steps.append(trace)
                self._save_run_to_db(run)

    def set_active_step(self, run_id: str, step_id: str, title: str, details: str | None = None) -> None:
        with self._lock:
            run = self._by_id.get(run_id)
            if run and run.status == "running":
                run.active_step = {
                    "step_id": step_id,
                    "title": title,
                    "details": details,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                self._save_run_to_db(run)

    def complete_run(
        self,
        run_id: str,
        response: IdentifyResponse,
        duration_ms: int,
        provider_info: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            run = self._by_id.get(run_id)
            if run:
                run.status = "completed"
                run.duration_ms = duration_ms
                run.active_step = None
                run.output = response.model_dump()
                run.provider = provider_info or (
                    response.provider.model_dump() if response.provider else None
                )
                self._save_run_to_db(run)

    def fail_run(self, run_id: str, error_message: str, duration_ms: int = 0) -> None:
        with self._lock:
            run = self._by_id.get(run_id)
            if run:
                run.status = "failed"
                run.active_step = None
                run.error = error_message
                run.duration_ms = duration_ms
                self._save_run_to_db(run)

    def list_history(self) -> list[dict[str, Any]]:
        with self._lock:
            # Newest runs first
            return [run.to_summary() for run in reversed(self._runs)]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._by_id.get(run_id)
            return run.to_detail() if run else None

    def get_replay_data(self, run_id: str) -> tuple[list[PreparedImage], IdentifyContext] | None:
        with self._lock:
            run = self._by_id.get(run_id)
            if run and run._cached_images and run._cached_context:
                return list(run._cached_images), run._cached_context.model_copy()
            return None

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._by_id.clear()
            try:
                with sqlite3.connect(str(self._db_path)) as conn:
                    conn.execute("DELETE FROM runs")
            except Exception:
                pass


# Global activity singleton across the engine
GLOBAL_ACTIVITY = ActivityTracker(max_runs=50)
