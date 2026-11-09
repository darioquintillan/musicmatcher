"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    token: "", ready: false, connected: false, source: "spotify", config: {},
    snapshot: {job: {status: "idle"}, catalog: {total: 0, counts: {}}, logs: []},
    tracks: [], total: 0, offset: 0, limit: 25, filter: "all", query: "",
    file: null, submitting: false, modalBusy: false, stopPending: false,
    requestNumber: 0, catalogSignature: "", reviewTrack: null, authorizeTrack: null,
    confirmAction: null, eventSource: null, tableTimer: null,
  };
  const statusNames = {
    accepted: "Aceptada", review: "Por revisar", unmatched: "Sin coincidencia",
    pending: "Pendiente", search_error: "Error de búsqueda", unavailable: "No disponible",
  };
  const jobNames = {idle: "En espera", running: "En marcha", paused: "Detenido", completed: "Completado", error: "Con errores"};

  function node(tag, className = "", text = undefined) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function count(value) { return Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0; }
  function artistNames(track) {
    return Array.isArray(track.artists) ? track.artists.filter((v) => typeof v === "string").join(", ") : (track.artist || "Artista no indicado");
  }
  function duration(seconds) {
    if (!Number.isFinite(Number(seconds)) || Number(seconds) <= 0) return "—";
    const value = Math.round(Number(seconds));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
  }
  function elapsed(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(value / 3600);
    return `${hours ? `${hours}:` : ""}${String(Math.floor(value / 60) % 60).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }
  function canonicalVideo(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value.trim());
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port) return null;
      let id;
      if (url.hostname === "youtu.be") id = url.pathname.replace(/^\/+|\/+$/g, "");
      else if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(url.hostname)) {
        if (url.pathname === "/watch") id = url.searchParams.get("v");
        else id = url.pathname.match(/^\/(?:shorts|embed)\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
      }
      return typeof id === "string" && /^[A-Za-z0-9_-]{11}$/.test(id) ? `https://www.youtube.com/watch?v=${id}` : null;
    } catch { return null; }
  }
  function videoLink(url, text = "Abrir vídeo ↗", className = "match-link") {
    const safe = canonicalVideo(url);
    if (!safe) return node("span", "match-caption", "Sin enlace válido");
    const anchor = node("a", className, text);
    anchor.href = safe;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return anchor;
  }
  function showError(message) {
    $("error-message").textContent = String(message);
    $("error-banner").hidden = false;
  }
  function inlineError(id, message = "") {
    $(id).textContent = message;
    $(id).hidden = !message;
  }
  function errorText(data, fallback) {
    const detail = data?.detail ?? data?.error ?? data?.message;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || String(item)).join(". ");
    return fallback;
  }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const method = options.method || "GET";
    if (method !== "GET") headers.set("X-MM-Token", state.token);
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(path, {
        ...options, headers, credentials: "same-origin", signal: controller.signal,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
      const raw = await response.text();
      let data = {};
      try { data = raw ? JSON.parse(raw) : {}; } catch { /* Un error HTTP puede devolver texto. */ }
      if (!response.ok) throw new Error(errorText(data, `No se pudo completar la petición (${response.status}).`));
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("El servidor local no ha respondido a tiempo. Comprueba su estado antes de reintentar.");
      if (error instanceof TypeError) throw new Error("No se puede conectar con el servidor local de MusicMatcher.");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function running() { return state.snapshot.job?.status === "running"; }
  function locked() { return !state.ready || !state.connected || running() || state.submitting || state.modalBusy; }
  function updateControls() {
    const blocked = locked();
    document.querySelectorAll("[data-lock]").forEach((element) => {
      const inactiveSource = (element.closest("#panel-spotify") && state.source !== "spotify")
        || (element.closest("#panel-file") && state.source !== "file");
      element.disabled = Boolean(blocked || inactiveSource);
    });
    const catalog = state.snapshot.catalog || {};
    const hasCatalog = count(catalog.total) > 0;
    $("search-button").disabled = blocked || !hasCatalog;
    $("download-button").disabled = blocked || !count(catalog.counts?.accepted);
    $("clear-catalog").disabled = blocked || !hasCatalog;
    $("stop-button").disabled = !running() || state.stopPending;
    $("stop-button").querySelector("span").textContent = state.stopPending ? "Deteniendo…" : "Detener proceso";
    $("run-button").title = hasCatalog ? "Buscar y descargar a partir del catálogo actual" : "Importar, buscar y descargar a partir de tu entrada";
    $("replace-row").hidden = !hasCatalog;
    document.querySelectorAll("#export-mapping, #export-links").forEach((anchor) => {
      anchor.setAttribute("aria-disabled", String(!hasCatalog));
      anchor.tabIndex = hasCatalog ? 0 : -1;
    });
    $("drop-zone").classList.toggle("disabled", blocked);
  }
  function updateConnection(connected) {
    state.connected = connected;
    const element = $("connection-status");
    element.classList.toggle("disconnected", !connected);
    element.replaceChildren(node("span", "status-dot"), document.createTextNode(connected ? "Entorno local conectado" : "Reconectando…"));
    element.title = connected ? "Conectado con el servidor de tu equipo" : "La conexión se restablecerá automáticamente";
    updateControls();
  }
  function applyConfig(config) {
    state.config = {...state.config, ...config};
    $("candidate-limit").value = state.config.candidate_limit ?? 8;
    $("matching-mode").value = state.config.matching_mode || "relaxed";
    $("source-client-id").value = state.config.spotify_client_id || "";
    $("source-redirect-uri").value = state.config.spotify_redirect_uri || "http://127.0.0.1:8888/callback";
  }
  function renderDependencies(dependencies = {}) {
    const ffmpeg = Boolean(dependencies.ffmpeg && dependencies.ffprobe);
    $("dep-ffmpeg").className = ffmpeg ? "available" : "missing";
    $("dep-ffmpeg").title = ffmpeg ? "FFmpeg y ffprobe disponibles" : "Falta FFmpeg o ffprobe; revisa su instalación o carpeta";
    $("dep-deno").className = dependencies.deno ? "available" : "missing";
    $("dep-deno").title = dependencies.deno ? "Deno disponible" : "Deno no detectado; revisa el motor JavaScript de yt-dlp";
  }
  function stageNumber(stage) {
    const value = String(stage || "").toLowerCase();
    if (/download|descarg/.test(value)) return 2;
    if (/search|match|compar|valid/.test(value)) return 1;
    if (/import|spotify|extract|file/.test(value)) return 0;
    if (/finish|complet|done/.test(value)) return 3;
    return -1;
  }
  function renderProgress(job = {}) {
    const total = count(job.total), done = count(job.done);
    const percent = Math.max(0, Math.min(100, Number.isFinite(Number(job.percent)) ? Number(job.percent) : (total ? done * 100 / total : 0)));
    $("job-status").textContent = jobNames[job.status] || "En espera";
    $("job-status").dataset.status = job.status || "idle";
    $("progress-percent").replaceChildren(document.createTextNode(String(Math.round(percent))), node("span", "", "%"));
    $("progress-fraction").textContent = `${done} de ${total}`;
    $("progress-meter").setAttribute("aria-valuenow", String(Math.round(percent)));
    $("ring-fill").style.strokeDashoffset = String(414.6902 * (1 - percent / 100));
    $("linear-progress").style.width = `${percent}%`;
    const step = stageNumber(job.stage);
    const titles = ["Importando tu lista", "Buscando la versión correcta", "Guardando tu música", "Proceso completado"];
    let heading = step >= 0 ? titles[step] : "Todo preparado";
    if (job.status === "paused") heading = "Resultados guardados";
    else if (job.status === "error") heading = "El proceso necesita atención";
    else if (job.status === "completed") heading = "Esta fase ha terminado";
    $("progress-heading").textContent = heading;
    $("job-message").textContent = job.message || "Importa una lista para empezar.";
    $("current-title").textContent = job.current_title || "";
    $("current-title").title = job.current_title || "";
    $("job-time").textContent = elapsed(job.elapsed_seconds);
    const eta = Number(job.eta_seconds);
    $("job-time").title = Number.isFinite(eta) && eta > 0 ? `Tiempo estimado restante: ${elapsed(eta)}` : "Tiempo transcurrido";
    const candidates = count(job.candidate_total);
    $("candidate-progress").textContent = step === 2 && job.download_percent != null
      ? `Audio actual: ${Math.round(Math.max(0, Math.min(100, Number(job.download_percent) || 0)))} %`
      : step === 1 && candidates ? `Evaluados: ${count(job.candidate_done)} de ${candidates} · máximo ${count(job.candidate_limit || state.config.candidate_limit)}`
        : (job.status === "running" ? "Procesando tu lista" : "Los resultados se guardan en local");
    document.querySelectorAll("[data-stage]").forEach((element, index) => {
      element.classList.toggle("active", index === step && job.status === "running");
      element.classList.toggle("finished", index < step || (index === step && job.status === "completed"));
    });
    if (job.status !== "running") state.stopPending = false;
  }
  function renderActivity(logs) {
    const list = $("activity-list");
    const fragment = document.createDocumentFragment();
    if (!Array.isArray(logs) || !logs.length) fragment.append(node("li", "activity-empty", "Aquí verás lo que ocurre durante el proceso."));
    else logs.slice(-12).reverse().forEach((entry) => {
      const level = String(entry.level || "info").toLowerCase();
      const item = node("li", ["error", "warning", "warn"].includes(level) ? (level === "error" ? "error" : "warning") : "");
      const rawTime = String(entry.time || "");
      const time = node("time");
      const parsed = new Date(rawTime);
      time.textContent = !Number.isNaN(parsed.getTime()) ? parsed.toLocaleTimeString("es-ES", {hour: "2-digit", minute: "2-digit"}) : (rawTime.match(/\d{2}:\d{2}/)?.[0] || "ahora");
      if (!Number.isNaN(parsed.getTime())) time.dateTime = parsed.toISOString();
      item.append(time, node("span", "log-message", entry.message || ""));
      fragment.append(item);
    });
    list.replaceChildren(fragment);
  }
  function renderSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    state.snapshot = snapshot;
    const catalog = snapshot.catalog || {};
    const counts = catalog.counts || {};
    $("stat-total").textContent = count(catalog.total);
    $("stat-accepted").textContent = count(counts.accepted);
    $("stat-review").textContent = count(counts.review) + count(counts.unmatched);
    $("stat-downloaded").textContent = count(catalog.download_counts?.completed);
    $("library-count").textContent = count(catalog.total);
    $("catalog-name").textContent = catalog.name || (count(catalog.total) ? "Tu catálogo de canciones" : "Importa una playlist o un archivo para ver tus canciones.");
    renderProgress(snapshot.job || {});
    renderActivity(snapshot.logs || []);
    renderDependencies(snapshot.dependencies || {});
    const signature = JSON.stringify([catalog.updated_at, catalog.total, catalog.counts, catalog.download_counts, catalog.name]);
    if (signature !== state.catalogSignature) {
      state.catalogSignature = signature;
      scheduleTracks();
    }
    updateControls();
  }
  async function bootstrap() {
    const result = await api("/api/bootstrap");
    if (!result.csrf_token) throw new Error("El servidor no ha proporcionado el token de la sesión local.");
    state.token = result.csrf_token;
    if (!state.ready) $("track-limit").value = result.snapshot?.job?.track_limit || "";
    state.ready = true;
    applyConfig(result.config || {});
    renderSnapshot(result.snapshot || {});
    updateConnection(true);
  }
  function connectEvents() {
    if (state.eventSource) state.eventSource.close();
    const events = new EventSource("/api/events");
    state.eventSource = events;
    events.addEventListener("open", () => updateConnection(true));
    events.addEventListener("snapshot", (event) => {
      try { renderSnapshot(JSON.parse(event.data)); updateConnection(true); }
      catch { showError("No se ha podido leer una actualización del servidor local."); }
    });
    events.addEventListener("error", () => updateConnection(false));
  }
  function scheduleTracks() {
    clearTimeout(state.tableTimer);
    state.tableTimer = setTimeout(() => loadTracks(), 180);
  }
  function renderTracks() {
    const body = $("tracks-body");
    const fragment = document.createDocumentFragment();
    state.tracks.forEach((track) => {
      const row = node("tr");
      const match = track.match || {};
      row.append(node("td", "position", track.position ?? "—"));
      const song = node("td");
      song.append(node("strong", "song-title", track.title || "Sin título"));
      const subtitle = node("div", "song-subtitle", artistNames(track));
      subtitle.append(node("span", "song-duration", duration(track.duration_ms ? track.duration_ms / 1000 : null)));
      song.append(subtitle);
      row.append(song);
      const statusCell = node("td");
      const badge = node("span", `status-badge ${Object.hasOwn(statusNames, match.status) ? match.status : "pending"}`, statusNames[match.status] || "Pendiente");
      badge.title = (Array.isArray(match.reasons) ? match.reasons.filter((reason) => typeof reason === "string").join(" · ") : "") || track.unavailable_reason || "";
      statusCell.append(badge);
      const evaluated = Array.isArray(match.candidates) ? match.candidates.length : 0;
      if (evaluated) statusCell.append(node("div", "match-caption", `${evaluated} vídeo${evaluated === 1 ? "" : "s"} evaluado${evaluated === 1 ? "" : "s"} · máx. ${match.candidate_limit || evaluated}`));
      row.append(statusCell);
      const linkCell = node("td");
      if (match.status === "accepted" && canonicalVideo(match.url)) {
        linkCell.append(videoLink(match.url));
        const authorized = track.authorization?.audio === true && track.authorization?.url === canonicalVideo(match.url);
        linkCell.append(node("div", "match-caption", authorized ? "Descarga autorizada" : "Pendiente de autorización"));
      } else linkCell.append(node("span", "match-caption", match.status === "review" ? "Comprueba los candidatos" : "—"));
      row.append(linkCell);
      const actionCell = node("td");
      const actions = node("div", "row-actions");
      if (match.status !== "unavailable") {
        const review = node("button", "row-button", match.status === "accepted" ? "Revisar" : "Elegir vídeo");
        review.type = "button"; review.dataset.lock = "";
        review.addEventListener("click", () => openReview(track));
        actions.append(review);
      }
      if (match.status === "accepted") {
        if (track.download?.status === "completed") actions.append(node("span", "download-ready", "✓ MP3"));
        else if (!(track.authorization?.audio === true && track.authorization?.url === canonicalVideo(match.url))) {
          const authorize = node("button", "row-button authorize", "Autorizar");
          authorize.type = "button"; authorize.dataset.lock = "";
          authorize.addEventListener("click", () => openAuthorization(track));
          actions.append(authorize);
        }
      }
      if (!actions.childNodes.length) actions.append(node("span", "match-caption", "—"));
      actionCell.append(actions); row.append(actionCell); fragment.append(row);
    });
    body.replaceChildren(fragment);
    $("library-empty").hidden = state.tracks.length > 0;
    if (!state.tracks.length) {
      const empty = $("library-empty");
      empty.querySelector("h3").textContent = count(state.snapshot.catalog?.total) ? "No hay canciones con este filtro" : "Tu próxima biblioteca empieza aquí";
      empty.querySelector("p").textContent = count(state.snapshot.catalog?.total) ? "Prueba con otro estado o cambia el texto de búsqueda." : "Importa tus canciones. Las coincidencias dudosas siempre pasan por tu revisión.";
    }
    $("pagination-label").textContent = state.total ? `${state.offset + 1}–${Math.min(state.offset + state.limit, state.total)} de ${state.total} canciones` : "Sin canciones";
    $("previous-page").disabled = state.offset <= 0;
    $("next-page").disabled = state.offset + state.limit >= state.total;
    updateControls();
  }
  async function loadTracks() {
    clearTimeout(state.tableTimer);
    if (!state.ready) return;
    const request = ++state.requestNumber;
    $("tracks-body").setAttribute("aria-busy", "true");
    const query = new URLSearchParams({status: state.filter, q: state.query, offset: String(state.offset), limit: String(state.limit)});
    try {
      const result = await api(`/api/tracks?${query}`);
      if (request !== state.requestNumber) return;
      state.tracks = Array.isArray(result.tracks) ? result.tracks : [];
      state.total = count(result.total);
      if (state.offset && state.offset >= state.total) { state.offset = 0; return loadTracks(); }
      renderTracks();
    } catch (error) { if (request === state.requestNumber) showError(error.message); }
    finally { if (request === state.requestNumber) $("tracks-body").setAttribute("aria-busy", "false"); }
  }
  function selectSource(source, focus = false) {
    if (locked()) return;
    state.source = source;
    document.querySelectorAll("[data-source]").forEach((button) => {
      const active = button.dataset.source === source;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    $("panel-spotify").hidden = source !== "spotify";
    $("panel-file").hidden = source !== "file";
    $("spotify-url").required = source === "spotify";
    $("import-button").replaceChildren(document.createTextNode(source === "spotify" ? "Importar playlist " : "Importar archivo "), node("span", "", "↗"));
    $("source-note").textContent = source === "spotify" ? "Lectura mediante la API oficial" : "CSV o JSON con tus canciones";
    updateControls();
  }
  async function readFile(file) {
    if (locked() || !file) return;
    if (!/\.(csv|json)$/i.test(file.name)) { showError("Elige un archivo CSV o JSON."); return; }
    try {
      const content = await file.text();
      if (!content.trim()) throw new Error("El archivo está vacío.");
      state.file = {filename: file.name, content};
      $("file-name").textContent = file.name;
      $("file-description").textContent = `${new Intl.NumberFormat("es-ES", {maximumFractionDigits: 1}).format(file.size / 1024)} KB · Listo para importar`;
    } catch (error) { showError(error.message || "No se ha podido leer el archivo."); }
  }
  function sourceConfig() {
    const limit = Number($("candidate-limit").value);
    if (!Number.isInteger(limit) || limit < 1 || limit > 25) throw new Error("Indica un máximo de entre 1 y 25 vídeos por canción.");
    return {candidate_limit: limit, spotify_client_id: $("source-client-id").value.trim(), spotify_redirect_uri: $("source-redirect-uri").value.trim()};
  }
  function importPayload() {
    const replace = $("replace-catalog").checked;
    if (state.source === "spotify") {
      const spotify_url = $("spotify-url").value.trim();
      if (!spotify_url) throw new Error("Pega el enlace de la playlist que quieres importar.");
      if (!$("source-client-id").value.trim()) {
        $("source-client-id").closest("details").open = true;
        $("source-client-id").focus();
        throw new Error("Introduce el Client ID de tu aplicación de Spotify en «Conexión con Spotify».");
      }
      return {spotify_url, replace};
    }
    if (!state.file) throw new Error("Elige primero el archivo CSV o JSON que quieres importar.");
    return {...state.file, replace};
  }
  async function startJob(action) {
    if (locked()) return;
    try {
      const hasCatalog = count(state.snapshot.catalog?.total) > 0;
      let payload = {action};
      if (["search", "download", "run"].includes(action) && $("track-limit").value.trim()) {
        const limit = Number($("track-limit").value);
        if (!Number.isInteger(limit) || limit < 1 || limit > 10000) throw new Error("El lote debe tener entre 1 y 10000 canciones.");
        payload.track_limit = limit;
      }
      if (action === "import_spotify" || action === "import_file" || (action === "run" && !hasCatalog)) {
        payload = {...payload, ...importPayload()};
        if (hasCatalog && !payload.replace) throw new Error("Ya tienes un catálogo. Marca «Sustituir el catálogo actual» para importar otro.");
      }
      const config = sourceConfig();
      config.matching_mode = $("matching-mode").value;
      state.submitting = true;
      updateControls();
      $("error-banner").hidden = true;
      const saved = await api("/api/config", {method: "POST", body: config});
      applyConfig(saved.config || config);
      await api("/api/jobs", {method: "POST", body: payload});
      await bootstrap();
    } catch (error) { showError(error.message); }
    finally { state.submitting = false; updateControls(); }
  }
  async function stopJob() {
    if (!running() || state.stopPending) return;
    state.stopPending = true;
    updateControls();
    try { await api("/api/jobs/stop", {method: "POST", body: {}}); }
    catch (error) { state.stopPending = false; updateControls(); showError(error.message); }
  }
  function openSettings() {
    if (locked()) return;
    const config = state.config;
    $("settings-limit").value = $("candidate-limit").value || config.candidate_limit || 8;
    $("settings-bitrate").value = config.bitrate || 320;
    $("settings-output").value = config.output_dir || "downloads";
    $("settings-ffmpeg").value = config.ffmpeg_location || "";
    $("settings-client-id").value = $("source-client-id").value;
    $("settings-redirect").value = $("source-redirect-uri").value;
    inlineError("settings-error");
    $("settings-dialog").showModal();
  }
  async function saveSettings(event) {
    event.preventDefault();
    if (locked()) return;
    const config = {
      candidate_limit: Number($("settings-limit").value), bitrate: Number($("settings-bitrate").value),
      output_dir: $("settings-output").value.trim(), ffmpeg_location: $("settings-ffmpeg").value.trim(),
      spotify_client_id: $("settings-client-id").value.trim(), spotify_redirect_uri: $("settings-redirect").value.trim(),
    };
    state.modalBusy = true; updateControls(); inlineError("settings-error");
    try {
      const result = await api("/api/config", {method: "POST", body: config});
      applyConfig(result.config || config);
      $("settings-dialog").close();
      await bootstrap();
    } catch (error) { inlineError("settings-error", error.message); }
    finally { state.modalBusy = false; updateControls(); }
  }
  function candidateUrl(evaluation) {
    const candidate = evaluation.candidate || evaluation;
    return canonicalVideo(evaluation.url || candidate.webpage_url || candidate.url)
      || (/^[A-Za-z0-9_-]{11}$/.test(candidate.id || "") ? `https://www.youtube.com/watch?v=${candidate.id}` : null);
  }
  function markSelectedCandidate() {
    const selected = canonicalVideo($("review-url").value);
    $("candidate-list").querySelectorAll(".candidate-option").forEach((option) => {
      const radio = option.querySelector("input");
      radio.checked = Boolean(selected && radio.value === selected);
      option.classList.toggle("selected", radio.checked);
    });
  }
  function openReview(track) {
    if (locked()) return;
    state.reviewTrack = track;
    $("review-song").textContent = track.title || "Sin título";
    $("review-artists").textContent = `${artistNames(track)} · ${duration(track.duration_ms ? track.duration_ms / 1000 : null)}`;
    $("review-url").value = canonicalVideo(track.match?.url) || "";
    $("review-note").value = track.match?.note || "";
    $("review-authorize").checked = false;
    $("review-authorization-fields").hidden = true;
    $("review-authorization-note").value = "";
    $("review-authorization-note").required = false;
    $("review-cover").value = "";
    inlineError("review-error");
    const fragment = document.createDocumentFragment();
    const candidates = Array.isArray(track.match?.candidates) ? track.match.candidates : [];
    candidates.slice(0, 25).forEach((evaluation) => {
      const candidate = evaluation.candidate || evaluation;
      const url = candidateUrl(evaluation);
      const rejected = evaluation.eligible === false || (Array.isArray(evaluation.rejected) ? evaluation.rejected.length > 0 : Boolean(evaluation.rejected));
      const option = node("div", `candidate-option${rejected ? " rejected" : ""}`);
      const radio = node("input");
      radio.type = "radio"; radio.name = "candidate-video"; radio.value = url || ""; radio.disabled = !url;
      if (url) radio.dataset.lock = "";
      radio.setAttribute("aria-label", `Elegir ${candidate.title || candidate.track || "este candidato"}`);
      radio.addEventListener("change", () => { $("review-url").value = url || ""; markSelectedCandidate(); });
      const content = node("div", "candidate-content");
      content.append(node("div", "candidate-title", candidate.title || candidate.track || "Vídeo sin título"));
      const metadata = [artistNames(candidate), candidate.channel || candidate.uploader, duration(candidate.duration)].filter((value) => value && value !== "Artista no indicado");
      content.append(node("p", "candidate-meta", metadata.join(" · ")));
      let reasons = Array.isArray(evaluation.reasons) ? evaluation.reasons : (evaluation.reasons ? [evaluation.reasons] : []);
      if (Array.isArray(evaluation.rejected)) reasons = [...reasons, ...evaluation.rejected];
      reasons = reasons.filter((value) => typeof value === "string");
      if (reasons.length) content.append(node("p", "candidate-reasons", reasons.join(" · ")));
      const score = node("div", "candidate-score");
      score.append(node("strong", "", Number.isFinite(Number(evaluation.score)) ? `${Math.round(Number(evaluation.score))}/100` : "Sin puntuación"));
      if (url) score.append(videoLink(url, "Abrir ↗", ""));
      option.append(radio, content, score); fragment.append(option);
    });
    if (!candidates.length) fragment.append(node("p", "candidate-empty", "No hay candidatos guardados. Puedes indicar un enlace de YouTube que hayas comprobado."));
    $("candidate-list").replaceChildren(fragment);
    markSelectedCandidate(); updateControls();
    $("review-dialog").showModal();
  }
  async function saveReview(event) {
    event.preventDefault();
    if (locked() || !state.reviewTrack) return;
    const url = canonicalVideo($("review-url").value);
    if (!url) { inlineError("review-error", "Introduce un enlace válido a un vídeo de YouTube."); return; }
    const authorize = $("review-authorize").checked;
    const permission = $("review-authorization-note").value.trim();
    if (authorize && !permission) { inlineError("review-error", "Indica el permiso o la licencia que autoriza esta descarga."); return; }
    const payload = {key: state.reviewTrack.key, url, note: $("review-note").value.trim(), authorize_audio: authorize};
    if (authorize) {
      payload.authorization_note = permission;
      const cover = $("review-cover").value.trim();
      if (cover) payload.cover_path = cover;
    }
    state.modalBusy = true; updateControls(); inlineError("review-error");
    try {
      await api("/api/review", {method: "POST", body: payload});
      $("review-dialog").close();
      await bootstrap(); await loadTracks();
    } catch (error) { inlineError("review-error", error.message); }
    finally { state.modalBusy = false; updateControls(); }
  }
  function openAuthorization(track) {
    if (locked()) return;
    state.authorizeTrack = track;
    $("authorize-song").textContent = track.title || "Sin título";
    $("authorize-artists").textContent = artistNames(track);
    $("authorize-confirm").checked = false;
    $("authorize-note").value = "";
    $("authorize-cover").value = "";
    inlineError("authorize-error");
    $("authorize-dialog").showModal();
  }
  async function saveAuthorization(event) {
    event.preventDefault();
    if (locked() || !state.authorizeTrack) return;
    if (!$("authorize-confirm").checked) { inlineError("authorize-error", "Confirma que tienes autorización para descargar este audio."); return; }
    const note = $("authorize-note").value.trim();
    if (!note) { inlineError("authorize-error", "Indica el permiso o la licencia aplicable."); return; }
    const payload = {key: state.authorizeTrack.key, note};
    if ($("authorize-cover").value.trim()) payload.cover_path = $("authorize-cover").value.trim();
    state.modalBusy = true; updateControls(); inlineError("authorize-error");
    try {
      await api("/api/authorize", {method: "POST", body: payload});
      $("authorize-dialog").close();
      await bootstrap(); await loadTracks();
    } catch (error) { inlineError("authorize-error", error.message); }
    finally { state.modalBusy = false; updateControls(); }
  }
  function confirmAction(title, message, label, callback) {
    if (locked()) return;
    state.confirmAction = callback;
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = message;
    $("confirm-action").textContent = label;
    inlineError("confirm-error");
    $("confirm-dialog").showModal();
  }
  async function performConfirmedAction() {
    if (locked() || !state.confirmAction) return;
    const action = state.confirmAction;
    state.modalBusy = true; updateControls();
    try {
      await action();
      $("confirm-dialog").close();
      state.offset = 0;
      await bootstrap(); await loadTracks();
    } catch (error) { inlineError("confirm-error", error.message); }
    finally { state.modalBusy = false; updateControls(); }
  }

  $("dismiss-error").addEventListener("click", () => { $("error-banner").hidden = true; });
  document.querySelectorAll("[data-open-settings]").forEach((button) => button.addEventListener("click", openSettings));
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const rectangle = dialog.getBoundingClientRect();
    if (event.clientX < rectangle.left || event.clientX > rectangle.right || event.clientY < rectangle.top || event.clientY > rectangle.bottom) dialog.close();
  }));
  document.querySelectorAll("[data-source]").forEach((button) => {
    button.addEventListener("click", () => selectSource(button.dataset.source));
    button.addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        selectSource(event.key === "Home" ? "spotify" : event.key === "End" ? "file" : (state.source === "spotify" ? "file" : "spotify"), true);
      }
    });
  });
  $("file-input").addEventListener("change", (event) => readFile(event.target.files?.[0]));
  $("drop-zone").addEventListener("dragover", (event) => { event.preventDefault(); if (!locked()) $("drop-zone").classList.add("dragging"); });
  $("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragging"));
  $("drop-zone").addEventListener("drop", (event) => { event.preventDefault(); $("drop-zone").classList.remove("dragging"); readFile(event.dataTransfer?.files?.[0]); });
  $("import-form").addEventListener("submit", (event) => { event.preventDefault(); startJob(state.source === "spotify" ? "import_spotify" : "import_file"); });
  $("run-button").addEventListener("click", () => startJob("run"));
  $("search-button").addEventListener("click", () => startJob("search"));
  $("download-button").addEventListener("click", () => startJob("download"));
  $("stop-button").addEventListener("click", stopJob);
  $("settings-form").addEventListener("submit", saveSettings);
  $("review-form").addEventListener("submit", saveReview);
  $("authorize-form").addEventListener("submit", saveAuthorization);
  $("review-url").addEventListener("input", markSelectedCandidate);
  $("review-authorize").addEventListener("change", () => {
    const checked = $("review-authorize").checked;
    $("review-authorization-fields").hidden = !checked;
    $("review-authorization-note").required = checked;
  });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter; state.offset = 0;
    document.querySelectorAll("[data-filter]").forEach((item) => {
      item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button));
    });
    loadTracks();
  }));
  $("track-search").addEventListener("input", () => { state.query = $("track-search").value.trim(); state.offset = 0; scheduleTracks(); });
  $("previous-page").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadTracks(); });
  $("next-page").addEventListener("click", () => { state.offset += state.limit; loadTracks(); });
  document.querySelectorAll("#export-mapping, #export-links").forEach((anchor) => anchor.addEventListener("click", (event) => {
    if (anchor.getAttribute("aria-disabled") === "true") event.preventDefault();
  }));
  $("confirm-action").addEventListener("click", performConfirmedAction);
  $("clear-catalog").addEventListener("click", () => confirmAction("¿Vaciar el catálogo?", "Se eliminarán la lista y las coincidencias guardadas. Los archivos MP3 de tu carpeta seguirán donde están.", "Vaciar catálogo", () => api("/api/catalog", {method: "DELETE"})));
  $("disconnect-button").addEventListener("click", () => confirmAction("¿Desconectar Spotify?", "Se eliminará la autorización local de Spotify y el catálogo procedente de Spotify. Los archivos MP3 se conservarán.", "Desconectar", () => api("/api/spotify/disconnect", {method: "POST", body: {}})));
  window.addEventListener("beforeunload", () => state.eventSource?.close());

  updateControls();
  bootstrap().then(() => { connectEvents(); loadTracks(); }).catch((error) => {
    updateConnection(false); showError(`${error.message} Recarga la página cuando el servidor esté disponible.`);
  });
})();
