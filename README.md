# Pasito Lab Linux

Открытый Linux-инструментарий для BLE-вейпа **Smoant Pasito III (S052)**: сканер, живой поток
кадров, декодер протокола, чтение/запись 512-байтового буфера `0004`, редактор DVW-кривой,
веб-дашборд, обои рабочего стола из профиля, уведомления и HUD-оверлей.


## Что подтверждено на живом устройстве

Снято с реального Pasito III (`LE-S052`, `41:42:FD:AA:BB:CC`):

- BLE-сервис один: `6e400001-b5a3-f393-e0a9-e50e24dcca9d` (клон Nordic UART с `9D` на конце);
  характеристики `0002` write-without-response, `0003` notify, `0004` read/write.
- После подписки на `0003` устройство **само** присылает три кадра: `0x035c` (state), `0x0351`
  (mode), `0x0370` (dvw). Формат: `[seq:u8][msg:u16][flags:u8][len:u8][payload]`.
- `0004` — **буфер 512 байт** (в репозитории-предшественнике считали, что там 2 байта).
- Сессию устройство закрывает само: ~13 с простоя или сразу после чтения `0004`.
- Реклама: manufacturer ID `0x0642` = **Bluetrum**, service data `00003802` = MAC устройства.
- Кадр DVW-профиля: `02` + 10 байт, 1 байт ≈ ватты (диапазон 5–100 Вт).
- Официального приложения, DFU/OTA-сервиса и файлов прошивки у Pasito III нет; экран — 1.3" TFT
  с 24 готовыми обоями, но канала для загрузки своих обоев на устройство не существует.

Полный разбор и открытые вопросы: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Возможности

| Возможность | Где |
|---|---|
| Скан BLE, фильтр кандидатов Pasito, RSSI, manufacturer/service data | `pasito scan` |
| Живой поток кадров с автопереподключением | `pasito watch`, дашборд |
| Запись потока в JSONL (весь сеанс) | `pasito capture`, `/api/capture/download` |
| Чтение 512-байтового `0004` и запись произвольных байт | `pasito read-control`, `write-control`, `write-rx` |
| Отправка DVW-кривой `02+10` с ATT-подтверждением | `pasito dvw send`, дашборд |
| Редактор 10 точек + профили (сохранение/загрузка) | дашборд, `pasito profile` |
| Обои рабочего стола из кривой (4 темы) | `pasito wallpaper`, `/api/wallpaper` |
| Уведомления и звуки | `pasito.notify` |
| HUD-оверлей поверх окон (GTK4 layer-shell) | `pasito hud` || Демо-режим без устройства (реплей реальных кадров) | `pasito serve --demo` |

## Установка

```sh
cd ~/Projects/pasito-lab-linux
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Требования: Linux + BlueZ 5.5x+, Python 3.11+, для оверлея — GTK4 и `gtk4-layer-shell`
(на Omarchy/Arch уже есть). Root не нужен.

## Быстрый старт

1. **Разбудите Pasito**: BLE включается вместе с экраном. Держите устройство включённым
   (экран/меню Bluetooth/Share) — иначе сканер его не увидит.
2. Проверьте связь:

```sh
./run.sh scan                 # должен найтись LE-S052 с RSSI и manufacturer data
./run.sh watch                # живой поток кадров; Ctrl+C — выход
```

3. Запустите дашборд:

```sh
./run.sh serve                # http://127.0.0.1:8787
```

Откройте `http://127.0.0.1:8787`, нажмите **Connect** — дашборд сам будет переподключаться,
когда устройство закрывает сессию. Без устройства:

```sh
./run.sh serve --demo         # dashboard на реплее реальных кадров
```

4. Сгенерируйте обои из кривой и поставьте на рабочий стол:

```sh
./run.sh wallpaper --values 20,30,40,50,60,70,80,90,100,100 --theme aurora --apply
```

5. Отправьте свой DVW-профиль (экспериментально, смотрите на экран устройства):

```sh
./run.sh dvw send --values 20,30,40,50,60,70,80,90,100,100
```

## CLI

