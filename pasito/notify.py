"""Desktop notifications and UI sounds for Pasito Lab.

Design rules:
- never raise: a missing notify-send / sound daemon is not an application error;
- every subprocess call has a timeout, so BLE callbacks can never hang;
- quiet mode for tests and headless runs: ``PASITO_NOTIFY=0`` makes every call a
  no-op success without touching dbus or the speakers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "send",
    "sound",
    "connected",
    "disconnected",
    "profile_sent",
    "high_power",
    "quiet_mode",
    "SOUND_DIR",
]

APP_NAME = "Pasito Lab"
DEFAULT_TIMEOUT = 15
SOUND_TIMEOUT = 10
SOUND_DIR = Path("/usr/share/sounds/freedesktop/stereo")
VALID_URGENCY = ("low", "normal", "critical")
_QUIET_VALUES = ("0", "false", "off", "no")


def quiet_mode() -> bool:
    """PASITO_NOTIFY=0 (or false/off/no) disables notifications and sounds."""
    return os.environ.get("PASITO_NOTIFY", "1").strip().lower() in _QUIET_VALUES


def _sound_path(name: str) -> Path | None:
    """Resolve a canberra event name / explicit path to an .oga file."""
    raw = Path(name).expanduser()
    if raw.suffix.lower() in {".oga", ".ogg", ".wav"}:
        return raw if raw.is_file() else None
    for variant in (name, name.replace("-", "_"), name.replace("_", "-")):
        candidate = SOUND_DIR / f"{variant}.oga"
        if candidate.is_file():
            return candidate
    return None


def _run(command: list[str], timeout: float) -> bool:
    """Run a UI-side command, swallowing every expected failure."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def send(title: str, body: str, urgency: str = "normal", sound: str | None = None, icon: str | None = None) -> bool:
    """Show a desktop notification; returns True only if notify-send accepted it."""
    if quiet_mode():
        return True
    executable = shutil.which("notify-send")
    if not executable:
        return False

    level = urgency if urgency in VALID_URGENCY else "normal"
    command = [executable, "-a", APP_NAME, "-u", level]
    if icon:
        command += ["-i", str(icon)]
    command += [str(title), str(body)]

    if not _run(command, DEFAULT_TIMEOUT):
        return False
    if isinstance(sound, str) and sound:
        _play_sound(sound)
    return True


def sound(name: str) -> bool:
    """Play a freedesktop event sound: canberra first, paplay as fallback."""
    if quiet_mode():
        return True
    if not name:
        return False

    canberra = shutil.which("canberra-gtk-play")
    if canberra and _run([canberra, "-i", str(name)], SOUND_TIMEOUT):
        return True

    paplay = shutil.which("paplay")
    if not paplay:
        return False
    path = _sound_path(str(name))
    if path is None:
        return False
    return _run([paplay, str(path)], SOUND_TIMEOUT)


# `send(sound=...)` parameter shadows the function name; keep a private alias.
_play_sound = sound


# ---------------------------------------------------------------------------
# High-level events (short Russian texts for the desktop)
# ---------------------------------------------------------------------------


def connected(addr: str) -> bool:
    return send(
        "Pasito III подключён",
        f"Устройство {addr} на связи. Можно читать кадры.",
        urgency="low",
        sound="device-added",
    )


def disconnected() -> bool:
    return send(
        "Pasito III отключён",
        "Связь с устройством потеряна. Сервер попробует переподключиться.",
        urgency="normal",
        sound="device-removed",
    )


def profile_sent(values, att_ack: bool) -> bool:
    if isinstance(values, int) and not isinstance(values, bool):
        count = values
    else:
        try:
            count = len(list(values))
        except TypeError:
            count = 0
    ack = "ATT-подтверждение получено" if att_ack else "без ATT-подтверждения"
    return send(
        "Профиль DVW отправлен",
        f"{count} точек, {ack}.",
        urgency="normal",
        sound="complete" if att_ack else "dialog-warning",
    )


def high_power(watts) -> bool:
    try:
        shown = f"{int(round(float(watts)))} Вт"
    except (TypeError, ValueError):
        shown = f"{watts} Вт"
    return send(
        "Высокая мощность",
        f"{shown} — проверьте койл и аккумулятор.",
        urgency="critical",
        sound="dialog-warning",
    )
