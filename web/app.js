const state = {
  channels: [],
  channelLanguageFilter: "all",
  activeTab: "channels",
  statusTimer: null,
  wasRunning: false,
};

const $ = (selector) => document.querySelector(selector);
const THEME_STORAGE_KEY = "importadorLingqTheme";

const languageAliases = {
  en: ["en", "ingles", "inglês", "english"],
  es: ["es", "espanhol", "español", "spanish"],
  it: ["it", "italiano", "italian"],
  fr: ["fr", "frances", "francês", "french"],
};

function secondsToMmss(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "";
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return "";
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function mmssToSeconds(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const match = trimmed.match(/^(\d{1,3}):([0-5]\d)$/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Erro HTTP ${response.status}`);
  }
  return response.json();
}

function renderChannels() {
  const body = $("#channelsBody");
  body.innerHTML = "";

  const filteredChannels = state.channelLanguageFilter === "all"
    ? state.channels
    : state.channels.filter((channel) => getChannelLanguageCode(channel) === state.channelLanguageFilter);

  for (const channel of filteredChannels) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><input type="checkbox" ${channel.ativo ? "checked" : ""} aria-label="Ativo"></td>
      <td>${escapeHtml(channel.nome)}</td>
      <td>${escapeHtml(channel.idioma)}</td>
      <td><span class="languageCode">${renderLanguageFlag(channel)}<code>${escapeHtml(channel.lang_code)}</code></span></td>
      <td>${channel.videos_por_execucao || 1}</td>
      <td><a href="${escapeAttr(channel.url)}" target="_blank" rel="noreferrer">${escapeHtml(channel.url)}</a></td>
      <td class="actions">
        <button data-action="edit">Editar</button>
        <button data-action="delete" class="danger">Remover</button>
      </td>
    `;

    row.querySelector("input[type='checkbox']").addEventListener("change", async (event) => {
      await api(`/api/channels/${channel.id}`, {
        method: "PUT",
        body: JSON.stringify({ ativo: event.target.checked }),
      });
      await loadChannels();
    });

    row.querySelector("[data-action='edit']").addEventListener("click", () => openDialog(channel));
    row.querySelector("[data-action='delete']").addEventListener("click", async () => {
      if (!confirm(`Remover ${channel.nome}?`)) return;
      await api(`/api/channels/${channel.id}`, { method: "DELETE" });
      await loadChannels();
    });

    body.appendChild(row);
  }

  if (!filteredChannels.length) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td colspan="7" class="emptyState">Nenhum canal encontrado para este idioma.</td>
    `;
    body.appendChild(row);
  }
}

function setLanguageFilter(langCode) {
  state.channelLanguageFilter = langCode;
  document.querySelectorAll("#languageFilter .segment").forEach((button) => {
    const active = button.dataset.lang === langCode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  renderChannels();
}

function getChannelLanguageCode(channel) {
  const values = [channel.lang_code, channel.idioma].map((value) => normalizeText(value));
  for (const [code, aliases] of Object.entries(languageAliases)) {
    if (values.some((value) => aliases.includes(value))) {
      return code;
    }
  }
  return values[0] || "";
}

function renderLanguageFlag(channel) {
  const flagClass = {
    en: "flagGb",
    es: "flagEs",
    it: "flagIt",
    fr: "flagFr",
  }[getChannelLanguageCode(channel)];

  if (!flagClass) return "";
  return `<span class="flagIcon ${flagClass}" aria-hidden="true"></span>`;
}

function normalizeText(value) {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

function openDialog(channel = null) {
  $("#dialogTitle").textContent = channel ? "Editar canal" : "Adicionar canal";
  $("#channelId").value = channel?.id || "";
  $("#channelName").value = channel?.nome || "";
  $("#channelLanguage").value = channel?.idioma || "";
  $("#channelLangCode").value = channel?.lang_code || "";
  $("#channelUrl").value = channel?.url || "";
  $("#channelLimit").value = channel?.videos_por_execucao || 1;
  $("#channelDurationMin").value = secondsToMmss(channel?.duracao_min_segundos ?? 300);
  $("#channelDurationMax").value = secondsToMmss(channel?.duracao_max_segundos ?? 1200);
  $("#channelActive").checked = channel?.ativo ?? true;
  $("#channelDialog").showModal();
}

function closeChannelDialog() {
  $("#channelDialog").close();
}

async function saveChannel(event) {
  event.preventDefault();
  const payload = {
    nome: $("#channelName").value.trim(),
    idioma: $("#channelLanguage").value.trim(),
    lang_code: $("#channelLangCode").value.trim(),
    url: $("#channelUrl").value.trim(),
    videos_por_execucao: Number($("#channelLimit").value),
    duracao_min_segundos: mmssToSeconds($("#channelDurationMin").value),
    duracao_max_segundos: mmssToSeconds($("#channelDurationMax").value),
    ativo: $("#channelActive").checked,
  };

  const id = $("#channelId").value;
  if (id) {
    await api(`/api/channels/${id}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    await api("/api/channels", { method: "POST", body: JSON.stringify(payload) });
  }

  $("#channelDialog").close();
  await loadChannels();
}

