"""Pasito III BLE protocol: UUIDs, frame model, DVW profile codec.

Facts confirmed live on Smoant Pasito III (S052), 2026-11-18:
- single vendor GATT service 6e400001-…-CCA9D (clone of Nordic UART with last byte 9D)
- 0002 write-without-response, 0003 notify, 0004 read/write (512-byte buffer)
- device pushes 3 frames right after notify subscription, then drops link in ~13 s
- notify frame layout: [seq:u8][msg:u16 LE][flags:u8][len:u8][payload]
- big frame (msg 0x035c, declared_len 29) arrives truncated at 30 bytes -> MTU 33
"""

from __future__ import annotations

import dataclasses
import time

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9d"
RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9d"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9d"
CONTROL_UUID = "6e400004-b5a3-f393-e0a9-e50e24dcca9d"

DEVICE_NAME = "LE-S052"
MANUFACTURER_ID = 0x0642  # Bluetrum Technology Co., Ltd
SERVICE_0X3802 = "00003802-0000-1000-8000-00805f9b34fb"

HEADER_LEN = 5
MSG_NAMES = {0x035C: "state", 0x0351: "mode", 0x0370: "dvw"}

MIN_W = 5
MAX_W = 100
DVW_POINTS = 10
DVW_STEP_SECONDS = 0.5


def hx(data: bytes) -> str:
    """Bytes -> '00 3f 5c'."""
    return " ".join(f"{b:02x}" for b in data)


def unhex(text: str) -> bytes:
    """'00 3f 5c' / '003f5c' / '0x00 0x3f' -> bytes."""
    cleaned = text.replace("0x", " ").replace(",", " ").replace(":", " ")
    compact = "".join(cleaned.split())
    if len(compact) % 2:
        raise ValueError("hex string must have an even number of digits")
    return bytes.fromhex(compact)


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        text = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        lines.append(f"{off:08x}  {hx(chunk):<{width * 3 - 1}}  {text}")
    return "\n".join(lines)


@dataclasses.dataclass(frozen=True)
class Frame:
    t: float
    seq: int
    msg: int
    flags: int
    declared_len: int
    payload: bytes
    raw: bytes

    @property
    def msg_name(self) -> str:
        return MSG_NAMES.get(self.msg, f"0x{self.msg:04x}")

    @property
    def truncated(self) -> bool:
        return len(self.payload) < self.declared_len

    def to_dict(self) -> dict:
        return {
            "t": round(self.t, 3),
            "seq": self.seq,
            "msg": self.msg,
            "msg_name": self.msg_name,
            "flags": self.flags,
            "declared_len": self.declared_len,
            "payload_hex": hx(self.payload),
            "raw_hex": hx(self.raw),
            "truncated": self.truncated,
        }

    @classmethod
    def parse(cls, raw: bytes, t: float | None = None) -> "Frame | None":
        if len(raw) < HEADER_LEN:
            return None
        return cls(
            t=t if t is not None else time.time(),
            seq=raw[0],
            msg=int.from_bytes(raw[1:3], "little"),
            flags=raw[3],
            declared_len=raw[4],
            payload=bytes(raw[5:]),
            raw=bytes(raw),
        )


def dvw_frame(values) -> bytes:
    """10 powers -> 11-byte control frame '02' + 10 bytes."""
    vals = list(values)
    if len(vals) != DVW_POINTS:
        raise ValueError(f"expected {DVW_POINTS} values, got {len(vals)}")
    for v in vals:
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"value {v!r} is not an int")
        if not 0 <= v <= 255:
            raise ValueError(f"value {v} does not fit one byte (0..255)")
    return bytes([0x02] + vals)


def parse_dvw(data: bytes) -> list[int] | None:
    """Control payload -> powers, if it matches the observed 02+10 shape."""
    data = bytes(data)
    if len(data) == 11 and data[0] == 0x02:
        return list(data[1:])
    return None


def power_curve(values, step_seconds: float = DVW_STEP_SECONDS):
    """[(sec, watts)] for plotting."""
    return [(round(i * step_seconds, 3), int(v)) for i, v in enumerate(values)]


def candidate_reason(name: str, manufacturer: bytes | None = None, services=()) -> str | None:
    """Same heuristics as the macOS lab, re-checked against live advertising."""
    if any(str(s).lower() == SERVICE_UUID for s in services):
        return "service uuid"
    if (name or "").upper() == DEVICE_NAME:
        return "name LE-S052"
    if manufacturer and len(manufacturer) == 15 and bytes(manufacturer[:5]) == bytes([0x42, 0x06, 0x02, 0x02, 0x00]):
        return "manufacturer data"
    return None
