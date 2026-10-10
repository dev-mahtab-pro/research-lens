import os
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from flask import Flask, render_template, request, jsonify
import requests
import PyPDF2
import io
from werkzeug.utils import secure_filename
try:
    import psutil
except ImportError:  # pragma: no cover - graceful fallback when dependency is missing
    psutil = None

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_BYTES", str(80 * 1024 * 1024)))

CPU_COUNT = os.cpu_count() or 8
DEFAULT_CPU_THREADS = max(4, min(16, CPU_COUNT - 2))
DEFAULT_DISCOVERY_THREADS = max(4, min(12, DEFAULT_CPU_THREADS))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:1b")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "2048"))
OLLAMA_NUM_GPU = int(os.environ.get("OLLAMA_NUM_GPU", "999"))
OLLAMA_NUM_THREAD = int(os.environ.get("OLLAMA_NUM_THREAD", "2"))
OLLAMA_NUM_BATCH = int(os.environ.get("OLLAMA_NUM_BATCH", "256"))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
DISCOVERY_NUM_CTX = int(os.environ.get("DISCOVERY_NUM_CTX", "2048"))
DISCOVERY_NUM_PREDICT = int(os.environ.get("DISCOVERY_NUM_PREDICT", "900"))
DISCOVERY_NUM_THREAD = int(os.environ.get("DISCOVERY_NUM_THREAD", "2"))
DISCOVERY_NUM_BATCH = int(os.environ.get("DISCOVERY_NUM_BATCH", "128"))
DISCOVERY_PARALLELISM = int(os.environ.get("DISCOVERY_PARALLELISM", "6"))
ANALYSIS_MAX_CHARS = int(os.environ.get("ANALYSIS_MAX_CHARS", "120000"))
PRIMARY_ANALYSIS_MAX_CHARS = int(os.environ.get("PRIMARY_ANALYSIS_MAX_CHARS", "180000"))
REFERENCE_ANALYSIS_MAX_CHARS = int(os.environ.get("REFERENCE_ANALYSIS_MAX_CHARS", "90000"))
OLLAMA_FORCE_GPU = os.environ.get("OLLAMA_FORCE_GPU", "true").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_MAIN_GPU = int(os.environ.get("OLLAMA_MAIN_GPU", "0"))
OLLAMA_LOW_VRAM = os.environ.get("OLLAMA_LOW_VRAM", "true").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_USE_MMAP = os.environ.get("OLLAMA_USE_MMAP", "false").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_USE_MLOCK = os.environ.get("OLLAMA_USE_MLOCK", "false").strip().lower() in ("1", "true", "yes", "on")
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}
DATASET_DIR = os.path.join(app.root_path, "datasets")
HISTORY_FILE = os.path.join(DATASET_DIR, "research_history.json")

MODEL_RUNTIME_LIMITS = {
    "gemma3:1b": {
        "analysis_num_ctx": int(os.environ.get("GEMMA_ANALYSIS_NUM_CTX", "4096")),
        "discovery_num_ctx": int(os.environ.get("GEMMA_DISCOVERY_NUM_CTX", "2048")),
        "analysis_num_predict": int(os.environ.get("GEMMA_ANALYSIS_NUM_PREDICT", "2048")),
        "discovery_num_predict": int(os.environ.get("GEMMA_DISCOVERY_NUM_PREDICT", "900")),
    },
    "mistral:latest": {
        "analysis_num_ctx": int(os.environ.get("MISTRAL_ANALYSIS_NUM_CTX", "3072")),
        "discovery_num_ctx": int(os.environ.get("MISTRAL_DISCOVERY_NUM_CTX", "2048")),
        "analysis_num_predict": int(os.environ.get("MISTRAL_ANALYSIS_NUM_PREDICT", "1536")),
        "discovery_num_predict": int(os.environ.get("MISTRAL_DISCOVERY_NUM_PREDICT", "800")),
    },
    "deepseek-r1:7b": {
        "analysis_num_ctx": int(os.environ.get("DEEPSEEK_ANALYSIS_NUM_CTX", "2048")),
        "discovery_num_ctx": int(os.environ.get("DEEPSEEK_DISCOVERY_NUM_CTX", "1536")),
        "analysis_num_predict": int(os.environ.get("DEEPSEEK_ANALYSIS_NUM_PREDICT", "1024")),
        "discovery_num_predict": int(os.environ.get("DEEPSEEK_DISCOVERY_NUM_PREDICT", "700")),
    },
}

MODEL_OPTIONS = [
    {
        "id": "gemma3:1b",
        "label": "Gemma 3 1B",
        "badge": "Fast",
        "description": "Best for quicker responses and lightweight local runs.",
        "strength": "Recommended when you want speed.",
    },
    {
        "id": "mistral:latest",
        "label": "Mistral Latest",
        "badge": "Balanced",
        "description": "Balanced choice for good quality with solid speed.",
        "strength": "Recommended when you want a middle ground between speed and depth.",
    },
    {
        "id": "deepseek-r1:7b",
        "label": "DeepSeek R1 7B",
        "badge": "High Accuracy",
        "description": "Best for deeper reasoning and more careful research analysis.",
        "strength": "Recommended when accuracy matters most.",
    },
]
DETAIL_LEVEL_OPTIONS = [
    {
        "id": "standard",
        "label": "Standard",
        "description": "Balanced detail for normal research work.",
    },
    {
        "id": "deep",
        "label": "Deep",
        "description": "More explanation, interpretation, and practical detail.",
    },
    {
        "id": "expert",
        "label": "Expert",
        "description": "Maximum depth, nuance, rigor, and long-form reasoning.",
    },
]
DEFAULT_DETAIL_LEVEL = os.environ.get("DETAIL_LEVEL", "deep").strip().lower()
AVAILABLE_MODEL_IDS = {option["id"] for option in MODEL_OPTIONS}
AVAILABLE_DETAIL_LEVELS = {option["id"] for option in DETAIL_LEVEL_OPTIONS}
MODEL_ALIASES = {
    "gemma3:1b": "gemma3:1b",
    "gemma3": "gemma3:1b",
    "gemma": "gemma3:1b",
    "mistral:latest": "mistral:latest",
    "mistral": "mistral:latest",
    "deepseek-r1:7b": "deepseek-r1:7b",
    "deepseek-r1": "deepseek-r1:7b",
    "deepseek": "deepseek-r1:7b",
}
APP_STARTED_AT = time.time()
OLLAMA_START_LOCK = threading.Lock()
OLLAMA_SERVER_PROCESS = None
OLLAMA_MODEL_PULLS = set()
METRICS_LOCK = threading.Lock()
METRICS_STATE = {
    "active_requests": 0,
    "latest_request": {},
    "active_request_details": {},
    "models": {
        option["id"]: {
            "id": option["id"],
            "label": option["label"],
            "badge": option["badge"],
            "calls": 0,
            "active_requests": 0,
            "prompt_tokens": 0,
            "generated_tokens": 0,
            "total_duration_seconds": 0.0,
            "last_tokens_per_second": None,
            "last_seen": None,
            "profile": None,
        }
        for option in MODEL_OPTIONS
    },
}
RESEARCH_CONTEXTS_LOCK = threading.Lock()
RESEARCH_CONTEXTS = {}
HISTORY_LOCK = threading.Lock()
MAX_HISTORY_ITEMS = 120
MAX_CHAT_TURNS = 6


def ensure_storage_dirs():
    os.makedirs(DATASET_DIR, exist_ok=True)


def load_history_store():
    ensure_storage_dirs()
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return payload
    except (OSError, json.JSONDecodeError):
        return []
    return []


def write_history_store(items):
    ensure_storage_dirs()
    temp_path = HISTORY_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(items, handle, ensure_ascii=True, indent=2)
    os.replace(temp_path, HISTORY_FILE)


def seed_research_contexts_from_history():
    for item in load_history_store():
        context = item.get("context") or {}
        context_id = item.get("context_id")
        if not context_id or not context:
            continue
        payload = dict(context)
        payload["context_id"] = context_id
        payload["created_at"] = item.get("created_at", time.time())
        RESEARCH_CONTEXTS[context_id] = payload


def iso_timestamp(unix_seconds):
    return datetime.fromtimestamp(float(unix_seconds or time.time())).strftime("%Y-%m-%d %H:%M")


def build_history_summary(item):
    result_payload = item.get("result_payload") or {}
    return {
        "context_id": item.get("context_id"),
        "kind": item.get("kind"),
        "title": item.get("title"),
        "subtitle": item.get("subtitle"),
        "overview": item.get("overview"),
        "model": item.get("model"),
        "detail_level": item.get("detail_level"),
        "result_type": item.get("result_type"),
        "chat_turn_count": len(item.get("chat_history") or []),
        "created_at": item.get("created_at"),
        "created_at_label": iso_timestamp(item.get("created_at")),
        "updated_at": item.get("updated_at"),
        "updated_at_label": iso_timestamp(item.get("updated_at")),
        "result_meta": {
            "paper_name": result_payload.get("paper_name"),
            "field": result_payload.get("field"),
            "theme": result_payload.get("theme"),
        },
    }


def save_history_item(item):
    with HISTORY_LOCK:
        items = load_history_store()
        item["updated_at"] = time.time()
        retained = [existing for existing in items if existing.get("context_id") != item.get("context_id")]
        retained.insert(0, item)
        retained = retained[:MAX_HISTORY_ITEMS]
        write_history_store(retained)


def append_chat_turn(context_id, role, content):
    if not context_id or not content:
        return
    with HISTORY_LOCK:
        items = load_history_store()
        changed = False
        for item in items:
            if item.get("context_id") != context_id:
                continue
            history = item.setdefault("chat_history", [])
            history.append({
                "role": role,
                "content": content,
                "created_at": time.time(),
            })
            item["updated_at"] = time.time()
            changed = True
            break
        if changed:
            write_history_store(items[:MAX_HISTORY_ITEMS])


def clear_chat_turns(context_id):
    if not context_id:
        return
    with HISTORY_LOCK:
        items = load_history_store()
        changed = False
        for item in items:
            if item.get("context_id") != context_id:
                continue
            item["chat_history"] = []
            item["updated_at"] = time.time()
            changed = True
            break
        if changed:
            write_history_store(items[:MAX_HISTORY_ITEMS])