async function loadChannels() {
  state.channels = await api("/api/channels");
  renderChannels();
}

async function runImport() {
  $("#runImport").disabled = true;
  try {
    await api("/api/import/run", { method: "POST" });
    setActiveTab("status");
    await loadStatus();
  } catch (error) {
    alert(error.message);
  } finally {
    $("#runImport").disabled = false;
  }
}

async function loadStatus() {
  const status = await api("/api/import/status");
  $("#jobBadge").textContent = status.running ? "Rodando" : "Parado";
  $("#jobBadge").className = status.running ? "badge active" : "badge";
  $("#startedAt").textContent = status.started_at || "-";
  $("#finishedAt").textContent = status.finished_at || "-";
  $("#summary").textContent = status.summary || "Sem execução ativa.";
  $("#logs").textContent = (status.logs || []).join("\n");
  $("#runImport").disabled = status.running;

  if (state.wasRunning && !status.running) {
    await loadHistory();
  }
  state.wasRunning = status.running;
}

async function loadHistory() {
  const history = await api("/api/history");
  $("#historyTotal").textContent = `${history.total} vídeos`;
  const list = $("#historyList");
  list.innerHTML = "";

  for (const item of history.items.slice(0, 30)) {
    const element = document.createElement("article");
    element.className = "historyItem";
    element.innerHTML = `
      <strong>${escapeHtml(item.titulo || item.id)}</strong>
      <span>${escapeHtml(item.canal || "-")} · ${escapeHtml(item.idioma || "-")}</span>
      <small>${escapeHtml(item.importado_em || "")} · ${escapeHtml(item.resultado || "")}</small>
    `;
    list.appendChild(element);
  }
}

async function loadSessionStatus() {
  const session = await api("/api/lingq-session");
  const configured = session.has_sessionid && session.has_csrftoken;
  $("#sessionBadge").textContent = configured ? "Configurada" : "Não configurada";
  $("#sessionBadge").className = configured ? "badge active" : "badge";
}

async function saveSession(event) {
  event.preventDefault();
  await api("/api/lingq-session", {
    method: "PUT",
    body: JSON.stringify({
      sessionid: $("#sessionId").value.trim(),
      csrftoken: $("#csrfToken").value.trim(),
    }),
  });
  $("#sessionId").value = "";
  $("#csrfToken").value = "";
  await loadSessionStatus();
  closeSessionDialog();
}

async function clearSession() {
  await api("/api/lingq-session", {
    method: "PUT",
    body: JSON.stringify({ sessionid: "", csrftoken: "" }),
  });
  $("#sessionId").value = "";
  $("#csrfToken").value = "";
  await loadSessionStatus();
}

function openSessionDialog() {
  $("#sessionDialog").showModal();
}

function closeSessionDialog() {
  $("#sessionDialog").close();
}

function setActiveTab(tabName) {
  state.activeTab = tabName;

  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });

  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    const active = panel.dataset.tabPanel === tabName;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

function setTheme(theme) {
  const resolvedTheme = theme === "light" ? "light" : "dark";
  document.body.dataset.theme = resolvedTheme;
  localStorage.setItem(THEME_STORAGE_KEY, resolvedTheme);

  const isLight = resolvedTheme === "light";
  $("#themeToggle").setAttribute("aria-pressed", String(isLight));
  $("#themeToggle").setAttribute("aria-label", isLight ? "Alternar para modo escuro" : "Alternar para modo claro");
  $("#themeToggleText").textContent = isLight ? "Modo escuro" : "Modo claro";
}

function toggleTheme() {
  setTheme(document.body.dataset.theme === "light" ? "dark" : "light");
}

setTheme(localStorage.getItem(THEME_STORAGE_KEY) || document.body.dataset.theme);

$("#addChannel").addEventListener("click", () => openDialog());
$("#channelForm").addEventListener("submit", saveChannel);
$("#cancelChannel").addEventListener("click", closeChannelDialog);
$("#runImport").addEventListener("click", runImport);
$("#openSession").addEventListener("click", openSessionDialog);
$("#closeSession").addEventListener("click", closeSessionDialog);
$("#themeToggle").addEventListener("click", toggleTheme);
$("#sessionForm").addEventListener("submit", saveSession);
$("#clearSession").addEventListener("click", clearSession);
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});
document.querySelectorAll("#languageFilter [data-lang]").forEach((button) => {
  button.addEventListener("click", () => setLanguageFilter(button.dataset.lang));
});

setActiveTab(state.activeTab);
loadChannels();
loadStatus();
loadHistory();
loadSessionStatus();
state.statusTimer = setInterval(loadStatus, 2500);
