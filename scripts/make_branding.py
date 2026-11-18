#!/usr/bin/env python3
"""Generate Pasito Lab branding assets (banner, logo) with Pillow.

Usage:
    python3 scripts/make_branding.py [--out assets/brand]

Outputs:
    assets/brand/banner.png   1600x640 hero banner for the README
    assets/brand/logo.png     512x512 mark (diamond + DVW curve)
    assets/brand/social.png   1280x640 GitHub social preview
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BG_TOP = (6, 10, 15)
BG_BOTTOM = (10, 18, 26)
TEAL = (45, 212, 191)
CYAN = (34, 211, 238)
BLUE = (96, 165, 250)
TEXT = (230, 237, 245)
DIM = (139, 151, 168)

FONT_CANDIDATES = {
    "sans_bold": (
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    ),
    "sans": (
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    ),
    "mono": (
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",
    ),
    "mono_bold": (
        "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    ),
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES[kind]:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def rounded_pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, color: tuple[int, int, int]) -> int:
    typeface = font("mono", size)
    box = draw.textbbox((0, 0), text, font=typeface)
    width = box[2] - box[0] + size * 2
    height = size * 2
    x, y = xy
    draw.rounded_rectangle([x, y, x + width, y + height], radius=height // 2, outline=color, width=2)
    draw.text((x + size, y + size // 2 - 1), text, font=typeface, fill=color)
    return width


def draw_curve(draw: ImageDraw.ImageDraw, points, color: tuple[int, int, int], width: int = 4) -> None:
    layer_points = [(x, y) for x, y in points]
    draw.line(layer_points, fill=color, width=width, joint="curve")
    for x, y in layer_points:
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=BG_TOP, outline=color, width=3)


def make_banner(out: Path) -> None:
    width, height = 1600, 640
    image = gradient((width, height))
    draw = ImageDraw.Draw(image)

    # soft glow behind the title
    halo = Image.new("RGB", (width, height), (0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    halo_draw.ellipse([-260, -220, 760, 520], fill=(10, 60, 60))
    halo = halo.filter(ImageFilter.GaussianBlur(160))
    image = Image.blend(image, Image.blend(image, halo, 0.55), 1.0)
    draw = ImageDraw.Draw(image)

    # grid lines
    for x in range(0, width, 64):
        draw.line([(x, 0), (x, height)], fill=(18, 26, 35), width=1)
    for y in range(0, height, 64):
        draw.line([(0, y), (width, y)], fill=(18, 26, 35), width=1)

    # brand title
    draw.text((80, 88), "PASITO", font=font("sans_bold", 126), fill=TEXT)
    lab = font("sans_bold", 126)
    pasito_width = draw.textlength("PASITO ", font=lab)
    draw.text((80 + pasito_width, 88), "LAB", font=lab, fill=TEAL)

    draw.text((84, 258), "SMOANT PASITO III  ·  BLE WORKBENCH", font=font("mono_bold", 22), fill=DIM)

    x = 84
    x += rounded_pill(draw, (x, 324), "PROTOCOL RE", 18, TEAL) + 14
    x += rounded_pill(draw, (x, 324), "LIVE TELEMETRY", 18, CYAN) + 14
    rounded_pill(draw, (x, 324), "DVW EDITOR", 18, BLUE)

    draw.text((84, 432), "Linux · BlueZ · bleak · FastAPI · no root", font=font("mono", 20), fill=DIM)
    draw.text((84, 480), "github.com/guyd-prod/Pasito-LAB-Linux", font=font("mono", 20), fill=(120, 200, 190))

    # curve panel on the right
    panel = (980, 120, 1510, 520)
    draw.rounded_rectangle(panel, radius=22, fill=(9, 14, 20), outline=(28, 38, 50), width=2)
    draw.text((1012, 148), "DVW · 10 ШАГОВ · 0.5 С", font=font("mono", 16), fill=DIM)
    values = [20, 26, 34, 44, 56, 68, 78, 88, 96, 100]
    left, right = 1024, 1470
    top, bottom = 196, 470
    points = [
        (left + (index / 9) * (right - left), bottom - (value / 110) * (bottom - top))
        for index, value in enumerate(values)
    ]
    draw_curve(draw, points, TEAL, width=4)
    for watts in (25, 50, 75, 100):
        y = bottom - (watts / 110) * (bottom - top)
        draw.line([(left, y), (right, y)], fill=(24, 33, 44), width=1)
        draw.text((left + 4, y - 20), f"{watts}", font=font("mono", 13), fill=(70, 84, 100))

    image.save(out)


def make_logo(out: Path) -> None:
    size = 512
    image = gradient((size, size))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=96, outline=(30, 40, 52), width=6)

    # diamond outline, like the dashboard brand mark
    center = size // 2
    radius = 150
    diamond = [
        (center, center - radius),
        (center + radius, center),
        (center, center + radius),
        (center - radius, center),
    ]
    draw.line(diamond + [diamond[0]], fill=TEAL, width=18, joint="curve")

    # dvw curve inside
    values = [30, 36, 45, 55, 66, 76, 86, 93, 97, 100]
    left, right = center - 92, center + 92
    bottom, top = center + 74, center - 74
    points = [
        (left + (index / 9) * (right - left), bottom - (value / 110) * (bottom - top))
        for index, value in enumerate(values)
    ]
    draw.line(points, fill=CYAN, width=12, joint="curve")
    draw.ellipse([center - 12, center - 12, center + 12, center + 12], fill=(10, 16, 22), outline=TEAL, width=8)
    image.save(out)


def make_social(out: Path, banner: Path) -> None:
    with Image.open(banner) as source:
        image = source.convert("RGB").resize((1280, 512), Image.LANCZOS)
    canvas = Image.new("RGB", (1280, 640), BG_TOP)
    canvas.paste(image, (0, 64))
    canvas.save(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Pasito Lab branding assets")
    parser.add_argument("--out", type=Path, default=Path("assets/brand"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    banner = args.out / "banner.png"
    make_banner(banner)
    make_logo(args.out / "logo.png")
    make_social(args.out / "social.png", banner)
    for name in ("banner.png", "logo.png", "social.png"):
        path = args.out / name
        print(f"{path}  {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
