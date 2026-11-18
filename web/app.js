"use strict";

/* Pasito Lab dashboard — vanilla JS, no build step, no external deps. */

const UI = {
  values: [20, 25, 30, 40, 50, 60, 70, 80, 90, 100],
  frames: [],
  total: 0,
  profiles: [],
  paused: false,
  selected: null,
  dragIndex: -1,
  wallTimer: null,
  renderScheduled: false,
  connected: false,
  watch: false,
  sending: false,
};

const $ = (id) => document.getElementById(id);
const MIN_W = 5;
const MAX_W = 100;
const MAX_ROWS = 200;

/* ---------- helpers ---------- */

function fmtTime(epoch) {
  const d = new Date(epoch * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

function fmtHex(hex) {
  return hex || "—";
}

function msgClass(msg) {
  if (msg === 0x035c) return "state";
  if (msg === 0x0351) return "mode";
  if (msg === 0x0370) return "dvw";
  return "other";
}

function showBanner(text) {
  const el = $("banner");
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = text;
  el.classList.remove("hidden");
}

function setWsState(text, ok) {
  const el = $("ws-state");
  el.textContent = text;
  el.style.color = ok ? "var(--ok)" : "var(--dim)";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `${response.status} ${response.statusText}`;
    throw new Error(detail);
  }
  return data;
}

/* ---------- state ---------- */

function applyState(state) {
  if (!state) return;
  UI.connected = !!state.connected;
  UI.watch = !!state.watch;
  UI.total = state.frames_total || 0;
  UI.profiles = state.profiles || [];

  const dot = $("conn-dot");
  dot.classList.toggle("on", UI.connected);
  dot.classList.toggle("off", !UI.connected);
  $("conn-text").textContent = UI.connected
    ? "подключено"
    : UI.watch
      ? "ищу устройство…"
      : "не подключено";
  $("device-name").textContent = state.address || "—";
  $("frames-count").textContent = `${UI.total} кадров`;
  $("capture-path").textContent = state.capture_file ? `журнал: ${state.capture_file}` : "журнал: —";
  $("btn-connect").disabled = UI.watch;
  $("btn-disconnect").disabled = !UI.watch && !UI.connected;

  showBanner(state.last_error || "");
  renderProfiles();

  if (Array.isArray(state.frames) && state.frames.length && UI.frames.length === 0) {
    UI.frames = state.frames.slice(0, MAX_ROWS);
    scheduleFramesRender();
  }
}

function addFrame(frame) {
  UI.frames.unshift(frame);
  if (UI.frames.length > MAX_ROWS) UI.frames.length = MAX_ROWS;
  UI.total += 1;
  $("frames-count").textContent = `${UI.total} кадров`;
  if (!UI.paused && passesFilter(frame)) scheduleFramesRender();
}

function passesFilter(frame) {
  const msgFilter = $("filter-msg").value;
  if (msgFilter && String(frame.msg) !== msgFilter) return false;
  const needle = $("search").value.trim().toLowerCase();
  if (!needle) return true;
  return (
    (frame.payload_hex || "").toLowerCase().includes(needle) ||
    (frame.raw_hex || "").toLowerCase().includes(needle) ||
    (frame.msg_name || "").toLowerCase().includes(needle)
  );
}

function scheduleFramesRender() {
  if (UI.renderScheduled) return;
  UI.renderScheduled = true;
  requestAnimationFrame(() => {
    UI.renderScheduled = false;
    renderFrames();
  });
}

/* ---------- frames table ---------- */

function refreshMsgFilter() {
  const select = $("filter-msg");
  const known = new Map();
  for (const frame of UI.frames) known.set(String(frame.msg), frame.msg_name || "");
  for (const [value, name] of known) {
    if (![...select.options].some((option) => option.value === value)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = `0x${Number(value).toString(16).padStart(4, "0")} ${name}`;
      select.appendChild(option);
    }
  }
}

function renderFrames() {
  refreshMsgFilter();
  const body = $("frames-body");
  const rows = UI.frames.filter(passesFilter);
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="dim center">кадров нет — подключите Pasito</td></tr>';
    return;
  }
  const frag = document.createDocumentFragment();
  for (const frame of rows) {
    const tr = document.createElement("tr");
    if (UI.selected && UI.selected.raw_hex === frame.raw_hex && UI.selected.t === frame.t) tr.classList.add("sel");
    tr.dataset.raw = frame.raw_hex;
    const cls = msgClass(frame.msg);
    tr.innerHTML =
      `<td>${fmtTime(frame.t)}</td>` +
      `<td>${frame.seq}</td>` +
      `<td class="msg-${cls}">0x${frame.msg.toString(16).padStart(4, "0")} ${frame.msg_name}</td>` +
      `<td>${frame.flags}</td>` +
      `<td>${frame.declared_len}/${(frame.raw_hex.match(/[0-9a-f]{2}/g) || []).length - 5}` +
      (frame.truncated ? '<span class="trunc">trunc</span>' : "") +
      `</td>` +
      `<td class="hex">${fmtHex(frame.payload_hex)}</td>`;
    tr.addEventListener("click", () => selectFrame(frame, tr));
    frag.appendChild(tr);
  }
  body.replaceChildren(frag);
}

