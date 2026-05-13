from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import import_videos


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
HISTORY_PATH = PROJECT_DIR / "history.json"
WEB_DIR = PROJECT_DIR / "web"

app = FastAPI(title="Importador LingQ")
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

job_lock = threading.Lock()
job_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "summary": "",
    "logs": [],
}


class JobLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        with job_lock:
            job_state["logs"].append(message)
            job_state["logs"] = job_state["logs"][-400:]


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def normalize_channel(channel: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(channel)
    normalized.setdefault("id", uuid.uuid4().hex)
    normalized.setdefault("ativo", True)
    normalized.setdefault("videos_por_execucao", 1)
    normalized["videos_por_execucao"] = max(1, int(normalized.get("videos_por_execucao") or 1))
    return normalized


def load_web_config() -> dict[str, Any]:
    config = read_json(CONFIG_PATH, {})
    config.setdefault("canais", [])
    original_channels = config["canais"]
    normalized_channels = [normalize_channel(channel) for channel in original_channels]
    config["canais"] = normalized_channels
    if normalized_channels != original_channels:
        write_json(CONFIG_PATH, config)
    return config


def save_web_config(config: dict[str, Any]) -> dict[str, Any]:
    current = load_web_config()
    merged = dict(current)
    merged.update(config)
    merged["canais"] = [normalize_channel(channel) for channel in config.get("canais", current["canais"])]
    write_json(CONFIG_PATH, merged)
    return merged


def find_channel(config: dict[str, Any], channel_id: str) -> tuple[int, dict[str, Any]]:
    for index, channel in enumerate(config["canais"]):
        if channel.get("id") == channel_id:
            return index, channel
    raise HTTPException(status_code=404, detail="Canal nao encontrado")


def run_import_job() -> None:
    handler = JobLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    import_videos.log.addHandler(handler)

    with job_lock:
        job_state.update({
            "running": True,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "exit_code": None,
            "summary": "Importacao em andamento",
            "logs": [],
        })

    exit_code = 0
    summary = "Importacao concluida"
    try:
        config = import_videos.load_config()
        history = import_videos.load_history()
        import_videos.log.info("=" * 60)
        import_videos.log.info("Inicio pela aplicacao web: %s", datetime.now().isoformat(timespec="seconds"))
        import_videos.run_import(config, history)
        import_videos.log.info("Fim: %s", datetime.now().isoformat(timespec="seconds"))
    except Exception as exc:
        exit_code = 1
        summary = f"Erro: {exc}"
        import_videos.log.exception("Erro inesperado na execucao web:")
    finally:
        import_videos.log.removeHandler(handler)
        with job_lock:
            job_state.update({
                "running": False,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "exit_code": exit_code,
                "summary": summary,
            })


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return load_web_config()


@app.put("/api/config")
def put_config(config: dict[str, Any]) -> dict[str, Any]:
    return save_web_config(config)


@app.get("/api/lingq-session")
def get_lingq_session() -> dict[str, bool]:
    cookies = (load_web_config().get("lingq_cookies") or {})
    has_session_cookie = bool(
        str(cookies.get("sessionid") or "").strip()
        or str(cookies.get("wwwlingqcomsa") or "").strip()
    )
    return {
        "has_sessionid": has_session_cookie,
        "has_csrftoken": bool(str(cookies.get("csrftoken") or "").strip()),
    }


@app.put("/api/lingq-session")
def put_lingq_session(payload: dict[str, Any]) -> dict[str, bool]:
    config = load_web_config()
    session_cookie = str(payload.get("sessionid") or payload.get("wwwlingqcomsa") or "").strip()
    csrftoken = str(payload.get("csrftoken") or "").strip()
    if session_cookie or csrftoken:
        if not session_cookie or not csrftoken:
            raise HTTPException(status_code=400, detail="Informe o cookie de sessao e o csrftoken.")
        config["lingq_cookies"] = {
            "wwwlingqcomsa": session_cookie,
            "csrftoken": csrftoken,
        }
    else:
        config["lingq_cookies"] = {}
    save_web_config(config)
    return get_lingq_session()


@app.get("/api/channels")
def list_channels() -> list[dict[str, Any]]:
    return load_web_config()["canais"]


@app.post("/api/channels")
def create_channel(channel: dict[str, Any]) -> dict[str, Any]:
    required = ["nome", "idioma", "lang_code", "url"]
    missing = [field for field in required if not str(channel.get(field, "")).strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Campos obrigatorios: {', '.join(missing)}")

    config = load_web_config()
    new_channel = normalize_channel(channel)
    config["canais"].append(new_channel)
    save_web_config(config)
    return new_channel


@app.put("/api/channels/{channel_id}")
def update_channel(channel_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    config = load_web_config()
    index, channel = find_channel(config, channel_id)
    updated = normalize_channel({**channel, **patch, "id": channel_id})
    config["canais"][index] = updated
    save_web_config(config)
    return updated


@app.delete("/api/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict[str, str]:
    config = load_web_config()
    index, _ = find_channel(config, channel_id)
    del config["canais"][index]
    save_web_config(config)
    return {"status": "deleted"}


@app.post("/api/import/run")
def run_import(background_tasks: BackgroundTasks) -> dict[str, str]:
    with job_lock:
        if job_state["running"]:
            raise HTTPException(status_code=409, detail="Ja existe uma importacao em andamento")
        job_state["running"] = True
        job_state["summary"] = "Importacao na fila"
    background_tasks.add_task(run_import_job)
    return {"status": "started"}


@app.get("/api/import/status")
def import_status() -> dict[str, Any]:
    with job_lock:
        return dict(job_state)


@app.get("/api/history")
def history() -> dict[str, Any]:
    data = read_json(HISTORY_PATH, {})
    items = []
    for video_id, item in data.items():
        item = dict(item)
        item["id"] = video_id
        items.append(item)
    items.sort(key=lambda item: item.get("importado_em", ""), reverse=True)
    return {"items": items[:200], "total": len(data)}
