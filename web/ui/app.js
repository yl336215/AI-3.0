"use strict";

const LAST_LABEL_SETTINGS_KEY = "ai3:lastLabelSettings";

function readLastLabelSettings() {
  try { return JSON.parse(localStorage.getItem(LAST_LABEL_SETTINGS_KEY) || "{}"); }
  catch { return {}; }
}

const API = Object.freeze({
  status: "/api/status",
  storage: "/api/storage",
  databaseSelectFolder: "/api/databases/select-folder",
  databaseChooseParent: "/api/databases/choose-parent-folder",
  storageSelectFolder: "/api/storage/select-folder",
  databases: "/api/databases",
  databaseCreateFromFolder: "/api/databases/create-from-folder",
  databaseActivate: databaseUid => `/api/databases/${encodeURIComponent(databaseUid)}/activate`,
  databaseRemove: databaseUid => `/api/databases/${encodeURIComponent(databaseUid)}`,
  summary: "/api/summary",
  files: "/api/files",
  conditionOptions: "/api/files/condition-options",
  databaseOpen: "/api/databases/open",
  databaseUpdate: databaseUid => `/api/databases/${encodeURIComponent(databaseUid)}`,
  databaseLines: databaseUid => `/api/databases/${encodeURIComponent(databaseUid)}/lines`,
  databaseLinesAutoDetect: databaseUid => `/api/databases/${encodeURIComponent(databaseUid)}/lines/auto-detect`,
  labelingResolve: "/api/labeling/resolve",
  labelingProjects: "/api/labeling/projects",
  labelingProjectDelete: projectId => `/api/labeling/projects/${encodeURIComponent(projectId)}`,
  labelingSelectProjectRoot: "/api/labeling/projects/select-root",
  labelingScanProject: projectId => `/api/labeling/projects/${encodeURIComponent(projectId)}/scan`,
  labelingSelectFile: fileType => `/api/labeling/select-file?file_type=${encodeURIComponent(fileType)}`,
  labelingSelectSources: (mode, fileType) => `/api/labeling/select-sources?mode=${encodeURIComponent(mode)}&file_type=${encodeURIComponent(fileType)}`,
  labelingWaveform: (path, sampleId, line, wavProfile = "", filter = false) => `/api/labeling/waveform?path=${encodeURIComponent(path)}&sample_id=${encodeURIComponent(sampleId)}&line=${encodeURIComponent(line)}&wav_profile=${encodeURIComponent(wavProfile)}&low_frequency_filter=${filter}`,
  labelingAudio: (path, sampleId, line, wavProfile = "", filter = false) => `/api/labeling/audio.wav?path=${encodeURIComponent(path)}&sample_id=${encodeURIComponent(sampleId)}&line=${encodeURIComponent(line)}&wav_profile=${encodeURIComponent(wavProfile)}&low_frequency_filter=${filter}`,
  labelingAnalysisCards: "/api/labeling/analysis-cards",
  labelingAnalysis: "/api/labeling/analysis",
  labelingSession: "/api/labeling/session",
  labelingSelectJson: "/api/labeling/select-json",
  labelingQueueLabels: (path, historyPath) => `/api/labeling/queue-labels?path=${encodeURIComponent(path)}&history_path=${encodeURIComponent(historyPath || "")}`,
  labelingSave: "/api/labeling/labels",
  labelingPrototype: "/api/labeling/prototype",
  labelingEditCapability: "/api/labeling/labels/edit-capability",
  labelingDelete: "/api/labeling/labels",
  labelingTaxonomy: path => path ? `/api/labeling/taxonomy?path=${encodeURIComponent(path)}` : "/api/labeling/taxonomy",
  importSelectSources: (mode, fileType) => `/api/import/select-sources?mode=${encodeURIComponent(mode)}&file_type=${encodeURIComponent(fileType)}`,
  importFiles: "/api/import",
  importPreview: "/api/import/preview",
  reconcilePreview: "/api/reconcile/preview",
  reconcileApply: "/api/reconcile/apply",
  filePathOptions: "/api/files/path-options",
  fileConditions: "/api/files/conditions",
  exportFilesByLine: "/api/export/files-by-line",
});

const state = {
  status: {},
  storage: {},
  databases: [],
  summary: {},
  files: [],
  filteredFiles: [],
  filteredFileCount: 0,
  filteredSampleCount: 0,
  selectedFiles: new Set(),
  expandedFiles: new Set(),
  reconcile: null,
  reconcilePathOptions: { folders: [], files: [] },
  labelingFile: null,
  labelProjects: [],
  labelProjectId: "",
  labelProjectQueues: new Map(),
  labelingFiles: [],
  labelLayoutSettings: new Map(),
  labelingAnalysisSample: "",
  labelingAnalysisFile: null,
  labelingAnalysisCards: [],
  labelingAnalysisKind: "",
  labelingQueue: [],
  labelingAllQueue: [],
  labelingQueueLabels: new Map(),
  labelingQueueIndex: -1,
  labelingQueueLoading: false,
  labelingPickerPending: false,
  labelingConfigLoading: false,
  labelingResolveGeneration: 0,
  labelingSessionPath: "",
  labelingSessionRequest: null,
  labelingHistoryPath: "",
  labelingHistorySelected: false,
  labelingOutputDirectory: "",
  labelingSourceSelection: null,
  labelingRestoredQueuePending: false,
  labelingRestoredLine: "",
  labelingPlaybackRate: 1,
  labelingPlaybackVolume: 1,
  labelingLowFrequencyFilter: false,
  labelTaxonomy: { path: "", sourcePath: "", results: [], reasons: [] },
  reconcilePathRequestToken: 0,
  reconcileRequestToken: 0,
  importPreviewId: null,
  importWizardStep: 1,
  updateWizardStep: 1,
  currentDatabase: null,
  panelDatabaseUid: new URLSearchParams(window.location.search).get("database_uid"),
  currentView: "databases",
  dataPanelOpen: false,
  updateDialog: {
    entryMode: "normal",
    allowed: { conditions: true, path: true },
    enabled: { conditions: true, path: false },
    selectedFileUids: [],
    singleFile: false,
    conditionSourceKey: "",
    scopeFiles: [],
  },
  conditionOptions: {},
  conditionOptionsDatabaseUid: "",
  conditionOptionsRequestToken: 0,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const deletedExtraFieldsByEditor = new WeakMap();
let reconcileFileSearchTimer = null;
let labelWaveformLoadTask = null;
let labelWaveformLoadGeneration = -1;
let labelWaveformLoadToken = 0;

function saveLastLabelSettings() {
  const settings = {
    sessionJsonPath: $("#labelSelectedSessionPath")?.value || "",
    historyPath: state.labelingHistorySelected ? state.labelingHistoryPath : "",
    historySelected: state.labelingHistorySelected,
    taxonomyPath: state.labelTaxonomy.sourcePath || "",
    sourceType: $("#labelSourceType")?.value || "operator",
    sourceIdentity: $("#labelSourceIdentity")?.value || "",
    fileType: $("#labelFileType")?.value || "tdms",
    wavProfile: $("#labelWavProfileSelect")?.value || "",
    line: $("#labelLineSelect")?.value || "",
    sourceSelection: state.labelingSourceSelection,
    projectId: state.labelProjectId,
    queueIndex: state.labelingQueueIndex,
  };
  try { localStorage.setItem(LAST_LABEL_SETTINGS_KEY, JSON.stringify(settings)); }
  catch { notify("上次设置未能保存到浏览器，请重新选择文件", "error"); }
}
const AI2_SPECTRUM_COLORS = Object.freeze([
  [0.00, "#0d0887"],
  [0.20, "#5b02a3"],
  [0.40, "#9a179b"],
  [0.60, "#cc4778"],
  [0.80, "#ed7953"],
  [0.92, "#fdb42f"],
  [1.00, "#f0f921"],
]);
const LABEL_EVENT_COLORS = Object.freeze([
  "#f97316",
  "#0ea5e9",
  "#8b5cf6",
  "#16a34a",
  "#e11d48",
  "#ca8a04",
  "#0891b2",
  "#db2777",
]);

const CONDITION_FIELD_DEFINITIONS = Object.freeze([
  { key: "line", label: "产线（line）", type: "text", filterPlaceholder: "可用逗号输入多个值" },
  { key: "device_id", label: "设备（device_id）", type: "text", filterPlaceholder: "采集设备 ID" },
  { key: "model_name", label: "项目（model_name）", type: "text" },
  { key: "reference", label: "型号（reference）", type: "text" },
  { key: "load_value", label: "负载（load_value）", type: "number", step: "any" },
  { key: "load_unit", label: "负载单位（load_unit）", type: "text" },
  { key: "speed_ratio", label: "转速比（speed_ratio）", type: "number", step: "any" },
  { key: "acquired_at", label: "采集时间（timestamp）", type: "datetime-local" },
]);

const CONDITION_CARD_PERMISSIONS = Object.freeze({
  import: { purpose: "create", allowCustomFields: true },
  update: { purpose: "patch", allowCustomFields: true },
  filter: { purpose: "filter", allowCustomFields: false },
});

const LABEL_FILTER_DEFINITIONS = Object.freeze([
  { key: "label_source", label: "标签来源" },
  { key: "label_result", label: "标签结果" },
  { key: "label_reason", label: "标签原因" },
  { key: "annotator_id", label: "标注人" },
  { key: "label_status", label: "标签状态" },
]);

function multiSelectFieldMarkup(field, { disabled = false } = {}) {
  const summary = disabled ? "待标签数据" : "正在读取…";
  const labelId = `${field.key}-multi-label`;
  const statusId = `${field.key}-multi-status`;
  return `<div class="multi-select-field" data-multi-select-field="${escapeHtml(field.key)}">
    <span id="${labelId}" class="multi-select-label">${escapeHtml(field.label)}</span>
    <details class="multi-select${disabled ? " is-disabled" : ""}" data-multi-select${disabled ? ' aria-disabled="true"' : ""}>
      <summary aria-labelledby="${labelId} ${statusId}"${disabled ? ' aria-disabled="true" tabindex="-1"' : ""}><span id="${statusId}" data-selection-label>${summary}</span></summary>
      <div class="multi-select-menu">
        <div class="multi-select-menu-head"><span>${escapeHtml(field.label)}</span><button class="multi-select-clear" type="button" data-clear-multi-select>清空</button></div>
        <fieldset class="multi-select-option-set"><legend class="visually-hidden">${escapeHtml(field.label)}多选项</legend><div class="multi-select-options" data-multi-select-options><span class="multi-select-empty">${disabled ? "标签功能尚未启用" : "正在读取选项…"}</span></div></fieldset>
      </div>
    </details>
  </div>`;
}

function conditionFieldMarkup(field, mode) {
  const filterMode = mode === "filter";
  if (filterMode) return multiSelectFieldMarkup(field);
  const attributes = filterMode
    ? `name="${field.key}" data-condition-filter="${field.key}"`
    : `data-condition="${field.key}"`;
  const placeholder = filterMode && field.filterPlaceholder
    ? ` placeholder="${escapeHtml(field.filterPlaceholder)}"`
    : "";
  const step = field.step ? ` step="${field.step}"` : "";
  return `<label><span>${escapeHtml(field.label)}</span><input ${attributes} type="${field.type}"${step}${placeholder}></label>`;
}

function mountConditionCard(host, mode) {
  const permission = CONDITION_CARD_PERMISSIONS[mode];
  if (!host || !permission) return;
  const template = $("#conditionCardTemplate");
  const fragment = template.content.cloneNode(true);
  const editor = $(".condition-editor", fragment);
  editor.dataset.conditionMode = mode;
  editor.dataset.conditionPurpose = permission.purpose;
  $("[data-condition-fields]", fragment).innerHTML = CONDITION_FIELD_DEFINITIONS
    .map(field => conditionFieldMarkup(field, mode))
    .join("");
  const extraHeading = $("[data-extra-fields-heading]", fragment);
  const extraFields = $("[data-extra-fields]", fragment);
  if (!permission.allowCustomFields) {
    extraHeading.remove();
    extraFields.remove();
  }
  host.replaceChildren(fragment);
  if (permission.allowCustomFields) {
    const headingText = $("strong", extraHeading);
    if (headingText) headingText.textContent = "其他文件属性（metadata）";
    const addButton = $(".add-extra-field-button", host);
    if (addButton) addButton.textContent = "＋ 添加 metadata 字段";
  }
  host.dataset.conditionPermission = mode;
  host.dataset.conditionEditor = "";
  bindMultiSelectDetails(host);
}

function mountConditionCards() {
  $$("[data-condition-card]").forEach(host => mountConditionCard(host, host.dataset.conditionCard));
  const labelHost = $("#labelFilterFields");
  if (labelHost) {
    labelHost.innerHTML = LABEL_FILTER_DEFINITIONS.map(field => multiSelectFieldMarkup(field, { disabled: true })).join("");
    bindMultiSelectDetails(labelHost);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function firstDefined(source, keys, fallback = "") {
  for (const key of keys) {
    if (source && source[key] !== undefined && source[key] !== null) return source[key];
  }
  return fallback;
}

function asArray(value, keys = []) {
  if (Array.isArray(value)) return value;
  for (const key of keys) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  return [];
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : "—";
}

function formatBytes(value) {
  let number = Number(value);
  if (!Number.isFinite(number) || number < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let index = 0;
  while (number >= 1024 && index < units.length - 1) {
    number /= 1024;
    index += 1;
  }
  const digits = index === 0 ? 0 : number >= 100 ? 0 : number >= 10 ? 1 : 2;
  return `${number.toFixed(digits)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}

async function request(path, options = {}) {
  const init = { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
  if (init.body && !(init.body instanceof FormData) && typeof init.body !== "string") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : { message: await response.text() };
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail) ? detail.map(item => `${item.loc?.join(".") || "参数"}：${item.msg || "无效"}`).join("；")
      : detail?.message || payload?.error || payload?.message || `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return payload;
}

let noticeTimer;
function notify(message, type = "success", sticky = false) {
  const target = $("#globalNotice");
  target.textContent = message;
  target.hidden = false;
  target.classList.toggle("is-error", type === "error");
  clearTimeout(noticeTimer);
  if (!sticky) noticeTimer = setTimeout(() => { target.hidden = true; }, 5200);
}

function setBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.busyHtml = button.innerHTML;
    button.dataset.wasDisabled = button.disabled ? "true" : "false";
    button.dataset.label = button.textContent;
    const cardLabel = button.classList.contains("new-database-card") ? $(".card-arrow", button) : null;
    if (cardLabel) cardLabel.textContent = busyText;
    else button.textContent = busyText;
    button.disabled = true;
    button.classList.add("loading");
  } else {
    if (button.classList.contains("new-database-card") && button.dataset.busyHtml) {
      button.innerHTML = button.dataset.busyHtml;
    } else {
      button.textContent = button.dataset.label || button.textContent;
    }
    button.disabled = button.dataset.wasDisabled === "true";
    button.classList.remove("loading");
    delete button.dataset.busyHtml;
    delete button.dataset.wasDisabled;
  }
}

function switchView(name) {
  const view = $(`#view-${name}`);
  if (!view) return;
  const databaseArea = ["databases", "database", "data-panel"].includes(name);
  $$(".nav-item").forEach(item => item.classList.toggle(
    "is-active",
    item.dataset.view === name || (item.dataset.view === "databases" && databaseArea),
  ));
  $$(".view").forEach(item => item.classList.toggle("is-active", item === view));
  $(".label-workflow-tabs").classList.toggle("is-current", name === "labeling");
  $("#pageTitle").textContent = name === "labeling" ? "音频标注" : "数据库";
  $("#pageEyebrow").textContent = "AI-3.0";
  if (name === "labeling") document.title = "音频标注 · AI-3.0";
  const workspaceTabs = $("#databaseWorkspaceTabs");
  if (workspaceTabs) workspaceTabs.hidden = !["database", "data-panel"].includes(name);
  const dataTab = $("#dataPanelWorkspaceTab");
  if (dataTab) dataTab.hidden = !state.dataPanelOpen;
  $$(".workspace-tab").forEach(tab => tab.classList.toggle("is-active", tab.dataset.workspaceView === name));
  state.currentView = name;
  document.body.dataset.currentView = name;
  if (name !== "labeling") releaseInactiveLabelWaveforms();
  history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name !== "labeling") renderDatabaseContext();
}

async function switchTopLevelView(name) {
  switchView(name);
  if (name === "labeling") {
    await loadLabelProjects();
    switchLabelWorkflowTab("projects");
    return;
  }
  if (!state.databases.length) await refreshAll(false);
}

function activeDatabase(status = state.status) {
  return status.active_database || status.database || status.current_database || {};
}

function selectedDatabase() {
  if (state.panelDatabaseUid) {
    const panelDatabase = state.databases.find(database => database.uid === state.panelDatabaseUid);
    return panelDatabase || null;
  }
  return state.currentDatabase;
}

function scopedDatabaseUid() {
  return state.panelDatabaseUid || selectedDatabase()?.uid || "";
}

function renderDatabaseContext() {
  const database = selectedDatabase();
  const storageReady = storageIsReady();
  const name = database?.name || "未打开数据库";
  const path = database?.pendingPath
    ? "尚未指定数据库路径"
    : database?.databaseRoot || database?.path || "请先从主页打开或新建数据库。";
  $("#activeDatabaseName").textContent = database?.name || "未打开";
  $("#detailDatabaseName").textContent = name;
  $("#detailDatabasePath").textContent = path;
  $("#detailDatabaseNameInput").value = database?.name || "";
  $("#detailDatabaseRootInput").value = database?.pendingPath ? "尚未指定" : database?.databaseRoot || "";
  $("#detailSqlitePath").textContent = database?.pendingPath
    ? "选择数据库路径后创建"
    : database?.sqlitePath || database?.path || "—";
  $("#importDatabaseName").textContent = name;
  $("#labelImportDatabaseName").textContent = name;
  $("#dataPanelDatabaseName").textContent = database ? `${database.name} · 数据面板` : "数据面板";
  $("#dataPanelDatabasePath").textContent = database?.databaseRoot || database?.path || "";
  $("#loadLabelTdmsButton").disabled = false;
  $("#selectLabelTdmsButton").disabled = false;
  $("#openDataMaintenanceButton").disabled = !database;
  $("#exportDatabaseButton").disabled = !database || database.pendingPath;
  for (const selector of ["#openLabelImportButton", "#openDataPanelButton"]) {
    $(selector).disabled = !database || database.pendingPath;
  }
  $("#saveDatabaseMetaButton").disabled = !database;
  $("#selectStorageFolderButton").disabled = !database;
  $("#reconcilePreviewButton").disabled = !database || !storageReady;
  $("#reconcilePreviewButton").title = database && !storageReady ? "请先选择可用的数据文件夹" : "";
  updateReconcileHint();
  if (state.currentView === "data-panel") {
    document.title = database ? `${database.name} · 数据面板` : "数据面板";
  } else if (state.currentView === "labeling") {
    document.title = "音频标注 · AI-3.0";
  } else {
    document.title = "AI-3.0 · 数据库";
  }
}

async function loadStatus() {
  state.status = await request(API.status);
  const database = activeDatabase();
  if (firstDefined(database, ["database_uid", "uid", "id"], "")) {
    state.currentDatabase = databaseRecord(database);
  } else if (!state.panelDatabaseUid) {
    state.currentDatabase = null;
  }
  const profiles = asArray(state.status, ["sample_profiles", "profiles"]);
  if (profiles.length) populateProfiles(profiles);
  renderDatabaseContext();
  return state.status;
}

async function loadStorage() {
  state.storage = await request(API.storage);
  const selected = Boolean(firstDefined(
    state.storage,
    ["selected", "storage_selected"],
    firstDefined(state.storage, ["root_path", "path", "storage_root"], ""),
  ));
  const mounted = Boolean(firstDefined(state.storage, ["mounted", "available", "is_mounted"], false));
  const path = firstDefined(state.storage, ["root_path", "path", "storage_root"], "") || "尚未选择数据文件夹";
  const storageId = selected ? firstDefined(state.storage, ["storage_id", "id"], "—") : "—";
  const free = firstDefined(state.storage, ["free_bytes", "available_bytes", "disk_free_bytes"], null);
  const total = firstDefined(state.storage, ["total_bytes", "disk_total_bytes"], null);

  $("#storagePath").textContent = path;
  $("#storageId").textContent = storageId;
  $("#storageFree").textContent = formatBytes(free);
  $("#storageFreeDetail").textContent = Number.isFinite(Number(total)) ? `总容量 ${formatBytes(total)}` : "容量信息不可用";
  $("#storageMessage").textContent = !selected
    ? "尚未配置数据库路径。"
    : mounted
      ? "数据文件夹可访问，可进行数据库与文件操作。"
      : firstDefined(state.storage, ["message", "error"], "所选数据文件夹当前不可访问；不会把文件批量标记为缺失。");
  $("#storageBadge").textContent = !selected ? "未选择" : mounted ? "可用" : "不可用";
  $("#storageHero").classList.toggle("is-unmounted", selected && !mounted);
  $("#sidebarMountDot").className = `status-dot ${!selected ? "is-unknown" : mounted ? "is-good" : "is-bad"}`;
  $("#sidebarMountText").textContent = !selected ? "尚未选择数据文件夹" : mounted ? "数据文件夹可用" : "数据文件夹不可用";
  $("#selectStorageFolderButton").textContent = selected ? "修改数据库路径" : "选择数据库路径";
  renderDatabaseContext();
  return state.storage;
}

function storageIsSelected() {
  return Boolean(firstDefined(
    state.storage,
    ["selected", "storage_selected"],
    firstDefined(state.storage, ["root_path", "path", "storage_root"], ""),
  ));
}

function storageIsReady() {
  return storageIsSelected() && Boolean(firstDefined(state.storage, ["mounted", "available", "is_mounted"], false));
}

function databaseRecord(raw) {
  const sqlitePath = firstDefined(raw, ["sqlite_path", "path", "database_path"], "");
  return {
    name: firstDefined(raw, ["database_name", "name"], "未命名数据库"),
    path: sqlitePath,
    sqlitePath,
    databaseRoot: firstDefined(raw, ["database_root", "storage_root", "root_path"], ""),
    uid: firstDefined(raw, ["database_uid", "uid", "id"], ""),
    updatedAt: firstDefined(raw, ["updated_at", "last_opened_at", "created_at"], ""),
    active: Boolean(firstDefined(raw, ["active", "is_active", "opened"], false)),
    storageId: firstDefined(raw, ["default_storage_id", "storage_id"], ""),
    schemaVersion: firstDefined(raw, ["schema_version"], ""),
    error: firstDefined(raw, ["error"], ""),
    pendingPath: Boolean(firstDefined(raw, ["pending_path"], false)),
    lines: asArray(raw, ["lines"]),
  };
}

async function loadDatabases() {
  const payload = await request(API.databases);
  state.databases = asArray(payload, ["databases", "items", "results"]).map(databaseRecord);
  renderDatabases();
  renderDatabaseContext();
  return state.databases;
}

async function selectDatabaseFolder(button = null) {
  setBusy(button, true, "选择中…");
  try {
    const selection = await request(API.databaseSelectFolder, { method: "POST", body: {} });
    if (selection?.cancelled || selection?.selected === false) return false;
    const hasReturnedList = ["databases", "items", "results"].some(key => Array.isArray(selection?.[key]));
    const returned = asArray(selection, ["databases", "items", "results"]);
    const databases = hasReturnedList
      ? returned.map(databaseRecord)
      : await loadDatabases();
    if (hasReturnedList) {
      state.databases = databases;
      renderDatabases();
      renderDatabaseContext();
    }
    const opened = databases.find(database => database.path && !database.error);
    if (!opened) throw new Error("所选文件夹中没有可打开的数据库");
    state.currentDatabase = opened;
    state.panelDatabaseUid = null;
    state.dataPanelOpen = false;
    await refreshAll(false);
    switchView("database");
    notify("数据库已打开");
    return true;
  } catch (error) {
    if (!/(取消|cancel)/i.test(error.message)) notify(error.message, "error", true);
    return false;
  } finally {
    setBusy(button, false);
  }
}

async function selectStorageFolder(button = null) {
  if (!selectedDatabase()) {
    notify("请先打开数据库，再设置数据库路径", "error");
    return false;
  }
  setBusy(button, true, "选择中…");
  try {
    const selection = await request(API.storageSelectFolder, { method: "POST", body: {} });
    if (selection?.cancelled || selection?.selected === false) return false;
    await Promise.all([loadStorage(), loadStatus(), loadDatabases()]);
    notify(storageIsReady() ? "数据库路径已更新" : "数据库路径已记录，但当前不可访问");
    return true;
  } catch (error) {
    if (!/(取消|cancel)/i.test(error.message)) notify(error.message, "error", true);
    return false;
  } finally {
    setBusy(button, false);
    if (button) button.textContent = storageIsSelected() ? "修改数据库路径" : "选择数据库路径";
    renderDatabaseContext();
  }
}

function renderDatabases() {
  const target = $("#databaseList");
  if (!state.databases.length) {
    target.className = "database-list empty-state";
    target.textContent = "尚未发现数据库。可以先新建记录，再选择数据库路径。";
    return;
  }
  target.className = "database-list";
  target.innerHTML = state.databases.map((database, index) => `
    <div class="database-item">
      <div><strong>${escapeHtml(database.name)}</strong><small class="database-path" title="${escapeHtml(database.path)}">${escapeHtml(database.pendingPath ? "尚未指定数据库路径" : database.path || "路径未返回")}</small>${database.error ? `<small class="database-error">${escapeHtml(database.error)}</small>` : ""}</div>
      <div class="database-item-actions"><button class="button button-secondary open-database-button" type="button" data-index="${index}" ${database.error ? "disabled" : ""}>打开</button><button class="button button-ghost database-remove-button" type="button" data-index="${index}">删除记录</button></div>
    </div>`).join("");
}

async function loadSummary() {
  const params = new URLSearchParams();
  if (scopedDatabaseUid()) params.set("database_uid", scopedDatabaseUid());
  const url = params.size ? `${API.summary}?${params.toString()}` : API.summary;
  state.summary = await request(url);
  const files = firstDefined(state.summary, ["file_count", "files", "total_files"], 0);
  const samples = firstDefined(state.summary, ["sample_count", "samples", "total_samples"], 0);
  const runs = firstDefined(state.summary, ["import_run_count", "import_runs", "run_count"], 0);
  const issues = firstDefined(state.summary, ["issue_count", "issues", "problem_count"], null);
  const inferredIssues = ["missing_count", "changed_count", "unreadable_count"]
    .reduce((sum, key) => sum + Number(state.summary[key] || 0), 0);
  if ($("#metricFiles")) $("#metricFiles").textContent = formatNumber(files);
  if ($("#metricSamples")) $("#metricSamples").textContent = formatNumber(samples);
  if ($("#metricRuns")) $("#metricRuns").textContent = formatNumber(runs);
  if ($("#metricIssues")) $("#metricIssues").textContent = formatNumber(issues ?? inferredIssues);
  $("#panelMetricFiles").textContent = formatNumber(files);
  $("#panelMetricSamples").textContent = formatNumber(samples);
  $("#panelMetricRuns").textContent = formatNumber(runs);
  $("#panelMetricIssues").textContent = formatNumber(issues ?? inferredIssues);
  return state.summary;
}

function normalizeSample(sample) {
  return {
    sampleId: firstDefined(sample, ["sample_id", "id"], "—"),
    scope: firstDefined(sample, ["sample_scope", "scope"], "—"),
    displayName: firstDefined(sample, ["display_name", "name"], ""),
    rate: firstDefined(sample, ["sampling_rate_hz", "sampling_rate", "rate"], null),
    duration: firstDefined(sample, ["duration_s", "duration"], null),
    locator: firstDefined(sample, ["locator_json", "locator"], {}),
    availability: firstDefined(sample, ["availability_status", "availability"], ""),
  };
}

function normalizeFile(file) {
  const conditionSource = file.conditions || file;
  const samples = asArray(file, ["samples", "sample_rows", "children"]).map(normalizeSample);
  const metadata = firstDefined(file, ["metadata_json", "metadata"], {}) || {};
  return {
    uid: firstDefined(file, ["file_uid", "uid", "id"], ""),
    storageId: firstDefined(file, ["storage_id"], "wuxi_raw"),
    relativePath: firstDefined(file, ["relative_path", "path"], ""),
    conditions: {
      line: firstDefined(conditionSource, ["line"], ""),
      device_id: firstDefined(conditionSource, ["device_id"], ""),
      model_name: firstDefined(conditionSource, ["model_name"], ""),
      reference: firstDefined(conditionSource, ["reference"], ""),
      load_value: firstDefined(conditionSource, ["load_value"], ""),
      load_unit: firstDefined(conditionSource, ["load_unit"], ""),
      speed_ratio: firstDefined(conditionSource, ["speed_ratio"], ""),
      acquired_at: firstDefined(conditionSource, ["timestamp", "acquired_at"], ""),
      extra_fields: firstDefined(
        conditionSource,
        ["extra_fields"],
        firstDefined(metadata, ["condition_extras"], {}),
      ) || {},
    },
    recordStatus: firstDefined(file, ["record_status"], "active"),
    availability: firstDefined(file, ["availability_status", "availability"], "present"),
    integrity: firstDefined(file, ["integrity_status", "integrity"], "verified"),
    payloadSize: firstDefined(file, ["payload_size_bytes"], null),
    storedSize: firstDefined(file, ["stored_size_bytes", "size_bytes"], null),
    updatedAt: firstDefined(file, ["updated_at", "last_seen_at"], ""),
    sampleCount: Number(firstDefined(file, ["sample_count"], samples.length) || 0),
    sampleDiscoveryStatus: firstDefined(metadata, ["sample_discovery_status"], ""),
    sampleIssues: asArray(metadata, ["sample_issues"]),
    samples,
  };
}

async function loadFiles() {
  const filter = filterValues();
  const params = new URLSearchParams({ limit: "5000" });
  if (scopedDatabaseUid()) params.set("database_uid", scopedDatabaseUid());
  if (filter.filename?.trim()) params.set("filename", filter.filename.trim());
  for (const key of ["line", "device_id", "model_name", "reference", "load_value", "load_unit", "speed_ratio", "acquired_at"]) {
    const values = Array.isArray(filter[key]) ? filter[key] : valuesFromText(filter[key]);
    values.forEach(value => params.append(key, value));
  }
  for (const [formKey, apiKey] of [
    ["availability_status", "availability_status"], ["integrity_status", "integrity_status"],
  ]) {
    if (filter[formKey] !== "") params.set(apiKey, filter[formKey]);
  }
  const payload = await request(`${API.files}?${params.toString()}`);
  state.files = asArray(payload, ["files", "items", "results"]).map(normalizeFile);
  state.filteredFileCount = Number(firstDefined(payload, ["file_count", "total_files"], state.files.length));
  state.filteredSampleCount = Number(firstDefined(payload, ["sample_count", "total_samples"], 0));
  state.selectedFiles.clear();
  state.filteredFiles = state.files;
  renderFiles();
  return state.files;
}

function valuesFromText(text) {
  return String(text || "").split(/[,，]/).map(item => item.trim()).filter(Boolean);
}

function bindMultiSelectDetails(root) {
  $$("[data-multi-select]", root).forEach(details => {
    $("summary", details)?.addEventListener("click", event => {
      if (details.classList.contains("is-disabled") || details.classList.contains("is-empty")) event.preventDefault();
    });
    details.addEventListener("toggle", () => {
      if (!details.open) return;
      $$("[data-multi-select]").forEach(other => {
        if (other !== details) other.open = false;
      });
    });
    details.addEventListener("keydown", event => {
      if (event.key !== "Escape" || !details.open) return;
      details.open = false;
      $("summary", details)?.focus();
    });
  });
}

function multiSelectField(key) {
  return $$("[data-multi-select-field]").find(field => field.dataset.multiSelectField === key) || null;
}

function updateMultiSelectSummary(field) {
  if (!field) return;
  const details = $("[data-multi-select]", field);
  const label = $("[data-selection-label]", field);
  if (!details || !label) return;
  if (details.classList.contains("is-disabled")) {
    label.textContent = "待标签数据";
    return;
  }
  const options = $$("input[type=checkbox]", field);
  const selected = options.filter(input => input.checked);
  if (!options.length) label.textContent = "暂无选项";
  else if (!selected.length) label.textContent = "全部";
  else if (selected.length === 1) label.textContent = selected[0].dataset.optionLabel || selected[0].value;
  else label.textContent = `已选 ${selected.length} 项`;
}

function updateAllMultiSelectSummaries(root = document) {
  $$("[data-multi-select-field]", root).forEach(updateMultiSelectSummary);
}

function renderConditionFilterOptions() {
  CONDITION_FIELD_DEFINITIONS.forEach(definition => {
    const field = multiSelectField(definition.key);
    if (!field) return;
    const details = $("[data-multi-select]", field);
    const optionsHost = $("[data-multi-select-options]", field);
    const selected = new Set(
      $$("input[type=checkbox]:checked", field).map(input => input.value),
    );
    const values = asArray(state.conditionOptions[definition.key], ["values"]);
    const stableValues = [...new Map(values
      .filter(value => value !== null && value !== undefined && String(value) !== "")
      .map(value => [String(value), value])).values()];
    details.classList.toggle("is-empty", stableValues.length === 0);
    optionsHost.innerHTML = stableValues.length
      ? stableValues.map(value => {
        const raw = String(value);
        const display = definition.key === "acquired_at" ? formatDate(raw) : raw;
        return `<label class="multi-select-option"><input type="checkbox" name="${definition.key}" value="${escapeHtml(raw)}" data-option-label="${escapeHtml(display)}" ${selected.has(raw) ? "checked" : ""}><span>${escapeHtml(display)}</span></label>`;
      }).join("")
      : '<span class="multi-select-empty">当前数据库暂无可选值</span>';
    updateMultiSelectSummary(field);
  });
}

async function loadConditionOptions() {
  const databaseUid = scopedDatabaseUid();
  if (!databaseUid) {
    state.conditionOptions = {};
    state.conditionOptionsDatabaseUid = "";
    renderConditionFilterOptions();
    return {};
  }
  const requestToken = ++state.conditionOptionsRequestToken;
  const payload = await request(`${API.conditionOptions}?${new URLSearchParams({ database_uid: databaseUid })}`);
  if (requestToken !== state.conditionOptionsRequestToken || scopedDatabaseUid() !== databaseUid) return null;
  const source = payload.options || payload.condition_options || payload;
  state.conditionOptions = Object.fromEntries(CONDITION_FIELD_DEFINITIONS.map(definition => [
    definition.key,
    asArray(source[definition.key], ["values"]),
  ]));
  state.conditionOptionsDatabaseUid = databaseUid;
  renderConditionFilterOptions();
  return state.conditionOptions;
}

function filterValues() {
  const data = new FormData($("#fileFilterForm"));
  const result = Object.fromEntries(data.entries());
  CONDITION_FIELD_DEFINITIONS.forEach(definition => {
    result[definition.key] = data.getAll(definition.key);
  });
  return result;
}

function applyFileFilters() {
  return loadFiles();
}

const statusLabels = {
  active: "有效", superseded: "已替换", archived: "已归档",
  present: "存在", missing: "缺失",
  verified: "已验证", changed: "已变化", unreadable: "不可读",
};

function statusBadge(value) {
  const label = statusLabels[value] || value || "未知";
  const tone = ["missing", "changed", "unreadable"].includes(value) ? "bad"
    : ["verified", "present", "active"].includes(value) ? "good" : "neutral";
  return `<span class="badge badge-${tone}">${escapeHtml(label)}</span>`;
}

function conditionTags(conditions) {
  const labels = {
    line: "产线", device_id: "设备", model_name: "项目", reference: "型号",
    load_value: "负载", load_unit: "负载单位", speed_ratio: "转速比", acquired_at: "采集时间",
  };
  const items = [];
  Object.entries(labels).forEach(([key, label]) => {
    let value = conditions[key];
    if (value === "" || value === null || value === undefined) return;
    if (key === "acquired_at") value = formatDate(value);
    items.push(`<span class="condition-tag"><b>${escapeHtml(label)}</b> ${escapeHtml(value)}</span>`);
  });
  Object.entries(conditions.extra_fields || {}).forEach(([key, value]) => {
    if (value === "" || value === null || value === undefined) return;
    items.push(`<span class="condition-tag"><b>${escapeHtml(key)}</b> ${escapeHtml(value)}</span>`);
  });
  return items.length ? items.join("") : '<span class="condition-tag">未填写工况</span>';
}

function sampleMarkup(file) {
  if (!file.samples.length && file.sampleDiscoveryStatus === "pending_channel_selection") {
    return `<div class="sample-list"><h3>待选择 WAV 通道</h3><p>${escapeHtml(file.sampleIssues.join("；") || "多通道 WAV 不会默认绑定第一通道。")}</p></div>`;
  }
  if (!file.samples.length) return '<div class="sample-list"><h3>当前文件没有已登记样本。</h3></div>';
  return `<div class="sample-list"><h3>${file.samples.length} 个样本</h3><div class="sample-grid">${file.samples.map(sample => {
    const locator = typeof sample.locator === "string" ? sample.locator : JSON.stringify(sample.locator || {});
    const rate = Number(sample.rate) ? `${formatNumber(sample.rate)} Hz` : "采样率未知";
    const duration = Number(sample.duration) ? `${Number(sample.duration).toFixed(3)} s` : "时长未知";
    return `<div class="sample-card"><strong>${escapeHtml(sample.sampleId)}</strong><span>${escapeHtml(sample.displayName || sample.scope)}</span><span>${escapeHtml(rate)}</span><span>${escapeHtml(duration)}</span><code>${escapeHtml(locator)}</code></div>`;
  }).join("")}</div></div>`;
}

function renderFiles() {
  const target = $("#fileRows");
  $("#fileCountText").textContent = `${formatNumber(state.filteredFileCount)} 个文件`;
  $("#sampleCountText").textContent = `${formatNumber(state.filteredSampleCount)} 个样本`;
  $("#editSelectedConditionsButton").disabled = state.selectedFiles.size === 0;
  $("#selectAllFiles").checked = state.filteredFiles.length > 0 && state.filteredFiles.every(file => state.selectedFiles.has(file.uid));
  $("#selectAllFiles").indeterminate = state.selectedFiles.size > 0 && !$("#selectAllFiles").checked;

  if (!state.filteredFiles.length) {
    target.innerHTML = '<tr><td colspan="6" class="empty-cell">没有符合当前筛选条件的文件。</td></tr>';
    return;
  }
  target.innerHTML = state.filteredFiles.map(file => {
    const name = file.relativePath.split("/").pop() || file.relativePath || file.uid;
    const expanded = state.expandedFiles.has(file.uid);
    return `<tr>
      <td class="cell-check"><input class="file-select" type="checkbox" data-uid="${escapeHtml(file.uid)}" aria-label="选择 ${escapeHtml(name)}" ${state.selectedFiles.has(file.uid) ? "checked" : ""}></td>
      <td><strong class="file-name" title="${escapeHtml(name)}">${escapeHtml(name)}</strong><span class="file-path" title="${escapeHtml(file.relativePath)}">${escapeHtml(file.storageId)}:${escapeHtml(file.relativePath)}</span></td>
      <td><div class="condition-tags">${conditionTags(file.conditions)}</div></td>
      <td><div class="status-stack">${statusBadge(file.recordStatus)}${statusBadge(file.availability)}${statusBadge(file.integrity)}${file.sampleDiscoveryStatus === "pending_channel_selection" ? '<span class="badge badge-warn">待选通道</span>' : ""}</div></td>
      <td><strong>${formatNumber(file.sampleCount || file.samples.length)}</strong><div class="file-path">${formatBytes(file.storedSize)}</div></td>
      <td class="cell-action"><button class="expand-button" type="button" data-uid="${escapeHtml(file.uid)}">${expanded ? "收起" : "展开"}</button></td>
    </tr>${expanded ? `<tr class="sample-detail-row"><td colspan="6">${sampleMarkup(file)}</td></tr>` : ""}`;
  }).join("");
}

function collectConditions(root, includeEmpty = false) {
  const result = {};
  $$('[data-condition]', root).forEach(input => {
    const key = input.dataset.condition;
    let value = input.value.trim();
    if (value === "") {
      if (includeEmpty) result[key] = null;
      return;
    }
    if (["load_value", "speed_ratio"].includes(key) && value !== "") value = Number(value);
    result[key] = value;
  });
  const extraFields = {};
  deletedExtraFieldsByEditor.get(root)?.forEach(key => { extraFields[key] = null; });
  const currentKeys = new Set();
  $$(".extra-condition-row", root).forEach(row => {
    const key = $("[data-extra-key]", row)?.value.trim() || "";
    const value = $("[data-extra-value]", row)?.value.trim() || "";
    const originalKey = row.dataset.originalExtraKey || "";
    if (originalKey && originalKey !== key) extraFields[originalKey] = null;
    if (!key) return;
    if (currentKeys.has(key)) return;
    currentKeys.add(key);
    if (value !== "" || includeEmpty) extraFields[key] = value === "" ? null : value;
  });
  if (Object.keys(extraFields).length || includeEmpty) result.extra_fields = extraFields;
  return result;
}

function collectChangedConditions(root) {
  const baseline = state.updateDialog.conditionBaseline || {};
  const current = collectConditions(root, true);
  const changed = {};
  Object.keys(current).filter(key => key !== "extra_fields").forEach(key => {
    if (current[key] !== baseline[key]) changed[key] = current[key];
  });
  const beforeExtra = baseline.extra_fields || {};
  const afterExtra = current.extra_fields || {};
  const changedExtra = {};
  new Set([...Object.keys(beforeExtra), ...Object.keys(afterExtra)]).forEach(key => {
    const before = beforeExtra[key] ?? null;
    const after = afterExtra[key] ?? null;
    if (before !== after) changedExtra[key] = after;
  });
  if (Object.keys(changedExtra).length) changed.extra_fields = changedExtra;
  return changed;
}

function populateConditions(root, conditions = {}) {
  deletedExtraFieldsByEditor.set(root, new Set());
  $$('[data-condition]', root).forEach(input => {
    let value = conditions[input.dataset.condition] ?? "";
    if (input.type === "datetime-local" && value) value = String(value).slice(0, 16);
    input.value = value;
  });
  const rows = $("[data-extra-fields]", root);
  if (rows) {
    rows.innerHTML = "";
    Object.entries(conditions.extra_fields || {}).forEach(([key, value]) => addExtraConditionRow(root, key, value, true));
  }
}

function addExtraConditionRow(root, key = "", value = "", existing = false) {
  const host = $("[data-extra-fields]", root);
  if (!host) return;
  const row = document.createElement("div");
  row.className = "extra-condition-row";
  if (existing && key) row.dataset.originalExtraKey = key;
  row.innerHTML = `
    <input data-extra-key type="text" maxlength="80" placeholder="字段名称" value="${escapeHtml(key)}" aria-label="自定义字段名称">
    <input data-extra-value type="text" placeholder="字段值" value="${escapeHtml(value ?? "")}" aria-label="自定义字段值">
    <button class="icon-button remove-extra-field" type="button" aria-label="删除字段">×</button>`;
  host.append(row);
  $("[data-extra-key]", row)?.focus();
}

function populateProfiles(profiles) {
  const select = $("#importSampleProfile");
  const current = select.value;
  const normalized = profiles.map(profile => typeof profile === "string"
    ? { id: profile, name: profile }
    : { id: firstDefined(profile, ["id", "key", "name"], ""), name: firstDefined(profile, ["display_name", "label", "name", "id"], "") })
    .filter(profile => profile.id);
  const unique = [...new Map(normalized.map(profile => [profile.id, profile])).values()];
  if (!unique.some(profile => profile.id === "default")) unique.unshift({ id: "default", name: "默认 Up / Down 通道" });
  select.innerHTML = unique.map(profile => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)}</option>`).join("");
  if (unique.some(profile => profile.id === current)) select.value = current;
}

function renderImportResult(payload) {
  const result = payload.result || payload;
  const counts = [
    ["总计", firstDefined(result, ["total_count", "total"], 0)],
    ["成功", firstDefined(result, ["success_count", "registered_count", "success"], 0)],
    ["跳过", firstDefined(result, ["skip_count", "skipped_count", "skipped"], 0)],
    ["冲突", firstDefined(result, ["conflict_count", "conflicts"], 0)],
    ["失败", firstDefined(result, ["failed_count", "failure_count", "failed"], 0)],
  ];
  const cleanupItems = asArray(result, ["items"]).filter(item => item.status === "cleanup_pending");
  const cleanupActions = cleanupItems.length
    ? `<div class="toolbar-actions">${cleanupItems.map(item => `<button class="button button-secondary retry-cleanup" type="button" data-item-uuid="${escapeHtml(item.item_uuid)}">重试清理 ${escapeHtml(String(item.source_display_path || "源 TDMS").split("/").pop())}</button>`).join("")}</div>`
    : "";
  $("#importResult").innerHTML = `<div class="result-summary">${counts.map(([label, value]) => `<div><span>${label}</span><strong>${formatNumber(value)}</strong></div>`).join("")}</div>${cleanupActions}<pre class="result-log">${escapeHtml(JSON.stringify(result, null, 2))}</pre>`;
  $("#importResultPanel").hidden = false;
  $("#importResultPanel").scrollIntoView({ behavior: "smooth", block: "start" });
}

const reconcileMeta = {
  unchanged: ["未变化", "ignore"],
  pending_register: ["待登记", "register"],
  new: ["待登记", "register"],
  pending_compress: ["待压缩、待登记", "compress_and_register"],
  move: ["移动", "update_path"],
  moved: ["移动", "update_path"],
  missing: ["缺失", "mark_missing"],
  restored: ["重新出现", "mark_present"],
  recompressed: ["重新压缩", "accept_recompression"],
  content_replaced: ["内容替换", "supersede_and_register"],
  replaced: ["内容替换", "supersede_and_register"],
  ambiguous: ["无法判断", "manual_review"],
  conflict: ["冲突", "manual_review"],
};

function reconcileItems(payload) {
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.changes)) return payload.changes;
  const categories = payload?.categories || payload?.groups || {};
  return Object.entries(categories).flatMap(([category, items]) => {
    if (Array.isArray(items)) return items.map(item => ({ ...item, category: item.category || category }));
    return [];
  });
}

function renderReconcile(payload) {
  const items = reconcileItems(payload);
  state.reconcile = { ...payload, items };
  const previewId = firstDefined(payload, ["preview_id", "id"], "");
  const counts = { ...(payload.counts || {}) };
  items.forEach(item => {
    const category = firstDefined(item, ["category", "kind", "status"], "unknown");
    counts[category] = counts[category] ?? 0;
    if (!payload.counts) counts[category] += 1;
  });

  $("#reconcileEmpty").hidden = true;
  $("#reconcileWorkspace").hidden = false;
  $("#reconcilePreviewId").textContent = previewId ? `预览 ${previewId}` : "";
  $("#reconcileCounts").innerHTML = Object.entries(counts).map(([category, count]) => `
    <div class="category-card"><span>${escapeHtml(reconcileMeta[category]?.[0] || category)}</span><strong>${formatNumber(count)}</strong></div>`).join("");

  const target = $("#reconcileRows");
  if (!items.length) {
    target.innerHTML = '<tr><td colspan="4" class="empty-cell">目录与数据库一致，没有待处理差异。</td></tr>';
  } else {
    target.innerHTML = items.map((item, index) => {
      const category = firstDefined(item, ["category", "kind", "status"], "unknown");
      const meta = reconcileMeta[category] || [category, "manual_review"];
      const oldPath = firstDefined(item, ["old_relative_path", "relative_path", "source_relative_path", "path"], "—");
      const candidate = firstDefined(item, ["new_relative_path", "candidate_relative_path", "target_relative_path", "message", "detail"], "—");
      const candidates = asArray(item, ["candidates"]);
      const candidateCell = category === "ambiguous" && candidates.length
        ? `<select class="ambiguous-target">${candidates.map(path => `<option value="${escapeHtml(path)}">${escapeHtml(path)}</option>`).join("")}</select>`
        : `<code>${escapeHtml(candidate)}</code>`;
      const itemId = firstDefined(item, ["item_uuid", "item_id", "file_uid", "id"], `${category}:${oldPath}`);
      return `<tr data-index="${index}" data-item-id="${escapeHtml(itemId)}">
        <td><span class="badge ${["missing", "content_replaced", "ambiguous", "conflict"].includes(category) ? "badge-warn" : "badge-neutral"}">${escapeHtml(meta[0])}</span></td>
        <td><code>${escapeHtml(oldPath)}</code></td>
        <td>${candidateCell}</td>
        <td><select class="reconcile-action" data-default="${escapeHtml(meta[1])}">
          <option value="ignore">暂不处理</option>
          ${meta[1] !== "ignore" ? `<option value="${escapeHtml(meta[1])}">${escapeHtml(actionLabel(meta[1]))}</option>` : ""}
          ${category === "ambiguous" ? '<option value="update_path">选用候选路径</option>' : ""}
        </select></td>
      </tr>`;
    }).join("");
  }
  updateReconcileHint();
  syncUpdateBusinessUi();
}

function actionLabel(action) {
  return ({
    register: "登记", compress_and_register: "压缩并登记", update_path: "更新路径",
    mark_missing: "标记缺失", accept_recompression: "接受重新压缩",
    mark_present: "恢复为可用",
    supersede_and_register: "旧记录失效并登记新内容", manual_review: "标记待人工处理",
  })[action] || action;
}

function selectedReconcileActions() {
  return $$("#reconcileRows tr[data-index]").map(row => {
    const select = $(".reconcile-action", row);
    const item = state.reconcile?.items?.[Number(row.dataset.index)] || {};
    return {
      item_id: row.dataset.itemId,
      category: firstDefined(item, ["category", "kind", "status"], ""),
      action: select.value,
      target_relative_path: $(".ambiguous-target", row)?.value || firstDefined(item, ["new_relative_path", "candidate_relative_path", "target_relative_path"], ""),
    };
  }).filter(item => item.action !== "ignore");
}

function updateReconcileHint() {
  const count = state.reconcile ? selectedReconcileActions().length : 0;
  $("#reconcileSelectionHint").textContent = count ? `已选择 ${count} 项变更` : "未选择需要应用的变更";
  $("#reconcileApplyButton").disabled = count === 0 || !selectedDatabase() || !storageIsReady() || !updateBusinessEnabled("path");
}

async function refreshAll(showMessage = true) {
  const results = await Promise.allSettled([loadStatus(), loadStorage(), loadDatabases()]);
  if (selectedDatabase() && !selectedDatabase().pendingPath) {
    results.push(...await Promise.allSettled([loadSummary(), loadFiles(), loadConditionOptions()]));
  } else {
    state.summary = {};
    state.files = [];
    state.filteredFiles = [];
    state.filteredFileCount = 0;
    state.filteredSampleCount = 0;
  }
  const failed = results.filter(result => result.status === "rejected");
  if (failed.length) {
    notify(`有 ${failed.length} 项状态未能读取：${failed[0].reason?.message || "服务暂不可用"}`, "error", true);
  } else if (showMessage) {
    notify("状态已刷新");
  }
  renderDatabaseContext();
}

async function chooseDatabaseParent(button) {
  setBusy(button, true, "选择中…");
  try {
    const result = await request(API.databaseChooseParent, { method: "POST", body: {} });
    if (!result.cancelled) $("#createDatabaseParentPath").value = result.path || "";
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

async function createDatabase(button) {
  setBusy(button, true, "正在建立…");
  try {
    const result = await request(API.databaseCreateFromFolder, {
      method: "POST",
      body: { path: $("#createDatabaseParentPath").value, name: $("#createDatabaseName").value.trim() },
    });
    if (result?.cancelled || result?.selected === false) return;
    $("#createDatabaseDialog").close();
    const created = result.database || result;
    state.currentDatabase = databaseRecord(created);
    state.panelDatabaseUid = null;
    state.dataPanelOpen = false;
    await Promise.all([loadDatabases(), loadStatus(), loadStorage()]);
    switchView("database");
    notify(`数据库“${state.currentDatabase.name}”已创建`);
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

async function openDatabase(database, button) {
  if (!database?.uid) return;
  setBusy(button, true, "正在打开…");
  try {
    const opened = database.pendingPath
      ? await request(API.databaseActivate(database.uid), { method: "POST", body: {} })
      : await request(API.databaseOpen, { method: "POST", body: { path: database.path } });
    state.currentDatabase = databaseRecord(opened);
    state.panelDatabaseUid = null;
    state.dataPanelOpen = false;
    await refreshAll(false);
    switchView("database");
    notify("数据库已打开");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

async function removeDatabaseRecord(database, button) {
  if (!database?.uid) return;
  const confirmed = window.confirm(
    `从主页移除数据库“${database.name}”？\n\n只删除页面记录，不删除 SQLite 或原始文件。以后可通过“打开数据库”重新添加。`,
  );
  if (!confirmed) return;
  setBusy(button, true, "正在移除…");
  try {
    await request(API.databaseRemove(database.uid), { method: "DELETE" });
    if (selectedDatabase()?.uid === database.uid) {
      state.currentDatabase = null;
      state.panelDatabaseUid = null;
      state.dataPanelOpen = false;
    }
    await Promise.all([loadDatabases(), loadStatus(), loadStorage()]);
    switchView("databases");
    notify("已从主页移除；SQLite 和原始文件均未删除");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

async function saveDatabaseMetadata(event) {
  event.preventDefault();
  const database = selectedDatabase();
  const name = $("#detailDatabaseNameInput").value.trim();
  if (!database?.uid) return notify("请先打开数据库", "error");
  if (!name) return notify("数据库名称不能为空", "error");
  const button = $("#saveDatabaseMetaButton");
  setBusy(button, true, "正在保存…");
  try {
    const updated = await request(API.databaseUpdate(database.uid), {
      method: "PATCH",
      body: { name },
    });
    const normalized = databaseRecord(updated);
    if (state.currentDatabase?.uid === normalized.uid) state.currentDatabase = normalized;
    const index = state.databases.findIndex(item => item.uid === normalized.uid);
    if (index >= 0) state.databases[index] = normalized;
    renderDatabases();
    renderDatabaseContext();
    notify("数据库名称已更新，物理路径未改变");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

function openDataPanel() {
  const database = selectedDatabase();
  if (!database?.uid) return notify("请先打开数据库", "error");
  state.dataPanelOpen = true;
  $("#dataPanelWorkspaceTab").hidden = false;
  switchView("data-panel");
  Promise.allSettled([loadSummary(), loadFiles(), loadConditionOptions()]);
}

function closeDataPanel() {
  state.dataPanelOpen = false;
  state.panelDatabaseUid = null;
  $("#dataPanelWorkspaceTab").hidden = true;
  switchView("database");
}

function openRawImportDialog() {
  if (!selectedDatabase()) return notify("请先打开数据库", "error");
  if (!storageIsReady()) return notify("数据库路径当前不可访问", "error");
  renderDatabaseContext();
  $("#importPlacementLabel").textContent = "文件处理方式";
  $("#importForm").reset();
  populateConditions($("#importConditions"), {});
  populateImportLines();
  $("#createLineFields").hidden = true;
  applyImportLine();
  state.importPreviewId = null;
  state.importWizardStep = 1;
  $("#importResultPanel").hidden = true;
  $("#importProgress").hidden = true;
  $("#startImportButton").dataset.label = "生成导入预览";
  $("#startImportButton").textContent = "生成导入预览";
  updateImportSteps();
  $("#rawImportDialog").showModal();
}

function populateImportLines(selected = "") {
  const lines = selectedDatabase()?.lines || [];
  const select = $("#importLineSelect");
  select.innerHTML = '<option value="">请选择产线</option>'
    + lines.map(line => `<option value="${escapeHtml(line)}">${escapeHtml(line)}</option>`).join("");
  if (lines.includes(selected)) select.value = selected;
}

function populateLabelLines(selected = "") {
  const lines = asArray(state.status, ["tdms_lines"]);
  const select = $("#labelLineSelect");
  select.innerHTML = '<option value="">请选择产线</option>'
    + lines.map(line => `<option value="${escapeHtml(line)}">${escapeHtml(line)}</option>`).join("");
  select.disabled = !state.labelingQueue.length;
  if (lines.includes(selected)) select.value = selected;
}

function syncLabelLineField() {
  const isTdms = $("#labelFileType").value === "tdms";
  $("#labelLineField").hidden = !isTdms;
  $("#labelWavProfileField").hidden = true;
  if (!isTdms) {
    $("#labelLineSelect").value = "";
    $("#labelLineSelect").disabled = true;
  } else {
    populateLabelLines($("#labelLineSelect").value);
  }
}

function selectedLabelWavProfile() {
  return $("#labelFileType").value === "wav" ? $("#labelWavProfileSelect").value : "";
}

function loadedLabelWavProfile() {
  if (state.labelingFile && !state.labelingFile.absolute_path?.toLowerCase().endsWith(".wav")) return "";
  const profile = state.labelingFile?.metadata?.wav_profile;
  return profile === "motor" || profile === "rail" ? profile : selectedLabelWavProfile();
}

function labelFileForCard(card) {
  return (state.labelingFiles.length ? state.labelingFiles : [state.labelingFile])
    .find(file => file?.absolute_path === card?.dataset.labelFile) || null;
}

function groupLabelSources(paths, wavProfile, isWav = Boolean(wavProfile)) {
  if (!isWav) return paths.map(path => ({ path, paths: [path], labeled: false, wavProfile }));
  const groups = new Map();
  paths.forEach(path => {
    const filename = path.replaceAll("\\", "/").split("/").pop();
    const match = filename.match(/^(.+)-(rfw|rbw|ifw|rbf)\.wav$/i);
    const parent = path.slice(0, -filename.length);
    const key = match ? `${parent}${match[1]}` : path;
    if (!groups.has(key)) groups.set(key, { path, paths: [], labeled: false, wavProfile, displayName: match?.[1] || filename });
    groups.get(key).paths.push(path);
  });
  return [...groups.values()].map(item => {
    const order = path => /-(?:ifw|rfw)\.wav$/i.test(path) ? 0 : 1;
    item.paths.sort((left, right) => order(left) - order(right));
    item.path = item.paths[0];
    return item;
  });
}

function restoreLastLabelSettings() {
  const saved = readLastLabelSettings();
  if (saved.sourceType === "expert" || saved.sourceType === "operator") $("#labelSourceType").value = saved.sourceType;
  if (typeof saved.sourceIdentity === "string") $("#labelSourceIdentity").value = saved.sourceIdentity;
  if (saved.fileType === "wav" || saved.fileType === "tdms") $("#labelFileType").value = saved.fileType;
  if (saved.wavProfile === "motor" || saved.wavProfile === "rail") $("#labelWavProfileSelect").value = saved.wavProfile;
  const sessionJsonPath = saved.historySelected ? (saved.historyPath || saved.sessionJsonPath) : "";
  if (sessionJsonPath) $("#labelSelectedSessionPath").value = sessionJsonPath;
  if (sessionJsonPath) $("#labelSessionSelectionStatus").textContent = `上次使用：${sessionJsonPath}`;
  if (saved.taxonomyPath) {
    $("#labelSelectedJsonPath").value = saved.taxonomyPath;
    $("#labelJsonSelectionStatus").textContent = "上次选择的标签类别 JSON";
  }
  state.labelingRestoredLine = saved.line || "";
  if (sessionJsonPath) {
    state.labelingHistoryPath = sessionJsonPath;
    state.labelingHistorySelected = true;
  }
  if (Array.isArray(saved.sourceSelection?.paths) && saved.sourceSelection.paths.length) {
    const selection = saved.sourceSelection;
    const paths = selection.paths.filter(path => typeof path === "string" && path);
    if (paths.length) {
      state.labelProjectId = saved.projectId || "";
      if (saved.projectId) state.labelProjectQueues.set(saved.projectId, { rootPath: selection.rootPath, paths, fileFormat: saved.fileType, fileType: saved.wavProfile || "generic" });
      state.labelingSourceSelection = { ...selection, paths };
      state.labelingOutputDirectory = selection.rootPath || paths[0].split("/").slice(0, -1).join("/");
      $("#labelSelectedSessionPath").value = state.labelingHistoryPath;
      $("#labelSessionSelectionStatus").textContent = state.labelingHistoryPath ? `上次使用：${state.labelingHistoryPath}` : "不选择则自动生成标注 JSON";
      state.labelingQueue = groupLabelSources(paths, $("#labelWavProfileSelect").value, $("#labelFileType").value === "wav");
      state.labelingAllQueue = state.labelingQueue;
      renderLabelQueueFolderOptions();
      state.labelingQueueIndex = Math.max(0, Math.min(state.labelingQueue.length - 1, Number(saved.queueIndex) || 0));
      $("#labelSelectedSourcePath").value = selection.displayPath || state.labelingOutputDirectory;
      $("#labelTdmsPath").value = state.labelingQueue[state.labelingQueueIndex]?.path || "";
      state.labelingRestoredQueuePending = true;
      renderLabelQueue();
    }
  }
  updateLabelSourcePreview();
  for (const id of ["labelSourceType", "labelFileType", "labelWavProfileSelect"]) $(`#${id}`)._syncTouchPicker?.();
  return saved;
}

