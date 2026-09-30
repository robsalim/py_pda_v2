import asyncio
import threading
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import os

from config import WEB_HOST, WEB_PORT
from database.db_service import DatabaseService

app = FastAPI(title="PDA Web Viewer")
db_service: Optional[DatabaseService] = None
# Реестр живых значений DriverManager; когда он есть, актуальные состояния
# отдаются из памяти, без запроса к истории
registry = None

def parse_span(text: str) -> timedelta:
    t = str(text).strip()
    if "15" in t and "мин" in t: return timedelta(minutes=15)
    if "1" in t and "час" in t and "12" not in t: return timedelta(hours=1)
    if "4" in t and "час" in t and "24" not in t: return timedelta(hours=4)
    if "12" in t: return timedelta(hours=12)
    if "72" in t: return timedelta(hours=72)
    if "24" in t: return timedelta(hours=24)
    return timedelta(hours=1)

@app.get("/")
async def get_index():
    return FileResponse("web/index.html")

@app.get("/api/tags")
async def get_tags():
    if not db_service:
        return []
    tags = db_service.get_all_tags()
    conn_names = {c.id: c.name for c in db_service.get_all_connections()}
    out = []
    for t in tags:
        try:
            addr = int(t.address_str)
        except Exception:
            addr = 0
        out.append({"id": t.id, "name": t.name, "address": addr, "unit": t.unit,
                    "data_type": t.data_type, "group": t.group_name or "Общие",
                    "connection": conn_names.get(t.connection_id, "")})
    return out

@app.get("/api/points/{tag_id}")
async def get_points(tag_id: int, span: str = "1 час"):
    if not db_service:
        return {"times": [], "values": []}
    delta = parse_span(span)
    points, t_start, t_end = db_service.get_data_points_by_data_span(tag_id, delta)
    times = [p.timestamp.timestamp() for p in points]
    values = [p.value for p in points]
    return {"times": times, "values": values, "t_start": t_start, "t_end": t_end}

@app.get("/api/archive/{tag_id}")
async def get_archive(tag_id: int, start: str, duration: str = "1 час"):
    if not db_service:
        return {"times": [], "values": []}
    try:
        start_dt = datetime.fromisoformat(start)
    except Exception:
        start_dt = datetime.now() - timedelta(hours=1)
    dur_delta = parse_span(duration)
    end_dt = start_dt + dur_delta

    points = db_service.get_data_points_range(tag_id, start_dt, end_dt)
    times = [p.timestamp.timestamp() for p in points]
    values = [p.value for p in points]
    return {
        "times": times, 
        "values": values, 
        "t_start": start_dt.timestamp(), 
        "t_end": end_dt.timestamp()
    }

@app.get("/api/live")
async def get_live():
    """Текущие значения всех сигналов из реестра (без обращения к истории)."""
    if registry is not None:
        live = registry.live_snapshot()
        out = []
        for tid, (ts, val, quality) in live.items():
            p = registry.get(tid)
            out.append({
                "id": tid,
                "name": getattr(p, "name", ""),
                "value": val,
                "quality": quality,
                "timestamp": ts.timestamp() if ts is not None else None,
            })
        return out
    if not db_service:
        return []
    last = db_service.get_tag_last_values()
    return [{"id": tid, "value": v, "timestamp": ts.timestamp()}
            for tid, (ts, v) in last.items()]


def run_web_server(db: DatabaseService, tag_registry=None):
    global db_service, registry
    db_service = db
    registry = tag_registry
    # use_colors=False обязателен: под pythonw.exe (run.bat) sys.stdout=None,
    # и uvicorn падает на sys.stdout.isatty() при настройке логгера —
    # поток веб-сервера умирал молча, без окна консоли.
    # Запускаем без автоматического вызова браузера
    config = uvicorn.Config(app, host=WEB_HOST, port=WEB_PORT,
                            log_level="warning", use_colors=False)
    server = uvicorn.Server(config)
    server.run()

def start_web_thread(db: DatabaseService, tag_registry=None):
    t = threading.Thread(target=run_web_server, args=(db, tag_registry), daemon=True)
    t.start()
    return t
