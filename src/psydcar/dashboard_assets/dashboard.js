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
    proposals: { sidecarId: null, key: "", pending: [], reviewed: [], loaded: false, expanded: false },
    review: null,
  };

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

  const ago = (value) => { const text = relative(value); return text === "Just now" ? "just now" : text; };
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
    hideToast();
    void toast.offsetWidth;
    toast.hidden = false;
    // As a popover the toast joins the top layer, so it shows above an open dialog.
    if (toast.showPopover) toast.showPopover();
    clearTimeout(model.toastTimer);
    model.toastTimer = setTimeout(hideToast, 5000);
  }

  function hideToast() {
    const toast = byId("toast");
    toast.hidden = true;
    if (toast.hidePopover && toast.matches(":popover-open")) toast.hidePopover();
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
    syncProposals();
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
    const focusKey = document.activeElement?.dataset?.focusKey;
    renderCounts();
    renderReviewCallout();
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
    syncProposals();
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
      if (sidecar.pending_proposals) {
        const line = element("div", "row-name-line");
        name.prepend(line);
        line.append(link);
        const pill = add(line, "span", "review-pill");
        pill.title = `${plural(sidecar.pending_proposals, "proposed edit")} to review`;
        pill.append(icon("git-pull-request"));
        add(pill, "span", "", count(sidecar.pending_proposals)).setAttribute("aria-hidden", "true");
        add(pill, "span", "visually-hidden", pill.title);
      }
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

    renderProposals(target, sidecar);

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
    const selected = byId("connection-sidecars").querySelectorAll('input[name="sidecar-id"]:checked').length;
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
      const row = add(list, "div", "picker-row");
      const option = add(row, "label", "picker-option");
      const input = add(option, "input");
      input.type = "checkbox";
      input.value = sidecar.id;
      input.name = "sidecar-id";
      const text = add(option, "span", "picker-option-text");
      add(text, "strong", "", sidecar.name);
      add(text, "small", "", sidecar.source_path).title = `${sidecar.source_path}\n${sidecar.id}`;
      const grantLabel = add(row, "label", "picker-grant");
      const grant = add(grantLabel, "input");
      grant.type = "checkbox";
      grant.name = "proposal-id";
      grant.value = sidecar.id;
      grant.disabled = true;
      grant.setAttribute("aria-label", `Allow edit proposals for ${sidecar.name}`);
      add(grantLabel, "span", "", "Allow proposals");
      input.addEventListener("change", () => {
        grant.disabled = !input.checked;
        if (!input.checked) grant.checked = false;
        updateSelectedCount();
      });
    }
    list.hidden = model.sidecars.length === 0;
    byId("connection-empty").hidden = model.sidecars.length !== 0;
    updateSelectedCount();
    byId("connection-dialog").showModal();
    byId("connection-name").focus();
  }

  // Proposals

  const byCreated = (a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0);
  const pendingTotal = () => model.sidecars.reduce((total, item) => total + Number(item.pending_proposals || 0), 0);

  function renderReviewCallout() {
    const total = pendingTotal();
    const waiting = model.sidecars.filter((item) => item.pending_proposals);
    byId("review-callout").hidden = total === 0;
    document.title = total ? `(${total}) Sidecars · Psydcar` : "Sidecars · Psydcar";
    if (!total) return;
    byId("review-callout-title").textContent = `${plural(total, "proposed edit")} ${total === 1 ? "is" : "are"} waiting for review`;
    const names = waiting.map((item) => item.name);
    const where = names.length <= 2 ? names.join(" and ") : `${names.slice(0, 2).join(", ")}, and ${plural(names.length - 2, "other sidecar")}`;
    byId("review-callout-text").textContent = `Agents proposed changes in ${where}. Files change only when you approve.`;
  }

  function syncProposals() {
    // A changed snapshot can mean a new or decided proposal, or a source edit that
    // leaves a proposal out of date, so reload the selected sidecar's queue with it.
    const sidecar = model.sidecars.find((item) => item.id === model.selectedId);
    if (!sidecar) return;
    const key = `${sidecar.id}:${model.snapshotKey}`;
    if (key !== model.proposals.key) loadProposals(sidecar.id, key);
  }

  async function loadProposals(sidecarId, key) {
    if (model.proposals.sidecarId !== sidecarId) {
      model.proposals = { sidecarId, key, pending: [], reviewed: [], loaded: false, expanded: false };
    } else {
      model.proposals.key = key;
    }
    try {
      const result = await request(`/api/sidecars/${sidecarId}/proposals`);
      if (model.proposals.sidecarId !== sidecarId) return;
      model.proposals.pending = (result.pending || []).sort(byCreated);
      model.proposals.reviewed = result.reviewed || [];
      model.proposals.loaded = true;
    } catch (error) {
      if (model.proposals.sidecarId !== sidecarId) return;
      model.proposals.key = "";
      model.proposals.error = error.message;
    }
    if (model.selectedId === sidecarId) {
      const focusKey = document.activeElement?.dataset?.focusKey;
      renderDetail();
      if (focusKey) document.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    }
  }

  function agentLine(parent, item, verb, when) {
    const line = add(parent, "span", "proposal-meta");
    line.append(icon("bot"));
    add(line, "span", "proposal-agent", item.agent_name);
    const time = add(line, "span", "", `${verb} ${ago(when)}`);
    time.title = date(when);
    return line;
  }

  function renderProposals(parent, sidecar) {
    const state = model.proposals.sidecarId === sidecar.id ? model.proposals : null;
    const block = add(parent, "section", "detail-section proposals");
    const head = add(block, "div", "proposals-head");
    add(head, "h3", "", "Proposed edits");
    const waiting = Number(sidecar.pending_proposals || 0);
    if (waiting) add(head, "span", "counter counter-pop", count(waiting));

    if (!state || (!state.loaded && !state.error)) {
      add(block, "p", "proposal-empty", waiting ? "Loading proposals…" : "No edits are waiting for review.");
      return;
    }
    if (state.error) {
      flash(block, "danger", "Proposals could not be loaded", { text: `${state.error} Reload the page to try again.` });
      return;
    }

    if (state.pending.length) {
      const list = add(block, "ul", "proposal-list");
      for (const item of state.pending) {
        const entry = button(add(list, "li"), "proposal-item", "", {
          focusKey: `proposal:${item.id}`, onClick: () => openReview(sidecar.id, item.id),
        });
        entry.setAttribute("aria-label", `Review ${item.path}${item.stale ? " (out of date)" : ""}: ${item.summary}, proposed by ${item.agent_name}`);
        const body = add(entry, "span", "proposal-body");
        const top = add(body, "span", "proposal-top");
        add(top, "span", "proposal-path", item.path).title = item.path;
        if (item.stale) add(top, "span", "badge badge-warning proposal-badge", "Out of date");
        add(body, "span", "proposal-reason", item.summary);
        agentLine(body, item, "proposed", item.created_at);
        entry.append(icon("chevron-right", "proposal-chevron"));
      }
    } else {
      const empty = add(block, "p", "proposal-empty");
      empty.append(icon("inbox"));
      add(empty, "span", "", "No edits are waiting for review. Agents you allow to propose edits for this sidecar queue them here.");
    }

    if (state.reviewed.length) {
      add(block, "h4", "proposals-subhead", "Recent decisions");
      const shown = state.expanded ? state.reviewed : state.reviewed.slice(0, 5);
      const list = add(block, "ul", "decision-list");
      for (const item of shown) {
        const row = add(list, "li", "decision");
        const top = add(row, "div", "decision-top");
        const approved = item.status === "approved";
        add(top, "span", `badge decision-badge${approved ? " badge-success" : ""}`, approved ? "Approved" : "Rejected");
        add(top, "span", "proposal-path", item.path).title = item.path;
        add(row, "span", "proposal-reason", item.summary);
        agentLine(row, item, "decided", item.decided_at);
      }
      if (state.reviewed.length > 5) {
        button(block, "btn btn-sm decision-more", state.expanded ? "Show fewer" : `Show all ${count(state.reviewed.length)}`, {
          focusKey: "detail:decisions-more",
          onClick: () => { model.proposals.expanded = !model.proposals.expanded; renderDetail(); },
        });
      }
    }
  }

  // Review dialog

  function diffTable(target, diff) {
    const table = add(target, "table", "diff");
    const body = add(table, "tbody");
    let oldLine = 0;
    let newLine = 0;
    let added = 0;
    let removed = 0;
    for (const line of diff.split("\n")) {
      if (line === "" || line.startsWith("--- ") || line.startsWith("+++ ")) continue;
      const row = add(body, "tr");
      const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (hunk) {
        oldLine = Number(hunk[1]);
        newLine = Number(hunk[2]);
        row.className = "diff-hunk";
        add(row, "td", "diff-num");
        add(row, "td", "diff-num");
        add(row, "td", "diff-code", line);
        continue;
      }
      if (line.startsWith("\\")) {
        row.className = "diff-note";
        add(row, "td", "diff-num");
        add(row, "td", "diff-num");
        add(row, "td", "diff-code", line.slice(2));
        continue;
      }
      const sign = line[0];
      row.className = sign === "+" ? "diff-add" : sign === "-" ? "diff-del" : "";
      add(row, "td", "diff-num", sign === "+" ? "" : oldLine++);
      add(row, "td", "diff-num", sign === "-" ? "" : newLine++);
      const code = add(row, "td", "diff-code");
      add(code, "span", "diff-sign", sign === " " ? " " : sign).setAttribute("aria-label", sign === "+" ? "Added" : sign === "-" ? "Removed" : "Unchanged");
      code.append(document.createTextNode(line.slice(1)));
      if (sign === "+") added++;
      if (sign === "-") removed++;
    }
    return { added, removed };
  }

  function reviewQueue() {
    return model.proposals.sidecarId === model.review?.sidecarId ? model.proposals.pending.map((item) => item.id) : [];
  }

  function renderReviewNav() {
    const queue = reviewQueue();
    const index = queue.indexOf(model.review?.proposalId);
    byId("review-position").textContent = index >= 0 && queue.length > 1 ? `${index + 1} of ${queue.length}` : "";
    byId("review-prev").hidden = byId("review-next").hidden = queue.length < 2 || index < 0;
    byId("review-prev").disabled = index <= 0;
    byId("review-next").disabled = index < 0 || index >= queue.length - 1;
  }

  function setReviewBusy(busy) {
    const detail = model.review?.detail;
    const pending = detail && detail.status === "pending";
    byId("review-approve").disabled = busy || !pending || detail.stale;
    byId("review-reject").disabled = busy || !pending;
  }

  function renderReview() {
    const target = byId("review-body");
    target.replaceChildren();
    renderReviewNav();
    const review = model.review;
    const detail = review?.detail;
    if (!detail) {
      if (review?.loadError) flash(target, "danger", "This proposal could not be opened", { text: review.loadError });
      else add(target, "p", "review-loading", "Loading proposal…");
      setReviewBusy(true);
      return;
    }
    const sidecar = model.sidecars.find((item) => item.id === review.sidecarId);
    const head = add(target, "div", "review-head");
    const meta = add(head, "div", "review-byline");
    meta.append(icon("bot"));
    const byline = add(meta, "span");
    add(byline, "strong", "proposal-agent", detail.agent_name);
    byline.append(document.createTextNode(` proposed an edit${sidecar ? ` in ${sidecar.name}` : ""} ${ago(detail.created_at)}`));
    byline.title = date(detail.created_at);
    add(head, "p", "review-reason", detail.summary);

    if (detail.status !== "pending") {
      flash(target, "info", `This proposal was ${detail.status}`, { text: detail.decided_at ? `Decided ${ago(detail.decided_at)}. Nothing is left to review.` : "" });
    } else if (review.error) {
      flash(target, "danger", review.error.title, { text: review.error.text });
    } else if (detail.stale) {
      flash(target, "warning", "Out of date", { text: `${detail.stale_message} Approving is unavailable, but you can reject it to clear it from the queue.` });
    }

    const block = add(target, "div", "code-block review-diff");
    const header = add(block, "div", "code-block-header");
    const file = add(header, "span", "review-file", detail.path);
    file.title = detail.path;
    const stats = add(header, "span", "review-stats");
    if (detail.diff) {
      const scroller = add(block, "div", "diff-scroll");
      scroller.tabIndex = 0;
      scroller.setAttribute("role", "region");
      scroller.setAttribute("aria-label", `Changes to ${detail.path}`);
      const { added, removed } = diffTable(scroller, detail.diff);
      add(stats, "span", "change-added-text", `+${count(added)}`);
      add(stats, "span", "change-removed-text", `−${count(removed)}`);
    } else {
      add(block, "p", "review-kept", "Psydcar keeps the proposed text only until a decision.");
    }
    setReviewBusy(false);
  }

  async function showProposal(proposalId) {
    const review = model.review;
    if (!review) return;
    review.proposalId = proposalId;
    review.detail = null;
    review.error = null;
    review.loadError = null;
    renderReview();
    try {
      const detail = await request(`/api/sidecars/${review.sidecarId}/proposals/${proposalId}`);
      if (model.review !== review || review.proposalId !== proposalId) return;
      review.detail = detail;
    } catch (error) {
      if (model.review !== review || review.proposalId !== proposalId) return;
      review.loadError = error.message;
    }
    renderReview();
    byId("review-body").querySelector(".diff-scroll")?.focus({ preventScroll: true });
  }

  async function openReview(sidecarId, proposalId) {
    if (model.proposals.sidecarId !== sidecarId || !model.proposals.loaded) {
      await loadProposals(sidecarId, `${sidecarId}:${model.snapshotKey}`);
    }
    const target = proposalId || model.proposals.pending[0]?.id;
    if (!target) { message("No edits are waiting for review in this sidecar."); return; }
    model.review = { sidecarId, proposalId: target, detail: null };
    const dialog = byId("review-dialog");
    if (!dialog.open) dialog.showModal();
    await showProposal(target);
  }

  async function decide(decision) {
    const review = model.review;
    const detail = review?.detail;
    if (!detail) return;
    const queue = reviewQueue();
    const position = queue.indexOf(detail.id);
    setReviewBusy(true);
    review.error = null;
    try {
      const result = await request(`/api/sidecars/${review.sidecarId}/proposals/${detail.id}`, {
        method: "POST", body: JSON.stringify({ decision }),
      });
      if (decision === "rejected") message(`Rejected. ${result.path} was not changed.`);
      else if (result.index_notice) message(`Approved and saved ${result.path}. ${result.index_notice}`, true);
      else message(`Approved. ${result.path} was saved and is searchable now.`);
      await refresh(false, true);
      await loadProposals(review.sidecarId, `${review.sidecarId}:${model.snapshotKey}`);
      if (model.review !== review) return;
      const remaining = reviewQueue();
      if (!remaining.length) { byId("review-dialog").close(); return; }
      await showProposal(remaining[Math.min(Math.max(position, 0), remaining.length - 1)]);
    } catch (error) {
      if (model.review !== review) return;
      if (error.status === 409) {
        detail.stale = true;
        detail.stale_message = error.message;
        review.error = { title: "This edit can't be approved", text: `${error.message} The file was not changed.` };
      } else {
        review.error = { title: decision === "approved" ? "Approval failed" : "Rejection failed", text: error.message };
      }
      renderReview();
    }
  }

  function startReview() {
    const sidecar = visibleSidecars().find((item) => item.pending_proposals) || model.sidecars.find((item) => item.pending_proposals);
    if (!sidecar) return;
    if (model.selectedId !== sidecar.id) select(sidecar.id, false);
    openReview(sidecar.id);
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
  byId("toast").addEventListener("click", hideToast);
  byId("review-callout-button").addEventListener("click", startReview);
  byId("review-approve").addEventListener("click", () => decide("approved"));
  byId("review-reject").addEventListener("click", () => decide("rejected"));
  byId("review-prev").addEventListener("click", () => {
    const queue = reviewQueue();
    const index = queue.indexOf(model.review?.proposalId);
    if (index > 0) showProposal(queue[index - 1]);
  });
  byId("review-next").addEventListener("click", () => {
    const queue = reviewQueue();
    const index = queue.indexOf(model.review?.proposalId);
    if (index >= 0 && index < queue.length - 1) showProposal(queue[index + 1]);
  });
  byId("review-dialog").addEventListener("close", () => { model.review = null; });

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
    const picker = byId("connection-sidecars");
    const sidecarIds = Array.from(picker.querySelectorAll('input[name="sidecar-id"]:checked'), (input) => input.value);
    const proposalIds = Array.from(picker.querySelectorAll('input[name="proposal-id"]:checked'), (input) => input.value);
    if (!name) { formError("connection-error", "Enter a server name.", byId("connection-name")); byId("connection-name").focus(); return; }
    if (!sidecarIds.length) { formError("connection-error", "Select at least one sidecar."); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    byId("connection-result").hidden = true;
    model.connectionPreview = null;
    try {
      const preview = await request("/api/connections/preview", {
        method: "POST", body: JSON.stringify({ name, sidecar_ids: sidecarIds, proposal_ids: proposalIds }),
      });
      model.connectionPreview = preview;
      byId("result-name").textContent = preview.name;
      byId("result-selection").textContent = `Limited to ${preview.selected.map((item) => item.name).join(", ")}.`;
      const proposalNames = preview.selected.filter((item) => proposalIds.includes(item.id)).map((item) => item.name);
      if (proposalNames.length) byId("result-selection").textContent += ` Proposals allowed for ${proposalNames.join(", ")}.`;
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

  refresh(true);
  setInterval(() => { if (!document.hidden) refresh(); }, 2500);
})();