function selectFrame(frame, tr) {
  UI.selected = frame;
  for (const row of document.querySelectorAll("#frames-body tr")) row.classList.remove("sel");
  tr.classList.add("sel");
  const detail = $("frame-detail");
  detail.classList.remove("hidden");
  detail.textContent =
    `seq=${frame.seq}  msg=0x${frame.msg.toString(16).padStart(4, "0")} (${frame.msg_name})  ` +
    `flags=${frame.flags}  declared_len=${frame.declared_len}  payload=${frame.declared_len} байт объявлено\n` +
    `raw:     ${frame.raw_hex}\n` +
    `payload: ${frame.payload_hex}\n` +
    (frame.truncated ? "кадр усечён MTU: доставлено меньше, чем объявлено\n" : "");
}

/* ---------- curve editor ---------- */

function setValues(values, redraw = true) {
  UI.values = values.map((v) => Math.max(0, Math.min(255, Math.round(v))));
  const inputs = document.querySelectorAll("#points input");
  inputs.forEach((input, index) => {
    input.value = String(UI.values[index]);
    input.classList.toggle("out", UI.values[index] < MIN_W || UI.values[index] > MAX_W);
  });
  const outside = UI.values.some((v) => v < MIN_W || v > MAX_W);
  $("range-warn").classList.toggle("hidden", !outside);
  $("sum").textContent = `среднее: ${(UI.values.reduce((a, b) => a + b, 0) / UI.values.length).toFixed(1)} Вт`;
  if (redraw) {
    drawCurve();
    scheduleWallpaperPreview();
  }
}

function buildPointInputs() {
  const container = $("points");
  container.replaceChildren();
  for (let index = 0; index < 10; index += 1) {
    const label = document.createElement("label");
    label.textContent = `шаг ${index + 1}`;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = "255";
    input.value = String(UI.values[index]);
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (Number.isFinite(value)) {
        UI.values[index] = Math.max(0, Math.min(255, Math.round(value)));
        setValues(UI.values);
      } else {
        input.value = String(UI.values[index]);
      }
    });
    label.appendChild(input);
    container.appendChild(label);
  }
  setValues(UI.values, false);
}

function canvasGeometry() {
  const canvas = $("curve");
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(280, rect.width);
  const height = 240;
  const margin = { left: 44, right: 18, top: 16, bottom: 28 };
  return {
    canvas, width, height, margin,
    plotW: width - margin.left - margin.right,
    plotH: height - margin.top - margin.bottom,
  };
}

function drawCurve() {
  const { canvas, width, height, margin, plotW, plotH } = canvasGeometry();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const x = (index) => margin.left + (index / 9) * plotW;
  const y = (value) => margin.top + plotH - (Math.max(0, Math.min(110, value)) / 110) * plotH;

  ctx.font = '10px "JetBrains Mono", monospace';
  ctx.fillStyle = "#8b97a8";
  ctx.strokeStyle = "#1e2632";
  ctx.lineWidth = 1;
  for (const watts of [0, 25, 50, 75, 100]) {
    const py = y(watts);
    ctx.beginPath();
    ctx.moveTo(margin.left, py);
    ctx.lineTo(width - margin.right, py);
    ctx.stroke();
    ctx.fillText(String(watts), 8, py + 3);
  }
  ctx.fillText("Вт", 8, 12);
  for (let index = 0; index < 10; index += 1) {
    const px = x(index);
    ctx.beginPath();
    ctx.moveTo(px, margin.top);
    ctx.lineTo(px, margin.top + plotH);
    ctx.stroke();
    ctx.fillText(`${(index * 0.5).toFixed(1)}`, px - 8, height - 8);
  }
  ctx.fillText("с", width - margin.right - 4, height - 8);

  const gradient = ctx.createLinearGradient(margin.left, 0, width - margin.right, 0);
  gradient.addColorStop(0, "#60a5fa");
  gradient.addColorStop(0.5, "#2dd4bf");
  gradient.addColorStop(1, "#22d3ee");

  ctx.beginPath();
  ctx.moveTo(x(0), y(UI.values[0]));
  for (let index = 1; index < 10; index += 1) ctx.lineTo(x(index), y(UI.values[index]));
  ctx.lineTo(x(9), margin.top + plotH);
  ctx.lineTo(x(0), margin.top + plotH);
  ctx.closePath();
  const fill = ctx.createLinearGradient(0, margin.top, 0, margin.top + plotH);
  fill.addColorStop(0, "rgba(45, 212, 191, 0.28)");
  fill.addColorStop(1, "rgba(45, 212, 191, 0.02)");
  ctx.fillStyle = fill;
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(x(0), y(UI.values[0]));
  for (let index = 1; index < 10; index += 1) ctx.lineTo(x(index), y(UI.values[index]));
  ctx.strokeStyle = gradient;
  ctx.lineWidth = 2.2;
  ctx.shadowColor = "rgba(45, 212, 191, 0.85)";
  ctx.shadowBlur = 12;
  ctx.stroke();
  ctx.shadowBlur = 0;

  for (let index = 0; index < 10; index += 1) {
    const px = x(index);
    const py = y(UI.values[index]);
    ctx.beginPath();
    ctx.arc(px, py, index === UI.dragIndex ? 6 : 4.2, 0, Math.PI * 2);
    ctx.fillStyle = "#0b0f14";
    ctx.fill();
    ctx.strokeStyle = "#5eead4";
    ctx.lineWidth = index === UI.dragIndex ? 3 : 2;
    ctx.stroke();
    if (index === UI.dragIndex) {
      ctx.fillStyle = "#e6edf5";
      ctx.font = '12px "JetBrains Mono", monospace';
      ctx.fillText(`${UI.values[index]} Вт`, px + 10, py - 10);
    }
  }
}

