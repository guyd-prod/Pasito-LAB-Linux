"""Wallpaper generator for Pasito Lab: render the DVW curve as a premium dark poster.

Public API (see docs/CONTRACT.md, fx-dev section):

    generate_png(values, out_path, size=(2560, 1440), theme="aurora", meta=None) -> Path
    render_preview(values, theme="aurora", size=(900, 506)) -> bytes
    apply_wallpaper(path) -> (bool, str)

CPU-only, no display required: everything is drawn with Pillow. The wallpapers are
deliberately quiet: a dark gradient, one glowing 10-point DVW line, fine tick labels
and a big Latin title. No random noise, no invented meta values.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import store
from .proto import DVW_POINTS, DVW_STEP_SECONDS, MAX_W, MIN_W

__all__ = [
    "THEMES",
    "THEME_NAMES",
    "generate_png",
    "render_preview",
    "render_image",
    "apply_wallpaper",
    "parse_values",
    "parse_size",
    "parse_meta",
    "validate_values",
    "validate_size",
    "main",
    "build_parser",
]

MIN_SIZE = (320, 200)
MAX_SIZE = (7680, 4320)
DEFAULT_SIZE = (2560, 1440)
PREVIEW_SIZE = (900, 506)

# Dark, premium palettes. Each theme has a vertical/diagonal base gradient, one
# accent for the curve and a secondary tone for the ambient glow.
THEMES: dict[str, dict] = {
    "aurora": {
        "label": "Aurora",
        "top": (4, 9, 23),
        "bottom": (8, 51, 66),
        "accent": (78, 234, 214),
        "secondary": (54, 132, 255),
        "text": (235, 246, 250),
        "muted": (142, 178, 190),
        "glow": True,
        "fill": True,
        "grid": True,
    },
    "ember": {
        "label": "Ember",
        "top": (18, 15, 14),
        "bottom": (82, 33, 13),
        "accent": (255, 143, 64),
        "secondary": (255, 74, 40),
        "text": (255, 241, 231),
        "muted": (198, 156, 136),
        "glow": True,
        "fill": True,
        "grid": True,
    },
    "mono": {
        "label": "Mono",
        "top": (12, 12, 14),
        "bottom": (49, 49, 55),
        "accent": (242, 242, 246),
        "secondary": (154, 154, 166),
        "text": (246, 246, 249),
        "muted": (158, 158, 170),
        "glow": False,
        "fill": True,
        "grid": True,
    },
    "minimal": {
        "label": "Minimal",
        "top": (4, 4, 5),
        "bottom": (11, 11, 13),
        "accent": (228, 228, 234),
        "secondary": (120, 120, 128),
        "text": (224, 224, 230),
        "muted": (118, 118, 128),
        "glow": False,
        "fill": False,
        "grid": False,
    },
}

THEME_NAMES = tuple(THEMES)

_FONT_DIRS = (
    Path.home() / ".local" / "share" / "fonts",
    Path.home() / ".fonts",
    Path("/usr/local/share/fonts"),
    Path("/usr/share/fonts"),
)

_SANS_REGULAR = (
    "dejavusans.ttf",
    "adwaitasans-regular.ttf",
    "liberationsans-regular.ttf",
    "notosans-regular.ttf",
    "jetbrainsmononerdfont-regular.ttf",
)
_SANS_BOLD = (
    "dejavusans-bold.ttf",
    "adwaitasans-bold.ttf",
    "liberationsans-bold.ttf",
    "notosans-bold.ttf",
    "jetbrainsmononerdfont-bold.ttf",
)
_MONO_REGULAR = (
    "dejavusansmono.ttf",
    "jetbrainsmononerdfont-regular.ttf",
    "adwaitamono-regular.ttf",
    "liberationmono-regular.ttf",
    "notosansmono-regular.ttf",
)
_MONO_BOLD = (
    "jetbrainsmononerdfont-bold.ttf",
    "dejavusansmono-bold.ttf",
    "adwaitamono-bold.ttf",
    "liberationmono-bold.ttf",
)


# ---------------------------------------------------------------------------
# Validation helpers (shared with CLI / tests)
# ---------------------------------------------------------------------------


def validate_values(values) -> list[int]:
    """Return the 10 DVW values as ints; anything outside 0..255 is an error."""
    if isinstance(values, (str, bytes)):
        raise ValueError("значения должны быть списком чисел, а не строкой")
    try:
        vals = list(values)
    except TypeError as exc:  # pragma: no cover - defensive
        raise ValueError("значения должны быть списком чисел") from exc
    if len(vals) != DVW_POINTS:
        raise ValueError(f"нужно ровно {DVW_POINTS} значений DVW, получено {len(vals)}")
    clean: list[int] = []
    for v in vals:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"значение {v!r} не является целым числом")
        if not 0 <= v <= 255:
            raise ValueError(f"значение {v} вне диапазона 0..255")
        clean.append(v)
    return clean


def validate_size(size) -> tuple[int, int]:
    """Check wallpaper size bounds (320x200 .. 7680x4320)."""
    try:
        width, height = size
    except (TypeError, ValueError) as exc:
        raise ValueError("размер должен быть парой (ширина, высота)") from exc
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise ValueError("ширина и высота должны быть целыми числами")
    if not MIN_SIZE[0] <= width <= MAX_SIZE[0] or not MIN_SIZE[1] <= height <= MAX_SIZE[1]:
        raise ValueError(
            f"размер {width}x{height} вне допустимого диапазона "
            f"{MIN_SIZE[0]}x{MIN_SIZE[1]}..{MAX_SIZE[0]}x{MAX_SIZE[1]}"
        )
    return width, height


def parse_values(text: str) -> list[int]:
    """'20,25, 30 ...' -> [20, 25, 30, ...] with strict validation."""
    if text is None:
        raise ValueError("не заданы значения DVW")
    parts = [p for p in re.split(r"[\s,;]+", str(text).strip()) if p]
    if not parts:
        raise ValueError("пустой список значений DVW")
    if len(parts) != DVW_POINTS:
        raise ValueError(f"нужно ровно {DVW_POINTS} значений через запятую, получено {len(parts)}")
    values: list[int] = []
    for part in parts:
        try:
            value = int(part, 10)
        except ValueError as exc:
            raise ValueError(f"«{part}» не целое число") from exc
        if not 0 <= value <= 255:
            raise ValueError(f"значение {value} вне диапазона 0..255")
        values.append(value)
    return values


def parse_size(text: str) -> tuple[int, int]:
    """'2560x1440' -> (2560, 1440)."""
    match = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", str(text or ""))
    if not match:
        raise ValueError("размер должен быть в виде ШИРИНАxВЫСОТА, например 2560x1440")
    return validate_size((int(match.group(1)), int(match.group(2))))


def parse_meta(text: str | None) -> dict:
    """Optional meta object; only a JSON object is accepted."""
    if text is None or str(text).strip() == "":
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--meta-json: некорректный JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise ValueError("--meta-json должен быть JSON-объектом, например '{\"battery_pct\": 80}'")
    return data


def _known_theme(name: str) -> dict:
    theme = THEMES.get(str(name))
    if theme is None:
        raise ValueError(f"неизвестная тема «{name}»; доступны: {', '.join(sorted(THEMES))}")
    return theme


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _font_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in _FONT_DIRS:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                lowered = filename.lower()
                if lowered.endswith((".ttf", ".otf")):
                    index.setdefault(lowered, Path(dirpath) / filename)
    return index


def _find_font(candidates: tuple[str, ...]) -> Path | None:
    index = _font_index()
    for name in candidates:
        if name in index:
            return index[name]
    for name in candidates:
        stem = name.rsplit(".", 1)[0]
        for key, path in index.items():
            if stem in key:
                return path
    return None


@lru_cache(maxsize=64)
def _font(size: int, mono: bool = False, bold: bool = False):
    """Font lookup with DejaVu/JetBrains/Adwaita fallbacks and a bitmap fallback."""
    px = max(6, int(round(size)))
    candidates = (_MONO_BOLD if bold else _MONO_REGULAR) if mono else (_SANS_BOLD if bold else _SANS_REGULAR)
    path = _find_font(candidates)
    if path is not None:
        try:
            return ImageFont.truetype(str(path), px)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Small drawing helpers
# ---------------------------------------------------------------------------


def _mix(a, b, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def _rgba(color, alpha: int = 255) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], max(0, min(255, int(alpha))))


def _tracked_text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float = 0.0) -> float:
    """Draw text with letter spacing; returns the end x coordinate."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill, anchor="la")
        x += font.getlength(char) + tracking
    return x


