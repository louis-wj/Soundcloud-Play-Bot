"""FastAPI server — REST endpoints + WebSocket for real-time stats."""
import asyncio, json, logging, os, re, threading
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from system_info import get_system_info
from proxy_utils import check_proxies, detect_format
from engine import BotEngine, fetch_track_metadata, get_client_id
from mixcloud_engine import router as mx_router, mx_engine
from sc_accounts_engine import router as sc_accounts_router, sc_accounts_engine

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="SoundCloud PlayBot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(mx_router)
app.include_router(sc_accounts_router)

engine = BotEngine()

# ── Models ────────────────────────────────────────────────────────────────────

class TrackRequest(BaseModel):
    track_input: str  # ID or URL

class ProxyCheckRequest(BaseModel):
    proxies: list[str]

class StartRequest(BaseModel):
    track_id: str
    num_plays: int
    concurrency: int
    proxies: list[str]
    use_proxies: bool = True


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/system")
def system_info():
    return get_system_info()

@app.post("/api/track")
def get_track(req: TrackRequest):
    # Extract track ID from URL if needed
    tid = req.track_input.strip()
    # Handle full URLs
    if '/' in tid:
        # Try to resolve via API
        m = re.search(r'/tracks/(\d+)', tid)
        if m:
            tid = m.group(1)
        else:
            # It's a permalink — resolve it
            from curl_cffi import requests as crequests
            try:
                resp = crequests.get(
                    f'https://api-v2.soundcloud.com/resolve?url={tid}&client_id={get_client_id()}',
                    timeout=15, impersonate='chrome110',
                )
                if resp.status_code == 200:
                    tid = str(resp.json().get('id', tid))
            except Exception:
                pass
    try:
        return fetch_track_metadata(tid)
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/proxies/check")
def check_proxy_list(req: ProxyCheckRequest):
    proxies = [p.strip() for p in req.proxies if p.strip()]
    if not proxies:
        return {"error": "No proxies provided"}
    fmt = detect_format(proxies[0]) if proxies else "unknown"
    result = check_proxies(proxies, max_workers=10)
    result["detected_format"] = fmt
    return result

@app.post("/api/start")
def start_bot(req: StartRequest):
    proxies = [p.strip() for p in req.proxies if p.strip()]
    if req.use_proxies and not proxies:
        return {"error": "No proxies provided for Proxy Mode"}
    if engine.running:
        return {"error": "Already running"}
    
    # If not using proxies, pass an empty list (engine handled accordingly)
    final_proxies = proxies if req.use_proxies else []
    engine.start(req.track_id, req.num_plays, req.concurrency, final_proxies)
    return {"status": "started"}

@app.post("/api/stop")
def stop_bot():
    engine.stop()
    return {"status": "stopping"}

@app.get("/api/stats")
def get_stats():
    snap = engine.get_snapshot()
    snap["running"] = engine.running
    return snap

# ── Log capture for UI ────────────────────────────────────────────────────────

_log_buffer = []
_log_lock = threading.Lock()

class _WSLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            with _log_lock:
                _log_buffer.append({"time": record.created, "level": record.levelname, "message": msg})
                if len(_log_buffer) > 200:
                    _log_buffer.pop(0)
        except Exception:
            pass

_wslh = _WSLogHandler()
_wslh.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(_wslh)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_stats(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            snap = engine.get_snapshot()
            with _log_lock:
                logs = list(_log_buffer[-100:])
            mx_snap = mx_engine.get_snapshot()
            sc_snap = sc_accounts_engine.get_snapshot()
            payload = {"type": "state", "stats": snap, "mx_stats": mx_snap, "sc_accounts_stats": sc_snap, "logs": logs}
            try:
                await websocket.send_json(payload)
            except Exception:
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

# ── Serve frontend static files if built ──────────────────────────────────────
# In packaged mode the frontend dist is served by Electron, not FastAPI.
# In dev mode, serve it if the build exists.

_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'dist')
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8899)