async function loadRestoredLabelQueue() {
  if (!state.labelingRestoredQueuePending || !state.labelingQueue.length) return;
  syncLabelLineField();
  if (state.labelingRestoredLine && Array.from($("#labelLineSelect").options).some(option => option.value === state.labelingRestoredLine)) {
    $("#labelLineSelect").value = state.labelingRestoredLine;
    $("#labelLineSelect")._syncTouchPicker?.();
  }
  state.labelingRestoredQueuePending = false;
  if (state.labelTaxonomy.path) await loadLabelQueueIndex(state.labelingQueueIndex);
}

function applyImportLine() {
  const line = $("#importLineSelect").value;
  const input = $('[data-condition="line"]', $("#importConditions"));
  if (input) {
    input.value = line;
    input.readOnly = true;
    input.title = "产线由步骤 1 自动填充";
  }
  const placementSelect = $("#importPlacementMode");
  placementSelect.querySelector('option[value="register"]').textContent = line
    ? `不拷贝，文件已经在“${line}”产线文件夹内`
    : "不拷贝，文件已经在所选产线文件夹内";
  placementSelect.querySelector('option[value="copy"]').textContent = line
    ? `拷贝到“${line}”产线文件夹内`
    : "拷贝到所选产线文件夹内";
  placementSelect.querySelector('option[value="move"]').textContent = line
    ? `移动到“${line}”产线文件夹内`
    : "移动到所选产线文件夹内";
  updateImportSteps();
}

