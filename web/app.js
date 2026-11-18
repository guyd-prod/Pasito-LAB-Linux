/* Pasito Lab — dashboard logic.
 * Classic script (no modules), vanilla ES2020, no external dependencies.
 * All data comes from the FastAPI backend on the same origin.
 */
(() => {
  "use strict";

  const POINTS = 10;
  const MAX_FRAMES = 200;
  const RENDER_INTERVAL_MS = 100;
  const PREVIEW_DEBOUNCE_MS = 350;
  const WS_MAX_DELAY_MS = 15000;
  const REQUEST_TIMEOUT_MS = 20000;
  const THEME_IDS = ["aurora", "ember", "mono", "minimal"];
  const KNOWN_MSGS = { 0x035c: "state", 0x0351: "mode", 0x0370: "dvw" };
  const MSG_CLASSES = { 0x035c: "msg-state", 0x0351: "msg-mode", 0x0370: "msg-dvw" };
  const numberFormatter = new Intl.NumberFormat("ru-RU");

  const state = {
    meta: null,
    metaLoaded: false,
    minW: 5,
    maxW: 100,
    server: {
      online: false,
      checked: false,
      fetching: false,
      offlineToastShown: false,
    },
    connection: {
      connected: false,
      address: "",
      name: "",
      watch: false,
      framesTotal: 0,
      captureFile: "",
      lastError: "",
    },
    frames: [],
    paused: false,
    frozenFrames: null,
    pauseStartTotal: 0,
    filterMsg: "all",
    search: "",
    expanded: new Set(),
    profiles: [],
    selectedProfileId: null,
    renamingId: null,
    profileBusy: false,
    profileSaving: false,
    activeProfile: null,
    activeProfileDirty: false,
    curve: new Array(POINTS).fill(20),
    wallpaper: {
      theme: "aurora",
      previewKey: "",
      previewToken: 0,
      pending: null,
      result: null,
    },
    send: {
      pending: false,
      result: null,
    },
    ws: {
      socket: null,
      generation: 0,
      attempts: 0,
      status: "idle",
      everConnected: false,
      manualClose: false,
      retryTimer: null,
      countdownTimer: null,
      retryAt: 0,
    },
    pollTimer: null,
    render: {
      dirty: false,
      rafPending: false,
      rafId: null,
      timer: null,
      lastAt: 0,
    },
    connectPending: null,
    downloadPending: false,
    previewDebounce: null,
    searchDebounce: null,
  };

  const dom = {};
  const chart = {
    dragging: -1,
    pointerId: null,
    geom: null,
  };

  /* ------------------------------------------------------------------ */
  /* utilities                                                          */
  /* ------------------------------------------------------------------ */

  function byId(id) {
    return document.getElementById(id);
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function padNumber(value, width) {
    return String(Math.max(0, Math.trunc(Number(value) || 0))).padStart(width, "0");
  }

  function hex4(value) {
    return (Math.trunc(Number(value) || 0) & 0xffff).toString(16).padStart(4, "0");
  }

  function hex2(value) {
    return (Math.trunc(Number(value) || 0) & 0xff).toString(16).padStart(2, "0");
  }

  function formatInt(value) {
    const n = Number(value);
    return numberFormatter.format(Number.isFinite(n) ? n : 0);
  }

  function pluralRu(count, forms) {
    const n10 = count % 10;
    const n100 = count % 100;
    if (n10 === 1 && n100 !== 11) return forms[0];
    if (n10 >= 2 && n10 <= 4 && (n100 < 10 || n100 >= 20)) return forms[1];
    return forms[2];
  }

  function msgName(msg) {
    return KNOWN_MSGS[msg] || "0x" + hex4(msg);
  }

  function displayMsgName(frame) {
    const name = frame.msg_name || msgName(frame.msg);
    if (/^0x[0-9a-f]{4}$/i.test(name) && name.toLowerCase() === "0x" + hex4(frame.msg)) {
      return "other";
    }
    return name;
  }

  function formatTime(t) {
    const n = Number(t);
    if (!Number.isFinite(n) || n <= 0) return "—";
    if (n > 1e9) {
      const d = new Date(n * 1000);
      if (Number.isNaN(d.getTime())) return n.toFixed(3);
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      const ss = String(d.getSeconds()).padStart(2, "0");
      const ms = String(d.getMilliseconds()).padStart(3, "0");
      return hh + ":" + mm + ":" + ss + "." + ms;
    }
    return n.toFixed(3) + " с";
  }

  function timeTitle(t) {
    const n = Number(t);
    if (!Number.isFinite(n) || n <= 0) return "";
    if (n > 1e9) {
      const d = new Date(n * 1000);
      if (!Number.isNaN(d.getTime())) return d.toISOString();
    }
    return "t = " + n.toFixed(3) + " с";
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatClock(date) {
    return date.toLocaleTimeString("ru-RU", { hour12: false });
  }

  function baseName(path) {
    const text = String(path || "");
    const parts = text.split(/[\\/]/);
    return parts[parts.length - 1] || text;
  }

  function bytesFromHex(hex) {
    const cleaned = String(hex == null ? "" : hex).trim();
    if (!cleaned) return [];
    const parts = cleaned.split(/\s+/);
    if (
      parts.length === 1 &&
      parts[0].length > 2 &&
      parts[0].length % 2 === 0 &&
      /^[0-9a-fA-F]+$/.test(parts[0])
    ) {
      const out = [];
      for (let i = 0; i < parts[0].length; i += 2) out.push(parts[0].slice(i, i + 2).toLowerCase());
      return out;
    }
    return parts
      .map((part) => part.toLowerCase().replace(/^0x/, ""))
      .filter((part) => /^[0-9a-f]{2}$/.test(part));
  }

  function hexDump(bytes, width = 16) {
    if (!bytes.length) return "(пусто)";
    const lines = [];
    for (let offset = 0; offset < bytes.length; offset += width) {
      const chunk = bytes.slice(offset, offset + width);
      const hexPart = chunk.join(" ").padEnd(width * 3 - 1, " ");
      const ascii = chunk
        .map((byte) => {
          const code = parseInt(byte, 16);
          return code >= 32 && code <= 126 ? String.fromCharCode(code) : ".";
        })
        .join("");
      lines.push(offset.toString(16).padStart(8, "0") + "  " + hexPart + "  |" + ascii + "|");
    }
    return lines.join("\n");
  }

  function normalizeFrame(raw) {
    if (!raw || typeof raw !== "object") return null;
    const msg = Number(raw.msg) || 0;
    return {
      t: Number(raw.t) || 0,
      seq: Number(raw.seq) || 0,
      msg: msg,
      msg_name: typeof raw.msg_name === "string" && raw.msg_name ? raw.msg_name : msgName(msg),
      flags: Number(raw.flags) || 0,
      declared_len: Math.max(0, Math.trunc(Number(raw.declared_len) || 0)),
      payload_hex: typeof raw.payload_hex === "string" ? raw.payload_hex : "",
      raw_hex: typeof raw.raw_hex === "string" ? raw.raw_hex : "",
      truncated: Boolean(raw.truncated),
    };
  }

  function frameKey(frame) {
    return String(frame.t) + "|" + frame.seq + "|" + frame.msg + "|" + frame.raw_hex;
  }

  function frameIsTruncated(frame, bytes) {
    return Boolean(frame.truncated) || (frame.declared_len > 0 && bytes.length < frame.declared_len);
  }

  function searchKey(text) {
    return String(text == null ? "" : text)
      .toLowerCase()
      .replace(/0x/g, "")
      .replace(/[\s,;:._\-]+/g, "");
  }

  function normalizeValues(values) {
    if (!Array.isArray(values) || values.length !== POINTS) return null;
    return values.map((value) => clamp(Math.round(Number(value) || 0), 0, 255));
  }

  function outOfRangeIndices() {
    const result = [];
    state.curve.forEach((value, index) => {
      if (!Number.isFinite(value) || value < state.minW || value > state.maxW) result.push(index);
    });
    return result;
  }

  /* ------------------------------------------------------------------ */
  /* HTTP API                                                           */
  /* ------------------------------------------------------------------ */

  function extractApiDetail(data) {
    if (data && typeof data === "object") {
      let detail = data.detail != null ? data.detail : data.error != null ? data.error : data.message;
      if (Array.isArray(detail)) {
        detail = detail.map((item) => (item && item.msg ? item.msg : JSON.stringify(item))).join("; ");
      }
      return detail == null ? "" : String(detail);
    }
    if (typeof data === "string") {
      const trimmed = data.trim();
      if (trimmed && trimmed.length < 240 && trimmed[0] !== "<") return trimmed;
    }
    return "";
  }

  async function api(path, options = {}) {
    const opts = Object.assign({}, options);
    const headers = new Headers(opts.headers || {});
    headers.set("Accept", "application/json");
    if (opts.body !== undefined && !(opts.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
      if (typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    }
    opts.headers = headers;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    opts.signal = controller.signal;

    try {
      const response = await fetch(path, opts);
      const text = await response.text();
      let data = null;
      if (text) {
        const contentType = response.headers.get("content-type") || "";
        if (contentType.indexOf("json") !== -1) {
          try {
            data = JSON.parse(text);
          } catch (err) {
            data = text;
          }
        } else {
          data = text;
        }
      }
      if (!response.ok) {
        const detail = extractApiDetail(data);
        throw new Error("HTTP " + response.status + (detail ? " — " + detail : ""));
      }
      return data;
    } catch (err) {
      if (err && err.name === "AbortError") throw new Error("таймаут запроса");
      if (err instanceof TypeError) {
        throw new Error("сеть недоступна (" + (err.message || "fetch failed") + ")");
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  /* ------------------------------------------------------------------ */
  /* toasts                                                             */
  /* ------------------------------------------------------------------ */

  function toast(message, kind = "info", ttl = 4800) {
    if (!dom.toasts) return;
    const element = document.createElement("div");
    element.className = "toast toast-" + kind;
    element.setAttribute("role", kind === "error" ? "alert" : "status");
    const icon = kind === "error" ? "✕" : kind === "success" ? "✓" : kind === "warn" ? "!" : "i";
    element.innerHTML =
      '<span class="toast-icon" aria-hidden="true">' +
      icon +
      '</span><span class="toast-msg">' +
      escapeHtml(message) +
      '</span><button class="toast-close" type="button" aria-label="Закрыть">×</button>';
    const remove = () => {
      clearTimeout(timer);
      if (!element.isConnected) return;
      element.classList.add("toast-out");
      setTimeout(() => element.remove(), 180);
    };
    const timer = setTimeout(remove, ttl);
    element.querySelector(".toast-close").addEventListener("click", remove);
    dom.toasts.appendChild(element);
    while (dom.toasts.children.length > 4) dom.toasts.firstElementChild.remove();
  }

  /* ------------------------------------------------------------------ */
  /* server state, status bar                                           */
  /* ------------------------------------------------------------------ */

  function markServerOnline() {
    const wasOnline = state.server.online;
    state.server.online = true;
    state.server.checked = true;
    state.server.offlineToastShown = false;
    showOffline(false);
    if (!wasOnline) {
      renderStatus();
      renderServerChip();
      updateWallpaperPreview(true);
      if (!state.metaLoaded) loadMeta();
    }
  }

  function showOffline(offline, detail) {
    if (!dom.offlineBanner) return;
    dom.offlineBanner.hidden = !offline;
    if (offline) {
      if (detail) dom.offlineDetail.textContent = "Причина: " + detail;
    } else {
      dom.offlineDetail.textContent = "";
    }
  }

  function renderServerChip() {
    if (!dom.serverChip) return;
    dom.serverChip.classList.remove("is-online", "is-offline", "is-checking");
    if (!state.server.checked) {
      dom.serverChip.classList.add("is-checking");
      dom.serverChipText.textContent = "сервер: проверка…";
      return;
    }
    if (state.server.online) {
      dom.serverChip.classList.add("is-online");
      dom.serverChipText.textContent = "сервер: онлайн";
    } else {
      dom.serverChip.classList.add("is-offline");
      dom.serverChipText.textContent = "сервер: офлайн";
    }
  }

  function renderStatus() {
    const connection = state.connection;
    const online = state.server.online;

    dom.linkDot.className = "link-dot";
    if (!online) {
      dom.linkDot.classList.add("is-offline");
      dom.linkText.textContent = "Сервер недоступен";
    } else if (connection.connected) {
      dom.linkDot.classList.add("is-online");
      dom.linkText.textContent = "На связи";
    } else if (connection.watch) {
      dom.linkDot.classList.add("is-watch");
      dom.linkText.textContent = "Переподключение (watch)";
    } else {
      dom.linkDot.classList.add("is-offline");
      dom.linkText.textContent = "Не подключено";
    }

    if (connection.name || connection.address) {
      const name = connection.name || "Pasito III";
      const address = connection.address || "адрес неизвестен";
      dom.deviceText.innerHTML =
        '<span class="dev-name">' +
        escapeHtml(name) +
        '</span><span class="dev-addr">' +
        escapeHtml(address) +
        "</span>";
    } else {
      dom.deviceText.textContent = "—";
    }

    dom.watchPill.className = "pill " + (connection.watch ? "on" : "off");
    dom.watchPill.textContent = "watch " + (connection.watch ? "on" : "off");

    updateFramesCounter();

    if (connection.captureFile) {
      dom.captureText.textContent = baseName(connection.captureFile);
      dom.captureText.title = connection.captureFile;
    } else {
      dom.captureText.textContent = "—";
      dom.captureText.title = "";
    }

    const error = connection.lastError;
    dom.errorBox.classList.toggle("has-error", Boolean(error));
    dom.errorText.textContent = error || "—";
    dom.errorText.title = error || "";

    const pending = state.connectPending;
    dom.connectBtn.disabled = !online || Boolean(pending) || connection.connected || connection.watch;
    dom.disconnectBtn.disabled =
      !online || Boolean(pending) || (!connection.connected && !connection.watch);
    dom.connectBtn.textContent = pending === "connect" ? "Connect…" : "Connect";
    dom.disconnectBtn.textContent = pending === "disconnect" ? "Disconnect…" : "Disconnect";
    dom.connectBtn.setAttribute("aria-busy", pending === "connect" ? "true" : "false");
    dom.disconnectBtn.setAttribute("aria-busy", pending === "disconnect" ? "true" : "false");
    dom.connectBtn.title = "Включить watch: POST /api/connect";
    dom.disconnectBtn.title = "Остановить watch: POST /api/disconnect";

    if (dom.profilesEmpty) {
      dom.profilesEmpty.textContent = online
        ? "Профилей пока нет. Сохраните текущую кривую — она появится здесь."
        : "Сервер недоступен — профили не загружены.";
    }

    renderServerChip();
  }

  function updateFramesCounter() {
    dom.framesTotal.textContent = formatInt(state.connection.framesTotal);
  }

  function applyState(raw) {
    if (!raw || typeof raw !== "object") return;
    const connection = state.connection;
    connection.connected = Boolean(raw.connected);
    connection.address = typeof raw.address === "string" ? raw.address : "";
    connection.name = typeof raw.name === "string" ? raw.name : "";
    connection.watch = Boolean(raw.watch);

    const total = Number(raw.frames_total);
    if (Number.isFinite(total) && total >= 0) connection.framesTotal = Math.trunc(total);

    connection.captureFile = typeof raw.capture_file === "string" ? raw.capture_file : "";
    connection.lastError = raw.last_error == null ? "" : String(raw.last_error);

    if (Array.isArray(raw.frames)) mergeFrames(raw.frames);
    if (Array.isArray(raw.profiles)) applyProfilesFromState(raw.profiles);

    markServerOnline();
    renderStatus();
    if (state.paused) updatePauseHint();
    else scheduleFramesRender();
  }

  async function refreshState(options = {}) {
    if (state.server.fetching) return state.server.online;
    state.server.fetching = true;
    const wasOffline = !state.server.online;
    try {
      const data = await api("/api/state");
      if (!data || typeof data !== "object") throw new Error("некорректный ответ /api/state");
      state.server.online = true;
      state.server.checked = true;
      state.server.offlineToastShown = false;
      applyState(data);
      if (wasOffline) {
        kickWs();
        updateWallpaperPreview(true);
        if (!state.metaLoaded) loadMeta();
      }
      return true;
    } catch (err) {
      state.server.online = false;
      state.server.checked = true;
      renderStatus();
      renderServerChip();
      showOffline(true, err.message);
      if (!wasOffline && !state.server.offlineToastShown && !options.silent) {
        toast("Сервер не отвечает: " + err.message, "error", 6000);
        state.server.offlineToastShown = true;
      }
      if (options.manual) toast("Сервер всё ещё недоступен: " + err.message, "error", 5000);
      return false;
    } finally {
      state.server.fetching = false;
      schedulePoll();
    }
  }

  function schedulePoll() {
    clearTimeout(state.pollTimer);
    const delay = document.hidden ? 15000 : state.server.online ? 4000 : 3000;
    state.pollTimer = setTimeout(() => {
      state.pollTimer = null;
      refreshState({ silent: true });
    }, delay);
  }

  async function changeConnection(action) {
    if (state.connectPending) return;
    state.connectPending = action;
    renderStatus();
    try {
      const response =
        action === "connect"
          ? await api("/api/connect", { method: "POST", body: {} })
          : await api("/api/disconnect", { method: "POST" });
      if (response && response.ok === false) throw new Error("сервер вернул ok=false");
      toast(
        action === "connect" ? "Watch запущен — сервер ищет устройство" : "Отключено, watch остановлен",
        "success"
      );
      await refreshState({ silent: true });
    } catch (err) {
      toast(
        (action === "connect" ? "Не удалось подключиться: " : "Не удалось отключиться: ") + err.message,
        "error",
        6000
      );
    } finally {
      state.connectPending = null;
      renderStatus();
    }
  }

  /* ------------------------------------------------------------------ */
  /* WebSocket                                                           */
  /* ------------------------------------------------------------------ */

  function renderWs() {
    if (!dom.wsBadge) return;
    dom.wsBadge.classList.remove("is-live", "is-reconnecting", "is-off");
    const status = state.ws.status;
    let text = "WS: ожидание";
    if (status === "live") {
      dom.wsBadge.classList.add("is-live");
      text = "WS: live";
    } else if (status === "connecting") {
      dom.wsBadge.classList.add("is-reconnecting");
      text = "WS: подключение…";
    } else if (status === "reconnecting") {
      dom.wsBadge.classList.add("is-reconnecting");
      const seconds = Math.max(0, Math.ceil((state.ws.retryAt - Date.now()) / 1000));
      const pollHint = state.ws.attempts > 2 ? " · API" : "";
      text = "WS: реконнект " + seconds + " с" + pollHint;
    } else if (status === "unavailable") {
      dom.wsBadge.classList.add("is-off");
      text = "WS: нет соединения";
    } else {
      dom.wsBadge.classList.add("is-off");
    }
    dom.wsText.textContent = text;
    dom.wsBadge.title =
      "WebSocket /ws · попытка " +
      state.ws.attempts +
      " · при обрыве таблица обновляется через /api/state";
  }

  function wsConnect() {
    if (state.ws.manualClose) return;
    if (typeof WebSocket === "undefined" || !location.host) {
      state.ws.status = "unavailable";
      renderWs();
      return;
    }

    const previous = state.ws.socket;
    state.ws.socket = null;
    state.ws.generation += 1;
    const generation = state.ws.generation;
    if (previous) {
      try {
        previous.close();
      } catch (err) {
        /* ignore */
      }
    }

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const url = protocol + "//" + location.host + "/ws";
    state.ws.status = "connecting";
    state.ws.retryAt = 0;
    renderWs();

    let socket;
    try {
      socket = new WebSocket(url);
    } catch (err) {
      state.ws.status = "reconnecting";
      scheduleReconnect();
      return;
    }
    state.ws.socket = socket;

    const handshakeTimer = setTimeout(() => {
      if (generation !== state.ws.generation || state.ws.socket !== socket) return;
      if (socket.readyState === WebSocket.CONNECTING) {
        state.ws.socket = null;
        try {
          socket.close();
        } catch (err) {
          /* ignore */
        }
        scheduleReconnect();
      }
    }, 8000);

    socket.addEventListener("open", () => {
      clearTimeout(handshakeTimer);
      if (generation !== state.ws.generation) return;
      state.ws.status = "live";
      state.ws.everConnected = true;
      state.ws.attempts = 0;
      state.ws.retryAt = 0;
      clearTimeout(state.ws.retryTimer);
      clearInterval(state.ws.countdownTimer);
      state.ws.retryTimer = null;
      state.ws.countdownTimer = null;
      renderWs();
      refreshState({ silent: true });
    });

    socket.addEventListener("message", (event) => {
      if (generation !== state.ws.generation) return;
      handleWsMessage(event.data);
    });

    socket.addEventListener("close", () => {
      clearTimeout(handshakeTimer);
      if (generation !== state.ws.generation) return;
      if (state.ws.socket !== socket) return;
      state.ws.socket = null;
      scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      if (generation !== state.ws.generation) return;
      if (socket.readyState === WebSocket.CLOSED && state.ws.socket === socket) {
        state.ws.socket = null;
        scheduleReconnect();
      }
    });
  }

  function scheduleReconnect() {
    if (state.ws.manualClose) return;
    clearTimeout(state.ws.retryTimer);
    clearInterval(state.ws.countdownTimer);
    state.ws.status = "reconnecting";
    const attempt = Math.min(state.ws.attempts, 6);
    const base = Math.min(WS_MAX_DELAY_MS, 500 * Math.pow(2, attempt));
    const delay = Math.round(base * (0.8 + Math.random() * 0.4));
    state.ws.attempts = Math.min(state.ws.attempts + 1, 10);
    state.ws.retryAt = Date.now() + delay;
    renderWs();
    state.ws.countdownTimer = setInterval(renderWs, 500);
    state.ws.retryTimer = setTimeout(() => {
      clearInterval(state.ws.countdownTimer);
      state.ws.countdownTimer = null;
      state.ws.retryTimer = null;
      wsConnect();
    }, delay);
  }

  function kickWs() {
    if (state.ws.manualClose) return;
    const socket = state.ws.socket;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    clearTimeout(state.ws.retryTimer);
    clearInterval(state.ws.countdownTimer);
    state.ws.retryTimer = null;
    state.ws.countdownTimer = null;
    state.ws.attempts = 0;
    wsConnect();
  }

  function handleWsMessage(raw) {
    let message;
    try {
      message = JSON.parse(raw);
    } catch (err) {
      return;
    }
    if (!message || typeof message !== "object") return;
    if (message.type === "hello" || message.type === "state") {
      markServerOnline();
      if (message.state) applyState(message.state);
    } else if (message.type === "frame" && message.frame) {
      markServerOnline();
      ingestFrame(message.frame);
    }
  }

  /* ------------------------------------------------------------------ */
  /* frames                                                             */
  /* ------------------------------------------------------------------ */

  function mergeFrames(rawFrames) {
    const incoming = rawFrames.map(normalizeFrame).filter(Boolean);
    const seen = new Set();
    const merged = [];
    incoming.forEach((frame) => {
      const key = frameKey(frame);
      if (seen.has(key)) return;
      seen.add(key);
      merged.push(frame);
    });
    state.frames.forEach((frame) => {
      const key = frameKey(frame);
      if (seen.has(key)) return;
      seen.add(key);
      merged.push(frame);
    });
    merged.sort((a, b) => b.t - a.t || b.seq - a.seq);
    state.frames = merged.slice(0, MAX_FRAMES);
  }

  function ingestFrame(rawFrame) {
    const frame = normalizeFrame(rawFrame);
    if (!frame) return;
    const key = frameKey(frame);
    if (state.frames.some((existing) => frameKey(existing) === key)) return;
    state.frames.unshift(frame);
    if (state.frames.length > MAX_FRAMES) state.frames.length = MAX_FRAMES;
    state.connection.framesTotal += 1;
    updateFramesCounter();
    if (state.paused) updatePauseHint();
    else scheduleFramesRender();
  }

  function filteredFrames(source) {
    const filter = state.filterMsg;
    const query = searchKey(state.search);
    return source.filter((frame) => {
      if (filter === "other") {
        if (KNOWN_MSGS[frame.msg]) return false;
      } else if (filter !== "all") {
        if (frame.msg !== parseInt(filter, 16)) return false;
      }
      if (!query) return true;
      const haystack = searchKey(
        frame.payload_hex +
          " " +
          frame.raw_hex +
          " 0x" +
          hex4(frame.msg) +
          " " +
          (frame.msg_name || "")
      );
      return haystack.indexOf(query) !== -1;
    });
  }

  function frameRowHtml(frame) {
    const bytes = bytesFromHex(frame.payload_hex);
    const truncated = frameIsTruncated(frame, bytes);
    const msgClass = MSG_CLASSES[frame.msg] || "";
    const key = frameKey(frame);
    const open = state.expanded.has(key);
    const payload = bytes.length
      ? bytes.map((byte) => '<span class="byte">' + byte + "</span>").join(" ")
      : '<span class="payload-empty">пусто</span>';

    let html =
      '<tr class="frame-row ' +
      msgClass +
      (open ? " open" : "") +
      '" data-key="' +
      escapeAttr(key) +
      '" tabindex="0" aria-expanded="' +
      (open ? "true" : "false") +
      '">' +
      '<td class="time-cell" title="' +
      escapeAttr(timeTitle(frame.t)) +
      '">' +
      escapeHtml(formatTime(frame.t)) +
      "</td>" +
      '<td class="seq-cell">' +
      escapeHtml(padNumber(frame.seq, 3)) +
      "</td>" +
      '<td><span class="msg-badge ' +
      msgClass +
      '"><span class="msg-name">' +
      escapeHtml(displayMsgName(frame)) +
      '</span><span class="msg-hex">0x' +
      hex4(frame.msg) +
      "</span></span></td>" +
      '<td class="flags-cell">0x' +
      hex2(frame.flags) +
      "</td>" +
      '<td class="len-cell' +
      (truncated ? " is-trunc" : "") +
      '">' +
      bytes.length +
      '<span class="len-sep">/</span>' +
      frame.declared_len +
      "</td>" +
      '<td class="payload-cell">' +
      payload +
      "</td>" +
      "<td>" +
      (truncated
        ? '<span class="badge badge-warn" title="payload короче declared_len — кадр обрезан MTU">truncated</span>'
        : '<span class="muted">—</span>') +
      "</td></tr>";

    if (open) html += frameDetailHtml(frame, bytes);
    return html;
  }

  function frameDetailHtml(frame, bytes) {
    const rawBytes = bytesFromHex(frame.raw_hex);
    return (
      '<tr class="frame-detail-row"><td colspan="7"><div class="frame-detail">' +
      '<div class="detail-block"><h4>raw_hex <span>' +
      rawBytes.length +
      " байт</span></h4><pre class=\"hexdump\">" +
      escapeHtml(hexDump(rawBytes, 8)) +
      "</pre></div>" +
      '<div class="detail-block"><h4>payload по байтам <span>' +
      bytes.length +
      " / " +
      frame.declared_len +
      '</span></h4><pre class="hexdump">' +
      escapeHtml(hexDump(bytes, 8)) +
      "</pre></div>" +
      "</div></td></tr>"
    );
  }

  function renderFrames() {
    const source = state.paused && state.frozenFrames ? state.frozenFrames : state.frames;
    const list = filteredFrames(source);
    const knownKeys = new Set(state.frames.map(frameKey));
    Array.from(state.expanded).forEach((key) => {
      if (!knownKeys.has(key)) state.expanded.delete(key);
    });

    dom.framesBody.innerHTML = list.map(frameRowHtml).join("");
    dom.framesEmpty.hidden = list.length > 0;
    if (!list.length) {
      const hasFilter = state.filterMsg !== "all" || state.search.trim() !== "";
      dom.framesEmptyText.textContent = source.length === 0
        ? "Кадров пока нет. Подключитесь к устройству — поток появится здесь автоматически."
        : hasFilter
          ? "Под фильтр или поиск ничего не попало. Измените условия."
          : "Ожидание кадров…";
    }

    dom.framesCount.textContent = source.length + " / " + MAX_FRAMES;
    const hasFilter = state.filterMsg !== "all" || state.search.trim() !== "";
    dom.filterCount.textContent = hasFilter
      ? "показано " + list.length + " из " + source.length
      : "показано " + list.length;
    if (state.paused) updatePauseHint();
  }

  function scheduleFramesRender() {
    if (state.paused) {
      updatePauseHint();
      return;
    }
    state.render.dirty = true;
    if (state.render.rafPending || state.render.timer) return;
    state.render.rafPending = true;
    state.render.rafId = requestAnimationFrame(() => {
      state.render.rafPending = false;
      state.render.rafId = null;
      const wait = RENDER_INTERVAL_MS - (performance.now() - state.render.lastAt);
      if (wait > 0) {
        state.render.timer = setTimeout(() => {
          state.render.timer = null;
          flushFramesRender();
        }, wait);
      } else {
        flushFramesRender();
      }
    });
  }

  function flushFramesRender() {
    if (state.paused) {
      state.render.dirty = false;
      return;
    }
    state.render.dirty = false;
    state.render.lastAt = performance.now();
    renderFrames();
  }

  function cancelFramesRender() {
    if (state.render.rafPending && state.render.rafId != null) cancelAnimationFrame(state.render.rafId);
    if (state.render.timer) clearTimeout(state.render.timer);
    state.render.rafPending = false;
    state.render.rafId = null;
    state.render.timer = null;
    state.render.dirty = false;
  }

  function renderFramesNow() {
    cancelFramesRender();
    state.render.lastAt = performance.now();
    renderFrames();
  }

  function toggleFrame(key) {
    if (!key) return;
    if (state.expanded.has(key)) state.expanded.delete(key);
    else state.expanded.add(key);
    renderFramesNow();
    const row = Array.from(dom.framesBody.querySelectorAll("tr.frame-row")).find(
      (candidate) => candidate.dataset.key === key
    );
    if (row) row.focus({ preventScroll: true });
  }

  function togglePause() {
    if (!state.paused) {
      state.paused = true;
      state.frozenFrames = state.frames.slice();
      state.pauseStartTotal = state.connection.framesTotal;
      cancelFramesRender();
    } else {
      state.paused = false;
      state.frozenFrames = null;
      state.pauseStartTotal = 0;
      renderFramesNow();
    }
    updatePauseUi();
    if (state.paused) updatePauseHint();
  }

  function updatePauseUi() {
    dom.pauseBtn.classList.toggle("is-active", state.paused);
    dom.pauseBtn.setAttribute("aria-pressed", state.paused ? "true" : "false");
    dom.pauseIcon.textContent = state.paused ? "▶" : "❚❚";
    dom.pauseLabel.textContent = state.paused ? "Продолжить" : "Пауза";
    dom.pausedHint.hidden = !state.paused;
    dom.pausedHint.textContent = "лента заморожена";
  }

  function updatePauseHint() {
    if (!state.paused) {
      dom.pausedHint.hidden = true;
      return;
    }
    dom.pausedHint.hidden = false;
    const fresh = Math.max(0, state.connection.framesTotal - state.pauseStartTotal);
    dom.pausedHint.textContent =
      "лента заморожена" + (fresh > 0 ? " · +" + formatInt(fresh) + " новых" : "");
  }

  async function downloadCapture() {
    if (state.downloadPending) return;
    state.downloadPending = true;
    dom.downloadBtn.disabled = true;
    const original = dom.downloadBtn.textContent;
    dom.downloadBtn.textContent = "Скачивание…";
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      let response;
      try {
        response = await fetch("/api/capture/download", { signal: controller.signal });
      } finally {
        clearTimeout(timer);
      }
      if (!response.ok) {
        let detail = "";
        try {
          const data = await response.json();
          detail = extractApiDetail(data);
        } catch (err) {
          /* ignore */
        }
        throw new Error("HTTP " + response.status + (detail ? " — " + detail : ""));
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      let name = "";
      const starMatch = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(disposition);
      const plainMatch = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition);
      const rawName = starMatch && starMatch[1] ? starMatch[1] : plainMatch && plainMatch[1] ? plainMatch[1] : "";
      if (rawName) {
        const cleaned = rawName.trim().replace(/^"|"$/g, "");
        try {
          name = decodeURIComponent(cleaned);
        } catch (err) {
          name = cleaned;
        }
      }
      if (!name) name = baseName(state.connection.captureFile) || "pasito-capture.jsonl";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1200);
      toast("JSONL-захват скачан: " + name, "success");
    } catch (err) {
      const message = err && err.name === "AbortError" ? "таймаут скачивания" : err.message;
      toast("Не удалось скачать захват: " + message, "error", 6000);
    } finally {
      state.downloadPending = false;
      dom.downloadBtn.disabled = false;
      dom.downloadBtn.textContent = original;
    }
  }

  /* ------------------------------------------------------------------ */
  /* curve editor                                                       */
  /* ------------------------------------------------------------------ */

  function buildCurveInputs() {
    dom.curveInputs.innerHTML = state.curve
      .map(
        (value, index) =>
          '<label class="curve-field">' +
          '<span class="curve-no">Шаг ' +
          (index + 1) +
          "</span>" +
          '<span class="input-shell">' +
          '<input type="number" min="0" max="255" step="1" inputmode="numeric" data-point="' +
          index +
          '" value="' +
          value +
          '" aria-label="Шаг ' +
          (index + 1) +
          ', мощность в ваттах">' +
          '<span class="curve-unit">Вт</span>' +
          "</span></label>"
      )
      .join("");
  }

  function syncCurveInputs(force) {
    const inputs = dom.curveInputs.querySelectorAll("input[data-point]");
    inputs.forEach((input) => {
      const index = Number(input.dataset.point);
      if (!force && document.activeElement === input) return;
      input.value = String(state.curve[index]);
    });
  }

  function updateRangeWarnings() {
    const inputs = dom.curveInputs.querySelectorAll("input[data-point]");
    inputs.forEach((input) => {
      const index = Number(input.dataset.point);
      const value = state.curve[index];
      input.classList.toggle("warn", value < state.minW || value > state.maxW);
    });

    const bad = outOfRangeIndices();
    if (!bad.length) {
      dom.rangeWarning.hidden = true;
      dom.rangeWarning.textContent = "";
      return;
    }
    dom.rangeWarning.hidden = false;
    const steps = bad.map((index) => index + 1).join(", ");
    dom.rangeWarning.textContent =
      bad.length +
      " " +
      pluralRu(bad.length, ["точка", "точки", "точек"]) +
      " вне диапазона " +
      state.minW +
      "–" +
      state.maxW +
      " Вт (шаги " +
      steps +
      "). Устройство может отклонить такие значения.";
  }

  function renderActiveProfileChip() {
    if (!dom.activeProfileChip) return;
    if (!state.activeProfile) {
      dom.activeProfileChip.hidden = true;
      dom.activeProfileChip.innerHTML = "";
      dom.activeProfileChip.title = "";
      return;
    }
    dom.activeProfileChip.hidden = false;
    dom.activeProfileChip.innerHTML =
      escapeHtml(state.activeProfile.name) +
      (state.activeProfileDirty ? " <em>· изменена</em>" : "");
    dom.activeProfileChip.title = "Загружен профиль: " + state.activeProfile.name;
  }

  function afterCurveEdit(markDirty = true) {
    updateRangeWarnings();
    drawCurve();
    schedulePreviewUpdate();
    if (markDirty && state.activeProfile) {
      state.activeProfileDirty = true;
    }
    renderActiveProfileChip();
  }

  function setCurve(values, options = {}) {
    const normalized = normalizeValues(values);
    if (!normalized) {
      toast("Кривая должна содержать ровно " + POINTS + " значений", "error");
      return false;
    }
    state.curve = normalized;
    syncCurveInputs(true);
    updateRangeWarnings();
    drawCurve();
    schedulePreviewUpdate();

    if (options.profile) {
      state.selectedProfileId = options.profile.id;
      state.activeProfile = { id: options.profile.id, name: options.profile.name };
      state.activeProfileDirty = false;
      renderProfiles();
    } else if (state.activeProfile) {
      state.activeProfileDirty = true;
    }
    renderActiveProfileChip();
    return true;
  }

  function curveMetrics() {
    const width = Math.max(120, dom.curveCanvas.clientWidth || 320);
    const height = Math.max(120, dom.curveCanvas.clientHeight || 230);
    const padding = { left: 42, right: 14, top: 20, bottom: 34 };
    const maxValue = Math.max(100, state.maxW, ...state.curve);
    const step = maxValue <= 120 ? 20 : maxValue <= 250 ? 50 : 100;
    const yMax = Math.max(120, Math.ceil((maxValue + 8) / step) * step);
    return {
      width,
      height,
      padding,
      step,
      yMax,
      plotW: width - padding.left - padding.right,
      plotH: height - padding.top - padding.bottom,
    };
  }

  function chartX(metrics, index) {
    return metrics.padding.left + (index / (POINTS - 1)) * metrics.plotW;
  }

  function chartY(metrics, value) {
    return (
      metrics.padding.top +
      (1 - clamp(value, 0, metrics.yMax) / metrics.yMax) * metrics.plotH
    );
  }

  function drawCurve() {
    const canvas = dom.curveCanvas;
    if (!canvas || !canvas.isConnected) return;
    const metrics = curveMetrics();
    if (metrics.width < 60 || metrics.height < 60) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pixelWidth = Math.round(metrics.width * dpr);
    const pixelHeight = Math.round(metrics.height * dpr);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, metrics.width, metrics.height);

    const monoFont = '10px "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace';
    context.font = monoFont;
    context.textBaseline = "middle";

    const tickCount = Math.round(metrics.yMax / metrics.step);
    for (let tick = 0; tick <= tickCount; tick += 1) {
      const value = tick * metrics.step;
      const y = chartY(metrics, value);
      context.strokeStyle = tick === 0 ? "rgba(45,212,191,0.24)" : "rgba(45,212,191,0.075)";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(metrics.padding.left, y + 0.5);
      context.lineTo(metrics.width - metrics.padding.right, y + 0.5);
      context.stroke();
      context.fillStyle = "#6f8a83";
      context.textAlign = "right";
      context.fillText(String(value), metrics.padding.left - 8, y);
    }

    for (let index = 0; index < POINTS; index += 1) {
      const x = chartX(metrics, index);
      context.strokeStyle = "rgba(45,212,191,0.05)";
      context.beginPath();
      context.moveTo(x + 0.5, metrics.padding.top);
      context.lineTo(x + 0.5, metrics.height - metrics.padding.bottom);
      context.stroke();
      context.fillStyle = "#6f8a83";
      context.textAlign = "center";
      context.fillText(String(index + 1), x, metrics.height - metrics.padding.bottom + 12);
    }

    if (metrics.yMax > 100 && state.maxW >= 100) {
      const y100 = chartY(metrics, 100);
      context.save();
      context.strokeStyle = "rgba(251,191,36,0.28)";
      context.setLineDash([4, 4]);
      context.beginPath();
      context.moveTo(metrics.padding.left, y100 + 0.5);
      context.lineTo(metrics.width - metrics.padding.right, y100 + 0.5);
      context.stroke();
      context.setLineDash([]);
      context.fillStyle = "rgba(251,191,36,0.7)";
      context.textAlign = "left";
      context.fillText("100 Вт", metrics.padding.left + 3, y100 - 7);
      context.restore();
    }

    context.fillStyle = "#8aa39c";
    context.textAlign = "left";
    context.fillText("Вт", 8, 11);
    context.textAlign = "right";
    context.fillText("шаг · 0,5 с", metrics.width - 4, metrics.height - 7);

    const gradient = context.createLinearGradient(
      0,
      metrics.padding.top,
      0,
      metrics.height - metrics.padding.bottom
    );
    gradient.addColorStop(0, "rgba(45,212,191,0.28)");
    gradient.addColorStop(1, "rgba(45,212,191,0.015)");

    context.beginPath();
    state.curve.forEach((value, index) => {
      const x = chartX(metrics, index);
      const y = chartY(metrics, value);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.lineTo(chartX(metrics, POINTS - 1), metrics.height - metrics.padding.bottom);
    context.lineTo(chartX(metrics, 0), metrics.height - metrics.padding.bottom);
    context.closePath();
    context.fillStyle = gradient;
    context.fill();

    context.beginPath();
    state.curve.forEach((value, index) => {
      const x = chartX(metrics, index);
      const y = chartY(metrics, value);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = "#2dd4bf";
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.shadowColor = "rgba(45,212,191,0.45)";
    context.shadowBlur = 8;
    context.stroke();
    context.shadowBlur = 0;

    state.curve.forEach((value, index) => {
      const x = chartX(metrics, index);
      const y = chartY(metrics, value);
      const outside = value < state.minW || value > state.maxW;
      const active = chart.dragging === index;
      context.beginPath();
      context.arc(x, y, active ? 6 : 4.2, 0, Math.PI * 2);
      context.fillStyle = "#08120f";
      context.fill();
      context.strokeStyle = outside ? "#f87171" : "#2dd4bf";
      context.lineWidth = 2;
      context.stroke();

      if (active) {
        context.beginPath();
        context.arc(x, y, 10, 0, Math.PI * 2);
        context.strokeStyle = "rgba(45,212,191,0.35)";
        context.lineWidth = 1.5;
        context.stroke();
      }

      context.font = monoFont;
      context.textAlign = "center";
      context.fillStyle = outside ? "#fca5a5" : "#c7e8e1";
      const nearTop = y - 12 < metrics.padding.top + 5;
      context.fillText(String(value), x, nearTop ? y + 16 : y - 12);
    });

    chart.geom = metrics;
  }

  function nearestPointIndex(clientX, clientY) {
    const metrics = chart.geom;
    if (!metrics) return -1;
    const rect = dom.curveCanvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let best = -1;
    let bestDistance = Infinity;
    for (let index = 0; index < POINTS; index += 1) {
      const dx = x - chartX(metrics, index);
      const dy = y - chartY(metrics, state.curve[index]);
      const distance = Math.hypot(dx, dy);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    }
    return bestDistance <= 27 ? best : -1;
  }

  function updateCurveFromPointer(event) {
    const metrics = chart.geom;
    if (!metrics || chart.dragging < 0) return;
    const rect = dom.curveCanvas.getBoundingClientRect();
    const y = event.clientY - rect.top;
    const ratio = clamp((y - metrics.padding.top) / metrics.plotH, 0, 1);
    const value = clamp(Math.round((1 - ratio) * metrics.yMax), 0, 255);
    if (state.curve[chart.dragging] === value) return;
    state.curve[chart.dragging] = value;
    syncCurveInputs(true);
    afterCurveEdit(true);
  }

  function onCanvasPointerDown(event) {
    if (!chart.geom) drawCurve();
    const index = nearestPointIndex(event.clientX, event.clientY);
    if (index < 0) return;
    event.preventDefault();
    chart.dragging = index;
    chart.pointerId = event.pointerId;
    dom.curveCanvas.classList.add("dragging");
    try {
      dom.curveCanvas.setPointerCapture(event.pointerId);
    } catch (err) {
      /* ignore */
    }
    updateCurveFromPointer(event);
  }

  function onCanvasPointerMove(event) {
    if (chart.dragging < 0) return;
    if (chart.pointerId != null && event.pointerId !== chart.pointerId) return;
    event.preventDefault();
    updateCurveFromPointer(event);
  }

  function endCanvasDrag(event) {
    if (chart.dragging < 0) return;
    if (event && chart.pointerId != null && event.pointerId !== chart.pointerId) return;
    try {
      if (chart.pointerId != null && dom.curveCanvas.hasPointerCapture(chart.pointerId)) {
        dom.curveCanvas.releasePointerCapture(chart.pointerId);
      }
    } catch (err) {
      /* ignore */
    }
    chart.dragging = -1;
    chart.pointerId = null;
    dom.curveCanvas.classList.remove("dragging");
    drawCurve();
  }

  function randomCurve() {
    state.curve = Array.from({ length: POINTS }, () =>
      Math.round(state.minW + Math.random() * (state.maxW - state.minW))
    );
    syncCurveInputs(true);
    afterCurveEdit();
  }

  function equalCurve() {
    const raw = String(dom.equalN.value).replace(",", ".").trim();
    const parsed = Number(raw);
    if (raw === "" || !Number.isFinite(parsed)) {
      toast("Введите N числом", "warn");
      dom.equalN.focus();
      return;
    }
    const value = Math.round(parsed);
    if (value < state.minW || value > state.maxW) {
      toast(
        "N=" + value + " вне диапазона " + state.minW + "–" + state.maxW + " Вт — устройство может отклонить",
        "warn",
        6000
      );
    }
    state.curve = new Array(POINTS).fill(clamp(value, 0, 255));
    syncCurveInputs(true);
    afterCurveEdit();
  }

  function loadProfile(profile, options = {}) {
    if (!profile || !profile.values) {
      toast("У профиля некорректные values — загрузить нельзя", "error");
      return;
    }
    if (!setCurve(profile.values, { profile })) return;
    if (!options.silent) toast("Профиль «" + profile.name + "» загружен в редактор", "success");
  }

  function loadSelectedProfile() {
    let profile = state.profiles.find((item) => item.id === state.selectedProfileId);
    if (!profile && state.activeProfile) {
      profile = state.profiles.find((item) => item.id === state.activeProfile.id);
    }
    if (!profile) {
      toast("Сначала выберите профиль в списке ниже", "warn");
      if (dom.profileList) dom.profileList.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    loadProfile(profile);
  }

  /* ------------------------------------------------------------------ */
  /* send                                                                */
  /* ------------------------------------------------------------------ */

  function renderSend() {
    dom.sendBtn.disabled = state.send.pending;
    dom.sendBtn.textContent = state.send.pending ? "Отправка…" : "Отправить на Pasito";
    const result = state.send.result;
    if (!result) {
      dom.sendResult.hidden = true;
      dom.sendResult.innerHTML = "";
      return;
    }
    dom.sendResult.hidden = false;
    dom.sendResult.className = "send-result " + (result.ok ? "is-ok" : "is-err");
    dom.sendResult.innerHTML =
      '<div class="result-row">' +
      '<span class="result-key">ok</span><span class="pill ' +
      (result.ok ? "on" : "off") +
      '">' +
      (result.ok ? "true" : "false") +
      "</span>" +
      '<span class="result-key">att_ack</span><span class="pill ' +
      (result.attAck ? "on" : "off") +
      '">' +
      (result.attAck ? "true" : "false") +
      "</span>" +
      '<span class="result-time">' +
      escapeHtml(result.time || "") +
      "</span></div>" +
      (result.detail ? '<div class="result-detail">' + escapeHtml(result.detail) + "</div>" : "") +
      (result.raw
        ? '<div class="result-row"><span class="result-key">raw</span><code class="result-raw">' +
          escapeHtml(result.raw) +
          "</code></div>"
        : "") +
      (result.readBack
        ? '<div class="result-row"><span class="result-key">read_back</span><code class="result-raw">' +
          escapeHtml(result.readBack) +
          "</code></div>"
        : "");
  }

  async function sendCurve() {
    if (state.send.pending) return;
    const bad = outOfRangeIndices();
    if (bad.length) {
      toast(
        bad.length +
          " " +
          pluralRu(bad.length, ["точка", "точки", "точек"]) +
          " вне " +
          state.minW +
          "–" +
          state.maxW +
          " Вт — отправляем, но устройство может отклонить",
        "warn",
        6000
      );
    }
    state.send.pending = true;
    renderSend();
    try {
      const response = await api("/api/send", {
        method: "POST",
        body: { values: state.curve.slice() },
      });
      const ok = Boolean(response && response.ok);
      state.send.result = {
        ok: ok,
        attAck: Boolean(response && response.att_ack),
        detail: response && response.detail != null ? String(response.detail) : "",
        raw: response && response.raw != null ? String(response.raw) : "",
        readBack: response && response.read_back != null ? String(response.read_back) : "",
        time: formatClock(new Date()),
      };
      if (ok) {
        toast(
          state.send.result.attAck ? "Профиль отправлен, ATT ACK получен" : "Профиль отправлен без ATT ACK",
          "success"
        );
      } else {
        toast("Отправка не подтверждена устройством", "warn", 6000);
      }
    } catch (err) {
      state.send.result = {
        ok: false,
        attAck: false,
        detail: err.message,
        raw: "",
        readBack: "",
        time: formatClock(new Date()),
      };
      toast("Ошибка отправки: " + err.message, "error", 6000);
    } finally {
      state.send.pending = false;
      renderSend();
    }
  }

  /* ------------------------------------------------------------------ */
  /* profiles                                                           */
  /* ------------------------------------------------------------------ */

  function sanitizeProfile(raw) {
    if (!raw || typeof raw !== "object" || raw.id == null) return null;
    const values =
      Array.isArray(raw.values) && raw.values.length === POINTS
        ? raw.values.map((value) => clamp(Math.round(Number(value) || 0), 0, 255))
        : null;
    return {
      id: String(raw.id),
      name: String(raw.name || "Без названия"),
      note: String(raw.note || ""),
      created: String(raw.created || ""),
      values: values,
    };
  }

  function sanitizeProfiles(rawList) {
    if (!Array.isArray(rawList)) return [];
    return rawList.map(sanitizeProfile).filter(Boolean);
  }

  function profilesSignature(profiles) {
    return JSON.stringify(
      profiles.map((profile) => [profile.id, profile.name, profile.note, profile.values])
    );
  }

  function applyProfilesFromState(rawList) {
    const incoming = sanitizeProfiles(rawList);
    if (profilesSignature(incoming) === profilesSignature(state.profiles)) return;
    state.profiles = incoming;
    if (state.selectedProfileId && !incoming.some((item) => item.id === state.selectedProfileId)) {
      state.selectedProfileId = null;
    }
    if (state.renamingId && !incoming.some((item) => item.id === state.renamingId)) {
      state.renamingId = null;
    }
    renderProfiles();
  }

  function renderProfiles() {
    if (state.renamingId && !state.profiles.some((item) => item.id === state.renamingId)) {
      state.renamingId = null;
    }

    dom.profileList.innerHTML = state.profiles
      .map((profile) => {
        const selected = profile.id === state.selectedProfileId;
        if (state.renamingId === profile.id) {
          return (
            '<li class="profile-item' +
            (selected ? " is-selected" : "") +
            '" data-id="' +
            escapeAttr(profile.id) +
            '"><form class="rename-form" data-id="' +
            escapeAttr(profile.id) +
            '">' +
            '<input class="rename-input" name="name" type="text" maxlength="80" value="' +
            escapeAttr(profile.name) +
            '" aria-label="Новое имя профиля">' +
            '<button class="btn btn-primary btn-sm" type="submit">OK</button>' +
            '<button class="icon-btn" type="button" data-action="cancel-rename" aria-label="Отмена">×</button>' +
            "</form></li>"
          );
        }
        const values = profile.values || [];
        const spark = values
          .map(
            (value) =>
              '<span style="height:' +
              Math.max(2, Math.round((clamp(value, 0, state.maxW) / state.maxW) * 16)) +
              'px"></span>'
          )
          .join("");
        const created = profile.created ? formatDate(profile.created) : "";
        const titleParts = values.length ? values.join(" ") + " Вт" : "values недоступны";
        return (
          '<li class="profile-item' +
          (selected ? " is-selected" : "") +
          '" data-id="' +
          escapeAttr(profile.id) +
          '"><div class="profile-main">' +
          '<button class="profile-load" type="button" data-action="load" title="Загрузить в редактор · ' +
          escapeAttr(titleParts) +
          '"' +
          (profile.values ? "" : " disabled") +
          ">" +
          '<span class="profile-name-row"><span class="profile-name">' +
          escapeHtml(profile.name) +
          '</span><span class="profile-points">' +
          (profile.values ? POINTS : "!") +
          "</span></span>" +
          '<span class="profile-sub">' +
          escapeHtml(created || "без даты") +
          (profile.note ? " · " + escapeHtml(profile.note) : "") +
          "</span>" +
          '<span class="profile-spark" aria-hidden="true">' +
          spark +
          "</span></button>" +
          '<div class="profile-actions">' +
          '<button class="icon-btn" type="button" data-action="rename" title="Переименовать" aria-label="Переименовать профиль ' +
          escapeAttr(profile.name) +
          '">✎</button>' +
          '<button class="icon-btn danger" type="button" data-action="delete" title="Удалить" aria-label="Удалить профиль ' +
          escapeAttr(profile.name) +
          '">✕</button>' +
          "</div></div></li>"
        );
      })
      .join("");

    dom.profilesCount.textContent = String(state.profiles.length);
    dom.profilesEmpty.hidden = state.profiles.length > 0;

    if (state.renamingId) {
      const input = dom.profileList.querySelector(".rename-input");
      if (input) {
        input.focus();
        input.select();
      }
    }
  }

  function setProfiles(profiles, force = false) {
    const incoming = sanitizeProfiles(profiles);
    if (!force && profilesSignature(incoming) === profilesSignature(state.profiles)) return;
    state.profiles = incoming;
    if (state.selectedProfileId && !incoming.some((item) => item.id === state.selectedProfileId)) {
      state.selectedProfileId = null;
    }
    if (state.renamingId && !incoming.some((item) => item.id === state.renamingId)) {
      state.renamingId = null;
    }
    renderProfiles();
  }

  async function loadProfiles(options = {}) {
    if (state.profileBusy) return;
    state.profileBusy = true;
    if (dom.profilesRefresh) dom.profilesRefresh.disabled = true;
    try {
      const list = await api("/api/profiles");
      setProfiles(list, true);
      if (!options.silent) toast("Список профилей обновлён", "success");
    } catch (err) {
      if (!options.silent) toast("Не удалось загрузить профили: " + err.message, "error", 6000);
    } finally {
      state.profileBusy = false;
      if (dom.profilesRefresh) dom.profilesRefresh.disabled = false;
    }
  }

  async function saveProfile(event) {
    event.preventDefault();
    if (state.profileSaving) return;
    const name = dom.profileName.value.trim();
    if (!name) {
      toast("Введите имя профиля", "warn");
      dom.profileName.focus();
      return;
    }
    const note = dom.profileNote.value.trim();
    state.profileSaving = true;
    dom.saveProfileBtn.disabled = true;
    dom.saveProfileBtn.textContent = "Сохранение…";
    try {
      const created = await api("/api/profiles", {
        method: "POST",
        body: { name: name, values: state.curve.slice(), note: note },
      });
      const profile = sanitizeProfile(created);
      if (!profile) throw new Error("сервер вернул некорректный профиль");
      state.profiles.push(profile);
      state.selectedProfileId = profile.id;
      state.activeProfile = { id: profile.id, name: profile.name };
      state.activeProfileDirty = false;
      renderProfiles();
      renderActiveProfileChip();
      dom.profileName.value = "";
      dom.profileNote.value = "";
      toast("Профиль «" + profile.name + "» сохранён", "success");
    } catch (err) {
      toast("Не удалось сохранить профиль: " + err.message, "error", 6000);
    } finally {
      state.profileSaving = false;
      dom.saveProfileBtn.disabled = false;
      dom.saveProfileBtn.textContent = "Сохранить кривую";
    }
  }

  async function renameProfile(id, name) {
    const profile = state.profiles.find((item) => item.id === id);
    if (!profile) return;
    const newName = name.trim();
    if (!newName) {
      toast("Имя профиля не может быть пустым", "warn");
      return;
    }
    if (!profile.values) {
      toast("У профиля некорректные values — переименование недоступно", "error");
      return;
    }
    if (state.profileBusy) return;
    state.profileBusy = true;
    try {
      const updated = await api("/api/profiles/" + encodeURIComponent(id), {
        method: "PUT",
        body: { name: newName, values: profile.values, note: profile.note },
      });
      const normalized = sanitizeProfile(updated) || Object.assign({}, profile, { name: newName });
      state.profiles = state.profiles.map((item) => (item.id === id ? normalized : item));
      state.renamingId = null;
      if (state.activeProfile && state.activeProfile.id === id) {
        state.activeProfile.name = normalized.name;
        renderActiveProfileChip();
      }
      renderProfiles();
      toast("Профиль переименован", "success");
    } catch (err) {
      toast("Не удалось переименовать профиль: " + err.message, "error", 6000);
    } finally {
      state.profileBusy = false;
    }
  }

  async function deleteProfile(id) {
    const profile = state.profiles.find((item) => item.id === id);
    if (!profile) return;
    if (!window.confirm("Удалить профиль «" + profile.name + "»?")) return;
    if (state.profileBusy) return;
    state.profileBusy = true;
    try {
      await api("/api/profiles/" + encodeURIComponent(id), { method: "DELETE" });
      state.profiles = state.profiles.filter((item) => item.id !== id);
      if (state.selectedProfileId === id) state.selectedProfileId = null;
      if (state.activeProfile && state.activeProfile.id === id) {
        state.activeProfile = null;
        state.activeProfileDirty = false;
        renderActiveProfileChip();
      }
      if (state.renamingId === id) state.renamingId = null;
      renderProfiles();
      toast("Профиль удалён", "success");
    } catch (err) {
      toast("Не удалось удалить профиль: " + err.message, "error", 6000);
    } finally {
      state.profileBusy = false;
    }
  }

  /* ------------------------------------------------------------------ */
  /* wallpaper                                                          */
  /* ------------------------------------------------------------------ */

  function renderThemeButtons() {
    const buttons = dom.themeRow.querySelectorAll("[data-theme]");
    buttons.forEach((button) => {
      const active = button.dataset.theme === state.wallpaper.theme;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.disabled = Boolean(state.wallpaper.pending);
    });
  }

  function applyThemesFromMeta(meta) {
    let available =
      Array.isArray(meta.themes) && meta.themes.length ? new Set(meta.themes) : new Set(THEME_IDS);
    if (!THEME_IDS.some((theme) => available.has(theme))) available = new Set(THEME_IDS);
    const buttons = Array.from(dom.themeRow.querySelectorAll("[data-theme]"));
    buttons.forEach((button) => {
      const allowed = available.has(button.dataset.theme);
      button.hidden = !allowed;
      button.disabled = !allowed;
    });
    if (!available.has(state.wallpaper.theme)) {
      const first = buttons.find((button) => !button.hidden);
      if (first) state.wallpaper.theme = first.dataset.theme;
    }
    renderThemeButtons();
    updateWallpaperPreview(true);
  }

  function setTheme(theme) {
    if (THEME_IDS.indexOf(theme) === -1) return;
    state.wallpaper.theme = theme;
    renderThemeButtons();
    updateWallpaperPreview(true);
  }

  function updateRangeUi() {
    dom.rangeChip.textContent = state.minW + "–" + state.maxW + " Вт";
    dom.equalN.min = String(state.minW);
    dom.equalN.max = String(state.maxW);
    updateRangeWarnings();
  }

  function schedulePreviewUpdate() {
    clearTimeout(state.previewDebounce);
    state.previewDebounce = setTimeout(() => {
      state.previewDebounce = null;
      updateWallpaperPreview(false);
    }, PREVIEW_DEBOUNCE_MS);
  }

  function updateWallpaperPreview(force) {
    const theme = state.wallpaper.theme;
    const values = state.curve.join(",");
    const key = theme + "|" + values;
    if (!force && key === state.wallpaper.previewKey) return;
    state.wallpaper.previewKey = key;
    state.wallpaper.previewToken += 1;
    const token = state.wallpaper.previewToken;
    const url =
      "/api/wallpaper/preview.png?theme=" +
      encodeURIComponent(theme) +
      "&values=" +
      encodeURIComponent(values) +
      "&_=" +
      Date.now();

    dom.previewWrap.classList.remove("is-loaded", "is-loading");
    dom.previewWrap.classList.add("is-loading");
    dom.previewLoading.hidden = false;
    dom.previewError.hidden = true;
    dom.wallpaperPreview.alt =
      "Предпросмотр обоев · тема " + theme + " · " + state.curve.join(", ") + " Вт";

    dom.wallpaperPreview.onload = () => {
      if (token !== state.wallpaper.previewToken) return;
      dom.previewWrap.classList.remove("is-loading");
      dom.previewWrap.classList.add("is-loaded");
      dom.previewLoading.hidden = true;
      dom.previewError.hidden = true;
    };
    dom.wallpaperPreview.onerror = () => {
      if (token !== state.wallpaper.previewToken) return;
      dom.previewWrap.classList.remove("is-loading", "is-loaded");
      dom.previewLoading.hidden = true;
      dom.previewError.hidden = false;
      dom.previewError.textContent = state.server.online
        ? "Не удалось получить превью — проверьте /api/wallpaper/preview.png"
        : "Сервер не отвечает — превью недоступно";
    };
    dom.wallpaperPreview.src = url;
  }

  function renderWallpaperControls() {
    const pending = state.wallpaper.pending;
    dom.wallpaperSave.disabled = Boolean(pending);
    dom.wallpaperApply.disabled = Boolean(pending);
    dom.wallpaperSave.textContent = pending === "save" ? "Сохранение…" : "Сохранить обои";
    dom.wallpaperApply.textContent = pending === "apply" ? "Применение…" : "Поставить на рабочий стол";
    renderThemeButtons();
  }

  function renderWallpaperResult() {
    const result = state.wallpaper.result;
    if (!result) {
      dom.wallpaperResult.hidden = true;
      dom.wallpaperResult.innerHTML = "";
      return;
    }
    dom.wallpaperResult.hidden = false;
    dom.wallpaperResult.className = "wallpaper-result " + (result.error ? "is-err" : "is-ok");
    dom.wallpaperResult.innerHTML =
      '<div class="result-row"><span class="result-key">файл</span><code class="result-path">' +
      escapeHtml(result.path || "—") +
      "</code></div>" +
      '<div class="result-row"><span class="result-key">применено</span><span class="pill ' +
      (result.applied ? "on" : "off") +
      '">' +
      (result.applied ? "true" : "false") +
      "</span>" +
      (result.time ? '<span class="result-time">' + escapeHtml(result.time) + "</span>" : "") +
      "</div>" +
      (result.detail ? '<div class="result-detail">' + escapeHtml(result.detail) + "</div>" : "");
  }

  async function saveWallpaper(apply) {
    if (state.wallpaper.pending) return;
    state.wallpaper.pending = apply ? "apply" : "save";
    renderWallpaperControls();
    try {
      const response = await api("/api/wallpaper", {
        method: "POST",
        body: {
          values: state.curve.slice(),
          theme: state.wallpaper.theme,
          apply: Boolean(apply),
        },
      });
      state.wallpaper.result = {
        error: false,
        path: response && response.path != null ? String(response.path) : "",
        applied: Boolean(response && response.applied),
        detail: response && response.detail != null ? String(response.detail) : "",
        time: formatClock(new Date()),
      };
      if (apply && !state.wallpaper.result.applied) {
        toast(
          "Обои сохранены, но применить не удалось" +
            (state.wallpaper.result.detail ? ": " + state.wallpaper.result.detail : ""),
          "warn",
          6500
        );
      } else {
        toast(apply ? "Обои применены" : "Обои сохранены", "success");
      }
    } catch (err) {
      state.wallpaper.result = {
        error: true,
        path: "",
        applied: false,
        detail: err.message,
        time: formatClock(new Date()),
      };
      toast("Не удалось сохранить обои: " + err.message, "error", 6000);
    } finally {
      state.wallpaper.pending = null;
      renderWallpaperControls();
      renderWallpaperResult();
    }
  }

  /* ------------------------------------------------------------------ */
  /* meta                                                               */
  /* ------------------------------------------------------------------ */

  async function loadMeta() {
    try {
      const meta = await api("/api/meta");
      if (!meta || typeof meta !== "object") return;
      state.meta = meta;
      state.metaLoaded = true;
      const constants = meta.constants || {};
      const minW = Number(constants.min_w);
      const maxW = Number(constants.max_w);
      if (Number.isFinite(minW) && minW >= 0) state.minW = Math.trunc(minW);
      if (Number.isFinite(maxW) && maxW >= state.minW) state.maxW = Math.trunc(maxW);
      const version = String(meta.version || "0.1.0").replace(/^v/i, "");
      dom.versionLabel.textContent = "v" + version;
      dom.footerVersion.textContent = "v" + version;
      updateRangeUi();
      applyThemesFromMeta(meta);
    } catch (err) {
      /* /api/meta is optional; the dashboard keeps its defaults. */
    }
  }

  /* ------------------------------------------------------------------ */
  /* events & init                                                      */
  /* ------------------------------------------------------------------ */

  function bindEvents() {
    dom.connectBtn.addEventListener("click", () => changeConnection("connect"));
    dom.disconnectBtn.addEventListener("click", () => changeConnection("disconnect"));
    dom.offlineRetry.addEventListener("click", async () => {
      dom.offlineRetry.disabled = true;
      const ok = await refreshState({ manual: true });
      dom.offlineRetry.disabled = false;
      if (ok) toast("Сервер на связи", "success");
    });

    dom.pauseBtn.addEventListener("click", togglePause);
    dom.downloadBtn.addEventListener("click", downloadCapture);

    dom.msgFilter.addEventListener("change", () => {
      state.filterMsg = dom.msgFilter.value;
      renderFramesNow();
    });
    dom.hexSearch.addEventListener("input", () => {
      state.search = dom.hexSearch.value;
      clearTimeout(state.searchDebounce);
      state.searchDebounce = setTimeout(renderFramesNow, 120);
    });

    dom.framesBody.addEventListener("click", (event) => {
      const row = event.target.closest("tr.frame-row");
      if (!row) return;
      toggleFrame(row.dataset.key);
    });
    dom.framesBody.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("tr.frame-row");
      if (!row) return;
      event.preventDefault();
      toggleFrame(row.dataset.key);
    });

    dom.curveInputs.addEventListener("input", (event) => {
      const input = event.target.closest("input[data-point]");
      if (!input) return;
      const index = Number(input.dataset.point);
      const raw = String(input.value).replace(",", ".").trim();
      if (raw === "" || !Number.isFinite(Number(raw))) {
        input.classList.add("warn");
        return;
      }
      state.curve[index] = clamp(Math.round(Number(raw)), 0, 255);
      afterCurveEdit();
    });
    dom.curveInputs.addEventListener("focusout", (event) => {
      const input = event.target.closest("input[data-point]");
      if (!input) return;
      const index = Number(input.dataset.point);
      const raw = String(input.value).replace(",", ".").trim();
      const parsed = Number(raw);
      if (raw !== "" && Number.isFinite(parsed)) {
        state.curve[index] = clamp(Math.round(parsed), 0, 255);
      }
      input.value = String(state.curve[index]);
      afterCurveEdit();
    });

    dom.randomBtn.addEventListener("click", randomCurve);
    dom.equalBtn.addEventListener("click", equalCurve);
    dom.equalN.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        equalCurve();
      }
    });
    dom.fromProfileBtn.addEventListener("click", loadSelectedProfile);
    dom.sendBtn.addEventListener("click", sendCurve);

    dom.saveProfileForm.addEventListener("submit", saveProfile);
    dom.profilesRefresh.addEventListener("click", () => loadProfiles());

    dom.profileList.addEventListener("click", (event) => {
      const actionButton = event.target.closest("[data-action]");
      const item = event.target.closest(".profile-item");
      if (!actionButton || !item) return;
      const id = item.dataset.id;
      const action = actionButton.dataset.action;
      if (action === "load") {
        const profile = state.profiles.find((candidate) => candidate.id === id);
        if (profile) loadProfile(profile);
      } else if (action === "rename") {
        state.renamingId = id;
        renderProfiles();
      } else if (action === "cancel-rename") {
        state.renamingId = null;
        renderProfiles();
      } else if (action === "delete") {
        deleteProfile(id);
      }
    });
    dom.profileList.addEventListener("submit", (event) => {
      const form = event.target.closest("form.rename-form");
      if (!form) return;
      event.preventDefault();
      const input = form.querySelector(".rename-input");
      if (!input) return;
      renameProfile(form.dataset.id, input.value);
    });

    dom.themeRow.addEventListener("click", (event) => {
      const button = event.target.closest("[data-theme]");
      if (!button) return;
      setTheme(button.dataset.theme);
    });
    dom.wallpaperSave.addEventListener("click", () => saveWallpaper(false));
    dom.wallpaperApply.addEventListener("click", () => saveWallpaper(true));

    dom.curveCanvas.addEventListener("pointerdown", onCanvasPointerDown);
    dom.curveCanvas.addEventListener("pointermove", onCanvasPointerMove);
    dom.curveCanvas.addEventListener("pointerup", endCanvasDrag);
    dom.curveCanvas.addEventListener("pointercancel", endCanvasDrag);
    window.addEventListener("pointerup", endCanvasDrag);
    window.addEventListener("pointercancel", endCanvasDrag);

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(drawCurve, 100);
    });
    if (typeof ResizeObserver !== "undefined") {
      let observerTimer = null;
      const observer = new ResizeObserver(() => {
        clearTimeout(observerTimer);
        observerTimer = setTimeout(drawCurve, 80);
      });
      observer.observe(dom.curveCanvas.parentElement);
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshState({ silent: true });
    });
    window.addEventListener("beforeunload", () => {
      state.ws.manualClose = true;
      clearTimeout(state.ws.retryTimer);
      clearInterval(state.ws.countdownTimer);
      if (state.ws.socket) {
        try {
          state.ws.socket.close();
        } catch (err) {
          /* ignore */
        }
      }
    });
  }

  function cacheDom() {
    const ids = [
      "offlineBanner",
      "offlineDetail",
      "offlineRetry",
      "serverChip",
      "serverChipText",
      "versionLabel",
      "linkDot",
      "linkText",
      "deviceText",
      "watchPill",
      "framesTotal",
      "captureText",
      "errorBox",
      "errorText",
      "connectBtn",
      "disconnectBtn",
      "wsBadge",
      "wsText",
      "framesCount",
      "pauseBtn",
      "pauseIcon",
      "pauseLabel",
      "downloadBtn",
      "msgFilter",
      "hexSearch",
      "filterCount",
      "framesBody",
      "framesEmpty",
      "framesEmptyText",
      "pausedHint",
      "curveInputs",
      "rangeWarning",
      "curveCanvas",
      "randomBtn",
      "equalN",
      "equalBtn",
      "fromProfileBtn",
      "sendBtn",
      "sendResult",
      "activeProfileChip",
      "profilesCount",
      "profilesRefresh",
      "saveProfileForm",
      "profileName",
      "profileNote",
      "saveProfileBtn",
      "profileList",
      "profilesEmpty",
      "themeRow",
      "previewWrap",
      "wallpaperPreview",
      "previewLoading",
      "previewError",
      "wallpaperSave",
      "wallpaperApply",
      "wallpaperResult",
      "footerVersion",
      "toasts",
    ];
    ids.forEach((id) => {
      dom[id] = byId(id);
    });
    dom.rangeChip = byId("rangeChip");
  }

  function init() {
    cacheDom();
    buildCurveInputs();
    bindEvents();

    renderServerChip();
    renderStatus();
    renderWs();
    updatePauseUi();
    updateRangeUi();
    renderFramesNow();
    renderProfiles();
    renderSend();
    renderThemeButtons();
    renderWallpaperControls();
    renderWallpaperResult();
    renderActiveProfileChip();
    updateWallpaperPreview(true);

    loadMeta();
    loadProfiles({ silent: true });
    refreshState({ silent: true });
    wsConnect();

    requestAnimationFrame(drawCurve);
    window.setTimeout(drawCurve, 250);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
