(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const model = { token: null, sidecars: [], selectedId: null, filter: "", watching: false, loading: false, toastTimer: null, provider: { configured: false }, connectionPreview: null };

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

  function date(value) {
    if (!value) return "Never";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function status(sidecar) {
    if (sidecar.queued || sidecar.state === "pending") return { label: "Updating", className: "pending" };
    if (sidecar.state === "attention" || sidecar.error) return { label: "Needs attention", className: "attention" };
    if (sidecar.freshness === "stale") return { label: "Stale", className: "stale" };
    if (sidecar.state === "not_indexed") return { label: "Not indexed", className: "not-indexed" };
    return { label: "Current", className: "current" };
  }

  function pill(sidecar) {
    const value = status(sidecar);
    return element("span", `status-pill ${value.className}`, value.label);
  }

  function message(text, error = false) {
    const toast = byId("toast");
    toast.textContent = text;
    toast.classList.toggle("error", error);
    toast.hidden = false;
    clearTimeout(model.toastTimer);
    model.toastTimer = setTimeout(() => { toast.hidden = true; }, 5500);
  }

  function banner(text, error = false) {
    const item = byId("banner");
    item.textContent = text;
    item.classList.toggle("error", error);
    item.hidden = !text;
  }

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
      throw error;
    }
    return payload;
  }

  function applySnapshot(payload) {
    model.sidecars = payload.sidecars || [];
    model.provider = payload.provider || { configured: false };
    model.watching = Boolean(payload.watching);
    if (model.selectedId && !model.sidecars.some((item) => item.id === model.selectedId)) model.selectedId = null;
    if (!model.selectedId && model.sidecars.length) model.selectedId = model.sidecars[0].id;
    const watchText = model.watching ? "Watching local sources" : "Source watcher unavailable";
    byId("sidebar-watch-label").textContent = watchText;
    byId("topbar-watch-label").textContent = model.watching ? "Local · watching" : "Local · watcher stopped";
    document.querySelectorAll(".live-dot").forEach((dot) => dot.classList.toggle("offline", !model.watching));
    if (!model.watching) banner("Automatic source updates are unavailable. Use Update on a sidecar or restart the dashboard.", true);
    else banner("");
    render();
  }

  async function refresh(first = false) {
    if (model.loading) return;
    model.loading = true;
    try {
      const payload = await request(first || !model.token ? "/api/bootstrap" : "/api/sidecars");
      if (payload.token) model.token = payload.token;
      applySnapshot(payload);
    } catch (error) {
      banner(`Dashboard could not load: ${error.message} Reload the page or check the local service.`, true);
    } finally {
      model.loading = false;
    }
  }

  function render() {
    const items = model.sidecars;
    const statuses = items.map(status);
    byId("metric-total").textContent = items.length;
    byId("metric-current").textContent = statuses.filter((item) => item.className === "current").length;
    byId("metric-pending").textContent = statuses.filter((item) => item.className === "pending").length;
    byId("metric-attention").textContent = statuses.filter((item) => item.className === "attention" || item.className === "stale").length;
    byId("nav-count").textContent = items.length;
    byId("sidecar-count").textContent = items.length;
    renderSetup();
    renderList();
    renderDetail();
  }

  function renderList() {
    const list = byId("sidecar-list");
    list.replaceChildren();
    const query = model.filter.trim().toLocaleLowerCase();
    const items = model.sidecars.filter((item) => `${item.name} ${item.source_path}`.toLocaleLowerCase().includes(query));
    byId("empty-state").hidden = model.sidecars.length !== 0;
    byId("filter-empty").hidden = model.sidecars.length === 0 || items.length !== 0;
    for (const sidecar of items) {
      const card = element("button", "sidecar-card");
      card.type = "button";
      card.classList.toggle("selected", sidecar.id === model.selectedId);
      card.setAttribute("aria-pressed", String(sidecar.id === model.selectedId));
      card.setAttribute("aria-label", `${sidecar.name}, ${status(sidecar).label}. Show details.`);
      const top = add(card, "div", "card-top");
      add(top, "span", "folder-icon", "▱").setAttribute("aria-hidden", "true");
      const main = add(top, "div", "card-main");
      add(main, "h3", "", sidecar.name);
      const path = add(main, "p", "card-path", sidecar.source_path);
      path.title = sidecar.source_path;
      add(top, "span", "card-badge").append(pill(sidecar));
      const meta = add(card, "div", "card-meta");
      add(meta, "span", "", `${sidecar.coverage.files} files`);
      add(meta, "span", "", `${sidecar.coverage.passages} passages`);
      add(meta, "span", "push", sidecar.queued ? "Updating now" : "View details →");
      card.addEventListener("click", () => { model.selectedId = sidecar.id; render(); });
      list.append(card);
    }
  }

  function infoRow(parent, label, value) {
    const row = add(parent, "div", "info-row");
    add(row, "span", "", label);
    add(row, "strong", "", value);
  }

  function section(parent, title) {
    const block = add(parent, "section", "detail-section");
    add(block, "h3", "", title);
    return block;
  }

  function guidance(sidecar) {
    if (sidecar.queued) return "An update is running. Indexed evidence will remain available while it finishes.";
    if (sidecar.state === "attention") return "The last update needs review. Read the issue below, fix the source or provider, then choose Update.";
    if (sidecar.source_drift) return "The source changed since the last index. Choose Update to refresh this sidecar.";
    if (sidecar.state === "pending") return "Processing has not finished. Check the update details below.";
    if (sidecar.state === "not_indexed") return "No update has completed yet. Choose Update to index supported files.";
    return "The indexed source is current. The source watcher will follow new changes while this dashboard is open.";
  }

  function issueBox(parent, title, lines, next) {
    const box = add(parent, "div", "attention-box");
    add(box, "strong", "", title);
    const list = add(box, "ul");
    for (const line of lines) add(list, "li", "", line);
    if (next) add(box, "p", "guidance", next);
  }

  function renderDetail() {
    const sidecar = model.sidecars.find((item) => item.id === model.selectedId);
    byId("detail-placeholder").hidden = Boolean(sidecar);
    const target = byId("detail-content");
    target.hidden = !sidecar;
    target.replaceChildren();
    if (!sidecar) {
      byId("detail-placeholder").querySelector("h2").textContent = model.sidecars.length ? "Choose a sidecar" : "Your details will appear here";
      byId("detail-placeholder").querySelector("p").textContent = model.sidecars.length ? "Select a collection to see its source, update history, and next actions." : "Create your first sidecar to see its source coverage and update history.";
      return;
    }

    const head = add(target, "div", "detail-header");
    add(head, "p", "detail-eyebrow", "SIDECAR DETAILS");
    const title = add(head, "div", "detail-title-row");
    add(title, "h2", "", sidecar.name);
    title.append(pill(sidecar));
    add(head, "p", "detail-subtitle", guidance(sidecar));
    const actions = add(head, "div", "detail-actions");
    const updateButton = add(actions, "button", "button button-primary", sidecar.queued ? "Update running" : "↻ Update now");
    updateButton.type = "button";
    updateButton.disabled = sidecar.queued;
    updateButton.addEventListener("click", () => updateSidecar(sidecar));
    const renameButton = add(actions, "button", "button button-quiet", "Rename");
    renameButton.type = "button";
    renameButton.addEventListener("click", () => openRename(sidecar));
    const removeButton = add(actions, "button", "button button-text danger-text", "Remove");
    removeButton.type = "button";
    removeButton.addEventListener("click", () => openRemove(sidecar));

    const body = add(target, "div", "detail-body");
    const source = section(body, "Source coverage");
    add(source, "div", "source-path", sidecar.source_path);
    infoRow(source, "Indexed files", sidecar.coverage.files);
    infoRow(source, "Text and code", sidecar.coverage.text_files);
    infoRow(source, "PDFs", sidecar.coverage.pdfs);
    infoRow(source, "Searchable passages", sidecar.coverage.passages);
    infoRow(source, "Index freshness", sidecar.freshness === "stale" ? "Stale" : sidecar.freshness === "not_indexed" ? "Not indexed" : "Current");
    infoRow(source, "Source changes", sidecar.source_drift ? "Detected since update" : "None since last update");
    const identity = section(body, "Identity");
    const idRow = add(identity, "div", "id-row");
    const code = add(idRow, "code", "", sidecar.id);
    code.title = sidecar.id;
    const copy = add(idRow, "button", "mini-button", "Copy ID");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(sidecar.id); message("Sidecar ID copied."); }
      catch { message(`Sidecar ID: ${sidecar.id}`); }
    });
    const history = section(body, "Update history");
    infoRow(history, "Last successful update", date(sidecar.update.last_success_at));
    infoRow(history, "Last attempt", date(sidecar.update.started_at));
    infoRow(history, "Files added", sidecar.update.added);
    infoRow(history, "Files changed", sidecar.update.changed);
    infoRow(history, "Files removed", sidecar.update.deleted);
    if (sidecar.embedding) {
      const embedding = section(body, "Embeddings");
      infoRow(embedding, "State", sidecar.embedding.status.replaceAll("_", " "));
      infoRow(embedding, "Progress", `${sidecar.embedding.embedded_count} of ${sidecar.embedding.expected_count} passages`);
      if (sidecar.embedding.message) issueBox(embedding, "Provider needs attention", [sidecar.embedding.message], "Check the configured provider and its credential, then choose Update.");
    }
    const issues = Array.isArray(sidecar.update.issues) ? sidecar.update.issues.map((issue) => typeof issue === "string" ? issue : [issue.source || issue.path, issue.error || issue.message].filter(Boolean).join(": ")) : [];
    if (sidecar.error && !issues.includes(sidecar.error)) issues.unshift(sidecar.error);
    if (issues.length) {
      const review = section(body, "Needs attention");
      issueBox(review, "Source or processing issue", issues, sidecar.status === "source_attention" ? "Restore a file if its removal was unintended, or choose Update to acknowledge it. Removing this sidecar is a separate action." : "Fix the source or provider and choose Update to retry.");
    } else if (sidecar.state === "attention") {
      const review = section(body, "Needs attention");
      issueBox(review, "Review the last update", [sidecar.status === "source_attention" ? "A source file was removed from this sidecar's index." : "The last processing pass did not complete."], "Review the source and choose Update to clear this notice.");
    } else if (sidecar.source_drift) {
      const review = section(body, "Source changed");
      issueBox(review, "Indexed content may be out of date", ["A file changed, appeared, or disappeared since the last update."], "Choose Update to reconcile this sidecar now.");
    }
    const pages = (sidecar.pdf_pages || []).filter((page) => page.status !== "indexed");
    if (pages.length) {
      const pdf = section(body, "PDF page notices");
      issueBox(pdf, "Pages without searchable text", pages.map((page) => `${page.source}, page ${page.page}: ${page.message || page.status}`), "Use a text-searchable PDF or OCR the source file, then choose Update.");
    }
  }

  function formError(id, text, input) {
    const box = byId(id);
    box.textContent = text;
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
      await refresh();
    } catch (error) { message(error.message, true); }
  }

  function renderSetup() {
    const provider = model.provider;
    const state = byId("provider-state");
    state.className = `status-pill ${!provider.configured ? "not-indexed" : provider.credential_available ? "current" : "attention"}`;
    state.textContent = !provider.configured ? "Not configured" : provider.credential_available ? "Configured" : "Credential missing";
    byId("provider-description").textContent = !provider.configured
      ? "Lexical search is available. Configure an OpenAI-compatible provider to add semantic ranking."
      : provider.credential_available
        ? "Configured for embedding updates. Test the route or edit its settings below."
        : `Set ${provider.api_key_env} before starting the dashboard, then restart it and test the route.`;
    const summary = byId("provider-summary");
    summary.hidden = !provider.configured;
    summary.replaceChildren();
    if (provider.configured) {
      add(summary, "span", "", "Route"); add(summary, "strong", "", provider.base_url);
      add(summary, "span", "", "Model"); add(summary, "strong", "", provider.model);
      add(summary, "span", "", "Key variable"); add(summary, "strong", "", provider.api_key_env || "No key required");
    }
    byId("provider-button").textContent = provider.configured ? "Edit provider" : "Configure provider";
    byId("clear-provider-button").hidden = !provider.configured;
    const count = model.sidecars.length;
    byId("connection-count").textContent = `${count} sidecar${count === 1 ? "" : "s"}`;
    byId("connection-description").textContent = count
      ? "Choose one or more sidecars and prepare a local MCP entry for a project or coding tool."
      : "Create a sidecar first. Then choose exactly which collections a project or coding tool can access.";
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
    const count = byId("connection-sidecars").querySelectorAll("input:checked").length;
    byId("selected-count").textContent = `${count} selected`;
  }

  function openConnection() {
    byId("connection-form").reset();
    formError("connection-error", "");
    byId("connection-result").hidden = true;
    model.connectionPreview = null;
    const list = byId("connection-sidecars");
    list.replaceChildren();
    for (const sidecar of model.sidecars) {
      const option = add(list, "label", "selection-option");
      const input = add(option, "input");
      input.type = "checkbox";
      input.value = sidecar.id;
      input.name = "sidecar-id";
      input.addEventListener("change", updateSelectedCount);
      const copy = add(option, "span");
      add(copy, "strong", "", sidecar.name);
      add(copy, "small", "", `${sidecar.source_path} · ${sidecar.id}`);
    }
    byId("connection-empty").hidden = model.sidecars.length !== 0;
    updateSelectedCount();
    byId("connection-dialog").showModal();
    byId("connection-name").focus();
  }

  byId("provider-button").addEventListener("click", openProvider);
  byId("clear-provider-button").addEventListener("click", () => {
    formError("clear-provider-error", "");
    byId("clear-provider-dialog").showModal();
  });
  byId("connection-button").addEventListener("click", openConnection);
  byId("provider-form").addEventListener("input", () => { byId("provider-test-result").hidden = true; formError("provider-error", ""); });
  byId("connection-form").addEventListener("input", () => { byId("connection-result").hidden = true; formError("connection-error", ""); });

  byId("provider-test-button").addEventListener("click", async () => {
    const button = byId("provider-test-button");
    button.disabled = true;
    formError("provider-error", "");
    byId("provider-test-result").hidden = true;
    try {
      const result = await request("/api/provider/test", { method: "POST", body: JSON.stringify(providerDraft()) });
      const box = byId("provider-test-result");
      box.textContent = `${result.message} Vector dimensions: ${result.dimensions}.`;
      box.hidden = false;
    } catch (error) { formError("provider-error", `${error.message}${error.hint ? ` ${error.hint}` : ""}`); }
    finally { button.disabled = false; }
  });

  byId("provider-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("provider-save-button");
    button.disabled = true;
    formError("provider-error", "");
    try {
      const result = await request("/api/provider", { method: "POST", body: JSON.stringify(providerDraft()) });
      byId("provider-dialog").close();
      message(result.message);
      await refresh();
    } catch (error) { formError("provider-error", `${error.message}${error.hint ? ` ${error.hint}` : ""}`); }
    finally { button.disabled = false; }
  });

  byId("clear-provider-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const result = await request("/api/provider", { method: "DELETE", body: JSON.stringify({ confirm: "clear" }) });
      byId("clear-provider-dialog").close();
      message(result.message);
      await refresh();
    } catch (error) { formError("clear-provider-error", error.message); }
    finally { button.disabled = false; }
  });

  byId("connection-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = byId("connection-name").value.trim();
    const sidecarIds = Array.from(byId("connection-sidecars").querySelectorAll("input:checked"), (input) => input.value);
    if (!name) { formError("connection-error", "Enter a connection name.", byId("connection-name")); byId("connection-name").focus(); return; }
    if (!sidecarIds.length) { formError("connection-error", "Select at least one sidecar. Create one first if this list is empty."); return; }
    const button = event.target.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const preview = await request("/api/connections/preview", { method: "POST", body: JSON.stringify({ name, sidecar_ids: sidecarIds }) });
      model.connectionPreview = preview;
      byId("result-name").textContent = preview.name;
      byId("result-selection").textContent = `Access: ${preview.selected.map((item) => `${item.name} (${item.id})`).join(", ")}.`;
      byId("connection-json").textContent = JSON.stringify(preview.config, null, 2);
      byId("result-footnote").textContent = `If the client cannot start Psydcar, check that the release is installed at the command path above. A removed sidecar ID makes this connection fail closed.${model.provider.api_key_env ? ` To use embeddings, set ${model.provider.api_key_env} in the MCP client's environment. Its value is never copied here.` : ""}`;
      byId("connection-result").hidden = false;
      byId("connection-result").scrollIntoView({ block: "nearest" });
    } catch (error) { formError("connection-error", error.message); }
    finally { button.disabled = false; }
  });

  byId("copy-connection-button").addEventListener("click", async () => {
    if (!model.connectionPreview) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(model.connectionPreview.config, null, 2));
      message("MCP configuration copied. Add it to your client's local server settings.");
    } catch { message("Copy failed. Select and copy the JSON shown in this dialog.", true); }
  });

  byId("create-button").addEventListener("click", openCreate);
  byId("empty-create-button").addEventListener("click", openCreate);
  byId("refresh-button").addEventListener("click", () => refresh());
  byId("filter-input").addEventListener("input", (event) => { model.filter = event.target.value; renderList(); });
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  byId("remove-confirm").addEventListener("input", (event) => {
    const sidecar = model.sidecars.find((item) => item.id === byId("remove-form").dataset.id);
    byId("remove-submit").disabled = !sidecar || event.target.value !== sidecar.name;
  });

  byId("create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = byId("create-name").value.trim();
    const source = byId("create-source").value.trim();
    if (!name) { formError("create-error", "Enter a sidecar name.", byId("create-name")); byId("create-name").focus(); return; }
    if (!source) { formError("create-error", "Enter an existing source directory path.", byId("create-source")); byId("create-source").focus(); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request("/api/sidecars", { method: "POST", body: JSON.stringify({ name, source }) });
      model.selectedId = result.id;
      byId("create-dialog").close();
      message(result.message);
      await refresh();
    } catch (error) { formError("create-error", error.message); }
    finally { submit.disabled = false; }
  });

  byId("rename-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = byId("rename-name").value.trim();
    if (!name) { formError("rename-error", "Enter a new sidecar name.", byId("rename-name")); byId("rename-name").focus(); return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await request(`/api/sidecars/${event.target.dataset.id}/rename`, { method: "POST", body: JSON.stringify({ name }) });
      byId("rename-dialog").close();
      message(result.message);
      await refresh();
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
      await refresh();
    } catch (error) { formError("remove-error", error.message); }
    finally { submit.disabled = false; }
  });

  refresh(true);
  setInterval(() => { if (!document.hidden) refresh(); }, 2500);
})();
