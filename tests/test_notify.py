"""Tests for notifications/sounds and the HUD helpers.

Everything is offline: PASITO_NOTIFY=0 is set for every test by an autouse
fixture, subprocesses are mocked, and the HUD is exercised through its pure
HTTP/formatting helpers (no window is ever opened).
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pasito import hud, notify

# ---------------------------------------------------------------------------
# Fixtures: keep the real desktop out of the test run
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def quiet_by_default(monkeypatch):
    monkeypatch.setenv("PASITO_NOTIFY", "0")
    return None


@pytest.fixture
def loud(monkeypatch):
    monkeypatch.delenv("PASITO_NOTIFY", raising=False)
    return None


class _FakeRun:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        assert kwargs.get("timeout"), "subprocess must always use a timeout"
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


def test_quiet_mode_skips_subprocess(monkeypatch):
    assert notify.quiet_mode() is True

    def explode(*_args, **_kwargs):
        raise AssertionError("no subprocess is allowed in quiet mode")

    monkeypatch.setattr(notify.subprocess, "run", explode)
    assert notify.send("Заголовок", "Текст", urgency="critical", sound="complete") is True
    assert notify.sound("complete") is True


def test_send_without_notify_send_returns_false(loud, monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify.send("Заголовок", "Текст") is False


def test_send_builds_expected_command(loud, monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", fake)
    assert notify.send("Pasito", "Готово", urgency="critical", icon="battery") is True
    assert fake.calls == [
        ["/usr/bin/notify-send", "-a", "Pasito Lab", "-u", "critical", "-i", "battery", "Pasito", "Готово"]
    ]


def test_send_normalizes_unknown_urgency(loud, monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", fake)
    assert notify.send("t", "b", urgency="loud") is True
    assert "normal" in fake.calls[0]


def test_send_plays_sound_only_when_asked(loud, monkeypatch):
    played = []
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", _FakeRun())
    monkeypatch.setattr(notify, "_play_sound", lambda name: played.append(name) or True)
    assert notify.send("t", "b", sound="complete") is True
    assert played == ["complete"]


@pytest.mark.parametrize(
    "exception",
    [OSError("boom"), subprocess.TimeoutExpired(["notify-send"], 15), subprocess.SubprocessError("nope")],
)
def test_send_swallows_process_errors(loud, monkeypatch, exception):
    def fake_run(command, **kwargs):
        raise exception

    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify.send("t", "b") is False


def test_send_returns_false_on_nonzero_exit(loud, monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", _FakeRun(returncode=1, stderr="no bus"))
    assert notify.send("t", "b") is False


# ---------------------------------------------------------------------------
# sound()
# ---------------------------------------------------------------------------


def test_sound_missing_binaries_is_false(loud, monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify.sound("complete") is False


def test_sound_uses_canberra_first(loud, monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/canberra-gtk-play")
    monkeypatch.setattr(notify.subprocess, "run", fake)
    assert notify.sound("complete") is True
    assert fake.calls == [["/usr/bin/canberra-gtk-play", "-i", "complete"]]


def test_sound_falls_back_to_paplay(loud, monkeypatch, tmp_path):
    sound_file = tmp_path / "complete.oga"
    sound_file.write_bytes(b"OggS")
    monkeypatch.setattr(notify, "SOUND_DIR", tmp_path)

    def which(name):
        return {"paplay": "/usr/bin/paplay"}.get(name)

    fake = _FakeRun()
    monkeypatch.setattr(notify.shutil, "which", which)
    monkeypatch.setattr(notify.subprocess, "run", fake)
    assert notify.sound("complete") is True
    assert fake.calls == [["/usr/bin/paplay", str(sound_file)]]


def test_sound_failure_is_silent(loud, monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/canberra-gtk-play")
    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify.sound("complete") is False


def test_sound_without_name_is_false(loud):
    assert notify.sound("") is False


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_send(monkeypatch):
    calls = []

    def fake_send(title, body, urgency="normal", sound=None, icon=None):
        calls.append({"title": title, "body": body, "urgency": urgency, "sound": sound, "icon": icon})
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    return calls


def test_connected_event(captured_send):
    assert notify.connected("41:42:FD:AA:BB:CC") is True
    call = captured_send[0]
    assert "41:42:FD:AA:BB:CC" in call["body"]
    assert call["urgency"] == "low"
    assert call["sound"] == "device-added"


def test_disconnected_event(captured_send):
    assert notify.disconnected() is True
    call = captured_send[0]
    assert "связь" in call["body"].lower() or "отключ" in call["title"].lower()
    assert call["sound"] == "device-removed"


@pytest.mark.parametrize("att_ack", [True, False])
def test_profile_sent_event(captured_send, att_ack):
    assert notify.profile_sent([20, 30, 40, 50, 60, 70, 80, 90, 100, 100], att_ack) is True
    call = captured_send[0]
    assert "10" in call["body"]
    assert ("получено" in call["body"]) is att_ack


def test_profile_sent_accepts_count(captured_send):
    assert notify.profile_sent(10, True) is True
    assert "10" in captured_send[0]["body"]


def test_high_power_event(captured_send):
    assert notify.high_power(87.6) is True
    call = captured_send[0]
    assert "88" in call["body"]
    assert call["urgency"] == "critical"


def test_high_power_bad_input_does_not_raise(captured_send):
    assert notify.high_power("много") is True
    assert "много" in captured_send[0]["body"]


# ---------------------------------------------------------------------------
# HUD helpers: fetch_state without a server, formatting, no GTK needed
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_hud_fetch_state_no_server_returns_none():
    port = _free_port()
    assert hud.fetch_state(f"http://127.0.0.1:{port}/api/state", timeout=0.4) is None


class _StateHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.endswith("/bad"):
            body = b"not json at all"
        elif self.path.endswith("/error"):
            body = b"oops"
        else:
            body = json.dumps(
                {
                    "connected": True,
                    "address": "41:42:FD:AA:BB:CC",
                    "name": "LE-S052",
                    "watch": True,
                    "frames_total": 42,
                    "frames": [
                        {
                            "seq": 10,
                            "msg": 860,
                            "msg_name": "state",
                            "flags": 0,
                            "declared_len": 29,
                            "payload_hex": "00 3f 5c 01 02 03 04 05 06 07 08 09 0a 0b",
                            "truncated": True,
                        }
                    ],
                }
            ).encode()
        status = 500 if self.path.endswith("/error") else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.fixture
def state_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StateHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/api/state"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hud_fetch_state_ok(state_server):
    state = hud.fetch_state(state_server, timeout=2)
    assert isinstance(state, dict)
    assert state["connected"] is True
    assert state["frames_total"] == 42


def test_hud_fetch_state_bad_json(state_server):
    assert hud.fetch_state(state_server + "/bad", timeout=2) is None


def test_hud_fetch_state_http_error(state_server):
    assert hud.fetch_state(state_server + "/error", timeout=2) is None


def test_hud_split_hex_lines():
    lines = hud.split_hex_lines("00 3f 5c 01 02 03 04 05 06 07 08 09 0a 0b")
    assert len(lines) == 2
    assert lines[0].split()[0] == "00"
    assert lines[1].split()[-1] == "0b"
    assert hud.split_hex_lines("") == ["—", ""]


def test_hud_describe_state_without_server():
    info = hud.describe_state(None)
    assert "нет связи" in info["status_html"]
    assert info["frame"] == "—"
    assert info["error"]


def test_hud_describe_state_with_frame():
    state = {
        "connected": True,
        "name": "LE-S052",
        "address": "41:42:FD:AA:BB:CC",
        "frames_total": 42,
        "frames": [
            {"msg_name": "state", "seq": 10, "payload_hex": "00 3f 5c 01", "truncated": True},
        ],
    }
    info = hud.describe_state(state)
    assert "connected" in info["status_html"]
    assert info["name"] == "LE-S052"
    assert info["frames"] == "42"
    assert "seq 10" in info["frame"]
    assert "truncated" in info["frame"]
    assert info["hex1"] == "00 3f"
    assert info["hex2"] == "5c 01"


def test_hud_describe_state_offline_is_graceful():
    info = hud.describe_state({"connected": False, "last_error": "ble timeout"})
    assert "offline" in info["status_html"]
    assert info["error"] == "ble timeout"
    assert info["frames"] == "0"


def test_hud_help_and_bad_values():
    with pytest.raises(SystemExit) as exc:
        hud.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    assert hud.main(["--values", "1,2,3"]) == 2


def test_hud_without_gtk_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(hud, "_gtk_available", lambda: (False, "No module named 'gi'"))
    assert hud.main([]) == 2
    err = capsys.readouterr().err
    assert "GTK4" in err or "PyGObject" in err