def get_history_item(context_id):
    if not context_id:
        return None
    with HISTORY_LOCK:
        items = load_history_store()
    for item in items:
        if item.get("context_id") == context_id:
            return item
    return None


def list_history_items():
    with HISTORY_LOCK:
        items = load_history_store()
    return [build_history_summary(item) for item in items]


seed_research_contexts_from_history()


def build_ollama_options(model_name, profile="analysis"):
    """Build Ollama runtime options tuned to keep local models on GPU where possible."""
    normalized_model = normalize_model_identifier(model_name)
    model_limits = MODEL_RUNTIME_LIMITS.get(normalized_model, {})
    is_discovery = profile == "discovery"

    options = {
        "num_ctx": model_limits.get("discovery_num_ctx" if is_discovery else "analysis_num_ctx", DISCOVERY_NUM_CTX if is_discovery else OLLAMA_NUM_CTX),
        "num_predict": model_limits.get("discovery_num_predict" if is_discovery else "analysis_num_predict", DISCOVERY_NUM_PREDICT if is_discovery else OLLAMA_NUM_PREDICT),
        "num_gpu": OLLAMA_NUM_GPU,
        "num_thread": DISCOVERY_NUM_THREAD if is_discovery else OLLAMA_NUM_THREAD,
        "num_batch": DISCOVERY_NUM_BATCH if is_discovery else OLLAMA_NUM_BATCH,
        "temperature": 0.2 if is_discovery else 0.35,
    }

    if OLLAMA_FORCE_GPU:
        options.update({
            "main_gpu": OLLAMA_MAIN_GPU,
            "low_vram": OLLAMA_LOW_VRAM,
            "use_mmap": OLLAMA_USE_MMAP,
            "use_mlock": OLLAMA_USE_MLOCK,
            "num_thread": min(options["num_thread"], 2),
        })

    return options


def ollama_base_url():
    return OLLAMA_URL.rsplit('/api/', 1)[0]


