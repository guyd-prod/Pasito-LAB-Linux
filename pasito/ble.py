"""Async BLE client for Smoant Pasito III (bleak wrapper).

Live-tested behaviour (2026-11-18):
* one vendor service, chars 0002/0003/0004;
* after subscribing to 0003 the device pushes state frames immediately;
* the device closes the link ~13 s after connect (and right after a 0004 read);
* the 0004 value is a 512-byte buffer.
"""

from __future__ import annotations

import asyncio
import logging
import time

from bleak import BleakClient, BleakError, BleakScanner

from .proto import (
    CONTROL_UUID,
    DEVICE_NAME,
    NOTIFY_UUID,
    RX_UUID,
    Frame,
    candidate_reason,
    dvw_frame,
    hx,
)

log = logging.getLogger(__name__)
MANUFACTURER_KEY = 0x0642  # Bluetrum Technology


class PasitoError(RuntimeError):
    """Human-readable BLE failure."""


class Finding:
    """One scanner result (advertising observation)."""

    __slots__ = ("address", "name", "rssi", "manufacturer", "service_data", "services", "reason", "connectable")

    def __init__(self, address, name, rssi, manufacturer, service_data, services, reason, connectable=True):
        self.address = address
        self.name = name
        self.rssi = rssi
        self.manufacturer = manufacturer
        self.service_data = service_data
        self.services = services
        self.reason = reason
        self.connectable = connectable

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "rssi": self.rssi,
            "manufacturer_hex": hx(self.manufacturer),
            "service_data": {k: hx(v) for k, v in self.service_data.items()},
            "services": self.services,
            "candidate": self.reason or "",
        }


def _finding(device, adv) -> Finding:
    name = device.name or getattr(adv, "local_name", None) or ""
    mfr_map = dict(getattr(adv, "manufacturer_data", None) or {})
    manufacturer = b""
    if MANUFACTURER_KEY in mfr_map:
        manufacturer = bytes((0x42, 0x06)) + bytes(mfr_map[MANUFACTURER_KEY])
    elif mfr_map:
        key, value = next(iter(mfr_map.items()))
        manufacturer = bytes((key & 0xFF, (key >> 8) & 0xFF)) + bytes(value)
    services = [str(u) for u in (getattr(adv, "service_uuids", None) or [])]
    reason = candidate_reason(name, manufacturer, services)
    service_data = {str(k): bytes(v) for k, v in (getattr(adv, "service_data", None) or {}).items()}
    return Finding(device.address, name, getattr(adv, "rssi", None), manufacturer, service_data, services, reason)


async def scan(timeout: float = 8.0, include_all: bool = False) -> list[Finding]:
    """Scan for BLE advertising; by default only Pasito candidates."""
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    findings = []
    for device, adv in discovered.values():
        finding = _finding(device, adv)
        if include_all or finding.reason:
            findings.append(finding)
    findings.sort(key=lambda f: (f.reason is None, -(f.rssi if f.rssi is not None else -999)))
    return findings


class PasitoClient:
    """Reusable client. Frame callbacks are invoked synchronously on the event loop."""

    def __init__(self, address: str | None = None, name: str = DEVICE_NAME, on_frame=None, on_state=None):
        self.address = address
        self.name = name
        self.on_frame = on_frame
        self.on_state = on_state
        self.last_error = ""
        self.frames_received = 0
        self.connected_at: float | None = None
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    def _notify_state(self) -> None:
        if self.on_state:
            try:
                self.on_state()
            except Exception:  # never let UI callbacks break the BLE layer
                log.exception("on_state callback failed")

    async def find(self, timeout: float = 12.0):
        """Find the device by cached address, then by advertised name."""
        if self.address:
            try:
                device = await BleakScanner.find_device_by_address(self.address, timeout=timeout)
                if device is not None:
                    return device
            except Exception:
                log.debug("address lookup failed", exc_info=True)
        wanted = self.name.upper()

        def match(device, adv):
            return (device.name or getattr(adv, "local_name", "") or "").upper() == wanted

        return await BleakScanner.find_device_by_filter(match, timeout=timeout)

    async def connect(self, timeout: float = 20.0) -> None:
        async with self._connect_lock:
            if self.connected:
                return
            device = await self.find()
            if device is None:
                raise PasitoError(
                    f"{self.name} не найден в эфире. Включите экран Pasito и откройте меню с Bluetooth/Share."
                )
            self.address = device.address
            client = BleakClient(device, timeout=timeout, disconnected_callback=self._on_disconnect)
            await client.connect()
            self._client = client
            self.connected_at = time.monotonic()
            self.last_error = ""
            log.info("connected to %s", self.address)
            self._notify_state()

    def _on_disconnect(self, _client) -> None:
        self.connected_at = None
        log.info("disconnected")
        self._notify_state()

    async def subscribe(self) -> None:
        if not self.connected:
            raise PasitoError("нет подключения")
        await self._client.start_notify(NOTIFY_UUID, self._on_notify)
        log.info("subscribed to %s", NOTIFY_UUID)

    def _on_notify(self, _characteristic, data: bytearray) -> None:
        frame = Frame.parse(bytes(data))
        if frame is None:
            return
        self.frames_received += 1
        if self.on_frame:
            try:
                self.on_frame(frame)
            except Exception:
                log.exception("on_frame callback failed")

    async def read_control(self) -> bytes:
        if not self.connected:
            raise PasitoError("нет подключения")
        return bytes(await self._client.read_gatt_char(CONTROL_UUID))

    async def write_control(self, data: bytes, response: bool = True) -> None:
        if not self.connected:
            raise PasitoError("нет подключения")
        await self._client.write_gatt_char(CONTROL_UUID, bytes(data), response=response)

    async def write_rx(self, data: bytes) -> None:
        """Write to 0002 (write without response)."""
        if not self.connected:
            raise PasitoError("нет подключения")
        await self._client.write_gatt_char(RX_UUID, bytes(data), response=False)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            log.debug("disconnect failed", exc_info=True)
        self.connected_at = None
        self._notify_state()

    async def wait_until_disconnected(self, poll: float = 0.5) -> None:
        while self.connected:
            await asyncio.sleep(poll)

    async def send_dvw(self, values, handshake: bool = False, read_back: bool = True, settle: float = 80.0) -> dict:
        """Write the '02' + ten powers frame to 0004 and report ATT result.

        handshake=True replays the observed sequence: write 01 00, read the
        control buffer, then write the profile (still experimental).
        """
        frame = dvw_frame(values)
        result = {
            "values": list(values),
            "frame_hex": hx(frame),
            "handshake": handshake,
            "att_ack": False,
            "read_hex": "",
            "steps": [],
        }
        if not self.connected:
            await self.connect()
        if not self.connected:
            raise PasitoError("не удалось подключиться")
        await self.subscribe()
        if handshake:
            try:
                await self.write_control(b"\x01\x00", response=True)
                result["steps"].append("01 00 -> 0004")
                await asyncio.sleep(settle / 1000)
                result["read_after_accept_hex"] = hx(await self.read_control())
            except Exception as exc:
                result["handshake_error"] = str(exc)
        await self.write_control(frame, response=True)
        result["att_ack"] = True
        result["steps"].append(f"{hx(frame)} -> 0004")
        await asyncio.sleep(settle / 1000)
        if read_back:
            try:
                result["read_hex"] = hx(await self.read_control())
            except Exception as exc:
                result["read_error"] = str(exc)
        return result