async function createImportLine(button) {
  const database = selectedDatabase();
  const name = $("#newLineName").value.trim();
  if (!database?.uid) return notify("请先打开数据库", "error");
  if (!name) return notify("请输入产线名称", "error");
  setBusy(button, true, "正在创建…");
  try {
    const result = await request(API.databaseLines(database.uid), { method: "POST", body: { name } });
    database.lines = asArray(result, ["lines"]);
    if (state.currentDatabase?.uid === database.uid) state.currentDatabase.lines = [...database.lines];
    populateImportLines(result.line || name);
    $("#newLineName").value = "";
    $("#createLineFields").hidden = true;
    applyImportLine();
    notify(`产线“${result.line || name}”已创建`);
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

async function autoDetectImportLines(button) {
  const database = selectedDatabase();
  if (!database?.uid) return notify("请先打开数据库", "error");
  setBusy(button, true, "识别中…");
  try {
    const selected = $("#importLineSelect").value;
    const result = await request(API.databaseLinesAutoDetect(database.uid), { method: "POST", body: {} });
    database.lines = asArray(result, ["lines"]);
    if (state.currentDatabase?.uid === database.uid) state.currentDatabase.lines = [...database.lines];
    const added = asArray(result, ["added"]);
    populateImportLines(selected || added[0] || "");
    applyImportLine();
    notify(added.length ? `已识别并注册 ${added.length} 条产线` : "未发现需要新增的产线");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

function updateLabelSourcePreview() {
  const type = $("#labelSourceType").value;
  const identity = $("#labelSourceIdentity").value.trim();
  $("#labelSourcePreview").textContent = identity ? `${type}_${identity}` : type;
}

function currentLabelSource() {
  const type = $("#labelSourceType").value;
  const identity = $("#labelSourceIdentity").value.trim();
  return identity ? `${type}_${identity}` : type;
}

async function startLabelingSession() {
  if (!state.labelTaxonomy.path) throw new Error("请先选择标签类别 JSON");
  const queuePaths = state.labelingQueue.flatMap(item => item.paths || [item.path]).filter(Boolean);
  if (!queuePaths.length) throw new Error("请先选择测量文件");
  const outputDirectory = state.labelingOutputDirectory || null;
  const historyPath = state.labelingHistorySelected ? state.labelingHistoryPath : null;
  const source = currentLabelSource();
  const key = JSON.stringify({ paths: queuePaths, outputDirectory, historyPath });
  if (state.labelingSessionRequest?.key === key) return state.labelingSessionRequest.promise;
  const sessionRequest = { key, promise: null };
  sessionRequest.promise = request(API.labelingSession, {
    method: "POST",
    body: { paths: queuePaths, output_directory: outputDirectory, history_path: historyPath, source },
  }).then(payload => {
    if (state.labelingSessionRequest === sessionRequest) {
      state.labelingSessionPath = payload.session_path;
      $("#labelSelectedSessionPath").value = payload.session_path;
      $("#labelSessionJsonPath").textContent = payload.session_path;
      $("#labelSessionSelectionStatus").textContent = `当前标注 JSON：${payload.session_path}`;
      saveLastLabelSettings();
    }
    return payload.session_path;
  }).finally(() => {
    if (state.labelingSessionRequest === sessionRequest) state.labelingSessionRequest = null;
  });
  state.labelingSessionRequest = sessionRequest;
  return sessionRequest.promise;
}

function renderLabelTaxonomyEditor() {
  const taxonomy = state.labelTaxonomy;
  $("#labelTaxonomyPath").textContent = taxonomy.path || "请先选择项目根目录";
  $("#saveLabelTaxonomyButton").disabled = !taxonomy.path;
  if (!taxonomy.path) return;
  const resultOptions = taxonomy.results.map(item => `<option value="${escapeHtml(item.result_key)}">${escapeHtml(item.result_name || item.result_key)}</option>`).join("");
  $("#labelTaxonomyEditor").innerHTML = `
    <div class="label-taxonomy-section"><h4>result 标注结果</h4>${taxonomy.results.map((item, index) => `<div class="label-taxonomy-row" data-taxonomy-kind="results" data-taxonomy-index="${index}"><input data-taxonomy-field="result_key" value="${escapeHtml(item.result_key)}" placeholder="result_key"><input type="number" data-taxonomy-field="result_id" value="${escapeHtml(item.result_id)}" placeholder="ID"><input data-taxonomy-field="result_name" value="${escapeHtml(item.result_name)}" placeholder="显示名称"><button class="button button-ghost" data-delete-taxonomy type="button">删除</button></div>`).join("")}</div>
    <div class="label-taxonomy-section"><h4>reason 细分类别</h4>${taxonomy.reasons.map((item, index) => `<div class="label-taxonomy-row label-taxonomy-reason" data-taxonomy-kind="reasons" data-taxonomy-index="${index}"><input data-taxonomy-field="reason_key" value="${escapeHtml(item.reason_key)}" placeholder="reason_key"><input type="number" data-taxonomy-field="reason_id" value="${escapeHtml(item.reason_id)}" placeholder="ID"><input data-taxonomy-field="reason_name" value="${escapeHtml(item.reason_name)}" placeholder="显示名称"><select data-taxonomy-field="result_key">${resultOptions}</select><button class="button button-ghost" data-delete-taxonomy type="button">删除</button></div>`).join("")}</div>`;
  $$("[data-taxonomy-kind='reasons']", $("#labelTaxonomyEditor")).forEach((row, index) => {
    $("[data-taxonomy-field='result_key']", row).value = taxonomy.reasons[index].result_key;
  });
}

function compareReasonIds(left, right) {
  const leftId = Number(left.id);
  const rightId = Number(right.id);
  return (Number.isFinite(leftId) ? leftId : Infinity) - (Number.isFinite(rightId) ? rightId : Infinity)
    || left.key.localeCompare(right.key);
}

async function loadLabelTaxonomy(path, { selectedPath = path } = {}) {
  const payload = await request(API.labelingTaxonomy(path));
  state.labelTaxonomy = { path: payload.path, sourcePath: selectedPath, results: payload.results || [], reasons: (payload.reasons || []).sort((a, b) => compareReasonIds({ id: a.reason_id, key: a.reason_key }, { id: b.reason_id, key: b.reason_key })) };
  renderLabelTaxonomyEditor();
  renderLabelQueueFilterOptions();
}

function renderLabelQueueFilterOptions() {
  const result = $("#labelQueueResultFilter");
  const reason = $("#labelQueueReasonFilter");
  const selectedResult = result.value;
  const selectedReason = reason.value;
  result.innerHTML = '<option value="">全部结果</option><option value="__unlabeled__">未标注</option>' + state.labelTaxonomy.results.map(item => `<option value="${escapeHtml(item.result_key)}">${escapeHtml(item.result_name || item.result_key)}</option>`).join("");
  reason.innerHTML = '<option value="">全部原因</option>' + state.labelTaxonomy.reasons.map(item => `<option value="${escapeHtml(item.reason_key)}">${escapeHtml(item.reason_name || item.reason_key)}</option>`).join("");
  if ([...result.options].some(option => option.value === selectedResult)) result.value = selectedResult;
  if ([...reason.options].some(option => option.value === selectedReason)) reason.value = selectedReason;
  result._syncTouchPicker?.();
  reason._syncTouchPicker?.();
}

function labelQueueRoot() {
  const projectRoot = state.labelProjects.find(item => item.project_id === state.labelProjectId)?.root_path;
  return (projectRoot || state.labelingSourceSelection?.rootPath || state.labelingOutputDirectory || "").replaceAll("\\", "/").replace(/\/$/, "");
}

function labelQueueFolder(path, root) {
  const normalized = path.replaceAll("\\", "/");
  const relative = root && normalized.toLocaleLowerCase().startsWith(`${root.toLocaleLowerCase()}/`)
    ? normalized.slice(root.length + 1) : normalized.split("/").pop();
  return relative.includes("/") ? relative.slice(0, relative.lastIndexOf("/")) : "__root__";
}

function renderLabelQueueFolderOptions() {
  const select = $("#labelQueueFolderFilter");
  const selected = select.value;
  const root = labelQueueRoot();
  const folders = [...new Set(state.labelingAllQueue.flatMap(item => (item.paths || [item.path]).flatMap(path => {
    const directory = labelQueueFolder(path, root);
    if (directory === "__root__") return [directory];
    const parts = directory.split("/");
    return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
  })))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  select.innerHTML = '<option value="">全部子文件夹</option>' + folders.map(folder => `<option value="${escapeHtml(folder)}">${escapeHtml(folder === "__root__" ? "根目录文件" : folder)}</option>`).join("");
  if (folders.includes(selected)) select.value = selected;
  select._syncTouchPicker?.();
}

async function refreshLabelQueueLabels() {
  const path = state.labelingSessionPath || state.labelingHistoryPath;
  if (!path) { state.labelingQueueLabels = new Map(); return; }
  const payload = await request(API.labelingQueueLabels(path));
  const queuePaths = state.labelingAllQueue.flatMap(item => item.paths || [item.path]);
  const normalize = value => String(value || "").replaceAll("\\", "/").toLocaleLowerCase();
  const byName = new Map();
  queuePaths.forEach(source => {
    const name = normalize(source).split("/").pop();
    byName.set(name, [...(byName.get(name) || []), source]);
  });
  const saved = new Map();
  (payload.files || []).forEach(file => {
    const exact = queuePaths.find(source => normalize(source) === normalize(file.path));
    const matches = exact ? [exact] : byName.get(normalize(file.path).split("/").pop()) || [];
    if (matches.length === 1) saved.set(matches[0], file.events || []);
  });
  state.labelingQueueLabels = saved;
}

async function applyLabelQueueFilter() {
  const folder = $("#labelQueueFolderFilter").value;
  const root = labelQueueRoot();
  const search = $("#labelQueueSearchInput").value.trim().toLocaleLowerCase();
  const resultKey = $("#labelQueueResultFilter").value;
  const reasonKey = $("#labelQueueReasonFilter").value;
  const all = state.labelingAllQueue;
  const resultName = state.labelTaxonomy.results.find(item => item.result_key === resultKey)?.result_name;
  const reasonName = state.labelTaxonomy.reasons.find(item => item.reason_key === reasonKey)?.reason_name;
  const filtered = all.filter(item => {
    if (search && !(item.paths || [item.path]).some(path => path.split(/[\\/]/).pop().toLocaleLowerCase().includes(search))) return false;
    if (folder && !(item.paths || [item.path]).some(path => {
      const directory = labelQueueFolder(path, root);
      return directory === folder || (folder !== "__root__" && directory.startsWith(`${folder}/`));
    })) return false;
    if (!resultKey && !reasonKey) return true;
    const events = (item.paths || [item.path]).flatMap(path => state.labelingQueueLabels.get(path) || []);
    if (resultKey === "__unlabeled__") return !events.length;
    return events.some(event => (!resultKey || event.result_key === resultKey || (!event.result_key && event.result_name === resultName))
      && (!reasonKey || event.reason_key === reasonKey || (!event.reason_key && event.reason_name === reasonName)));
  });
  const previous = state.labelingQueue[state.labelingQueueIndex];
  if (previous && !filtered.includes(previous) && !discardPendingLabelEventsForFileChange()) return;
  state.labelingQueue = filtered;
  state.labelingQueueIndex = filtered.length ? Math.max(0, filtered.indexOf(previous)) : -1;
  $("#labelQueueFilterStatus").textContent = folder || search || resultKey || reasonKey ? `筛选结果 ${filtered.length}/${all.length} 件` : `显示全部 ${all.length} 件`;
  renderLabelQueue();
  if (filtered.length && !labelQueueItemReady(filtered[state.labelingQueueIndex])) await loadLabelQueueIndex(state.labelingQueueIndex);
}

function applySavedLabelTaxonomy(payload) {
  const taxonomy = {
    results: Object.fromEntries(payload.results.map(item => [item.result_key, { id: item.result_id, name: item.result_name }])),
    reasons: Object.fromEntries(payload.reasons.map(item => [item.reason_key, { id: item.reason_id, name: item.reason_name, parent: item.result_key }])),
  };
  for (const file of new Set([...state.labelingFiles, state.labelingFile].filter(Boolean))) {
    file.taxonomy = taxonomy;
    file.taxonomy_path = payload.path;
  }
  $$('[data-label-sample]', $("#labelChannelsRow")).forEach(card => {
    const result = $("[data-label-result]", card);
    const reason = $("[data-label-reason]", card);
    if (!result || !reason) return;
    const previousResult = result.value;
    const previousReason = reason.value;
    result.innerHTML = labelResultOptions();
    if (Array.from(result.options).some(option => option.value === previousResult)) result.value = previousResult;
    reason.innerHTML = labelReasonOptions(result.value);
    if (Array.from(reason.options).some(option => option.value === previousReason)) reason.value = previousReason;
    result._syncTouchPicker?.();
    reason._syncTouchPicker?.();
    if (card._labelEventEditor?.activeKey) captureActiveLabelEventForm(card);
  });
}

function syncLabelTaxonomyEditor() {
  $$("[data-taxonomy-kind]", $("#labelTaxonomyEditor")).forEach(row => {
    const list = state.labelTaxonomy[row.dataset.taxonomyKind];
    const item = list?.[Number(row.dataset.taxonomyIndex)];
    if (!item) return;
    $$('[data-taxonomy-field]', row).forEach(input => {
      item[input.dataset.taxonomyField] = input.type === "number" ? Number(input.value) : input.value;
    });
  });
}

async function saveLabelTaxonomy(button) {
  if (state.labelingPickerPending || state.labelingQueueLoading || state.labelingConfigLoading) return notify("当前选择、文件或标签配置操作尚未完成，请稍候", "error");
  if ($$('[data-label-sample]', $("#labelChannelsRow")).some(card => card.dataset.eventSavePending === "true")) return notify("当前事件正在保存，请稍候", "error");
  syncLabelTaxonomyEditor();
  setBusy(button, true, "保存中…");
  setLabelConfigLoading(true);
  try {
    const payload = await request("/api/labeling/taxonomy", { method: "PUT", body: { path: state.labelTaxonomy.sourcePath || $("#labelSelectedSourcePath").value.trim(), results: state.labelTaxonomy.results, reasons: state.labelTaxonomy.reasons } });
    state.labelTaxonomy = { path: payload.path, sourcePath: state.labelTaxonomy.sourcePath, results: payload.results, reasons: payload.reasons };
    renderLabelTaxonomyEditor();
    applySavedLabelTaxonomy(payload);
    notify("标签类别已保存并立即应用");
  } catch (error) { notify(error.message, "error", true); }
  finally {
    setBusy(button, false);
    setLabelConfigLoading(false);
  }
}

function pendingLabelEventCount() {
  return $$('[data-label-sample]', $("#labelChannelsRow")).reduce((count, card) => {
    const items = card._labelEventEditor?.items || [];
    return count + items.filter(item => item.status === "draft" || item.dirty).length;
  }, 0);
}

function guardPendingLabelEvents() {
  const saving = $$('[data-label-sample]', $("#labelChannelsRow"))
    .some(card => card.dataset.eventSavePending === "true");
  if (saving) {
    notify("当前事件正在保存，请稍候", "error", true);
    return true;
  }
  $$('[data-label-sample]', $("#labelChannelsRow")).forEach(card => {
    if (card._labelEventEditor?.activeKey) captureActiveLabelEventForm(card);
  });
  const count = pendingLabelEventCount();
  if (!count) return false;
  notify(`还有 ${count} 个事件未保存，请先保存或移除后再切换文件`, "error", true);
  return true;
}

function discardPendingLabelEventsForFileChange() {
  const cards = $$('[data-label-sample]', $("#labelChannelsRow"));
  if (cards.some(card => card.dataset.eventSavePending === "true")) {
    notify("当前事件正在保存，请稍候", "error", true);
    return false;
  }
  cards.forEach(card => {
    const editor = card._labelEventEditor;
    if (!editor) return;
    editor.items = editor.items.filter(item => item.status === "saved");
    editor.items.forEach(item => {
      if (!item.dirty) return;
      item.start = item.committedStart;
      item.end = item.committedEnd;
      item.form = { ...item.committedForm };
      item.dirty = false;
    });
    editor.activeKey = "";
    $("[data-event-audio]", card)?.pause();
    $("[data-signal-plot]", card)?._clearActiveEventSelection?.();
    $(".label-channel-form", card).hidden = true;
  });
  return true;
}

function setLabelWorkspaceLoading() {
  const workspace = $("#singleLabelWorkspace");
  if (!workspace) return;
  const busy = state.labelingQueueLoading || state.labelingConfigLoading || state.labelingPickerPending;
  workspace.inert = Boolean(busy);
  workspace.setAttribute("aria-busy", busy ? "true" : "false");
  workspace.classList.toggle("is-loading-file", Boolean(busy));
}

function setLabelConfigLoading(loading) {
  state.labelingConfigLoading = Boolean(loading);
  setLabelWorkspaceLoading();
  const editor = $("#labelTaxonomyEditor");
  if (editor) editor.inert = Boolean(loading);
  ["#addLabelResultButton", "#addLabelReasonButton", "#saveLabelTaxonomyButton"].forEach(selector => {
    const control = $(selector);
    if (control) control.disabled = loading || (selector === "#saveLabelTaxonomyButton" && !state.labelTaxonomy.path);
  });
}

function labelQueueItemReady(item) {
  const paths = item?.paths || (item?.path ? [item.path] : []);
  return Boolean(item?.loaded && paths.length && paths.every(path => state.labelingFiles.some(file => file.absolute_path === path)));
}

function renderLabelQueue() {
  const total = state.labelingQueue.length;
  const unit = $("#labelFileType").value === "wav" ? "件" : "个文件";
  const current = state.labelingQueueIndex;
  const currentItem = state.labelingQueue[current];
  const loading = state.labelingQueueLoading;
  const labeled = state.labelingQueue.filter(item => item.labeled).length;
  $("#labelQueuePosition").textContent = `${unit === "件" ? "件" : "文件"} ${current >= 0 ? current + 1 : 0}/${total}`;
  $("#annotationJumpIndex").value = current >= 0 ? current + 1 : "";
  $("#annotationJumpIndex").max = Math.max(1, total);
  $("#annotationJumpIndex").disabled = loading || !total;
  $("#annotationJumpButton").disabled = loading || !total;
  $("#annotationQueueTotal").textContent = total;
  $("#labelQueueProgressText").textContent = `标注进度 ${labeled}/${total}（${total ? Math.round(labeled * 100 / total) : 0}%）`;
  $("#labelQueueProgressBar").style.width = `${total ? labeled * 100 / total : 0}%`;
  $("#labelFileListCount").textContent = `${total} ${unit}`;
  $("#previousLabelFileButton").disabled = loading || current <= 0;
  $("#nextLabelFileButton").disabled = loading || current < 0 || current >= total - 1;
  $("#annotationPreviousButton").disabled = loading || current <= 0;
  $("#annotationNextButton").disabled = loading || current < 0 || current >= total - 1;
  $("#gotoLabelFileButton").disabled = loading || !total;
  $("#labelGotoIndex").disabled = loading || !total;
  $("#labelGotoIndex").max = Math.max(1, total);
  $("#labelGotoIndex").value = current >= 0 ? current + 1 : 1;
  const settingsStartButton = $("#startLabelingFromSettingsButton");
  const listStartButton = $("#startLabelingFromListButton");
  const settingsStatus = $("#labelSettingsSelectionStatus");
  const ready = labelQueueItemReady(currentItem) && Boolean(state.labelTaxonomy.path);
  if (settingsStartButton) {
    settingsStartButton.disabled = !state.labelingAllQueue.length || !state.labelTaxonomy.path || loading;
    settingsStartButton.textContent = "下一步 →";
  }
  if (listStartButton) listStartButton.disabled = !ready || loading;
  if (settingsStatus) settingsStatus.textContent = state.labelingAllQueue.length
    ? `${state.labelingAllQueue.length} ${unit} · 下一步查看文件列表`
    : "尚未选择文件";
  $("#labelFileQueueList").innerHTML = total ? state.labelingQueue.map((item, index) => {
    const samples = (item.annotations || item.samples || []).map(sample => {
      if (sample.missing) return `<span class="label-queue-sample"><b>${escapeHtml(sample.display_name || sample.sample_id)}</b><i>通道缺失</i></span>`;
      const latest = (sample.label_events || []).at(-1) || {};
      const scope = sample.sample_scope || {};
      return `<span class="label-queue-sample"><b>sample_id：${escapeHtml(sample.sample_id)}</b><em>sample_scope：${escapeHtml(scope.start_s ?? 0)}–${escapeHtml(scope.end_s ?? 0)} s</em><i>result：${escapeHtml(latest.result_name || latest.result_key || "未标注")}</i><i>reason：${escapeHtml(latest.reason_name || latest.reason_key || "—")}</i></span>`;
    }).join("");
    const status = item.loading ? "载入中…" : item.loadError ? "载入失败" : item.labeled ? "已标注" : item.partialLabeled ? "部分标注" : item.loaded ? "可开始" : "未载入";
    const files = item.paths || [item.path];
    return `<button class="label-file-queue-item ${index === current ? "is-active" : ""}" data-label-queue-index="${index}" type="button" title="单击选择，双击开始标注" ${loading ? "disabled" : ""}><span>${index + 1}</span><strong>${escapeHtml(item.displayName || item.path.split(/[\\/]/).pop())}</strong><small>${escapeHtml(files.map(path => path.split(/[\\/]/).pop()).join(" · "))}</small><i>${status}</i><span class="label-queue-samples">${samples}</span></button>`;
  }).join("") : `<div class="empty-state">${state.labelingAllQueue.length ? "没有符合筛选条件的文件，请调整子文件夹、result 或 reason" : "请在设置页选择文件或文件夹"}</div>`;
}

function resizeLabelSignalPlots() {
  if (!window.Plotly?.Plots) return;
  $$('[data-signal-plot]', $("#labelTabAnnotation")).forEach(plot => {
    if (!plot._fullLayout) return;
    Promise.resolve(plot._syncCompactLayout?.())
      .then(() => window.Plotly.Plots.resize(plot))
      .finally(() => plot._syncAudioTrackGeometry?.());
  });
}

function scheduleLabelSignalResize() {
  requestAnimationFrame(() => requestAnimationFrame(resizeLabelSignalPlots));
  window.setTimeout(resizeLabelSignalPlots, 160);
}

function switchLabelWorkflowTab(name) {
  if ((name === "files" || name === "annotation") && !state.labelProjectId) return notify("请先选择并保存项目", "error");
  if (name === "annotation" && state.labelingQueueIndex >= 0) {
    const current = state.labelingQueue[state.labelingQueueIndex];
    const ready = labelQueueItemReady(current);
    if (!ready) return notify("请等待当前文件载入完成，再开始标注", "error");
    if (!state.labelingSessionPath) return notify("请先创建标注会话", "error");
  }
  const pages = { projects: "#labelTabProjects", settings: "#labelTabSettings", files: "#labelTabFiles", annotation: "#labelTabAnnotation" };
  Object.entries(pages).forEach(([key, selector]) => { $(selector).hidden = key !== name; });
  if (name === "files") renderLabelQueueFolderOptions();
  $$('[data-label-tab]').forEach(button => button.classList.toggle("is-active", button.dataset.labelTab === name));
  $("#pageTitle").textContent = name === "projects" ? "项目列表" : name === "settings" ? $("#labelProjectSettingsTitle").textContent : name === "files" ? $("#labelProjectFilesTitle").textContent : "标注界面";
  if (name === "annotation") {
    scheduleLabelSignalResize();
    void loadCurrentLabelWaveforms();
  } else {
    ++labelWaveformLoadToken;
    labelWaveformLoadTask = null;
    releaseInactiveLabelWaveforms();
  }
}

async function enterLabelAnnotation(button = null) {
  const current = state.labelingQueue[state.labelingQueueIndex];
  if (!labelQueueItemReady(current)) return notify("当前文件尚未载入完成", "error");
  if (button) setBusy(button, true, "创建中…");
  try {
    await startLabelingSession();
    switchLabelWorkflowTab("annotation");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    if (button) setBusy(button, false);
  }
}

function enableLabelTaskTabs() {
  // 三个页签始终可访问；未创建任务时显示空状态。
}

function renderLabelProjects() {
  const host = $("#labelProjectsList");
  host.innerHTML = state.labelProjects.length ? state.labelProjects.map(project =>
    `<div class="label-project-card"><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.root_path)}</small><small>${escapeHtml(project.file_format.toUpperCase())} · ${escapeHtml(project.file_type)}</small><div class="label-project-actions"><button class="button button-primary" data-open-label-project="${escapeHtml(project.project_id)}" data-project-destination="files" type="button">进入标注</button><button class="button button-secondary" data-open-label-project="${escapeHtml(project.project_id)}" data-project-destination="settings" type="button">项目设置</button><button class="button button-ghost" data-delete-label-project="${escapeHtml(project.project_id)}" type="button">删除项目</button></div></div>`
  ).join("") : '<div class="empty-state">暂无标注项目，点击“新建项目”选择数据根文件夹。</div>';
}

async function loadLabelProjects() {
  const payload = await request(API.labelingProjects);
  state.labelProjects = payload.projects || [];
  renderLabelProjects();
}

async function deleteLabelProject(project, button) {
  if (!window.confirm(`删除项目“${project.name}”？只删除项目列表记录，原始文件和标注 JSON 会保留。`)) return;
  setBusy(button, true, "删除中…");
  try {
    await request(API.labelingProjectDelete(project.project_id), { method: "DELETE" });
    state.labelProjectQueues.delete(project.project_id);
    if (state.labelProjectId === project.project_id) { clearLabelProjectQueue(); state.labelProjectId = ""; }
    await loadLabelProjects();
    notify("项目记录已删除，文件和标注 JSON 已保留");
  } catch (error) { notify(error.message, "error", true); }
  finally { setBusy(button, false); }
}

function clearLabelProjectQueue() {
  ++state.labelingResolveGeneration;
  releaseLabelWorkspace();
  state.labelingFile = null;
  state.labelingFiles = [];
  state.labelingQueue = [];
  state.labelingAllQueue = [];
  state.labelingQueueIndex = -1;
  state.labelingQueueLabels = new Map();
  state.labelingSourceSelection = null;
  state.labelingSessionPath = "";
  $("#labelQueueSearchInput").value = "";
  $("#labelQueueFolderFilter").value = "";
  renderLabelQueueFolderOptions();
  renderLabelQueue();
}

async function openLabelProject(project = null, destination = "settings") {
  if (guardPendingLabelEvents()) return;
  const previousProject = state.labelProjects.find(item => item.project_id === state.labelProjectId);
  if (previousProject && state.labelingSourceSelection?.paths) {
    state.labelProjectQueues.set(previousProject.project_id, {
      rootPath: previousProject.root_path, fileFormat: previousProject.file_format,
      fileType: previousProject.file_type, paths: state.labelingSourceSelection.paths,
    });
  }
  clearLabelProjectQueue();
  state.labelProjectId = project?.project_id || "";
  $("#labelProjectName").value = project?.name || "";
  $("#labelSelectedSourcePath").value = project?.root_path || "";
  $("#labelFileType").value = project?.file_format || "wav";
  $("#labelProjectType").value = project?.file_type || "rail";
  $("#labelWavProfileSelect").value = project?.file_type === "motor" ? "motor" : project?.file_type === "rail" ? "rail" : "";
  for (const id of ["labelFileType", "labelProjectType", "labelWavProfileSelect"]) $(`#${id}`)._syncTouchPicker?.();
  syncLabelLineField();
  state.labelingHistoryPath = project?.history_path || "";
  state.labelingHistorySelected = Boolean(project?.history_path);
  $("#labelSelectedSessionPath").value = state.labelingHistoryPath;
  $("#labelSessionSelectionStatus").textContent = state.labelingHistoryPath ? `当前项目：${state.labelingHistoryPath}` : "不选择则自动生成标注 JSON";
  state.labelTaxonomy = { path: "", sourcePath: "", results: [], reasons: [] };
  $("#labelSelectedJsonPath").value = project?.taxonomy_path || "";
  $("#labelTaxonomyEditor").innerHTML = '<div class="empty-state">正在加载标签类别…</div>';
  renderLabelTaxonomyEditor();
  await loadLabelTaxonomy(project?.taxonomy_path || project?.root_path || "", { selectedPath: project?.taxonomy_path || "" });
  $("#labelSelectedJsonPath").value = project?.taxonomy_path || "";
  $("#labelJsonSelectionStatus").textContent = project?.taxonomy_path ? "已加载所选标签类别 JSON" : "默认：正常、异常、边界，可直接编辑";
  $("#labelProjectSettingsTitle").textContent = `项目设置 · ${project?.name || "新项目"}`;
  $("#labelProjectFilesTitle").textContent = `标注列表 · ${project?.name || "新项目"}`;
  switchLabelWorkflowTab(project ? destination : "settings");
  if (project && destination === "files") await refreshLabelProjectFiles(null, { force: false, quiet: true });
}

async function selectLabelProjectRoot(button) {
  setBusy(button, true, "选择中…");
  try {
    const selected = await request(API.labelingSelectProjectRoot, { method: "POST" });
    if (selected.path) {
      $("#labelSelectedSourcePath").value = selected.path;
      if (!state.labelTaxonomy.sourcePath) await loadLabelTaxonomy(selected.path, { selectedPath: "" });
    }
  } catch (error) { notify(error.message, "error", true); }
  finally { setBusy(button, false); }
}

async function saveLabelProject(button, { advance = false } = {}) {
  const name = $("#labelProjectName").value.trim();
  const rootPath = $("#labelSelectedSourcePath").value.trim();
  const fileFormat = $("#labelFileType").value;
  const fileType = $("#labelProjectType").value;
  if (!name || !rootPath) return notify("请填写项目名称并选择根文件夹", "error");
  if (fileFormat === "wav" && fileType === "generic") return notify("WAV 项目请选择电机或滑轨类型", "error");
  setBusy(button, true, "保存中…");
  try {
    const previous = state.labelProjects.find(item => item.project_id === state.labelProjectId);
    const project = await request(API.labelingProjects, { method: "POST", body: {
      project_id: state.labelProjectId || null, name, root_path: rootPath,
      file_format: fileFormat, file_type: fileType,
      taxonomy_path: state.labelTaxonomy.sourcePath || null,
      history_path: state.labelingHistorySelected ? state.labelingHistoryPath : null,
    } });
    if (previous && (previous.root_path !== project.root_path || previous.file_format !== project.file_format || previous.file_type !== project.file_type)) clearLabelProjectQueue();
    state.labelProjectId = project.project_id;
    const activeTaxonomyRoot = state.labelTaxonomy.path.split("/").slice(0, -1).join("/");
    if (!state.labelTaxonomy.sourcePath && activeTaxonomyRoot !== project.root_path) {
      await loadLabelTaxonomy(project.root_path, { selectedPath: "" });
    }
    await loadLabelProjects();
    $("#labelProjectSettingsTitle").textContent = `项目设置 · ${project.name}`;
    $("#labelProjectFilesTitle").textContent = `标注列表 · ${project.name}`;
    if (advance) {
      switchLabelWorkflowTab("files");
      await refreshLabelProjectFiles(null, { force: false, quiet: true });
      notify("项目已保存，文件列表已加载");
    } else notify("项目已保存");
  } catch (error) { notify(error.message, "error", true); }
  finally { setBusy(button, false); }
}

async function refreshLabelProjectFiles(button, { force = true, quiet = false } = {}) {
  if (!state.labelProjectId) return notify("请先选择并保存项目", "error");
  if (guardPendingLabelEvents()) return;
  setBusy(button, true, "扫描中…");
  try {
    const project = state.labelProjects.find(item => item.project_id === state.labelProjectId);
    if (!project) throw new Error("标注项目不存在");
    const cached = state.labelProjectQueues.get(project.project_id);
    const validCache = cached && cached.rootPath === project.root_path
      && cached.fileFormat === project.file_format && cached.fileType === project.file_type;
    const payload = !force && validCache ? cached : await request(API.labelingScanProject(state.labelProjectId), { method: "POST" });
    if (force || !validCache) state.labelProjectQueues.set(project.project_id, {
      rootPath: payload.root_path, fileFormat: project.file_format, fileType: project.file_type, paths: payload.paths || [],
    });
    clearLabelProjectQueue();
    const paths = payload.paths || [];
    const profile = project?.file_type === "generic" ? "" : project?.file_type || "";
    state.labelingOutputDirectory = payload.root_path;
    state.labelingQueue = groupLabelSources(paths, profile, project?.file_format === "wav");
    state.labelingAllQueue = state.labelingQueue;
    renderLabelQueueFolderOptions();
    state.labelingQueueIndex = paths.length ? 0 : -1;
    syncLabelLineField();
    state.labelingSourceSelection = { mode: "folder", rootPath: payload.root_path, displayPath: payload.root_path, paths };
    $("#labelTdmsPath").value = state.labelingQueue[0]?.path || "";
    saveLastLabelSettings();
    if (state.labelingHistoryPath) {
      await refreshLabelQueueLabels();
      state.labelingAllQueue.forEach(item => {
        const labeled = (item.paths || [item.path]).filter(path => (state.labelingQueueLabels.get(path) || []).length > 0).length;
        item.labeled = labeled === (item.paths || [item.path]).length;
        item.partialLabeled = labeled > 0 && !item.labeled;
      });
    }
    $("#labelFileListPanel").hidden = false;
    renderLabelQueue();
    if (!quiet) notify(`${force || !validCache ? "扫描完成" : "已加载"}：${paths.length} 个文件`);
  } catch (error) { notify(error.message, "error", true); }
  finally { setBusy(button, false); }
}

async function loadLabelQueueIndex(index, { force = false } = {}) {
  if (index < 0 || index >= state.labelingQueue.length) return;
  if (state.labelingConfigLoading) return notify("标签配置正在载入，请稍候", "error");
  if (state.labelingPickerPending) return notify("请先完成当前文件选择", "error");
  if (state.labelingQueueLoading) return notify("当前文件正在载入，请稍候", "error");
  const current = state.labelingQueue[state.labelingQueueIndex];
  if (!force && index === state.labelingQueueIndex && labelQueueItemReady(current)) {
    renderLabelQueue();
    return;
  }
  if ((force || index !== state.labelingQueueIndex) && !discardPendingLabelEventsForFileChange()) return;
  state.labelingQueueIndex = index;
  $("#labelTdmsPath").value = state.labelingQueue[index].path;
  saveLastLabelSettings();
  renderLabelQueue();
  await resolveLabelTdms($("#loadLabelTdmsButton"), false);
}

async function selectLabelSources(mode, button) {
  if (state.labelingQueueLoading || state.labelingConfigLoading) return notify("当前文件或标签配置正在载入，请稍候", "error");
  if (state.labelingPickerPending) return notify("文件选择窗口已打开", "error");
  if ($$('[data-label-sample]', $("#labelChannelsRow")).some(card => card.dataset.eventSavePending === "true")) return notify("当前事件正在保存，请稍候", "error", true);
  const fileType = $("#labelFileType").value === "tdms" ? "tdms" : "wav";
  const wavProfile = selectedLabelWavProfile();
  state.labelingPickerPending = true;
  setLabelWorkspaceLoading();
  setBusy(button, true, mode === "folder" ? "扫描中…" : "选择中…");
  try {
    const result = await request(API.labelingSelectSources(mode, fileType), { method: "POST", body: {} });
    const paths = asArray(result, ["paths"]);
    if (result?.cancelled || !paths.length) return;
    if (state.labelingQueueLoading || state.labelingConfigLoading) return notify("文件或标签配置状态已变化，请重新选择", "error", true);
    if (!discardPendingLabelEventsForFileChange()) return;
    ++state.labelingResolveGeneration;
    releaseLabelWorkspace();
    state.labelingPickerPending = false;
    setLabelWorkspaceLoading();
    state.labelingSessionRequest = null;
    state.labelingSessionPath = "";
    state.labelingOutputDirectory = result.root_path || paths[0].split("/").slice(0, -1).join("/");
    $("#labelSelectedSessionPath").value = state.labelingHistoryPath;
    $("#labelSessionSelectionStatus").textContent = state.labelingHistoryPath ? `上次使用：${state.labelingHistoryPath}` : "不选择则自动生成标注 JSON";
    $("#labelSelectedSourcePath").value = mode === "folder" ? result.root_path : paths.length === 1 ? paths[0] : `${paths.length} 个文件（${result.root_path || "不同目录"}）`;
    $("#labelSessionJsonPath").textContent = state.labelingHistoryPath || "开始标注时自动生成";
    state.labelingQueue = groupLabelSources(paths, wavProfile, fileType === "wav");
    state.labelingAllQueue = state.labelingQueue;
    renderLabelQueueFolderOptions();
    state.labelingQueueLabels = new Map();
    $("#labelQueueResultFilter").value = "";
    $("#labelQueueReasonFilter").value = "";
    state.labelingQueueIndex = 0;
    state.labelingSourceSelection = { mode, rootPath: state.labelingOutputDirectory, displayPath: $("#labelSelectedSourcePath").value, paths };
    saveLastLabelSettings();
    syncLabelLineField();
    $("#labelFileListPanel").hidden = false;
    renderLabelQueue();
    if (fileType === "tdms") {
      $("#labelSettingsSelectionStatus").textContent = `${paths.length} 个文件 · 请选择产线`;
    } else if (!wavProfile) {
      $("#labelSettingsSelectionStatus").textContent = `${paths.length} 个文件 · 请选择电机或滑轨`;
    } else if (!state.labelTaxonomy.path) {
      $("#labelSettingsSelectionStatus").textContent = `${paths.length} 个文件 · 请先选择标签类别 JSON`;
    } else {
      await loadLabelQueueIndex(0);
    }
    enableLabelTaskTabs();
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setLabelConfigLoading(false);
    state.labelingPickerPending = false;
    setLabelWorkspaceLoading();
    setBusy(button, false);
  }
}

async function selectLabelJson(button, historyOnly = false) {
  if (state.labelingQueueLoading || state.labelingConfigLoading) return notify("当前文件或标签配置正在载入，请稍候", "error");
  if (state.labelingPickerPending) return notify("文件选择窗口已打开", "error");
  if (guardPendingLabelEvents()) return;
  state.labelingPickerPending = true;
  setLabelWorkspaceLoading();
  setBusy(button, true, "选择中…");
  try {
    const result = await request(API.labelingSelectJson, { method: "POST", body: {} });
    if (result.cancelled || !result.path) return;
    if (state.labelingQueueLoading || state.labelingConfigLoading) return notify("文件或标签配置状态已变化，请重新选择", "error", true);
    if (guardPendingLabelEvents()) return;
    state.labelingPickerPending = false;
    setLabelWorkspaceLoading();
    let kind = result.kind;
    if (!kind) {
      try {
        await request(API.labelingTaxonomy(result.path));
        kind = "taxonomy";
      } catch (error) {
        if (!/至少保留一个 result|reasons 必须|result 的|reason /.test(error.message)) throw error;
        kind = "history";
      }
    }
    if (historyOnly && kind !== "history") throw new Error("请选择已有标注 JSON，不要选择标签类别 JSON");
    if (!historyOnly && kind !== "taxonomy") throw new Error("请选择标签类别 JSON，不要选择标注 JSON");
    if (kind === "taxonomy") {
      setLabelConfigLoading(true);
      await loadLabelTaxonomy(result.path);
      setLabelConfigLoading(false);
      state.labelingSessionRequest = null;
      state.labelingSessionPath = "";
      $("#labelSessionJsonPath").textContent = state.labelingHistoryPath || "开始标注时自动生成";
      $("#labelSelectedJsonPath").value = result.path;
      $("#labelJsonSelectionStatus").textContent = "已加载标签类别 JSON";
      saveLastLabelSettings();
      if (state.labelingQueue.length) await loadLabelQueueIndex(state.labelingQueueIndex, { force: true });
      return;
    }
    if (kind !== "history") throw new Error("无法识别所选 JSON 类型");
    state.labelingHistoryPath = result.path;
    state.labelingHistorySelected = true;
    state.labelingSessionRequest = null;
    state.labelingSessionPath = "";
    $("#labelSelectedSessionPath").value = result.path;
    $("#labelSessionSelectionStatus").textContent = "已选择标注 JSON；保存时直接写入此文件";
    $("#labelSessionJsonPath").textContent = result.path;
    saveLastLabelSettings();
    state.labelingAllQueue.forEach(item => {
      item.labeled = false;
      item.partialLabeled = false;
      item.loaded = false;
      item.annotations = [];
      item.samples = [];
      delete item.loadedFilePath;
      delete item.loadError;
    });
    renderLabelQueue();
    await refreshLabelQueueLabels();
    if (state.labelTaxonomy.path && state.labelingQueue.length) await loadLabelQueueIndex(state.labelingQueueIndex, { force: true });
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setLabelConfigLoading(false);
    state.labelingPickerPending = false;
    setLabelWorkspaceLoading();
    setBusy(button, false);
  }
}

function labelReasonOptions(resultKey) {
  const reasons = state.labelingFile?.taxonomy?.reasons || {};
  return Object.entries(reasons)
    .filter(([, reason]) => reason.parent === resultKey)
    .sort(([leftKey, left], [rightKey, right]) => compareReasonIds({ id: left.id, key: leftKey }, { id: right.id, key: rightKey }))
    .map(([key, reason]) => `<option value="${escapeHtml(key)}">${escapeHtml(reason.name)}</option>`)
    .join("");
}

function labelResultOptions() {
  const results = state.labelingFile?.taxonomy?.results || {};
  const defaultKey = defaultLabelResultKey();
  return Object.entries(results).map(([key, result]) => `<option value="${escapeHtml(key)}"${key === defaultKey ? " selected" : ""}>${escapeHtml(result.name)}</option>`).join("");
}

function defaultLabelResultKey() {
  const results = state.labelingFile?.taxonomy?.results || {};
  return Object.entries(results).find(([key, result]) => key === "ok" || result.name === "正常")?.[0] || Object.keys(results)[0] || "";
}

function defaultLabelEventFormState() {
  const reasons = state.labelingFile?.taxonomy?.reasons || {};
  const resultKey = defaultLabelResultKey();
  const reasonKey = Object.entries(reasons).find(([, reason]) => reason.parent === resultKey)?.[0] || "";
  return { resultKey, reasonKey, confidence: 0.9, note: "", prototype: false, sourceTypeOverride: "" };
}

function labelEventFormStateFromAnnotation(annotation) {
  const latest = (annotation?.label_events || []).at(-1) || {};
  return {
    resultKey: latest.result_key || "",
    reasonKey: latest.reason_key || "",
    confidence: latest.result_confidence != null && Number.isFinite(Number(latest.result_confidence)) ? Number(latest.result_confidence) : 0.9,
    note: latest.note || "",
    prototype: false,
    sourceTypeOverride: String(latest.source || "").startsWith("expert") ? "expert" : "",
  };
}

function labelScopeKind(annotation) {
  const latest = (annotation?.label_events || []).at(-1) || {};
  return latest.scope_kind === "whole" || String(annotation?.sample_id || "").endsWith("_whole") ? "whole" : "event";
}

function ensureLabelEventEditor(card) {
  if (card._labelEventEditor) return card._labelEventEditor;
  const annotations = channelLabelAnnotations(card.dataset.labelSample, labelFileForCard(card));
  card._labelEventEditor = {
    activeKey: "",
    draftSerial: 0,
    colorSerial: annotations.length,
    items: annotations.map((annotation, index) => {
      const start = Math.max(0, Number(annotation.sample_scope?.start_s || 0));
      const end = Math.max(0, Number(annotation.sample_scope?.end_s || 0));
      const form = labelEventFormStateFromAnnotation(annotation);
      return {
        key: `saved:${annotation.sample_id}`,
        status: "saved",
        scopeKind: labelScopeKind(annotation),
        sampleId: annotation.sample_id,
        start,
        end,
        committedStart: start,
        committedEnd: end,
        color: LABEL_EVENT_COLORS[index % LABEL_EVENT_COLORS.length],
        form: { ...form },
        committedForm: { ...form },
        dirty: false,
      };
    }),
  };
  return card._labelEventEditor;
}

function activeLabelEvent(card) {
  const editor = ensureLabelEventEditor(card);
  return editor.items.find(item => item.key === editor.activeKey) || null;
}

function labelEventItemForSample(card, sampleId) {
  return ensureLabelEventEditor(card).items.find(item => item.status === "saved" && item.sampleId === sampleId) || null;
}

function readLabelEventForm(card) {
  return {
    resultKey: $("[data-label-result]", card)?.value || "",
    reasonKey: $("[data-label-reason]", card)?.value || "",
    confidence: Number($("input[type=radio]:checked", card)?.value || 0.9),
    note: $("[data-label-note]", card)?.value || "",
    prototype: card.dataset.prototype === "true",
    sourceTypeOverride: card.dataset.eventSourceTypeOverride || "",
  };
}

function labelEventItemIsDirty(item) {
  if (!item || item.status !== "saved") return false;
  const tolerance = 0.00051;
  return Math.abs(item.start - item.committedStart) > tolerance
    || Math.abs(item.end - item.committedEnd) > tolerance
    || JSON.stringify(item.form) !== JSON.stringify(item.committedForm);
}

function captureActiveLabelEventForm(card) {
  const item = activeLabelEvent(card);
  if (!item) return;
  item.form = readLabelEventForm(card);
  if (item.status === "saved") item.dirty = labelEventItemIsDirty(item);
  renderLabelEventSwitcher(card);
}

function restoreLabelEventForm(card, item) {
  const values = item?.form || defaultLabelEventFormState();
  const result = $("[data-label-result]", card);
  if (!result) return;
  const resultKey = Array.from(result.options).some(option => option.value === values.resultKey)
    ? values.resultKey
    : (result.options[0]?.value || "");
  result.value = resultKey;
  const reason = $("[data-label-reason]", card);
  reason.innerHTML = labelReasonOptions(resultKey);
  if (Array.from(reason.options).some(option => option.value === values.reasonKey)) reason.value = values.reasonKey;
  result._syncTouchPicker?.();
  reason._syncTouchPicker?.();
  $$('input[type="radio"]', card).forEach(input => { input.checked = Number(input.value) === Number(values.confidence); });
  if (!$('input[type="radio"]:checked', card) && $('input[type="radio"]', card)) $('input[type="radio"]', card).checked = true;
  $("[data-label-note]", card).value = values.note || "";
  card.dataset.prototype = values.prototype ? "true" : "false";
  card.dataset.eventSourceTypeOverride = values.sourceTypeOverride || "";
}

function renderLabelEventSwitcher(card) {
  const host = $("[data-event-switcher]", card);
  if (!host) return;
  const editor = ensureLabelEventEditor(card);
  host.innerHTML = editor.items.map((item, index) => {
    const active = item.key === editor.activeKey;
    const status = item.status === "saved" ? (item.dirty ? "修改待保存" : "已保存") : "待保存";
    const label = item.scopeKind === "whole" ? "整体" : item.sampleId || `新事件 ${index + 1}`;
    return `<button class="label-event-switch-button${active ? " is-active" : ""}" data-event-key="${escapeHtml(item.key)}" style="--event-color:${item.color}" type="button" title="${item.start.toFixed(3)}–${item.end.toFixed(3)} s"><i></i><span>${escapeHtml(label)}</span><small>${status} · ${item.start.toFixed(3)}–${item.end.toFixed(3)} s</small></button>`;
  }).join("") || '<span class="label-event-switch-empty">点击“新建事件”增加第一组边界</span>';
}

function createLabelEventDraft(card, start, end, scopeKind = "event") {
  const editor = ensureLabelEventEditor(card);
  const item = {
    key: `draft:${++editor.draftSerial}`,
    status: "draft",
    scopeKind,
    sampleId: "",
    start,
    end,
    color: LABEL_EVENT_COLORS[editor.colorSerial++ % LABEL_EVENT_COLORS.length],
    form: defaultLabelEventFormState(),
  };
  editor.items.push(item);
  editor.activeKey = item.key;
  return item;
}

function commitLabelEventItem(card, eventKey, saved, formState = null) {
  const editor = ensureLabelEventEditor(card);
  let item = editor.items.find(candidate => candidate.key === eventKey);
  if (!item) {
    item = {
      key: eventKey,
      status: "draft",
      scopeKind: saved.scope_kind === "whole" ? "whole" : "event",
      sampleId: "",
      start: Number(saved.sample_scope?.start_s || 0),
      end: Number(saved.sample_scope?.end_s || 0),
      color: LABEL_EVENT_COLORS[editor.colorSerial++ % LABEL_EVENT_COLORS.length],
      form: formState ? { ...formState } : labelEventFormStateFromAnnotation({ label_events: [saved] }),
    };
    editor.items.push(item);
  }
  const nextKey = `saved:${saved.sample_id}`;
  item.key = nextKey;
  item.status = "saved";
  item.scopeKind = saved.scope_kind === "whole" ? "whole" : item.scopeKind || "event";
  item.sampleId = saved.sample_id;
  item.start = Number(saved.sample_scope?.start_s ?? item.start);
  item.end = Number(saved.sample_scope?.end_s ?? item.end);
  item.form = formState ? { ...formState } : labelEventFormStateFromAnnotation({ label_events: [saved] });
  item.committedStart = item.start;
  item.committedEnd = item.end;
  item.committedForm = { ...item.form };
  item.dirty = false;
  if (editor.activeKey === eventKey) editor.activeKey = nextKey;
  return item;
}

function setLabelEventSavePending(card, saving) {
  card.dataset.eventSavePending = saving ? "true" : "false";
  card.classList.toggle("is-saving-event", saving);
  $$('[data-new-event], [data-label-whole], [data-close-event], [data-event-key], .label-event-block, .label-saved-event-row, [data-relabel-sample], [data-confirm-label-event], [data-delete-label-event], [data-cancel-label], [data-quick-anomaly], [data-scope-start], [data-scope-duration], [data-label-result], [data-label-reason], input[type="radio"], [data-label-note], [data-event-boundary], [data-event-range-move]', card).forEach(control => {
    control.disabled = saving || (control.matches('[data-new-event], [data-label-whole], .label-event-block, .label-saved-event-row') && card.dataset.signalReady !== "true") || (control.matches('[data-quick-anomaly]') && !activeLabelEvent(card));
  });
}

function setLabelPlaybackRate(value) {
  const rate = Math.max(0.1, Math.min(2, Math.round(Number(value || 1) * 10) / 10));
  state.labelingPlaybackRate = rate;
  $$('[data-playback-speed]').forEach(input => { input.value = String(rate); });
  $$('[data-playback-speed-value]').forEach(output => { output.textContent = `${rate.toFixed(1)}×`; });
  $$('[data-event-audio]').forEach(audio => {
    audio.defaultPlaybackRate = rate;
    audio.playbackRate = rate;
    audio._refreshPlaybackStatus?.();
  });
  return rate;
}

let labelAudioContext = null;

function ensureLabelAudioGain(audio) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;
  if (!audio._gainNode) {
    try {
      labelAudioContext ||= new AudioContextClass();
      audio._sourceNode = labelAudioContext.createMediaElementSource(audio);
      audio._gainNode = labelAudioContext.createGain();
      audio._sourceNode.connect(audio._gainNode).connect(labelAudioContext.destination);
      audio.volume = 1;
    } catch (error) {
      console.warn("音量增益初始化失败", error);
      return;
    }
  }
  audio._gainNode.gain.value = state.labelingPlaybackVolume;
  void labelAudioContext.resume();
}

function setLabelPlaybackVolume(value) {
  const percent = Math.max(0, Math.min(500, Math.round(Number(value))));
  state.labelingPlaybackVolume = percent / 100;
  $("#labelPlaybackVolume").value = String(percent);
  $("#labelPlaybackVolumeValue").textContent = `${percent}%`;
  $$('[data-event-audio]').forEach(audio => {
    if (audio._gainNode) audio._gainNode.gain.value = state.labelingPlaybackVolume;
    else audio.volume = Math.min(1, state.labelingPlaybackVolume);
  });
}

function stopOtherLabelAudio(activeAudio) {
  $$('[data-event-audio]').forEach(audio => {
    if (audio === activeAudio) return;
    audio._playRequestId = Number(audio._playRequestId || 0) + 1;
    if (audio.paused) {
      if (audio._statusKind === "正在载入音频") {
        audio._statusKind = "已停止（切换通道）";
        audio._refreshPlaybackStatus?.();
      }
      return;
    }
    audio._pauseReason = "已停止（切换通道）";
    audio.pause();
    const card = audio.closest("[data-label-sample]");
    if (card) $("[data-signal-plot]", card)?._refreshEventShapes?.();
  });
}

function channelLabelAnnotations(channelId, file = state.labelingFile) {
  const prefix = `${channelId}_`;
  return (file?.annotations || [])
    .filter(item => item.sample_id === channelId || String(item.sample_id || "").startsWith(prefix))
    .slice()
    .sort((left, right) => {
      const startDifference = Number(left.sample_scope?.start_s || 0) - Number(right.sample_scope?.start_s || 0);
      return startDifference || String(left.sample_id || "").localeCompare(String(right.sample_id || ""));
    });
}

function channelLabelEventCount(channelId, file = state.labelingFile) {
  return channelLabelAnnotations(channelId, file)
    .reduce((count, annotation) => count + (annotation.label_events || []).length, 0);
}

function labelEventKind(event, index = 0) {
  return event?.result_key === "nok" ? "abnormal" : event?.result_key === "boundary" ? "boundary" : `normal tone-${index % 4}`;
}

function selectLabelEvent(card, sampleId = "") {
  $$('[data-event-sample-id]', card).forEach(item => {
    item.classList.toggle("is-selected", Boolean(sampleId) && item.dataset.eventSampleId === sampleId);
  });
}

function populateLabelEventForm(card, sampleId) {
  const annotation = (labelFileForCard(card)?.annotations || []).find(item => item.sample_id === sampleId);
  const latest = (annotation?.label_events || []).at(-1);
  if (!latest) return;
  const result = $("[data-label-result]", card);
  if (latest.result_key && Array.from(result.options).some(option => option.value === latest.result_key)) {
    result.value = latest.result_key;
    $("[data-label-reason]", card).innerHTML = labelReasonOptions(latest.result_key);
  }
  const reason = $("[data-label-reason]", card);
  if (latest.reason_key && Array.from(reason.options).some(option => option.value === latest.reason_key)) reason.value = latest.reason_key;
  result._syncTouchPicker?.();
  reason._syncTouchPicker?.();
  const confidence = latest.result_confidence == null ? NaN : Number(latest.result_confidence);
  if (Number.isFinite(confidence)) {
    $$("input[type=radio]", card).forEach(input => { input.checked = Number(input.value) === confidence; });
  }
  $("[data-label-note]", card).value = latest.note || "";
}

function labelHistoryMarkup(sample, file = state.labelingFile) {
  const annotations = channelLabelAnnotations(sample?.sample_id, file);
  if (!annotations.length) {
    return '<div class="label-history-empty">当前通道暂无标注</div>';
  }
  return annotations.map((annotation, index) => {
    const scope = annotation.sample_scope || {};
    const start = Math.max(0, Number(scope.start_s || 0));
    const end = Math.max(start, Number(scope.end_s || start));
    const events = annotation.label_events || [];
    const latest = events.at(-1) || {};
    const kind = labelEventKind(latest, index);
    const result = latest.result_name || latest.result_key || "未标注";
    const reason = latest.reason_name || latest.reason_key || "—";
    const confidence = latest.result_confidence == null ? "—" : latest.result_confidence;
    const details = [latest.timestamp || "未记录时间", latest.note].filter(Boolean).join("· ");
    const versions = events.map((entry, eventIndex) => {
      const entryResult = entry.result_name || entry.result_key || "未标注";
      const entryReason = entry.reason_name || entry.reason_key || "—";
      const entryConfidence = entry.result_confidence == null ? "—" : entry.result_confidence;
      return `<div class="label-history-version"><b>第 ${eventIndex + 1} 次</b><span>${escapeHtml(entryResult)} / ${escapeHtml(entryReason)}</span><span>${escapeHtml(entry.source || "—")} · 置信度 ${escapeHtml(entryConfidence)}</span><span>${escapeHtml(entry.timestamp || "未记录时间")}${entry.note ? ` · ${escapeHtml(entry.note)}` : ""}</span><div class="label-history-version-actions"><button class="button button-secondary" data-relabel-sample="${escapeHtml(annotation.sample_id)}" data-label-event-uuid="${escapeHtml(entry.event_uuid || "")}" type="button">编辑</button><button class="button button-secondary" data-confirm-label-event="${escapeHtml(entry.event_uuid || "")}" data-event-sample="${escapeHtml(annotation.sample_id)}" type="button">确认</button><button class="button button-ghost label-delete-event-button" data-delete-label-event="${escapeHtml(entry.event_uuid || "")}" data-event-sample="${escapeHtml(annotation.sample_id)}" type="button">删除</button></div></div>`;
    }).reverse().join("");
    return `<div class="label-history-group">
      <button class="label-history-row label-saved-event-row is-${kind}" data-event-sample-id="${escapeHtml(annotation.sample_id)}" data-event-start="${start}" data-event-end="${end}" type="button" title="单击选择，双击编辑标签">
        <span class="label-history-order"><b>${escapeHtml(annotation.sample_id)}</b><small>${labelScopeKind(annotation) === "whole" ? "整体标注" : "事件标注"} · ${events.length} 次</small></span>
        <strong class="label-history-range">${start.toFixed(3)}–${end.toFixed(3)} s</strong>
        <span class="label-history-result">${escapeHtml(result)} / ${escapeHtml(reason)}</span>
        <span>${escapeHtml(latest.source || "—")} · 置信度 ${escapeHtml(confidence)}</span>
        <span class="label-history-details">${escapeHtml(details)}</span>
      </button><div class="label-history-versions">${versions}</div></div>`;
  }).join("");
}

function labelPlaybackControlsMarkup(audioUrl = "", file = state.labelingFile) {
  const allSegmentName = "全段";
  const playbackRate = Number(state.labelingPlaybackRate || 1).toFixed(1);
  return `<div class="label-audio-track-controls"><audio preload="metadata" data-event-audio data-audio-src="${audioUrl}"></audio><div class="audio-play-actions"><button class="button button-primary" data-playback-mode="all" type="button" disabled>▶ 播放${allSegmentName}</button><button class="button button-ghost" data-playback-mode="event" type="button" disabled>▶ 播放当前事件</button></div><div class="audio-play-feedback"><strong data-selected-event-summary>当前未选择事件</strong><span class="audio-play-status" data-audio-play-status>待播放 · ${playbackRate}×</span><input data-audio-seek type="range" min="0" max="1000" step="1" value="0" aria-label="拖动播放进度" disabled></div><button class="button button-ghost" data-close-event type="button" hidden>取消事件</button><a class="button button-ghost" href="${audioUrl}" download>下载全段</a></div>`;
}

function labelEventTrackMarkup(channelId, duration, file = state.labelingFile) {
  const events = channelLabelAnnotations(channelId, file);
  const blocks = events.map((item, index) => {
    const scope = item.sample_scope || {};
    const start = Math.max(0, Number(scope.start_s || 0));
    const end = Math.max(start, Number(scope.end_s || start));
    const latest = (item.label_events || []).at(-1) || {};
    const left = duration > 0 ? Math.min(100, start * 100 / duration) : 0;
    const width = duration > 0 ? Math.max(0.8, Math.min(100 - left, (end - start) * 100 / duration)) : 0;
    const kind = labelEventKind(latest, index);
    const title = `${labelScopeKind(item) === "whole" ? "整体标注" : "事件标注"} · ${item.sample_id} · ${start.toFixed(3)}–${end.toFixed(3)} s · ${latest.result_name || latest.result_key || "未标注"}${latest.reason_name ? ` / ${latest.reason_name}` : ""}`;
    return `<button class="label-event-block is-${kind}" style="left:${left}%;width:${width}%" data-event-sample-id="${escapeHtml(item.sample_id)}" data-event-start="${start}" data-event-end="${end}" type="button" title="${escapeHtml(title)}" disabled><span>${labelScopeKind(item) === "whole" ? "整体" : escapeHtml(item.sample_id)}</span></button>`;
  }).join("");
  return `<section class="label-audio-track" aria-label="已保存事件">
    <div class="audio-saved-events"><span>已保存事件 ${events.length}</span><div class="audio-saved-event-strip">${blocks || '<em>暂无事件</em>'}</div></div>
  </section>`;
}

function labelAudioAbortError() {
  return Object.assign(new Error("音频组件已释放"), { name: "AbortError" });
}

function disposeLabelAudio(audio) {
  if (!audio) return;
  audio._disposed = true;
  audio._playRequestId = Number(audio._playRequestId || 0) + 1;
  audio.pause();
  audio._sourceNode?.disconnect();
  audio._gainNode?.disconnect();
  audio._audioAbortController?.abort();
  delete audio._audioAbortController;
  audio._seekableCleanup?.();
  delete audio._seekableCleanup;
  if (audio._pendingObjectUrl) {
    URL.revokeObjectURL(audio._pendingObjectUrl);
    delete audio._pendingObjectUrl;
  }
  if (audio._objectUrl) {
    URL.revokeObjectURL(audio._objectUrl);
    delete audio._objectUrl;
  }
  audio.removeAttribute("src");
}

function labelChannelDisplay(sample, file) {
  const name = sample.display_name || sample.sample_id;
  if (!file.absolute_path?.toLowerCase().endsWith(".wav")) return { number: sample.sample_id, title: name };
  const index = Number(sample.locator?.channel_index);
  if (!Number.isInteger(index) || index < 0) return { number: sample.sample_id, title: name };
  const number = `channel_${index}`;
  return { number, title: `${number} = ${name}` };
}

function labelLayoutContext(files) {
  const profile = files.find(file => file.absolute_path?.toLowerCase().endsWith(".wav"))?.metadata?.wav_profile || selectedLabelWavProfile() || "tdms";
  const layoutKey = `${profile}:${files.length}`;
  const choices = files.flatMap((file, fileIndex) => (file.samples || []).map((sample, index) => ({
    key: `${fileIndex}:${index}`, file, sample,
    title: `${file.metadata?.direction ? `${file.metadata.direction} · ` : ""}${labelChannelDisplay(sample, file).title}`,
  }))).filter(item => !item.sample.missing);
  let settings = state.labelLayoutSettings.get(layoutKey);
  if (!settings) {
    const defaults = choices.filter(item => profile !== "motor" || Number(item.sample.locator?.channel_index) < 2);
    const rows = defaults.length > 1 ? 2 : 1;
    const columns = Math.max(1, Math.ceil(defaults.length / rows));
    settings = { rows, columns, slots: defaults.map(item => item.key) };
    state.labelLayoutSettings.set(layoutKey, settings);
  }
  return { choices, settings };
}

function syncLabelHistorySidebar() {
  const sidebar = $("#labelHistorySidebarContent");
  if (!sidebar) return;
  const scrollTop = sidebar.scrollTop;
  const cards = $$('[data-label-sample]', $("#labelChannelsRow"));
  sidebar.innerHTML = cards.length ? cards.map((card, index) => {
    const title = $(".label-channel-title h3", card)?.textContent || card.dataset.labelSample;
    const count = $(".label-channel-history-title span", card)?.textContent || "0 条";
    return `<section class="label-history-sidebar-section" data-history-card-index="${index}"><div class="label-history-sidebar-section-title"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(count)}</span></div><div class="label-history-sidebar-items">${$(".label-channel-history", card)?.innerHTML || ""}</div></section>`;
  }).join("") : '<div class="empty-state">当前文件暂无标签记录</div>';
  sidebar.scrollTop = scrollTop;
}

function renderLabelChannels() {
  const host = $("#labelChannelsRow");
  $$('[data-event-audio]', host).forEach(disposeLabelAudio);
  $$('[data-signal-plot]', host).forEach(plot => window.Plotly?.purge?.(plot));
  const firstResultKey = defaultLabelResultKey();
  const files = state.labelingFiles.length ? state.labelingFiles : [state.labelingFile].filter(Boolean);
  const { choices, settings } = labelLayoutContext(files);
  $("#labelGridRows").value = settings.rows;
  $("#labelGridColumns").value = settings.columns;
  host.style.setProperty("--layout-rows", settings.rows);
  host.style.setProperty("--layout-columns", settings.columns);
  $("#labelLayoutSlots").style.setProperty("--layout-columns", settings.columns);
  $("#labelLayoutSlots").innerHTML = Array.from({ length: settings.rows * settings.columns }, (_, slotIndex) => `<label class="label-layout-slot"><span>位置 ${slotIndex + 1}</span><select data-layout-slot="${slotIndex}"><option value="">不显示</option>${choices.map(item => `<option value="${item.key}" ${settings.slots[slotIndex] === item.key ? "selected" : ""}>${escapeHtml(item.title)}</option>`).join("")}</select></label>`).join("");
  const slotOrder = (fileIndex, index) => settings.slots.indexOf(`${fileIndex}:${index}`);
  const analysisChoices = files.flatMap((file, fileIndex) => (file.samples || []).map((sample, index) => sample.missing || slotOrder(fileIndex, index) < 0 ? "" : `<option value="${fileIndex}:${index}">${escapeHtml(file.metadata?.direction ? `${file.metadata.direction} · ` : "")}${escapeHtml(labelChannelDisplay(sample, file).title)}</option>`)).join("");
  $("#labelAnalysisChannel").innerHTML = analysisChoices;
  $("#openNavigationAnalysisButton").disabled = !analysisChoices;
  host.innerHTML = files.map((file, fileIndex) => {
    const wavDirection = file.metadata?.direction || "";
    return (file.samples || []).map((sample, index) => {
    const position = slotOrder(fileIndex, index);
    if (position < 0) return "";
    const sampleId = escapeHtml(sample.sample_id);
    const display = labelChannelDisplay(sample, file);
    const scope = sample.sample_scope || {};
    const duration = Number(sample.duration_s || scope.end_s || 0);
    const arrow = sample.sample_id === "up" ? "↑" : sample.sample_id === "down" ? "↓" : "●";
    if (sample.missing) return `<article id="label-channel-${fileIndex}-${index}" class="panel label-channel-col label-missing-channel" style="grid-row:${Math.floor(position / settings.columns) + 1};grid-column:${position % settings.columns + 1}"><div class="label-channel-head"><div class="label-channel-title"><span class="label-channel-arrow">${arrow}</span><div><p class="kicker">通道 · ${escapeHtml(wavDirection || "WAV")}</p><h3>${escapeHtml(display.title)}</h3></div></div><span class="badge badge-neutral">${escapeHtml(display.number)}</span></div><div class="empty-state">该 WAV 未包含此通道，保持为空</div></article>`;
    return `
      <article id="label-channel-${fileIndex}-${index}" class="panel label-channel-col" style="grid-row:${Math.floor(position / settings.columns) + 1};grid-column:${position % settings.columns + 1}" tabindex="-1" data-label-sample="${sampleId}" data-label-file="${escapeHtml(file.absolute_path || "")}">
        <div class="label-channel-topline">
        <div class="label-channel-head">
          <div class="label-channel-title"><span class="label-channel-arrow">${arrow}</span><div><p class="kicker">通道${wavDirection ? ` · ${escapeHtml(wavDirection)}` : ""}</p><h3>${escapeHtml(display.title)}</h3></div></div>
        </div>
        ${labelPlaybackControlsMarkup(API.labelingAudio(file.absolute_path, sample.sample_id, $("#labelLineSelect").value, file.metadata?.wav_profile || "", state.labelingLowFrequencyFilter), file)}
        <div class="label-channel-tools"><button class="button button-secondary" data-label-whole type="button" disabled>标注全段</button><span class="label-channel-tools-meta"><span class="badge badge-neutral">${escapeHtml(display.number)}</span><button class="button button-secondary" data-quick-anomaly type="button" disabled>★ 标记典型异常</button></span><button class="button button-primary label-new-event-top" data-new-event type="button" disabled>＋ 新建事件</button></div>
        </div>
        <section class="label-analysis-card label-timeline-stack">
          <div class="label-combined-plot" data-signal-plot></div>
          ${labelEventTrackMarkup(sample.sample_id, duration, file)}
        </section>
        <div class="label-signal-hint" data-waveform-hint>正在读取曲线和频谱…</div>
        <section class="label-analysis-card label-entry-card">
        <div class="label-save-feedback" data-label-save-feedback hidden>✓ 标注保存成功</div>
        <form class="label-channel-form" hidden>
          <div class="label-range-fields"><label class="event-start-control"><span>● 开始</span><input data-scope-start type="number" min="0" step="0.001" value="${escapeHtml(scope.start_s ?? 0)}"></label><label class="event-end-control"><span>● 持续时间（秒）</span><input data-scope-duration type="number" min="0.001" step="0.001" value="1.000"></label></div>
          <label class="label-result-field"><span>结果 result</span><select data-label-result aria-label="结果 result">${labelResultOptions()}</select></label>
          <label class="label-reason-field"><span>原因 reason</span><select data-label-reason aria-label="原因 reason">${labelReasonOptions(firstResultKey)}</select></label>
          <fieldset class="label-confidence"><legend>置信度</legend><div class="label-confidence-options">
            ${[0.9, 0.6, 0.3].map(value => `<label><input type="radio" name="label-confidence-${fileIndex}-${index}" value="${value}" ${value === 0.9 ? "checked" : ""}><span>${value}</span></label>`).join("")}
          </div></fieldset>
          <label class="full-width"><span>备注</span><textarea data-label-note rows="1" placeholder="备注（可选）"></textarea></label>
          <div class="label-form-actions"><button class="button button-primary" data-save-label type="submit">保存标注</button><button class="button button-ghost" data-cancel-label type="button">取消</button></div>
        </form></section>
        <div class="label-channel-history-title"><strong>🕘 已有标注</strong><span>${channelLabelEventCount(sample.sample_id, file)} 条</span></div>
        <div class="label-channel-history">${labelHistoryMarkup(sample, file)}</div>
      </article>`;
    }).join("");
  }).join("");
  $$('[data-label-result], [data-label-reason]', host).forEach(enhanceLabelTouchSelect);
  syncLabelHistorySidebar();
  if (!$("#labelTabAnnotation").hidden) void loadCurrentLabelWaveforms();
}

function releaseLabelChannelWaveform(card) {
  if (!card?._labelSignal || !card.querySelector('[data-event-audio]')?.paused) return;
  const plot = $('[data-signal-plot]', card);
  window.Plotly?.purge?.(plot);
  plot.replaceChildren();
  for (const key of ["_syncCompactLayout", "_syncAudioTrackGeometry", "_syncPlayheadGeometry", "_refreshEventShapes", "_selectLabelEventItem", "_activateEventSelection", "_clearActiveEventSelection", "_commitSelectionInputs", "_labelAfterPlotHandler", "_labelRelayoutHandler"]) delete plot[key];
  const audio = $('[data-event-audio]', card);
  const sourceUrl = audio.dataset.audioSrc;
  disposeLabelAudio(audio);
  $('.label-audio-track-controls', card).outerHTML = labelPlaybackControlsMarkup(sourceUrl);
  delete card._labelSignal;
  card.dataset.signalReady = "false";
  $$('[data-playback-mode], [data-new-event], [data-label-whole]', card).forEach(button => { button.disabled = true; });
  $('[data-waveform-hint]', card).textContent = "进入标注界面时重新加载波形…";
}

function releaseInactiveLabelWaveforms() {
  $$('[data-label-sample]', $("#labelChannelsRow")).forEach(card => {
    const audio = $('[data-event-audio]', card);
    if (audio && !audio.paused) {
      audio._pauseReason = "已停止（切换页面）";
      audio.pause();
    }
    releaseLabelChannelWaveform(card);
  });
}

function loadCurrentLabelWaveforms() {
  const generation = state.labelingResolveGeneration;
  if (labelWaveformLoadTask && labelWaveformLoadGeneration === generation) return labelWaveformLoadTask;
  labelWaveformLoadGeneration = generation;
  const token = ++labelWaveformLoadToken;
  const task = (async () => {
    for (const file of state.labelingFiles) {
      for (const sample of file.samples || []) {
        if (token !== labelWaveformLoadToken || generation !== state.labelingResolveGeneration || $("#labelTabAnnotation").hidden) return;
        if (sample.missing) continue;
        const card = $$('[data-label-sample]', $("#labelChannelsRow")).find(item => item.dataset.labelFile === file.absolute_path && item.dataset.labelSample === sample.sample_id);
        if (!card) continue;
        if (card?._labelSignal) continue;
        await loadLabelWaveform(sample, file, generation, token);
      }
    }
  })();
  labelWaveformLoadTask = task;
  void task.finally(() => { if (labelWaveformLoadTask === task) labelWaveformLoadTask = null; });
  return task;
}

function plotlyLayout(height = 240) {
  return {
    autosize: true,
    height,
    margin: { l: 48, r: 12, t: 8, b: 34 },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#eaf0f8",
    font: { size: 11, color: "#475569" },
    xaxis: { title: "时间（秒）", gridcolor: "#ffffff", zerolinecolor: "#cbd5e1" },
    yaxis: { title: "振幅", gridcolor: "#ffffff", zerolinecolor: "#cbd5e1" },
    showlegend: false,
  };
}

function renderDefaultSignalPlots(card, payload) {
  if (!window.Plotly) return;
  const filtered = payload.low_frequency_filter === true;
  const allSegmentName = "全段";
  const eventEditor = ensureLabelEventEditor(card);
  const duration = Number(payload.duration_s || 0);
  const compactPlot = card.clientWidth < 560;
  const cutStart = Number(payload.cut_start_s ?? Math.max(0, (duration - Number(payload.cut_duration_s || duration)) / 2));
  const cutEnd = Number(payload.cut_end_s ?? (cutStart + Number(payload.cut_duration_s || duration)));
  const melTimes = (payload.mel_times || []).map(value => Number(value) + cutStart);
  const startInput = $("[data-scope-start]", card);
  const durationInput = $("[data-scope-duration]", card);
  startInput.max = duration;
  durationInput.max = duration;
  const initialStart = Math.max(0, Math.min(duration, Number(startInput.value || 0)));
  const initialEnd = Math.max(initialStart, Math.min(duration, initialStart + Number(durationInput.value || 1)));
  const eventFillColor = color => {
    const value = String(color || "#f97316").replace("#", "");
    const integer = Number.parseInt(value, 16);
    return `rgba(${(integer >> 16) & 255}, ${(integer >> 8) & 255}, ${integer & 255}, 0.12)`;
  };
  const plotEventShapes = () => eventEditor.items.flatMap(item => {
    const active = item.key === eventEditor.activeKey && card.dataset.eventActive === "true";
    const line = { color: item.color, width: active ? 4 : 2, dash: item.status === "draft" ? "dash" : "solid" };
    return (filtered ? [["x", "y"], ["x2", "y2"], ["x3", "y3"]] : [["x", "y"], ["x2", "y2"]]).flatMap(([xref, yref]) => [
      ...(active ? [{ type: "rect", xref, yref: `${yref} domain`, x0: item.start, x1: item.end, y0: 0, y1: 1, line: { width: 0 }, fillcolor: eventFillColor(item.color), layer: "above", editable: false }] : []),
      { type: "line", xref, yref: `${yref} domain`, x0: item.start, x1: item.start, y0: 0, y1: 1, line, layer: "above", editable: false },
      { type: "line", xref, yref: `${yref} domain`, x0: item.end, x1: item.end, y0: 0, y1: 1, line, layer: "above", editable: false },
    ]);
  });
  const lineTrace = (y, start, end, xaxis, yaxis) => ({ x0: start, dx: (end - start) / Math.max(1, y.length - 1), y, xaxis, yaxis, type: y.length > 50000 ? "scattergl" : "scatter", mode: "lines", line: { color: "blue", width: 1 }, hovertemplate: "%{x:.3f}s<br>%{y:.4f}<extra></extra>" });
  const axis = (matches, showTickLabels = false) => ({
    domain: [0, 1],
    matches,
    range: [0, duration],
    autorange: false,
    showticklabels: showTickLabels,
    gridcolor: "#ffffff",
    zerolinecolor: "#cbd5e1",
    ...(showTickLabels ? { title: "时间（秒）" } : {}),
  });
  const layout = {
    autosize: true,
    height: filtered ? 700 : 520,
    margin: compactPlot ? { l: 56, r: 28, t: 12, b: 42 } : { l: 100, r: 76, t: 12, b: 42 },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#eaf0f8",
    font: { size: 11, color: "#475569" },
    showlegend: false,
    hovermode: "x",
    dragmode: "zoom",
    xaxis: axis(undefined),
    xaxis2: axis("x", !filtered),
    ...(filtered ? { xaxis3: axis("x", true) } : {}),
    yaxis: { domain: filtered ? [0.69, 1] : [0.52, 1], title: "原始时域", gridcolor: "#ffffff", zerolinecolor: "#cbd5e1" },
    yaxis2: { domain: filtered ? [0.35, 0.66] : [0, 0.46], title: filtered ? "20 Hz 高通后时域" : "mel频谱", gridcolor: "#ffffff", zerolinecolor: "#cbd5e1" },
    ...(filtered ? { yaxis3: { domain: [0, 0.31], title: "mel频谱", gridcolor: "#ffffff", zerolinecolor: "#cbd5e1" } } : {}),
    annotations: [],
    shapes: plotEventShapes(),
  };
  const traces = [lineTrace(payload.values || [], 0, duration, "x", "y")];
  if (filtered) traces.push(lineTrace(payload.highpass_20hz_values || [], cutStart, cutEnd, "x2", "y2"));
  traces.push({ x: melTimes, y: payload.mel_freqs, z: payload.mel_db || [], xaxis: filtered ? "x3" : "x2", yaxis: filtered ? "y3" : "y2", type: "heatmap", colorscale: AI2_SPECTRUM_COLORS, zmin: -50, zmax: 0, zsmooth: false, colorbar: { title: compactPlot ? "dB" : "功率（dB）", thickness: compactPlot ? 12 : 18, x: 1.02, len: filtered ? 0.31 : 0.46, y: filtered ? 0.155 : 0.23 }, hovertemplate: "%{x:.3f}s<br>%{y:.0f}Hz<br>%{z:.1f} dB<extra></extra>" });
  const plot = $("[data-signal-plot]", card);
  if (plot._labelAfterPlotHandler) plot.removeListener?.("plotly_afterplot", plot._labelAfterPlotHandler);
  if (plot._labelRelayoutHandler) plot.removeListener?.("plotly_relayout", plot._labelRelayoutHandler);
  $(".plot-event-handle-layer", plot)?.remove();
  $(".plot-playhead-layer", plot)?.remove();
  Plotly.react(plot, traces, layout, { responsive: true, displaylogo: false, scrollZoom: true, editable: false });
  const eventHandleLayer = document.createElement("div");
  eventHandleLayer.className = "plot-event-handle-layer";
  eventHandleLayer.hidden = true;
  eventHandleLayer.innerHTML = `
    <button class="plot-event-handle is-start" data-event-boundary="start" type="button" aria-label="拖动事件开始边界" title="拖动调整开始；方向键微调，Shift 加速"><span>开始</span></button>
    <button class="plot-event-handle is-end" data-event-boundary="end" type="button" aria-label="拖动事件结束边界" title="拖动调整结束；方向键微调，Shift 加速"><span>结束</span></button>
    <button class="plot-event-range-handle" data-event-range-move type="button" aria-label="整体平移事件范围" title="拖动时两条边界平行移动；方向键微调"><span>↔ 整体</span></button>`;
  plot.append(eventHandleLayer);
  const playheadLayer = document.createElement("div");
  playheadLayer.className = "plot-playhead-layer";
  playheadLayer.hidden = true;
  playheadLayer.innerHTML = '<i class="plot-playhead-line" data-playhead-axis="yaxis"></i><i class="plot-playhead-line" data-playhead-axis="yaxis2"></i>' + (filtered ? '<i class="plot-playhead-line" data-playhead-axis="yaxis3"></i>' : '');
  plot.append(playheadLayer);
  let playheadTime = null;
  const syncPlayheadGeometry = (time = playheadTime) => {
    playheadTime = Number.isFinite(time) ? time : null;
    const xAxis = plot?._fullLayout?.xaxis;
    const start = Number(xAxis?.range?.[0]);
    const end = Number(xAxis?.range?.[1]);
    const span = end - start;
    const visible = playheadTime !== null && xAxis?._length > 0 && Number.isFinite(span) && span !== 0
      && (playheadTime - start) / span >= 0 && (playheadTime - start) / span <= 1;
    playheadLayer.hidden = !visible;
    if (!visible) return;
    const left = xAxis._offset + (playheadTime - start) / span * xAxis._length;
    $$('[data-playhead-axis]', playheadLayer).forEach(line => {
      const yAxis = plot?._fullLayout?.[line.dataset.playheadAxis];
      if (!yAxis) return;
      line.style.left = `${left}px`;
      line.style.top = `${yAxis._offset}px`;
      line.style.height = `${yAxis._length}px`;
    });
  };
  plot._syncPlayheadGeometry = syncPlayheadGeometry;
  plot.dataset.compactPlot = String(compactPlot);
  plot._syncCompactLayout = () => {
    const nextCompact = card.clientWidth < 560;
    if (plot.dataset.compactPlot === String(nextCompact)) return Promise.resolve();
    plot.dataset.compactPlot = String(nextCompact);
    return Promise.all([
      Plotly.relayout(plot, nextCompact
        ? { "margin.l": 56, "margin.r": 28 }
        : { "margin.l": 100, "margin.r": 76 }),
      Plotly.restyle(plot, {
        "colorbar.title.text": nextCompact ? "dB" : "功率（dB）",
        "colorbar.thickness": nextCompact ? 12 : 18,
      }, [filtered ? 2 : 1]),
    ]);
  };
  const syncSavedEventPositions = axis => {
    const range = axis?.range || [0, duration];
    const rangeStart = Math.min(Number(range[0]), Number(range[1]));
    const rangeEnd = Math.max(Number(range[0]), Number(range[1]));
    const visibleDuration = rangeEnd - rangeStart;
    if (!Number.isFinite(visibleDuration) || visibleDuration <= 0) return;
    $$('.audio-saved-event-strip [data-event-sample-id]', card).forEach(block => {
      const start = Number(block.dataset.eventStart);
      const end = Number(block.dataset.eventEnd);
      const visibleStart = Math.max(rangeStart, start);
      const visibleEnd = Math.min(rangeEnd, end);
      block.hidden = visibleEnd <= visibleStart;
      if (block.hidden) return;
      const left = Math.max(0, Math.min(100, (visibleStart - rangeStart) * 100 / visibleDuration));
      const width = Math.max(0.8, Math.min(100 - left, (visibleEnd - visibleStart) * 100 / visibleDuration));
      block.style.left = `${left}%`;
      block.style.width = `${width}%`;
    });
  };
  const syncEventHandles = () => {
    const xAxis = plot?._fullLayout?.xaxis;
    const yAxis = plot?._fullLayout?.yaxis;
    const active = card.dataset.eventActive === "true";
    const activeItem = activeLabelEvent(card);
    if (!active || !activeItem || activeItem.scopeKind === "whole" || !xAxis || !yAxis || !plot.clientWidth) {
      eventHandleLayer.hidden = true;
      return;
    }
    const rangeStart = Number(xAxis.range?.[0]);
    const rangeEnd = Number(xAxis.range?.[1]);
    const rangeDuration = rangeEnd - rangeStart;
    if (!Number.isFinite(rangeDuration) || rangeDuration === 0) {
      eventHandleLayer.hidden = true;
      return;
    }
    eventHandleLayer.hidden = false;
    eventHandleLayer.style.setProperty("--event-color", activeItem.color);
    const selectionValues = [Number(card.dataset.scopeStart), Number(card.dataset.scopeEnd)];
    const selectionPixels = selectionValues.map(value => xAxis._offset + ((value - rangeStart) / rangeDuration) * xAxis._length);
    $$('[data-event-boundary]', eventHandleLayer).forEach(handle => {
      const value = Number(handle.dataset.eventBoundary === "start" ? card.dataset.scopeStart : card.dataset.scopeEnd);
      const ratio = (value - rangeStart) / rangeDuration;
      const visible = Number.isFinite(ratio) && ratio >= 0 && ratio <= 1;
      handle.hidden = !visible;
      if (!visible) return;
      handle.style.left = `${xAxis._offset + ratio * xAxis._length}px`;
      handle.style.top = `${yAxis._offset}px`;
      handle.style.height = `${yAxis._length}px`;
    });
    const rangeHandle = $("[data-event-range-move]", eventHandleLayer);
    const axisLeft = xAxis._offset;
    const axisRight = xAxis._offset + xAxis._length;
    const visibleLeft = Math.max(axisLeft, Math.min(...selectionPixels));
    const visibleRight = Math.min(axisRight, Math.max(...selectionPixels));
    eventHandleLayer.classList.toggle("is-tight", visibleRight - visibleLeft < 130);
    rangeHandle.hidden = !selectionPixels.every(Number.isFinite) || visibleRight <= visibleLeft;
    if (!rangeHandle.hidden) {
      rangeHandle.style.left = `${(visibleLeft + visibleRight) / 2}px`;
      rangeHandle.style.top = `${yAxis._offset + 38}px`;
    }
  };
  const syncAudioTrackGeometry = () => {
    const track = $(".label-audio-track", card);
    const axis = plot?._fullLayout?.xaxis;
    if (!track || !axis || !plot.clientWidth) return;
    track.style.marginLeft = `${Math.max(0, axis._offset)}px`;
    track.style.marginRight = `${Math.max(0, plot.clientWidth - axis._offset - axis._length)}px`;
    syncSavedEventPositions(axis);
    syncEventHandles();
    syncPlayheadGeometry();
  };
  plot._syncAudioTrackGeometry = syncAudioTrackGeometry;
  plot._labelAfterPlotHandler = syncAudioTrackGeometry;
  plot.on("plotly_afterplot", syncAudioTrackGeometry);
  requestAnimationFrame(syncAudioTrackGeometry);
  setTimeout(syncAudioTrackGeometry, 120);
  const eventAudio = $("[data-event-audio]", card);
  eventAudio._disposed = false;
  eventAudio._playRequestId = 0;
  const playStatus = $("[data-audio-play-status]", card);
  const seekSlider = $("[data-audio-seek]", card);
  const eventSummary = $("[data-selected-event-summary]", card);
  const modeButtons = $$('[data-playback-mode]', card);
  eventAudio.preservesPitch = true;
  if ("webkitPreservesPitch" in eventAudio) eventAudio.webkitPreservesPitch = true;
  eventAudio.defaultPlaybackRate = state.labelingPlaybackRate;
  eventAudio.playbackRate = state.labelingPlaybackRate;
  eventAudio.volume = Math.min(1, state.labelingPlaybackVolume);
  const activeEventName = () => {
    const item = activeLabelEvent(card);
    if (!item) return "当前未选择事件";
    if (item.scopeKind === "whole") return item.sampleId ? "整体标注 · 已保存" : "整体标注 · 待保存";
    const index = ensureLabelEventEditor(card).items.indexOf(item) + 1;
    return item.sampleId ? `当前事件 · ${item.sampleId}` : `新事件 ${index} · 待保存`;
  };
  const refreshPlaybackStatus = () => {
    const item = activeLabelEvent(card);
    if (eventSummary) {
      eventSummary.textContent = item
        ? `${activeEventName()} · ${item.start.toFixed(3)}–${item.end.toFixed(3)} s（${Math.max(0, item.end - item.start).toFixed(3)} s）`
        : "当前未选择事件";
    }
    if (!playStatus) return;
    const rate = Number(state.labelingPlaybackRate || 1).toFixed(1);
    const start = Number(eventAudio._playStartLocal || 0);
    const end = Number(eventAudio._playEndLocal || 0);
    const elapsed = Math.max(0, Math.min(Math.max(0, end - start), Number(eventAudio.currentTime || 0) - start));
    const total = Math.max(0, end - start);
    const progress = eventAudio._seekReady ? ` ${elapsed.toFixed(2)} / ${total.toFixed(2)} s` : "";
    playStatus.textContent = `${eventAudio._statusKind || "待播放"}${progress} · ${rate}×`;
    if (!seekSlider._scrubbing) seekSlider.value = total > 0 ? String(Math.round(elapsed * 1000 / total)) : "0";
  };
  eventAudio._refreshPlaybackStatus = refreshPlaybackStatus;
  seekSlider.addEventListener("pointerdown", () => { seekSlider._scrubbing = true; });
  seekSlider.addEventListener("pointerup", () => { seekSlider._scrubbing = false; refreshPlaybackStatus(); });
  seekSlider.addEventListener("pointercancel", () => { seekSlider._scrubbing = false; refreshPlaybackStatus(); });
  seekSlider.addEventListener("input", () => {
    if (!eventAudio._seekReady) return;
    const start = Number(eventAudio._playStartLocal || 0);
    const end = Number(eventAudio._playEndLocal || 0);
    eventAudio.currentTime = start + (end - start) * Number(seekSlider.value) / 1000;
    syncPlayheadGeometry(Number(eventAudio._playOrigin || cutStart) + eventAudio.currentTime);
    refreshPlaybackStatus();
  });
  const loadPlaybackAudio = async (sourceUrl, expectedRange = null) => {
    if (eventAudio._disposed || !eventAudio.isConnected) return Promise.reject(labelAudioAbortError());
    eventAudio._audioAbortController?.abort();
    eventAudio._seekableCleanup?.();
    delete eventAudio._seekableCleanup;
    if (eventAudio._objectUrl) {
      URL.revokeObjectURL(eventAudio._objectUrl);
      delete eventAudio._objectUrl;
    }
    eventAudio.removeAttribute("src");
    eventAudio.load();
    const controller = new AbortController();
    eventAudio._audioAbortController = controller;
    return fetch(sourceUrl, { signal: controller.signal, cache: "no-store" })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (expectedRange) {
          const actualStart = Number(response.headers.get("X-Audio-Start-S"));
          const actualEnd = Number(response.headers.get("X-Audio-End-S"));
          if (!response.headers.has("X-Audio-Start-S") || !response.headers.has("X-Audio-End-S")
            || !Number.isFinite(actualStart) || !Number.isFinite(actualEnd)
            || Math.abs(actualStart - expectedRange.start) > 0.002
            || Math.abs(actualEnd - expectedRange.end) > 0.002) {
            throw new Error("服务仍在返回整段音频，请重启 AI-3.0 服务后重试");
          }
          eventAudio._playOrigin = actualStart;
          eventAudio._playEndLocal = actualEnd - actualStart;
        }
        return response.blob();
      })
      .then(blob => new Promise((resolve, reject) => {
        if (eventAudio._disposed || !eventAudio.isConnected) return reject(labelAudioAbortError());
        const objectUrl = URL.createObjectURL(blob);
        eventAudio._pendingObjectUrl = objectUrl;
        const clear = () => {
          eventAudio.removeEventListener("loadedmetadata", onLoaded);
          eventAudio.removeEventListener("error", onError);
        };
        eventAudio._seekableCleanup = () => {
          clear();
          URL.revokeObjectURL(objectUrl);
          if (eventAudio._pendingObjectUrl === objectUrl) delete eventAudio._pendingObjectUrl;
          reject(labelAudioAbortError());
        };
        const onLoaded = () => {
          clear();
          delete eventAudio._seekableCleanup;
          if (eventAudio._disposed || !eventAudio.isConnected) {
            URL.revokeObjectURL(objectUrl);
            if (eventAudio._pendingObjectUrl === objectUrl) delete eventAudio._pendingObjectUrl;
            reject(labelAudioAbortError());
            return;
          }
          delete eventAudio._pendingObjectUrl;
          eventAudio._objectUrl = objectUrl;
          eventAudio.defaultPlaybackRate = state.labelingPlaybackRate;
          eventAudio.playbackRate = state.labelingPlaybackRate;
          eventAudio._seekReady = true;
          eventAudio._loadedPlaybackMode = eventAudio._playbackMode;
          seekSlider.disabled = false;
          refreshPlaybackStatus();
          resolve();
        };
        const onError = () => {
          clear();
          delete eventAudio._seekableCleanup;
          URL.revokeObjectURL(objectUrl);
          if (eventAudio._pendingObjectUrl === objectUrl) delete eventAudio._pendingObjectUrl;
          reject(eventAudio._disposed ? labelAudioAbortError() : new Error("音频数据无法载入"));
        };
        eventAudio.addEventListener("loadedmetadata", onLoaded);
        eventAudio.addEventListener("error", onError);
        eventAudio.src = objectUrl;
        eventAudio.load();
      }))
      .finally(() => {
        if (eventAudio._audioAbortController === controller) delete eventAudio._audioAbortController;
      });
  };
  const setPlaybackMode = (mode, pause = true) => {
    eventAudio._playRequestId = Number(eventAudio._playRequestId || 0) + 1;
    eventAudio._seekReady = false;
    seekSlider.disabled = true;
    seekSlider.value = "0";
    syncPlayheadGeometry(null);
    if (pause && !eventAudio.paused) {
      eventAudio._pauseReason = "范围已更新";
      eventAudio.pause();
    }
    const scopeStart = Math.max(cutStart, Math.min(cutEnd, Number(card.dataset.scopeStart ?? cutStart)));
    const scopeEnd = Math.max(scopeStart, Math.min(cutEnd, Number(card.dataset.scopeEnd ?? cutEnd)));
    eventAudio._playbackMode = mode;
    eventAudio._playOrigin = mode === "event" ? scopeStart : cutStart;
    eventAudio._playStartLocal = 0;
    eventAudio._playEndLocal = mode === "event" ? scopeEnd - scopeStart : Math.max(0, cutEnd - cutStart);
    modeButtons.forEach(button => {
      const active = button.dataset.playbackMode === mode;
      button.classList.toggle("button-primary", active);
      button.classList.toggle("button-ghost", !active);
    });
    eventAudio._statusKind = mode === "event" ? "待播放当前事件" : `待播放${allSegmentName}`;
    refreshPlaybackStatus();
  };
  eventAudio._setPlaybackMode = setPlaybackMode;
  card.dataset.cutStart = String(cutStart);
  card.dataset.cutEnd = String(cutEnd);
  let selectionSyncId = 0;
  const refreshPlotShapes = () => {
    plot.dataset.syncingSelection = "true";
    const currentSyncId = ++selectionSyncId;
    return Plotly.relayout(plot, { shapes: plotEventShapes() })
      .finally(() => {
        if (currentSyncId === selectionSyncId) delete plot.dataset.syncingSelection;
        syncEventHandles();
      });
  };
  plot._refreshEventShapes = refreshPlotShapes;
  const updateSelection = (start, end, updatePlot = true) => {
    const activeItem = activeLabelEvent(card);
    if (card.dataset.relabeling === "true" && activeItem?.status === "saved") {
      start = activeItem.committedStart;
      end = activeItem.committedEnd;
    }
    if (activeItem?.scopeKind === "whole") {
      start = cutStart;
      end = cutEnd;
    }
    const minimum = Math.max(cutStart, Math.min(cutEnd, Number(start)));
    const maximum = Math.max(cutStart, Math.min(cutEnd, Number(end)));
    const orderedStart = Math.min(minimum, maximum);
    const orderedEnd = Math.max(minimum, maximum);
    startInput.value = orderedStart.toFixed(3);
    durationInput.value = (orderedEnd - orderedStart).toFixed(3);
    card.dataset.scopeStart = String(orderedStart);
    card.dataset.scopeEnd = String(orderedEnd);
    startInput.readOnly = activeItem?.scopeKind === "whole" || card.dataset.relabeling === "true";
    durationInput.readOnly = activeItem?.scopeKind === "whole" || card.dataset.relabeling === "true";
    const item = activeLabelEvent(card);
    $("[data-quick-anomaly]", card).disabled = !item || card.dataset.eventSavePending === "true";
    if (item) {
      item.start = orderedStart;
      item.end = orderedEnd;
      if (item.status === "saved") item.dirty = labelEventItemIsDirty(item);
      card.style.setProperty("--active-event-color", item.color);
    }
    renderLabelEventSwitcher(card);
    const closeButton = $("[data-close-event]", card);
    if (closeButton && item) closeButton.textContent = item.status === "draft" ? "移除此事件" : "退出事件";
    if (updatePlot) {
      card.dataset.eventActive = "true";
      closeButton.hidden = false;
      refreshPlotShapes();
      const eventModeButton = $('[data-playback-mode="event"]', card);
      eventModeButton.disabled = false;
      setPlaybackMode("event");
    }
    refreshPlaybackStatus();
  };
  updateSelection(initialStart, initialEnd, false);
  renderLabelEventSwitcher(card);
  plot._selectLabelEventItem = (eventKey, showForm = true) => {
    if (eventEditor.activeKey && eventEditor.activeKey !== eventKey) captureActiveLabelEventForm(card);
    const item = eventEditor.items.find(candidate => candidate.key === eventKey);
    if (!item) return;
    card.dataset.relabeling = "false";
    $("[data-save-label]", card).textContent = "保存标注";
    eventEditor.activeKey = item.key;
    if (item.sampleId) card.dataset.eventSampleId = item.sampleId;
    else delete card.dataset.eventSampleId;
    card.dataset.eventActive = "true";
    restoreLabelEventForm(card, item);
    selectLabelEvent(card, item.sampleId || "");
    updateSelection(item.start, item.end, true);
    $(".label-channel-form", card).hidden = !showForm;
  };
  plot._activateEventSelection = (start, end) => {
    card.dataset.eventActive = "true";
    updateSelection(start, end, true);
  };
  plot._clearActiveEventSelection = () => {
    eventEditor.activeKey = "";
    card.dataset.relabeling = "false";
    card.dataset.eventActive = "false";
    $("[data-quick-anomaly]", card).disabled = true;
    delete card.dataset.eventSampleId;
    renderLabelEventSwitcher(card);
    refreshPlaybackStatus();
    refreshPlotShapes();
    syncEventHandles();
  };
  delete plot._labelRelayoutHandler;
  const boundaryStep = Math.max(0.001, 1 / Math.max(1, Number(payload.sampling_rate_hz || 1000)));
  const applyBoundaryValue = (boundary, value) => {
    const currentStart = Number(card.dataset.scopeStart ?? cutStart);
    const currentEnd = Number(card.dataset.scopeEnd ?? cutEnd);
    if (boundary === "start") {
      updateSelection(Math.max(cutStart, Math.min(currentEnd - boundaryStep, value)), currentEnd);
    } else {
      updateSelection(currentStart, Math.min(cutEnd, Math.max(currentStart + boundaryStep, value)));
    }
  };
  const pointerBoundaryValue = clientX => {
    const axis = plot?._fullLayout?.xaxis;
    if (!axis || !axis._length) return null;
    const bounds = plot.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - bounds.left - axis._offset) / axis._length));
    const rangeStart = Number(axis.range?.[0]);
    const rangeEnd = Number(axis.range?.[1]);
    if (!Number.isFinite(rangeStart) || !Number.isFinite(rangeEnd)) return null;
    return rangeStart + ratio * (rangeEnd - rangeStart);
  };
  $$('[data-event-boundary]', eventHandleLayer).forEach(handle => {
    let dragFrame = 0;
    let pendingValue = null;
    const flushDrag = () => {
      dragFrame = 0;
      if (pendingValue === null) return;
      const value = pendingValue;
      pendingValue = null;
      applyBoundaryValue(handle.dataset.eventBoundary, value);
    };
    handle.addEventListener("pointerdown", event => {
      if (card.dataset.eventActive !== "true") return;
      event.preventDefault();
      event.stopPropagation();
      handle.focus({ preventScroll: true });
      handle.classList.add("is-dragging");
      handle.setPointerCapture?.(event.pointerId);
    });
    handle.addEventListener("pointermove", event => {
      if (!handle.hasPointerCapture?.(event.pointerId)) return;
      event.preventDefault();
      pendingValue = pointerBoundaryValue(event.clientX);
      if (pendingValue === null || dragFrame) return;
      dragFrame = requestAnimationFrame(flushDrag);
    });
    const finishDrag = event => {
      if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture?.(event.pointerId);
      handle.classList.remove("is-dragging");
      if (dragFrame) cancelAnimationFrame(dragFrame);
      dragFrame = 0;
      flushDrag();
    };
    handle.addEventListener("pointerup", finishDrag);
    handle.addEventListener("pointercancel", finishDrag);
    handle.addEventListener("lostpointercapture", finishDrag);
    handle.addEventListener("keydown", event => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const keyboardStep = event.shiftKey ? 0.01 : 0.001;
      const current = Number(handle.dataset.eventBoundary === "start" ? card.dataset.scopeStart : card.dataset.scopeEnd);
      applyBoundaryValue(handle.dataset.eventBoundary, current + direction * keyboardStep);
    });
  });
  const rangeHandle = $("[data-event-range-move]", eventHandleLayer);
  let rangeDrag = null;
  let rangeDragFrame = 0;
  let pendingRangeClientX = null;
  const applyRangeOffset = clientX => {
    if (!rangeDrag) return;
    const delta = (clientX - rangeDrag.clientX) * rangeDrag.unitsPerPixel;
    const width = rangeDrag.end - rangeDrag.start;
    const nextStart = Math.max(cutStart, Math.min(cutEnd - width, rangeDrag.start + delta));
    updateSelection(nextStart, nextStart + width);
  };
  const flushRangeDrag = () => {
    rangeDragFrame = 0;
    if (pendingRangeClientX === null) return;
    const clientX = pendingRangeClientX;
    pendingRangeClientX = null;
    applyRangeOffset(clientX);
  };
  rangeHandle.addEventListener("pointerdown", event => {
    if (card.dataset.eventActive !== "true" || (event.pointerType === "mouse" && event.button !== 0)) return;
    const axis = plot?._fullLayout?.xaxis;
    const rangeStart = Number(axis?.range?.[0]);
    const rangeEnd = Number(axis?.range?.[1]);
    if (!axis?._length || !Number.isFinite(rangeStart) || !Number.isFinite(rangeEnd)) return;
    event.preventDefault();
    event.stopPropagation();
    rangeDrag = {
      clientX: event.clientX,
      start: Number(card.dataset.scopeStart),
      end: Number(card.dataset.scopeEnd),
      unitsPerPixel: (rangeEnd - rangeStart) / axis._length,
    };
    rangeHandle.focus({ preventScroll: true });
    rangeHandle.classList.add("is-dragging");
    rangeHandle.setPointerCapture?.(event.pointerId);
  });
  rangeHandle.addEventListener("pointermove", event => {
    if (!rangeDrag || !rangeHandle.hasPointerCapture?.(event.pointerId)) return;
    event.preventDefault();
    event.stopPropagation();
    pendingRangeClientX = event.clientX;
    if (!rangeDragFrame) rangeDragFrame = requestAnimationFrame(flushRangeDrag);
  });
  const finishRangeDrag = event => {
    if (!rangeDrag) return;
    event.preventDefault?.();
    event.stopPropagation?.();
    if (rangeDragFrame) cancelAnimationFrame(rangeDragFrame);
    rangeDragFrame = 0;
    flushRangeDrag();
    rangeDrag = null;
    rangeHandle.classList.remove("is-dragging");
    if (rangeHandle.hasPointerCapture?.(event.pointerId)) rangeHandle.releasePointerCapture?.(event.pointerId);
  };
  rangeHandle.addEventListener("pointerup", finishRangeDrag);
  rangeHandle.addEventListener("pointercancel", finishRangeDrag);
  rangeHandle.addEventListener("lostpointercapture", finishRangeDrag);
  rangeHandle.addEventListener("keydown", event => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const step = event.shiftKey ? 0.01 : 0.001;
    const start = Number(card.dataset.scopeStart);
    const end = Number(card.dataset.scopeEnd);
    const width = end - start;
    const nextStart = Math.max(cutStart, Math.min(cutEnd - width, start + direction * step));
    updateSelection(nextStart, nextStart + width);
  });
  const commitSelectionInputs = () => {
    const applyIfChanged = (start, end) => {
      if (Math.abs(Number(card.dataset.scopeStart) - start) < 1e-9 && Math.abs(Number(card.dataset.scopeEnd) - end) < 1e-9) return true;
      updateSelection(start, end);
      return true;
    };
    if (card.dataset.relabeling === "true") {
      const item = activeLabelEvent(card);
      if (!item || item.status !== "saved") return false;
      return applyIfChanged(item.committedStart, item.committedEnd);
    }
    if (activeLabelEvent(card)?.scopeKind === "whole") {
      return applyIfChanged(cutStart, cutEnd);
    }
    const start = Number(startInput.value);
    const selectedDuration = Number(durationInput.value);
    if (!startInput.value.trim() || !durationInput.value.trim() || !Number.isFinite(start) || !Number.isFinite(selectedDuration) || selectedDuration <= 0 || start < cutStart || start + selectedDuration > cutEnd + 1e-9) return false;
    return applyIfChanged(start, start + selectedDuration);
  };
  plot._commitSelectionInputs = commitSelectionInputs;
  startInput.onchange = commitSelectionInputs;
  durationInput.onchange = commitSelectionInputs;
  modeButtons.forEach(button => button.onclick = async () => {
    ensureLabelAudioGain(eventAudio);
    if (button.dataset.playbackMode === "event" && card.dataset.eventActive !== "true") return;
    if (button.dataset.playbackMode === "event" && !commitSelectionInputs()) return notify("请填写有效的事件开始时间和持续时间", "error");
    if (!eventAudio.paused && eventAudio._playbackMode === button.dataset.playbackMode) {
      eventAudio._playRequestId = Number(eventAudio._playRequestId || 0) + 1;
      eventAudio._pauseReason = "已暂停";
      return eventAudio.pause();
    }
    const mode = button.dataset.playbackMode;
    if (eventAudio.paused && eventAudio._seekReady && eventAudio._loadedPlaybackMode === mode) {
      if (eventAudio.currentTime >= Number(eventAudio._playEndLocal || 0)) eventAudio.currentTime = Number(eventAudio._playStartLocal || 0);
      try { stopOtherLabelAudio(eventAudio); await eventAudio.play(); }
      catch (error) { notify(`音频播放失败：${error.message}`, "error"); }
      return;
    }
    setPlaybackMode(mode);
    const playRequestId = eventAudio._playRequestId;
    if (Number(eventAudio._playEndLocal) <= Number(eventAudio._playStartLocal)) return notify("当前播放范围无有效音频", "error");
    try {
      stopOtherLabelAudio(eventAudio);
      eventAudio._statusKind = "正在载入音频";
      refreshPlaybackStatus();
      const sourceUrl = button.dataset.playbackMode === "event"
        ? `${eventAudio.dataset.audioSrc}&start_s=${encodeURIComponent(card.dataset.scopeStart)}&end_s=${encodeURIComponent(card.dataset.scopeEnd)}`
        : eventAudio.dataset.audioSrc;
      await loadPlaybackAudio(sourceUrl, button.dataset.playbackMode === "event"
        ? { start: Number(card.dataset.scopeStart), end: Number(card.dataset.scopeEnd) }
        : null);
      if (eventAudio._playRequestId !== playRequestId || eventAudio._disposed || !eventAudio.isConnected) return;
      eventAudio.defaultPlaybackRate = state.labelingPlaybackRate;
      eventAudio.playbackRate = state.labelingPlaybackRate;
      eventAudio.currentTime = Number(eventAudio._playStartLocal);
      await eventAudio.play();
    } catch (error) {
      if (eventAudio._playRequestId !== playRequestId) return;
      if (error.name !== "AbortError") {
        eventAudio._statusKind = "播放失败";
        refreshPlaybackStatus();
        notify(`音频播放失败：${error.message}`, "error");
      }
    }
  });
  const finalizePlayback = () => {
    const playEndLocal = Number(eventAudio._playEndLocal || 0);
    if (!Number.isFinite(playEndLocal)) return;
    if (eventAudio._statusKind === "播放完成" && eventAudio.paused) return;
    eventAudio._pauseReason = "播放完成";
    eventAudio.pause();
    eventAudio.currentTime = playEndLocal;
    eventAudio._statusKind = "播放完成";
    refreshPlaybackStatus();
    syncPlayheadGeometry(Number(eventAudio._playOrigin || cutStart) + playEndLocal);
  };
  eventAudio.ontimeupdate = () => {
    refreshPlaybackStatus();
    if (!eventAudio.paused && eventAudio.currentTime >= Number(eventAudio._playEndLocal || 0)) finalizePlayback();
  };
  eventAudio.onended = finalizePlayback;
  let playheadFrame = 0;
  const updatePlayhead = () => {
    if (eventAudio.paused) return;
    const playEndLocal = Number(eventAudio._playEndLocal || 0);
    if (eventAudio.currentTime >= playEndLocal) {
      finalizePlayback();
      return;
    }
    const originalTime = Math.max(cutStart, Math.min(cutEnd, Number(eventAudio._playOrigin || cutStart) + eventAudio.currentTime));
    syncPlayheadGeometry(originalTime);
    playheadFrame = requestAnimationFrame(updatePlayhead);
  };
  eventAudio.onplay = () => {
    eventAudio.defaultPlaybackRate = state.labelingPlaybackRate;
    eventAudio.playbackRate = state.labelingPlaybackRate;
    eventAudio._statusKind = eventAudio._playbackMode === "event" ? "播放当前事件" : `播放${allSegmentName}`;
    modeButtons.forEach(button => {
      if (button.dataset.playbackMode === eventAudio._playbackMode) button.textContent = button.dataset.playbackMode === "event" ? "❚❚ 暂停当前事件" : `❚❚ 暂停${allSegmentName}`;
    });
    refreshPlaybackStatus();
    cancelAnimationFrame(playheadFrame);
    updatePlayhead();
  };
  eventAudio.onpause = () => {
    cancelAnimationFrame(playheadFrame);
    eventAudio._statusKind = eventAudio._pauseReason || eventAudio._statusKind || "已暂停";
    delete eventAudio._pauseReason;
    modeButtons.forEach(button => { button.textContent = button.dataset.playbackMode === "event" ? "▶ 播放当前事件" : `▶ 播放${allSegmentName}`; });
    refreshPlaybackStatus();
  };
  setPlaybackMode("all", false);
  if (card.dataset.eventActive === "true" && activeLabelEvent(card)) {
    setPlaybackMode("event", false);
    $('[data-playback-mode="event"]', card).disabled = false;
  }
  setLabelPlaybackRate(state.labelingPlaybackRate);
  $('[data-playback-mode="all"]', card).disabled = false;
  $("[data-new-event]", card).disabled = false;
  $("[data-label-whole]", card).disabled = false;
  $$('[data-event-sample-id]', card).forEach(block => { block.disabled = false; });
  card.dataset.signalReady = "true";
  scheduleLabelSignalResize();
}

