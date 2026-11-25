"""Model Hub, host hardware detection, and Ollama model manager for Tasador."""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import subprocess
import threading
from typing import Any

import httpx

logger = logging.getLogger("tasador_engine.model_hub")

CURATED_VISION_MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen2.5vl:3b",
        "name": "Qwen 2.5 VL 3B",
        "family": "Qwen",
        "params": "3.2B",
        "quant": "Q4_K_M",
        "size_bytes": 2_200_000_000,
        "vram_required_mb": 2400,
        "description": "Ultra-fast multimodal model. High accuracy for invoice rows, labels, and equipment serial plates.",
        "recommended": True,
        "speed": "⚡ Ultra-Fast (~1.2s)",
    },
    {
        "id": "qwen2.5vl:7b",
        "name": "Qwen 2.5 VL 7B",
        "family": "Qwen",
        "params": "7.6B",
        "quant": "Q4_K_M",
        "size_bytes": 4_800_000_000,
        "vram_required_mb": 5000,
        "description": "Flagship 7B multimodal model. Exceptional detail on complex tables, multi-page PDFs, and obscure DJ/AV hardware.",
        "recommended": False,
        "speed": "🚀 Medium (~3.8s)",
    },
    {
        "id": "llava:7b",
        "name": "LLaVA 1.5 7B",
        "family": "LLaVA",
        "params": "7.0B",
        "quant": "Q4_0",
        "size_bytes": 4_700_000_000,
        "vram_required_mb": 4800,
        "description": "Battle-tested general visual reasoning model for photographic asset identification.",
        "recommended": False,
        "speed": "🚀 Medium (~4.2s)",
    },
    {
        "id": "minicpm-v:8b",
        "name": "MiniCPM-V 8B",
        "family": "MiniCPM",
        "params": "8.0B",
        "quant": "Q4_K_M",
        "size_bytes": 5_800_000_000,
        "vram_required_mb": 6000,
        "description": "Specialized high-resolution OCR architecture with high spatial reasoning for small invoice typography.",
        "recommended": False,
        "speed": "🐢 Slower (~6.5s)",
    },
]


_CACHED_HARDWARE: dict[str, Any] | None = None


