"""FastAPI backend for the Pasito Lab dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import proto, store
from .ble import PasitoClient, PasitoError, scan

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ValuesBody(BaseModel):
    values: Optional[list[int]] = None
    profile_id: Optional[str] = None
    handshake: bool = False


class ProfileBody(BaseModel):
    name: str
    values: list[int]
    note: str = ""


class WallpaperBody(BaseModel):
    values: Optional[list[int]] = None
    profile_id: Optional[str] = None
    theme: str = "aurora"
    apply: bool = False
    meta: Optional[dict] = None


def resolve_values(body: ValuesBody | WallpaperBody) -> list[int]:
    if body.profile_id:
        profile = store.get_profile(body.profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="профиль не найден")
        return profile["values"]
    if body.values:
        if len(body.values) != proto.DVW_POINTS:
            raise HTTPException(status_code=422, detail=f"нужно {proto.DVW_POINTS} значений")
        return [int(v) for v in body.values]
    return list(store.DEFAULT_VALUES)


DEMO = os.environ.get("PASITO_DEMO", "") not in ("", "0", "false", "no")

# Real frames captured from hardware; replayed in demo mode.
DEMO_FRAMES = (
    bytes.fromhex("5c03001d003f5c3704575f00003f000000ffff1f00322a0000ff0f0000"),
    bytes.fromhex("510300020000"),
    bytes.fromhex("700300040118b441"),
)


class Lab:
    """Owns the single BLE link, the frame ring buffer and the WebSocket fan-out."""

    def __init__(self) -> None:
        self.demo = DEMO
        self.client = PasitoClient(on_frame=self._on_frame, on_state=self._notify_state)
        self.frames: deque = deque(maxlen=2000)
        self.frames_total = 0
        self.subscribers: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()
        self.watch = False
        self.task: asyncio.Task | None = None
        self.capture: store.CaptureWriter | None = None
        self.last_error = ""

    # --- state -------------------------------------------------------
    def state(self) -> dict:
        store.ensure_dirs()
        return {
            "connected": True if self.demo else self.client.connected,
            "address": "DEMO" if self.demo else self.client.address,
            "name": "LE-S052 (demo)" if self.demo else self.client.name,
            "watch": self.watch,
            "frames_total": self.frames_total,
            "frames": [f.to_dict() for f in list(self.frames)[:200]],
            "profiles": store.load_profiles(),
            "capture_file": str(self.capture.path) if self.capture else "",
            "last_error": self.last_error,
        }

    def _notify_state(self) -> None:
        self.broadcast({"type": "state", "state": self.state()})

    def broadcast(self, message: dict) -> None:
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except Exception:
                    pass

    # --- frames ------------------------------------------------------
    def _on_frame(self, frame) -> None:
        self.frames_total += 1
        self.frames.appendleft(frame)
        if self.capture:
            self.capture.frame(frame)
        self.broadcast({"type": "frame", "frame": frame.to_dict()})

    # --- watch loop --------------------------------------------------
    def start_watch(self) -> None:
        self.watch = True
        if self.capture is None:
            self.capture = store.CaptureWriter()
            self.capture.event("watch_started", mode="demo" if self.demo else "ble")
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._demo_loop() if self.demo else self._watch_loop())
        self._notify_state()

    def stop_watch(self) -> None:
        self.watch = False
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def _watch_loop(self) -> None:
        while self.watch:
            try:
                if not self.client.connected:
                    await self.client.connect()
                    await self.client.subscribe()
                    self.last_error = ""
                    self._notify_state()
                await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("watch loop: %s", exc)
                self._notify_state()
                await asyncio.sleep(2.0)

    async def _demo_loop(self) -> None:
        """Replay captured frames so the dashboard can be developed without hardware."""
        seq = 0x10
        while self.watch:
            for rest in DEMO_FRAMES:
                frame = proto.Frame.parse(bytes([seq & 0xFF]) + rest)
                if frame is not None:
                    self._on_frame(frame)
                seq += 1
                await asyncio.sleep(0.07)
            await asyncio.sleep(1.2)

    async def send_dvw(self, values, handshake: bool) -> dict:
        if self.demo:
            await asyncio.sleep(0.25)
            frame = proto.dvw_frame(values)
            return {
                "values": list(values),
                "frame_hex": proto.hx(frame),
                "handshake": handshake,
                "att_ack": True,
                "read_hex": proto.hx(frame),
                "steps": [f"{proto.hx(frame)} -> 0004 (demo)"],
            }
        async with self.lock:
            if not self.client.connected:
                await self.client.connect()
            result = await self.client.send_dvw(values, handshake=handshake)
            self.last_error = ""
            return result

    async def shutdown(self) -> None:
        self.stop_watch()
        try:
            await self.client.disconnect()
        except Exception:
            pass
        if self.capture:
            self.capture.close()
            self.capture = None


lab = Lab()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.ensure_dirs()
    yield
    await lab.shutdown()


app = FastAPI(title="Pasito Lab Linux", version="0.1.0", lifespan=lifespan)


# --- API -----------------------------------------------------------------
@app.get("/api/state")
async def api_state() -> dict:
    return lab.state()


@app.get("/api/frames")
async def api_frames(limit: int = 200) -> list[dict]:
    return [f.to_dict() for f in list(lab.frames)[: max(1, min(limit, 2000))]]


@app.get("/api/scan")
async def api_scan(timeout: float = 6.0, all: bool = False) -> list[dict]:
    try:
        return [f.to_dict() for f in await scan(timeout, include_all=all)]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/connect")
async def api_connect(body: Optional[dict] = None) -> dict:
    if body and body.get("address"):
        lab.client.address = str(body["address"]).strip() or None
    lab.start_watch()
    return {"ok": True, "watch": True}


@app.post("/api/disconnect")
async def api_disconnect() -> dict:
    lab.stop_watch()
    await lab.client.disconnect()
    lab._notify_state()
    return {"ok": True}


@app.get("/api/profiles")
async def api_profiles() -> list[dict]:
    return store.load_profiles()


@app.post("/api/profiles")
async def api_profile_create(body: ProfileBody) -> dict:
    try:
        return store.add_profile(body.name, body.values, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/profiles/{pid}")
async def api_profile_update(pid: str, body: ProfileBody) -> dict:
    try:
        profile = store.update_profile(pid, name=body.name, values=body.values, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not profile:
        raise HTTPException(status_code=404, detail="профиль не найден")
    return profile


@app.delete("/api/profiles/{pid}")
async def api_profile_delete(pid: str) -> dict:
    if not store.delete_profile(pid):
        raise HTTPException(status_code=404, detail="профиль не найден")
    return {"ok": True}


@app.post("/api/send")
async def api_send(body: ValuesBody) -> dict:
    values = resolve_values(body)
    try:
        result = await lab.send_dvw(values, handshake=body.handshake)
    except PasitoError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": bool(result.get("att_ack")),
        "att_ack": bool(result.get("att_ack")),
        "detail": "ATT-запись принята; применение профиля на устройстве не подтверждено", 
        "raw": result.get("frame_hex", ""),
        "read_back": result.get("read_hex", ""),
    }


@app.get("/api/capture/download")
async def api_capture_download():
    if not lab.capture or not lab.capture.path.exists():
        raise HTTPException(status_code=404, detail="запись ещё не начата")
    lab.capture.path.parent.mkdir(parents=True, exist_ok=True)
    return FileResponse(str(lab.capture.path), filename=lab.capture.path.name, media_type="text/plain")


@app.post("/api/wallpaper")
async def api_wallpaper(body: WallpaperBody) -> dict:
    try:
        from . import wallpaper
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=f"модуль обоев недоступен: {exc}") from exc
    values = resolve_values(body)
    store.ensure_dirs()
    path = store.WALLPAPERS_DIR / f"pasito-{body.theme}-{int(time.time())}.png"
    try:
        generated = wallpaper.generate_png(values, path, theme=body.theme, meta=body.meta)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    applied, detail = False, ""
    if body.apply:
        try:
            applied, detail = wallpaper.apply_wallpaper(generated)
        except Exception as exc:
            detail = str(exc)
    return {"path": str(generated), "applied": applied, "detail": detail}


@app.get("/api/wallpaper/preview.png")
async def api_wallpaper_preview(theme: str = "aurora", values: Optional[str] = None) -> Response:
    try:
        from . import wallpaper
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=f"модуль обоев недоступен: {exc}") from exc
    if values:
        try:
            parsed = [int(part) for part in values.replace(";", ",").split(",") if part.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="values: ожидаются целые числа") from exc
        if len(parsed) != proto.DVW_POINTS:
            raise HTTPException(status_code=422, detail=f"нужно {proto.DVW_POINTS} значений")
    else:
        parsed = list(store.DEFAULT_VALUES)
    try:
        png = wallpaper.render_preview(parsed, theme=theme)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/meta")
async def api_meta() -> dict:
    return {
        "version": "0.1.0",
        "protocol": {
            "service": proto.SERVICE_UUID,
            "chars": {
                "rx": proto.RX_UUID,
                "notify": proto.NOTIFY_UUID,
                "control": proto.CONTROL_UUID,
            },
            "frame_layout": "[seq:u8][msg:u16 LE][flags:u8][len:u8][payload]",
        },
        "constants": {"min_w": proto.MIN_W, "max_w": proto.MAX_W, "points": proto.DVW_POINTS},
        "themes": ["aurora", "ember", "mono", "minimal"],
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    lab.subscribers.add(queue)
    try:
        await websocket.send_json({"type": "hello", "state": lab.state()})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        lab.subscribers.discard(queue)


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