function renderAnalysisTargets() {
  const file = state.labelingAnalysisFile || state.labelingFile;
  const samples = file?.samples || [];
  $("#analysisTargets").innerHTML = `<strong>分析对象</strong>${samples.map(sample => `<button class="button ${sample.sample_id === state.labelingAnalysisSample ? "button-primary" : "button-ghost"}" data-analysis-sample="${escapeHtml(sample.sample_id)}" type="button">${escapeHtml(sample.display_name || sample.sample_id)}</button>`).join("")}`;
  const sample = samples.find(item => item.sample_id === state.labelingAnalysisSample);
  $("#analysisCurrentSample").textContent = sample ? `${file.relative_path} · ${sample.display_name || sample.sample_id}` : "当前无可分析通道";
}

function openLabelAnalysis(sampleId, file = state.labelingFile) {
  state.labelingAnalysisFile = file;
  state.labelingAnalysisSample = sampleId || state.labelingAnalysisSample || file?.samples?.[0]?.sample_id || "";
  renderAnalysisTargets();
  $("#analysisTabs").innerHTML = state.labelingAnalysisCards.map(card => `<button class="button button-secondary" data-analysis-kind="${escapeHtml(card.id)}" type="button">${escapeHtml(card.title)}</button>`).join("");
  $("#labelAnalysisMask").hidden = false;
  requestAnimationFrame(() => $$('[data-analysis-result] .analysis-plot').forEach(host => window.Plotly?.Plots.resize(host)));
}