function curvePointer(event) {
  const canvas = $("curve");
  const rect = canvas.getBoundingClientRect();
  const { margin, plotW, plotH } = canvasGeometry();
  const px = event.clientX - rect.left;
  const py = event.clientY - rect.top;
  const index = Math.max(0, Math.min(9, Math.round(((px - margin.left) / plotW) * 9)));
  const value = Math.round(((margin.top + plotH - py) / plotH) * 110);
  return { index, value: Math.max(0, Math.min(255, value)) };
}

function bindCurve() {
  const canvas = $("curve");
  canvas.addEventListener("pointerdown", (event) => {
    const { index, value } = curvePointer(event);
    UI.dragIndex = index;
    UI.values[index] = Math.max(MIN_W, Math.min(MAX_W, value));
    canvas.setPointerCapture(event.pointerId);
    setValues(UI.values);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (UI.dragIndex < 0) return;
    const { index, value } = curvePointer(event);
    if (index !== UI.dragIndex) return;
    UI.values[index] = Math.max(MIN_W, Math.min(MAX_W, value));
    setValues(UI.values);
  });
  const stop = () => {
    if (UI.dragIndex < 0) return;
    UI.dragIndex = -1;
    drawCurve();
  };
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);
  window.addEventListener("resize", drawCurve);
}

/* ---------- profiles ---------- */

