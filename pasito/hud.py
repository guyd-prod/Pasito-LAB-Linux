"""Pasito Lab HUD: a small GTK4 + gtk4-layer-shell overlay for the BLE dashboard.

It polls ``http://127.0.0.1:8787/api/state`` once a second with urllib (no extra
dependencies) and shows the link status, device name/address, frame counter, the
last frame payload and, optionally, the 10-point DVW curve.

Usage::

    python -m pasito.hud
    python -m pasito.hud --url http://127.0.0.1:8787/api/state --values 20,25,...
    python -m pasito.hud --help

Escape or a click closes the overlay. Without Gtk4LayerShell it falls back to a
plain undecorated Gtk.Window; without GTK4/PyGObject it exits with code 2 and a
human-readable message (no traceback).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8787/api/state"
POLL_INTERVAL_MS = 1000
FETCH_TIMEOUT = 1.5
MARGIN = 24
CARD_WIDTH = 372

__all__ = [
    "DEFAULT_URL",
    "fetch_state",
    "split_hex_lines",
    "format_frame_line",
    "build_parser",
    "main",
]

CSS = """
window.hud {
    background-color: transparent;
}
window.hud label,
window.hud .hud-card {
    font-family: "JetBrainsMono Nerd Font", "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
.hud-card {
    background-color: rgba(9, 11, 16, 0.84);
    border-radius: 18px;
    border: 1px solid rgba(255, 255, 255, 0.09);
}
.hud-kicker {
    font-size: 10px;
    letter-spacing: 2px;
    color: rgba(160, 185, 198, 0.60);
}
.hud-status {
    font-size: 13px;
    font-weight: 700;
}
.hud-name {
    font-size: 18px;
    font-weight: 700;
    color: #f1f5f9;
}
.hud-dim {
    font-size: 11px;
    color: rgba(170, 192, 204, 0.60);
}
.hud-value {
    font-size: 12px;
    color: #e2e8f0;
}
.hud-hex {
    font-size: 11px;
    color: rgba(226, 232, 240, 0.80);
}
.hud-footer {
    font-size: 10px;
    color: rgba(148, 163, 184, 0.55);
}
.hud-chart {
    background-color: rgba(255, 255, 255, 0.035);
    border-radius: 8px;
}
.hud-sep {
    background-color: rgba(255, 255, 255, 0.08);
    min-height: 1px;
}
"""


# ---------------------------------------------------------------------------
# Networking / formatting helpers (testable without GTK)
# ---------------------------------------------------------------------------


def fetch_state(url: str = DEFAULT_URL, timeout: float = FETCH_TIMEOUT) -> dict[str, Any] | None:
    """GET the state endpoint; returns None on any network/JSON error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - localhost only
            raw = response.read(2 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def split_hex_lines(payload_hex: Any) -> list[str]:
    """Split '00 3f 5c ...' into two balanced lines for the HUD."""
    tokens = str(payload_hex or "").replace("0x", " ").split()
    if not tokens:
        return ["—", ""]
    half = (len(tokens) + 1) // 2
    return [" ".join(tokens[:half]), " ".join(tokens[half:])]


def format_frame_line(frame: dict[str, Any]) -> str:
    """One-line frame summary: msg_name · seq · declared/actual bytes."""
    if not isinstance(frame, dict):
        return "—"
    name = str(frame.get("msg_name") or frame.get("msg") or "?")
    seq = frame.get("seq")
    payload = frame.get("payload_hex") or ""
    size = len(str(payload).split())
    line = f"{name} · seq {seq if seq is not None else '—'} · {size} B"
    if frame.get("truncated"):
        line += " · truncated"
    return line


def describe_state(state: dict[str, Any] | None, url: str = DEFAULT_URL) -> dict[str, str]:
    """Pure state -> display strings; used by the GTK labels and easy to test."""
    if not state:
        return {
            "status_html": '<span foreground="#fbbf24">○</span> <span foreground="#fbbf24">нет связи с сервером</span>',
            "name": "Pasito III",
            "address": f"опрос {url}",
            "frames": "—",
            "frame": "—",
            "hex1": "",
            "hex2": "",
            "error": "Сервер не отвечает, повторяю запросы раз в секунду.",
        }

    connected = bool(state.get("connected"))
    if connected:
        status_html = '<span foreground="#5eead4">●</span> <span foreground="#5eead4">connected</span>'
    else:
        status_html = '<span foreground="#93a4b8">○</span> <span foreground="#cbd5e1">offline</span>'

    name = str(state.get("name") or "Pasito III")
    address = str(state.get("address") or "адрес неизвестен")
    if state.get("watch"):
        address += " · watch"

    frames = state.get("frames") if isinstance(state.get("frames"), list) else []
    total = state.get("frames_total")
    if isinstance(total, int) and not isinstance(total, bool):
        frames_text = str(total)
    else:
        frames_text = str(len(frames))

    last = frames[0] if frames and isinstance(frames[0], dict) else None
    lines = split_hex_lines(last.get("payload_hex")) if last else ["—", ""]
    error = str(state.get("last_error") or "").strip()
    return {
        "status_html": status_html,
        "name": name,
        "address": address,
        "frames": frames_text,
        "frame": format_frame_line(last) if last else "—",
        "hex1": lines[0],
        "hex2": lines[1] if len(lines) > 1 else "",
        "error": error,
    }


# ---------------------------------------------------------------------------
# GTK overlay
# ---------------------------------------------------------------------------


class Hud:
    """GTK4 overlay window; only constructed when GTK4 is importable."""

    def __init__(self, app, url: str, values: list[int] | None = None):
        self.app = app
        self.url = url
        self.values = values or []
        self.window = None
        self._stopped = False
        self._gi = None
        self._build()

    # -- UI construction ----------------------------------------------------
    def _build(self) -> None:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gtk

        gi.require_version("Pango", "1.0")
        from gi.repository import Pango

        self._gi = {"Gtk": Gtk, "Gdk": Gdk, "Pango": Pango}
        layer_shell = _load_layer_shell()

        self.window = Gtk.Window(application=self.app)
        self.window.set_title("Pasito Lab HUD")
        self.window.set_size_request(CARD_WIDTH, -1)
        self.window.add_css_class("hud")
        try:
            self.window.set_decorated(False)
        except AttributeError:  # pragma: no cover - future GTK
            pass

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("hud-card")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_top(16)
        content.set_margin_bottom(14)
        content.set_margin_start(18)
        content.set_margin_end(18)
        card.append(content)
        self.window.set_child(card)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_label = Gtk.Label(xalign=0.0)
        self.status_label.add_css_class("hud-status")
        self.status_label.set_hexpand(True)
        self.frames_label = Gtk.Label(xalign=1.0)
        self.frames_label.add_css_class("hud-dim")
        header.append(self.status_label)
        header.append(self.frames_label)
        content.append(header)

        self.name_label = Gtk.Label(xalign=0.0)
        self.name_label.add_css_class("hud-name")
        content.append(self.name_label)

        self.address_label = Gtk.Label(xalign=0.0)
        self.address_label.add_css_class("hud-dim")
        self.address_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.address_label.set_max_width_chars(44)
        content.append(self.address_label)

        self.error_label = Gtk.Label(xalign=0.0)
        self.error_label.add_css_class("hud-dim")
        self.error_label.set_wrap(True)
        self.error_label.set_max_width_chars(44)
        self.error_label.set_visible(False)
        content.append(self.error_label)

        content.append(self._separator())

        kicker = Gtk.Label(label="ПОСЛЕДНИЙ КАДР", xalign=0.0)
        kicker.add_css_class("hud-kicker")
        content.append(kicker)

        self.frame_label = Gtk.Label(xalign=0.0)
        self.frame_label.add_css_class("hud-value")
        self.frame_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.frame_label.set_max_width_chars(46)
        content.append(self.frame_label)

        self.hex1_label = Gtk.Label(xalign=0.0)
        self.hex1_label.add_css_class("hud-hex")
        self.hex1_label.set_wrap(True)
        self.hex1_label.set_max_width_chars(48)
        content.append(self.hex1_label)

        self.hex2_label = Gtk.Label(xalign=0.0)
        self.hex2_label.add_css_class("hud-hex")
        self.hex2_label.set_wrap(True)
        self.hex2_label.set_max_width_chars(48)
        content.append(self.hex2_label)

        if self.values:
            chart = Gtk.DrawingArea()
            chart.add_css_class("hud-chart")
            chart.set_size_request(CARD_WIDTH - 36, 64)
            chart.set_draw_func(self._draw_chart)
            content.append(chart)
            self.chart = chart
        else:
            self.chart = None

        footer = Gtk.Label(label="Esc / клик — выход", xalign=0.0)
        footer.add_css_class("hud-footer")
        content.append(footer)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key)
        self.window.add_controller(key_controller)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.window.add_controller(click)

        self.window.connect("close-request", self._on_close)

        positioned = _setup_layer_shell(self.window, layer_shell)
        if not positioned:
            # Plain undecorated Wayland window as a fallback.
            self.window.set_default_size(CARD_WIDTH, 300)
            if layer_shell is not None and not layer_shell.is_supported():
                print(
                    "hud: gtk4-layer-shell не поддержан композитором, показываю обычное окно без декора",
                    file=sys.stderr,
                )

        from gi.repository import GLib

        self._glib = GLib
        self._apply_state(None)
        self.window.present()
        self._install_signals()
        GLib.timeout_add(POLL_INTERVAL_MS, self._tick)
        GLib.idle_add(self._first_tick)

    def _separator(self):
        Gtk = self._gi["Gtk"]
        sep = Gtk.Box()
        sep.add_css_class("hud-sep")
        sep.set_size_request(-1, 1)
        return sep

    def _install_signals(self) -> None:
        GLib = self._glib
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_sigint)
        except (AttributeError, TypeError):
            pass

    # -- state --------------------------------------------------------------
    def _apply_state(self, state: dict[str, Any] | None) -> None:
        if self.window is None:
            return
        info = describe_state(state, self.url)
        self.status_label.set_markup(info["status_html"])
        self.name_label.set_text(info["name"])
        self.address_label.set_text(info["address"])
        self.frames_label.set_text(f"frames {info['frames']}")
        self.frame_label.set_text(info["frame"])
        self.hex1_label.set_text(info["hex1"])
        self.hex2_label.set_text(info["hex2"])
        if info["error"]:
            self.error_label.set_text(info["error"])
            self.error_label.set_visible(True)
        else:
            self.error_label.set_visible(False)

    def _first_tick(self) -> bool:
        """One-shot initial poll (idle_add would repeat while it returns True)."""
        self._tick()
        return False

    def _tick(self) -> bool:
        if self._stopped or self.window is None:
            return False
        self._apply_state(fetch_state(self.url))
        return True

    # -- drawing ------------------------------------------------------------
    def _draw_chart(self, _area, cr, width: int, height: int) -> None:
        values = self.values
        if not values:
            return
        pad = 8.0
        inner_w = max(1.0, width - 2 * pad)
        inner_h = max(1.0, height - 2 * pad)
        top_value = max(100, max(values))
        points = [
            (pad + inner_w * i / (len(values) - 1), pad + inner_h * (1 - v / top_value))
            for i, v in enumerate(values)
        ]
        # baseline
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.10)
        cr.set_line_width(1.0)
        cr.move_to(pad, pad + inner_h)
        cr.line_to(pad + inner_w, pad + inner_h)
        cr.stroke()
        # glow
        cr.set_source_rgba(0.31, 0.92, 0.84, 0.22)
        cr.set_line_width(6.0)
        cr.set_line_cap(1)  # CAIRO_LINE_CAP_ROUND
        cr.set_line_join(1)  # CAIRO_LINE_JOIN_ROUND
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.stroke()
        # curve
        cr.set_source_rgba(0.31, 0.92, 0.84, 0.95)
        cr.set_line_width(1.8)
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.stroke()
        # knots
        for x, y in points:
            cr.set_source_rgba(0.31, 0.92, 0.84, 1.0)
            cr.arc(x, y, 1.7, 0, 6.28318)
            cr.fill()

    # -- closing ------------------------------------------------------------
    def _on_key(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        Gdk = self._gi["Gdk"]
        if keyval == Gdk.KEY_Escape:
            self._quit()
            return True
        return False

    def _on_click(self, _gesture, _n_press: int, _x: float, _y: float) -> None:
        self._quit()

    def _on_close(self, *_args) -> bool:
        self._stopped = True
        self.app.quit()
        return True

    def _on_sigint(self) -> bool:
        self._quit()
        return False

    def _quit(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.app.quit()


def _load_layer_shell():
    """Import Gtk4LayerShell if the typelib is present; None otherwise."""
    try:
        import gi

        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell

        return Gtk4LayerShell
    except (ImportError, ValueError, OSError):
        return None


def _setup_layer_shell(window, layer_shell) -> bool:
    """Anchor the window to the top-right corner with layer=OVERLAY."""
    if layer_shell is None:
        return False
    try:
        if not layer_shell.is_supported():
            return False
        layer_shell.init_for_window(window)
        layer_shell.set_namespace(window, "pasito-hud")
        layer_shell.set_layer(window, layer_shell.Layer.OVERLAY)
        layer_shell.set_anchor(window, layer_shell.Edge.TOP, True)
        layer_shell.set_anchor(window, layer_shell.Edge.RIGHT, True)
        layer_shell.set_margin(window, layer_shell.Edge.TOP, MARGIN)
        layer_shell.set_margin(window, layer_shell.Edge.RIGHT, MARGIN)
        layer_shell.set_exclusive_zone(window, 0)
        layer_shell.set_keyboard_mode(window, layer_shell.KeyboardMode.ON_DEMAND)
        return True
    except (AttributeError, TypeError, RuntimeError) as exc:
        print(f"hud: не удалось включить layer-shell ({exc}), показываю обычное окно", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pasito.hud",
        description="Оверлей Pasito Lab: статус BLE, последний кадр и мини-кривая DVW поверх всех окон.",
        epilog="Escape или клик — выход. Без сервера оверлей продолжает опрос и показывает «нет связи».",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"адрес состояния (по умолчанию {DEFAULT_URL})")
    parser.add_argument(
        "--values",
        metavar="V1,V2,...",
        default=None,
        help="10 значений DVW (0..255) для мини-кривой, например 20,25,30,40,50,60,70,80,90,100",
    )
    return parser


def _gtk_available() -> tuple[bool, str]:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError, OSError) as exc:
        return False, str(exc)
    return True, ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    values = None
    if args.values:
        try:
            from .wallpaper import parse_values

            values = parse_values(args.values)
        except (ValueError, ImportError) as exc:
            print(f"hud: ошибка в --values: {exc}", file=sys.stderr)
            return 2

    available, reason = _gtk_available()
    if not available:
        print(
            "hud: не найден GTK4/PyGObject. Установите пакеты python-gobject, gtk4 "
            "и gtk4-layer-shell (Arch: sudo pacman -S python-gobject gtk4 gtk4-layer-shell), "
            f"подробность: {reason}",
            file=sys.stderr,
        )
        return 2

    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk

    app = Gtk.Application(
        application_id="io.github.pasitolab.hud",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    holder: dict[str, Hud] = {}

    def on_activate(_app) -> None:
        if "hud" not in holder:
            holder["hud"] = Hud(app, args.url, values)

    app.connect("activate", on_activate)
    try:
        return int(app.run([]))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