function releaseAnalysisResults() {
  const results = $("#analysisResults");
  $$('[data-analysis-result]', results).forEach(card => {
    card._analysisAbortController?.abort();
    window.Plotly?.purge?.($(".analysis-plot", card));
  });
  results.innerHTML = '<div class="analysis-empty">选择上方分析方法后显示结果。</div>';
  $("#applyAnalysisParametersButton").disabled = false;
}

function releaseLabelWorkspace() {
  ++labelWaveformLoadToken;
  labelWaveformLoadTask = null;
  const host = $("#labelChannelsRow");
  $$('[data-event-audio]', host).forEach(disposeLabelAudio);
  $$('[data-signal-plot]', host).forEach(plot => window.Plotly?.purge?.(plot));
  $$('[data-label-sample]', host).forEach(card => { delete card._labelSignal; });
  host.replaceChildren();
  releaseAnalysisResults();
  $("#labelAnalysisMask").hidden = true;
  state.labelingAnalysisFile = null;
  state.labelingAnalysisSample = "";
  state.labelingFiles = [];
  state.labelingFile = null;
}

function renderAnalysisParameters(spec) {
  const panel = $("#analysisParameterPanel");
  panel.hidden = false;
  $("#analysisParameterFields").innerHTML = (spec.params || []).map(parameter => {
    const attributes = `data-analysis-param="${escapeHtml(parameter.key)}"`;
    const control = parameter.type === "select"
      ? `<select ${attributes}>${(parameter.options || []).map(option => `<option value="${escapeHtml(option)}" ${option === parameter.default ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select>`
      : `<input ${attributes} type="number" value="${escapeHtml(parameter.default)}" min="${escapeHtml(parameter.min)}" max="${escapeHtml(parameter.max)}" step="${escapeHtml(parameter.step)}">`;
    return `<label><span>${escapeHtml(parameter.label)}</span>${control}</label>`;
  }).join("");
}

async function renderAdvancedAnalysis(kind, resetParameters = true) {
  const file = state.labelingAnalysisFile || state.labelingFile;
  if (!file || !window.Plotly) return notify("当前通道分析数据尚未载入", "error");
  const channelCard = $$('[data-label-sample]', $("#labelChannelsRow")).find(item =>
    item.dataset.labelFile === file.absolute_path && item.dataset.labelSample === state.labelingAnalysisSample);
  const signal = channelCard?._labelSignal;
  const data = signal?.cut_values || signal?.values;
  if ((!Array.isArray(data) && !ArrayBuffer.isView(data)) || !data.length || !Number(signal?.sampling_rate_hz)) {
    return notify("当前通道的完整信号尚未载入", "error");
  }
  const spec = state.labelingAnalysisCards.find(card => card.id === kind);
  if (!spec) return;
  state.labelingAnalysisKind = kind;
  if (resetParameters) renderAnalysisParameters(spec);
  const params = {};
  $$("[data-analysis-param]", $("#analysisParameterFields")).forEach(field => { params[field.dataset.analysisParam] = field.type === "number" ? Number(field.value) : field.value; });
  const resultKey = `${file.absolute_path}:${state.labelingAnalysisSample}:${kind}`;
  const sample = (file.samples || []).find(item => item.sample_id === state.labelingAnalysisSample);
  const title = `${sample?.display_name || state.labelingAnalysisSample} · ${spec.title}`;
  const results = $("#analysisResults");
  results.querySelector(":scope > .analysis-empty")?.remove();
  let card = $$('[data-analysis-result]', results).find(item => item.dataset.analysisResult === resultKey);
  if (!card) {
    card = document.createElement("section");
    card.className = "label-analysis-card analysis-result-card";
    card.dataset.analysisResult = resultKey;
    card.innerHTML = `<div class="analysis-result-head"><h4>${escapeHtml(title)} · 计算中…</h4><button class="button button-ghost" data-close-analysis-result type="button" aria-label="关闭分析卡片">×</button></div><div class="analysis-plot"><div class="analysis-empty">正在计算，请稍候…</div></div>`;
    results.prepend(card);
  }
  card._analysisAbortController?.abort();
  const analysisAbortController = new AbortController();
  card._analysisAbortController = analysisAbortController;
  const heading = $("h4", card);
  const host = $(".analysis-plot", card);
  const startButton = $("#applyAnalysisParametersButton");
  startButton.disabled = true;
  heading.textContent = `${title} · 计算中…`;
  host.innerHTML = '<div class="analysis-empty">正在计算，请稍候…</div>';
  $$("[data-analysis-kind]").forEach(button => button.classList.toggle("button-primary", button.dataset.analysisKind === kind));
  try {
    const result = await request(API.labelingAnalysis, { method: "POST", signal: analysisAbortController.signal, cache: "no-store", body: { data: Array.from(data), sampling_rate_hz: signal.sampling_rate_hz, sample_id: state.labelingAnalysisSample, card_id: kind, params } });
    if (!card.isConnected || card._analysisAbortController !== analysisAbortController) return;
    heading.textContent = title;
    host.innerHTML = "";
    const traces = (result.figure?.data || []).map(trace => (
      trace.type === "heatmap" && kind !== "mfcc" ? { ...trace, colorscale: AI2_SPECTRUM_COLORS } : trace
    ));
    const plotHeights = { dwt: 780, pcen: 580, mfcc: 640, mel_detail: 640, wavelet: 740, emd: 840 };
    Plotly.react(host, traces, { ...(result.figure?.layout || {}), autosize: true, height: Math.max(Number(result.figure?.layout?.height || 0), plotHeights[kind] || 620) }, { responsive: true, displaylogo: false });
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (!card.isConnected || error.name === "AbortError") return;
    heading.textContent = title;
    host.innerHTML = `<div class="analysis-empty error-text">分析失败：${escapeHtml(error.message)}</div>`;
  } finally {
    if (card._analysisAbortController === analysisAbortController) startButton.disabled = false;
  }
}

async function loadLabelWaveform(sample, file, generation, loadToken = null) {
  if (!file || !sample) return;
  const card = $$("[data-label-sample]", $("#labelChannelsRow")).find(item => item.dataset.labelSample === sample.sample_id && item.dataset.labelFile === file.absolute_path);
  if (!card) return;
  const hint = $("[data-waveform-hint]", card);
  const isCurrent = () => generation === state.labelingResolveGeneration
    && (loadToken === null || loadToken === labelWaveformLoadToken)
    && state.labelingFiles.includes(file)
    && card.isConnected
    && !$("#labelTabAnnotation").hidden;
  try {
    const payload = await request(API.labelingWaveform(file.absolute_path, sample.sample_id, $("#labelLineSelect").value, file.metadata?.wav_profile || "", state.labelingLowFrequencyFilter), { cache: "no-store" });
    if (!isCurrent()) return;
    for (const key of ["values", "cut_values", "highpass_20hz_values"]) {
      if (Array.isArray(payload[key])) payload[key] = Float32Array.from(payload[key]);
    }
    card._labelSignal = payload;
    renderDefaultSignalPlots(card, payload);
    hint.textContent = `${payload.group_name} / ${payload.channel_name} · ${formatNumber(payload.sampling_rate_hz || 0)} Hz · ${formatNumber(payload.duration_s || 0)} s`;
    return true;
  } catch (error) {
    if (isCurrent()) hint.textContent = `波形读取失败：${error.message}`;
    return false;
  }
}

async function resolveLabelTdms(button, addToQueue = true) {
  if (state.labelingConfigLoading) return notify("标签配置正在载入，请稍候", "error");
  if (state.labelingPickerPending) return notify("请先完成当前文件选择", "error");
  if (state.labelingQueueLoading) return notify("当前文件正在载入，请稍候", "error");
  const path = $("#labelTdmsPath").value.trim();
  if (!state.labelTaxonomy.path) return notify("请先选择标签类别 JSON", "error");
  const queuedProfile = state.labelingQueue[state.labelingQueueIndex]?.wavProfile;
  const isTdms = !queuedProfile && $("#labelFileType").value === "tdms";
  const isWav = path.toLowerCase().endsWith(".wav");
  const line = isTdms ? $("#labelLineSelect").value : "";
  if (!path) return notify("请选择或输入文件路径", "error");
  if (isTdms && !line) return notify("请选择产线", "error");
  if (isWav && !queuedProfile && !selectedLabelWavProfile()) return notify("请选择 WAV 类型：电机或滑轨", "error");
  if (!discardPendingLabelEventsForFileChange()) return;
  const generation = ++state.labelingResolveGeneration;
  releaseLabelWorkspace();
  state.labelingQueueLoading = true;
  setLabelWorkspaceLoading();
  button._labelResolveGeneration = generation;
  if (addToQueue) {
    state.labelingSessionRequest = null;
    state.labelingQueue = [{ path, labeled: false, loaded: false, wavProfile: selectedLabelWavProfile() }];
    state.labelingAllQueue = state.labelingQueue;
    state.labelingQueueIndex = 0;
    renderLabelQueue();
  }
  const targetQueueItem = state.labelingQueue[state.labelingQueueIndex];
  if (targetQueueItem) {
    targetQueueItem.loadGeneration = generation;
    targetQueueItem.loading = true;
    targetQueueItem.loaded = false;
    delete targetQueueItem.loadError;
    renderLabelQueue();
  }
  setBusy(button, true, "载入中…");
  try {
    if (addToQueue) {
      state.labelingSessionPath = "";
      state.labelingOutputDirectory = path.split("/").slice(0, -1).join("/");
      $("#labelSessionJsonPath").textContent = state.labelingHistoryPath || "开始标注时自动生成";
    }
    const paths = targetQueueItem?.paths || [path];
    const payloads = [];
    for (const sourcePath of paths) {
      const payload = await request(API.labelingResolve, { method: "POST", body: { path: sourcePath, line, wav_profile: targetQueueItem?.wavProfile || null, session_path: state.labelingSessionPath || state.labelingHistoryPath || null, taxonomy_path: state.labelTaxonomy.path || null } });
      if (generation !== state.labelingResolveGeneration) return;
      payloads.push(payload);
    }
    const analysis = await request(API.labelingAnalysisCards);
    if (generation !== state.labelingResolveGeneration) return;
    state.labelingFiles = payloads;
    state.labelingFile = payloads[0];
    state.labelingAnalysisCards = asArray(analysis, ["cards"]);
    $("#labelCurrentFile").textContent = payloads.map(file => file.absolute_path).join("\n");
    $("#labelFileCondition").textContent = payloads[0].conditions?.line || (targetQueueItem?.wavProfile ? "WAV" : "未填写产线");
    if (state.labelingSessionPath) $("#labelSessionJsonPath").textContent = payloads[0].sidecar_path || "—";
    $("#singleLabelWorkspace").hidden = false;
    $("#labelAnnotationEmpty").hidden = true;
    renderLabelChannels();
    const current = state.labelingQueue[state.labelingQueueIndex];
    if (current) {
      delete current.loadGeneration;
      current.loading = false;
      current.loaded = true;
      current.loadedFilePath = payloads[0].absolute_path;
      current.paths = payloads.map(file => file.absolute_path);
      current.path = payloads[0].absolute_path;
      delete current.loadError;
      current.samples = payloads.flatMap(file => file.samples || []);
      current.annotations = payloads.flatMap(file => file.annotations || []);
      const labeledFiles = payloads.filter(file => (file.annotations || []).some(sample => (sample.label_events || []).length > 0)).length;
      current.labeled = labeledFiles === payloads.length;
      current.partialLabeled = labeledFiles > 0 && labeledFiles < payloads.length;
    }
    $("#labelTdmsPath").value = payloads[0].absolute_path;
    if (addToQueue) state.labelingOutputDirectory = payloads[0].absolute_path.split("/").slice(0, -1).join("/");
    renderLabelQueue();
    enableLabelTaskTabs();
    if (addToQueue) switchLabelWorkflowTab("files");
  } catch (error) {
    if (generation !== state.labelingResolveGeneration) return;
    releaseLabelWorkspace();
    if (targetQueueItem?.wavProfile && error.message === "请求失败（422）") {
      error = new Error("当前 8030 后端仍是旧版，不支持 WAV 类型参数；请重启 AI-3.0 服务后重试");
    }
    state.labelingFile = null;
    state.labelingFiles = [];
    if (targetQueueItem) {
      delete targetQueueItem.loadGeneration;
      targetQueueItem.loading = false;
      targetQueueItem.loaded = false;
      targetQueueItem.loadError = error.message;
      renderLabelQueue();
    }
    $("#singleLabelWorkspace").hidden = true;
    $("#labelAnnotationEmpty").hidden = false;
    notify(error.message, "error", true);
  } finally {
    if (targetQueueItem?.loadGeneration === generation) {
      delete targetQueueItem.loadGeneration;
      targetQueueItem.loading = false;
      renderLabelQueue();
    }
    if (generation === state.labelingResolveGeneration) {
      state.labelingQueueLoading = false;
      setLabelWorkspaceLoading();
      renderLabelQueue();
    }
    if (button._labelResolveGeneration === generation) setBusy(button, false);
  }
}

async function saveSingleLabel(event, card) {
  event.preventDefault();
  if (state.labelingPickerPending || state.labelingQueueLoading || state.labelingConfigLoading || !labelFileForCard(card)) {
    return notify("当前文件正在切换，请等待载入完成", "error");
  }
  if (card.dataset.eventSavePending === "true") return notify("当前事件正在保存，请稍候", "error");
  if (!$('[data-signal-plot]', card)?._commitSelectionInputs?.()) return notify("请填写有效的事件开始时间和持续时间", "error");
  captureActiveLabelEventForm(card);
  const editor = ensureLabelEventEditor(card);
  const savingEventKey = editor.activeKey;
  const savingItem = activeLabelEvent(card);
  const file = labelFileForCard(card);
  const generation = state.labelingResolveGeneration;
  const queueItem = state.labelingQueue[state.labelingQueueIndex];
  const sample = file?.samples?.find(item => item.sample_id === card.dataset.labelSample);
  const identity = $("#labelSourceIdentity").value.trim();
  if (!file || !sample) return notify("请先载入文件", "error");
  if (!savingItem) return notify("请先新建或选择一个事件", "error");
  const savingForm = { ...savingItem.form };
  const sourceType = savingForm.sourceTypeOverride || $("#labelSourceType").value;
  const source = identity ? `${sourceType}_${identity}` : sourceType;
  const button = $("[data-save-label]", card);
  const confidence = Number(savingForm.confidence ?? 0.9);
  const startS = Number(savingItem.start);
  const endS = Number(savingItem.end);
  const resultKey = savingForm.resultKey;
  const reasonKey = savingForm.reasonKey;
  const note = savingForm.note;
  const prototype = Boolean(savingForm.prototype);
  const taxonomyPath = state.labelTaxonomy.path || null;
  if (!Number.isFinite(startS) || !Number.isFinite(endS) || endS <= startS) return notify("请先用两条标注线选择有效事件范围", "error");
  const annotations = file.annotations || [];
  const isCurrent = () => generation === state.labelingResolveGeneration
    && state.labelingFiles.includes(file)
    && card.isConnected;
  const prefix = `${sample.sample_id}_`;
  const nextNumber = Math.max(0, ...annotations
    .map(item => String(item.sample_id || ""))
    .filter(id => id.startsWith(prefix))
    .map(id => Number(id.slice(prefix.length)))
    .filter(Number.isFinite)) + 1;
  const eventSampleId = savingItem.sampleId || (savingItem.scopeKind === "whole" ? `${sample.sample_id}_whole` : `${sample.sample_id}_${String(nextNumber).padStart(3, "0")}`);
  setLabelEventSavePending(card, true);
  setBusy(button, true, "保存中…");
  try {
    const sessionPath = state.labelingSessionPath;
    if (!sessionPath) throw new Error("请先点击“开始标注”创建标注会话");
    if (card.dataset.relabeling === "true") await requireLabelEditBackend();
    const saved = await request(API.labelingSave, {
      method: "POST",
      body: {
        path: file.absolute_path,
        line: $("#labelLineSelect").value,
        wav_profile: file.metadata?.wav_profile === "motor" || file.metadata?.wav_profile === "rail" ? file.metadata.wav_profile : null,
        session_path: sessionPath,
        taxonomy_path: taxonomyPath,
        source_sample_id: sample.sample_id,
        sample_id: eventSampleId,
        ...(card.dataset.relabeling === "true" && card.dataset.editEventUuid
          ? { target_event_uuid: card.dataset.editEventUuid } : {}),
        sample_scope: { start_s: startS, end_s: endS },
        scope_kind: savingItem.scopeKind || "event",
        source,
        result_key: resultKey,
        result_confidence: confidence,
        reason_key: reasonKey,
        reason_confidence: confidence,
        note,
        prototype,
      },
    });
    file.annotations = annotations;
    sample.label_events = sample.label_events || [];
    const samplePosition = saved.replaced_event_uuid ? sample.label_events.findIndex(item => item.event_uuid === saved.replaced_event_uuid) : -1;
    if (samplePosition >= 0) sample.label_events.splice(samplePosition, 1);
    sample.label_events.push(saved);
    const existingAnnotation = annotations.find(item => item.sample_id === saved.sample_id);
    if (existingAnnotation) {
      existingAnnotation.sample_scope = saved.sample_scope;
      existingAnnotation.label_events = existingAnnotation.label_events || [];
      const annotationPosition = saved.replaced_event_uuid ? existingAnnotation.label_events.findIndex(item => item.event_uuid === saved.replaced_event_uuid) : -1;
      if (annotationPosition >= 0) existingAnnotation.label_events.splice(annotationPosition, 1);
      existingAnnotation.label_events.push(saved);
    } else {
      annotations.push({ sample_id: saved.sample_id, sample_scope: saved.sample_scope, label_events: [saved] });
    }
    const committedItem = commitLabelEventItem(card, savingEventKey, saved, savingForm);
    card.dataset.relabeling = "false";
    delete card.dataset.editEventUuid;
    if (editor.activeKey === committedItem?.key) {
      $("[data-scope-start]", card).value = String(committedItem.start);
      $("[data-scope-duration]", card).value = (committedItem.end - committedItem.start).toFixed(3);
    }
    if (queueItem && state.labelingQueue.includes(queueItem) && (queueItem.paths || [queueItem.path]).includes(file.absolute_path)) {
      queueItem.labeled = state.labelingFiles.every(item => (item.annotations || []).some(annotation => (annotation.label_events || []).length > 0));
      queueItem.partialLabeled = !queueItem.labeled;
      queueItem.samples = state.labelingFiles.flatMap(item => item.samples || []);
      queueItem.annotations = state.labelingFiles.flatMap(item => item.annotations || []);
      renderLabelQueue();
    }
    if (!isCurrent()) return;
    state.labelingSessionPath = saved.sidecar_path;
    $("#labelSessionJsonPath").textContent = saved.sidecar_path;
    if (editor.activeKey === committedItem?.key) card.dataset.eventSampleId = saved.sample_id;
    const previousAudio = $("[data-event-audio]", card);
    disposeLabelAudio(previousAudio);
    const playbackControls = $(".label-audio-track-controls", card);
    if (playbackControls) playbackControls.outerHTML = labelPlaybackControlsMarkup(API.labelingAudio(file.absolute_path, sample.sample_id, $("#labelLineSelect").value, file.metadata?.wav_profile || "", state.labelingLowFrequencyFilter), file);
    const track = $(".label-audio-track", card);
    if (track) track.outerHTML = labelEventTrackMarkup(sample.sample_id, Number(card._labelSignal?.duration_s || sample.duration_s || 0), file);
    if (card._labelSignal) {
      renderDefaultSignalPlots(card, card._labelSignal);
      const activeItem = activeLabelEvent(card);
      if (activeItem) $("[data-signal-plot]", card)._selectLabelEventItem(activeItem.key, false);
    }
    $(".label-channel-history", card).innerHTML = labelHistoryMarkup(sample, file);
    $(".label-channel-history-title span", card).textContent = `${channelLabelEventCount(sample.sample_id, file)} 条`;
    syncLabelHistorySidebar();
    renderLabelEventSwitcher(card);
    const savedItemStillActive = activeLabelEvent(card)?.sampleId === saved.sample_id;
    selectLabelEvent(card, activeLabelEvent(card)?.sampleId || "");
    if (savedItemStillActive) {
      $(".label-channel-form", card).hidden = true;
    }
    const feedback = $("[data-label-save-feedback]", card);
    feedback.hidden = false;
    window.clearTimeout(feedback._hideTimer);
    feedback._hideTimer = window.setTimeout(() => { feedback.hidden = true; }, 2200);
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setLabelEventSavePending(card, false);
    setBusy(button, false);
    if (card.dataset.relabeling !== "true") button.textContent = "保存标注";
  }
}

async function requireLabelEditBackend() {
  try {
    const capability = await request(API.labelingEditCapability);
    if (capability.annotator_upsert === true) return;
  } catch (_) { /* The running service may still be the old version. */ }
  throw new Error("当前 8030 服务尚未加载新的标注保存规则，请重启 AI-3.0 服务后再编辑或确认");
}

async function confirmSavedLabelEvent(card, button) {
  const file = labelFileForCard(card);
  const sampleId = button.dataset.eventSample;
  const annotation = (file?.annotations || []).find(item => item.sample_id === sampleId);
  const selected = annotation?.label_events?.find(item => item.event_uuid === button.dataset.confirmLabelEvent);
  const sourceSample = file?.samples?.find(item => item.sample_id === card.dataset.labelSample);
  if (!file || !sourceSample || !selected) return notify("未找到这条已保存的标注", "error");
  if (guardPendingLabelEvents()) return;
  setLabelEventSavePending(card, true);
  setBusy(button, true, "确认中…");
  try {
    await requireLabelEditBackend();
    const saved = await request(API.labelingSave, {
      method: "POST",
      body: {
        path: file.absolute_path,
        line: $("#labelLineSelect").value,
        wav_profile: file.metadata?.wav_profile === "motor" || file.metadata?.wav_profile === "rail" ? file.metadata.wav_profile : null,
        session_path: state.labelingSessionPath,
        taxonomy_path: state.labelTaxonomy.path,
        source_sample_id: sourceSample.sample_id,
        sample_id: sampleId,
        target_event_uuid: selected.event_uuid,
        sample_scope: annotation.sample_scope,
        scope_kind: selected.scope_kind || (sampleId.endsWith("_whole") ? "whole" : "event"),
        source: currentLabelSource(),
        result_key: selected.result_key,
        result_confidence: selected.result_confidence,
        reason_key: selected.reason_key,
        reason_confidence: selected.reason_confidence,
        note: selected.note || "",
        prototype: Boolean(file.prototype),
      },
    });
    setLabelEventSavePending(card, false);
    await loadLabelQueueIndex(state.labelingQueueIndex, { force: true });
    notify(saved.replaced_event_uuid ? "已更新当前标注员的记录" : "已新增当前标注员的确认记录");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setLabelEventSavePending(card, false);
    setBusy(button, false);
  }
}

async function deleteSavedLabelEvent(card, button) {
  const file = labelFileForCard(card);
  const sampleId = button.dataset.eventSample;
  const eventUuid = button.dataset.deleteLabelEvent;
  const annotation = (file?.annotations || []).find(item => item.sample_id === sampleId);
  if (!file || !eventUuid || !annotation?.label_events?.some(item => item.event_uuid === eventUuid)) {
    return notify("未找到这条已保存的标注", "error");
  }
  if (!window.confirm(`删除 ${sampleId} 的这条标注？此操作会更新标注 JSON。`)) return;
  setLabelEventSavePending(card, true);
  setBusy(button, true, "删除中…");
  try {
    const result = await request(API.labelingDelete, {
      method: "DELETE",
      body: { path: file.absolute_path, session_path: state.labelingSessionPath,
        sample_id: sampleId, event_uuid: eventUuid },
    });
    annotation.label_events = annotation.label_events.filter(item => item.event_uuid !== eventUuid);
    if (!annotation.label_events.length) file.annotations = file.annotations.filter(item => item !== annotation);
    const sourceSample = (file.samples || []).find(item => item.sample_id === card.dataset.labelSample);
    if (sourceSample) sourceSample.label_events = (sourceSample.label_events || []).filter(item => item.event_uuid !== eventUuid);
    const editor = ensureLabelEventEditor(card);
    const item = labelEventItemForSample(card, sampleId);
    const removedActiveItem = Boolean(item && !annotation.label_events.length && editor.activeKey === item.key);
    if (item && !annotation.label_events.length) {
      editor.items = editor.items.filter(candidate => candidate !== item);
      if (removedActiveItem) editor.activeKey = "";
    } else if (item) {
      item.form = labelEventFormStateFromAnnotation(annotation);
      item.committedForm = { ...item.form };
      item.dirty = false;
      if (editor.activeKey === item.key) restoreLabelEventForm(card, item);
    }
    card.dataset.relabeling = "false";
    $("[data-save-label]", card).textContent = "保存标注";
    $(".label-channel-form", card).hidden = true;
    $(".label-channel-history", card).innerHTML = labelHistoryMarkup(sourceSample, file);
    $(".label-channel-history-title span", card).textContent = `${channelLabelEventCount(card.dataset.labelSample, file)} 条`;
    syncLabelHistorySidebar();
    const track = $(".label-audio-track", card);
    if (track) track.outerHTML = labelEventTrackMarkup(card.dataset.labelSample, Number(card._labelSignal?.duration_s || sourceSample?.duration_s || 0), file);
    renderLabelEventSwitcher(card);
    const plot = $("[data-signal-plot]", card);
    if (removedActiveItem) plot?._clearActiveEventSelection?.();
    else plot?._refreshEventShapes?.();
    const queueItem = state.labelingQueue[state.labelingQueueIndex];
    if (queueItem) {
      const count = state.labelingFiles.filter(item => (item.annotations || []).some(entry => (entry.label_events || []).length)).length;
      queueItem.labeled = count === state.labelingFiles.length;
      queueItem.partialLabeled = count > 0 && !queueItem.labeled;
      renderLabelQueue();
    }
    notify(`已删除标注；该 sample 还剩 ${result.remaining_events} 条`);
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    setLabelEventSavePending(card, false);
    setBusy(button, false);
  }
}

async function selectImportSources(mode, button) {
  const fileType = $("#importSourceFileType").value;
  if (!fileType) return notify("请先选择导入文件类型", "error");
  setBusy(button, true, mode === "folder" ? "选择文件夹…" : "选择文件…");
  try {
    const payload = await request(API.importSelectSources(mode, fileType), { method: "POST" });
    if (payload.cancelled) return;
    const paths = asArray(payload, ["paths", "source_paths"]);
    if (!paths.length) return;
    const field = $("#importSourcePaths");
    const existing = field.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    field.value = [...new Set([...existing, ...paths])].join("\n");
    field.dispatchEvent(new Event("input", { bubbles: true }));
    notify(mode === "folder" ? "已选择原始数据文件夹" : `已选择 ${paths.length} 个原始数据文件`);
  } catch (error) {
    if (!/(取消|cancel)/i.test(error.message)) notify(error.message, "error", true);
  } finally {
    setBusy(button, false);
  }
}

function updateImportSteps() {
  const placementMode = $("#importPlacementMode").value;
  const line = $("#importLineSelect").value;
  $("#importLineStep").hidden = state.importWizardStep !== 1;
  $("#importSourceStep").hidden = state.importWizardStep !== 2;
  $("#importLocationStep").hidden = state.importWizardStep !== 3;
  $("#importConditionStep").hidden = state.importWizardStep !== 4;
  $("#importPreviewStep").hidden = state.importWizardStep !== 5;
  $("#importLineDestinationHint").textContent = line
    ? `${placementMode === "register" ? "文件必须已经位于" : "文件将保存到"}“${line}”产线文件夹内。`
    : "请先选择产线。";
  $$('[data-import-step]').forEach(item => {
    const step = Number(item.dataset.importStep);
    item.classList.toggle("is-active", step === state.importWizardStep);
    item.classList.toggle("is-complete", step < state.importWizardStep);
  });
  if (state.importWizardStep === 5) renderImportReview();
}

function renderImportReview() {
  const paths = $("#importSourcePaths").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const placementMode = $("#importPlacementMode").value;
  const placementLabels = {
    register: "不拷贝，文件已经在数据库文件夹内",
    copy: "拷贝到数据库文件夹",
    move: "移动到数据库文件夹",
  };
  const fileTypeLabels = { tdms: "TDMS / TDMS.ZST", wav: "WAV" };
  const conditions = collectConditions($("#importConditions"), false);
  const conditionCount = Object.entries(conditions)
    .filter(([key, value]) => key !== "extra_fields" && value !== null && value !== "")
    .length + Object.keys(conditions.extra_fields || {}).length;
  $("#importReviewSummary").innerHTML = `
    <div><span>产线</span><strong>${escapeHtml($("#importLineSelect").value || "未选择")}</strong></div>
    <div><span>文件类型</span><strong>${escapeHtml(fileTypeLabels[$("#importSourceFileType").value] || "未选择")}</strong></div>
    <div><span>来源</span><strong>${escapeHtml(paths.length === 1 ? paths[0] : `${paths.length} 个文件或文件夹`)}</strong></div>
    <div><span>导入方式</span><strong>${escapeHtml(placementLabels[placementMode] || "未选择")}</strong></div>
    <div><span>工况与 metadata</span><strong>${conditionCount ? `已填写 ${conditionCount} 项` : "全部留空"}</strong></div>`;
}

function goToImportStep(step) {
  if (step === 2) {
    if (!$("#importLineSelect").value) return notify("请先选择或创建产线", "error");
  }
  if (step === 3) {
    if (!$("#importSourceFileType").value) return notify("请选择导入文件类型", "error");
    const hasSources = $("#importSourcePaths").value.split(/\r?\n/).some(value => value.trim());
    if (!hasSources) return notify("请先选择文件或文件夹", "error");
  }
  if (step === 4) {
    const placementMode = $("#importPlacementMode").value;
    if (!placementMode) return notify("请选择是否拷贝到数据库文件夹内", "error");
  }
  state.importWizardStep = Math.max(1, Math.min(5, step));
  updateImportSteps();
}

function normalizePathOption(item) {
  if (typeof item === "string") return item.trim();
  return String(firstDefined(item, ["relative_path", "path", "value", "name"], "")).trim();
}

function renderReconcilePathOptions() {
  const folderSelect = $("#reconcileFolderPath");
  const currentFolder = folderSelect.value;
  folderSelect.innerHTML = '<option value="">请选择文件夹</option>'
    + state.reconcilePathOptions.folders
      .map(path => `<option value="${escapeHtml(path)}">${escapeHtml(path)}</option>`)
      .join("");
  if (state.reconcilePathOptions.folders.includes(currentFolder)) folderSelect.value = currentFolder;

  $("#reconcileFileOptions").innerHTML = state.reconcilePathOptions.files
    .map(path => {
      const filename = path.split("/").pop() || path;
      return `<option value="${escapeHtml(path)}" label="${escapeHtml(filename)}"></option>`;
    })
    .join("");
}

function setReconcileFileSearchHint(message) {
  const hint = $("#reconcileFileSearchHint");
  if (hint) hint.textContent = message;
}

async function loadReconcilePathOptions({ fileQuery = "", fileLimit = 30 } = {}) {
  const databaseUid = scopedDatabaseUid();
  if (!databaseUid) return null;
  const requestToken = ++state.reconcilePathRequestToken;
  const params = new URLSearchParams();
  params.set("database_uid", databaseUid);
  params.set("file_limit", String(Math.min(Math.max(Number(fileLimit) || 30, 1), 200)));
  if (fileQuery) params.set("file_query", fileQuery);
  const url = params.size ? `${API.filePathOptions}?${params.toString()}` : API.filePathOptions;
  const payload = await request(url);
  if (requestToken !== state.reconcilePathRequestToken || scopedDatabaseUid() !== databaseUid) return null;
  const folders = asArray(payload, ["folders"]).map(normalizePathOption).filter(Boolean);
  const files = asArray(payload, ["files"]).map(normalizePathOption).filter(Boolean);
  state.reconcilePathOptions = {
    folders: [...new Set(folders)].sort((a, b) => a.localeCompare(b, "zh-CN")),
    files: [...new Set(files)].sort((a, b) => a.localeCompare(b, "zh-CN")),
  };
  renderReconcilePathOptions();
  setReconcileFileSearchHint(fileQuery
    ? files.length
      ? `找到 ${files.length} 个候选文件，请从建议中选择。`
      : "没有找到匹配文件，请更换关键词。"
    : "输入至少 2 个字符，可按文件名或相对路径搜索。",
  );
  return state.reconcilePathOptions;
}

function queueReconcileFileSearch() {
  window.clearTimeout(reconcileFileSearchTimer);
  const query = $("#reconcileFilePath").value.trim().replace(/^\.\//, "");
  if (query && query.length < 2) {
    state.reconcilePathRequestToken += 1;
    state.reconcilePathOptions = { ...state.reconcilePathOptions, files: [] };
    renderReconcilePathOptions();
    setReconcileFileSearchHint("请再输入 1 个字符后搜索。");
    return;
  }
  setReconcileFileSearchHint(query ? "正在搜索文件…" : "正在载入最近的文件…");
  reconcileFileSearchTimer = window.setTimeout(() => {
    loadReconcilePathOptions({ fileQuery: query, fileLimit: query ? 200 : 30 })
      .catch(error => {
        if (scopedDatabaseUid()) setReconcileFileSearchHint(`文件搜索失败：${error.message}`);
      });
  }, 200);
}

function updateBusinessEnabled(key) {
  return Boolean(state.updateDialog.allowed?.[key] && state.updateDialog.enabled?.[key]);
}

function renderUpdateReview() {
  const scopeLabels = { selected_files: "所选文件", database: "整个数据库", folder: "指定文件夹", file: "指定文件" };
  const scope = $("#reconcileScope").value;
  const target = scope === "folder" ? $("#reconcileFolderPath").value
    : scope === "file" ? $("#reconcileFilePath").value : scopeLabels[scope];
  const actions = state.reconcile ? selectedReconcileActions().length : 0;
  const changedConditions = collectChangedConditions($("#reconcileConditions"));
  $("#updateReviewSummary").innerHTML = `
    <div><span>更新内容</span><strong>${updateBusinessEnabled("conditions") && Object.keys(changedConditions).length ? "已编辑工况" : "工况不变"}；${updateBusinessEnabled("path") && actions ? `路径变更 ${actions} 项` : "路径不变"}</strong></div>
    <div><span>更新范围</span><strong>${escapeHtml(target || scopeLabels[scope] || "未选择")}</strong></div>
    <div><span>路径变更</span><strong>${updateBusinessEnabled("path") ? `已选择 ${actions} 项` : "不执行"}</strong></div>`;
}

function commonConditions(files) {
  if (!files.length) return {};
  const keys = ["line", "device_id", "model_name", "reference", "load_value", "load_unit", "speed_ratio", "acquired_at"];
  const common = {};
  keys.forEach(key => {
    const values = files.map(file => file.conditions?.[key] ?? "");
    if (values.every(value => value === values[0]) && values[0] !== "") common[key] = values[0];
  });
  const extraKeys = Object.keys(files[0].conditions?.extra_fields || {});
  const extraFields = {};
  extraKeys.forEach(key => {
    const values = files.map(file => file.conditions?.extra_fields?.[key]);
    if (values.every(value => value !== undefined && value === values[0])) extraFields[key] = values[0];
  });
  if (Object.keys(extraFields).length) common.extra_fields = extraFields;
  return common;
}

function populateUpdateConditionsFromScope() {
  const scopeFiles = state.updateDialog.scopeFiles || state.files;
  const scope = $("#reconcileScope").value;
  let files = [];
  let sourceKey = scope;
  if (scope === "selected_files") {
    files = scopeFiles.filter(file => state.updateDialog.selectedFileUids.includes(file.uid));
    sourceKey += `:${state.updateDialog.selectedFileUids.join(",")}`;
  } else if (scope === "folder") {
    const folder = $("#reconcileFolderPath").value.replace(/\/+$/, "");
    sourceKey += `:${folder}`;
    files = folder ? scopeFiles.filter(file => file.relativePath === folder || file.relativePath.startsWith(`${folder}/`)) : [];
  } else if (scope === "file") {
    const resolved = resolveReconcileFilePath();
    sourceKey += `:${resolved.value}`;
    files = resolved.value ? scopeFiles.filter(file => file.relativePath === resolved.value) : [];
  } else {
    files = [...scopeFiles];
  }
  if (state.updateDialog.conditionSourceKey === sourceKey) return;
  state.updateDialog.conditionSourceKey = sourceKey;
  populateConditions($("#reconcileConditions"), commonConditions(files));
  state.updateDialog.conditionBaseline = collectConditions($("#reconcileConditions"), true);
  const badge = $("#conditionUpdatePermissionBadge");
  if (files.length === 1) badge.textContent = "已显示当前工况";
  else if (files.length > 1) badge.textContent = `已显示 ${files.length} 个文件的共同工况`;
  else badge.textContent = "范围内暂无文件";
}

function updateUpdateSteps() {
  $("#updateBusinessStep").hidden = state.updateWizardStep !== 1;
  $("#updateScopeStep").hidden = state.updateWizardStep !== 2;
  $("#updateConditionStep").hidden = state.updateWizardStep !== 3;
  $("#updatePathStep").hidden = state.updateWizardStep !== 4;
  $("#updateConfirmStep").hidden = state.updateWizardStep !== 5;
  $$('[data-update-step]').forEach(item => {
    const step = Number(item.dataset.updateStep);
    item.classList.toggle("is-active", step === state.updateWizardStep);
    item.classList.toggle("is-complete", step < state.updateWizardStep);
  });
  if (state.updateWizardStep === 5) renderUpdateReview();
}

function goToUpdateStep(step) {
  $("#updateNoChangesNotice").hidden = true;
  if (step >= 3) {
    const target = updateConditionTarget();
    if (target.error) return notify(target.error, "error");
  }
  if (step === 3) populateUpdateConditionsFromScope();
  if (step >= 5 && updateBusinessEnabled("path") && !state.reconcile) {
    $("#updateNoChangesNotice").textContent = "请先生成路径检查预览，或返回取消路径检查。";
    $("#updateNoChangesNotice").hidden = false;
    return;
  }
  if (step >= 5) {
    const changed = updateBusinessEnabled("conditions") && Object.keys(collectChangedConditions($("#reconcileConditions"))).length;
    const chosenPaths = updateBusinessEnabled("path") && state.reconcile && selectedReconcileActions().length;
    if (!changed && !chosenPaths) {
      $("#updateNoChangesNotice").textContent = "请先编辑工况或选择一项路径变更。";
      $("#updateNoChangesNotice").hidden = false;
      return;
    }
  }
  state.updateWizardStep = Math.max(1, Math.min(5, step));
  updateUpdateSteps();
}

function syncUpdateBusinessUi() {
  const conditionEnabled = updateBusinessEnabled("conditions");
  const pathEnabled = updateBusinessEnabled("path");
  const conditionToggle = $("#conditionUpdateBusinessToggle");
  const pathToggle = $("#pathUpdateBusinessToggle");

  conditionToggle.checked = conditionEnabled;
  conditionToggle.disabled = !state.updateDialog.allowed.conditions;
  pathToggle.checked = pathEnabled;
  pathToggle.disabled = !state.updateDialog.allowed.path;

  $("#updateConditionsPanel").hidden = !conditionEnabled;
  $("#updateConditionsSkipped").hidden = conditionEnabled;
  $("#directoryUpdatePanel").hidden = !pathEnabled;
  $("#updatePathSkipped").hidden = pathEnabled;
  $("#reconcileEmpty").hidden = !pathEnabled || Boolean(state.reconcile);
  $("#reconcileWorkspace").hidden = !pathEnabled || !state.reconcile;

  const hint = $("#updateBusinessHint");
  if (state.updateDialog.entryMode === "selected_files") {
    hint.textContent = "当前入口只更新数据面板中所选文件的工况。";
  } else if (!state.updateDialog.allowed.path) {
    hint.textContent = "数据库路径当前不可访问，本次只能更新工况；路径可用后才能检查和更新路径。";
  } else if (conditionEnabled && pathEnabled) {
    hint.textContent = "仅提交改动的工况字段和手动选择的路径变更。";
  } else if (conditionEnabled) {
    hint.textContent = "仅提交本次编辑的工况字段，不检查或修改路径。";
  } else {
    hint.textContent = "只应用手动选择的路径变更，不修改已登记文件的工况。";
  }
  updateReconcileHint();
  updateUpdateSteps();
}

function setUpdateBusinessEnabled(key, enabled) {
  if (!state.updateDialog.allowed?.[key]) return syncUpdateBusinessUi();
  const next = { ...state.updateDialog.enabled, [key]: enabled };
  if (!next.conditions && !next.path) {
    notify("工况更新、路径检查和更新至少选择一项", "error");
    return syncUpdateBusinessUi();
  }
  const pathTurnedOff = state.updateDialog.enabled.path && !next.path;
  state.updateDialog.enabled = next;
  if (pathTurnedOff) {
    state.reconcileRequestToken += 1;
    setBusy($("#reconcilePreviewButton"), false);
    setBusy($("#reconcileApplyButton"), false);
  }
  syncUpdateBusinessUi();
}

function invalidateReconcilePreview() {
  state.reconcileRequestToken += 1;
  state.reconcile = null;
  $("#reconcilePreviewId").textContent = "";
  setBusy($("#reconcilePreviewButton"), false);
  setBusy($("#reconcileApplyButton"), false);
  syncUpdateBusinessUi();
}

function updateReconcileScopeFields() {
  const scope = $("#reconcileScope").value;
  $("#reconcileFolderField").hidden = scope !== "folder";
  $("#reconcileFileField").hidden = scope !== "file";
  syncUpdateBusinessUi();
}

function resolveReconcileFilePath() {
  const input = $("#reconcileFilePath");
  const query = input.value.trim().replace(/^\.\//, "");
  if (!query) return { value: "", message: "请输入文件名并从建议中选择" };
  const lowered = query.toLocaleLowerCase("zh-CN");
  const exact = state.reconcilePathOptions.files.find(path => path.toLocaleLowerCase("zh-CN") === lowered);
  if (exact) return { value: exact };
  const matches = state.reconcilePathOptions.files.filter(path => {
    const normalized = path.toLocaleLowerCase("zh-CN");
    const filename = (path.split("/").pop() || path).toLocaleLowerCase("zh-CN");
    return normalized.includes(lowered) || filename.includes(lowered);
  });
  if (matches.length === 1) {
    input.value = matches[0];
    return { value: matches[0] };
  }
  if (matches.length > 1) return { value: "", message: `找到 ${matches.length} 个匹配文件，请从建议中选择具体文件` };
  return { value: "", message: "没有找到匹配文件，请更换关键词" };
}

async function submitImport(event) {
  event.preventDefault();
  if (!storageIsReady()) return notify("数据库路径当前不可访问", "error");
  const button = $("#startImportButton");
  const sourcePaths = $("#importSourcePaths").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const placementMode = $("#importPlacementMode").value;
  const sourceScope = placementMode === "register" ? "inside" : "outside";
  const transferMode = placementMode === "move" ? "move" : "copy";
  const line = $("#importLineSelect").value;
  const sourceFileType = $("#importSourceFileType").value;
  if (!sourcePaths.length) return notify("请至少填写一个源文件路径", "error");
  if (!line) return notify("请先选择产线", "error");
  if (!sourceFileType) return notify("请选择导入文件类型", "error");
  if (!placementMode) return notify("请选择是否拷贝到数据库文件夹内", "error");
  const conditions = collectConditions($("#importConditions"), true);
  conditions.line = line;
  const payload = {
    source_paths: sourcePaths,
    source_scope: sourceScope,
    transfer_mode: transferMode,
    source_file_type: sourceFileType,
    target_storage_id: selectedDatabase()?.storageId,
    target_relative_dir: sourceScope === "inside" ? "" : line,
    conditions,
    sample_profile: $("#importSampleProfile").value || "default",
  };
  const confirming = Boolean(state.importPreviewId);
  if (confirming) payload.preview_id = state.importPreviewId;
  $("#importProgress").hidden = !confirming;
  setBusy(button, true, confirming ? "正在导入…" : "正在预览…");
  try {
    if (!confirming) {
      const preview = await request(API.importPreview, { method: "POST", body: payload });
      state.importPreviewId = preview.preview_id;
      renderImportResult(preview);
      button.dataset.label = "确认并登记";
      updateImportSteps();
      notify("预览已生成，尚未复制、压缩或写入数据库；核对后再次点击确认");
      return;
    }
    const result = await request(API.importFiles, { method: "POST", body: payload });
    renderImportResult(result);
    state.importPreviewId = null;
    button.dataset.label = "生成导入预览";
    await Promise.allSettled([loadSummary(), loadFiles(), loadConditionOptions()]);
    notify("导入任务已完成，请核对报告");
    const importedCount = Number(firstDefined(result?.result || result, ["success_count", "registered_count", "success"], 0));
    window.alert(importedCount > 0 ? `导入成功，共导入 ${importedCount} 个文件。` : "导入完成，请核对导入报告。");
  } catch (error) {
    notify(error.message, "error", true);
  } finally {
    $("#importProgress").hidden = true;
    setBusy(button, false);
  }
}

async function previewReconcile() {
  if (!updateBusinessEnabled("path")) return notify("请先选择“路径检查和更新”", "error");
  if (!storageIsReady()) return notify("数据库路径当前不可访问", "error");
  const databaseUid = scopedDatabaseUid();
  if (!databaseUid) return notify("请先打开数据库", "error");
  const requestToken = ++state.reconcileRequestToken;
  const button = $("#reconcilePreviewButton");
  const scope = $("#reconcileScope").value;
  let relativePath = "";
  if (scope === "folder") {
    relativePath = $("#reconcileFolderPath").value;
    if (!relativePath) return notify("请从下拉列表选择要更新的文件夹", "error");
  } else if (scope === "file") {
    const resolved = resolveReconcileFilePath();
    if (!resolved.value) return notify(resolved.message, "error");
    relativePath = resolved.value;
  }
  const params = new URLSearchParams({ scope });
  params.set("database_uid", databaseUid);
  if (scope !== "database") params.set("relative_path", relativePath);
  setBusy(button, true, "正在扫描…");
  try {
    const payload = await request(`${API.reconcilePreview}?${params.toString()}`, { method: "POST", body: {} });
    if (requestToken !== state.reconcileRequestToken || scopedDatabaseUid() !== databaseUid) return;
    renderReconcile(payload);
    notify("更新预览已生成，尚未修改数据库或文件");
  } catch (error) {
    if (requestToken === state.reconcileRequestToken && scopedDatabaseUid() === databaseUid) {
      notify(error.message, "error", true);
    }
  } finally {
    if (requestToken === state.reconcileRequestToken) setBusy(button, false);
  }
}

async function applyReconcile({ quiet = false, refreshPreview = true } = {}) {
  if (!updateBusinessEnabled("path")) { if (!quiet) notify("请先选择“路径检查和更新”", "error"); return false; }
  if (!storageIsReady()) { if (!quiet) notify("数据库路径当前不可访问", "error"); return false; }
  const databaseUid = scopedDatabaseUid();
  if (!databaseUid) { if (!quiet) notify("请先打开数据库", "error"); return false; }
  const actions = selectedReconcileActions();
  const previewId = firstDefined(state.reconcile, ["preview_id", "id"], "");
  if (!previewId) { if (!quiet) notify("预览 ID 缺失，请重新生成预览", "error"); return false; }
  if (!actions.length) return true;
  const requestToken = ++state.reconcileRequestToken;
  const button = $("#reconcileApplyButton");
  const body = {
    database_uid: databaseUid,
    preview_id: previewId,
    actions,
  };
  if (updateBusinessEnabled("conditions")) {
    const replacementConditions = collectChangedConditions($("#reconcileConditions"));
    if (Object.keys(replacementConditions).length) body.replacement_conditions = replacementConditions;
  }
  setBusy(button, true, "正在应用…");
  try {
    const result = await request(API.reconcileApply, {
      method: "POST",
      body,
    });
    if (requestToken !== state.reconcileRequestToken || scopedDatabaseUid() !== databaseUid) return;
    if (!quiet) notify(firstDefined(result, ["message"], `已应用 ${actions.length} 项变更`));
    await Promise.allSettled([loadSummary(), loadFiles(), loadConditionOptions()]);
    if (requestToken !== state.reconcileRequestToken || scopedDatabaseUid() !== databaseUid || !updateBusinessEnabled("path")) return;
    if (refreshPreview) await previewReconcile();
    return true;
  } catch (error) {
    if (requestToken === state.reconcileRequestToken && scopedDatabaseUid() === databaseUid) {
      notify(error.message, "error", true);
    }
    return false;
  } finally {
    setBusy(button, false);
    updateReconcileHint();
  }
}

async function openUpdateDialog({ selectedFiles = false } = {}) {
  const database = selectedDatabase();
  if (!database?.uid) return notify("请先打开数据库", "error");

  const selected = selectedFiles
    ? state.files.filter(file => state.selectedFiles.has(file.uid))
    : [];
  if (selectedFiles && !selected.length) return notify("请先选择文件", "error");

  let scopeFiles = selected;
  if (!selectedFiles) {
    try {
      const params = new URLSearchParams({ database_uid: database.uid, limit: "5000" });
      const payload = await request(`${API.files}?${params.toString()}`);
      scopeFiles = asArray(payload, ["files", "items", "results"]).map(normalizeFile);
    } catch (error) {
      return notify(`工况读取失败：${error.message}`, "error", true);
    }
  }

  window.clearTimeout(reconcileFileSearchTimer);
  state.reconcilePathRequestToken += 1;
  state.reconcileRequestToken += 1;
  state.reconcile = null;
  state.updateWizardStep = 1;
  const pathAllowed = !selectedFiles && storageIsReady();
  state.updateDialog = {
    entryMode: selectedFiles ? "selected_files" : "normal",
    allowed: { conditions: true, path: pathAllowed },
    enabled: { conditions: true, path: false },
    selectedFileUids: selected.map(file => file.uid),
    singleFile: selected.length === 1,
    conditionSourceKey: "",
    scopeFiles,
  };

  const selectedScope = $("#selectedFilesUpdateScope");
  const scope = $("#reconcileScope");
  selectedScope.hidden = !selectedFiles;
  selectedScope.disabled = !selectedFiles;
  scope.disabled = selectedFiles;
  scope.value = selectedFiles ? "selected_files" : "database";

  state.reconcilePathOptions = { folders: [], files: [] };
  renderReconcilePathOptions();
  $("#reconcileFilePath").value = "";
  $("#reconcilePreviewId").textContent = "";
  setBusy($("#reconcilePreviewButton"), false);
  setBusy($("#reconcileApplyButton"), false);

  const conditionRoot = $("#reconcileConditions");
  populateConditions(conditionRoot, selected.length === 1 ? selected[0].conditions : {});
  state.updateDialog.conditionBaseline = collectConditions(conditionRoot, true);
  const badge = $("#conditionUpdatePermissionBadge");
  const hint = $("#updateConditionsHint");
  if (selected.length === 1) {
    badge.textContent = "单文件编辑";
    hint.textContent = `正在编辑 ${selected[0].relativePath}；只提交本次修改的字段，清空字段可删除该值。`;
  } else if (selected.length > 1) {
    badge.textContent = `${selected.length} 个文件`;
    hint.textContent = "批量更新只提交本次修改的字段。";
  } else {
    badge.textContent = "按范围更新";
    hint.textContent = "预填工况仅供查看；只提交本次修改的字段。";
  }

  updateReconcileScopeFields();
  updateUpdateSteps();
  $("#reconcileDialog").showModal();
  if (!selectedFiles) {
    setReconcileFileSearchHint("正在载入文件夹和最近的文件…");
    loadReconcilePathOptions({ fileLimit: 30 })
      .catch(error => notify(`文件夹和文件列表读取失败：${error.message}`, "error", true));
  }
}

async function confirmWizardUpdate() {
  const button = $("#confirmUpdateButton");
  setBusy(button, true, "正在更新…");
  try {
    const hasConditionChanges = updateBusinessEnabled("conditions") && Object.keys(collectChangedConditions($("#reconcileConditions"))).length > 0;
    const hasPathChanges = updateBusinessEnabled("path") && selectedReconcileActions().length > 0;
    if (!hasConditionChanges && !hasPathChanges) return notify("没有已选择的更新内容", "error");
    if (hasConditionChanges) {
      const saved = await saveScopeConditions({ quiet: true });
      if (!saved) return;
    }
    if (hasPathChanges) {
      const applied = await applyReconcile({ quiet: true, refreshPreview: false });
      if (!applied) return;
    }
    window.alert("更新成功。");
    $("#reconcileDialog").close();
  } finally {
    setBusy(button, false);
  }
}

function updateConditionTarget() {
  const scope = $("#reconcileScope").value;
  if (scope === "selected_files") {
    if (!state.updateDialog.selectedFileUids.length) return { error: "请先选择文件" };
    return { file_uids: [...state.updateDialog.selectedFileUids] };
  }
  if (scope === "folder") {
    const relativePath = $("#reconcileFolderPath").value;
    if (!relativePath) return { error: "请从下拉列表选择要更新的文件夹" };
    return { scope, relative_path: relativePath };
  }
  if (scope === "file") {
    const resolved = resolveReconcileFilePath();
    if (!resolved.value) return { error: resolved.message };
    return { scope, relative_path: resolved.value };
  }
  return { scope: "database" };
}

async function saveScopeConditions({ quiet = false } = {}) {
  if (!updateBusinessEnabled("conditions")) { if (!quiet) notify("请先选择“工况更新”", "error"); return false; }
  const conditions = collectChangedConditions($("#reconcileConditions"));
  if (!Object.keys(conditions).length) { if (!quiet) notify("没有已修改的工况字段", "error"); return false; }
  const target = updateConditionTarget();
  if (target.error) { if (!quiet) notify(target.error, "error"); return false; }
  const button = $("#saveScopeConditionsButton");
  setBusy(button, true, "正在更新…");
  try {
    const params = new URLSearchParams();
    if (scopedDatabaseUid()) params.set("database_uid", scopedDatabaseUid());
    const url = params.size ? `${API.fileConditions}?${params.toString()}` : API.fileConditions;
    const result = await request(url, {
      method: "PATCH",
      body: { ...target, conditions },
    });
    if (!Number(result.updated_count || 0)) {
      notify("所选范围内没有可更新的已登记文件", "error");
      return false;
    }
    await Promise.allSettled([loadFiles(), loadSummary(), loadConditionOptions()]);
    if (!quiet) notify(`已更新 ${formatNumber(result.updated_count)} 个文件的工况`);
    return true;
  } catch (error) {
    notify(error.message, "error", true);
    return false;
  } finally {
    setBusy(button, false);
  }
}

function bindEvents() {
  window.addEventListener("resize", scheduleLabelSignalResize);
  $("#newLabelProjectButton").addEventListener("click", () => openLabelProject().catch(error => notify(error.message, "error", true)));
  $("#backToLabelProjectsButton").addEventListener("click", () => { loadLabelProjects().then(() => switchLabelWorkflowTab("projects")).catch(error => notify(error.message, "error", true)); });
  $("#labelProjectsList").addEventListener("click", event => {
    const deleteButton = event.target.closest("[data-delete-label-project]");
    if (deleteButton) {
      const project = state.labelProjects.find(item => item.project_id === deleteButton.dataset.deleteLabelProject);
      if (project) void deleteLabelProject(project, deleteButton);
      return;
    }
    const button = event.target.closest("[data-open-label-project]");
    if (!button) return;
    const project = state.labelProjects.find(item => item.project_id === button.dataset.openLabelProject);
    if (project) openLabelProject(project, button.dataset.projectDestination).catch(error => notify(error.message, "error", true));
  });
  $("#saveLabelProjectButton").addEventListener("click", event => saveLabelProject(event.currentTarget));
  $("#nextLabelProjectButton").addEventListener("click", event => saveLabelProject(event.currentTarget, { advance: true }));
  $("#refreshLabelProjectFilesButton").addEventListener("click", event => refreshLabelProjectFiles(event.currentTarget));
  $("#dataFilterPanel").addEventListener("toggle", event => {
    $(".filter-collapse-state", event.currentTarget).textContent = event.currentTarget.open ? "收起" : "展开";
  });
  $$(".nav-item").forEach(button => button.addEventListener("click", () => {
    switchTopLevelView(button.dataset.view).catch(error => notify(error.message, "error", true));
  }));
  $("#refreshAllButton").addEventListener("click", async event => {
    setBusy(event.currentTarget, true, "刷新中…");
    try { await refreshAll(true); } finally { setBusy(event.currentTarget, false); }
  });
  $("#reloadDatabasesButton").addEventListener("click", async event => {
    setBusy(event.currentTarget, true, "刷新中…");
    try { await loadDatabases(); } catch (error) { notify(error.message, "error"); } finally { setBusy(event.currentTarget, false); }
  });
  $("#createDatabaseCardButton").addEventListener("click", () => $("#createDatabaseDialog").showModal());
  $("#chooseDatabaseParentButton").addEventListener("click", event => chooseDatabaseParent(event.currentTarget));
  $("#createDatabaseForm").addEventListener("submit", event => {
    event.preventDefault();
    createDatabase($("#confirmCreateDatabaseButton"));
  });
  $("#openDatabaseFolderCardButton").addEventListener("click", event => {
    selectDatabaseFolder(event.currentTarget);
  });
  $("#databaseList").addEventListener("click", event => {
    const openButton = event.target.closest(".open-database-button");
    if (openButton) {
      openDatabase(state.databases[Number(openButton.dataset.index)], openButton);
      return;
    }
    const removeButton = event.target.closest(".database-remove-button");
    if (removeButton) removeDatabaseRecord(state.databases[Number(removeButton.dataset.index)], removeButton);
  });
  $("#backToDatabasesButton").addEventListener("click", () => switchView("databases"));
  $("#databaseWorkspaceTab").addEventListener("click", () => switchView("database"));
  $("#dataPanelWorkspaceTab").addEventListener("click", event => {
    if (event.target.closest(".workspace-tab-close")) return;
    switchView("data-panel");
  });
  $("#closeDataPanelTab").addEventListener("click", event => {
    event.stopPropagation();
    closeDataPanel();
  });
  $("#databaseMetaForm").addEventListener("submit", saveDatabaseMetadata);
  $("#openDataMaintenanceButton").addEventListener("click", () => {
    $$('[data-maintenance-action]').forEach(button => {
      const needsStorage = button.dataset.maintenanceAction === "import";
      button.disabled = needsStorage && !storageIsReady();
      button.title = button.disabled ? "数据库路径当前不可访问" : "";
    });
    $("#dataMaintenanceDialog").showModal();
  });
  $("#exportDatabaseButton").addEventListener("click", async () => {
    const button = $("#exportDatabaseButton");
    button.disabled = true;
    try {
      const databaseUid = scopedDatabaseUid();
      if (!databaseUid) throw new Error("请先打开数据库");
      const result = await request(`${API.exportFilesByLine}?database_uid=${encodeURIComponent(databaseUid)}`, { method: "POST" });
      notify(`已导出 ${result.line_count} 条产线、${result.file_count} 个文件到 ${result.export_directory}`);
    } catch (error) { notify(`导出失败：${error.message}`, "error", true); }
    finally { button.disabled = false; }
  });
  $$('[data-maintenance-action]').forEach(button => button.addEventListener("click", async () => {
    $("#dataMaintenanceDialog").close();
    if (button.dataset.maintenanceAction === "import") return openRawImportDialog();
    await openUpdateDialog();
  }));
  $("#selectLabelTdmsButton").addEventListener("click", event => selectLabelSources("files", event.currentTarget));
  $("#selectLabelFolderButton").addEventListener("click", event => selectLabelProjectRoot(event.currentTarget));
  $("#labelFileType").addEventListener("change", () => { syncLabelLineField(); saveLastLabelSettings(); });
  $("#labelProjectType").addEventListener("change", event => {
    $("#labelWavProfileSelect").value = event.currentTarget.value === "generic" ? "" : event.currentTarget.value;
    saveLastLabelSettings();
  });
  $("#labelLineSelect").addEventListener("change", async event => {
    saveLastLabelSettings();
    if (!event.currentTarget.value || !state.labelingQueue.length) return;
    await loadLabelQueueIndex(0, { force: true });
  });
  $("#labelWavProfileSelect").addEventListener("change", async event => {
    saveLastLabelSettings();
    const profile = event.currentTarget.value;
    if (!profile || !state.labelingAllQueue.length || !state.labelingAllQueue[0].path?.toLowerCase().endsWith(".wav")) return;
    state.labelingAllQueue.forEach(item => { item.wavProfile = profile; item.loaded = false; });
    await loadLabelQueueIndex(0, { force: true });
  });
  $("#selectLabelJsonButton").addEventListener("click", event => selectLabelJson(event.currentTarget));
  $("#clearLabelJsonButton").addEventListener("click", async event => {
    if (state.labelingQueueLoading || state.labelingConfigLoading || guardPendingLabelEvents()) return;
    const button = event.currentTarget;
    setBusy(button, true, "切换中…");
    try {
      await loadLabelTaxonomy($("#labelSelectedSourcePath").value.trim(), { selectedPath: "" });
      $("#labelSelectedJsonPath").value = "";
      $("#labelJsonSelectionStatus").textContent = "默认：正常、异常、边界，可直接编辑";
      state.labelingSessionRequest = null;
      state.labelingSessionPath = "";
      saveLastLabelSettings();
      if (state.labelingQueue.length) await loadLabelQueueIndex(state.labelingQueueIndex, { force: true });
    } catch (error) { notify(error.message, "error", true); }
    finally { setBusy(button, false); }
  });
  $("#selectLabelSessionButton").addEventListener("click", event => selectLabelJson(event.currentTarget, true));
  $("#clearLabelSessionButton").addEventListener("click", () => {
    if (guardPendingLabelEvents()) return;
    state.labelingHistoryPath = "";
    state.labelingHistorySelected = false;
    state.labelingSessionPath = "";
    state.labelingSessionRequest = null;
    $("#labelSelectedSessionPath").value = "";
    $("#labelSessionJsonPath").textContent = "开始标注时自动生成";
    $("#labelSessionSelectionStatus").textContent = "不选择则自动生成标注 JSON";
    saveLastLabelSettings();
  });
  $("#labelSourceType").addEventListener("change", () => { updateLabelSourcePreview(); saveLastLabelSettings(); });
  $("#labelSourceIdentity").addEventListener("input", () => { updateLabelSourcePreview(); saveLastLabelSettings(); });
  $("#addLabelResultButton").addEventListener("click", () => {
    if (!state.labelTaxonomy.path) return notify("标签配置尚未加载", "error");
    syncLabelTaxonomyEditor();
    state.labelTaxonomy.results.push({ result_key: `result_${state.labelTaxonomy.results.length + 1}`, result_id: state.labelTaxonomy.results.length, result_name: "新结果" });
    renderLabelTaxonomyEditor();
  });
  $("#addLabelReasonButton").addEventListener("click", () => {
    if (!state.labelTaxonomy.path) return notify("标签配置尚未加载", "error");
    syncLabelTaxonomyEditor();
    state.labelTaxonomy.reasons.push({ reason_key: `reason_${state.labelTaxonomy.reasons.length + 1}`, reason_id: state.labelTaxonomy.reasons.length, reason_name: "新类别", result_key: state.labelTaxonomy.results[0]?.result_key || "" });
    renderLabelTaxonomyEditor();
  });
  $("#saveLabelTaxonomyButton").addEventListener("click", event => saveLabelTaxonomy(event.currentTarget));
  $("#labelTaxonomyEditor").addEventListener("input", syncLabelTaxonomyEditor);
  $("#labelTaxonomyEditor").addEventListener("change", event => {
    if (event.target.dataset.taxonomyField !== "reason_id") return;
    syncLabelTaxonomyEditor();
    state.labelTaxonomy.reasons.sort((a, b) => compareReasonIds({ id: a.reason_id, key: a.reason_key }, { id: b.reason_id, key: b.reason_key }));
    renderLabelTaxonomyEditor();
  });
  $("#labelTaxonomyEditor").addEventListener("click", event => {
    const button = event.target.closest("[data-delete-taxonomy]");
    if (!button) return;
    syncLabelTaxonomyEditor();
    const row = button.closest("[data-taxonomy-kind]");
    state.labelTaxonomy[row.dataset.taxonomyKind].splice(Number(row.dataset.taxonomyIndex), 1);
    renderLabelTaxonomyEditor();
  });
  $("#toggleLabelFileListButton").addEventListener("click", () => { $("#labelFileListPanel").hidden = !$("#labelFileListPanel").hidden; });
  $("#previousLabelFileButton").addEventListener("click", () => loadLabelQueueIndex(state.labelingQueueIndex - 1));
  $("#nextLabelFileButton").addEventListener("click", () => loadLabelQueueIndex(state.labelingQueueIndex + 1));
  $("#gotoLabelFileButton").addEventListener("click", () => loadLabelQueueIndex(Number($("#labelGotoIndex").value) - 1));
  $("#startLabelingFromSettingsButton").addEventListener("click", async event => {
    try {
      await refreshLabelQueueLabels();
      switchLabelWorkflowTab("files");
      renderLabelQueue();
    } catch (error) { notify(error.message, "error", true); }
  });
  $("#startLabelingFromListButton").addEventListener("click", event => enterLabelAnnotation(event.currentTarget));
  $("#applyLabelQueueFilterButton").addEventListener("click", async () => {
    try { await refreshLabelQueueLabels(); await applyLabelQueueFilter(); }
    catch (error) { notify(error.message, "error", true); }
  });
  $("#clearLabelQueueFilterButton").addEventListener("click", async () => {
    $("#labelQueueSearchInput").value = "";
    $("#labelQueueFolderFilter").value = "";
    $("#labelQueueResultFilter").value = "";
    $("#labelQueueReasonFilter").value = "";
    for (const id of ["labelQueueFolderFilter", "labelQueueResultFilter", "labelQueueReasonFilter"]) $(`#${id}`)._syncTouchPicker?.();
    await applyLabelQueueFilter();
  });
  let labelQueueSearchTimer;
  $("#labelQueueSearchInput").addEventListener("input", () => {
    clearTimeout(labelQueueSearchTimer);
    labelQueueSearchTimer = setTimeout(() => void applyLabelQueueFilter(), 200);
  });
  $("#labelQueueResultFilter").addEventListener("change", event => {
    if (event.currentTarget.value === "__unlabeled__") $("#labelQueueReasonFilter").value = "";
  });
  let labelQueueClickTimer;
  $("#labelFileQueueList").addEventListener("click", event => {
    const item = event.target.closest("[data-label-queue-index]");
    if (!item || event.detail > 1) return;
    clearTimeout(labelQueueClickTimer);
    const index = Number(item.dataset.labelQueueIndex);
    labelQueueClickTimer = setTimeout(() => loadLabelQueueIndex(index), 300);
  });
  $("#labelFileQueueList").addEventListener("dblclick", async event => {
    const item = event.target.closest("[data-label-queue-index]");
    if (!item) return;
    clearTimeout(labelQueueClickTimer);
    const index = Number(item.dataset.labelQueueIndex);
    await loadLabelQueueIndex(index);
    const current = state.labelingQueue[index];
    if (state.labelingQueueIndex !== index || !labelQueueItemReady(current)) return;
    await enterLabelAnnotation();
  });
  $$('[data-label-tab]').forEach(button => button.addEventListener("click", async () => {
    if (state.currentView !== "labeling") await switchTopLevelView("labeling");
    if (button.dataset.labelTab === "annotation" && state.labelingQueueIndex >= 0) await enterLabelAnnotation(button);
    else switchLabelWorkflowTab(button.dataset.labelTab);
  }));
  $("#annotationPreviousButton").addEventListener("click", () => loadLabelQueueIndex(state.labelingQueueIndex - 1));
  $("#annotationNextButton").addEventListener("click", () => loadLabelQueueIndex(state.labelingQueueIndex + 1));
  const navigationSpeed = $("#labelPlaybackSpeed");
  navigationSpeed.addEventListener("input", () => setLabelPlaybackRate(navigationSpeed.value));
  let lastSpeedWheelAt = 0;
  navigationSpeed.addEventListener("wheel", event => {
    if (event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const now = performance.now();
    if (now - lastSpeedWheelAt < 55) return;
    lastSpeedWheelAt = now;
    setLabelPlaybackRate(Number(navigationSpeed.value) + (event.deltaY < 0 ? 0.1 : -0.1));
  }, { passive: false });
  $("#labelPlaybackVolume").addEventListener("input", event => setLabelPlaybackVolume(event.currentTarget.value));
  $("#labelLayoutSlots").addEventListener("change", event => {
    if (!event.target.matches("[data-layout-slot]")) return;
    const files = state.labelingFiles.length ? state.labelingFiles : [state.labelingFile].filter(Boolean);
    const { settings } = labelLayoutContext(files);
    const index = Number(event.target.dataset.layoutSlot);
    if (guardPendingLabelEvents()) { event.target.value = settings.slots[index] || ""; return; }
    if (event.target.value) settings.slots = settings.slots.map((key, slot) => slot !== index && key === event.target.value ? "" : key);
    settings.slots[index] = event.target.value;
    ++labelWaveformLoadToken;
    labelWaveformLoadTask = null;
    renderLabelChannels();
  });
  $("#labelLowFrequencyFilter").addEventListener("click", async event => {
    if (state.labelingQueueLoading || guardPendingLabelEvents()) return;
    const button = event.currentTarget;
    state.labelingLowFrequencyFilter = !state.labelingLowFrequencyFilter;
    const enabled = state.labelingLowFrequencyFilter;
    button.textContent = `低频滤波（20 Hz 高通）：${enabled ? "是" : "否"}`;
    button.setAttribute("aria-pressed", String(enabled));
    button.classList.toggle("button-primary", enabled);
    button.classList.toggle("button-secondary", !enabled);
    if (!state.labelingFiles.length) return;
    button.disabled = true;
    ++state.labelingResolveGeneration;
    releaseAnalysisResults();
    renderLabelChannels();
    button.disabled = false;
  });
  $("#openNavigationAnalysisButton").addEventListener("click", async () => {
    const [fileIndex, sampleIndex] = $("#labelAnalysisChannel").value.split(":").map(Number);
    const file = state.labelingFiles[fileIndex];
    const sample = file?.samples?.[sampleIndex];
    if (!sample || sample.missing) return notify("请先选择可分析通道", "error");
    const card = $$('[data-label-sample]', $("#labelChannelsRow")).find(item => item.dataset.labelFile === file.absolute_path && item.dataset.labelSample === sample.sample_id);
    if (!card?._labelSignal && !await loadLabelWaveform(sample, file, state.labelingResolveGeneration)) return notify("通道数据尚未载入", "error");
    openLabelAnalysis(sample.sample_id, file);
  });
  $("#annotationJumpForm").addEventListener("submit", async event => {
    event.preventDefault();
    const target = Number($("#annotationJumpIndex").value);
    if (!Number.isInteger(target) || target < 1 || target > state.labelingQueue.length) {
      notify(`请输入 1 到 ${state.labelingQueue.length} 之间的序号`, "error");
      return;
    }
    try {
      await loadLabelQueueIndex(target - 1);
    } finally {
      $("#annotationJumpIndex").value = state.labelingQueueIndex >= 0 ? state.labelingQueueIndex + 1 : "";
    }
  });
  $("#applyLabelLayoutButton").addEventListener("click", () => {
    const rows = Number($("#labelGridRows").value);
    const columns = Number($("#labelGridColumns").value);
    if (![rows, columns].every(value => Number.isInteger(value) && value >= 1 && value <= 8)) return notify("行数和列数请输入 1 到 8 的整数", "error");
    if (guardPendingLabelEvents()) return;
    const files = state.labelingFiles.length ? state.labelingFiles : [state.labelingFile].filter(Boolean);
    const { settings } = labelLayoutContext(files);
    settings.rows = rows;
    settings.columns = columns;
    settings.slots.length = rows * columns;
    ++labelWaveformLoadToken;
    labelWaveformLoadTask = null;
    renderLabelChannels();
    scheduleLabelSignalResize();
  });
  const historyLayout = $("#labelWorkspaceLayout");
  const historyResizer = $("#labelHistorySidebarResizer");
  try {
    const savedWidth = Number(localStorage.getItem("ai3-label-history-sidebar-width"));
    if (Number.isFinite(savedWidth) && savedWidth >= 280 && savedWidth <= 600) historyLayout.style.setProperty("--history-sidebar-width", savedWidth + "px");
  } catch { /* Use default width. */ }
  historyResizer.addEventListener("pointerdown", event => {
    if (getComputedStyle(historyResizer).display === "none") return;
    const updateWidth = clientX => {
      const rect = historyLayout.getBoundingClientRect();
      const width = Math.max(280, Math.min(600, rect.width - 500, rect.right - clientX));
      historyLayout.style.setProperty("--history-sidebar-width", width + "px");
      return width;
    };
    event.preventDefault();
    historyResizer.setPointerCapture(event.pointerId);
    let width = updateWidth(event.clientX);
    const move = moveEvent => { width = updateWidth(moveEvent.clientX); };
    const finish = () => {
      historyResizer.removeEventListener("pointermove", move);
      historyResizer.removeEventListener("pointerup", finish);
      historyResizer.removeEventListener("pointercancel", finish);
      try { localStorage.setItem("ai3-label-history-sidebar-width", String(width)); } catch { /* Keep current width. */ }
      scheduleLabelSignalResize();
    };
    historyResizer.addEventListener("pointermove", move);
    historyResizer.addEventListener("pointerup", finish);
    historyResizer.addEventListener("pointercancel", finish);
  });
  historyResizer.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const current = parseFloat(historyLayout.style.getPropertyValue("--history-sidebar-width")) || 360;
    const width = Math.max(280, Math.min(600, current + (event.key === "ArrowLeft" ? 20 : -20)));
    historyLayout.style.setProperty("--history-sidebar-width", width + "px");
    try { localStorage.setItem("ai3-label-history-sidebar-width", String(width)); } catch { /* Keep current width. */ }
    scheduleLabelSignalResize();
  });
  $("#labelHistorySidebarContent").addEventListener("click", event => {
    const button = event.target.closest("button");
    const section = button?.closest("[data-history-card-index]");
    if (!section) return;
    const card = $$('[data-label-sample]', $("#labelChannelsRow"))[Number(section.dataset.historyCardIndex)];
    if (!card) return;
    const index = $$("button", section).indexOf(button);
    const original = $$("button", $(".label-channel-history", card))[index];
    original?.click();
  });
  $("#labelHistorySidebarContent").addEventListener("dblclick", event => {
    const row = event.target.closest(".label-saved-event-row");
    const section = row?.closest("[data-history-card-index]");
    if (!section) return;
    const card = $$('[data-label-sample]', $("#labelChannelsRow"))[Number(section.dataset.historyCardIndex)];
    if (!card) return;
    const rows = $$(".label-saved-event-row", section);
    const original = $$(".label-saved-event-row", $(".label-channel-history", card))[rows.indexOf(row)];
    original?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
  });
  $("#labelChannelsRow").addEventListener("change", event => {
    const result = event.target.closest("[data-label-result]");
    const card = event.target.closest("[data-label-sample]");
    if (!card) return;
    if (result) {
      $("[data-label-reason]", card).innerHTML = labelReasonOptions(result.value);
      $("[data-label-reason]", card)._syncTouchPicker?.();
    }
    if (event.target.matches("[data-label-result], [data-label-reason], input[type=radio]")) captureActiveLabelEventForm(card);
  });
  $("#labelChannelsRow").addEventListener("input", event => {
    const card = event.target.closest("[data-label-sample]");
    if (card && event.target.matches("[data-label-note]")) captureActiveLabelEventForm(card);
  });
  $("#labelChannelsRow").addEventListener("submit", event => {
    const form = event.target.closest(".label-channel-form");
    if (!form) return;
    saveSingleLabel(event, form.closest("[data-label-sample]"));
  });
  $("#labelChannelsRow").addEventListener("dblclick", event => {
    const row = event.target.closest(".label-saved-event-row, .label-event-block");
    const card = row?.closest("[data-label-sample]");
    if (!card || card.dataset.eventSavePending === "true") return;
    const item = labelEventItemForSample(card, row.dataset.eventSampleId);
    if (!item) return;
    $("[data-signal-plot]", card)._selectLabelEventItem?.(item.key, true);
    $(".label-channel-form", card).scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#labelChannelsRow").addEventListener("click", async event => {
    const card = event.target.closest("[data-label-sample]");
    if (!card) return;
    if (card.dataset.eventSavePending === "true" && event.target.closest('[data-new-event], [data-label-whole], [data-close-event], [data-event-key], .label-event-block, .label-saved-event-row, [data-relabel-sample], [data-confirm-label-event], [data-delete-label-event], [data-cancel-label], [data-quick-anomaly]')) {
      notify("当前事件正在保存，请稍候", "error");
      return;
    }
    if (event.target.closest("[data-close-event]")) {
      card.dataset.relabeling = "false";
      delete card.dataset.editEventUuid;
      captureActiveLabelEventForm(card);
      const editor = ensureLabelEventEditor(card);
      const activeItem = activeLabelEvent(card);
      const activeIndex = editor.items.indexOf(activeItem);
      if (activeItem?.status === "draft") editor.items = editor.items.filter(item => item !== activeItem);
      else if (activeItem?.status === "saved" && activeItem.dirty) {
        activeItem.start = activeItem.committedStart;
        activeItem.end = activeItem.committedEnd;
        activeItem.form = { ...activeItem.committedForm };
        activeItem.dirty = false;
      }
      const eventAudio = $("[data-event-audio]", card);
      eventAudio.pause();
      const eventModeButton = $('[data-playback-mode="event"]', card);
      eventModeButton.disabled = true;
      eventAudio._setPlaybackMode?.("all", false);
      card.dataset.prototype = "false";
      $(".label-channel-form", card).hidden = true;
      const candidates = editor.items.filter(item => item !== activeItem);
      const nextItem = candidates[Math.min(Math.max(0, activeIndex), candidates.length - 1)] || null;
      editor.activeKey = "";
      if (nextItem) {
        $("[data-signal-plot]", card)._selectLabelEventItem?.(nextItem.key, false);
      } else {
        $("[data-signal-plot]", card)._clearActiveEventSelection?.();
        event.target.closest("[data-close-event]").hidden = true;
        card.dataset.eventSourceTypeOverride = "";
        selectLabelEvent(card);
      }
      return;
    }
    const deleteButton = event.target.closest("[data-delete-label-event]");
    if (deleteButton) {
      await deleteSavedLabelEvent(card, deleteButton);
      return;
    }
    const confirmButton = event.target.closest("[data-confirm-label-event]");
    if (confirmButton) {
      await confirmSavedLabelEvent(card, confirmButton);
      return;
    }
    const relabelButton = event.target.closest("[data-relabel-sample]");
    if (relabelButton) {
      const item = labelEventItemForSample(card, relabelButton.dataset.relabelSample);
      if (!item) return notify("未找到该 sample 的已保存标注", "error");
      const annotation = (labelFileForCard(card)?.annotations || []).find(entry => entry.sample_id === relabelButton.dataset.relabelSample);
      const selectedEvent = annotation?.label_events?.find(entry => entry.event_uuid === relabelButton.dataset.labelEventUuid);
      if (!selectedEvent) return notify("未找到这条已保存的标注", "error");
      item.start = item.committedStart;
      item.end = item.committedEnd;
      item.form = { ...labelEventFormStateFromAnnotation({ label_events: [selectedEvent] }), sourceTypeOverride: "" };
      item.dirty = false;
      $("[data-signal-plot]", card)._selectLabelEventItem?.(item.key, true);
      card.dataset.relabeling = "true";
      card.dataset.editEventUuid = selectedEvent.event_uuid;
      $("[data-signal-plot]", card)._activateEventSelection?.(item.committedStart, item.committedEnd);
      $("[data-save-label]", card).textContent = "保存编辑";
      $(".label-channel-form", card).scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const eventSwitch = event.target.closest("[data-event-key]");
    if (eventSwitch) {
      $("[data-signal-plot]", card)._selectLabelEventItem?.(eventSwitch.dataset.eventKey);
      return;
    }
    const eventBlock = event.target.closest(".label-event-block, .label-saved-event-row");
    if (eventBlock) {
      const item = labelEventItemForSample(card, eventBlock.dataset.eventSampleId);
      if (item) $("[data-signal-plot]", card)._selectLabelEventItem?.(item.key, false);
      return;
    }
    const form = $(".label-channel-form", card);
    if (event.target.closest("[data-label-whole]")) {
      captureActiveLabelEventForm(card);
      const editor = ensureLabelEventEditor(card);
      const existing = editor.items.find(item => item.scopeKind === "whole");
      const plot = $("[data-signal-plot]", card);
      if (existing) {
        plot._selectLabelEventItem?.(existing.key);
      } else {
        const start = Number(card.dataset.cutStart || 0);
        const end = Number(card.dataset.cutEnd || 0);
        if (end <= start) return notify("当前通道没有有效时间范围", "error");
        const item = createLabelEventDraft(card, start, end, "whole");
        plot._selectLabelEventItem?.(item.key);
      }
    } else if (event.target.closest("[data-new-event]")) {
      captureActiveLabelEventForm(card);
      card.dataset.prototype = "false";
      const duration = Number(card._labelSignal?.duration_s || $("[data-scope-duration]", card).max || 0);
      const cutStart = Number(card.dataset.cutStart || 0);
      const cutEnd = Number(card.dataset.cutEnd || duration);
      const cutDuration = Math.max(0, cutEnd - cutStart);
      const width = Math.min(cutDuration, 1);
      const editor = ensureLabelEventEditor(card);
      const offset = (editor.items.length % 6) * cutDuration * 0.04;
      const start = Math.max(cutStart, Math.min(cutEnd - width, cutStart + offset));
      const item = createLabelEventDraft(card, start, start + width);
      $("[data-signal-plot]", card)._selectLabelEventItem?.(item.key);
    } else if (event.target.closest("[data-cancel-label]")) {
      if (card.dataset.relabeling === "true") {
        const item = activeLabelEvent(card);
        if (item?.status === "saved") {
          item.form = { ...item.committedForm };
          item.dirty = false;
        }
      } else captureActiveLabelEventForm(card);
      card.dataset.relabeling = "false";
      $("[data-save-label]", card).textContent = "保存标注";
      form.hidden = true;
    } else if (event.target.closest("[data-quick-anomaly]")) {
      const results = state.labelingFile?.taxonomy?.results || {};
      const abnormalKey = Object.entries(results).find(([key, value]) => key === "nok" || String(value.name).includes("异常"))?.[0];
      if (!abnormalKey) return notify("标签类别中没有定义异常结果", "error");
      const file = labelFileForCard(card);
      if (!file || !state.labelingSessionPath) return notify("请先创建标注会话", "error");
      const button = event.target.closest("[data-quick-anomaly]");
      setBusy(button, true, "保存中…");
      try {
        await request(API.labelingPrototype, { method: "PATCH", body: {
          path: file.absolute_path, session_path: state.labelingSessionPath,
        } });
      } catch (error) {
        return notify(`标记典型异常失败：${error.message}`, "error", true);
      } finally {
        setBusy(button, false);
      }
      file.prototype = true;
      card.dataset.prototype = "true";
      card.dataset.eventSourceTypeOverride = "expert";
      $("[data-label-result]", card).value = abnormalKey;
      $("[data-label-reason]", card).innerHTML = labelReasonOptions(abnormalKey);
      $("[data-label-result]", card)._syncTouchPicker?.();
      $("[data-label-reason]", card)._syncTouchPicker?.();
      form.hidden = false;
      captureActiveLabelEventForm(card);
      notify("已将当前文件的 prototype 保存为 true");
    }
  });
  $("#closeLabelAnalysisButton").addEventListener("click", () => { releaseAnalysisResults(); $("#labelAnalysisMask").hidden = true; });
  const analysisDrawer = $("#analysisDrawer");
  const analysisResizer = $("#analysisDrawerResizer");
  analysisResizer.addEventListener("pointerdown", event => {
    event.preventDefault();
    document.body.classList.add("is-resizing-analysis");
    analysisResizer.setPointerCapture(event.pointerId);
  });
  analysisResizer.addEventListener("pointermove", event => {
    if (!analysisResizer.hasPointerCapture(event.pointerId)) return;
    const width = Math.max(420, Math.min(window.innerWidth * 0.9, window.innerWidth - event.clientX));
    analysisDrawer.style.width = `${width}px`;
    $$('[data-analysis-result] .analysis-plot').forEach(host => window.Plotly?.Plots.resize(host));
  });
  const stopAnalysisResize = event => {
    if (analysisResizer.hasPointerCapture(event.pointerId)) analysisResizer.releasePointerCapture(event.pointerId);
    document.body.classList.remove("is-resizing-analysis");
  };
  analysisResizer.addEventListener("pointerup", stopAnalysisResize);
  analysisResizer.addEventListener("pointercancel", stopAnalysisResize);
  $("#labelAnalysisMask").addEventListener("click", event => {
    if (event.target === event.currentTarget) { releaseAnalysisResults(); event.currentTarget.hidden = true; }
    const closeResult = event.target.closest("[data-close-analysis-result]");
    if (closeResult) {
      const card = closeResult.closest("[data-analysis-result]");
      card._analysisAbortController?.abort();
      window.Plotly?.purge($(".analysis-plot", card));
      card.remove();
      if (!$("#analysisResults").querySelector("[data-analysis-result]")) $("#analysisResults").innerHTML = '<div class="analysis-empty">选择上方分析方法后显示结果。</div>';
      return;
    }
    const target = event.target.closest("[data-analysis-sample]");
    if (target) {
      state.labelingAnalysisSample = target.dataset.analysisSample;
      renderAnalysisTargets();
    }
    const kind = event.target.closest("[data-analysis-kind]");
    if (kind) {
      state.labelingAnalysisKind = kind.dataset.analysisKind;
      renderAnalysisParameters(state.labelingAnalysisCards.find(card => card.id === state.labelingAnalysisKind));
      $$('[data-analysis-kind]').forEach(button => button.classList.toggle("button-primary", button === kind));
    }
  });
  $("#applyAnalysisParametersButton").addEventListener("click", () => {
    if (state.labelingAnalysisKind) renderAdvancedAnalysis(state.labelingAnalysisKind, false);
  });
  $("#openLabelImportButton").addEventListener("click", () => {
    if (!selectedDatabase()) return notify("请先打开数据库", "error");
    renderDatabaseContext();
    $("#labelImportDialog").showModal();
  });
  $("#openDataPanelButton").addEventListener("click", openDataPanel);
  $("#selectStorageFolderButton").addEventListener("click", event => selectStorageFolder(event.currentTarget));
  $$('[data-close-dialog]').forEach(button => button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.closeDialog);
    if (dialog?.open) dialog.close();
  }));

  $("#selectImportFilesButton").addEventListener("click", event => selectImportSources("files", event.currentTarget));
  $("#selectImportFolderButton").addEventListener("click", event => selectImportSources("folder", event.currentTarget));
  $("#importSourcePaths").addEventListener("input", updateImportSteps);
  $("#importPlacementMode").addEventListener("change", updateImportSteps);
  $("#importLineSelect").addEventListener("change", applyImportLine);
  $("#showCreateLineButton").addEventListener("click", () => {
    const fields = $("#createLineFields");
    fields.hidden = !fields.hidden;
    if (!fields.hidden) requestAnimationFrame(() => $("#newLineName").focus());
  });
  $("#autoDetectLinesButton").addEventListener("click", event => autoDetectImportLines(event.currentTarget));
  $("#createLineButton").addEventListener("click", event => createImportLine(event.currentTarget));
  $("#newLineName").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      createImportLine($("#createLineButton"));
    }
  });
  $("#importStep1Next").addEventListener("click", () => goToImportStep(2));
  $("#importStep2Next").addEventListener("click", () => goToImportStep(3));
  $("#importStep3Next").addEventListener("click", () => goToImportStep(4));
  $("#importStep4Next").addEventListener("click", () => goToImportStep(5));
  $$('[data-import-back]').forEach(button => button.addEventListener("click", () => goToImportStep(state.importWizardStep - 1)));
  $("#importForm").addEventListener("submit", submitImport);
  $("#importForm").addEventListener("input", () => {
    if (!state.importPreviewId) {
      updateImportSteps();
      return;
    }
    state.importPreviewId = null;
    const button = $("#startImportButton");
    button.dataset.label = "生成导入预览";
    button.textContent = "生成导入预览";
    $("#importResultPanel").hidden = true;
    updateImportSteps();
  });
  $("#importResult").addEventListener("click", async event => {
    const button = event.target.closest(".retry-cleanup");
    if (!button) return;
    setBusy(button, true, "正在清理…");
    try {
      await request(`/api/import-items/${encodeURIComponent(button.dataset.itemUuid)}/retry-cleanup`, { method: "POST" });
      button.remove();
      notify("源 TDMS 已安全清理");
    } catch (error) {
      notify(error.message, "error", true);
    } finally {
      if (button.isConnected) setBusy(button, false);
    }
  });

  $("#conditionUpdateBusinessToggle").addEventListener("change", event => {
    setUpdateBusinessEnabled("conditions", event.currentTarget.checked);
  });
  $("#pathUpdateBusinessToggle").addEventListener("change", event => {
    setUpdateBusinessEnabled("path", event.currentTarget.checked);
  });
  $("#reconcilePreviewButton").addEventListener("click", previewReconcile);
  $("#reconcileFilePath").addEventListener("input", () => {
    invalidateReconcilePreview();
    queueReconcileFileSearch();
  });
  $("#reconcileFolderPath").addEventListener("change", invalidateReconcilePreview);
  $("#reconcileScope").addEventListener("change", () => {
    updateReconcileScopeFields();
    invalidateReconcilePreview();
  });
  $("#reconcileRows").addEventListener("change", event => {
    if (event.target.classList.contains("reconcile-action")) updateReconcileHint();
  });
  $("#reconcileApplyButton").addEventListener("click", applyReconcile);
  $("#saveScopeConditionsButton").addEventListener("click", saveScopeConditions);
  $$('[data-update-next]').forEach(button => button.addEventListener("click", () => goToUpdateStep(Number(button.dataset.updateNext))));
  $$('[data-update-back]').forEach(button => button.addEventListener("click", () => goToUpdateStep(state.updateWizardStep - 1)));
  $("#confirmUpdateButton").addEventListener("click", confirmWizardUpdate);

  $("#fileFilterForm").addEventListener("submit", async event => {
    event.preventDefault();
    try { await applyFileFilters(); } catch (error) { notify(error.message, "error", true); }
  });
  $("#clearFileFiltersButton").addEventListener("click", async () => {
    $("#fileFilterForm").reset();
    updateAllMultiSelectSummaries($("#fileFilterForm"));
    $$("[data-multi-select]", $("#fileFilterForm")).forEach(details => { details.open = false; });
    try { await applyFileFilters(); } catch (error) { notify(error.message, "error", true); }
  });
  $("#fileFilterForm").addEventListener("change", event => {
    const checkbox = event.target.closest("[data-multi-select-field] input[type=checkbox]");
    if (checkbox) updateMultiSelectSummary(checkbox.closest("[data-multi-select-field]"));
  });
  $("#reloadFilesButton").addEventListener("click", async event => {
    setBusy(event.currentTarget, true, "刷新中…");
    try { await loadFiles(); notify("文件列表已刷新"); } catch (error) { notify(error.message, "error"); } finally { setBusy(event.currentTarget, false); }
  });
  $("#selectAllFiles").addEventListener("change", event => {
    state.filteredFiles.forEach(file => event.target.checked ? state.selectedFiles.add(file.uid) : state.selectedFiles.delete(file.uid));
    renderFiles();
  });
  $("#fileRows").addEventListener("change", event => {
    const checkbox = event.target.closest(".file-select");
    if (!checkbox) return;
    checkbox.checked ? state.selectedFiles.add(checkbox.dataset.uid) : state.selectedFiles.delete(checkbox.dataset.uid);
    renderFiles();
  });
  $("#fileRows").addEventListener("click", event => {
    const button = event.target.closest(".expand-button");
    if (!button) return;
    state.expandedFiles.has(button.dataset.uid) ? state.expandedFiles.delete(button.dataset.uid) : state.expandedFiles.add(button.dataset.uid);
    renderFiles();
  });
  $("#editSelectedConditionsButton").addEventListener("click", () => openUpdateDialog({ selectedFiles: true }));
  document.addEventListener("click", event => {
    const clearMultiSelect = event.target.closest("[data-clear-multi-select]");
    if (clearMultiSelect) {
      const field = clearMultiSelect.closest("[data-multi-select-field]");
      $$("input[type=checkbox]", field).forEach(input => { input.checked = false; });
      updateMultiSelectSummary(field);
    }
    if (!event.target.closest("[data-multi-select]")) {
      $$("[data-multi-select]").forEach(details => { details.open = false; });
    }
    const addButton = event.target.closest(".add-extra-field-button");
    if (addButton) addExtraConditionRow(addButton.closest("[data-condition-editor]"));
    const removeButton = event.target.closest(".remove-extra-field");
    if (removeButton) {
      const row = removeButton.closest(".extra-condition-row");
      const editor = removeButton.closest("[data-condition-editor]");
      const originalKey = row?.dataset.originalExtraKey || "";
      if (editor && originalKey) {
        const deletedKeys = deletedExtraFieldsByEditor.get(editor) || new Set();
        deletedKeys.add(originalKey);
        deletedExtraFieldsByEditor.set(editor, deletedKeys);
      }
      row?.remove();
    }
  });
}