def ensure_ollama_running():
    """Start Ollama on Windows/Linux when it is not already running."""
    global OLLAMA_SERVER_PROCESS
    try:
        requests.get(f"{ollama_base_url()}/api/tags", timeout=1.5)
        return True
    except requests.RequestException:
        pass

    with OLLAMA_START_LOCK:
        try:
            requests.get(f"{ollama_base_url()}/api/tags", timeout=1.5)
            return True
        except requests.RequestException:
            pass
        executable = shutil.which('ollama')
        if not executable:
            return False
        try:
            creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            OLLAMA_SERVER_PROCESS = subprocess.Popen(
                [executable, 'serve'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            return False

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            requests.get(f"{ollama_base_url()}/api/tags", timeout=1)
            return True
        except requests.RequestException:
            time.sleep(.25)
    return False


def ensure_ollama_model(model_name):
    """Pull a configured model once in the background when it is missing."""
    model_name = sanitize_model_name(model_name)
    if model_name in OLLAMA_MODEL_PULLS or not ensure_ollama_running():
        return
    try:
        tags = requests.get(f"{ollama_base_url()}/api/tags", timeout=3).json().get('models', [])
        installed = {normalize_model_identifier(item.get('name') or item.get('model') or '') for item in tags}
        if model_name in installed:
            return
    except (requests.RequestException, ValueError):
        return
    OLLAMA_MODEL_PULLS.add(model_name)
    subprocess.Popen([shutil.which('ollama') or 'ollama', 'pull', model_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sanitize_model_name(model_name):
    """Allow only configured local models from the interface."""
    normalized = normalize_model_identifier(model_name)
    if normalized in AVAILABLE_MODEL_IDS:
        return normalized
    return DEFAULT_OLLAMA_MODEL if DEFAULT_OLLAMA_MODEL in AVAILABLE_MODEL_IDS else MODEL_OPTIONS[0]["id"]


def sanitize_detail_level(detail_level):
    normalized = (detail_level or "").strip().lower()
    if normalized in AVAILABLE_DETAIL_LEVELS:
        return normalized
    return DEFAULT_DETAIL_LEVEL if DEFAULT_DETAIL_LEVEL in AVAILABLE_DETAIL_LEVELS else DETAIL_LEVEL_OPTIONS[1]["id"]


def normalize_model_identifier(model_name):
    raw = (model_name or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    for alias, canonical in MODEL_ALIASES.items():
        if lowered.startswith(alias):
            return canonical
    return raw


def ns_to_seconds(value):
    if value in (None, 0, ""):
        return 0.0
    return float(value) / 1_000_000_000


def begin_active_request(model_name, profile, prompt):
    model_name = normalize_model_identifier(model_name)
    with METRICS_LOCK:
        METRICS_STATE["active_requests"] += 1
        model_metrics = METRICS_STATE["models"].setdefault(model_name, {
            "id": model_name,
            "label": model_name,
            "badge": "Custom",
            "calls": 0,
            "active_requests": 0,
            "prompt_tokens": 0,
            "generated_tokens": 0,
            "total_duration_seconds": 0.0,
            "last_tokens_per_second": None,
            "last_seen": None,
            "profile": None,
        })
        model_metrics["active_requests"] += 1
        METRICS_STATE["active_request_details"][model_name] = {
            "model": model_name,
            "profile": profile,
            "started_at": time.time(),
            "estimated_prompt_tokens": estimate_token_count(prompt),
            "status": "running",
        }


def end_active_request(model_name):
    model_name = normalize_model_identifier(model_name)
    with METRICS_LOCK:
        METRICS_STATE["active_requests"] = max(0, METRICS_STATE["active_requests"] - 1)
        model_metrics = METRICS_STATE["models"].setdefault(model_name, {
            "id": model_name,
            "label": model_name,
            "badge": "Custom",
            "calls": 0,
            "active_requests": 0,
            "prompt_tokens": 0,
            "generated_tokens": 0,
            "total_duration_seconds": 0.0,
            "last_tokens_per_second": None,
            "last_seen": None,
            "profile": None,
        })
        model_metrics["active_requests"] = max(0, model_metrics["active_requests"] - 1)
        METRICS_STATE["active_request_details"].pop(model_name, None)


def record_ollama_metrics(model_name, payload, elapsed_seconds, profile):
    reported_model = normalize_model_identifier(payload.get("model") or payload.get("message", {}).get("model") or model_name)
    model_name = reported_model or normalize_model_identifier(model_name)
    message_text = (payload.get("message", {}).get("content") or "").strip()
    eval_count = payload.get("eval_count")
    prompt_eval_count = payload.get("prompt_eval_count")
    if eval_count in (None, 0):
        eval_count = estimate_token_count(message_text)
    if prompt_eval_count in (None, 0):
        prompt_eval_count = 0

    eval_duration_seconds = ns_to_seconds(payload.get("eval_duration"))
    total_duration_seconds = ns_to_seconds(payload.get("total_duration")) or float(elapsed_seconds)
    tokens_per_second = None
    if eval_duration_seconds > 0 and eval_count:
        tokens_per_second = round(float(eval_count) / eval_duration_seconds, 2)
    elif elapsed_seconds > 0 and eval_count:
        tokens_per_second = round(float(eval_count) / float(elapsed_seconds), 2)

    latest_request = {
        "model": model_name,
        "reported_model": payload.get("model") or model_name,
        "profile": profile,
        "prompt_tokens": int(prompt_eval_count or 0),
        "generated_tokens": int(eval_count or 0),
        "tokens_per_second": tokens_per_second,
        "eval_duration_seconds": round(eval_duration_seconds, 3),
        "total_duration_seconds": round(total_duration_seconds, 3),
        "load_duration_seconds": round(ns_to_seconds(payload.get("load_duration")), 3),
        "completed_at": round(time.time(), 3),
    }

    with METRICS_LOCK:
        model_metrics = METRICS_STATE["models"].setdefault(model_name, {
            "id": model_name,
            "label": model_name,
            "badge": "Custom",
            "calls": 0,
            "active_requests": 0,
            "prompt_tokens": 0,
            "generated_tokens": 0,
            "total_duration_seconds": 0.0,
            "last_tokens_per_second": None,
            "last_seen": None,
            "profile": None,
        })
        model_metrics["calls"] += 1
        model_metrics["prompt_tokens"] += int(prompt_eval_count or 0)
        model_metrics["generated_tokens"] += int(eval_count or 0)
        model_metrics["total_duration_seconds"] += float(total_duration_seconds)
        model_metrics["last_tokens_per_second"] = tokens_per_second
        model_metrics["last_seen"] = latest_request["completed_at"]
        model_metrics["profile"] = profile
        METRICS_STATE["latest_request"] = latest_request


def get_model_metrics_snapshot():
    with METRICS_LOCK:
        active_requests = METRICS_STATE["active_requests"]
        latest_request = dict(METRICS_STATE["latest_request"])
        active_request_details = {key: dict(value) for key, value in METRICS_STATE["active_request_details"].items()}
        models = [dict(metrics) for metrics in METRICS_STATE["models"].values()]

    for model in models:
        total_seconds = float(model.get("total_duration_seconds") or 0.0)
        generated_tokens = int(model.get("generated_tokens") or 0)
        model["average_tokens_per_second"] = round(generated_tokens / total_seconds, 2) if total_seconds > 0 else None
        model["utilization_hint_percent"] = min(100.0, round(active_requests * 25.0, 1)) if model.get("active_requests") else 0.0
        active_detail = active_request_details.get(model.get("id"))
        if active_detail:
            model["live_status"] = active_detail.get("status")
            model["live_prompt_tokens"] = active_detail.get("estimated_prompt_tokens")
            model["live_runtime_seconds"] = round(time.time() - float(active_detail.get("started_at") or time.time()), 1)
            model["is_live"] = True
        else:
            model["live_status"] = None
            model["live_prompt_tokens"] = None
            model["live_runtime_seconds"] = None
            model["is_live"] = False

    if latest_request.get("model") in active_request_details:
        active_detail = active_request_details[latest_request["model"]]
        latest_request["status"] = active_detail.get("status")
        latest_request["prompt_tokens"] = latest_request.get("prompt_tokens") or active_detail.get("estimated_prompt_tokens")
        latest_request["total_duration_seconds"] = round(time.time() - float(active_detail.get("started_at") or time.time()), 1)
    elif active_request_details and not latest_request:
        first_active = next(iter(active_request_details.values()))
        latest_request = {
            "model": first_active.get("model"),
            "profile": first_active.get("profile"),
            "prompt_tokens": first_active.get("estimated_prompt_tokens"),
            "generated_tokens": None,
            "tokens_per_second": None,
            "total_duration_seconds": round(time.time() - float(first_active.get("started_at") or time.time()), 1),
            "status": first_active.get("status"),
        }

    models.sort(key=lambda item: (item.get("active_requests", 0), item.get("last_seen") or 0), reverse=True)
    return {
        "active_requests": active_requests,
        "latest_request": latest_request,
        "models": models,
    }


def get_cpu_metrics():
    if not psutil:
        return {
            "available": False,
            "name": "CPU",
            "usage_percent": None,
            "physical_cores": CPU_COUNT,
            "logical_cores": CPU_COUNT,
            "note": "Install psutil to enable CPU metrics.",
        }

    return {
        "available": True,
        "name": "CPU",
        "usage_percent": psutil.cpu_percent(interval=0.15),
        "physical_cores": psutil.cpu_count(logical=False) or CPU_COUNT,
        "logical_cores": psutil.cpu_count(logical=True) or CPU_COUNT,
        "frequency_mhz": round((psutil.cpu_freq().current if psutil.cpu_freq() else 0.0), 1),
    }


def bytes_to_gb(value):
    return round(float(value) / (1024 ** 3), 2)


def get_memory_metrics():
    if not psutil:
        return {
            "available": False,
            "used_gb": None,
            "total_gb": None,
            "usage_percent": None,
            "note": "Install psutil to enable RAM metrics.",
        }
    vm = psutil.virtual_memory()
    return {
        "available": True,
        "used_gb": bytes_to_gb(vm.used),
        "total_gb": bytes_to_gb(vm.total),
        "usage_percent": vm.percent,
    }


def get_gpu_metrics():
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {
            "available": False,
            "name": "GPU",
            "usage_percent": None,
            "memory_usage_percent": None,
            "memory_used_gb": None,
            "memory_total_gb": None,
            "temperature_c": None,
            "note": "nvidia-smi not found. GPU metrics are unavailable on this device.",
        }

    try:
        command = [
            nvidia_smi,
            "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
        first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
        if not first_line:
            raise ValueError("No GPU rows returned.")
        parts = [part.strip() for part in first_line.split(",")]
        if len(parts) < 6:
            raise ValueError("Unexpected GPU metrics format.")
        memory_used_mb = float(parts[3])
        memory_total_mb = float(parts[4])
        memory_usage_percent = round((memory_used_mb / memory_total_mb) * 100, 1) if memory_total_mb else None
        return {
            "available": True,
            "name": parts[0],
            "usage_percent": float(parts[1]),
            "memory_controller_percent": float(parts[2]),
            "memory_used_gb": round(memory_used_mb / 1024, 2),
            "memory_total_gb": round(memory_total_mb / 1024, 2),
            "memory_usage_percent": memory_usage_percent,
            "temperature_c": float(parts[5]),
        }
    except Exception as exc:
        return {
            "available": False,
            "name": "GPU",
            "usage_percent": None,
            "memory_usage_percent": None,
            "memory_used_gb": None,
            "memory_total_gb": None,
            "temperature_c": None,
            "note": f"GPU metrics unavailable: {str(exc)}",
        }


def get_ollama_runtime_metrics():
    base_url = OLLAMA_URL.rsplit("/api/", 1)[0]
    try:
        response = requests.get(f"{base_url}/api/ps", timeout=2)
        response.raise_for_status()
        payload = response.json()
        active_models = []
        for model in payload.get("models", []):
            canonical_name = normalize_model_identifier(model.get("name") or model.get("model") or "")
            size_vram = model.get("size_vram")
            active_models.append({
                "name": model.get("name") or model.get("model") or "Unknown model",
                "canonical_name": canonical_name,
                "size_vram_gb": round(float(size_vram) / (1024 ** 3), 2) if isinstance(size_vram, (int, float)) else None,
                "expires_at": model.get("expires_at"),
            })
        return {
            "reachable": True,
            "url": base_url,
            "active_models": active_models,
        }
    except Exception as exc:
        return {
            "reachable": False,
            "url": base_url,
            "active_models": [],
            "note": f"Ollama status unavailable: {str(exc)}",
        }


def build_dashboard_payload():
    system_cpu = get_cpu_metrics()
    system_memory = get_memory_metrics()
    system_gpu = get_gpu_metrics()
    model_snapshot = get_model_metrics_snapshot()
    ollama_runtime = get_ollama_runtime_metrics()
    active_model_lookup = {
        item.get("canonical_name"): item
        for item in ollama_runtime.get("active_models", [])
        if item.get("canonical_name")
    }
    for model in model_snapshot["models"]:
        runtime_item = active_model_lookup.get(model.get("id"))
        model["loaded_in_ollama"] = bool(runtime_item)
        model["ollama_vram_gb"] = runtime_item.get("size_vram_gb") if runtime_item else None
    return {
        "timestamp": round(time.time(), 3),
        "uptime_seconds": round(time.time() - APP_STARTED_AT, 1),
        "runtime": {
            "force_gpu": OLLAMA_FORCE_GPU,
            "main_gpu": OLLAMA_MAIN_GPU,
            "low_vram": OLLAMA_LOW_VRAM,
        },
        "system": {
            "cpu": system_cpu,
            "memory": system_memory,
            "gpu": system_gpu,
        },
        "ollama": ollama_runtime,
        "models": model_snapshot["models"],
        "active_requests": model_snapshot["active_requests"],
        "latest_request": model_snapshot["latest_request"],
    }


def call_ollama(prompt, model_name, timeout_seconds=None, profile="analysis"):
    """Send a chat request to a local Ollama instance."""
    started_at = time.perf_counter()
    begin_active_request(model_name, profile, prompt)
    request_kwargs = {
        "json": {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": build_ollama_options(model_name, profile=profile),
        },
    }
    if timeout_seconds is not None:
        request_kwargs["timeout"] = max(1, timeout_seconds)
    try:
        response = requests.post(
            OLLAMA_URL,
            **request_kwargs,
        )
        if response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                record_ollama_metrics(
                    model_name,
                    payload,
                    elapsed_seconds=time.perf_counter() - started_at,
                    profile=profile,
                )
        return response
    finally:
        end_active_request(model_name)


def call_ollama_text(prompt, model_name, timeout_seconds=None, profile="analysis"):
    """Return plain text content from Ollama."""
    response = call_ollama(
        prompt,
        model_name,
        timeout_seconds=timeout_seconds,
        profile=profile,
    )
    response.raise_for_status()
    payload = response.json()
    return (payload.get("message", {}).get("content") or "").strip()


def extract_json_object(text):
    """Extract the first JSON object from model output."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model output.")
    return json.loads(match.group(0))


def generate_search_queries(field_name, theme_name, model_name, timeout_seconds=30):
    """Use the local model to expand a field/theme into crawlable search queries."""
    prompt = f"""
You are helping discover research papers.
Return valid JSON only with this schema:
{{
  "search_queries": ["query 1", "query 2", "query 3"],
  "focus": "one-sentence description"
}}

Field: {field_name}
Theme: {theme_name}

Rules:
- Generate exactly 3 short academic search queries.
- Keep them suitable for Google Scholar, Crossref, and arXiv.
- No markdown, no commentary, JSON only.
""".strip()
    try:
        model_output = call_ollama_text(
            prompt,
            model_name,
            timeout_seconds=timeout_seconds,
            profile="discovery",
        )
        parsed = extract_json_object(model_output)
        queries = [q.strip() for q in parsed.get("search_queries", []) if q and q.strip()]
        focus = parsed.get("focus", "").strip()
        if queries:
            return queries[:3], focus
    except Exception:
        pass

    fallback = [f"{field_name} {theme_name}".strip(), theme_name.strip(), field_name.strip()]
    queries = []
    for item in fallback:
        if item and item not in queries:
            queries.append(item)
    return queries[:3], f"Research discovery for {field_name} and {theme_name}".strip()


def normalize_whitespace(value):
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def normalize_title(value):
    return re.sub(r"[^a-z0-9]+", "", normalize_whitespace(value).lower())


def strip_html_tags(value):
    return normalize_whitespace(re.sub(r"<[^>]+>", " ", value or ""))


def google_scholar_search(query, limit=5, timeout_seconds=30):
    """Fetch paper-like results from Google Scholar HTML search."""
    response = requests.get(
        "https://scholar.google.com/scholar",
        params={"q": query, "hl": "en"},
        headers=REQUEST_HEADERS,
        timeout=max(1, timeout_seconds),
    )
    response.raise_for_status()
    html = response.text
    if re.search(r"unusual traffic|not a robot|captcha", html, re.I):
        return []

    papers = []
    blocks = re.findall(r'(<div class="gs_r gs_or gs_scl".*?</div>\s*</div>)', html, re.DOTALL)
    for result in blocks[:limit]:
        title_match = re.search(r'<h3 class="gs_rt".*?(?:<a[^>]*href="([^"]+)"[^>]*>)?(.*?)(?:</a>)?</h3>', result, re.DOTALL)
        meta_match = re.search(r'<div class="gs_a">(.*?)</div>', result, re.DOTALL)
        snippet_match = re.search(r'<div class="(?:gs_rs|gs_snippet)">(.*?)</div>', result, re.DOTALL)
        title = strip_html_tags(title_match.group(2) if title_match else "")
        if not title:
            continue
        papers.append({
            "title": title,
            "authors": strip_html_tags(meta_match.group(1) if meta_match else ""),
            "summary": strip_html_tags(snippet_match.group(1) if snippet_match else ""),
            "url": title_match.group(1) if title_match and title_match.group(1) else f"https://scholar.google.com/scholar?q={quote_plus(title)}",
            "source": "Google Scholar",
            "year": "",
            "query": query,
        })
    return papers


def crossref_search(query, limit=5, timeout_seconds=30):
    """Fetch paper metadata from Crossref."""
    response = requests.get(
        "https://api.crossref.org/works",
        params={"query": query, "rows": limit, "sort": "relevance"},
        headers=REQUEST_HEADERS,
        timeout=max(1, timeout_seconds),
    )
    response.raise_for_status()
    data = response.json()
    papers = []
    for item in data.get("message", {}).get("items", [])[:limit]:
        title_list = item.get("title", [])
        title = normalize_whitespace(title_list[0] if title_list else "")
        if not title:
            continue
        authors = []
        for author in item.get("author", [])[:4]:
            given = author.get("given", "")
            family = author.get("family", "")
            authors.append(normalize_whitespace(f"{given} {family}"))
        year_parts = item.get("published-print", {}).get("date-parts") or item.get("published-online", {}).get("date-parts") or [[]]
        year = str(year_parts[0][0]) if year_parts and year_parts[0] else ""
        papers.append({
            "title": title,
            "authors": ", ".join(a for a in authors if a),
            "summary": normalize_whitespace(item.get("abstract", "")),
            "url": item.get("URL", ""),
            "source": "Crossref",
            "year": year,
            "query": query,
        })
    return papers


def arxiv_search(query, limit=5, timeout_seconds=30):
    """Fetch paper metadata from arXiv Atom API."""
    response = requests.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
        headers=REQUEST_HEADERS,
        timeout=max(1, timeout_seconds),
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("atom:entry", ns)[:limit]:
        title = normalize_whitespace(entry.findtext("atom:title", default="", namespaces=ns))
        if not title:
            continue
        authors = [normalize_whitespace(node.text or "") for node in entry.findall("atom:author/atom:name", ns)]
        summary = normalize_whitespace(entry.findtext("atom:summary", default="", namespaces=ns))
        published = entry.findtext("atom:published", default="", namespaces=ns)
        year = published[:4] if published else ""
        link = ""
        for candidate in entry.findall("atom:link", ns):
            if candidate.attrib.get("rel") == "alternate":
                link = candidate.attrib.get("href", "")
                break
        papers.append({
            "title": title,
            "authors": ", ".join(authors[:4]),
            "summary": summary,
            "url": link,
            "source": "arXiv",
            "year": year,
            "query": query,
        })
    return papers


def deduplicate_papers(papers):
    """Merge same-title results from multiple sources."""
    merged = {}
    for paper in papers:
        key = normalize_title(paper.get("title", ""))
        if not key:
            continue
        if key not in merged:
            merged[key] = paper
            continue
        current = merged[key]
        current["source"] = " / ".join(dict.fromkeys((current.get("source", "") + " / " + paper.get("source", "")).split(" / "))).strip(" /")
        if not current.get("summary") and paper.get("summary"):
            current["summary"] = paper["summary"]
        if not current.get("authors") and paper.get("authors"):
            current["authors"] = paper["authors"]
        if not current.get("url") and paper.get("url"):
            current["url"] = paper["url"]
        if not current.get("year") and paper.get("year"):
            current["year"] = paper["year"]
    return list(merged.values())


def enrich_discovered_papers(papers, field_name, theme_name, model_name, detail_level, timeout_seconds=45):
    """Use local Ollama to rank and summarize crawled papers."""
    papers_text = "\n".join(
        f"{index + 1}. Title: {paper['title']}\n"
        f"   Source: {paper['source']}\n"
        f"   Authors: {paper['authors'] or 'Unknown'}\n"
        f"   Year: {paper['year'] or 'Unknown'}\n"
        f"   Summary: {paper['summary'][:500] or 'No summary available.'}"
        for index, paper in enumerate(papers[:15])
    )
    prompt = f"""
You are a research scout.
Based only on the papers below, produce markdown with these sections:

## Research Focus
## Why These Papers Matter
## Recommended Reading Order
- Mention paper titles exactly as given
## Observed Gaps
## Next Search Directions
## Deep Interpretation
## What This Means For A Researcher
## Practical Next Actions

{RESPONSE_STYLE_GUIDE}
{DEEP_ANALYSIS_EXTENSION}
{DETAIL_LEVEL_GUIDES.get(detail_level, DETAIL_LEVEL_GUIDES['deep'])}

Field: {field_name}
Theme: {theme_name}

Papers:
{papers_text}
""".strip()
    return call_ollama_text(
        prompt,
        model_name,
        timeout_seconds=timeout_seconds,
        profile="discovery",
    )


def build_discovery_fallback_overview(field_name, theme_name, queries, source_errors, model_name, detail_level, timeout_seconds=20):
    """Return a graceful fallback when external paper sources are unavailable."""
    error_notes = "\n".join(f"- {item}" for item in (source_errors or [])[:6]) or "- No external source details were captured."
    query_notes = "\n".join(f"- {item}" for item in (queries or [])[:6]) or "- No generated queries were available."
    prompt = f"""
You are a research assistant operating without live paper search results.
The external paper sources are currently unavailable, but the user still needs useful next-step guidance.

Produce markdown with these sections:
## Research Focus
## Why Live Results May Be Missing
## Suggested Search Queries
## Manual Search Plan
## Practical Next Actions
## What To Try In This App Next

Keep the explanation practical and direct.
Do not claim that papers were found.

{RESPONSE_STYLE_GUIDE}
{DETAIL_LEVEL_GUIDES.get(detail_level, DETAIL_LEVEL_GUIDES['deep'])}

Field: {field_name}
Theme: {theme_name}

Generated queries:
{query_notes}

Source issues:
{error_notes}
""".strip()
    try:
        return call_ollama_text(
            prompt,
            model_name,
            timeout_seconds=timeout_seconds,
            profile="discovery",
        )
    except Exception:
        return (
            "## Research Focus\n"
            f"Discovery guidance for {field_name} and {theme_name}.\n\n"
            "## Why Live Results May Be Missing\n"
            "The external paper sources were unavailable during this request, so no paper list could be collected.\n\n"
            "## Suggested Search Queries\n"
            + ("\n".join(f"- {item}" for item in (queries or [])[:6]) or "- No generated queries were available.")
            + "\n\n## Manual Search Plan\n"
            "- Retry discovery when network access to paper sources is available.\n"
            "- Search the suggested queries manually in Google Scholar, Crossref, or arXiv.\n"
            "- Narrow the theme if the topic is broad.\n\n"
            "## Practical Next Actions\n"
            "- Use Analyze Research Input if you already have a paper, abstract, or notes.\n"
            "- Retry discovery after checking network access.\n"
            "- Switch to a faster local model if the machine is under load.\n"
        )


def remaining_seconds(deadline):
    if deadline is None:
        return float("inf")
    return max(0.0, deadline - time.monotonic())


def estimate_token_count(text):
    """Rough token estimate for status messages and metadata."""
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def make_preview(text, limit=320):
    text = normalize_whitespace(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def clip_text(text, limit):
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n[truncated]"


def store_research_context(context):
    context_id = str(uuid.uuid4())
    payload = dict(context)
    payload["context_id"] = context_id
    payload["created_at"] = time.time()
    with RESEARCH_CONTEXTS_LOCK:
        RESEARCH_CONTEXTS[context_id] = payload
        stale_items = sorted(
            RESEARCH_CONTEXTS.items(),
            key=lambda item: item[1].get("created_at", 0),
            reverse=True,
        )[40:]
        for stale_id, _ in stale_items:
            RESEARCH_CONTEXTS.pop(stale_id, None)
    return context_id, payload


def load_research_context(context_id):
    if not context_id:
        return None
    with RESEARCH_CONTEXTS_LOCK:
        return RESEARCH_CONTEXTS.get(context_id)


def extract_paper_parameters(paper_text, filename):
    lines = [normalize_whitespace(line) for line in (paper_text or "").splitlines()]
    lines = [line for line in lines if line]
    title = lines[0] if lines else filename
    candidate_keywords = []
    for line in lines[:25]:
        if len(line) > 90 or len(line.split()) < 3:
            continue
        lowered = line.lower()
        if any(token in lowered for token in ("abstract", "introduction", "keywords", "index terms")):
            continue
        candidate_keywords.append(line)
    return {
        "title_guess": title,
        "document_type": "uploaded paper",
        "keyword_clues": candidate_keywords[:5],
    }


def build_analysis_chat_context(filename, paper_text, model_name, results, word_count, estimated_tokens, detail_level):
    overview_source = results.get("summarize") or next(iter(results.values()), "")
    parameters = extract_paper_parameters(paper_text, filename)
    parameters.update({
        "file_name": filename,
        "word_count": f"{word_count:,}",
        "estimated_tokens": f"{estimated_tokens:,}",
        "analysis_modes": list(results.keys()),
        "model": model_name,
        "detail_level": detail_level,
    })
    context_id, stored = store_research_context({
        "kind": "analysis",
        "model": model_name,
        "detail_level": detail_level,
        "title": filename,
        "overview": make_preview(overview_source, 360),
        "parameters": parameters,
        "paper_excerpt": clip_text(paper_text, 16000),
        "result_markdown": "\n\n".join(
            f"## {key.replace('_', ' ').title()}\n{value}"
            for key, value in results.items()
            if value
        ),
    })
    return {
        "context_id": context_id,
        "overview": stored["overview"],
        "parameters": stored["parameters"],
        "kind": stored["kind"],
        "title": stored["title"],
    }


def save_analysis_history_entry(chat_context, source_name, source_type, model_name, detail_level, word_count, estimated_tokens, results, reference_errors, references):
    context = load_research_context(chat_context["context_id"]) or {}
    result_type = "analysis_full" if len(results) > 1 else "analysis_single"
    subtitle = f"{source_type.title()} analysis"
    if len(results) == 1:
        only_mode = next(iter(results.keys()))
        subtitle = f"{only_mode.replace('_', ' ').title()} / {source_type.title()}"
    save_history_item({
        "context_id": chat_context["context_id"],
        "kind": "analysis",
        "title": source_name,
        "subtitle": subtitle,
        "overview": chat_context.get("overview"),
        "model": model_name,
        "detail_level": detail_level,
        "created_at": context.get("created_at", time.time()),
        "chat_history": [],
        "context": context,
        "result_type": result_type,
        "result_payload": {
            "paper_name": source_name,
            "source_type": source_type,
            "word_count": word_count,
            "estimated_tokens": estimated_tokens,
            "model": model_name,
            "detail_level": detail_level,
            "results": results,
            "reference_dataset_files": [item.get("filename") for item in references],
            "reference_dataset_errors": reference_errors,
            "used_reference_dataset": bool(references),
        },
    })


def build_discovery_chat_context(field_name, theme_name, model_name, queries, papers, ai_overview, source_errors, crawl_minutes, elapsed_seconds, detail_level):
    parameters = {
        "research_field": field_name,
        "research_theme": theme_name,
        "model": model_name,
        "detail_level": detail_level,
        "search_queries": queries,
        "paper_count": len(papers),
        "crawl_budget": f"{crawl_minutes} minute(s)" if crawl_minutes else "manual stop",
        "elapsed_seconds": elapsed_seconds,
    }
    if source_errors:
        parameters["source_notes"] = source_errors[:4]

    paper_lines = []
    for index, paper in enumerate(papers[:15], start=1):
        paper_lines.append(
            f"{index}. Title: {paper.get('title', 'Untitled')}\n"
            f"   Source: {paper.get('source', 'Unknown')}\n"
            f"   Year: {paper.get('year', 'Unknown')}\n"
            f"   Authors: {paper.get('authors', 'Unknown')}\n"
            f"   Query: {paper.get('query', '')}\n"
            f"   Summary: {paper.get('summary', '')}\n"
            f"   URL: {paper.get('url', '')}"
        )

    context_id, stored = store_research_context({
        "kind": "discovery",
        "model": model_name,
        "detail_level": detail_level,
        "title": f"{field_name} - {theme_name}",
        "overview": make_preview(ai_overview or f"Discovery results for {field_name} / {theme_name}", 360),
        "parameters": parameters,
        "discovery_overview": clip_text(ai_overview, 12000),
        "paper_catalog": "\n\n".join(paper_lines),
        "source_errors": source_errors[:8],
    })
    return {
        "context_id": context_id,
        "overview": stored["overview"],
        "parameters": stored["parameters"],
        "kind": stored["kind"],
        "title": stored["title"],
    }


def save_discovery_history_entry(chat_context, field_name, theme_name, model_name, detail_level, queries, papers, ai_overview, source_errors, crawl_minutes, elapsed_seconds, focus, stopped_due_to_time):
    context = load_research_context(chat_context["context_id"]) or {}
    save_history_item({
        "context_id": chat_context["context_id"],
        "kind": "discovery",
        "title": f"{field_name} - {theme_name}",
        "subtitle": "Research discovery / scraping",
        "overview": chat_context.get("overview"),
        "model": model_name,
        "detail_level": detail_level,
        "created_at": context.get("created_at", time.time()),
        "chat_history": [],
        "context": context,
        "result_type": "discovery",
        "result_payload": {
            "field": field_name,
            "theme": theme_name,
            "focus": focus,
            "queries": queries,
            "papers": papers,
            "ai_overview": ai_overview,
            "source_errors": source_errors,
            "crawl_minutes": crawl_minutes,
            "elapsed_seconds": elapsed_seconds,
            "stopped_due_to_time": stopped_due_to_time,
            "model": model_name,
            "detail_level": detail_level,
        },
    })


def build_chat_prompt(context, message, history):
    history_lines = []
    for turn in history[-MAX_CHAT_TURNS:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        content = normalize_whitespace(turn.get("content", ""))
        if content:
            history_lines.append(f"{role}: {content}")

    history_text = "\n".join(history_lines) if history_lines else "No earlier turns."
    context_parts = [
        f"Context type: {context.get('kind', 'research')}",
        f"Title: {context.get('title', '')}",
        f"Detail level: {context.get('detail_level', 'deep')}",
        f"Overview: {context.get('overview', '')}",
        f"Parameters JSON:\n{json.dumps(context.get('parameters', {}), ensure_ascii=True, indent=2)}",
    ]
    if context.get("paper_excerpt"):
        context_parts.append(f"Paper excerpt:\n{context['paper_excerpt']}")
    if context.get("result_markdown"):
        context_parts.append(f"Current analysis results:\n{context['result_markdown']}")
    if context.get("discovery_overview"):
        context_parts.append(f"Discovery overview:\n{context['discovery_overview']}")
    if context.get("paper_catalog"):
        context_parts.append(f"Discovered papers:\n{context['paper_catalog']}")
    if context.get("source_errors"):
        context_parts.append("Source issues:\n" + "\n".join(f"- {item}" for item in context["source_errors"]))

    return f"""
You are ResearchLens Copilot, a research assistant embedded in a local research workflow.
Answer only from the current context below. If the user asks for something not grounded in the available context, say what is missing and suggest a better next prompt or search refinement.

Goals:
- Help the user understand the current research results.
- Answer the user's question directly from the current research output.
- Suggest a stronger prompt, narrower theme, or better search phrasing only if the user explicitly asks for prompt help or if the current context is not enough to answer.
- Write in clean plain text, not markdown.
- Do not use markdown symbols such as #, ##, *, **, -, or bullet characters.
- Prefer a well-structured point-wise answer using numbered items like 1. 2. 3.
- When useful, start with a short one-line conclusion, then explain the answer in clear numbered points.
- Keep each point readable and explanatory instead of terse fragments.
- Go deeper than a short assistant reply. Explain the meaning, reasoning, implications, and practical interpretation.
- If the user says things like "explain that", "go deeper", or "simplify this", answer using the full current research output as the reference context.
- Prefer teaching-style explanations that are detailed but easy to follow.
- Mention uncertainty clearly, especially when source errors or rate limits affected the results.
- For discovery contexts, prioritize the scraped papers and overview as the current data source.
- For analysis contexts, prioritize the uploaded paper excerpt and generated analysis results.
- Do not invent papers, citations, experiments, or facts not present in the context.
- Do not output a rewritten meta-prompt unless the user clearly asks you to write or improve a prompt.

Requested detail style:
{DETAIL_LEVEL_GUIDES.get(context.get('detail_level', 'deep'), DETAIL_LEVEL_GUIDES['deep'])}

Current context:
{chr(10).join(context_parts)}

Recent conversation:
{history_text}

User message:
{message.strip()}
""".strip()


def collect_discovery_results(queries, deadline):
    """Fetch discovery results in parallel while respecting the time budget."""
    all_papers = []
    source_errors = []
    stopped_due_to_time = False
    tasks = []

    with ThreadPoolExecutor(max_workers=max(1, DISCOVERY_PARALLELISM)) as executor:
        for query in queries:
            for source_name, search_fn in (
                ("Google Scholar", google_scholar_search),
                ("Crossref", crossref_search),
                ("arXiv", arxiv_search),
            ):
                remaining = remaining_seconds(deadline)
                if remaining <= 0:
                    stopped_due_to_time = True
                    break
                request_timeout = 20 if deadline is None else min(12, max(2, int(remaining)))
                future = executor.submit(search_fn, query, 4, request_timeout)
                tasks.append((future, source_name))
            if stopped_due_to_time:
                break

        for future, source_name in tasks:
            if remaining_seconds(deadline) <= 0:
                stopped_due_to_time = True
                future.cancel()
                continue
            try:
                future_timeout = None if deadline is None else max(1, remaining_seconds(deadline))
                all_papers.extend(future.result(timeout=future_timeout))
            except Exception as exc:
                source_errors.append(f"{source_name}: {str(exc)}")

    return all_papers, source_errors, stopped_due_to_time

def extract_text_from_pdf(file_bytes):
    """Extract text from PDF bytes."""
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        return f"Error extracting PDF text: {str(e)}"

def extract_text_from_txt(file_bytes):
    """Extract text from plain text file."""
    try:
        return file_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        return f"Error reading text: {str(e)}"


def extract_text_from_docx(file_bytes):
    """Extract text from DOCX bytes."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            xml_bytes = archive.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        texts = [
            normalize_whitespace(node.text or "")
            for node in root.iter()
            if node.tag.endswith("}t") and normalize_whitespace(node.text or "")
        ]
        return "\n".join(texts).strip()
    except Exception as e:
        return f"Error reading DOCX: {str(e)}"


def extract_text_from_pptx(file_bytes):
    """Extract text from PPTX bytes."""
    try:
        texts = []
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            slide_names = sorted(
                name for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            for slide_name in slide_names:
                root = ET.fromstring(archive.read(slide_name))
                for node in root.iter():
                    if node.tag.endswith("}t"):
                        text = normalize_whitespace(node.text or "")
                        if text:
                            texts.append(text)
        return "\n".join(texts).strip()
    except Exception as e:
        return f"Error reading PPTX: {str(e)}"


def extract_text_from_legacy_binary(file_bytes, label):
    """Best-effort extraction for legacy binary Office files."""
    try:
        decoded = file_bytes.decode('latin-1', errors='ignore')
        chunks = re.findall(r"[A-Za-z0-9][A-Za-z0-9 ,.;:()/_\-]{5,}", decoded)
        cleaned = []
        seen = set()
        for chunk in chunks:
            text = normalize_whitespace(chunk)
            if len(text) < 6:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(text)
        return "\n".join(cleaned[:800]).strip()
    except Exception as e:
        return f"Error reading {label}: {str(e)}"


def extract_text_from_uploaded_file(uploaded_file):
    """Validate an uploaded file and return extracted text plus filename."""
    if uploaded_file is None:
        return None, None, 'Please provide research text or a paper file to continue.'

    if not uploaded_file.filename:
        return None, None, 'Please provide research text or a paper file to continue.'

    original_filename = uploaded_file.filename
    filename = original_filename.lower()
    file_bytes = uploaded_file.read()

    if not file_bytes:
        return None, None, 'Uploaded file is empty.'

    if filename.endswith('.pdf'):
        paper_text = extract_text_from_pdf(file_bytes)
    elif filename.endswith('.txt'):
        paper_text = extract_text_from_txt(file_bytes)
    elif filename.endswith('.doc'):
        paper_text = extract_text_from_legacy_binary(file_bytes, 'DOC')
    elif filename.endswith('.docx'):
        paper_text = extract_text_from_docx(file_bytes)
    elif filename.endswith('.ppt'):
        paper_text = extract_text_from_legacy_binary(file_bytes, 'PPT')
    elif filename.endswith('.pptx'):
        paper_text = extract_text_from_pptx(file_bytes)
    else:
        return None, None, 'Please provide a supported paper file or paste research text to continue.'

    if not paper_text:
        return None, None, 'Could not extract text from the file.'

    if (
        paper_text.startswith('Error extracting PDF text:')
        or paper_text.startswith('Error reading text:')
        or paper_text.startswith('Error reading DOC:')
        or paper_text.startswith('Error reading DOCX:')
        or paper_text.startswith('Error reading PPT:')
        or paper_text.startswith('Error reading PPTX:')
    ):
        return None, None, paper_text

    if len(paper_text.strip()) < 100:
        return None, None, 'Could not extract sufficient text from the file. Please check the file.'

    return paper_text, original_filename, None


def get_uploaded_paper_text(uploaded_file):
    paper_text, _, error = extract_text_from_uploaded_file(uploaded_file)
    return paper_text, error


def get_pasted_paper_text(raw_text):
    """Validate pasted research text."""
    paper_text = (raw_text or "").strip()
    if not paper_text:
        return None, "Please provide research text or a paper file to continue."
    if len(normalize_whitespace(paper_text)) < 100:
        return None, "Pasted text is too short. Please paste a fuller paper, abstract, or research content."
    return paper_text, None


def get_analysis_source(request_obj):
    """Return analysis text from either an uploaded file or pasted text."""
    uploaded_file = request_obj.files.get('paper')
    pasted_text = request_obj.form.get('paper_text', '')
    input_mode = (request_obj.form.get('analysis_input') or '').strip().lower()
    has_pasted_text = bool((pasted_text or "").strip())
    has_uploaded_file = bool(uploaded_file and uploaded_file.filename)

    # Prefer pasted text whenever it exists so report generation continues
    # even if the UI toggle or cached frontend state says "upload".
    if has_pasted_text:
        paper_text, error = get_pasted_paper_text(pasted_text)
        source_name = "Pasted Research Text"
        source_type = "pasted_text"
    elif input_mode == 'upload' or has_uploaded_file:
        paper_text, error = get_uploaded_paper_text(uploaded_file)
        source_name = uploaded_file.filename if uploaded_file and uploaded_file.filename else "Uploaded Research File"
        source_type = "file"
    else:
        paper_text, error = get_pasted_paper_text(pasted_text)
        source_name = "Pasted Research Text"
        source_type = "pasted_text"

    if error:
        return None, None, None, error
    return paper_text, source_name, source_type, None


def save_dataset_file(uploaded_file, filename):
    os.makedirs(DATASET_DIR, exist_ok=True)
    safe_name = secure_filename(filename or "dataset-file")
    timestamp = int(time.time() * 1000)
    stored_name = f"{timestamp}-{safe_name}" if safe_name else f"{timestamp}-dataset-file"
    stored_path = os.path.join(DATASET_DIR, stored_name)
    uploaded_file.stream.seek(0)
    uploaded_file.save(stored_path)
    uploaded_file.stream.seek(0)
    return stored_path


def collect_reference_dataset_texts(request_obj):
    dataset_files = [item for item in request_obj.files.getlist('dataset_files') if item and item.filename]
    references = []
    errors = []

    for uploaded_file in dataset_files[:8]:
        try:
            extracted_text, filename, error = extract_text_from_uploaded_file(uploaded_file)
            if error:
                errors.append(f"{uploaded_file.filename}: {error}")
                continue
            stored_path = save_dataset_file(uploaded_file, filename)
            references.append({
                "filename": filename,
                "stored_path": stored_path,
                "text": extracted_text,
            })
        except Exception as exc:
            errors.append(f"{uploaded_file.filename}: {str(exc)}")

    return references, errors


def compress_text_window(text, max_chars):
    """Keep head, middle, and tail for very large research inputs."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    head_size = max_chars // 2
    middle_size = max_chars // 5
    tail_size = max_chars - head_size - middle_size
    midpoint = len(text) // 2
    middle_start = max(0, midpoint - (middle_size // 2))
    middle_end = middle_start + middle_size
    parts = [
        text[:head_size].rstrip(),
        "[... middle sections omitted for context compression ...]",
        text[middle_start:middle_end].strip(),
        "[... later sections omitted for context compression ...]",
        text[-tail_size:].lstrip(),
    ]
    return "\n\n".join(part for part in parts if part)


def build_reference_dataset_context(references, max_chars=REFERENCE_ANALYSIS_MAX_CHARS):
    if not references:
        return ""
    chunks = []
    for index, reference in enumerate(references, start=1):
        reference_text = compress_text_window(reference.get("text", ""), max(8000, max_chars // max(1, len(references))))
        chunks.append(
            f"Reference {index}: {reference.get('filename', 'Dataset file')}\n"
            f"{reference_text}"
        )
    return compress_text_window("\n\n".join(chunks), max_chars)


def prepare_analysis_corpus(paper_text, references):
    primary_text = compress_text_window(paper_text, PRIMARY_ANALYSIS_MAX_CHARS)
    reference_text = build_reference_dataset_context(references, REFERENCE_ANALYSIS_MAX_CHARS)
    combined = primary_text
    if reference_text:
        combined += (
            "\n\nReference Dataset Material:\n"
            "Use these files as supporting evidence and context for a more grounded answer.\n\n"
            f"{reference_text}"
        )
    was_truncated = len(primary_text) != len((paper_text or "").strip()) or bool(reference_text)
    return combined, was_truncated


def build_analysis_prompt(prompt_template, paper_text, references, detail_level):
    prepared_text, used_reference_context = prepare_analysis_corpus(paper_text, references)
    prompt = (
        prompt_template
        .replace('{paper_text}', prepared_text)
        .replace('{style_guide}', RESPONSE_STYLE_GUIDE)
        .replace('{depth_guide}', DEEP_ANALYSIS_EXTENSION)
        .replace('{detail_level_guide}', DETAIL_LEVEL_GUIDES.get(detail_level, DETAIL_LEVEL_GUIDES['deep']))
    )
    if references:
        prompt += (
            "\n\nImportant instructions:\n"
            "- Use the uploaded research input as the main source.\n"
            "- Use the reference dataset material only to strengthen, validate, compare, or contextualize the answer.\n"
            "- If reference files and the main input conflict, mention the conflict clearly."
        )
    return prompt, prepared_text, used_reference_context


RESPONSE_STYLE_GUIDE = """
Writing rules:
- Use plain, professional academic English.
- Do not use emojis, icons, pictograms, or decorative symbols.
- Use clean markdown headings, short paragraphs, bullet lists, numbered lists, and tables where helpful.
- Keep the structure easy to scan and suitable for deep research work.
- Prefer precise, evidence-based reasoning over generic claims.
- Explain why each point matters instead of listing unsupported statements.
- Go deeper than a standard summary. Surface assumptions, implications, tradeoffs, and consequences.
- Keep the answer understandable to a non-specialist by defining technical language in simple words.
- When useful, explain in layers: simple explanation first, deeper explanation next, practical meaning last.
- Prefer concrete detail, examples, mechanisms, and interpretation over vague statements.
- Write with a natural, varied rhythm: mix short and long sentences, avoid repeating the same opening, and use transitions only when they improve clarity.
- Avoid stock filler such as “in conclusion,” “it is important to note,” and “delve into” unless the wording is genuinely useful.
- Preserve the writer's specific terminology and intent instead of replacing everything with generic academic language.
- Distinguish clearly between evidence from the paper, evidence from live sources, and your own interpretation.
- When live sources are provided, mention the source title or URL near important claims and never invent citations.
- If the input is large, preserve the most important themes and cross-connections instead of giving a shallow overview.
- End major sections with what the reader should take away or do next.
""".strip()

DEEP_ANALYSIS_EXTENSION = """
Depth requirements:
- Be highly detailed, but keep the writing easy to understand.
- Do not stop at naming points; explain each point fully.
- When making a claim, explain the reason, mechanism, and consequence.
- Use multiple levels of explanation where appropriate:
  1. Simple explanation
  2. Deeper technical explanation
  3. Practical implication
- If the paper or text has limitations, explain exactly how those limitations affect interpretation.
- If the input contains supporting dataset material, integrate it thoughtfully to make the answer more grounded and realistic.
- Aim for depth similar to a careful research mentor, not a short assistant reply.
""".strip()

DETAIL_LEVEL_GUIDES = {
    "standard": """
Detail level:
- Give a solid, useful answer with enough explanation to be clear.
- Focus on the most important ideas first.
- Keep the response practical and readable.
""".strip(),
    "deep": """
Detail level:
- Go deep into the reasoning, interpretation, tradeoffs, and implications.
- Explain important mechanisms and why they matter.
- Add practical meaning and next-step interpretation throughout the answer.
""".strip(),
    "expert": """
Detail level:
- Go very deep and be highly rigorous.
- Surface assumptions, edge cases, limitations, implications, and competing interpretations.
- Connect ideas across sections and explain the broader research significance.
- Make the answer substantially more detailed than a standard assistant response.
""".strip(),
}

ANALYSIS_PROMPTS = {
    "summarize": """You are an expert academic communicator. Read this research paper and provide a comprehensive, deep, and easy-to-understand summary.

Your summary should include:
1. Executive Overview
2. What problem does this research solve?
3. How did the researchers approach it?
4. Key findings
5. Why it matters
6. Key terms explained in simple language
7. What is genuinely novel or important here?
8. What a reader should remember after reading this paper

For each major section:
- Start with a simpler explanation.
- Then add the deeper reasoning or technical interpretation.
- Explain why the point matters in practice.

Use clear headings, concise bullet points where helpful, and avoid unnecessary jargon. Make it accessible to a reader with general education but no specialized background.

{style_guide}
{depth_guide}

Research Paper:
{paper_text}

Provide your response in well-structured markdown format.
Make the response deeply detailed, well-mannered, highly explanatory, and suitable for serious research review rather than brief.""",

    "weaknesses": """You are a critical research analyst and peer reviewer. Analyze this research paper and identify its weaknesses and improvement opportunities in deep detail.

Structure your analysis as:

## Methodological Weaknesses
- Identify flaws in research design, sample size, data collection, or analysis methods.
- Explain exactly why each flaw matters and what risk it creates for the results.

## Data and Evidence Gaps
- Highlight missing data, limited datasets, or insufficient evidence.
- Explain how each evidence gap changes confidence in the conclusions.

## Scope Limitations
- Explain what the study does not cover and any generalizability concerns.
- Clarify where the conclusions may not transfer well.

## Logical or Argumentation Weaknesses
- Point out unsupported claims, logical leaps, or correlation-versus-causation issues.
- Explain the reasoning step that breaks down.

## Areas for Improvement
- Suggest concrete ways to strengthen each identified weakness.
- Give realistic, actionable fixes rather than generic advice.

## Research Gaps to Explore
- Identify knowledge gaps that could support future deep research.

## Severity Prioritization
- Separate the most serious weaknesses from the minor ones.
- Explain what should be fixed first and why.

Be specific, cite relevant parts of the paper when possible, and remain constructive.

{style_guide}
{depth_guide}

Research Paper:
{paper_text}

Make the response deeply analytical, highly explanatory, constructive, and substantially detailed rather than brief.""",

    "new_ideas": """You are an innovative research scientist and strategic thinker. Based on this research paper, generate bold but plausible research ideas and future directions with deep explanation.

Structure your response as:

## Novel Research Directions
Provide 5 new research questions inspired by but going beyond this paper.

## Experimental Extensions
Provide 3 to 4 experiments that could build on this work.

## Cross-Domain Applications
Explain how these findings could transfer to other fields or industries.

## Interdisciplinary Opportunities
Explain how combining this work with other disciplines could create breakthroughs.

## Long-Term Research Visions
Provide 1 to 2 ambitious long-term research directions.

## Practical Applications
Describe real-world products, services, or systems that could be built from these ideas.

For each idea, explain:
- What it is
- Why it is interesting
- What problem it solves
- What data or experiments would be needed
- What impact it could have
- What the main implementation risk would be

{style_guide}
{depth_guide}

Research Paper:
{paper_text}

Make the response deeply detailed, imaginative, practical, and suitable for serious research planning rather than brief.""",

    "implementation": """You are a research project manager and implementation expert. Based on the identified weaknesses and new ideas from this research, provide a deep, detailed, actionable implementation roadmap.

Structure your response as:

## Priority Matrix
Start with a markdown table using these columns:
| Phase | Focus | Impact (High/Med/Low) | Feasibility (Easy/Medium/Hard) | Key Activities | Estimated Timeline | Resources Needed |

## Phase 1: Quick Wins (0-3 months)
Provide numbered steps to address the most critical gaps immediately.

## Phase 2: Core Improvements (3-12 months)
Provide numbered steps for medium-term improvements with specific milestones.

## Phase 3: New Research Launch (1-2 years)
Full roadmap for pursuing the most promising new research direction, including:
- Required resources and team
- Data collection strategy
- Methodology outline
- Expected outputs and publications

## Resource Planning
- Equipment or software needed
- Estimated timeline per phase
- Skills required in the team

## Success Metrics
Explain how to measure progress and determine whether each phase is complete.

## First 5 Action Steps
Provide the first five concrete actions to start immediately.

## Risks and Mitigations
- Identify the biggest implementation risks.
- Explain how to reduce each risk early.

## Simplified Explanation
- End with a plain-language explanation of the roadmap for a non-expert reader.

Formatting requirements:
- Use valid markdown.
- Use numbered lists for action sequences.
- Keep the table concise and readable.
- Do not merge multiple steps into one paragraph.
- Do not use emojis.

{style_guide}
{depth_guide}

Research Paper:
{paper_text}

Make this actionable, specific, realistic, deeply reasoned, and highly detailed rather than brief."""
}

@app.route('/')
def index():
    configured_model = sanitize_model_name(DEFAULT_OLLAMA_MODEL)
    threading.Thread(target=ensure_ollama_model, args=(configured_model,), daemon=True).start()
    configured_detail_level = sanitize_detail_level(DEFAULT_DETAIL_LEVEL)
    return render_template(
        'index.html',
        configured_model=configured_model,
        configured_detail_level=configured_detail_level,
        model_options=MODEL_OPTIONS,
        detail_level_options=DETAIL_LEVEL_OPTIONS,
    )


@app.post('/api/ollama/prepare')
def prepare_ollama_model():
    model_name = sanitize_model_name((request.get_json(silent=True) or {}).get('model'))
    threading.Thread(target=ensure_ollama_model, args=(model_name,), daemon=True).start()
    return jsonify({'success': True, 'model': model_name, 'preparing': True})


@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')


@app.route('/api/dashboard_metrics')
def dashboard_metrics():
    return jsonify(build_dashboard_payload())


@app.route('/api/history')
def api_history():
    return jsonify({
        'success': True,
        'items': list_history_items(),
    })


@app.route('/api/history/<context_id>')
def api_history_item(context_id):
    item = get_history_item(context_id)
    if not item:
        return jsonify({'error': 'That history item was not found.'}), 404
    return jsonify({
        'success': True,
        'item': item,
    })


@app.route('/api/history/<context_id>', methods=['DELETE'])
def api_history_delete(context_id):
    with HISTORY_LOCK:
        items = load_history_store()
        retained = [item for item in items if item.get('context_id') != context_id]
        if len(retained) == len(items):
            return jsonify({'error': 'That history item was not found.'}), 404
        write_history_store(retained)
    RESEARCH_CONTEXTS.pop(context_id, None)
    return jsonify({'success': True, 'context_id': context_id})


@app.route('/api/history/<context_id>/chat/reset', methods=['POST'])
def api_history_reset_chat(context_id):
    item = get_history_item(context_id)
    if not item:
        return jsonify({'error': 'That history item was not found.'}), 404
    clear_chat_turns(context_id)
    return jsonify({
        'success': True,
        'context_id': context_id,
    })

@app.route('/analyze', methods=['POST'])
def analyze():
    model_name = sanitize_model_name(request.form.get('model', ''))
    detail_level = sanitize_detail_level(request.form.get('detail_level', ''))
    analysis_type = request.form.get('analysis_type', 'summarize')

    paper_text, source_name, source_type, error = get_analysis_source(request)
    if error:
        return jsonify({'error': error}), 400
    references, reference_errors = collect_reference_dataset_texts(request)
    if request.form.get('internet_search', 'off') == 'on':
        live_queries, _ = generate_search_queries(
            'research paper literature',
            paper_text[:1200],
            model_name,
            timeout_seconds=20,
        )
        live_papers, live_errors, _ = collect_discovery_results(live_queries[:4], time.monotonic() + 45)
        for paper in deduplicate_papers(live_papers)[:8]:
            references.append({
                'filename': f"Live web source: {paper.get('title', 'Untitled')}",
                'text': (
                    f"Title: {paper.get('title', '')}\nAuthors: {paper.get('authors', '')}\n"
                    f"Year: {paper.get('year', '')}\nSource: {paper.get('source', '')}\n"
                    f"URL: {paper.get('url', '')}\nAbstract: {paper.get('abstract', '')}"
                ),
            })
        reference_errors.extend(live_errors)

    original_char_count = len(paper_text)
    original_token_estimate = estimate_token_count(paper_text)
    prompt_template = ANALYSIS_PROMPTS.get(analysis_type, ANALYSIS_PROMPTS['summarize'])
    prompt, prepared_text, used_reference_context = build_analysis_prompt(prompt_template, paper_text, references, detail_level)
    processed_char_count = len(prepared_text)
    processed_token_estimate = estimate_token_count(prepared_text)
    
    try:
        type_labels = {
            'summarize': 'Paper Summary',
            'weaknesses': 'Weakness Analysis',
            'new_ideas': 'New Research Ideas',
            'implementation': 'Implementation Roadmap'
        }

        response = call_ollama(prompt, model_name, timeout_seconds=None)
        if not response.ok:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            error_message = error_payload.get('error') or response.text or f'HTTP {response.status_code}'
            return jsonify({'error': f'Ollama error: {error_message}'}), response.status_code

        response_data = response.json()
        result_text = (response_data.get('message', {}).get('content') or '').strip()

        if not result_text:
            return jsonify({'error': 'The API returned an empty response.'}), 502

        chat_context = build_analysis_chat_context(
            source_name,
            prepared_text,
            model_name,
            {analysis_type: result_text},
            len(prepared_text.split()),
            processed_token_estimate,
            detail_level,
        )
        save_analysis_history_entry(
            chat_context,
            source_name,
            source_type,
            model_name,
            detail_level,
            len(prepared_text.split()),
            processed_token_estimate,
            {analysis_type: result_text},
            reference_errors,
            references,
        )
        
        return jsonify({
            'success': True,
            'result': result_text,
            'type': type_labels.get(analysis_type, 'Analysis'),
            'paper_name': source_name,
            'source_type': source_type,
            'word_count': len(prepared_text.split()),
            'char_count': processed_char_count,
            'estimated_tokens': processed_token_estimate,
            'original_char_count': original_char_count,
            'original_estimated_tokens': original_token_estimate,
            'was_truncated': original_char_count != processed_char_count,
            'reference_dataset_files': [item['filename'] for item in references],
            'reference_dataset_errors': reference_errors,
            'used_reference_dataset': bool(references),
            'detail_level': detail_level,
            'model': model_name,
            'context_id': chat_context['context_id'],
            'paper_id': chat_context['context_id'],
            'chat_context': chat_context,
        })
    except requests.ConnectionError:
        return jsonify({'error': 'Could not connect to the private AI engine. Start Ollama and make sure the configured model is installed.'}), 502
    except requests.Timeout:
        return jsonify({'error': 'Ollama took too long to respond. Try a smaller paper or a faster model.'}), 504
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'error': f'Unexpected Ollama response format: {str(e)}'}), 502
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/analyze_all', methods=['POST'])
def analyze_all():
    """Run all 4 analyses sequentially."""
    model_name = sanitize_model_name(request.form.get('model', ''))
    detail_level = sanitize_detail_level(request.form.get('detail_level', ''))

    paper_text, source_name, source_type, error = get_analysis_source(request)
    if error:
        return jsonify({'error': error}), 400
    references, reference_errors = collect_reference_dataset_texts(request)

    original_char_count = len(paper_text)
    original_token_estimate = estimate_token_count(paper_text)
    prepared_text, _ = prepare_analysis_corpus(paper_text, references)
    processed_char_count = len(prepared_text)
    processed_token_estimate = estimate_token_count(prepared_text)

    results = {}
    
    for analysis_type, prompt_template in ANALYSIS_PROMPTS.items():
        try:
            prompt, _, _ = build_analysis_prompt(prompt_template, paper_text, references, detail_level)
            response = call_ollama(prompt, model_name, timeout_seconds=None)
            if not response.ok:
                try:
                    error_payload = response.json()
                except ValueError:
                    error_payload = {}
                error_message = error_payload.get('error') or response.text or f'HTTP {response.status_code}'
                results[analysis_type] = f"Error in this analysis: {error_message}"
                continue

            response_data = response.json()
            result_text = (response_data.get('message', {}).get('content') or '').strip()
            results[analysis_type] = result_text or 'The API returned an empty response.'
        except Exception as e:
            results[analysis_type] = f"Error in this analysis: {str(e)}"

    chat_context = build_analysis_chat_context(
        source_name,
        prepared_text,
        model_name,
        results,
        len(prepared_text.split()),
        processed_token_estimate,
        detail_level,
    )
    save_analysis_history_entry(
        chat_context,
        source_name,
        source_type,
        model_name,
        detail_level,
        len(prepared_text.split()),
        processed_token_estimate,
        results,
        reference_errors,
        references,
    )
    
    return jsonify({
        'success': True,
        'results': results,
        'paper_name': source_name,
        'source_type': source_type,
        'word_count': len(prepared_text.split()),
        'char_count': processed_char_count,
        'estimated_tokens': processed_token_estimate,
        'original_char_count': original_char_count,
        'original_estimated_tokens': original_token_estimate,
        'was_truncated': original_char_count != processed_char_count,
        'reference_dataset_files': [item['filename'] for item in references],
        'reference_dataset_errors': reference_errors,
        'used_reference_dataset': bool(references),
        'detail_level': detail_level,
        'model': model_name,
        'context_id': chat_context['context_id'],
        'paper_id': chat_context['context_id'],
        'chat_context': chat_context,
    })


@app.route('/discover_papers', methods=['POST'])
def discover_papers():
    """Discover related research papers from web sources and rank them locally."""
    model_name = sanitize_model_name(request.form.get('model', ''))
    detail_level = sanitize_detail_level(request.form.get('detail_level', ''))
    field_name = request.form.get('field', '').strip()
    theme_name = request.form.get('theme', '').strip()
    crawl_minutes_raw = request.form.get('crawl_minutes', '').strip().lower()

    if not field_name:
        return jsonify({'error': 'Please enter the research field.'}), 400
    if not theme_name:
        return jsonify({'error': 'Please enter the research theme.'}), 400
    if crawl_minutes_raw in ('', 'none', 'manual', 'unlimited'):
        crawl_minutes = None
    else:
        try:
            crawl_minutes = int(crawl_minutes_raw)
        except ValueError:
            return jsonify({'error': 'Please select a valid crawl duration.'}), 400
        if crawl_minutes < 3 or crawl_minutes > 6:
            return jsonify({'error': 'Crawl duration must be between 3 and 6 minutes, or use No limit.'}), 400

    start_time = time.monotonic()
    deadline = None if crawl_minutes is None else start_time + (crawl_minutes * 60)
    query_timeout = None if deadline is None else min(20, max(3, int(remaining_seconds(deadline) - 5)))
    queries, focus = generate_search_queries(field_name, theme_name, model_name, timeout_seconds=query_timeout)
    all_papers, source_errors, stopped_due_to_time = collect_discovery_results(queries, deadline)

    papers = deduplicate_papers(all_papers)
    papers.sort(key=lambda item: (item.get("source") != "Google Scholar", item.get("year") or ""), reverse=True)
    papers = papers[:15]

    if not papers:
        ai_overview = build_discovery_fallback_overview(
            field_name,
            theme_name,
            queries,
            source_errors,
            model_name,
            detail_level,
            timeout_seconds=query_timeout,
        )
        chat_context = build_discovery_chat_context(
            field_name,
            theme_name,
            model_name,
            queries,
            [],
            ai_overview,
            source_errors,
            crawl_minutes,
            round(time.monotonic() - start_time, 1),
            detail_level,
        )
        elapsed_seconds = round(time.monotonic() - start_time, 1)
        save_discovery_history_entry(
            chat_context,
            field_name,
            theme_name,
            model_name,
            detail_level,
            queries,
            [],
            ai_overview,
            source_errors,
            crawl_minutes,
            elapsed_seconds,
            focus,
            stopped_due_to_time,
        )
        return jsonify({
            'success': True,
            'field': field_name,
            'theme': theme_name,
            'crawl_minutes': crawl_minutes,
            'elapsed_seconds': elapsed_seconds,
            'stopped_due_to_time': stopped_due_to_time,
            'focus': focus,
            'queries': queries,
            'papers': [],
            'ai_overview': ai_overview,
            'source_errors': source_errors,
            'model': model_name,
            'context_id': chat_context['context_id'],
            'paper_id': chat_context['context_id'],
            'chat_context': chat_context,
        })

    remaining = remaining_seconds(deadline)
    if deadline is None or remaining > 4:
        try:
            ai_overview = enrich_discovered_papers(
                papers,
                field_name,
                theme_name,
                model_name,
                detail_level,
                timeout_seconds=None if deadline is None else min(30, max(4, int(remaining))),
            )
        except Exception as exc:
            ai_overview = "## Research Focus\nA partial paper list was returned before the workflow fully completed.\n\n## Why These Papers Matter\nThe crawler collected relevant results, but the summary stage did not finish.\n\n## Observed Gaps\n- Local ranking summary was skipped or interrupted.\n\n## Next Search Directions\n- Narrow the theme for a tighter paper set.\n- Retry if you want a fuller ranked summary."
            source_errors.append(f"Local ranking summary: {str(exc)}")
    else:
        ai_overview = "## Research Focus\nA partial paper list was returned at the selected time limit.\n\n## Why These Papers Matter\nThe crawler stopped on time and returned the papers collected so far.\n\n## Observed Gaps\n- Local ranking summary was skipped to respect the selected time budget.\n\n## Next Search Directions\n- Increase the crawl budget if you want a richer ranked summary."

    chat_context = build_discovery_chat_context(
        field_name,
        theme_name,
        model_name,
        queries,
        papers,
        ai_overview,
        source_errors,
        crawl_minutes,
        round(time.monotonic() - start_time, 1),
        detail_level,
    )
    elapsed_seconds = round(time.monotonic() - start_time, 1)
    save_discovery_history_entry(
        chat_context,
        field_name,
        theme_name,
        model_name,
        detail_level,
        queries,
        papers,
        ai_overview,
        source_errors,
        crawl_minutes,
        elapsed_seconds,
        focus,
        stopped_due_to_time,
    )

    return jsonify({
        'success': True,
        'field': field_name,
        'theme': theme_name,
        'crawl_minutes': crawl_minutes,
        'elapsed_seconds': elapsed_seconds,
        'stopped_due_to_time': stopped_due_to_time,
        'focus': focus,
        'queries': queries,
        'papers': papers,
        'ai_overview': ai_overview,
        'source_errors': source_errors,
        'model': model_name,
        'context_id': chat_context['context_id'],
        'paper_id': chat_context['context_id'],
        'chat_context': chat_context,
    })


@app.route('/api/paper_chat', methods=['POST'])
def paper_chat():
    payload = request.get_json(silent=True) or {}
    context_id = (payload.get('context_id') or payload.get('paper_id') or '').strip()
    message = (payload.get('message') or '').strip()
    history = payload.get('history') or []
    selected_model = sanitize_model_name(payload.get('model') or '')

    if not context_id:
        return jsonify({'error': 'No research context is available yet. Run discovery or paper analysis first.'}), 400
    if not message:
        return jsonify({'error': 'Please enter a message for the chatbot.'}), 400
    if not isinstance(history, list):
        history = []

    context = load_research_context(context_id)
    if not context:
        return jsonify({'error': 'This research context expired. Please rerun the latest discovery or analysis.'}), 404

    stored_item = get_history_item(context_id)
    stored_history = stored_item.get('chat_history', []) if stored_item else []
    effective_history = stored_history if stored_history else history

    prompt = build_chat_prompt(context, message, effective_history)
    web_sources = []
    if payload.get('web_search', True):
        try:
            live_queries, _ = generate_search_queries(
                'current research updates', message, selected_model, timeout_seconds=15
            )
            live_papers, _, _ = collect_discovery_results(live_queries[:3], time.monotonic() + 30)
            for paper in deduplicate_papers(live_papers)[:6]:
                web_sources.append(
                    f"Title: {paper.get('title', '')}\nYear: {paper.get('year', '')}\n"
                    f"Source: {paper.get('source', '')}\nURL: {paper.get('url', '')}\n"
                    f"Abstract: {paper.get('abstract', '')}"
                )
        except Exception:
            web_sources = []
    if web_sources:
        prompt += (
            "\n\nLIVE WEB RESEARCH (use only as current supporting evidence; cite the URL when relevant):\n"
            + "\n\n".join(web_sources)
        )
    try:
        reply = call_ollama_text(
            prompt,
            selected_model,
            timeout_seconds=None,
            profile='analysis',
        )
        if not reply:
            return jsonify({'error': 'The chatbot returned an empty reply.'}), 502
        append_chat_turn(context_id, 'user', message)
        append_chat_turn(context_id, 'assistant', reply)
        return jsonify({
            'success': True,
            'reply': reply,
            'context_id': context_id,
            'model': selected_model,
            'history': (get_history_item(context_id) or {}).get('chat_history', []),
        })
    except requests.ConnectionError:
        return jsonify({'error': 'Could not connect to the private AI engine. Start Ollama and make sure the configured model is installed.'}), 502
    except requests.Timeout:
        return jsonify({'error': 'The chatbot timed out. Try a shorter follow-up question or a faster model.'}), 504
    except Exception as exc:
        return jsonify({'error': f'Chat request failed: {str(exc)}'}), 500

if __name__ == '__main__':
    # Disable Flask's reloader when running under a debugger to avoid SystemExit in debugpy.
    is_debugger_attached = os.environ.get("WERKZEUG_RUN_MAIN") == "true" or os.environ.get("VSCODE_PID")
    threading.Thread(target=ensure_ollama_model, args=(DEFAULT_OLLAMA_MODEL,), daemon=True).start()
    app.run(debug=True, port=5000, use_reloader=not is_debugger_attached)
