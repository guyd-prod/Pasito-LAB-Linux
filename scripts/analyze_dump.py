#!/usr/bin/env python3
"""Analyze a dumped SPI-NOR image for display resources (wallpapers/themes).

Usage:
    python3 scripts/analyze_dump.py dump.bin [--json]

Finds JPEG/PNG/BMP/GIF candidates, reports offsets/sizes, prints interesting
strings and a size histogram — enough to tell whether wallpapers are plain
images in a container or a filesystem.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
BMP_SIG = b"BM"
GIF_SIG = b"GIF8"
# Bluetrum resource blob: magic C5 CE D4 D2, 32-byte records: name[24] + addr u32 + len u32,
# assets mapped XIP from RES_BASE (see atc1441/Bluetrum_AB5682_Hacking).
ENTR_MAGIC = b"\xc5\xce\xd4\xd2"
RES_BASE = 0x11000000
KNOWN_SIZES = {
    "240x240 RGB565": 240 * 240 * 2,
    "360x360 RGB565": 360 * 360 * 2,
    "240x320 RGB565": 240 * 320 * 2,
    "320x240 RGB565": 320 * 240 * 2,
    "480x480 RGB565": 480 * 480 * 2,
    "240x240 RGB888": 240 * 240 * 3,
    "360x360 RGB888": 360 * 360 * 3,
}


def find_all(data: bytes, needle: bytes, limit: int = 500) -> list[int]:
    hits = []
    start = 0
    while len(hits) < limit:
        index = data.find(needle, start)
        if index < 0:
            break
        hits.append(index)
        start = index + 1
    return hits


def carve_jpegs(data: bytes) -> list[dict]:
    images = []
    for offset in find_all(data, JPEG_SOI):
        end = data.find(JPEG_EOI, offset + 3)
        if end < 0:
            continue
        size = end + 2 - offset
        if 256 <= size <= 4 * 1024 * 1024:
            images.append({"format": "jpeg", "offset": offset, "size": size})
    return images


def carve_pngs(data: bytes) -> list[dict]:
    images = []
    for offset in find_all(data, PNG_SIG):
        end = data.find(b"IEND", offset)
        if end < 0:
            continue
        size = end + 8 - offset
        if 64 <= size <= 4 * 1024 * 1024:
            images.append({"format": "png", "offset": offset, "size": size})
    return images


def carve_bmps(data: bytes) -> list[dict]:
    images = []
    for offset in find_all(data, BMP_SIG):
        if offset + 6 > len(data):
            continue
        size = int.from_bytes(data[offset + 2 : offset + 6], "little")
        if 64 <= size <= 4 * 1024 * 1024 and offset + size <= len(data):
            images.append({"format": "bmp", "offset": offset, "size": size})
    return images


def carve_gifs(data: bytes) -> list[dict]:
    return [
        {"format": "gif", "offset": offset, "size": None}
        for offset in find_all(data, GIF_SIG)
    ]


def interesting_strings(data: bytes, min_len: int = 6, limit: int = 40) -> list[str]:
    keywords = (
        "wallpaper", "theme", "image", "logo", "boot", "anim", "font",
        "version", "firmware", "smoant", "pasito", "s052", "dvw", "ui", "lcd",
        "spi", "flash", "lfs", "littlefs", "fat", "crc",
    )
    strings = []
    current = bytearray()
    for byte in data:
        if 32 <= byte <= 126:
            current.append(byte)
            continue
        if len(current) >= min_len:
            text = bytes(current).decode("ascii", "ignore")
            lowered = text.lower()
            if any(keyword in lowered for keyword in keywords):
                strings.append(text)
        current.clear()
        if len(strings) >= limit:
            break
    return strings


def parse_entr(data: bytes, limit: int = 4096) -> list[dict]:
    """Parse Bluetrum ENTR resource tables (name/address/length records)."""
    entries: list[dict] = []
    for magic_offset in find_all(data, ENTR_MAGIC, limit=8):
        start = magic_offset + 4
        for index in range(0, 8192, 32):
            record = data[start + index : start + index + 32]
            if len(record) < 32:
                break
            raw_name = record[:24].split(b"\x00")[0]
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError:
                break
            if not name or not all(32 <= ord(char) < 127 for char in name):
                break
            address = int.from_bytes(record[24:28], "little")
            length = int.from_bytes(record[28:32], "little")
            if not (0x10000000 <= address <= 0x30000000) or not (0 < length <= 16 * 1024 * 1024):
                break
            file_offset = address - RES_BASE
            entries.append(
                {
                    "table_offset": magic_offset,
                    "name": name,
                    "address": address,
                    "length": length,
                    "file_offset": file_offset if 0 <= file_offset < len(data) else None,
                }
            )
        if entries:
            break
    return entries


def size_histogram(images: list[dict]) -> dict:
    histogram = collections.Counter(image["size"] for image in images if image["size"])
    return {str(size): count for size, count in sorted(histogram.items(), key=lambda kv: -kv[1])[:12]}


def guess_rgb565_blocks(data: bytes) -> dict:
    """Look for runs of plausible RGB565 frames matching known panel sizes."""
    guesses = {}
    for label, size in KNOWN_SIZES.items():
        # Cheap heuristic: repeated uint16 values with high entropy suggest a frame.
        window = data[: min(len(data), 65536)]
        if len(window) < 2:
            continue
        words = memoryview(window)[: len(window) // 2 * 2].cast("H")
        unique = len(set(words[: min(len(words), 8192)]))
        if unique > 64:
            guesses[label] = unique
    return guesses


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a Pasito flash dump for display resources")
    parser.add_argument("dump", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.dump.is_file():
        print(f"файл не найден: {args.dump}", file=sys.stderr)
        return 1
    data = args.dump.read_bytes()
    images = carve_jpegs(data) + carve_pngs(data) + carve_bmps(data) + carve_gifs(data)
    images.sort(key=lambda image: image["offset"])
    report = {
        "file": str(args.dump),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "images": images[:200],
        "images_total": len(images),
        "formats": dict(collections.Counter(image["format"] for image in images)),
        "size_histogram": size_histogram(images),
        "strings": interesting_strings(data),
        "entr_entries": parse_entr(data),
        "rgb565_hint": guess_rgb565_blocks(data),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"файл: {report['file']}  ({report['size']} байт)")
    print(f"sha256: {report['sha256']}")
    print(f"изображений найдено: {report['images_total']}  по форматам: {report['formats']}")
    for image in report["images"][:20]:
        print(f"  {image['format']:<5} offset=0x{image['offset']:08x}  size={image['size']}")
    if report["size_histogram"]:
        print("частые размеры:", report["size_histogram"])
    if report["entr_entries"]:
        print(f"таблица ресурсов ENTR: {len(report['entr_entries'])} записей")
        for entry in report["entr_entries"][:30]:
            print(
                f"  {entry['name']:<24} addr=0x{entry['address']:08x} "
                f"len={entry['length']:<8} file_off={entry['file_offset']}"
            )
    else:
        print("таблица ENTR не найдена (другая раскладка/файловая система/сжатие)")
    if report["strings"]:
        print("строки по теме:")
        for text in report["strings"][:20]:
            print("  ", text)
    else:
        print("строк по теме не найдено (возможен сжатый/бинарный контейнер)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