def detect_hardware() -> dict[str, Any]:
    """Detect GPU name, VRAM (MB), and host system RAM (MB). Cached after first check."""
    global _CACHED_HARDWARE
    if _CACHED_HARDWARE is not None:
        return dict(_CACHED_HARDWARE)

    info: dict[str, Any] = {
        "gpu_name": "Integrated Graphics",
        "vram_mb": 0,
        "ram_mb": 8192,
        "has_dedicated_gpu": False,
    }

    # 1. Total System RAM via ctypes
    if os.name == "nt":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                info["ram_mb"] = int(stat.ullTotalPhys // (1024 * 1024))
        except Exception:
            pass
    elif hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            info["ram_mb"] = int((pages * page_size) // (1024 * 1024))
        except Exception:
            pass

    # 2. Fast check via nvidia-smi (<50ms)
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit():
                info["gpu_name"] = parts[0]
                info["vram_mb"] = int(parts[1])
                info["has_dedicated_gpu"] = True
                _CACHED_HARDWARE = dict(info)
                return info
    except Exception:
        pass

    # 3. Fallback on Windows via PowerShell CIM
    if os.name == "nt":
        try:
            cmd = "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json"
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                if isinstance(data, dict):
                    data = [data]
                best_gpu: dict[str, Any] | None = None
                max_vram = 0
                for item in data:
                    name = str(item.get("Name") or "")
                    ram = int(item.get("AdapterRAM") or 0)
                    is_dedicated = any(k in name.upper() for k in ("NVIDIA", "GEFORCE", "RTX", "GTX", "RADEON"))
                    if is_dedicated and ram > 0:
                        best_gpu = {"name": name, "vram": ram}
                        break
                    if ram > max_vram:
                        max_vram = ram
                        best_gpu = {"name": name, "vram": ram}
                if best_gpu:
                    info["gpu_name"] = best_gpu["name"]
                    info["vram_mb"] = int(best_gpu["vram"] // (1024 * 1024))
                    info["has_dedicated_gpu"] = any(
                        k in best_gpu["name"].upper() for k in ("NVIDIA", "GEFORCE", "RTX", "GTX", "RADEON")
                    )
        except Exception as exc:
            logger.debug("Failed to detect GPU via Win32_VideoController: %s", exc)

    _CACHED_HARDWARE = dict(info)
    return info


class ModelPullTracker:
    """Tracks background downloading of an Ollama model."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active_model: str | None = None
        self.status: str = "idle"  # idle, pulling, completed, error
        self.progress_percent: int = 0
        self.completed_bytes: int = 0
        self.total_bytes: int = 0
        self.message: str = ""
        self.error: str | None = None
        self._cancel_flag: bool = False

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_model": self.active_model,
                "status": self.status,
                "progress_percent": self.progress_percent,
                "completed_bytes": self.completed_bytes,
                "total_bytes": self.total_bytes,
                "message": self.message,
                "error": self.error,
            }

    def cancel(self) -> None:
        with self._lock:
            self._cancel_flag = True
            self.status = "idle"
            self.message = "Cancelled by user"

    def start_pull(self, model_id: str, ollama_url: str = "http://localhost:11434") -> bool:
        with self._lock:
            if self.status == "pulling" and self.active_model == model_id:
                return False
            self.active_model = model_id
            self.status = "pulling"
            self.progress_percent = 0
            self.completed_bytes = 0
            self.total_bytes = 0
            self.message = "Connecting to Ollama..."
            self.error = None
            self._cancel_flag = False

        threading.Thread(target=self._run_pull_thread, args=(model_id, ollama_url), daemon=True).start()
        return True

    def _run_pull_thread(self, model_id: str, ollama_url: str) -> None:
        url = f"{ollama_url.rstrip('/')}/api/pull"
        try:
            with httpx.Client(timeout=600) as client:
                with client.stream("POST", url, json={"name": model_id, "stream": True}) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"Ollama returned HTTP {response.status_code}")
                    for line in response.iter_lines():
                        if self._cancel_flag:
                            break
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                            status_text = msg.get("status", "")
                            completed = msg.get("completed", 0)
                            total = msg.get("total", 0)
                            percent = int((completed / total * 100)) if total > 0 else 0

                            with self._lock:
                                self.message = status_text
                                self.completed_bytes = completed
                                self.total_bytes = total
                                if percent > self.progress_percent:
                                    self.progress_percent = percent
                        except Exception:
                            pass

            with self._lock:
                if not self._cancel_flag:
                    self.status = "completed"
                    self.progress_percent = 100
                    self.message = f"Successfully pulled {model_id}"
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.error = str(exc)
                self.message = f"Failed to pull {model_id}: {exc}"


GLOBAL_PULL_TRACKER = ModelPullTracker()


def get_model_catalog(
    active_provider: str,
    active_model: str,
    ollama_url: str = "http://localhost:11434",
) -> dict[str, Any]:
    """Return enriched catalog of models, local installation status, and hardware sizing."""
    hardware = detect_hardware()
    vram_available = hardware.get("vram_mb", 0)

    # Check installed Ollama models
    installed_models: set[str] = set()
    ollama_online = False
    try:
        res = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=2)
        if res.status_code == 200:
            ollama_online = True
            for m in res.json().get("models", []):
                name = m.get("name", "")
                installed_models.add(name)
                # Also add name without :latest tag
                if name.endswith(":latest"):
                    installed_models.add(name[:-7])
    except Exception:
        pass

    enriched: list[dict[str, Any]] = []
    for m in CURATED_VISION_MODELS:
        m_id = m["id"]
        is_installed = (m_id in installed_models) or (f"{m_id}:latest" in installed_models)
        is_active = (active_provider == "ollama" and active_model == m_id)

        # Fit calculation
        req_vram = m["vram_required_mb"]
        if vram_available >= req_vram:
            fit_level = "fits_vram"
            fit_badge = "🟢 100% Fits in VRAM"
            fit_desc = "Runs fully accelerated in dedicated GPU memory without RAM spill."
        elif (vram_available + hardware["ram_mb"]) >= req_vram + 2048:
            fit_level = "partial_offload"
            fit_badge = "🟡 Partial Offload"
            fit_desc = f"Fits dedicated VRAM ({vram_available} MB) + spills into System RAM."
        else:
            fit_level = "cpu_heavy"
            fit_badge = "🔴 CPU Heavy"
            fit_desc = "Exceeds recommended GPU VRAM; will run primarily on CPU RAM."

        enriched.append({
            **m,
            "installed": is_installed,
            "active": is_active,
            "fit_level": fit_level,
            "fit_badge": fit_badge,
            "fit_desc": fit_desc,
        })

    return {
        "hardware": hardware,
        "ollama_online": ollama_online,
        "ollama_url": ollama_url,
        "active_provider": active_provider,
        "active_model": active_model,
        "models": enriched,
        "pull_status": GLOBAL_PULL_TRACKER.get_status(),
    }