def _dash_line(draw: ImageDraw.ImageDraw, start, end, fill, width: int = 1, dash: int = 8, gap: int = 10) -> None:
    x0, y0 = start
    x1, y1 = end
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        seg_end = min(pos + dash, length)
        draw.line(
            [(x0 + ux * pos, y0 + uy * pos), (x0 + ux * seg_end, y0 + uy * seg_end)],
            fill=fill,
            width=width,
        )
        pos += dash + gap


def _catmull_rom(points: list[tuple[float, float]], samples: int = 28) -> list[tuple[float, float]]:
    """Smooth interpolation through the given points (curve stays put at knots)."""
    if len(points) < 3:
        return list(points)
    extended = [points[0], *points, points[-1]]
    out: list[tuple[float, float]] = [points[0]]
    for i in range(len(points) - 1):
        p0, p1, p2, p3 = extended[i], extended[i + 1], extended[i + 2], extended[i + 3]
        for step in range(1, samples + 1):
            t = step / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    return out


def _meta_pairs(meta) -> list[tuple[str, str]]:
    """Only values explicitly supplied by the caller are ever drawn."""
    if not isinstance(meta, dict):
        return []
    pairs: list[tuple[str, str]] = []
    battery = meta.get("battery_pct")
    if isinstance(battery, (int, float)) and not isinstance(battery, bool) and 0 <= battery <= 100:
        pairs.append(("BATTERY", f"{int(round(battery))}%"))
    puffs = meta.get("puffs")
    if isinstance(puffs, int) and not isinstance(puffs, bool) and puffs >= 0:
        pairs.append(("PUFFS", str(puffs)))
    mode = meta.get("mode")
    if isinstance(mode, (str, int)) and not isinstance(mode, bool) and str(mode).strip():
        pairs.append(("MODE", str(mode).strip()[:24]))
    device = meta.get("device")
    if isinstance(device, str) and device.strip():
        pairs.append(("DEVICE", device.strip()[:24]))
    return pairs


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _background(size: tuple[int, int], theme: dict) -> Image.Image:
    """Smooth diagonal gradient + soft ambient glow, computed on a tiny canvas."""
    width, height = size
    small_w = 192
    small_h = max(2, int(round(small_w * height / width)))
    top, bottom = theme["top"], theme["bottom"]
    pixels = []
    denom_x = max(1, small_w - 1)
    denom_y = max(1, small_h - 1)
    for y in range(small_h):
        for x in range(small_w):
            t = 0.70 * (y / denom_y) + 0.30 * (x / denom_x)
            pixels.append(_mix(top, bottom, t))
    base = Image.new("RGB", (small_w, small_h))
    base.putdata(pixels)
    base = base.resize(size, Image.Resampling.BICUBIC)

    if not theme.get("glow"):
        return base

    glow = Image.new("RGBA", (small_w, small_h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse(
        [small_w * 0.40, -small_h * 0.45, small_w * 1.20, small_h * 0.95],
        fill=_rgba(theme["secondary"], 46),
    )
    gdraw.ellipse(
        [-small_w * 0.35, small_h * 0.55, small_w * 0.75, small_h * 1.60],
        fill=_rgba(theme["accent"], 20),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(9))
    glow = glow.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(max(6, width // 70)))
    return Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")


def _draw_meta(draw: ImageDraw.ImageDraw, meta_pairs, width: int, pad: int, scale: float, theme: dict) -> None:
    if not meta_pairs:
        return
    label_font = _font(max(9, int(11 * scale)), mono=True)
    value_font = _font(max(9, int(11 * scale)), mono=True, bold=True)
    right = width - pad
    y = pad
    label_color = _rgba(theme["muted"], 200)
    value_color = _rgba(theme["text"], 220)
    for label, value in meta_pairs:
        label_text = f"{label}  "
        label_w = label_font.getlength(label_text)
        value_w = value_font.getlength(value)
        x = right - label_w - value_w
        draw.text((x, y), label_text, font=label_font, fill=label_color, anchor="la")
        draw.text((x + label_w, y), value, font=value_font, fill=value_color, anchor="la")
        y += max(16, int(20 * scale))


def render_image(
    values,
    theme: str = "aurora",
    size: tuple[int, int] = DEFAULT_SIZE,
    meta: dict | None = None,
) -> Image.Image:
    """Render a wallpaper as a PIL image (RGB)."""
    palette = _known_theme(theme)
    vals = validate_values(values)
    width, height = validate_size(size)
    scale = max(0.42, min(width / 2560, height / 1440))

    image = _background((width, height), palette)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    text = _rgba(palette["text"])
    muted = _rgba(palette["muted"], 200)
    accent = _rgba(palette["accent"])
    minimal = theme == "minimal"

    # Keep the composition readable down to the 320x200 minimum.
    compact = width < 700 or height < 380
    pad = max(12, int(width * 0.045))
    chart_left = max(pad, 34) if not minimal and (compact or palette.get("grid")) else pad
    chart_right = width - pad
    if minimal:
        chart_top = height * 0.40
        axis_y = height * 0.80
    elif compact:
        chart_top = height * 0.44
        axis_y = height * 0.74
    else:
        chart_top = height * 0.36
        axis_y = height * 0.82
    chart_bottom = axis_y
    top_value = max(MAX_W, max(vals))
    chart_h = max(40.0, chart_bottom - chart_top)
    chart_w = max(60.0, chart_right - chart_left)

    def x_at(index: int) -> float:
        return chart_left + chart_w * index / (DVW_POINTS - 1)

    def y_at(value: float) -> float:
        return chart_bottom - chart_h * (value / top_value)

    points = [(x_at(i), y_at(v)) for i, v in enumerate(vals)]

    # --- grid, ticks, labels -------------------------------------------------
    if palette.get("grid"):
        grid_color = _rgba(palette["muted"], 34)
        axis_color = _rgba(palette["muted"], 90)
        tick_font = _font(max(8, int(10 * scale)), mono=True)
        tick_color = _rgba(palette["muted"], 190)
        if top_value <= MAX_W:
            y_ticks = [0, 25, 50, 75, 100]
        else:
            y_ticks = [0, int(round(top_value / 2)), int(round(top_value))]
        for tick in y_ticks:
            y = y_at(tick)
            if 0 <= y <= height:
                _dash_line(draw, (chart_left, y), (chart_right, y), grid_color, width=1, dash=6, gap=12)
                draw.text((chart_left - 10, y), str(tick), font=tick_font, fill=tick_color, anchor="rm")
        draw.text((chart_left, chart_top - max(10, int(16 * scale))), "POWER, W", font=tick_font,
                  fill=tick_color, anchor="ls")
        # x axis + per-step ticks and labels (0.0 .. 4.5 s, 0.5 s per point)
        draw.line([(chart_left, axis_y), (chart_right, axis_y)], fill=axis_color, width=1)
        for i in range(DVW_POINTS):
            x = x_at(i)
            draw.line([(x, axis_y), (x, axis_y + max(4, int(7 * scale)))], fill=axis_color, width=1)
            label = f"{i * DVW_STEP_SECONDS:.1f}"
            draw.text((x, axis_y + max(8, int(12 * scale))), label, font=tick_font, fill=tick_color, anchor="ma")

    # --- area fill + glow ----------------------------------------------------
    smooth = _catmull_rom(points, samples=30)
    if palette.get("fill"):
        area = [(chart_left, chart_bottom), *smooth, (chart_right, chart_bottom)]
        draw.polygon(area, fill=_rgba(palette["accent"], 22))

    if palette.get("glow"):
        glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        gdraw.line(smooth, fill=_rgba(palette["accent"], 190), width=max(6, int(9 * scale)), joint="curve")
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(max(4, int(9 * scale))))
        overlay = Image.alpha_composite(overlay, glow_layer)
        draw = ImageDraw.Draw(overlay)

    line_width = max(1, int(round(2.0 * scale))) if minimal else max(2, int(round(3.2 * scale)))
    draw.line(smooth, fill=accent, width=line_width, joint="curve")

    if not minimal:
        for i, (x, y) in enumerate(points):
            radius = max(2.0, 4.2 * scale)
            draw.ellipse(
                [x - radius * 2.4, y - radius * 2.4, x + radius * 2.4, y + radius * 2.4],
                fill=_rgba(palette["accent"], 28),
            )
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=accent)
            draw.ellipse(
                [x - radius * 0.45, y - radius * 0.45, x + radius * 0.45, y + radius * 0.45],
                fill=_rgba((255, 255, 255), 235),
            )
            if scale >= 0.7:
                draw.text(
                    (x, y - radius * 3.2), str(vals[i]), font=_font(max(8, int(10 * scale)), mono=True),
                    fill=muted, anchor="mb",
                )

    # --- header --------------------------------------------------------------
    kicker_font = _font(max(9, int(12 * scale)), mono=True)
    title_font = _font(max(18, int(78 * scale)), bold=True)
    _tracked_text(
        draw,
        (pad, height * 0.075),
        "PASITO LAB · DVW PROFILE",
        kicker_font,
        _rgba(palette["muted"], 210),
        tracking=max(2.0, 3.0 * scale),
    )
    title_y = height * 0.105
    draw.text((pad, title_y), "PASITO III", font=title_font, fill=text, anchor="la")
    title_box = title_font.getbbox("PASITO III")
    underline_y = title_y + (title_box[3] - title_box[1]) + max(10, int(16 * scale))
    draw.line(
        [(pad, underline_y), (pad + max(60, int(width * 0.075)), underline_y)],
        fill=accent,
        width=max(2, int(round(3 * scale))),
    )
    if not compact:
        subtitle_font = _font(max(9, int(14 * scale)), mono=True)
        subtitle = (
            f"{palette['label'].upper()} · {len(vals)} POINTS · "
            f"{DVW_STEP_SECONDS:.1f} S STEP · {MIN_W}–{MAX_W} W RANGE"
        )
        draw.text(
            (pad, underline_y + max(10, int(16 * scale))), subtitle, font=subtitle_font, fill=muted, anchor="la"
        )

    _draw_meta(draw, _meta_pairs(meta), width, pad, scale, palette)

    # --- footer --------------------------------------------------------------
    footer_y = height - pad * 0.55
    rule_y = height - pad * 1.45
    draw.line([(pad, rule_y), (width - pad, rule_y)], fill=_rgba(palette["muted"], 42), width=1)
    signature_font = _font(max(9, int(13 * scale)), mono=True)
    draw.text((pad, footer_y), "PASITO III · DVW", font=signature_font, fill=muted, anchor="ls")
    draw.text(
        (width - pad, footer_y),
        "PASITO LAB",
        font=signature_font,
        fill=_rgba(palette["muted"], 150),
        anchor="rs",
    )

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def generate_png(
    values,
    out_path,
    size: tuple[int, int] = DEFAULT_SIZE,
    theme: str = "aurora",
    meta: dict | None = None,
) -> Path:
    """Render the wallpaper and write it as PNG; returns the absolute-ish Path."""
    path = Path(out_path).expanduser()
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    image = render_image(values, theme=theme, size=size, meta=meta)
    image.save(path, format="PNG")
    return path


def render_preview(
    values,
    theme: str = "aurora",
    size: tuple[int, int] = PREVIEW_SIZE,
    meta: dict | None = None,
) -> bytes:
    """PNG bytes for the HTTP dashboard preview (default 900x506)."""
    image = render_image(values, theme=theme, size=size, meta=meta)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Applying the wallpaper (Wayland/Omarchy)
# ---------------------------------------------------------------------------


def apply_wallpaper(path) -> tuple[bool, str]:
    """Set the wallpaper. Omarchy first, swww as fallback; never hyprpaper blindly."""
    wallpaper = Path(path).expanduser()
    if not wallpaper.is_file():
        return False, f"файл обоев не найден: {wallpaper}"
    # Compositors resolve image paths against their own cwd, so pass an absolute one.
    absolute = wallpaper.resolve()

    candidates = (
        ("omarchy", ("theme", "bg", "set", str(absolute)), "omarchy theme bg set"),
        ("swww", ("img", str(absolute)), "swww img"),
    )
    reasons: list[str] = []
    for binary, arguments, readable in candidates:
        executable = shutil.which(binary)
        if not executable:
            reasons.append(f"{binary}: не установлен")
            continue
        try:
            result = subprocess.run(
                [executable, *arguments],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            reasons.append(f"{readable}: таймаут 15 с")
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            reasons.append(f"{readable}: {exc}")
            continue
        if result.returncode == 0:
            return True, f"обои применены: {readable}"
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f" — {detail[-1]}" if detail else ""
        reasons.append(f"{readable}: код {result.returncode}{suffix}")

    hint = "не удалось применить обои. " + "; ".join(reasons) + "."
    if not shutil.which("swww"):
        hint += " Установите swww (yay -S swww) или задайте вручную: omarchy theme bg set <path>."
    return False, hint


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pasito.wallpaper",
        description="Генератор обоев Pasito Lab: DVW-кривая (10 точек) как премиальный тёмный постер.",
        epilog="Пример: python -m pasito.wallpaper --values 20,25,30,40,50,60,70,80,90,100 "
        "--out ~/wall.png --theme aurora --apply",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--values",
        metavar="V1,V2,...",
        help=f"ровно {DVW_POINTS} значений DVW (0..255), через запятую",
    )
    source.add_argument("--profile", metavar="NAME|ID", help="взять значения из профиля store по имени или id")
    parser.add_argument("--out", metavar="PATH", required=True, help="куда сохранить PNG")
    parser.add_argument(
        "--theme",
        choices=sorted(THEMES),
        default="aurora",
        help="тема оформления (по умолчанию aurora)",
    )
    parser.add_argument("--size", metavar="WxH", default="2560x1440", help="размер, по умолчанию 2560x1440")
    parser.add_argument("--apply", action="store_true", help="сразу применить обои (omarchy/swww)")
    parser.add_argument(
        "--meta-json",
        metavar="JSON",
        default=None,
        help='опциональные данные: {"battery_pct":80,"puffs":120,"mode":"POW","device":"LE-S052"}',
    )
    return parser


def _profile_values(query: str) -> list[int]:
    profiles = store.load_profiles()
    needle = str(query).strip()
    match = next((p for p in profiles if p.get("id") == needle), None)
    if match is None:
        loose = needle.casefold()
        match = next((p for p in profiles if str(p.get("name", "")).casefold() == loose), None)
    if match is None:
        names = ", ".join(f"{p.get('name', '?')} ({p.get('id', '?')})" for p in profiles) or "профилей нет"
        raise ValueError(f"профиль «{query}» не найден. Доступно: {names}")
    return validate_values(match.get("values"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        values = parse_values(args.values) if args.values is not None else _profile_values(args.profile)
        size = parse_size(args.size)
        meta = parse_meta(args.meta_json)
    except ValueError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1

    try:
        output = generate_png(values, args.out, size=size, theme=args.theme, meta=meta)
    except (OSError, ValueError) as exc:
        print(f"ошибка: не удалось сохранить обои: {exc}", file=sys.stderr)
        return 1

    print(f"обои сохранены: {output} ({size[0]}x{size[1]}, {args.theme})")
    if args.apply:
        applied, detail = apply_wallpaper(output)
        print(("обои применены: " if applied else "обои не применены: ") + detail, file=sys.stdout if applied else sys.stderr)
        if not applied:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
