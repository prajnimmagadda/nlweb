// PM Copilot UI. Plain DOM, no framework. All text from the store (much of it written by the
// agent or by other people on GitHub) goes in through textContent, never as HTML.
"use strict";

(() => {
  const $main = document.getElementById("main");
  const $sidebar = document.getElementById("sidebar");
  const $toasts = document.getElementById("toasts");

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const KIND_LABEL = {
    action_request: "Agent action", change_request: "Change request", baseline: "Proposed baseline",
    risk: "Proposed risk", decision: "Proposed decision", charter: "Charter",
  };
  const DIMENSION_LABEL = {
    schedule: "Schedule", scope: "Scope", earned_value: "Earned value", aging_wip: "Aging work",
    blocked: "Blocked", risk: "Risk",
  };
  const STATUS = {
    pending: ["amber", "waiting for you"], submitted: ["amber", "submitted"], proposed: ["amber", "proposed"],
    draft: ["neutral", "draft"], approved: ["brand", "approved"], accepted: ["green", "accepted"],
    executed: ["green", "done"], implemented: ["green", "implemented"], failed: ["red", "failed"],
    rejected: ["neutral", "rejected"], superseded: ["neutral", "superseded"], open: ["brand", "open"],
    completed: ["green", "done"], finished: ["green", "done"], max_turns: ["amber", "turn limit"],
    max_tokens: ["amber", "output limit"], refusal: ["red", "refused"], api_error: ["red", "failed"],
    error: ["red", "failed"], running: ["brand", "running"],
  };

  // ----- small utilities -----------------------------------------------------

  const storage = (area) => ({
    get(key) { try { return window[area].getItem(key); } catch { return null; } },
    set(key, value) { try { window[area].setItem(key, value); } catch { /* storage may be blocked */ } },
    remove(key) { try { window[area].removeItem(key); } catch { /* ignore */ } },
  });
  const tabStore = storage("sessionStorage");
  const prefs = storage("localStorage");

  let uidCounter = 0;
  const uid = (prefix) => `${prefix}-${++uidCounter}`;
  const enc = encodeURIComponent;

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else if (key === "disabled" || key === "selected" || key === "checked") el[key] = true;
      else el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : String(child));
    }
    return el;
  }

  // Only http(s) links from stored data become clickable; anything else stays text.
  function safeUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }
  function externalLink(text, url) {
    const href = safeUrl(url);
    return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text) : h("span", null, text);
  }
  function issueLink(repo, number) {
    const [owner, name] = String(repo).split("/");
    return externalLink(`${repo}#${number}`, `https://github.com/${enc(owner)}/${enc(name)}/issues/${Number(number)}`);
  }
  function refLink(ref) {  // "owner/repo#12" or a URL or plain text
    const m = /^([\w.-]+\/[\w.-]+)#(\d+)$/.exec(ref);
    if (m) return issueLink(m[1], m[2]);
    return safeUrl(ref) ? externalLink(ref.replace(/^https?:\/\//, ""), ref) : h("span", null, ref);
  }

  function fmtDate(iso, withYear) {
    if (!iso) return "—";
    const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
    if (!y || !m || !d) return String(iso);
    return `${d} ${MONTHS[m - 1]}${withYear ? ` ${y}` : ""}`;
  }
  function fmtWhen(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso || "—";
    return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  function ago(iso) {
    const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (Number.isNaN(minutes)) return "at an unknown time";
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    return `${Math.round(hours / 24)} days ago`;
  }
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const fmtNumber = (n, digits = 1) => (n === null || n === undefined ? "—" : Number(n).toFixed(digits).replace(/\.0+$/, ""));

  function badge(text, tone = "neutral", dot = true) {
    return h("span", { class: `badge ${tone}${dot ? " dot" : ""}` }, text);
  }
  function ragBadge(rag) {
    return badge(rag || "unknown", ["green", "amber", "red"].includes(rag) ? rag : "neutral");
  }
  function statusBadge(status) {
    const [tone, label] = STATUS[status] || ["neutral", status || "unknown"];
    return badge(label, tone);
  }
  function button(label, { variant = "secondary", onclick, type = "button", disabled, title } = {}) {
    return h("button", { class: `btn ${variant}`, type, onclick, disabled, title }, label);
  }
  // A button marked data-locked stays disabled whatever happens (e.g. approving a stale analysis).
  function setBusy(buttons, busy) {
    for (const b of buttons) {
      b.disabled = busy || b.dataset.locked === "true";
      b.classList.toggle("busy", busy);
      b.setAttribute("aria-busy", busy ? "true" : "false");
    }
  }
  function field(label, control, hint) {
    if (!control.id) control.id = uid("f");
    return h("div", { class: "field" }, h("label", { for: control.id }, label), control,
      hint ? h("p", { class: "hint" }, hint) : null);
  }
  function input(props) { return h("input", { class: "input", ...props }); }
  function select(options, current, props) {
    return h("select", { class: "select", ...props },
      options.map(([value, label]) => h("option", { value, selected: value === current }, label)));
  }
  function card(...children) { return h("section", { class: "card" }, ...children); }
  function cardHeader(title, right) {
    return h("div", { class: "card-header" }, h("h2", { class: "t-heading" }, title), right || null);
  }
  function section(label, ...children) {
    return h("div", { class: "section" }, h("h3", { class: "t-overline section-label" }, label), ...children);
  }
  function pageHeader(title, { badges = [], subtitle, actions = [] } = {}) {
    return h("header", { class: "page-header" },
      h("div", null,
        h("div", { class: "title-row" }, h("h1", { class: "t-display", tabindex: "-1" }, title), badges),
        subtitle ? h("p", { class: "subtitle muted" }, subtitle) : null),
      actions.length ? h("div", { class: "actions" }, actions) : null);
  }
  function toast(message, bad = false) {
    const el = h("div", { class: `toast${bad ? " bad" : ""}` }, message);
    $toasts.append(el);
    setTimeout(() => el.remove(), bad ? 9000 : 5000);
  }
  function errorView(error) {
    return card(h("p", { class: "error-text", role: "alert" }, error.message || String(error)));
  }

  // ----- API -----------------------------------------------------------------

  const state = { token: null, session: null, project: null, counts: { inbox: 0, change_requests: 0 }, playbooks: null };

  function takeToken() {
    const match = /(?:^#|&)token=([A-Za-z0-9_-]{20,})/.exec(location.hash);
    if (match) {
      tabStore.set("pm-copilot-token", match[1]);
      history.replaceState(null, "", `${location.pathname}#/dashboard`);
    }
    return tabStore.get("pm-copilot-token");
  }

  async function api(method, path, body) {
    const headers = { "X-PM-Token": state.token || "", Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method, headers, cache: "no-store", credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = null; }
    if (!response.ok) {
      const error = new Error((data && data.error) || text || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  const P = (path) => `/api/projects/${enc(state.project)}${path}`;

  async function refreshCounts() {
    try {
      state.counts = await api("GET", P("/counts"));
      updateCounts();
    } catch { /* counts are a convenience */ }
  }
  function updateCounts() {
    for (const el of $sidebar.querySelectorAll("[data-count]")) {
      const n = state.counts[el.dataset.count] || 0;
      el.textContent = n ? String(n) : "";
    }
  }

  // ----- shell ---------------------------------------------------------------

  const NAV = [
    ["dashboard", "Dashboard"], ["inbox", "Inbox", "inbox"], ["changes", "Change requests", "change_requests"],
    ["run", "Run a playbook"], ["schedules", "Schedules"], ["settings", "Settings"],
  ];

  function renderSidebar(active) {
    const projects = (state.session && state.session.projects) || [];
    const current = projects.find((p) => p.project_id === state.project);
    let picker;
    if (projects.length > 1) {
      picker = select(projects.map((p) => [p.project_id, p.name]), state.project, {
        class: "project-select", "aria-label": "Project",
        onchange: (e) => { state.project = e.target.value; prefs.set("pm-copilot-project", state.project); route(); },
      });
    } else {
      picker = h("div", { class: "t-caption muted" }, current ? current.name : "No project yet");
    }
    const nav = h("nav", { class: "nav", "aria-label": "Main" }, NAV.map(([key, label, count]) =>
      h("a", { href: `#/${key}`, "aria-current": key === active ? "page" : null }, h("span", null, label),
        count ? h("span", { class: "count", "data-count": count }) : null)));
    const who = state.session ? state.session.user : "";
    $sidebar.replaceChildren(...[
      h("div", { class: "brand" }, h("div", { class: "brand-mark", "aria-hidden": "true" }, "PM"),
        h("div", null, h("div", { class: "brand-name" }, "PM Copilot"), picker)),
      state.project ? nav : null,
      who ? h("div", { class: "you" }, h("p", { class: "t-body" }, `You act as ${who}`),
        h("p", { class: "t-caption muted" },
          "Approvals, schedules and thresholds are yours alone. The agent can only propose.")) : null,
    ].filter(Boolean));
    updateCounts();
  }

  function renderLocked() {
    renderSidebar(null);
    $main.replaceChildren(card(
      h("h1", { class: "t-title" }, "Open PM Copilot from your terminal"),
      h("p", { class: "muted" },
        "This page needs the link printed by `python -m pm_agent ui`. It carries a one-time token that proves the "
        + "request comes from you, so other websites can't approve things in your name."),
    ));
  }

  function renderNoProject() {
    const create = button("Create a demo project", {
      variant: "primary",
      onclick: async () => {
        setBusy([create], true);
        try {
          await api("POST", "/api/demo", {});
          await loadSession();
          state.project = "demo";
          prefs.set("pm-copilot-project", "demo");
          location.hash = "#/dashboard";
          route();
        } catch (e) { toast(e.message, true); setBusy([create], false); }
      },
    });
    $main.replaceChildren(pageHeader("Welcome to PM Copilot", {
      subtitle: "Create a project from the terminal, or try it with synthetic data first.",
    }), card(
      h("div", { class: "stack-sm" },
        h("p", null, "Set up a real project:"),
        h("p", { class: "mono panel" }, "python -m pm_agent init my-app --repos owner/app --milestone v1 --target 2026-12-15"),
        h("p", null, "Or explore a demo project with 12 weeks of history, a baseline, a change request and an inbox:"),
        h("div", null, create)),
    ));
  }

  // ----- router --------------------------------------------------------------

  const ROUTES = [
    [/^#\/(?:dashboard)?$/, "dashboard", () => dashboardPage()],
    [/^#\/inbox(?:\/([a-z_]+)\/([^/]+))?$/, "inbox", (kind, id) => inboxPage(kind, id)],
    [/^#\/changes(?:\/([^/]+))?$/, "changes", (id) => (id ? changePage(id) : changesPage())],
    [/^#\/run(?:\/([a-z_]+))?$/, "run", (name) => runPage(name)],
    [/^#\/schedules$/, "schedules", () => schedulesPage()],
    [/^#\/settings$/, "settings", () => settingsPage()],
  ];
  let renderSeq = 0;

  async function route() {
    if (/(?:^#|&)token=/.test(location.hash)) {  // a fresh launch link pasted into this tab
      const fresh = takeToken();
      if (fresh && fresh !== state.token) { state.token = fresh; state.session = null; state.playbooks = null; }
    }
    if (!state.token) state.token = tabStore.get("pm-copilot-token");
    if (!state.token) { renderLocked(); return; }
    if (!state.session) {
      try {
        await loadSession();
      } catch (e) {
        if (e.status === 401) { tabStore.remove("pm-copilot-token"); state.token = null; renderLocked(); return; }
        $main.replaceChildren(errorView(e));
        return;
      }
    }
    const seq = ++renderSeq;
    const hash = location.hash || "#/dashboard";
    let found = null;
    for (const [pattern, name, render] of ROUTES) {
      const match = pattern.exec(hash);
      if (match) { found = { name, render, args: match.slice(1).map((a) => (a ? decodeURIComponent(a) : a)) }; break; }
    }
    if (!found) { location.replace("#/dashboard"); return; }
    renderSidebar(found.name);
    if (!state.project) { renderNoProject(); return; }
    $main.setAttribute("aria-busy", "true");
    try {
      const view = await found.render(...found.args);
      if (seq !== renderSeq) return;
      $main.replaceChildren(...[view].flat().filter(Boolean));
      const heading = $main.querySelector("h1");
      if (heading && document.activeElement === document.body) heading.focus({ preventScroll: true });
    } catch (error) {
      if (seq !== renderSeq) return;
      if (error.status === 401) {
        tabStore.remove("pm-copilot-token");
        state.token = null;
        state.session = null;
        renderLocked();
        return;
      }
      $main.replaceChildren(errorView(error));
    } finally {
      if (seq === renderSeq) $main.removeAttribute("aria-busy");
    }
  }

  async function loadSession() {
    state.session = await api("GET", "/api/session");
    const ids = state.session.projects.map((p) => p.project_id);
    const saved = prefs.get("pm-copilot-project");
    state.project = ids.includes(saved) ? saved : ids[0] || null;
  }

  // ----- dashboard -----------------------------------------------------------

  function inboxHref(item) {
    return item.kind === "change_request" ? `#/changes/${enc(item.id)}` : `#/inbox/${item.kind}/${enc(item.id)}`;
  }
  function inboxItem(item, current) {
    return h("a", { class: "inbox-item", href: current === undefined ? inboxHref(item) : `#/inbox/${item.kind}/${enc(item.id)}`,
      "aria-current": current ? "true" : null },
      h("div", { class: "t-overline brand-text" }, `${KIND_LABEL[item.kind] || item.kind} · ${item.id}`),
      h("div", { class: "t-strong" }, item.title),
      h("div", { class: "t-caption muted" }, `${item.verb} · ${item.requested_by} · ${fmtWhen(item.created_at)}`));
  }

  function runRow(run) {
    const title = (run.playbook || "").split(".").pop().replace(/_/g, " ");
    const summary = run.error || (run.summary || "").split("\n").find((l) => l.trim()) || "";
    const meta = [fmtWhen(run.ts), run.trigger, run.tool_calls !== undefined ? plural(run.tool_calls, "tool call") : null]
      .filter(Boolean).join(" · ");
    return h("div", { class: "list-row" },
      h("div", { class: "grow" },
        h("div", { class: "t-strong" }, title.charAt(0).toUpperCase() + title.slice(1)),
        h("div", { class: "t-caption muted" }, meta),
        summary ? h("div", { class: "t-caption muted", title: summary }, summary.length > 140 ? `${summary.slice(0, 140)}…` : summary) : null),
      statusBadge(run.status));
  }

  async function dashboardPage() {
    const d = await api("GET", P("/dashboard"));
    state.counts = d.counts;
    updateCounts();
    const { health, project } = d;
    const m = health.metrics;
    const subtitle = [
      project.release_milestone ? `Release ${project.release_milestone}` : "All open items",
      project.target_date ? `target ${fmtDate(project.target_date, true)}` : "no target date",
      project.sandbox ? "demo data; GitHub actions are simulated"
        : d.last_synced ? `synced from GitHub ${ago(d.last_synced)}` : "not synced from GitHub yet",
    ].join(" · ");

    const sync = button("Sync GitHub", {
      disabled: project.sandbox, title: project.sandbox ? "Demo projects don't sync" : null,
      onclick: async () => {
        setBusy([sync], true);
        try {
          const out = await api("POST", P("/sync"), {});
          const changed = out.repos.reduce((n, r) => n + (r.created || 0) + (r.updated || 0), 0);
          toast(`Synced ${plural(out.repos.length, "repo")}; ${plural(changed, "item")} changed.`);
          route();
        } catch (e) { toast(e.message, true); setBusy([sync], false); }
      },
    });
    const weekly = h("a", { class: "btn primary", href: "#/run/monitor_and_control_performance" }, "Run weekly status");

    const metric = (label, value, sub) => h("div", { class: "card metric" },
      h("div", { class: "t-caption muted" }, label), h("div", { class: "value" }, value),
      h("div", { class: "t-caption muted" }, sub));

    const healthCard = card(
      cardHeader("Health", h("span", { class: "t-caption muted" }, "Computed from project data, never chosen by the model")),
      h("div", { class: "rows" }, health.dimensions.map((dim) => h("div", { class: "row" },
        h("div", { class: "t-body", title: dim.value || null }, DIMENSION_LABEL[dim.name] || dim.name),
        h("div", null, ragBadge(dim.rag)),
        h("div", { class: "muted" }, dim.explanation)))),
    );

    const waiting = d.inbox.length;
    const decisions = card(
      cardHeader("Needs your decision", waiting ? badge(`${waiting} waiting`, "neutral", false) : null),
      waiting
        ? h("div", { class: "item-list" }, d.inbox.slice(0, 3).map((item) => inboxItem(item)))
        : h("p", { class: "muted" }, "Nothing is waiting for you."),
      waiting ? h("p", null, h("a", { href: "#/inbox" }, "Open inbox →")) : null,
    );
    const runs = card(
      cardHeader("Recent agent runs", h("a", { href: "#/run", class: "t-label" }, "Run a playbook")),
      d.runs.length ? h("div", null, d.runs.map(runRow))
        : h("p", { class: "muted" }, "No runs yet. Schedule a playbook, or run one now."),
    );

    return [
      pageHeader(project.name, { badges: [ragBadge(health.overall)], subtitle, actions: [sync, weekly] }),
      h("div", { class: "metrics" },
        metric("Forecast (P85)", fmtDate(m.forecast_p85), `P50 ${fmtDate(m.forecast_p50)} · target ${fmtDate(project.target_date)}`),
        metric("Remaining", plural(m.remaining_items, "item"), project.release_milestone ? `${project.release_milestone} milestone` : "all open items"),
        metric("Throughput", `${fmtNumber(m.throughput_per_week)} / week`, "last 90 days"),
        metric("Schedule index (SPI)", m.spi === null || m.spi === undefined ? "—" : Number(m.spi).toFixed(2), "agile earned value")),
      h("div", { class: "grid-2" }, healthCard, h("div", { class: "stack" }, decisions, runs)),
    ];
  }

  // ----- inbox ---------------------------------------------------------------

  async function inboxPage(kind, id) {
    const { items } = await api("GET", P("/inbox"));
    state.counts.inbox = items.length;
    updateCounts();
    if (!kind && items.length) {
      kind = items[0].kind;
      id = items[0].id;
      if (/^#\/inbox\/?$/.test(location.hash)) history.replaceState(null, "", `#/inbox/${kind}/${enc(id)}`);
    }
    const header = pageHeader("Inbox", {
      subtitle: items.length
        ? `${plural(items.length, "item")} ${items.length === 1 ? "needs" : "need"} your decision. The agent proposed them; nothing happens until you decide.`
        : "Nothing needs your decision right now.",
    });
    const list = h("div", { class: "item-list" },
      items.length ? items.map((item) => inboxItem(item, item.kind === kind && item.id === id))
        : card(h("p", { class: "empty" }, "All clear.")));
    const detail = kind ? await itemDetail(kind, id) : null;
    return [header, h("div", { class: "grid-list" }, list, detail || h("div"))];
  }

  async function itemDetail(kind, id) {
    const stored = await api("GET", P(`/items/${kind}/${enc(id)}`));
    const a = stored.data;
    const status = kind === "charter" ? (a.approved_by ? "approved" : "proposed") : (a.status || null);
    const title = a.title || a.name || id;
    const head = h("div", null,
      h("div", { class: "title-row" }, h("span", { class: "t-overline brand-text" }, `${KIND_LABEL[kind] || kind} · ${id}`),
        status ? statusBadge(status) : null),
      h("h2", { class: "t-title detail-title" }, title),
      h("p", { class: "t-caption muted" },
        `${stored.version > 1 ? "Last written" : "Proposed"} by ${stored.actor} · ${fmtWhen(stored.created_at)} · version ${stored.version}`));
    const body = DETAIL[kind] ? DETAIL[kind](stored, a) : [];
    const sources = (stored.sources || []).length
      ? section("Evidence", h("div", { class: "evidence" }, stored.sources.map(refLink))) : null;
    return h("section", { class: "card stack" }, head, body, sources);
  }

  function afterDecision() { refreshCounts(); route(); }

  function decisionForm({ kind, id, version, approveLabel, rejectable = true, footnote, noteLabel }) {
    const note = input({ maxlength: "2000", placeholder: "Why, in a sentence" });
    const error = h("p", { class: "error-text", role: "alert" });
    const approve = button(approveLabel, { variant: "primary" });
    const reject = rejectable ? button("Reject", { variant: "danger" }) : null;
    const buttons = [approve, reject].filter(Boolean);
    async function decide(decision) {
      error.textContent = "";
      if (decision === "reject" && !note.value.trim()) {
        error.textContent = "Say why you're rejecting it; the reason goes in the audit log.";
        note.focus();
        return;
      }
      setBusy(buttons, true);
      try {
        const out = await api("POST", P(`/items/${kind}/${enc(id)}/decision`), { decision, note: note.value, version });
        const bad = out.status === "failed";
        toast(decisionMessage(kind, decision, out), bad);
        afterDecision();
      } catch (e) {
        error.textContent = e.message;
        setBusy(buttons, false);
      }
    }
    approve.addEventListener("click", () => decide("approve"));
    if (reject) reject.addEventListener("click", () => decide("reject"));
    return h("div", { class: "panel" },
      field(noteLabel || (rejectable ? "Note for the audit log (required to reject)" : "Note for the audit log (optional)"), note),
      h("div", { class: "actions" }, buttons), error,
      footnote ? h("p", { class: "hint" }, footnote) : null);
  }

  function decisionMessage(kind, decision, out) {
    if (decision === "reject") return `${out.id} rejected. Your reason is in the audit log.`;
    if (kind === "action_request") {
      if (out.status === "failed") return `Approved, but GitHub refused: ${out.result && out.result.error}`;
      if (out.result && out.result.simulated) return "Approved. Demo project, so nothing was sent to GitHub.";
      const created = out.result && out.result.created;
      if (created) return `Done: created ${plural(created.length, "issue")} on GitHub.`;
      return `Done: moved ${plural((out.result && out.result.updated || []).length, "issue")} on GitHub.`;
    }
    if (kind === "change_request") {
      const parts = [`${out.id} approved`];
      if (out.target_date) parts.push(`target moved to ${fmtDate(out.target_date, true)}`);
      if (out.baseline) parts.push(`re-baselined as ${out.baseline}`);
      if (out.actions_waiting_for_approval) parts.push(`${plural(out.actions_waiting_for_approval.length, "GitHub action")} waiting in your inbox`);
      return `${parts.join("; ")}.`;
    }
    if (kind === "baseline") return `${out.id} is now the baseline${out.superseded ? ` (replaces ${out.superseded})` : ""}.`;
    return `${out.id} approved.`;
  }

  function actionResult(a) {
    if (!a.result) return null;
    if (a.status === "failed") {
      const done = (a.result.created || a.result.updated || []);
      return h("div", { class: "callout bad" }, `GitHub refused: ${a.result.error}`,
        done.length ? h("div", null, `Done before the failure: ${done.map((x) => (x.number ? `#${x.number}` : `#${x}`)).join(", ")}`) : null);
    }
    const repo = a.payload.repo;
    if (a.result.simulated) {
      const what = a.result.created ? a.result.created.map((c) => `#${c.number} ${c.title}`).join("; ")
        : `${(a.result.updated || []).map((n) => `#${n}`).join(", ")} → ${a.result.milestone || "no milestone"}`;
      return h("div", { class: "callout" }, `Simulated: ${what}. ${a.result.note}`);
    }
    if (a.result.created) {
      return h("div", { class: "callout good" }, "Created: ",
        a.result.created.map((c, i) => [i ? ", " : "", externalLink(`#${c.number} ${c.title}`, c.url)]));
    }
    return h("div", { class: "callout good" }, `Moved ${a.result.milestone ? `to ${a.result.milestone}` : "out of their milestone"}: `,
      (a.result.updated || []).map((n, i) => [i ? ", " : "", issueLink(repo, n)]));
  }

  const DETAIL = {
    action_request(stored, a) {
      const p = a.payload;
      const pending = a.status === "pending";
      const label = pending ? "What will happen" : "What was requested";
      const verb = (future, past) => (pending ? `PM Copilot will ${future}` : past);
      let what;
      let approveLabel;
      if (a.action === "github.create_issues") {
        approveLabel = p.issues.length === 1 ? "Approve and create the issue" : "Approve and create issues";
        what = section(label,
          h("p", null, `${verb("create", "Create")} ${plural(p.issues.length, "issue")} in ${p.repo}:`),
          h("div", null, p.issues.map((issue) => h("div", { class: "issue" },
            h("details", null, h("summary", null, issue.title),
              issue.body ? h("p", { class: "pre t-body muted" }, issue.body) : h("p", { class: "muted" }, "No description.")),
            h("span", { class: "t-caption muted" },
              [...(issue.labels || []), issue.milestone ? `milestone ${issue.milestone}` : null].filter(Boolean).join(" · "))))));
      } else {
        approveLabel = "Approve and move issues";
        what = section(label,
          h("p", null, `${verb("move", "Move")} ${plural(p.issue_numbers.length, "issue")} in ${p.repo} `
            + (p.milestone ? `to milestone ${p.milestone}:` : "out of their milestone:")),
          h("p", { class: "evidence" }, p.issue_numbers.map((n) => issueLink(p.repo, n))));
      }
      return [
        what,
        section("Why", h("p", { class: "pre" }, a.rationale)),
        a.change_request_id ? section("Implements", h("a", { href: `#/changes/${enc(a.change_request_id)}` }, a.change_request_id)) : null,
        pending
          ? decisionForm({ kind: "action_request", id: a.id, version: stored.version, approveLabel,
            footnote: stored.sandbox
              ? "This is a demo project, so approving only simulates the change; nothing is sent to GitHub."
              : "It runs with your GitHub token (GITHUB_TOKEN) right after you approve, and is logged under your "
                + "name. Rejecting records your reason; nothing changes on GitHub." })
          : decided(a.decided_by, a.decided_at, a.decision_note),
        actionResult(a),
        a.replaces ? h("p", { class: "hint" }, "Files ", h("a", { href: `#/inbox/action_request/${enc(a.replaces)}` }, a.replaces),
          " again.") : null,
        ["failed", "rejected"].includes(a.status) ? refileButton(a) : null,
      ];
    },
    change_request(stored, a) {
      return [
        h("p", { class: "pre" }, a.description),
        a.analysis ? impactTable(a.analysis, stored.analysis_current) : null,
        h("p", null, h("a", { class: "btn secondary", href: `#/changes/${enc(a.id)}` }, "Open the change request to decide")),
      ];
    },
    risk(stored, a) {
      return [
        section("The risk", h("p", null, `Because ${a.cause}, ${a.event} may happen, which would ${a.effect}.`)),
        section("Score", h("p", null, `Probability ${a.probability} × impact ${a.impact} = ${a.probability * a.impact}`,
          a.strategy ? ` · strategy: ${a.strategy}` : "", a.owner ? ` · owner: ${a.owner}` : "")),
        (a.responses || []).length ? section("Responses", h("ul", null, a.responses.map((r) => h("li", null, r)))) : null,
        (a.triggers || []).length ? section("Triggers", h("ul", null, a.triggers.map((t) => h("li", null, t)))) : null,
        a.status === "proposed" ? decisionForm({ kind: "risk", id: a.id, version: stored.version,
          approveLabel: "Promote to the register", footnote: "Promoting makes it an open risk that counts toward health." }) : null,
      ];
    },
    decision(stored, a) {
      return [
        section("Context", h("p", { class: "pre" }, a.context)),
        section("Decision", h("p", { class: "pre" }, a.decision)),
        section("Rationale", h("p", { class: "pre" }, a.rationale)),
        (a.alternatives || []).length ? section("Alternatives", h("ul", null, a.alternatives.map((x) => h("li", null, x)))) : null,
        a.status === "proposed" ? decisionForm({ kind: "decision", id: a.id, version: stored.version, approveLabel: "Accept decision" })
          : decided(a.decided_by, a.decided_at),
      ];
    },
    baseline(stored, a) {
      const current = stored.current_baseline;
      const rows = [
        ["Scope", `${plural(a.scope_item_ids.length, "item")}${a.planned_additions ? ` + ${a.planned_additions} planned` : ""}`,
          current ? `${current.scope_item_ids.length} items` : "—"],
        ["Points", fmtNumber(a.scope_points), current ? fmtNumber(current.scope_points) : "—"],
        ["Target", fmtDate(a.target_date, true), current ? fmtDate(current.target_date, true) : "—"],
        ["Forecast P50", fmtDate(a.forecast_p50), current ? fmtDate(current.forecast_p50) : "—"],
        ["Forecast P85", fmtDate(a.forecast_p85), current ? fmtDate(current.forecast_p85) : "—"],
      ];
      return [
        section("Why", h("p", { class: "pre" }, a.reason)),
        h("table", { class: "impact" },
          h("thead", null, h("tr", null, h("th", null, ""), h("th", { class: "t-overline" }, "This baseline"),
            h("th", { class: "t-overline" }, current ? `Current (${current.id})` : "Current"))),
          h("tbody", null, rows.map(([label, mine, theirs]) => h("tr", null, h("td", null, label), h("td", null, mine), h("td", null, theirs))))),
        a.status === "proposed" ? decisionForm({ kind: "baseline", id: a.id, version: stored.version, approveLabel: "Approve baseline",
          footnote: "Scope growth and forecast drift are measured against the approved baseline." }) : null,
      ];
    },
    charter(stored, a) {
      return [
        section("Purpose", h("p", { class: "pre" }, a.purpose)),
        section("Objectives", h("ul", null, a.objectives.map((o) => h("li", null, `${o.id}: ${o.statement}`)))),
        section("In scope", h("ul", null, a.in_scope.map((x) => h("li", null, x)))),
        (a.out_of_scope || []).length ? section("Out of scope", h("ul", null, a.out_of_scope.map((x) => h("li", null, x)))) : null,
        a.approved_by ? decided(a.approved_by, a.approved_at)
          : decisionForm({ kind: "charter", id: "charter", version: stored.version, approveLabel: "Approve charter", rejectable: false }),
      ];
    },
  };

  function refileButton(a) {
    const again = button("File it again", {
      onclick: async () => {
        setBusy([again], true);
        try {
          const out = await api("POST", P(`/items/action_request/${enc(a.id)}/refile`), {});
          toast(`Filed again as ${out.id}. It waits for your approval.`);
          refreshCounts();
          location.hash = `#/inbox/action_request/${enc(out.id)}`;
        } catch (e) { toast(e.message, true); setBusy([again], false); }
      },
    });
    return h("div", { class: "actions" }, again,
      h("span", { class: "hint" }, a.status === "failed" ? "Issues it already created are left out." : "Nothing ran the first time."));
  }

  function decided(by, at, note) {
    if (!by) return null;
    return h("p", { class: "callout" }, `Decided by ${by}${at ? ` on ${fmtWhen(at)}` : ""}${note ? `: “${note}”` : "."}`);
  }

  // ----- change requests -----------------------------------------------------

  function impactTable(analysis, current) {
    const b = analysis.before;
    const x = analysis.after;
    const cell = (before, after) => [h("td", null, before), h("td", { class: before !== after ? "changed" : null }, after)];
    const pts = (s) => (s.scope_points === null ? `${s.scope_items} items` : `${s.scope_items} items · ${fmtNumber(s.scope_points)} pts`);
    const done = (s) => (s.done_by_target_p85 === null ? "—" : `${s.done_by_target_p85} of ${s.remaining_items}`);
    const rows = [
      ["Remaining items", String(b.remaining_items), String(x.remaining_items)],
      ["Scope", pts(b), pts(x)],
      b.target_date !== x.target_date ? ["Target date", fmtDate(b.target_date, true), fmtDate(x.target_date, true)] : null,
      ["Forecast P50", fmtDate(b.forecast_p50), fmtDate(x.forecast_p50)],
      ["Forecast P85", fmtDate(b.forecast_p85), fmtDate(x.forecast_p85)],
    ].filter(Boolean);
    const touches = [];
    if (analysis.objectives_affected.length) touches.push(`Objectives affected: ${analysis.objectives_affected.join(", ")}.`);
    if (analysis.risks_linked.length) touches.push(`Linked risks: ${analysis.risks_linked.join(", ")}.`);
    return h("div", { class: "stack-sm" },
      current === false ? h("p", { class: "callout warn" }, "The proposal changed after this analysis. Compute it again before deciding.") : null,
      h("table", { class: "impact" },
        h("thead", null, h("tr", null, h("th", null, h("span", { class: "visually-hidden" }, "Measure")),
          h("th", { class: "t-overline" }, "Before"), h("th", { class: "t-overline" }, "After"))),
        h("tbody", null,
          rows.map(([label, before, after]) => h("tr", null, h("td", null, label), cell(before, after))),
          h("tr", null, h("td", null, "Schedule"), h("td", null, ragBadge(b.schedule_rag)), h("td", null, ragBadge(x.schedule_rag))),
          h("tr", null, h("td", null, "Done by target (P85)"), cell(done(b), done(x))))),
      touches.length || analysis.notes.length
        ? h("div", { class: "callout t-caption" }, touches.join(" "), analysis.notes.map((n) => h("div", null, n))) : null,
      h("p", { class: "hint" }, `Computed ${fmtWhen(analysis.computed_at)}.`));
  }

  function proposalSentence(p, milestone) {
    if (!p) return null;
    const parts = [];
    if (p.move_out_item_ids.length) {
      parts.push(`Move ${plural(p.move_out_item_ids.length, "item")} out of ${milestone || "the release"}`
        + (p.move_to_milestone ? ` to ${p.move_to_milestone}` : ""));
    }
    if (p.add_items) parts.push(`add ${plural(p.add_items, "new item")}${p.add_points !== null ? ` (${fmtNumber(p.add_points)} pts)` : ""}`);
    if (p.new_target_date) parts.push(`set the target date to ${fmtDate(p.new_target_date, true)}`);
    const text = parts.join(", ");
    return h("div", { class: "panel" },
      h("p", null, `${text.charAt(0).toUpperCase()}${text.slice(1)}.`),
      p.move_out_item_ids.length ? h("p", { class: "evidence t-caption" }, p.move_out_item_ids.map(refLink)) : null);
  }

  async function changesPage() {
    const [{ items }, baseline] = await Promise.all([api("GET", P("/change-requests")), api("GET", P("/baseline"))]);
    const list = card(
      cardHeader("Change requests"),
      items.length ? h("div", null, items.map((c) => h("div", { class: "list-row" },
        h("div", { class: "grow" },
          h("a", { href: `#/changes/${enc(c.id)}`, class: "t-strong" }, `${c.id} · ${c.title}`),
          h("div", { class: "t-caption muted" }, c.analysis ? `Impact computed ${fmtWhen(c.analysis.computed_at)}` : "No impact analysis yet")),
        statusBadge(c.status))))
        : h("p", { class: "muted" }, "No change requests yet. Run the “Assess a change” playbook when scope or dates need to move."),
    );
    return [
      pageHeader("Change requests", {
        subtitle: "Changes to the agreed scope or dates. The agent drafts them with a computed impact; you decide.",
        actions: [h("a", { class: "btn secondary", href: "#/run/assess_and_implement_changes" }, "Assess a change")],
      }),
      h("div", { class: "grid-2" }, list, baselineCard(baseline)),
    ];
  }

  function baselineCard(data) {
    const b = data.baseline;
    const v = data.variance;
    const name = input({ placeholder: "e.g. Release 1 plan", maxlength: "120" });
    const reason = input({ placeholder: "Why now", maxlength: "500" });
    const error = h("p", { class: "error-text", role: "alert" });
    const propose = button("Propose a baseline", {
      onclick: async () => {
        error.textContent = "";
        setBusy([propose], true);
        try {
          const out = await api("POST", P("/baseline"), { name: name.value, reason: reason.value });
          toast(`${out.id} proposed. Approve it in your inbox.`);
          refreshCounts();
          location.hash = `#/inbox/baseline/${enc(out.id)}`;
        } catch (e) { error.textContent = e.message; setBusy([propose], false); }
      },
    });
    return card(
      cardHeader("Baseline", ragBadge(data.status.rag)),
      b ? h("div", { class: "stack-sm" },
        h("p", null, `${b.id} · ${b.name}, approved by ${b.approved_by} on ${fmtWhen(b.approved_at)}.`),
        h("p", { class: "muted" }, data.status.explanation),
        v ? h("p", { class: "t-caption muted" },
          [`${v.added.length} added, ${v.removed.length} removed`,
            v.target_shift_days ? `target moved ${v.target_shift_days} days` : null,
            v.p85_drift_days !== null ? `P85 drifted ${v.p85_drift_days} days` : null].filter(Boolean).join(" · ")) : null)
        : h("p", { class: "muted" }, "No approved baseline yet, so scope growth can't be measured."),
      h("div", { class: "panel" },
        h("p", { class: "t-label" }, "Snapshot the release as it is now"),
        h("div", { class: "fields-2" }, field("Name", name), field("Reason", reason)),
        h("div", { class: "actions" }, propose), error),
    );
  }

  async function changePage(id) {
    const stored = await api("GET", P(`/items/change_request/${enc(id)}`));
    const a = stored.data;
    const imp = a.impact || {};
    const options = (a.options || []).map((option) => {
      const recommended = /\(recommended\)/i.test(option);
      const text = option.replace(/\s*\(recommended\)\s*/i, " ").trim();
      return h("div", { class: `option${recommended ? " recommended" : ""}` }, h("span", null, text),
        recommended ? badge("Recommended", "neutral", false) : null);
    });
    const words = [
      ["Scope", imp.scope], ["Schedule", imp.schedule_days !== null && imp.schedule_days !== undefined ? `${imp.schedule_days > 0 ? "+" : ""}${imp.schedule_days} days` : null],
      ["Cost", imp.cost !== null && imp.cost !== undefined ? String(imp.cost) : null], ["Risk", imp.risk], ["Benefits", imp.benefits],
    ].filter(([, value]) => value);

    const left = card(
      section("The change", h("p", { class: "pre" }, a.description), proposalSentence(a.proposal, stored.release_milestone)),
      section("Reason", h("p", { class: "pre" }, a.reason)),
      options.length ? section("Options considered", h("div", null, options)) : null,
      a.recommendation ? section("Recommendation", h("p", { class: "pre" }, a.recommendation)) : null,
      words.length ? section("Impact across the project", h("dl", { class: "stack-sm" }, words.map(([k, value]) =>
        h("div", null, h("dt", { class: "t-label" }, k), h("dd", { class: "muted" }, value))))) : null,
      imp.stakeholders ? section("Stakeholders", h("p", { class: "pre" }, imp.stakeholders)) : null,
    );

    const open = ["draft", "submitted"].includes(a.status);
    const assess = button(a.analysis ? "Compute again" : "Compute impact", {
      onclick: async () => {
        setBusy([assess], true);
        try { await api("POST", P(`/change-requests/${enc(id)}/assess`), {}); toast("Impact computed."); route(); }
        catch (e) { toast(e.message, true); setBusy([assess], false); }
      },
    });
    const analysisCard = card(
      cardHeader("Impact analysis", a.proposal && open && !stored.analysis_current ? assess : null),
      h("p", { class: "t-caption muted" },
        "Computed by PM Copilot from the release scope and throughput history. The agent can't edit these numbers."),
      a.analysis ? impactTable(a.analysis, stored.analysis_current)
        : h("p", { class: "callout" }, a.proposal ? "Not computed yet." : "This request has no machine-readable proposal, so its impact can't be computed. You can still decide on the description."),
    );

    let decision;
    if (open) {
      const p = a.proposal;
      const effects = ["records you as the decision maker"];
      if (p && p.new_target_date) effects.push(`moves the target to ${fmtDate(p.new_target_date, true)}`);
      if (p) effects.push("re-baselines the release");
      let footnote = `Approving ${effects.join(", ").replace(/, ([^,]*)$/, " and $1")}.`;
      if (p && p.move_out_item_ids.length) footnote += " Moving the issues on GitHub is then proposed as a separate action for you to approve.";
      const form = decisionForm({ kind: "change_request", id: a.id, version: stored.version, approveLabel: "Approve change", footnote,
        noteLabel: "Decision note (required to reject)" });
      if (p && !stored.analysis_current) {
        const approveButton = form.querySelector(".btn.primary");
        approveButton.dataset.locked = "true";
        approveButton.disabled = true;
        approveButton.title = "Compute the impact of the current proposal first";
      }
      decision = card(cardHeader("Your decision"), form);
    } else {
      decision = card(cardHeader("Decision"), decided(a.decided_by, a.decided_at, a.decision_rationale) || h("p", { class: "muted" }, a.status),
        (stored.actions || []).length ? h("div", null, stored.actions.map((act) => h("div", { class: "list-row" },
          h("a", { class: "grow", href: `#/inbox/action_request/${enc(act.id)}` }, `${act.id} · ${act.title}`), statusBadge(act.status)))) : null);
    }

    const history = h("div");
    const showHistory = button("View history", {
      variant: "link",
      onclick: async () => {
        setBusy([showHistory], true);
        try {
          const versions = await api("GET", P(`/items/change_request/${enc(id)}/history`));
          const rows = versions.items.slice().reverse().map((v) => h("div", { class: "list-row" },
            h("div", { class: "grow" },
              h("div", { class: "t-strong" }, `Version ${v.version} · ${v.actor}`),
              h("div", { class: "t-caption muted" }, [fmtWhen(v.created_at), v.rationale].filter(Boolean).join(" · ")))));
          history.replaceChildren(card(cardHeader("History"), h("div", null, rows)));
          showHistory.remove();
        } catch (e) { toast(e.message, true); setBusy([showHistory], false); }
      },
    });

    return [
      pageHeader(`${a.id} · ${a.title}`, {
        badges: [statusBadge(a.status)],
        subtitle: `Requested by ${a.requested_by || "unknown"} · last written by ${stored.actor} ${fmtWhen(stored.created_at)}`,
        actions: [showHistory],
      }),
      h("div", { class: "grid-2" }, left, h("div", { class: "stack" }, analysisCard, decision)),
      history,
    ];
  }

  // ----- playbooks -----------------------------------------------------------

  async function playbookList() {
    if (!state.playbooks) state.playbooks = (await api("GET", "/api/playbooks")).items;
    return state.playbooks;
  }

  async function runPage(name) {
    const playbooks = await playbookList();
    const chosen = playbooks.find((p) => p.name === name) || playbooks.find((p) => p.name === "manage_communications") || playbooks[0];
    const runs = (await api("GET", P(`/runs?playbook=${enc(chosen.name)}&limit=5`))).items;

    const list = h("div", null, playbooks.map((p) => h("a", { class: "pb", href: `#/run/${p.name}`, "aria-current": p === chosen ? "true" : null },
      h("div", null, h("div", { class: "t-strong" }, p.title), h("div", { class: "t-caption muted" }, p.processes.join(" · "))),
      badge(`L${p.autonomy} ${p.autonomy_label}`, "neutral", false))));

    const material = h("textarea", { class: "textarea", maxlength: "100000",
      placeholder: "Paste meeting notes, a request, or anything this run should consider." });
    const model = input({ value: "claude-opus-5", spellcheck: "false" });
    const effort = select([["low", "low"], ["medium", "medium"], ["high", "high"], ["xhigh", "xhigh"], ["max", "max"]], "high");
    const turns = input({ type: "number", min: "1", max: "100", value: "30" });
    const output = h("div", { "aria-live": "polite" });
    const runNow = button("Run now", { variant: "primary" });
    const jobKey = `pm-copilot-job:${state.project}:${chosen.name}`;

    function showJob(job) {
      if (job.status === "running") {
        output.replaceChildren(h("div", { class: "callout" }, h("span", { class: "spinner", "aria-hidden": "true" }), " ",
          `Running ${chosen.title}… this can take a few minutes. You can leave this page; the run carries on.`));
        return;
      }
      if (job.status === "error") {
        output.replaceChildren(h("div", { class: "callout bad" }, job.error));
        return;
      }
      const r = job.result;
      const tokens = r.usage ? (r.usage.input_tokens || 0) + (r.usage.output_tokens || 0) : 0;
      output.replaceChildren(h("div", { class: "panel" },
        h("div", { class: "card-header" }, h("span", { class: "t-strong" }, "Result"), statusBadge(r.status)),
        r.error ? h("p", { class: "error-text" }, r.error) : null,
        r.summary ? h("div", { class: "pre run-output" }, r.summary) : null,
        h("p", { class: "hint" }, `${plural(r.turns, "turn")} · ${plural(r.tool_calls.length, "tool call")} · ${tokens.toLocaleString()} tokens`
          + (r.served_by && r.served_by.length > 1 ? ` · answered by ${r.served_by.join(", ")}` : "")),
        r.status === "completed" ? h("p", null, h("a", { href: "#/inbox" }, "Check your inbox for anything it proposed →")) : null));
    }

    async function poll(jobId) {
      try {
        const job = await api("GET", `/api/jobs/${enc(jobId)}`);
        if (!output.isConnected) return;  // the user navigated away; the run continues
        showJob(job);
        if (job.status === "running") { setTimeout(() => poll(jobId), 2500); return; }
        tabStore.remove(jobKey);
        setBusy([runNow], false);
        refreshCounts();
      } catch (e) {
        tabStore.remove(jobKey);
        setBusy([runNow], false);
        if (output.isConnected) output.replaceChildren(h("div", { class: "callout bad" }, e.message));
      }
    }

    runNow.addEventListener("click", async () => {
      setBusy([runNow], true);
      try {
        const { job_id: jobId } = await api("POST", P("/runs"), {
          playbook: chosen.name, material: material.value || null, model: model.value, effort: effort.value,
          max_turns: Number.parseInt(turns.value, 10),
        });
        tabStore.set(jobKey, jobId);
        showJob({ status: "running" });
        poll(jobId);
      } catch (e) {
        output.replaceChildren(h("div", { class: "callout bad" }, e.message));
        setBusy([runNow], false);
      }
    });
    const pending = tabStore.get(jobKey);
    if (pending) { setBusy([runNow], true); setTimeout(() => poll(pending), 0); }

    const detail = card(
      h("h2", { class: "t-title" }, chosen.title),
      h("p", { class: "muted" }, chosen.summary),
      h("div", { class: "stack-sm" },
        field("Material for this run (optional)", material, "Treated as information, never as instructions."),
        h("div", { class: "fields-3" }, field("Model", model), field("Effort", effort), field("Turn limit", turns)),
        h("div", { class: "actions" }, runNow,
          h("span", { class: "hint" }, "Logged as agent:copilot-runner. Runs call the Claude API and cost money.")),
        output),
    );
    const recent = card(cardHeader("Recent runs of this playbook"),
      runs.length ? h("div", null, runs.map(runRow)) : h("p", { class: "muted" }, "It hasn't run for this project yet."));

    return [
      pageHeader("Run a playbook", {
        subtitle: "Claude runs it now with the same tools and guardrails as a scheduled run. Proposals land in your inbox.",
      }),
      h("div", { class: "grid-list" }, list, h("div", { class: "stack" }, detail, recent)),
    ];
  }

  // ----- schedules -----------------------------------------------------------

  const CADENCE = { daily: "Every day", weekdays: "Weekdays", weekly: "Weekly" };
  const DAYS = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]];

  function scheduleRows(profile, playbooks, { removable }) {
    const title = (name) => (playbooks.find((p) => p.name === name) || { title: name }).title;
    if (!profile.schedules.length) return h("p", { class: "muted" }, "No schedules. Playbooks only run when you start them.");
    return h("div", null, profile.schedules.map((s) => {
      const when = s.cadence === "weekly" ? `${DAYS.find(([d]) => d === s.day)[1]}s` : CADENCE[s.cadence];
      const remove = removable ? button("Remove", {
        variant: "link",
        onclick: async () => {
          setBusy([remove], true);
          try { await api("DELETE", P(`/schedules/${enc(s.playbook)}`)); toast(`${title(s.playbook)} unscheduled.`); route(); }
          catch (e) { toast(e.message, true); setBusy([remove], false); }
        },
      }) : null;
      return h("div", { class: "list-row" },
        h("div", { class: "grow" }, h("div", { class: "t-strong" }, title(s.playbook)),
          h("div", { class: "t-caption muted" }, `${when} at ${s.time} · ${profile.timezone}${s.enabled ? "" : " · paused"}`)),
        remove);
    }));
  }

  async function schedulesPage() {
    const [{ profile }, playbooks] = await Promise.all([api("GET", P("/settings")), playbookList()]);
    const playbook = select(playbooks.map((p) => [p.name, p.title]), "monitor_and_control_performance");
    const cadence = select([["weekly", "Weekly"], ["weekdays", "Weekdays"], ["daily", "Every day"]], "weekly");
    const day = select(DAYS, "mon");
    const dayField = field("Day", day);
    const time = input({ type: "time", value: "08:00", required: true });
    cadence.addEventListener("change", () => { dayField.hidden = cadence.value !== "weekly"; });
    const error = h("p", { class: "error-text", role: "alert" });
    const add = button("Add schedule", {
      variant: "primary",
      onclick: async () => {
        error.textContent = "";
        setBusy([add], true);
        try {
          await api("POST", P("/schedules"), { playbook: playbook.value, cadence: cadence.value,
            day: cadence.value === "weekly" ? day.value : null, time: time.value });
          toast("Scheduled.");
          route();
        } catch (e) { error.textContent = e.message; setBusy([add], false); }
      },
    });
    return [
      pageHeader("Schedules", { subtitle: "Playbooks that run on their own, as agent:copilot-runner." }),
      h("div", { class: "grid-2" },
        card(cardHeader("Scheduled"), scheduleRows(profile, playbooks, { removable: true }),
          h("p", { class: "hint" },
            "Your machine runs `python -m pm_agent run-due` every 15 minutes (cron or Task Scheduler). A run missed by more "
            + "than a day is skipped, not sent late.")),
        card(cardHeader("Add a schedule"), h("div", { class: "stack-sm" },
          field("Playbook", playbook, "Adding one replaces that playbook's existing schedule."),
          h("div", { class: "fields-3" }, field("Cadence", cadence), dayField, field(`Time (${profile.timezone})`, time)),
          h("div", { class: "actions" }, add), error))),
    ];
  }

  // ----- settings ------------------------------------------------------------

  async function settingsPage() {
    const [{ profile, google }, playbooks] = await Promise.all([api("GET", P("/settings")), playbookList()]);
    const t = profile.thresholds;
    const text = (value, props) => input({ value: value === null || value === undefined ? "" : String(value), ...props });
    const f = {
      name: text(profile.name, { required: true }),
      repos: text(profile.repos.join(", "), { placeholder: "owner/app, owner/api" }),
      release_milestone: text(profile.release_milestone, { placeholder: "v1" }),
      github_project: text(profile.github_project, { placeholder: "owner/3" }),
      start_date: text(profile.start_date, { type: "date" }),
      target_date: text(profile.target_date, { type: "date" }),
      iteration_days: text(profile.iteration_days, { type: "number", min: "1", max: "90" }),
      timezone: text(profile.timezone, { placeholder: "Asia/Kolkata" }),
      report_recipients: text(profile.report_recipients.join(", "), { placeholder: "sponsor@example.com" }),
      calendar_query: text(profile.calendar_query, { placeholder: "Checkout" }),
    };
    const num = (value, step) => text(value, { type: "number", step, min: "0" });
    const th = {
      spi_green: num(t.spi_green, "0.01"), spi_amber: num(t.spi_amber, "0.01"),
      aging_wip_amber: num(t.aging_wip_amber, "1"), aging_wip_red: num(t.aging_wip_red, "1"),
      blocked_amber: num(t.blocked_amber, "1"), blocked_red: num(t.blocked_red, "1"),
      risk_score_amber: num(t.risk_score_amber, "1"), risk_score_red: num(t.risk_score_red, "1"),
      scope_growth_amber: num(Math.round(t.scope_growth_amber * 100), "1"), scope_growth_red: num(Math.round(t.scope_growth_red * 100), "1"),
    };
    const pair = (label, a, b, suffix) => {
      a.id = uid("f");
      a.setAttribute("aria-label", `${label}: amber`);
      b.setAttribute("aria-label", `${label}: red`);
      return h("div", { class: "field" }, h("label", { for: a.id }, label),
        h("div", { class: "pair" }, a, h("span", { class: "muted" }, "/"), b), suffix ? h("p", { class: "hint" }, suffix) : null);
    };
    const error = h("div", { role: "alert" });
    const list = (value) => value.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
    const orNull = (value) => (value.trim() ? value.trim() : null);

    const save = button("Save changes", {
      variant: "primary",
      onclick: async () => {
        error.replaceChildren();
        setBusy([save], true);
        try {
          await api("PUT", P("/settings"), {
            profile: {
              name: f.name.value.trim(), repos: list(f.repos.value), release_milestone: orNull(f.release_milestone.value),
              github_project: orNull(f.github_project.value), start_date: orNull(f.start_date.value),
              target_date: orNull(f.target_date.value), iteration_days: Number.parseInt(f.iteration_days.value, 10),
              timezone: f.timezone.value.trim() || "UTC", report_recipients: list(f.report_recipients.value),
              calendar_query: orNull(f.calendar_query.value),
            },
            thresholds: {
              spi_green: Number(th.spi_green.value), spi_amber: Number(th.spi_amber.value),
              aging_wip_amber: Number(th.aging_wip_amber.value), aging_wip_red: Number(th.aging_wip_red.value),
              blocked_amber: Number(th.blocked_amber.value), blocked_red: Number(th.blocked_red.value),
              risk_score_amber: Number(th.risk_score_amber.value), risk_score_red: Number(th.risk_score_red.value),
              scope_growth_amber: Number(th.scope_growth_amber.value) / 100, scope_growth_red: Number(th.scope_growth_red.value) / 100,
            },
          });
          toast("Settings saved.");
          await loadSession();
          route();
        } catch (e) {
          error.replaceChildren(h("p", { class: "callout bad" }, e.message));
          setBusy([save], false);
        }
      },
    });

    const googlePanel = h("div", { class: "panel" },
      h("div", { class: "card-header" },
        h("div", null, h("div", { class: "t-strong" }, google.connected ? "Google connected" : "Google not connected"),
          h("div", { class: "t-caption muted" }, google.connected
            ? `Calendar (read), Gmail (drafts only; nothing is ever sent). Drive ${google.drive ? "granted" : "not granted"}.`
            : "Connect it to read meeting notes and put status reports in Gmail drafts.")),
        google.connected ? badge("Connected", "green") : badge("Off", "neutral")),
      h("p", { class: "hint" }, "Connect or reconnect from the terminal: ", h("span", { class: "mono" }, "python -m pm_agent google-auth --drive")));

    return [
      pageHeader("Settings", {
        subtitle: "Only you can change these. The agent can read them, but the store refuses its edits to schedules, recipients, the calendar filter and thresholds.",
        actions: [save],
      }),
      error,
      h("div", { class: "grid-2" },
        h("div", { class: "stack" },
          card(cardHeader("Project"), h("div", { class: "fields-2" },
            field("Name", f.name), field("GitHub repositories", f.repos),
            field("Release milestone", f.release_milestone), field("GitHub project board", f.github_project),
            field("Start date", f.start_date),
            field("Target date", f.target_date, "Changing it here skips change control; the baseline keeps its date."),
            field("Iteration length (days)", f.iteration_days), field("Timezone", f.timezone))),
          card(cardHeader("Who hears from the copilot, and what it can see"), h("div", { class: "stack-sm" },
            h("div", { class: "fields-2" }, field("Status email recipients", f.report_recipients), field("Calendar filter", f.calendar_query)),
            googlePanel))),
        h("div", { class: "stack" },
          card(cardHeader("Schedules", h("a", { href: "#/schedules", class: "t-label" }, "Add schedule")), scheduleRows(profile, playbooks, { removable: false })),
          card(cardHeader("RAG thresholds"),
            h("p", { class: "t-caption muted" }, "Colours are computed from these numbers. Tighten or loosen them with your team; the agent can't."),
            h("div", { class: "fields-2" },
              field("SPI green at", th.spi_green), field("SPI amber at", th.spi_amber),
              pair("Aging work amber / red", th.aging_wip_amber, th.aging_wip_red, "items"),
              pair("Blocked amber / red", th.blocked_amber, th.blocked_red, "items"),
              pair("Risk score amber / red", th.risk_score_amber, th.risk_score_red, "probability × impact"),
              pair("Scope growth amber / red", th.scope_growth_amber, th.scope_growth_red, "% since baseline"))))),
    ];
  }

  // ----- start ---------------------------------------------------------------

  function start() {
    window.addEventListener("hashchange", route);
    route();
  }

  start();
})();
