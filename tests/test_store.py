"""Profile/capture storage tests (isolated in tmp_path)."""

import json

import pytest

from pasito import proto, store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "PROFILES_FILE", tmp_path / "profiles.json")
    monkeypatch.setattr(store, "CAPTURES_DIR", tmp_path / "captures")
    monkeypatch.setattr(store, "WALLPAPERS_DIR", tmp_path / "wallpapers")
    return tmp_path


def test_profile_crud():
    assert store.load_profiles() == []
    profile = store.add_profile("Мой профиль", [5] * 10, note="тест")
    assert store.get_profile(profile["id"])["name"] == "Мой профиль"
    assert store.update_profile(profile["id"], name="Новый", values=[10] * 10)["name"] == "Новый"
    assert len(store.load_profiles()) == 1
    assert store.delete_profile(profile["id"]) is True
    assert store.load_profiles() == []
    assert store.delete_profile("nope") is False


def test_profile_validation():
    with pytest.raises(ValueError):
        store.add_profile("x", [1, 2, 3])
    with pytest.raises(ValueError):
        store.add_profile("x", [0] * 9 + [256])


def test_profiles_file_survives_corruption():
    store.ensure_dirs()
    store.PROFILES_FILE.write_text("{not json", encoding="utf-8")
    assert store.load_profiles() == []


def test_capture_writer_writes_jsonl(isolated):
    writer = store.CaptureWriter(isolated / "cap.jsonl")
    frame = proto.Frame.parse(proto.unhex("0b 51 03 00 02 00 00"))
    writer.frame(frame)
    writer.event("connected", address="41:42")
    writer.close()

    lines = [json.loads(line) for line in (isolated / "cap.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["kind"] for line in lines] == ["frame", "event"]
    assert lines[0]["msg_name"] == "mode"
    assert lines[1]["event"] == "connected"


def test_capture_path_created_in_captures_dir(isolated):
    path = store.new_capture_path("demo")
    assert path.parent == isolated / "captures"
    assert path.name.startswith("demo-")
