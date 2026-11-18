# Контракт Pasito Lab Linux

Проект: Linux-инструмент для Smoant Pasito III (BLE). Заменяет macOS-лабораторию.
Каталог: `~/Projects/pasito-lab-linux`. Venv: `.venv` (bleak, fastapi, uvicorn, pillow, pytest).

## Слои и владение файлами

| Слой | Файлы | Владелец |
|---|---|---|
| Протокол | `pasito/proto.py` | Lead (заморожен) |
| Хранилище | `pasito/store.py` | Lead (заморожен) |
| BLE | `pasito/ble.py` | Lead |
| CLI | `pasito/cli.py` | Lead |
| Сервер | `pasito/server.py` | Lead |
| Веб-дашборд | `web/index.html`, `web/app.js`, `web/style.css` | ui-dev |
| Плюшки | `pasito/wallpaper.py`, `pasito/notify.py`, `pasito/hud.py`, `scripts/` | fx-dev |
| Тесты плюшек | `tests/test_wallpaper.py`, `tests/test_notify.py` | fx-dev |

Не менять чужие файлы. Если нужна правка контракта — написать Lead.

## Модель кадра (notify `6e400003-…`)

Кадр приходит как один ATT-notify (пока наблюдали 30 байт максимум, MTU=33).

```
[seq:u8][msg:u16 LE][flags:u8][declared_len:u8][payload: declared_len байт]
```

- `seq` — сквозной счётчик сообщений устройства (mod 256).
- `msg` — id сообщения. Наблюдены: `0x035c` (state, ~29 байт), `0x0351` (mode, 2 байта), `0x0370` (dvw/profile, 4 байта).
- `declared_len` может быть больше фактически принятого payload (кадр `0x035c` режется MTU) → `truncated: true`.

JSON кадра (ровно в таком виде отдаёт сервер и принимает UI):

```json
{"t": 123.456, "seq": 10, "msg": 860, "msg_name": "state", "flags": 0,
 "declared_len": 29, "payload_hex": "00 3f 5c ...", "raw_hex": "...", "truncated": true}
```

## Модель профиля DVW

ДВВ-кривая = 10 точек, по 1 байту на точку, 1 байт ≈ ватты. Допустимо 5..100 (диапазон устройства), кодирование допускает 0..255.
Кадр записи в `0004`: `02` + 10 байт.

```json
{"id": "a1b2c3d4", "name": "Мой профиль", "values": [20,30,40,50,60,70,80,90,100,100],
 "note": "", "created": "2026-11-18T16:20:00+03:00"}
```

## HTTP API (порт 8787, хост 127.0.0.1)

- `GET /api/state` → State
- `GET /api/frames?limit=200` → `[Frame]` (новые первыми)
- `POST /api/connect` body `{}` → `{"ok": true}` — включает watch: сервер сам переподключается (девайс рвёт сессию через ~13 с)
- `POST /api/disconnect` → `{"ok": true}`
- `GET /api/profiles` → `[Profile]`
- `POST /api/profiles` body `{"name","values","note"}` → Profile
- `PUT /api/profiles/{id}` body `{"name","values","note"}` → Profile
- `DELETE /api/profiles/{id}` → `{"ok": true}`
- `POST /api/send` body `{"values":[...]}` или `{"profile_id":"..."}` → `{"ok":bool,"att_ack":bool,"detail":str,"raw":hex}`
- `GET /api/capture/download` → файл JSONL (`text/plain`, attachment)
- `POST /api/wallpaper` body `{"values":[...]}|{"profile_id":"..."}, "theme":"aurora", "apply":false` → `{"path":str,"applied":bool,"detail":str}`
- `GET /api/wallpaper/preview.png?theme=aurora&values=20,25,...` → `image/png` 900×506
- `GET /api/meta` → `{"protocol":{"service":..., "chars":{...}}, "constants":{"min_w":5,"max_w":100}, "themes":["aurora","ember","mono","minimal"], "version":"0.1.0"}`
- `WS /ws` → сервер шлёт `{"type":"hello","state":State}`, `{"type":"state","state":State}`, `{"type":"frame","frame":Frame}`

State:

```json
{"connected": true, "address": "41:42:FD:AA:BB:CC", "name": "LE-S052", "watch": true,
 "frames_total": 42, "frames": [Frame...] (<=200, новые первыми),
 "profiles": [Profile], "capture_file": "/home/.../session-....jsonl", "last_error": ""}
```

## CLI (python -m pasito.cli …)

`scan`, `watch`, `capture`, `read-control`, `write-control HEX`, `write-rx HEX`, `dvw send --values`, `profile list/add/rm`, `wallpaper`, `serve`.
У BLE-операций флаг `--address`, дефолт — поиск по имени `LE-S052`.

## Плюшки (fx-dev)

- `pasito/wallpaper.py`: `generate_png(values, out_path, size=(2560,1440), theme="aurora", meta=None) -> Path`, `render_preview(values, theme) -> bytes (PNG 900×506)`, `apply_wallpaper(path) -> (bool, str)`; темы `aurora|ember|mono|minimal`; CLI `python -m pasito.wallpaper --values 20,25,... --out f.png --theme aurora [--apply]`.
- `pasito/notify.py`: `send(title, body, urgency="normal", sound=None)`; события `connected(addr)`, `disconnected()`, `profile_sent(n, att_ack)`, `high_power(w)`.
- `pasito/hud.py`: GTK4-оверлей через `gi.repository.Gtk4LayerShell` (typelib есть), fallback — обычное окно без декора; опрос `http://127.0.0.1:8787/api/state` раз в секунду; показывает ватты/кривую/счётчик кадров; Escape — выход. `python -m pasito.hud [--url ...]`.

## Внешние команды на этой машине

- обои: `omarchy theme bg set <path>` (Omarchy 4.5 / quickshell)
- уведомления: `notify-send` (quickshell)
- звук: `canberra-gtk-play -i <name>`, fallback `paplay`
- Wayland/Hyprland 0.56, gtk4-layer-shell 1.3.0
