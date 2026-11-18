"""Tests for the wallpaper generator — no display, no real wallpaper changes."""

from __future__ import annotations

import io
import subprocess

import pytest
from PIL import Image

from pasito import wallpaper

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
VALUES = [20, 25, 30, 40, 50, 60, 70, 80, 90, 100]


def test_generate_png_all_themes(tmp_path):
    for theme in sorted(wallpaper.THEMES):
        out = tmp_path / f"{theme}.png"
        result = wallpaper.generate_png(VALUES, out, size=(800, 450), theme=theme)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 1000, f"{theme}: PNG is suspiciously small"
        assert out.read_bytes()[:8] == PNG_MAGIC
        with Image.open(out) as image:
            assert image.format == "PNG"
            assert image.size == (800, 450)
            assert image.mode == "RGB"


def test_generate_png_default_size_and_meta(tmp_path):
    out = tmp_path / "default.png"
    meta = {"battery_pct": 80, "puffs": 1234, "mode": "POW", "device": "LE-S052"}
    wallpaper.generate_png(VALUES, out, meta=meta)
    assert out.read_bytes()[:8] == PNG_MAGIC
    with Image.open(out) as image:
        assert image.size == (2560, 1440)


def test_generate_png_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "dir" / "wall.png"
    result = wallpaper.generate_png(VALUES, out, size=(320, 200))
    assert result == out
    assert out.is_file()


def test_render_preview_is_900x506_png():
    data = wallpaper.render_preview(VALUES, theme="aurora")
    assert isinstance(data, bytes)
    assert data[:8] == PNG_MAGIC
    assert len(data) > 10_000
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (900, 506)
        assert image.format == "PNG"


def test_render_preview_other_theme_and_size():
    data = wallpaper.render_preview(VALUES, theme="ember", size=(400, 300))
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (400, 300)


def test_meta_values_are_not_invented(tmp_path):
    """No meta -> still renders; weird/invalid meta must not crash."""
    for meta in (None, {}, {"battery_pct": "oops"}, {"puffs": -5}, {"device": ""}, {"battery_pct": True}):
        out = tmp_path / "meta.png"
        wallpaper.generate_png(VALUES, out, size=(320, 200), meta=meta)
        assert out.read_bytes()[:8] == PNG_MAGIC


@pytest.mark.parametrize("theme", ["nope", "", "Aurora"])
def test_unknown_theme_rejected(theme):
    with pytest.raises(ValueError, match="тема"):
        wallpaper.render_image(VALUES, theme=theme)


@pytest.mark.parametrize(
    "values",
    [
        [20] * 9,
        [20] * 11,
        [20] * 9 + [256],
        [20] * 9 + [-1],
        [20] * 9 + ["30"],
        [20] * 9 + [True],
        "20,25,30,40,50,60,70,80,90,100",
    ],
)
def test_invalid_values_rejected(values):
    with pytest.raises(ValueError):
        wallpaper.render_image(values, size=(320, 200))


@pytest.mark.parametrize(
    "size",
    [(319, 200), (320, 199), (7681, 1000), (1000, 4321), (0, 0), ("2560", "1440"), (2560,), None],
)
def test_invalid_sizes_rejected(size):
    with pytest.raises(ValueError):
        wallpaper.render_image(VALUES, size=size)


def test_size_bounds_accept_extremes():
    assert wallpaper.validate_size((320, 200)) == (320, 200)
    assert wallpaper.validate_size((7680, 4320)) == (7680, 4320)


# ---------------------------------------------------------------------------
# Parsing / CLI
# ---------------------------------------------------------------------------


def test_parse_values_accepts_commas_spaces_and_limits():
    assert wallpaper.parse_values("20,25,30,40,50,60,70,80,90,100") == VALUES
    assert wallpaper.parse_values(" 20 25 30 40 50 60 70 80 90 100 ") == VALUES
    assert wallpaper.parse_values("0,1,2,3,4,5,6,7,8,255") == [0, 1, 2, 3, 4, 5, 6, 7, 8, 255]


@pytest.mark.parametrize(
    "text",
    ["", "20,25", "20,25,30,40,50,60,70,80,90", "20,25,30,40,50,60,70,80,90,100,110",
     "20,25,30,40,50,60,70,80,90,256", "20,25,30,40,50,60,70,80,90,abc", "20,25,30,40,50,60,70,80,90,-1"],
)
def test_parse_values_errors(text):
    with pytest.raises(ValueError):
        wallpaper.parse_values(text)


def test_parse_size():
    assert wallpaper.parse_size("2560x1440") == (2560, 1440)
    assert wallpaper.parse_size(" 320X200 ") == (320, 200)
    assert wallpaper.parse_size("900×506") == (900, 506)
    with pytest.raises(ValueError):
        wallpaper.parse_size("2560*1440")
    with pytest.raises(ValueError):
        wallpaper.parse_size("100x100")