function renderProfiles() {
  const container = $("profiles");
  if (!UI.profiles.length) {
    container.innerHTML = '<span class="dim">пока нет</span>';
    return;
  }
  const frag = document.createDocumentFragment();
  for (const profile of UI.profiles) {
    const row = document.createElement("div");
    row.className = "profile";
    const name = document.createElement("span");
    name.className = "pname";
    name.textContent = profile.name;
    const values = document.createElement("span");
    values.className = "pvals";
    values.textContent = profile.values.join(",");
    const load = document.createElement("button");
    load.textContent = "В редактор";
    load.addEventListener("click", () => setValues(profile.values.slice()));
    const remove = document.createElement("button");
    remove.textContent = "✕";
    remove.title = "Удалить профиль";
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/profiles/${encodeURIComponent(profile.id)}`, { method: "DELETE" });
        UI.profiles = UI.profiles.filter((item) => item.id !== profile.id);
        renderProfiles();
      } catch (error) {
        showBanner(`Не удалось удалить профиль: ${error.message}`);
      }
    });
    row.append(name, values, load, remove);
    frag.appendChild(row);
  }
  container.replaceChildren(frag);
}

async function saveProfile() {
  const name = $("profile-name").value.trim() || `Профиль ${UI.profiles.length + 1}`;
  try {
    const profile = await api("/api/profiles", {
      method: "POST",
      body: JSON.stringify({ name, values: UI.values, note: "" }),
    });
    UI.profiles.push(profile);
    renderProfiles();
    $("profile-name").value = "";
    showBanner("");
  } catch (error) {
    showBanner(`Не удалось сохранить профиль: ${error.message}`);
  }
}

/* ---------- send / wallpaper / connect ---------- */

async function sendProfile() {
  if (UI.sending) return;
  UI.sending = true;
  const button = $("btn-send");
  button.disabled = true;
  const result = $("send-result");
  result.classList.remove("hidden", "ok", "bad");
  result.textContent = "Отправляю кадр 02 + 10 байт…";
  try {
    const data = await api("/api/send", {
      method: "POST",
      body: JSON.stringify({ values: UI.values }),
    });
    const ok = data && data.att_ack;
    result.classList.add(ok ? "ok" : "bad");
    result.textContent =
      `${ok ? "ATT-запись принята" : "ATT-запись не подтверждена"}\n` +
      `кадр: ${data.raw}\n` +
      (data.read_back ? `обратное чтение 0004: ${data.read_back}\n` : "") +
      "Применение профиля на устройстве не подтверждено — смотрите на экран Pasito.";
  } catch (error) {
    result.classList.add("bad");
    result.textContent = `Ошибка: ${error.message}`;
  } finally {
    UI.sending = false;
    button.disabled = false;
  }
}

function scheduleWallpaperPreview() {
  clearTimeout(UI.wallTimer);
  UI.wallTimer = setTimeout(() => {
    const theme = $("theme").value;
    $("wall-preview").src =
      `/api/wallpaper/preview.png?theme=${encodeURIComponent(theme)}` +
      `&values=${UI.values.join(",")}&t=${Date.now()}`;
  }, 350);
}

async function wallpaperAction(apply) {
  const result = $("wall-result");
  result.classList.remove("hidden", "ok", "bad");
  result.textContent = apply ? "Генерирую и ставлю обои…" : "Генерирую PNG…";
  try {
    const data = await api("/api/wallpaper", {
      method: "POST",
      body: JSON.stringify({ values: UI.values, theme: $("theme").value, apply }),
    });
    result.classList.add("ok");
    result.textContent = `файл: ${data.path}` + (apply ? `\nприменено: ${data.applied ? "да" : "нет"} ${data.detail || ""}` : "");
  } catch (error) {
    result.classList.add("bad");
    result.textContent = `Ошибка: ${error.message}`;
  }
}

async function connect() {
  showBanner("");
  try {
    await api("/api/connect", { method: "POST", body: "{}" });
  } catch (error) {
    showBanner(`Не удалось запустить подключение: ${error.message}. Сервер запущен? (./run.sh serve)`);
  }
}

async function disconnect() {
  try {
    await api("/api/disconnect", { method: "POST", body: "{}" });
  } catch (error) {
    showBanner(`Не удалось отключиться: ${error.message}`);
  }
}

/* ---------- websocket ---------- */

function connectWs() {
  let retry = 500;
  const open = () => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.onopen = () => {
      retry = 500;
      setWsState("WS: соединение установлено", true);
      showBanner("");
    };
    socket.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "hello" || message.type === "state") applyState(message.state);
      else if (message.type === "frame") addFrame(message.frame);
    };
    socket.onclose = () => {
      setWsState("WS: переподключение…", false);
      retry = Math.min(retry * 1.7, 8000);
      setTimeout(open, retry);
    };
    socket.onerror = () => socket.close();
  };
  open();
}

/* ---------- init ---------- */

function bindEvents() {
  $("btn-connect").addEventListener("click", connect);
  $("btn-disconnect").addEventListener("click", disconnect);
  $("btn-flat").addEventListener("click", () => setValues(Array.from({ length: 10 }, () => 50)));
  $("btn-random").addEventListener("click", () => {
    const values = Array.from({ length: 10 }, () => MIN_W + Math.round(Math.random() * (MAX_W - MIN_W)));
    setValues(values);
  });
  $("btn-save-profile").addEventListener("click", saveProfile);
  $("btn-send").addEventListener("click", sendProfile);
  $("btn-pause").addEventListener("click", () => {
    UI.paused = !UI.paused;
    $("btn-pause").textContent = UI.paused ? "Продолжить" : "Пауза";
    if (!UI.paused) scheduleFramesRender();
  });
  $("filter-msg").addEventListener("change", scheduleFramesRender);
  $("search").addEventListener("input", scheduleFramesRender);
  $("btn-wall-save").addEventListener("click", () => wallpaperAction(false));
  $("btn-wall-apply").addEventListener("click", () => wallpaperAction(true));
  $("theme").addEventListener("change", scheduleWallpaperPreview);
  for (const id of ["btn-connect", "btn-disconnect"]) {
    $(id).addEventListener("click", () => setTimeout(() => {}, 0));
  }
}

async function init() {
  buildPointInputs();
  bindCurve();
  bindEvents();
  connectWs();
  scheduleWallpaperPreview();
  try {
    const state = await api("/api/state");
    applyState(state);
  } catch (error) {
    showBanner(`Сервер недоступен. Запустите: ./run.sh serve — ${error.message}`);
  }
}

document.addEventListener("DOMContentLoaded", init);