function enhanceLabelTouchSelect(select) {
    if (select._syncTouchPicker) return;
    const label = select.closest("label");
    const labelText = label?.querySelector("span")?.textContent?.trim() || "选择";
    if (label) {
      const field = document.createElement("div");
      field.className = `${label.className} label-touch-field`;
      field.id = label.id;
      field.hidden = label.hidden;
      while (label.firstChild) field.append(label.firstChild);
      label.replaceWith(field);
    }
    const picker = document.createElement("div");
    picker.className = "label-touch-picker";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "label-touch-picker-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", labelText);
    const options = document.createElement("div");
    options.className = "label-touch-picker-options";
    options.setAttribute("role", "listbox");
    options.hidden = true;
    picker.append(trigger, options);
    select.after(picker);
    select.classList.add("label-touch-native-select");
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;
    const sync = () => {
      const fieldName = select.matches("[data-label-result]") ? "结果 result" : select.matches("[data-label-reason]") ? "原因 reason" : "";
      trigger.textContent = `${fieldName ? `${fieldName}：` : ""}${select.selectedOptions[0]?.textContent || "请选择"}`;
      trigger.disabled = select.disabled;
      options.replaceChildren();
      for (const option of select.options) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "label-touch-picker-option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(option.selected));
        item.textContent = select.matches("[data-label-result], [data-label-reason]") ? `${option.textContent}（${option.value}）` : option.textContent;
        item.disabled = option.disabled;
        item.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          select.value = option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          options.hidden = true;
          trigger.setAttribute("aria-expanded", "false");
          sync();
          trigger.focus();
        });
        options.append(item);
      }
    };
    trigger.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      if (select.disabled) return;
      sync();
      const opening = options.hidden;
      document.querySelectorAll(".label-touch-picker-options").forEach(menu => { menu.hidden = true; menu.previousElementSibling?.setAttribute("aria-expanded", "false"); });
      options.hidden = !opening;
      trigger.setAttribute("aria-expanded", String(opening));
    });
    select._syncTouchPicker = sync;
    select.addEventListener("change", sync);
    new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    sync();
}

