"""Protocol codec tests built on frames captured live from a real Pasito III."""

import pytest

from pasito import proto

# Real notification, captured 2026-11-18 from 41:42:FD:6A:53:2B (seq=0x0a):
# declared_len 29 but only 30 bytes total arrived -> truncated by MTU 33.
RAW_STATE = "0a 5c 03 00 1d 00 3f 5c 37 04 57 5f 00 00 3f 00 00 00 ff ff 1f 00 32 2a 00 00 ff 0f 00 00"
RAW_MODE = "0b 51 03 00 02 00 00"
RAW_DVW = "0c 70 03 00 04 01 18 b4 41"


def test_state_frame_is_truncated():
    frame = proto.Frame.parse(proto.unhex(RAW_STATE), t=1.0)
    assert frame is not None
    assert frame.seq == 0x0A
    assert frame.msg == 0x035C
    assert frame.msg_name == "state"
    assert frame.flags == 0
    assert frame.declared_len == 29
    assert len(frame.payload) == 25
    assert frame.truncated is True
    assert frame.t == 1.0


def test_small_frames_complete():
    mode = proto.Frame.parse(proto.unhex(RAW_MODE))
    dvw = proto.Frame.parse(proto.unhex(RAW_DVW))
    assert (mode.msg, mode.msg_name, mode.declared_len, len(mode.payload)) == (0x0351, "mode", 2, 2)
    assert mode.truncated is False
    assert (dvw.msg, dvw.msg_name, dvw.declared_len, len(dvw.payload)) == (0x0370, "dvw", 4, 4)
    assert dvw.truncated is False


def test_frame_dict_shape_matches_contract():
    payload = proto.Frame.parse(proto.unhex(RAW_MODE)).to_dict()
    assert set(payload) == {
        "t", "seq", "msg", "msg_name", "flags", "declared_len", "payload_hex", "raw_hex", "truncated",
    }
    assert payload["payload_hex"] == "00 00"


def test_parse_short_input_returns_none():
    assert proto.Frame.parse(b"\x00\x01\x02") is None


def test_dvw_roundtrip():
    values = [5, 10, 20, 30, 40, 50, 60, 70, 80, 100]
    frame = proto.dvw_frame(values)
    assert frame == bytes([0x02] + values)
    assert len(frame) == 11
    assert proto.parse_dvw(frame) == values


@pytest.mark.parametrize(
    "bad",
    [
        [1, 2, 3],
        [0] * 11,
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 256],
        [0, 0, 0, 0, 0, 0, 0, 0, 0, -1],
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 1.5],
    ],
)
def test_dvw_rejects_bad_values(bad):
    with pytest.raises((ValueError, TypeError)):
        proto.dvw_frame(bad)


def test_parse_dvw_ignores_other_shapes():
    assert proto.parse_dvw(bytes([0x01, 0x00])) is None
    assert proto.parse_dvw(bytes([0x02] + [0] * 9)) is None


def test_hex_helpers():
    assert proto.hx(b"\x00\x3f\xff") == "00 3f ff"
    assert proto.unhex("00 3f ff") == b"\x00\x3f\xff"
    assert proto.unhex("003fff") == b"\x00\x3f\xff"
    assert proto.unhex("0x00, 0x3f") == b"\x00\x3f"
    with pytest.raises(ValueError):
        proto.unhex("00 3")
    dump = proto.hexdump(b"\x00" * 20, width=16)
    assert dump.count("\n") == 1
    assert "00000010" in dump


def test_candidate_heuristics():
    real_manufacturer = bytes.fromhex("4206020200ecef50c7fe8600000000")
    assert proto.candidate_reason("LE-S052") == "name LE-S052"
    assert proto.candidate_reason("S052", real_manufacturer) == "manufacturer data"
    assert proto.candidate_reason("x", real_manufacturer, [proto.SERVICE_UUID]) == "service uuid"
    assert proto.candidate_reason("random", b"\x01\x02", []) is None


def test_power_curve_uses_half_second_steps():
    curve = proto.power_curve([10, 20])
    assert curve == [(0.0, 10), (0.5, 20)]
    assert proto.DVW_STEP_SECONDS == 0.5