def test_parse_meta():
    assert wallpaper.parse_meta(None) == {}
    assert wallpaper.parse_meta("") == {}
    assert wallpaper.parse_meta('{"battery_pct": 80}') == {"battery_pct": 80}
    with pytest.raises(ValueError, match="meta-json"):
        wallpaper.parse_meta("{not json}")
    with pytest.raises(ValueError, match="JSON-объектом"):
        wallpaper.parse_meta("[1, 2, 3]")


def test_cli_generates_file(tmp_path, capsys):
    out = tmp_path / "cli.png"
    code = wallpaper.main(["--values", ",".join(map(str, VALUES)), "--out", str(out), "--theme", "mono",
                           "--size", "400x300"])
    assert code == 0
    assert out.read_bytes()[:8] == PNG_MAGIC
    assert "обои сохранены" in capsys.readouterr().out


def test_cli_profile_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wallpaper.store,
        "load_profiles",
        lambda: [{"id": "abc12345", "name": "Мой профиль", "values": VALUES}],
    )
    out = tmp_path / "profile.png"
    assert wallpaper.main(["--profile", "Мой профиль", "--out", str(out), "--size", "320x200"]) == 0
    assert out.is_file()
    assert wallpaper.main(["--profile", "abc12345", "--out", str(tmp_path / "by-id.png"),
                           "--size", "320x200"]) == 0


def test_cli_profile_missing_is_friendly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wallpaper.store, "load_profiles", lambda: [])
    code = wallpaper.main(["--profile", "нет такого", "--out", str(tmp_path / "x.png")])
    assert code == 1
    err = capsys.readouterr().err
    assert "профиль" in err and "не найден" in err


def test_cli_bad_values_exit_1(tmp_path, capsys):
    code = wallpaper.main(["--values", "20,25", "--out", str(tmp_path / "x.png")])
    assert code == 1
    assert "ошибка" in capsys.readouterr().err.lower()


def test_cli_bad_size_exit_1(tmp_path, capsys):
    code = wallpaper.main(["--values", ",".join(map(str, VALUES)), "--out", str(tmp_path / "x.png"),
                           "--size", "100x100"])
    assert code == 1
    assert "размер" in capsys.readouterr().err


def test_cli_help_and_unknown_theme():
    with pytest.raises(SystemExit) as exc:
        wallpaper.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        wallpaper.build_parser().parse_args(["--values", "1", "--out", "x.png", "--theme", "unknown"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# apply_wallpaper: mocked, never touches the real desktop
# ---------------------------------------------------------------------------


def test_apply_missing_file():
    ok, detail = wallpaper.apply_wallpaper("/tmp/definitely-not-here-pasito.png")
    assert ok is False
    assert "не найден" in detail


def test_apply_without_any_binary(monkeypatch, tmp_path):
    img = tmp_path / "bg.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(wallpaper.shutil, "which", lambda name: None)
    ok, detail = wallpaper.apply_wallpaper(img)
    assert ok is False
    assert "swww" in detail or "omarchy" in detail


def test_apply_uses_omarchy(monkeypatch, tmp_path):
    img = tmp_path / "bg.png"
    img.write_bytes(b"x")
    calls = []

    def fake_which(name):
        return {"omarchy": "/usr/bin/omarchy"}.get(name)

    def fake_run(command, **kwargs):
        calls.append(command)
        assert kwargs.get("timeout") == 15
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(wallpaper.shutil, "which", fake_which)
    monkeypatch.setattr(wallpaper.subprocess, "run", fake_run)
    ok, detail = wallpaper.apply_wallpaper(img)
    assert ok is True
    assert "omarchy" in detail
    assert calls == [["/usr/bin/omarchy", "theme", "bg", "set", str(img)]]


def test_apply_falls_back_to_swww(monkeypatch, tmp_path):
    img = tmp_path / "bg.png"
    img.write_bytes(b"x")
    calls = []

    def fake_which(name):
        return {"omarchy": "/usr/bin/omarchy", "swww": "/usr/bin/swww"}.get(name)

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0].endswith("omarchy"):
            return subprocess.CompletedProcess(command, 1, "", "omarchy broke")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(wallpaper.shutil, "which", fake_which)
    monkeypatch.setattr(wallpaper.subprocess, "run", fake_run)
    ok, detail = wallpaper.apply_wallpaper(img)
    assert ok is True
    assert "swww" in detail
    assert [c[0] for c in calls] == ["/usr/bin/omarchy", "/usr/bin/swww"]


def test_apply_never_raises_on_timeout(monkeypatch, tmp_path):
    img = tmp_path / "bg.png"
    img.write_bytes(b"x")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 15)

    monkeypatch.setattr(wallpaper.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(wallpaper.subprocess, "run", fake_run)
    ok, detail = wallpaper.apply_wallpaper(img)
    assert ok is False
    assert "таймаут" in detail.lower()
