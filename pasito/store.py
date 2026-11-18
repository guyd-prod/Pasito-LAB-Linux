"""Local storage: profiles, captures, wallpapers (XDG data dir)."""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("PASITO_DATA_DIR", Path.home() / ".local" / "share" / "pasito-lab"))
PROFILES_FILE = DATA_DIR / "profiles.json"
CAPTURES_DIR = DATA_DIR / "captures"
WALLPAPERS_DIR = DATA_DIR / "wallpapers"

DEFAULT_VALUES = [20, 20, 20, 20, 20, 20, 20, 20, 20, 20]


def ensure_dirs() -> None:
    for d in (DATA_DIR, CAPTURES_DIR, WALLPAPERS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _validate(values) -> list[int]:
    vals = [int(v) for v in values]
    if len(vals) != 10:
        raise ValueError("DVW profile must have exactly 10 points")
    for v in vals:
        if not 0 <= v <= 255:
            raise ValueError(f"value {v} does not fit one byte (0..255)")
    return vals


def load_profiles() -> list[dict]:
    ensure_dirs()
    if not PROFILES_FILE.exists():
        return []
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, dict) and "values" in p]
    except (json.JSONDecodeError, OSError):
        return []


def save_profiles(profiles: list[dict]) -> None:
    ensure_dirs()
    tmp = PROFILES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROFILES_FILE)


def add_profile(name: str, values, note: str = "") -> dict:
    profiles = load_profiles()
    profile = {
        "id": uuid.uuid4().hex[:8],
        "name": (name or "Без названия").strip(),
        "values": _validate(values),
        "note": note or "",
        "created": _now(),
    }
    profiles.append(profile)
    save_profiles(profiles)
    return profile


def get_profile(pid: str) -> dict | None:
    return next((p for p in load_profiles() if p.get("id") == pid), None)


def update_profile(pid: str, name: str | None = None, values=None, note: str | None = None) -> dict | None:
    profiles = load_profiles()
    for p in profiles:
        if p.get("id") == pid:
            if name is not None:
                p["name"] = name.strip() or p["name"]
            if values is not None:
                p["values"] = _validate(values)
            if note is not None:
                p["note"] = note
            save_profiles(profiles)
            return p
    return None


def delete_profile(pid: str) -> bool:
    profiles = load_profiles()
    kept = [p for p in profiles if p.get("id") != pid]
    if len(kept) == len(profiles):
        return False
    save_profiles(kept)
    return True


def new_capture_path(stem: str = "session") -> Path:
    ensure_dirs()
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return CAPTURES_DIR / f"{stem}-{stamp}.jsonl"


class CaptureWriter:
    """Append-only JSONL writer for frames and events."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else new_capture_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def frame(self, frame) -> None:
        self.write({"kind": "frame", **frame.to_dict()})

    def event(self, name: str, **fields) -> None:
        self.write({"kind": "event", "event": name, "t": _dt.datetime.now().isoformat(timespec="seconds"), **fields})

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass
