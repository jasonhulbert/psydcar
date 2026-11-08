(() => {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";
  const ICONS = "";
  const byId = (id) => document.getElementById(id);
  const stacked = window.matchMedia("(max-width: 1120px)");
  const model = {
    token: null, sidecars: [], selectedId: null, filter: "", statusFilter: "all",
    watching: false, loading: false, toastTimer: null, provider: { configured: false },
    connectionPreview: null, snapshotKey: "", renderedAt: 0,
  };
  // The files workspace: one sidecar's text files and the file open in the editor.
  const files = {
    sidecarId: null, entries: [], treeKey: "", truncated: false, expanded: new Set(), filter: "", loadingTree: false,
    open: null, editor: null, conflict: null, saving: false, routeHash: "", treeTimer: null,
    preview: readSetting("psydcar.preview", "on") !== "off",
    // Wide screens remember a collapsed file pane; narrow screens show it as a drawer that starts closed.
    collapsed: readSetting("psydcar.files-collapsed", "false") === "true", drawerOpen: false,
  };
  const narrow = window.matchMedia("(max-width: 860px)");
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);

  function readSetting(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
  }

  function writeSetting(key, value) {
    try { localStorage.setItem(key, value); } catch { /* Settings are a convenience. */ }
  }

  // DOM helpers

  function element(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  }

  function add(parent, tag, className, value) {
    const item = element(tag, className, value);
    parent.append(item);
    return item;
  }

  function icon(name, className = "") {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", `icon ${className}`.trim());
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(SVG, "use");
    use.setAttribute("href", `${ICONS}#${name}`);
    svg.append(use);
    return svg;
  }

  function button(parent, className, label, { iconName, focusKey, onClick, ariaLabel } = {}) {
    const item = add(parent, "button", className);
    item.type = "button";
    if (iconName) item.append(icon(iconName));
    if (label) item.append(document.createTextNode(label));
    if (ariaLabel) { item.setAttribute("aria-label", ariaLabel); item.title = ariaLabel; }
    if (focusKey) item.dataset.focusKey = focusKey;
    if (onClick) item.addEventListener("click", onClick);
    return item;
  }

  function setIcon(svg, name) {
    svg.querySelector("use").setAttribute("href", `${ICONS}#${name}`);
  }

  async function copyText(value, trigger, confirmation) {
    try {
      await navigator.clipboard.writeText(value);
      if (trigger) {
        const svg = trigger.querySelector("svg");
        if (svg) { setIcon(svg, "check"); setTimeout(() => setIcon(svg, "copy"), 1400); }
      }
      if (confirmation) message(confirmation);
    } catch {
      message("Copy failed. Select the text and copy it manually.", true);
    }
  }

  // Formatting

  const absoluteFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  function parseDate(value) {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function date(value) {
    const parsed = parseDate(value);
    return parsed ? absoluteFormat.format(parsed) : value || "Never";
  }

  function relative(value) {
    const parsed = parseDate(value);
    if (!parsed) return value || "Never";
    const seconds = (parsed.getTime() - Date.now()) / 1000;
    const abs = Math.abs(seconds);
    if (abs < 45) return "Just now";
    if (abs < 3600) return relativeFormat.format(Math.round(seconds / 60), "minute");
    if (abs < 86400) return relativeFormat.format(Math.round(seconds / 3600), "hour");
    if (abs < 7 * 86400) return relativeFormat.format(Math.round(seconds / 86400), "day");
    const sameYear = parsed.getFullYear() === new Date().getFullYear();
    return parsed.toLocaleDateString(undefined, sameYear ? { month: "short", day: "numeric" } : { dateStyle: "medium" });
  }

  const count = (value) => Number(value || 0).toLocaleString();
  const plural = (value, word) => `${count(value)} ${word}${Number(value) === 1 ? "" : "s"}`;

  // Status

  function status(sidecar) {
    if (sidecar.queued || sidecar.state === "pending") return { label: "Updating", tone: "info", group: "pending", spinner: true };
    if (sidecar.state === "attention" || sidecar.error) return { label: "Needs attention", tone: "danger", group: "attention" };
    if (sidecar.freshness === "stale") return { label: "Stale", tone: "warning", group: "attention" };
    if (sidecar.state === "not_indexed") return { label: "Not indexed", tone: "neutral", group: "none" };
    return { label: "Current", tone: "success", group: "current" };
  }

  function badge(sidecar) {
    const value = status(sidecar);
    const item = element("span", `badge${value.tone === "neutral" ? "" : ` badge-${value.tone}`}`);
    if (value.spinner) item.append(icon("loader-circle", "spin"));
    item.append(document.createTextNode(value.label));
    return item;
  }

  // Notifications

  function message(text, error = false) {
    const toast = byId("toast");
    byId("toast-text").textContent = text;
    byId("toast-icon").setAttribute("href", `${ICONS}#${error ? "circle-alert" : "circle-check"}`);
    toast.classList.toggle("is-error", error);
    toast.hidden = true;
    void toast.offsetWidth;
    toast.hidden = false;
    clearTimeout(model.toastTimer);
    model.toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
  }

  function banner(text) {
    byId("banner-text").textContent = text;
    byId("banner").hidden = !text;
  }

  // Data

  async function request(path, options = {}) {
    const headers = { Accept: "application/json", ...options.headers };
    if (options.method && options.method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-Psydcar-Token"] = model.token;
    }
    const response = await fetch(path, { cache: "no-store", ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `Request failed (${response.status}).`);
      error.hint = payload.hint;
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function applySnapshot(payload, force) {
    const { token, ...snapshot } = payload;
    const key = JSON.stringify(snapshot);
    if (!force && key === model.snapshotKey && Date.now() - model.renderedAt < 30000) return;
    model.snapshotKey = key;
    model.sidecars = snapshot.sidecars || [];
    model.provider = snapshot.provider || { configured: false };
    model.watching = Boolean(snapshot.watching);
    if (model.selectedId && !model.sidecars.some((item) => item.id === model.selectedId)) model.selectedId = null;
    if (!model.selectedId && model.sidecars.length) model.selectedId = model.sidecars[0].id;

    const watch = byId("watch-status");
    watch.classList.toggle("is-on", model.watching);
    watch.classList.toggle("is-off", !model.watching);
    byId("watch-label").textContent = model.watching ? "Watching for changes" : "Watcher stopped";
    watch.title = model.watching ? "Sidecars update automatically when their folders change." : "Automatic updates are off. Use Update on a sidecar.";
    banner(model.watching ? "" : "Automatic updates are unavailable. Use Update on a sidecar, or restart the dashboard.");
    byId("footer-version").textContent = snapshot.version ? `Psydcar ${snapshot.version}` : "Psydcar";
    byId("footer-data-dir").textContent = snapshot.data_dir || "—";
    render();
  }

  async function refresh(first = false, force = false) {
    if (model.loading) return;
    model.loading = true;
    try {
      const payload = await request(first || !model.token ? "/api/bootstrap" : "/api/sidecars");
      if (payload.token) model.token = payload.token;
      applySnapshot(payload, first || force);
    } catch (error) {
      banner(`The dashboard could not reach the local service. ${error.message} Check that psydcar dashboard is still running, then reload.`);
    } finally {
      model.loading = false;
    }
  }

  // Rendering

  function render() {
    if (files.sidecarId) {
      const sidecar = model.sidecars.find((item) => item.id === files.sidecarId);
      if (sidecar) { byId("header-sidecar").textContent = sidecar.name; byId("files-title").textContent = sidecar.name; }
      return;
    }
    const focusKey = document.activeElement?.dataset?.focusKey;
    renderCounts();
    renderSetup();
    renderList();
    renderDetail();
    if (focusKey) document.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    model.renderedAt = Date.now();
  }

  function renderCounts() {
    const groups = model.sidecars.map((item) => status(item).group);
    const setCount = (id, value) => {
      byId(id).textContent = count(value);
      byId(id).classList.toggle("is-zero", value === 0);
    };
    setCount("count-all", groups.length);
    ["current", "pending", "attention"].forEach((group) => {
      setCount(`count-${group}`, groups.filter((item) => item === group).length);
    });
    document.querySelectorAll("#status-filters button").forEach((item) => {
      item.setAttribute("aria-pressed", String(item.dataset.status === model.statusFilter));
    });
  }

  function visibleSidecars() {
    const query = model.filter.trim().toLocaleLowerCase();
    return model.sidecars.filter((item) => {
      if (model.statusFilter !== "all" && status(item).group !== model.statusFilter) return false;
      return `${item.name} ${item.source_path}`.toLocaleLowerCase().includes(query);
    });
  }

  function select(id, reveal) {
    model.selectedId = id;
    renderList();
    renderDetail();
    if (reveal && stacked.matches) document.querySelector(".layout-side").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderList() {
    const rows = byId("sidecar-rows");
    rows.replaceChildren();
    const items = visibleSidecars();
    const empty = model.sidecars.length === 0;
    byId("sidecar-table").hidden = empty || items.length === 0;
    byId("list-toolbar").hidden = empty;
    byId("create-button").hidden = empty;
    byId("layout").classList.toggle("is-empty", empty);
    byId("empty-state").hidden = !empty;
    byId("filter-empty").hidden = empty || items.length !== 0;

    for (const sidecar of items) {
      const selected = sidecar.id === model.selectedId;
      const row = add(rows, "tr", selected ? "is-selected" : "");
      row.addEventListener("click", () => select(sidecar.id, true));

      const nameCell = add(row, "td");
      const name = add(nameCell, "div", "row-name");
      const link = button(name, "row-link", sidecar.name, { focusKey: `row:${sidecar.id}` });
      link.setAttribute("aria-current", String(selected));
      link.addEventListener("keydown", (event) => moveSelection(event, items, sidecar.id));
      const path = add(name, "span", "row-path");
      add(path, "bdi", "", sidecar.source_path);
      path.title = sidecar.source_path;

      add(row, "td").append(badge(sidecar));
      add(row, "td", "num", count(sidecar.coverage.files));
      add(row, "td", "num", count(sidecar.coverage.passages));
      const updated = add(row, "td", "updated", sidecar.update.last_success_at ? relative(sidecar.update.last_success_at) : "Never");
      if (sidecar.update.last_success_at) updated.title = date(sidecar.update.last_success_at);
    }
  }

  function moveSelection(event, items, id) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const index = items.findIndex((item) => item.id === id) + (event.key === "ArrowDown" ? 1 : -1);
    const next = items[index];
    if (!next) return;
    select(next.id, false);
    document.querySelector(`[data-focus-key="row:${CSS.escape(next.id)}"]`)?.focus();
  }

  function section(parent, title) {
    const block = add(parent, "section", "detail-section");
    add(block, "h3", "", title);
    return block;
  }

  function props(parent, rows) {
    const list = add(parent, "dl", "props");
    for (const [label, value, title] of rows) {
      add(list, "dt", "", label);
      const item = add(list, "dd", "", value);
      if (title) item.title = title;
    }
    return list;
  }

  function flash(parent, tone, title, { text, lines } = {}) {
    const box = add(parent, "div", `flash flash-${tone}`);
    box.append(icon(tone === "info" ? "info" : tone === "danger" ? "circle-alert" : "triangle-alert"));
    const body = add(box, "div", "flash-body");
    add(body, "strong", "", title);
    if (lines && lines.length) {
      const list = add(body, "ul");
      for (const line of lines) add(list, "li", "", line);
    }
    if (text) add(body, "p", "", text);
    return box;
  }

  function issuesFor(sidecar) {
    const issues = Array.isArray(sidecar.update.issues)
      ? sidecar.update.issues.map((issue) => typeof issue === "string" ? issue : [issue.source || issue.path, issue.error || issue.message].filter(Boolean).join(": "))
      : [];
    if (sidecar.error && !issues.includes(sidecar.error)) issues.unshift(sidecar.error);
    return issues;
  }

  function guidance(parent, sidecar) {
    if (sidecar.queued || sidecar.state === "pending") {
      flash(parent, "info", "Update in progress", { text: "Search keeps using the current index until the update finishes." });
      return;
    }
    const issues = issuesFor(sidecar);
    if (issues.length || sidecar.state === "attention") {
      const removed = sidecar.status === "source_attention";
      flash(parent, "danger", removed ? "Files were removed from the folder" : "The last update needs attention", {
        lines: issues.length ? issues : [removed ? "A source file was removed from this sidecar's index." : "The last processing pass did not complete."],
        text: removed
          ? "Restore the files if that was unintended, or run Update to accept the change."
          : "Fix the source or provider, then run Update to retry.",
      });
      return;
    }
    if (sidecar.source_drift || sidecar.freshness === "stale") {
      flash(parent, "warning", "Folder changed since the last update", { text: "Search results may be out of date. Run Update to re-index." });
      return;
    }
    if (sidecar.state === "not_indexed") {
      flash(parent, "info", "Not indexed yet", { text: "Run Update to index supported files in this folder." });
    }
  }

  function renderDetail() {
    const sidecar = model.sidecars.find((item) => item.id === model.selectedId);
    const placeholder = byId("detail-placeholder");
    placeholder.hidden = Boolean(sidecar);
    const target = byId("detail-content");
    target.hidden = !sidecar;
    target.replaceChildren();
    if (!sidecar) {
      placeholder.querySelector("h3").textContent = model.sidecars.length ? "No sidecar selected" : "Nothing to show yet";
      placeholder.querySelector("p").textContent = model.sidecars.length
        ? "Select a sidecar to see its coverage, update history, and actions."
        : "Details for a sidecar appear here once you create one.";
      return;
    }

    const state = status(sidecar);
    const head = add(target, "div", "detail-header");
    const title = add(head, "div", "detail-title");
    add(title, "h2", "", sidecar.name);
    title.append(badge(sidecar));
    const path = add(head, "div", "detail-path");
    add(add(path, "span"), "bdi", "", sidecar.source_path);
    const copyPath = button(path, "btn-icon btn-icon-sm", "", { iconName: "copy", ariaLabel: "Copy folder path", focusKey: "detail:copy-path" });
    copyPath.addEventListener("click", () => copyText(sidecar.source_path, copyPath, "Folder path copied."));

    const open = add(target, "a", "detail-open");
    open.href = filesHash(sidecar.id);
    open.dataset.focusKey = "detail:files";
    open.append(icon("folder-open"));
    const openText = add(open, "span", "detail-open-text");
    add(openText, "strong", "", "Browse and edit files");
    add(openText, "small", "", `${plural(sidecar.coverage.text_files, "text file")} indexed`);
    open.append(icon("chevron-right", "detail-open-chevron"));

    const actions = add(target, "div", "detail-actions");
    const needsUpdate = state.group === "attention" || sidecar.state === "not_indexed";
    const update = button(actions, `btn btn-sm${needsUpdate ? " btn-primary" : ""}`, sidecar.queued ? "Updating…" : "Update", {
      focusKey: "detail:update", onClick: () => updateSidecar(sidecar),
    });
    update.prepend(icon(sidecar.queued ? "loader-circle" : "refresh-cw", sidecar.queued ? "spin" : ""));
    update.disabled = Boolean(sidecar.queued);
    button(actions, "btn btn-sm", "Rename", { iconName: "pencil", focusKey: "detail:rename", onClick: () => openRename(sidecar) });
    button(actions, "btn btn-sm btn-danger-quiet", "Remove", { iconName: "trash-2", focusKey: "detail:remove", onClick: () => openRemove(sidecar) });

    guidance(target, sidecar);

    const stats = add(target, "div", "stats");
    for (const [label, value] of [["Files", sidecar.coverage.files], ["Text/code", sidecar.coverage.text_files], ["PDFs", sidecar.coverage.pdfs], ["Passages", sidecar.coverage.passages]]) {
      const stat = add(stats, "div", "stat");
      add(stat, "span", "stat-value", count(value));
      add(stat, "span", "stat-label", label);
    }

    const updates = section(target, "Updates");
    props(updates, [
      ["Last successful", sidecar.update.last_success_at ? relative(sidecar.update.last_success_at) : "Never", sidecar.update.last_success_at ? date(sidecar.update.last_success_at) : ""],
      ["Last attempt", sidecar.update.started_at ? relative(sidecar.update.started_at) : "Never", sidecar.update.started_at ? date(sidecar.update.started_at) : ""],
      ["Folder changes", sidecar.source_drift ? "Detected" : "None"],
    ]);
    const changes = add(updates, "div", "change-list");
    for (const [className, sign, value, word] of [["change-added", "+", sidecar.update.added, "added"], ["change-changed", "~", sidecar.update.changed, "changed"], ["change-removed", "−", sidecar.update.deleted, "removed"]]) {
      const chip = add(changes, "span", Number(value) ? `change ${className}` : "change");
      add(chip, "strong", "", `${sign}${count(value)}`);
      chip.append(document.createTextNode(word));
    }

    if (sidecar.embedding) {
      const embedding = section(target, "Embeddings");
      const expected = Number(sidecar.embedding.expected_count || 0);
      const done = Number(sidecar.embedding.embedded_count || 0);
      props(embedding, [["State", sidecar.embedding.status.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase())]]);
      const bar = add(embedding, "div", "progress");
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", "Embedded passages");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", String(expected));
      bar.setAttribute("aria-valuenow", String(done));
      add(bar, "span").style.width = `${expected ? Math.min(100, (done / expected) * 100) : 0}%`;
      add(embedding, "p", "progress-caption", `${count(done)} of ${plural(expected, "passage")}`);
      if (sidecar.embedding.message) {
        flash(embedding, "danger", "Provider needs attention", { lines: [sidecar.embedding.message], text: "Check the provider settings and credential, then run Update." });
      }
    }

    const pages = (sidecar.pdf_pages || []).filter((page) => page.status !== "indexed");
    if (pages.length) {
      const pdf = section(target, "PDF pages without text");
      flash(pdf, "warning", `${plural(pages.length, "page")} can't be searched`, {
        lines: pages.map((page) => `${page.source}, page ${page.page}: ${page.message || page.status}`),
        text: "These are probably scanned images. Run OCR on the file, then run Update.",
      });
    }

    const identity = section(target, "Sidecar ID");
    const field = add(identity, "div", "id-field");
    add(field, "code", "", sidecar.id).title = sidecar.id;
    const copyId = button(field, "btn-icon btn-icon-sm", "", { iconName: "copy", ariaLabel: "Copy sidecar ID", focusKey: "detail:copy-id" });
    copyId.addEventListener("click", () => copyText(sidecar.id, copyId, "Sidecar ID copied."));
  }

  function renderSetup() {
    const provider = model.provider;
    const state = byId("provider-state");
    state.className = `badge${!provider.configured ? "" : provider.credential_available ? " badge-success" : " badge-warning"}`;
    state.textContent = !provider.configured ? "Not configured" : provider.credential_available ? "Configured" : "Credential missing";
    byId("provider-description").textContent = !provider.configured
      ? "Keyword search runs locally. Add an OpenAI-compatible embeddings endpoint for semantic search."
      : provider.credential_available
        ? "Updates embed passages with this endpoint, and searches rank by meaning."
        : `Set ${provider.api_key_env} in the environment that runs the dashboard, then restart it.`;
    const summary = byId("provider-summary");
    summary.hidden = !provider.configured;
    summary.replaceChildren();
    if (provider.configured) {
      for (const [label, value] of [["Endpoint", provider.base_url], ["Model", provider.model], ["Key variable", provider.api_key_env || "None"]]) {
        add(summary, "dt", "", label);
        add(summary, "dd", "", value);
      }
    }
    byId("provider-button").textContent = provider.configured ? "Edit" : "Configure provider";
    byId("clear-provider-button").hidden = !provider.configured;
    byId("connection-button").disabled = model.sidecars.length === 0;
    byId("connection-description").textContent = model.sidecars.length
      ? "Generate a local MCP server entry that gives a coding agent access to selected sidecars only."
      : "Create a sidecar first. Then generate an MCP server entry limited to the sidecars you choose.";
  }

  // JSON preview

  function highlightJson(target, value) {
    target.replaceChildren();
    const text = JSON.stringify(value, null, 2);
    const pattern = /("(?:\\.|[^"\\])*")(\s*:)?|([{}[\],:])|(\s+)|([^\s{}[\],:"]+)/g;
    let match;
    while ((match = pattern.exec(text))) {
      if (match[1]) {
        add(target, "span", match[2] ? "tok-key" : "tok-string", match[1]);
        if (match[2]) add(target, "span", "tok-punct", match[2]);
      } else if (match[3]) add(target, "span", "tok-punct", match[3]);
      else target.append(document.createTextNode(match[4] || match[5]));
    }
  }

  // Dialogs

  function formError(id, text, input) {
    const box = byId(id);
    box.replaceChildren();
    if (text) {
      box.append(icon("circle-alert"));
      add(box, "span", "flash-body", text);
    }
    box.hidden = !text;
    if (input) input.setAttribute("aria-invalid", String(Boolean(text)));
  }

  function openCreate() {
    byId("create-form").reset();
    formError("create-error", "", byId("create-name"));
    byId("create-source").removeAttribute("aria-invalid");
    byId("create-dialog").showModal();
    byId("create-name").focus();
  }

  function openRename(sidecar) {
    byId("rename-form").dataset.id = sidecar.id;
    byId("rename-name").value = sidecar.name;
    formError("rename-error", "", byId("rename-name"));
    byId("rename-dialog").showModal();
    byId("rename-name").select();
  }

  function openRemove(sidecar) {
    byId("remove-form").dataset.id = sidecar.id;
    byId("remove-name-label").textContent = sidecar.name;
    byId("remove-confirm").value = "";
    byId("remove-submit").disabled = true;
    formError("remove-error", "", byId("remove-confirm"));
    byId("remove-dialog").showModal();
    byId("remove-confirm").focus();
  }

  async function updateSidecar(sidecar) {
    try {
      const result = await request(`/api/sidecars/${sidecar.id}/update`, { method: "POST", body: "{}" });
      message(result.message);
      await refresh(false, true);
    } catch (error) { message(error.message, true); }
  }

  function providerDraft() {
    return {
      base_url: byId("provider-base-url").value.trim(),
      model: byId("provider-model").value.trim(),
      api_key_env: byId("provider-env").value.trim() || null,
      allow_insecure_http: byId("provider-insecure").checked,
    };
  }

  function openProvider() {
    const provider = model.provider;
    byId("provider-base-url").value = provider.base_url || "";
    byId("provider-model").value = provider.model || "";
    byId("provider-env").value = provider.api_key_env || "";
    byId("provider-insecure").checked = Boolean(provider.allow_insecure_http);
    formError("provider-error", "");
    byId("provider-test-result").hidden = true;
    byId("provider-dialog").showModal();
    byId("provider-base-url").focus();
  }

  function updateSelectedCount() {
    const selected = byId("connection-sidecars").querySelectorAll("input:checked").length;
    byId("selected-count").textContent = `${selected} of ${model.sidecars.length} selected`;
  }

  function openConnection() {
    byId("connection-form").reset();
    formError("connection-error", "");
    byId("connection-result").hidden = true;
    model.connectionPreview = null;
    const list = byId("connection-sidecars");
    list.replaceChildren();
    for (const sidecar of model.sidecars) {
      const option = add(list, "label", "picker-option");
      const input = add(option, "input");
      input.type = "checkbox";
      input.value = sidecar.id;
      input.name = "sidecar-id";
      input.addEventListener("change", updateSelectedCount);
      const text = add(option, "span", "picker-option-text");
      add(text, "strong", "", sidecar.name);
      add(text, "small", "", sidecar.source_path).title = `${sidecar.source_path}\n${sidecar.id}`;
    }
    list.hidden = model.sidecars.length === 0;
    byId("connection-empty").hidden = model.sidecars.length !== 0;
    updateSelectedCount();
    byId("connection-dialog").showModal();
    byId("connection-name").focus();
  }

  // Files workspace: routing

  const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");
  const filesHash = (sidecarId, path) => `#/files/${sidecarId}${path ? `/${encodePath(path)}` : ""}`;
  const parentOf = (path) => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  const baseName = (path) => path.slice(path.lastIndexOf("/") + 1);
  const filesApi = (suffix = "files") => `/api/sidecars/${files.sidecarId}/${suffix}`;

  function parseRoute(hash = location.hash) {
    const match = hash.match(/^#\/files\/([0-9a-f-]{36})(?:\/(.+))?$/);
    if (!match) return null;
    try {
      return { sidecarId: match[1], path: match[2] ? match[2].split("/").map(decodeURIComponent).join("/") : null };
    } catch { return { sidecarId: match[1], path: null }; }
  }

  const isDirty = () => Boolean(files.editor && files.editor.isDirty());

  // Ask before dropping unsaved edits. Resolves true when it's safe to continue.
  async function settleChanges(nextLabel) {
    if (!isDirty()) return true;
    const dialog = byId("discard-dialog");
    byId("discard-text").textContent = `${baseName(files.open.path)} has changes that aren't saved. Save them before ${nextLabel}?`;
    dialog.returnValue = "";
    dialog.showModal();
    const choice = await new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue), { once: true }));
    if (choice === "discard") return true;
    if (choice === "save") return saveFile();
    return false;
  }

  async function route() {
    const next = parseRoute();
    const leavingFile = files.open && (!next || next.sidecarId !== files.sidecarId || next.path !== files.open.path);
    if (leavingFile && !(await settleChanges(next?.path ? "opening another file" : "leaving"))) {
      history.replaceState(null, "", files.routeHash || "#");
      return;
    }
    files.routeHash = location.hash;
    if (!next) { closeWorkspace(); return; }
    const sidecar = model.sidecars.find((item) => item.id === next.sidecarId);
    if (model.token && !sidecar) {
      message("That sidecar no longer exists.", true);
      history.replaceState(null, "", "#");
      files.routeHash = "";
      closeWorkspace();
      return;
    }
    const switching = files.sidecarId !== next.sidecarId;
    if (switching) {
      closeFile();
      files.sidecarId = next.sidecarId;
      files.entries = [];
      files.expanded = new Set();
      files.filter = "";
      byId("file-filter").value = "";
    }
    model.selectedId = next.sidecarId;
    showWorkspace(sidecar);
    if (switching || !files.entries.length) await loadTree();
    if (next.path) {
      if (!files.open || files.open.path !== next.path) await openFile(next.path);
    } else {
      closeFile();
    }
    renderWorkspace();
  }

  function showWorkspace(sidecar) {
    document.body.classList.add("is-workspace");
    byId("main").hidden = true;
    byId("files-view").hidden = false;
    byId("header-sidecar-divider").hidden = false;
    byId("header-sidecar").hidden = false;
    byId("header-sidecar").textContent = sidecar ? sidecar.name : "";
    byId("files-title").textContent = sidecar ? sidecar.name : "Files";
    byId("files-root").textContent = sidecar ? sidecar.source_path : "";
    byId("files-root").title = sidecar ? sidecar.source_path : "";
    document.title = `${sidecar ? sidecar.name : "Files"} · Psydcar`;
    clearInterval(files.treeTimer);
    files.treeTimer = setInterval(() => { if (!document.hidden) loadTree(true); }, 4000);
  }

  function closeWorkspace() {
    clearInterval(files.treeTimer);
    closeFile();
    files.sidecarId = null;
    document.body.classList.remove("is-workspace");
    byId("files-view").hidden = true;
    byId("main").hidden = false;
    byId("header-sidecar-divider").hidden = true;
    byId("header-sidecar").hidden = true;
    document.title = "Sidecars · Psydcar";
    render();
  }

  // Files workspace: tree

  async function loadTree(quiet = false) {
    if (!files.sidecarId || files.loadingTree) return;
    files.loadingTree = true;
    const sidecarId = files.sidecarId;
    try {
      const result = await request(filesApi());
      if (sidecarId !== files.sidecarId) return;
      const key = JSON.stringify(result.entries);
      const changed = key !== files.treeKey;
      files.treeKey = key;
      files.entries = result.entries;
      files.truncated = result.truncated;
      if (changed || !quiet) renderTree();
      checkOpenFile();
    } catch (error) {
      if (!quiet) message(error.message, true);
    } finally {
      files.loadingTree = false;
    }
  }

  function buildTree() {
    const root = { path: "", type: "folder", children: [] };
    const folders = new Map([["", root]]);
    for (const entry of files.entries) {
      const node = { ...entry, name: baseName(entry.path), children: [] };
      if (entry.type === "folder") folders.set(entry.path, node);
      (folders.get(parentOf(entry.path)) || root).children.push(node);
    }
    const order = (a, b) => (a.type === b.type ? a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }) : a.type === "folder" ? -1 : 1);
    for (const folder of folders.values()) folder.children.sort(order);
    return root;
  }

  function fileIcon(path) {
    return /\.(md|markdown|txt|rst)$/i.test(path) ? "file-text" : "file-code";
  }

  function renderTree() {
    const tree = byId("file-tree");
    const focusPath = document.activeElement?.closest?.(".tree-row")?.dataset.path;
    tree.replaceChildren();
    const query = files.filter.trim().toLocaleLowerCase();
    const rows = [];
    if (query) {
      for (const entry of files.entries) {
        if (entry.type === "file" && entry.path.toLocaleLowerCase().includes(query)) rows.push({ node: { ...entry, name: entry.path }, depth: 0 });
      }
    } else {
      const walk = (folder, depth) => {
        for (const node of folder.children) {
          rows.push({ node, depth });
          if (node.type === "folder" && files.expanded.has(node.path)) walk(node, depth + 1);
        }
      };
      walk(buildTree(), 0);
    }
    for (const { node, depth } of rows) treeRow(tree, node, depth);
    const hasFiles = files.entries.some((entry) => entry.type === "file");
    byId("file-tree-empty").hidden = files.entries.length > 0;
    tree.hidden = files.entries.length === 0;
    const note = byId("file-tree-note");
    note.hidden = !(files.truncated || (query && rows.length === 0));
    note.textContent = files.truncated
      ? "This folder has more than 5,000 entries. Only the first 5,000 are listed."
      : hasFiles ? "No files match this filter." : "";
    if (focusPath) tree.querySelector(`.tree-row[data-path="${CSS.escape(focusPath)}"] .tree-item`)?.focus({ preventScroll: true });
  }

  function treeRow(tree, node, depth) {
    const folder = node.type === "folder";
    const expanded = files.expanded.has(node.path);
    const active = !folder && files.open?.path === node.path;
    const row = add(tree, "div", `tree-row${active ? " is-active" : ""}`);
    row.dataset.path = node.path;
    row.dataset.type = node.type;
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-level", String(depth + 1));
    row.setAttribute("aria-selected", String(active));
    if (folder) row.setAttribute("aria-expanded", String(expanded));
    row.style.setProperty("--depth", String(depth));

    const item = add(row, "button", "tree-item");
    item.type = "button";
    item.tabIndex = -1;
    if (folder) item.append(icon("chevron-right", `tree-chevron${expanded ? " is-open" : ""}`));
    item.append(icon(folder ? (expanded ? "folder-open" : "folder") : fileIcon(node.path), "tree-icon"));
    add(item, "span", "tree-name", node.name).title = node.path;
    if (!folder && node.editable === false) add(item, "span", "tree-flag", "Too large");
    item.addEventListener("click", () => {
      if (folder) toggleFolder(node.path);
      else if (node.editable === false) message(`${node.path} is larger than 2 MiB, so it can't be edited here.`, true);
      else {
        closeDrawer();
        location.hash = filesHash(files.sidecarId, node.path);
      }
    });
    item.addEventListener("keydown", (event) => treeKey(event, node));

    const actions = add(row, "span", "tree-actions");
    if (folder) {
      button(actions, "btn-icon btn-icon-sm", "", { iconName: "file-plus", ariaLabel: `New file in ${node.path}`, onClick: () => openFileCreate("file", node.path) });
    }
    button(actions, "btn-icon btn-icon-sm", "", { iconName: "pencil", ariaLabel: `Rename or move ${node.path}`, onClick: () => openFileMove(node) });
    button(actions, "btn-icon btn-icon-sm btn-icon-danger", "", { iconName: "trash-2", ariaLabel: `Delete ${node.path}`, onClick: () => openFileDelete(node) });
    for (const action of actions.querySelectorAll("button")) action.tabIndex = -1;
  }

  // Roving focus: one tree item is tabbable; arrows move between rows and open or close folders.
  function syncTreeFocus() {
    const items = [...byId("file-tree").querySelectorAll(".tree-item")];
    const current = items.find((item) => item === document.activeElement)
      || byId("file-tree").querySelector(".tree-row.is-active .tree-item") || items[0];
    for (const item of items) item.tabIndex = item === current ? 0 : -1;
  }

  function treeKey(event, node) {
    const items = [...byId("file-tree").querySelectorAll(".tree-item")];
    const index = items.indexOf(event.currentTarget);
    const focus = (target) => { if (target) { target.focus(); syncTreeFocus(); } };
    if (event.key === "ArrowDown") { event.preventDefault(); focus(items[index + 1]); }
    else if (event.key === "ArrowUp") { event.preventDefault(); focus(items[index - 1]); }
    else if (event.key === "Home") { event.preventDefault(); focus(items[0]); }
    else if (event.key === "End") { event.preventDefault(); focus(items[items.length - 1]); }
    else if (event.key === "ArrowRight" && node.type === "folder" && !files.expanded.has(node.path)) { event.preventDefault(); toggleFolder(node.path); }
    else if (event.key === "ArrowLeft" && node.type === "folder" && files.expanded.has(node.path)) { event.preventDefault(); toggleFolder(node.path); }
    else if (event.key === "ArrowLeft" && parentOf(node.path)) {
      event.preventDefault();
      focus(byId("file-tree").querySelector(`.tree-row[data-path="${CSS.escape(parentOf(node.path))}"] .tree-item`));
    } else if (event.key === "F2") { event.preventDefault(); openFileMove(node); }
    else if (event.key === "Delete" || (isMac && event.key === "Backspace" && event.metaKey)) { event.preventDefault(); openFileDelete(node); }
  }

  function toggleFolder(path) {
    if (files.expanded.has(path)) files.expanded.delete(path);
    else files.expanded.add(path);
    renderTree();
    byId("file-tree").querySelector(`.tree-row[data-path="${CSS.escape(path)}"] .tree-item`)?.focus();
    syncTreeFocus();
  }

  function expandTo(path) {
    let folder = parentOf(path);
    while (folder) { files.expanded.add(folder); folder = parentOf(folder); }
  }

  // Files workspace: editor

  let editorScript = null;
  function loadEditor() {
    if (window.PsydcarEditor) return Promise.resolve();
    editorScript ??= new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/assets/editor.js";
      script.onload = resolve;
      script.onerror = () => { editorScript = null; reject(new Error("The editor couldn't load. Reload the page and try again.")); };
      document.head.append(script);
    });
    return editorScript;
  }

  async function openFile(path) {
    const sidecarId = files.sidecarId;
    showEditorMessage("loader-circle", "Opening…", path);
    try {
      const [file] = await Promise.all([request(`${filesApi("file")}?path=${encodeURIComponent(path)}`), loadEditor()]);
      if (sidecarId !== files.sidecarId || parseRoute()?.path !== path) return;
      closeFile();
      files.open = file;
      files.editor = window.PsydcarEditor.create({
        parent: byId("editor-surface"), doc: file.content, path: file.path, preview: files.preview,
        nonce: document.querySelector('meta[name="psydcar-style-nonce"]')?.content,
        onChange: renderEditorState, onSave: () => saveFile(),
      });
      expandTo(file.path);
      renderWorkspace();
      files.editor.focus();
    } catch (error) {
      closeFile();
      showEditorMessage("circle-alert", "This file can't be opened", error.message);
      renderTree();
    }
  }

  function closeFile() {
    if (files.editor) files.editor.destroy();
    files.editor = null;
    files.open = null;
    files.conflict = null;
    byId("editor-surface").replaceChildren();
    showEditorMessage("file-text", "Open a file to edit it",
      "Saving writes straight to the file in your folder. Psydcar re-indexes it right away, so agents see the change.");
  }

  function showEditorMessage(iconName, title, text) {
    byId("editor-empty-icon").setAttribute("href", `#${iconName}`);
    byId("editor-empty-icon").parentElement.classList.toggle("spin", iconName === "loader-circle");
    byId("editor-empty-title").textContent = title;
    byId("editor-empty-text").textContent = text;
  }

  // File pane visibility. Without an open file the pane is the whole view, so it can't be hidden.
  function renderSidebar() {
    const view = byId("files-view");
    if (!files.open || !narrow.matches) files.drawerOpen = false;
    view.classList.toggle("is-collapsed", files.collapsed);
    view.classList.toggle("is-drawer-open", files.drawerOpen);
    const shown = narrow.matches ? files.drawerOpen : !files.collapsed;
    const toggle = byId("files-toggle-button");
    const label = shown ? "Hide files" : "Show files";
    toggle.setAttribute("aria-expanded", String(shown));
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
    setIcon(toggle.querySelector("svg"), shown ? "panel-left-close" : "panel-left-open");
    byId("file-drawer-backdrop").hidden = !files.drawerOpen;
  }

  function toggleSidebar() {
    if (!files.open) return;
    if (narrow.matches) {
      files.drawerOpen = !files.drawerOpen;
      renderSidebar();
      if (files.drawerOpen) (byId("file-tree").querySelector('.tree-item[tabindex="0"]') || byId("file-filter")).focus();
    } else {
      files.collapsed = !files.collapsed;
      writeSetting("psydcar.files-collapsed", String(files.collapsed));
      renderSidebar();
    }
  }

  function closeDrawer(focusToggle = false) {
    if (!files.drawerOpen) return;
    files.drawerOpen = false;
    renderSidebar();
    if (focusToggle) byId("files-toggle-button").focus();
  }

  function renderWorkspace() {
    const open = files.open;
    byId("files-view").classList.toggle("has-file", Boolean(open));
    renderSidebar();
    byId("editor-toolbar").hidden = !open;
    byId("editor-surface").hidden = !open;
    byId("editor-empty").hidden = Boolean(open);
    byId("editor-status").hidden = !open;
    renderTree();
    syncTreeFocus();
    if (!open) { byId("editor-conflict").hidden = true; return; }
    byId("editor-path").replaceChildren();
    const parts = open.path.split("/");
    parts.forEach((part, index) => {
      if (index) add(byId("editor-path"), "span", "editor-path-sep", "/");
      add(byId("editor-path"), "span", index === parts.length - 1 ? "editor-path-name" : "", part);
    });
    byId("editor-path").title = open.path;
    byId("editor-path-icon").setAttribute("href", `#${fileIcon(open.path)}`);
    const toggle = byId("preview-toggle");
    toggle.hidden = !files.editor.isMarkdown;
    for (const item of toggle.querySelectorAll("button")) item.setAttribute("aria-pressed", String((item.dataset.preview === "on") === files.editor.preview));
    renderEditorState();
    renderConflict();
  }

  function renderEditorState() {
    if (!files.open) return;
    const dirty = isDirty();
    const stateLabel = byId("editor-state");
    stateLabel.textContent = files.saving ? "Saving…" : dirty ? "Unsaved changes" : "Saved";
    stateLabel.className = `editor-state${dirty ? " is-dirty" : ""}`;
    byId("save-button").disabled = !dirty || files.saving;
    document.title = `${dirty ? "● " : ""}${baseName(files.open.path)} · Psydcar`;
    const status = byId("editor-status");
    status.replaceChildren();
    for (const value of [files.editor.language, files.open.newline === "\r\n" ? "CRLF" : "LF", "UTF-8", formatBytes(files.open.size), `Modified ${relative(files.open.modified_at)}`]) {
      add(status, "span", "", value);
    }
  }

  function formatBytes(value) {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  async function saveFile({ overwrite = false } = {}) {
    if (!files.open || files.saving) return false;
    if (!isDirty() && !overwrite) return true;
    if (files.conflict && !overwrite) { renderConflict(); return false; }
    const open = files.open;
    const doc = files.editor.snapshot();
    files.saving = true;
    renderEditorState();
    try {
      if (overwrite && files.conflict?.deleted) {
        await request(filesApi(), { method: "POST", body: JSON.stringify({ path: open.path, type: "file" }) });
        files.conflict.version = (await request(`${filesApi("file")}?path=${encodeURIComponent(open.path)}`)).version;
      }
      const version = overwrite && files.conflict ? files.conflict.version : open.version;
      const result = await request(filesApi("file"), {
        method: "PUT",
        body: JSON.stringify({ path: open.path, content: doc.toString(), version, newline: open.newline, bom: open.bom }),
      });
      if (files.open !== open) return true;
      Object.assign(open, { version: result.version, size: result.size, mtime_ns: result.mtime_ns, modified_at: result.modified_at });
      files.editor.markSaved(doc);
      files.conflict = null;
      renderConflict();
      return true;
    } catch (error) {
      if (error.status === 409 && files.open === open) {
        files.conflict = { version: error.payload.version, deleted: error.payload.version === null };
        renderConflict();
      } else {
        message(error.message, true);
      }
      return false;
    } finally {
      files.saving = false;
      renderEditorState();
    }
  }

  function renderConflict() {
    const box = byId("editor-conflict");
    const conflict = files.conflict;
    box.hidden = !conflict;
    if (!conflict) return;
    const name = baseName(files.open.path);
    byId("conflict-title").textContent = conflict.deleted ? `${name} was moved or deleted on disk` : `${name} changed on disk`;
    byId("conflict-text").textContent = conflict.deleted
      ? "Your edits are still here. Recreate the file with your version, or close it."
      : "Another app changed this file while you were editing. Reload to see the new version and lose your edits, or keep your version and overwrite the file.";
    byId("conflict-reload-button").textContent = conflict.deleted ? "Close file" : "Reload from disk";
    byId("conflict-keep-button").textContent = conflict.deleted ? "Recreate with my version" : "Keep my version";
  }

  // Compare the open file with the latest listing, and react to changes made outside the dashboard.
  async function checkOpenFile() {
    const open = files.open;
    if (!open || files.saving) return;
    const entry = files.entries.find((item) => item.path === open.path && item.type === "file");
    if (!entry) {
      if (!files.conflict) { files.conflict = { version: null, deleted: true }; renderConflict(); }
      return;
    }
    if (files.conflict?.deleted) { files.conflict = null; renderConflict(); }
    if (entry.mtime_ns === open.mtime_ns && entry.size === open.size) return;
    try {
      const latest = await request(`${filesApi("file")}?path=${encodeURIComponent(open.path)}`);
      if (files.open !== open || files.saving) return;
      if (latest.version === open.version) {
        Object.assign(open, { mtime_ns: latest.mtime_ns, size: latest.size, modified_at: latest.modified_at });
        return;
      }
      if (isDirty()) {
        files.conflict = { version: latest.version, deleted: false };
        renderConflict();
        return;
      }
      files.open = latest;
      files.conflict = null;
      files.editor.load(latest.content);
      renderWorkspace();
      message(`Reloaded ${baseName(latest.path)} because it changed on disk.`);
    } catch { /* The next listing retries. */ }
  }

  async function reloadFromDisk() {
    if (!files.open) return;
    if (files.conflict?.deleted) {
      files.editor.markSaved();
      location.hash = filesHash(files.sidecarId);
      return;
    }
    try {
      const latest = await request(`${filesApi("file")}?path=${encodeURIComponent(files.open.path)}`);
      files.open = latest;
      files.conflict = null;
      files.editor.load(latest.content);
      renderWorkspace();
      files.editor.focus();
    } catch (error) { message(error.message, true); }
  }

  // Files workspace: create, move, delete

  function openFileCreate(type, folder = "") {
    if (folder === "" && files.open) folder = parentOf(files.open.path);
    const form = byId("file-create-form");
    form.dataset.type = type;
    byId("file-create-title").textContent = type === "folder" ? "New folder" : "New file";
    byId("file-create-submit").textContent = type === "folder" ? "Create folder" : "Create file";
    byId("file-create-hint").textContent = type === "folder"
      ? "A path inside this sidecar's folder. Use / to nest folders."
      : "Use a text extension Psydcar indexes, such as .md, .txt, or .py. Missing folders are created.";
    const prefix = folder ? `${folder}/` : "";
    const input = byId("file-create-path");
    input.value = type === "folder" ? prefix : `${prefix}untitled.md`;
    input.placeholder = type === "folder" ? "notes/drafts" : "notes/ideas.md";
    formError("file-create-error", "", input);
    byId("file-create-dialog").showModal();
    input.focus();
    // Select the editable part: the name without its extension.
    const start = prefix.length;
    const end = type === "folder" ? input.value.length : input.value.lastIndexOf(".");
    input.setSelectionRange(start, end > start ? end : input.value.length);
  }

  function openFileMove(node) {
    const form = byId("file-move-form");
    form.dataset.path = node.path;
    form.dataset.type = node.type;
    byId("file-move-title").textContent = node.type === "folder" ? "Rename or move folder" : "Rename or move file";
    const input = byId("file-move-path");
    input.value = node.path;
    formError("file-move-error", "", input);
    byId("file-move-dialog").showModal();
    input.focus();
    const start = node.path.lastIndexOf("/") + 1;
    const dot = node.type === "file" ? node.path.lastIndexOf(".") : -1;
    input.setSelectionRange(start, dot > start ? dot : node.path.length);
  }

  function openFileDelete(node) {
    const folder = node.type === "folder";
    const hasChildren = folder && files.entries.some((entry) => entry.path.startsWith(`${node.path}/`));
    const form = byId("file-delete-form");
    form.dataset.path = node.path;
    byId("file-delete-title").textContent = folder ? "Delete folder" : "Delete file";
    const text = byId("file-delete-text");
    text.replaceChildren();
    if (hasChildren) {
      text.append(`${node.path} isn't empty. Move or delete what's inside it first. Psydcar only deletes empty folders.`);
    } else {
      text.append(`This permanently deletes `);
      add(text, "strong", "mono", node.path);
      text.append(` from your folder. It doesn't go to the Trash and can't be undone.`);
    }
    byId("file-delete-submit").hidden = hasChildren;
    byId("file-delete-submit").textContent = folder ? "Delete folder" : "Delete file";
    formError("file-delete-error", "");
    byId("file-delete-dialog").showModal();
    (hasChildren ? byId("file-delete-dialog").querySelector("[data-close]") : byId("file-delete-submit")).focus();
  }

  // Events

  byId("create-button").addEventListener("click", openCreate);
  byId("empty-create-button").addEventListener("click", openCreate);
  byId("provider-button").addEventListener("click", openProvider);
  byId("connection-button").addEventListener("click", openConnection);
  byId("clear-provider-button").addEventListener("click", () => {
    formError("clear-provider-error", "");
    byId("clear-provider-dialog").showModal();
  });
  byId("filter-input").addEventListener("input", (event) => { model.filter = event.target.value; renderList(); });
  byId("status-filters").addEventListener("click", (event) => {
    const target = event.target.closest("button[data-status]");
    if (!target) return;
    model.statusFilter = target.dataset.status;
    renderCounts();
    renderList();
  });
  byId("clear-filter-button").addEventListener("click", () => {
    model.filter = "";
    model.statusFilter = "all";
    byId("filter-input").value = "";
    renderCounts();
    renderList();
  });
  document.querySelectorAll("[data-close]").forEach((item) => item.addEventListener("click", () => item.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  }));
  byId("toast").addEventListener("click", () => { byId("toast").hidden = true; });

  byId("provider-form").addEventListener("input", () => { byId("provider-test-result").hidden = true; formError("provider-error", ""); });
  byId("connection-form").addEventListener("input", () => { byId("connection-result").hidden = true; model.connectionPreview = null; formError("connection-error", ""); });
  byId("remove-confirm").addEventListener("input", (event) => {
    const sidecar = model.sidecars.find((item) => item.id === byId("remove-form").dataset.id);
    byId("remove-submit").disabled = !sidecar || event.target.value !== sidecar.name;
  });

  byId("provider-test-button").addEventListener("click", async () => {
    const trigger = byId("provider-test-button");
    trigger.disabled = true;
    formError("provider-error", "");
    byId("provider-test-result").hidden = true;
    try {
      const result = await request("/api/provider/test", { method: "POST", body: JSON.stringify(providerDraft()) });
      const box = byId("provider-test-result");
      box.textContent = `${result.message} Vector dimensions: ${result.dimensions}.`;
      box.hidden = false;
    } catch (error) { formError("provider-error", `${error.message}${error.hint ? ` ${error.hint}` : ""}`); }
    finally { trigger.disabled = false; }
  });

  byId("provider-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = byId("provider-save-button");
    submit.disabled = true;
    formError("provider-error", "");
    try {
      const result = await request("/api/provider", { method: "POST", body: JSON.stringify(providerDraft()) });
      byId("provider-dialog").close();
      message(result.message);
      await refresh(false, true);
    } catch (error) { formError("provider-error", `${error.message}${error.hint ? ` ${error.hint}` : ""}`); }
    finally { submit.disabled = false; }
  });

  byId("clear-provider-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request("/api/provider", { method: "DELETE", body: JSON.stringify({ confirm: "clear" }) });
      byId("clear-provider-dialog").close();
      message(result.message);
      await refresh(false, true);
    } catch (error) { formError("clear-provider-error", error.message); }
    finally { submit.disabled = false; }
  });

  byId("connection-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = byId("connection-name").value.trim();
    const sidecarIds = Array.from(byId("connection-sidecars").querySelectorAll("input:checked"), (input) => input.value);
    if (!name) { formError("connection-error", "Enter a server name.", byId("connection-name")); byId("connection-name").focus(); return; }
    if (!sidecarIds.length) { formError("connection-error", "Select at least one sidecar."); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    byId("connection-result").hidden = true;
    model.connectionPreview = null;
    try {
      const preview = await request("/api/connections/preview", { method: "POST", body: JSON.stringify({ name, sidecar_ids: sidecarIds }) });
      model.connectionPreview = preview;
      byId("result-name").textContent = preview.name;
      byId("result-selection").textContent = `Limited to ${preview.selected.map((item) => item.name).join(", ")}.`;
      highlightJson(byId("connection-json"), preview.config);
      byId("result-footnote").textContent = `Upgrading Psydcar doesn't break this entry. If you remove a selected sidecar, the server won't start until you generate a new entry.${model.provider.api_key_env ? ` For embeddings, set ${model.provider.api_key_env} in the MCP client's environment. Its value is never copied here.` : ""}`;
      byId("connection-result").hidden = false;
      byId("connection-result").scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (error) { formError("connection-error", error.message); }
    finally { submit.disabled = false; }
  });

  byId("copy-connection-button").addEventListener("click", (event) => {
    if (!model.connectionPreview) return;
    copyText(JSON.stringify(model.connectionPreview.config, null, 2), event.currentTarget, "Configuration copied.");
  });

  byId("create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = byId("create-name").value.trim();
    const source = byId("create-source").value.trim();
    if (!name) { formError("create-error", "Enter a name for this sidecar.", byId("create-name")); byId("create-name").focus(); return; }
    if (!source) { formError("create-error", "Enter the path to an existing folder.", byId("create-source")); byId("create-source").focus(); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request("/api/sidecars", { method: "POST", body: JSON.stringify({ name, source }) });
      model.selectedId = result.id;
      byId("create-dialog").close();
      message(result.message);
      await refresh(false, true);
    } catch (error) { formError("create-error", error.message); }
    finally { submit.disabled = false; }
  });

  byId("rename-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = byId("rename-name").value.trim();
    if (!name) { formError("rename-error", "Enter a new name.", byId("rename-name")); byId("rename-name").focus(); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request(`/api/sidecars/${event.target.dataset.id}/rename`, { method: "POST", body: JSON.stringify({ name }) });
      byId("rename-dialog").close();
      message(result.message);
      await refresh(false, true);
    } catch (error) { formError("rename-error", error.message, byId("rename-name")); }
    finally { submit.disabled = false; }
  });

  byId("remove-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const sidecar = model.sidecars.find((item) => item.id === event.target.dataset.id);
    if (!sidecar || byId("remove-confirm").value !== sidecar.name) { formError("remove-error", "Type the sidecar name exactly to confirm.", byId("remove-confirm")); return; }
    const submit = byId("remove-submit");
    submit.disabled = true;
    try {
      const result = await request(`/api/sidecars/${sidecar.id}`, { method: "DELETE", body: JSON.stringify({ confirm_id: sidecar.id }) });
      model.selectedId = null;
      byId("remove-dialog").close();
      message(result.message);
      await refresh(false, true);
    } catch (error) { formError("remove-error", error.message); }
    finally { submit.disabled = false; }
  });

  byId("new-file-button").addEventListener("click", () => openFileCreate("file"));
  byId("empty-new-file-button").addEventListener("click", () => openFileCreate("file"));
  byId("new-folder-button").addEventListener("click", () => openFileCreate("folder"));
  byId("reload-tree-button").addEventListener("click", () => loadTree());
  byId("files-toggle-button").addEventListener("click", toggleSidebar);
  byId("file-drawer-backdrop").addEventListener("click", () => closeDrawer(true));
  byId("file-pane").addEventListener("keydown", (event) => {
    if (event.key === "Escape" && files.drawerOpen) { event.preventDefault(); closeDrawer(true); }
  });
  narrow.addEventListener("change", renderSidebar);
  byId("file-filter").addEventListener("input", (event) => { files.filter = event.target.value; renderTree(); syncTreeFocus(); });
  byId("save-button").addEventListener("click", () => saveFile());
  byId("save-shortcut").textContent = isMac ? "⌘S" : "Ctrl+S";
  byId("conflict-reload-button").addEventListener("click", reloadFromDisk);
  byId("conflict-keep-button").addEventListener("click", () => saveFile({ overwrite: true }));
  byId("preview-toggle").addEventListener("click", (event) => {
    const target = event.target.closest("button[data-preview]");
    if (!target || !files.editor) return;
    files.preview = target.dataset.preview === "on";
    writeSetting("psydcar.preview", files.preview ? "on" : "off");
    files.editor.setPreview(files.preview);
    renderWorkspace();
    files.editor.focus();
  });
  byId("header-sidecars").addEventListener("click", (event) => {
    if (!parseRoute()) return;
    event.preventDefault();
    location.hash = "";
  });

  byId("file-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const type = event.target.dataset.type;
    const input = byId("file-create-path");
    const path = input.value.trim().replace(/^\/+|\/+$/g, "");
    if (!path) { formError("file-create-error", "Enter a path.", input); return; }
    const submit = byId("file-create-submit");
    submit.disabled = true;
    try {
      const result = await request(filesApi(), { method: "POST", body: JSON.stringify({ path, type }) });
      byId("file-create-dialog").close();
      expandTo(result.path);
      if (type === "folder") files.expanded.add(result.path);
      await loadTree();
      if (type === "file") location.hash = filesHash(files.sidecarId, result.path);
      else message(result.message);
    } catch (error) { formError("file-create-error", error.message, input); }
    finally { submit.disabled = false; }
  });

  byId("file-move-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const from = event.target.dataset.path;
    const input = byId("file-move-path");
    const to = input.value.trim().replace(/^\/+|\/+$/g, "");
    if (!to) { formError("file-move-error", "Enter a path.", input); return; }
    if (to === from) { byId("file-move-dialog").close(); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request(filesApi("files/move"), { method: "POST", body: JSON.stringify({ from, to }) });
      byId("file-move-dialog").close();
      if (files.expanded.delete(from)) files.expanded.add(result.path);
      expandTo(result.path);
      // Keep the open file (and its unsaved edits) when it or a folder above it moves.
      if (files.open && (files.open.path === from || files.open.path.startsWith(`${from}/`))) {
        files.open.path = result.path + files.open.path.slice(from.length);
        files.routeHash = filesHash(files.sidecarId, files.open.path);
        history.replaceState(null, "", files.routeHash);
        renderWorkspace();
      }
      await loadTree();
      message(result.message);
    } catch (error) { formError("file-move-error", error.message, input); }
    finally { submit.disabled = false; }
  });

  byId("file-delete-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const path = event.target.dataset.path;
    const submit = byId("file-delete-submit");
    submit.disabled = true;
    try {
      const result = await request(filesApi(), { method: "DELETE", body: JSON.stringify({ path, confirm_path: path }) });
      byId("file-delete-dialog").close();
      if (files.open?.path === path) {
        files.editor.markSaved();
        files.routeHash = filesHash(files.sidecarId);
        history.replaceState(null, "", files.routeHash);
        closeFile();
      }
      files.expanded.delete(path);
      await loadTree();
      renderWorkspace();
      message(result.message);
    } catch (error) { formError("file-delete-error", error.message); }
    finally { submit.disabled = false; }
  });

  window.addEventListener("hashchange", route);
  window.addEventListener("beforeunload", (event) => {
    if (isDirty()) { event.preventDefault(); event.returnValue = ""; }
  });

  refresh(true).then(route);
  setInterval(() => { if (!document.hidden) refresh(); }, 2500);
})();