```text
pasito scan            [--timeout 8] [--all] [--json]
pasito watch           [--address MAC]
pasito capture         [--seconds 30] [--out FILE] [--address MAC]
pasito read-control    [--hex] [--address MAC]
pasito write-control   HEX... [--no-response] [--read-back]
pasito write-rx        HEX...
pasito dvw send        --values 20,30,... | --profile NAME [--handshake] [--json]
pasito profile         list | add --name N --values ... | rm --id ID | show --id ID
pasito wallpaper       --values ... | --profile NAME [--theme aurora] [--out FILE] [--apply]
pasito serve           [--port 8787] [--demo]
pasito hud             [--url http://127.0.0.1:8787/api/state]
```

## Дашборд

Одностраничный интерфейс без внешних зависимостей: статус связи, живая таблица кадров (фильтры,
поиск по HEX, пауза, JSONL-экспорт), редактор DVW-кривой с графиком и профилями, отправка на
устройство, превью и применение обоев. Работает и в демо-режиме.

REST/WebSocket API заморожен в [docs/CONTRACT.md](docs/CONTRACT.md): `/api/state`, `/api/frames`,
`/api/connect`, `/api/send`, `/api/profiles`, `/api/wallpaper/preview.png`, `/ws`.

## Данные

```text
~/.local/share/pasito-lab/profiles.json          профили DVW
~/.local/share/pasito-lab/captures/*.jsonl       журналы сессий
~/.local/share/pasito-lab/wallpapers/*.png       сгенерированные обои
```

## Демо-режим

`PASITO_DEMO=1` или `pasito serve --demo` — сервер не трогает Bluetooth, а проигрывает реальные
кадры устройства (state/mode/dvw) с растущим `seq`. Нужен для разработки UI, скриншотов и
проверки дашборда, когда Pasito спит.

## Проверено на этой машине (2026-11-18)

- 96 тестов `pytest` зелёные.
- Сканер нашёл живой Pasito (RSSI −66 dBm), manufacturer data и service data совпали.
- BLE-клиент подключался, подписывался и принимал реальные кадры; чтение/запись `0004` проверены.
- Дашборд отдаётся на `127.0.0.1:8787`, REST и WebSocket отвечают, превью обоев генерируется.
- Обои применяются через `omarchy theme bg set` (проверено с возвратом исходного фона).
- Уведомления через quickshell работают; звук — canberra/paplay.
- HUD: в текущей сессии композитор не отдаёт layer-shell (`is_supported()` → False), поэтому
  оверлей открывается обычным окном без декора — это штатный fallback, код готов к layer-shell.

## Безопасность и ограничения

- Запись `02+10` — **экспериментальная**: ATT-подтверждение означает, что запись принял BLE-стек,
  а не что устройство применило профиль. Проверяйте экран Pasito.
- Значения мощности держите в 5–100 Вт; кодирование допускает 0–255, но это не диапазон устройства.
- Не запускайте одновременно несколько клиентов к одному адаптеру/устройству.
- Обои — на **ПК**; загрузить свои обои в Pasito нельзя (нет канала/DFU/приложения).
- Эксперименты — только со своим устройством.

## Troubleshooting

| Симптом | Причина/решение |
|---|---|
| «не найден в эфире» | Pasito погасил BLE вместе с экраном — разбудите, откройте меню Bluetooth/Share |
| Сессия обрывается через ~13 с | Так ведёт себя устройство; клиент переподключается автоматически |
| `read-control` рвёт сессию | Чтение `0004` завершает сессию на устройстве — это не ошибка |
| Пустой поток кадров | Нужна подписка на `0003`: `watch`/дашборд делают это сами |
| Не ставится обои | Нужен Omarchy/quickshell: `omarchy theme bg set <file>`; иначе путь просто сохраняется |

## Структура

```text
pasito/proto.py      протокол: UUID, кадры, DVW-кодек
pasito/ble.py        async-клиент bleak: скан/подключение/подписка/запись
pasito/store.py      профили, журналы, пути данных
pasito/cli.py        CLI
pasito/server.py     FastAPI + WebSocket + демо-режим
pasito/wallpaper.py  генератор обоев (PIL) + применение
pasito/notify.py     уведомления и звуки
pasito/hud.py        GTK4-оверлей (layer-shell)
web/                 дашборд (vanilla JS)
docs/CONTRACT.md     API и модели
docs/PROTOCOL.md     результаты реверса протокола
docs/sample-capture.jsonl  реальный захват кадров
tests/               pytest
```