function enhanceLabelSettingSelects() {
  for (const id of ["labelSourceType", "labelFileType", "labelProjectType", "labelLineSelect", "labelWavProfileSelect", "labelQueueFolderFilter", "labelQueueResultFilter", "labelQueueReasonFilter"]) enhanceLabelTouchSelect($(`#${id}`));
  document.addEventListener("pointerdown", event => {
    if (event.target.closest(".label-touch-picker")) return;
    document.querySelectorAll(".label-touch-picker-options").forEach(menu => { menu.hidden = true; menu.previousElementSibling?.setAttribute("aria-expanded", "false"); });
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".label-touch-picker-options").forEach(menu => { menu.hidden = true; menu.previousElementSibling?.setAttribute("aria-expanded", "false"); });
  });
}

async function start() {
  mountConditionCards();
  bindEvents();
  enhanceLabelSettingSelects();
  restoreLastLabelSettings();
  const initialView = location.hash.slice(1);
  if (initialView === "data-panel") {
    state.dataPanelOpen = true;
    switchView("data-panel");
  }
  else if (initialView === "database") switchView("database");
  else if (initialView === "labeling") switchView("labeling");
  else switchView("databases");
  if (state.currentView === "labeling") {
    try {
      await loadStatus();
      await loadLabelProjects();
      switchLabelWorkflowTab("projects");
    } catch (error) { notify(`标签配置加载失败：${error.message}`, "error", true); }
  } else {
    await refreshAll(false);
  }
  if (["database", "data-panel"].includes(state.currentView) && !selectedDatabase()) {
    switchView("databases");
    notify("数据库不存在或当前不可用", "error", true);
  }
}

document.addEventListener("DOMContentLoaded", start);
