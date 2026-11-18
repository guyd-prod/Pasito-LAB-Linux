"""Pasito Lab Linux CLI: scan / watch / capture / raw ops / DVW / profiles / serve."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from . import __version__, proto, store
from .ble import PasitoClient, PasitoError, scan


def parse_values(text: str) -> list[int]:
    try:
        values = [int(part) for part in text.replace(";", ",").split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"не числа: {text}") from exc
    if len(values) != proto.DVW_POINTS:
        raise argparse.ArgumentTypeError(f"нужно ровно {proto.DVW_POINTS} значений, получено {len(values)}")
    for value in values:
        if not 0 <= value <= 255:
            raise argparse.ArgumentTypeError(f"{value} не влезает в один байт (0..255)")
    return values


def frame_line(frame) -> str:
    mark = " !trunc" if frame.truncated else ""
    return (
        f"{time.strftime('%H:%M:%S')}.{int((frame.t % 1) * 1000):03d}  "
        f"seq={frame.seq:3d}  msg=0x{frame.msg:04x} {frame.msg_name:<5}  "
        f"len={frame.declared_len}/{len(frame.payload)}{mark}  {proto.hx(frame.payload)}"
    )


def print_frame(frame) -> None:
    print(frame_line(frame), flush=True)


async def _connect(args) -> PasitoClient:
    client = PasitoClient(address=getattr(args, "address", None))
    await client.connect()
    return client


async def cmd_scan(args) -> int:
    findings = await scan(args.timeout, include_all=args.all)
    if args.json:
        print(json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2))
        return 0
    if not findings:
        print("Ничего не найдено. Pasito гасит BLE, когда экран погас — разбудите устройство.")
        print("Другие BLE-устройства: --all")
        return 1
    for f in findings:
        tag = f.reason or "другое"
        rssi = f"{f.rssi} dBm" if f.rssi is not None else "—"
        print(f"{f.address}  {f.name[:24]:<24}  {rssi:>9}  {tag}")
        if f.manufacturer:
            print(f"    manufacturer: {proto.hx(f.manufacturer)}")
        for key, value in f.service_data.items():
            print(f"    service data {key}: {proto.hx(value)}")
    return 0


async def cmd_watch(args) -> int:
    client = PasitoClient(address=args.address, on_frame=print_frame)
    print("Слежение: Ctrl+C — выход. Устройство рвёт сессию само, клиент переподключится.")
    try:
        while True:
            try:
                if not client.connected:
                    await client.connect()
                    await client.subscribe()
                    print(f"— подключено к {client.address} —", flush=True)
                await asyncio.sleep(0.4)
            except PasitoError as exc:
                print(f"! {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(2.0)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"! {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(2.0)
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        await client.disconnect()
    return 0


async def cmd_capture(args) -> int:
    writer = store.CaptureWriter(args.out) if args.out else store.CaptureWriter()
    client = PasitoClient(address=args.address, on_frame=lambda f: (writer.frame(f), print_frame(f)))
    deadline = time.monotonic() + args.seconds
    print(f"Запись в {writer.path} ({args.seconds:.0f} c)")
    try:
        while time.monotonic() < deadline:
            try:
                if not client.connected:
                    await client.connect()
                    await client.subscribe()
                    writer.event("connected", address=client.address)
                await asyncio.sleep(0.4)
            except PasitoError as exc:
                writer.event("error", message=str(exc))
                print(f"! {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(2.0)
    except KeyboardInterrupt:
        pass
    finally:
        await client.disconnect()
        writer.event("capture_stopped", seconds=args.seconds)
        writer.close()
    print(f"Готово: {writer.path}")
    return 0


async def cmd_read_control(args) -> int:
    client = await _connect(args)
    try:
        data = await client.read_control()
    finally:
        await client.disconnect()
    if args.hex:
        print(proto.hx(data))
    else:
        print(f"0004: {len(data)} байт")
        print(proto.hexdump(data))
    return 0


async def cmd_write(args) -> int:
    data = proto.unhex(" ".join(args.hex))
    client = await _connect(args)
    try:
        if args.rx:
            await client.write_rx(data)
            print(f"0002 {len(data)} байт отправлено (without response): {proto.hx(data)}")
        else:
            await client.write_control(data, response=not args.no_response)
            print(f"0004 {len(data)} байт отправлено: {proto.hx(data)}")
            if not args.no_response and args.read_back:
                await asyncio.sleep(0.8)
                try:
                    back = await client.read_control()
                    print(f"чтение обратно: {proto.hx(back)}")
                except Exception as exc:
                    print(f"чтение обратно не удалось: {exc}")
    finally:
        await client.disconnect()
    return 0


async def cmd_dvw_send(args) -> int:
    if args.profile:
        profile = store.get_profile(args.profile) or next(
            (p for p in store.load_profiles() if p["name"].lower() == args.profile.lower()), None
        )
        if not profile:
            print(f"профиль «{args.profile}» не найден", file=sys.stderr)
            return 2
        values = profile["values"]
    else:
        values = args.values
    if not all(proto.MIN_W <= v <= proto.MAX_W for v in values) and not args.allow_out_of_range:
        print(
            f"предупреждение: значения вне типичного диапазона {proto.MIN_W}..{proto.MAX_W} Вт "
            "(кодирование допускает 0..255); повтор с --allow-out-of-range",
            file=sys.stderr,
        )
        return 2
    client = PasitoClient(address=args.address)
    try:
        result = await client.send_dvw(values, handshake=args.handshake)
    finally:
        await client.disconnect()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"кадр: {result['frame_hex']}")
        print(f"ATT-подтверждение записи: {'да' if result['att_ack'] else 'нет'}")
        for step in result["steps"]:
            print(f"  {step}")
        if result.get("read_hex"):
            print(f"обратное чтение 0004: {result['read_hex']}")
        print("ВАЖНО: ATT-ack ≠ применение на устройстве. Смотрите на экран Pasito.")
    return 0 if result.get("att_ack") else 1


def cmd_profile(args) -> int:
    if args.profile_cmd == "list":
        profiles = store.load_profiles()
        if not profiles:
            print("профилей нет")
            return 0
        for p in profiles:
            print(f"{p['id']}  {p['name']:<24}  {','.join(str(v) for v in p['values'])}")
        return 0
    if args.profile_cmd == "add":
        profile = store.add_profile(args.name, args.values, args.note or "")
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    if args.profile_cmd == "rm":
        print("удалён" if store.delete_profile(args.id) else "не найден")
        return 0
    if args.profile_cmd == "show":
        profile = store.get_profile(args.id)
        if not profile:
            print("не найден", file=sys.stderr)
            return 1
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    return 2


def cmd_wallpaper(args) -> int:
    try:
        from . import wallpaper
    except ImportError as exc:
        print(f"модуль обоев недоступен: {exc}", file=sys.stderr)
        return 2
    values = args.values
    if args.profile:
        profile = store.get_profile(args.profile) or next(
            (p for p in store.load_profiles() if p["name"].lower() == args.profile.lower()), None
        )
        if not profile:
            print("профиль не найден", file=sys.stderr)
            return 2
        values = profile["values"]
    if values is None:
        values = store.DEFAULT_VALUES
    store.ensure_dirs()
    out = args.out or str(store.WALLPAPERS_DIR / f"pasito-{args.theme}.png")
    path = wallpaper.generate_png(values, out, theme=args.theme)
    print(f"обои: {path}")
    if args.apply:
        ok, detail = wallpaper.apply_wallpaper(path)
        print(f"применено: {'да' if ok else 'нет'} {detail}".rstrip())
    return 0


def cmd_serve(args) -> int:
    if getattr(args, "demo", False):
        os.environ["PASITO_DEMO"] = "1"
    import uvicorn

    from .server import app

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _python_with_gi() -> str | None:
    """HUD needs PyGObject, which usually lives in the system interpreter, not in the venv."""
    import shutil
    import subprocess

    if not shutil.which("python3"):
        return None
    for candidate in ("/usr/bin/python3", shutil.which("python3")):
        if not candidate:
            continue
        try:
            check = subprocess.run([candidate, "-c", "import gi"], capture_output=True, timeout=10)
            if check.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def cmd_hud(args) -> int:
    python = _python_with_gi()
    if python is None:
        print(
            "для оверлея нужен PyGObject: sudo pacman -S python-gobject gtk4 gtk4-layer-shell",
            file=sys.stderr,
        )
        return 2
    env = dict(os.environ)
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = project + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    argv = [python, "-m", "pasito.hud"]
    if args.url:
        argv += ["--url", args.url]
    os.execve(python, argv, env)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pasito", description="Pasito Lab Linux — BLE-инструменты для Smoant Pasito III")
    parser.add_argument("--version", action="version", version=f"pasito {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="поиск BLE-устройств")
    p.add_argument("--timeout", type=float, default=8.0)
    p.add_argument("--all", action="store_true", help="показывать все BLE-устройства")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("watch", help="живой поток кадров (с переподключением)")
    p.add_argument("--address")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("capture", help="записать поток в JSONL")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--out")
    p.add_argument("--address")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("read-control", help="прочитать 0004 (512-байтовый буфер)")
    p.add_argument("--address")
    p.add_argument("--hex", action="store_true", help="только строка hex")
    p.set_defaults(func=cmd_read_control)

    p = sub.add_parser("write-control", help="записать в 0004")
    p.add_argument("hex", nargs="+")
    p.add_argument("--address")
    p.add_argument("--no-response", action="store_true")
    p.add_argument("--read-back", action="store_true", help="прочитать 0004 после записи")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("write-rx", help="записать в 0002 (write without response)")
    p.add_argument("hex", nargs="+")
    p.add_argument("--address")
    p.set_defaults(func=cmd_write, rx=True, no_response=True, read_back=False)

    p = sub.add_parser("dvw", help="DVW-профиль")
    dvw_sub = p.add_subparsers(dest="dvw_cmd", required=True)
    s = dvw_sub.add_parser("send", help="отправить кривую 02+10 на устройство")
    s.add_argument("--values", type=parse_values)
    s.add_argument("--profile", help="id или имя сохранённого профиля")
    s.add_argument("--address")
    s.add_argument("--handshake", action="store_true", help="сначала записать 01 00 и прочитать 0004")
    s.add_argument("--allow-out-of-range", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_dvw_send)

    p = sub.add_parser("profile", help="сохранённые профили")
    prof_sub = p.add_subparsers(dest="profile_cmd", required=True)
    prof_sub.add_parser("list")
    a = prof_sub.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--values", type=parse_values, required=True)
    a.add_argument("--note")
    r = prof_sub.add_parser("rm")
    r.add_argument("--id", required=True)
    sh = prof_sub.add_parser("show")
    sh.add_argument("--id", required=True)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("wallpaper", help="сгенерировать обои из кривой")
    p.add_argument("--values", type=parse_values)
    p.add_argument("--profile")
    p.add_argument("--out")
    p.add_argument("--theme", default="aurora", choices=["aurora", "ember", "mono", "minimal"])
    p.add_argument("--apply", action="store_true", help="поставить на рабочий стол (Omarchy)")
    p.set_defaults(func=cmd_wallpaper)

    p = sub.add_parser("serve", help="веб-дашборд")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--log-level", default="info")
    p.add_argument("--demo", action="store_true", help="демо-режим: реплей реальных кадров без устройства")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("hud", help="оверлей с живыми данными")
    p.add_argument("--url", default="http://127.0.0.1:8787/api/state")
    p.set_defaults(func=cmd_hud)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        return 130
    except PasitoError as exc:
        print(f"ошибка BLE: {exc}", file=sys.stderr)
        return 1
    if asyncio.iscoroutine(result):
        try:
            return asyncio.run(result)
        except KeyboardInterrupt:
            return 130
        except PasitoError as exc:
            print(f"ошибка BLE: {exc}", file=sys.stderr)
            return 1
    return result or 0


if __name__ == "__main__":
    raise SystemExit(main())
