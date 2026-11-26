import { createGraph } from "./graph.js";
import { icon } from "./icons.js";
import {
  decisionCard,
  drawOnboarding,
  initOnboarding,
  refreshOnboarding,
  resetOnboarding,
  stopOnboardingCamera,
} from "./onboarding.js";

const $ = (id) => document.getElementById(id);
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
const state = {
  key: "",
  overview: null,
  datasets: [],
  account: "",
  detail: null,
  tab: "Evidence",
  view: "overview",
  count: 25,
  network: 0,
  ovNetwork: 0,
  ovMode: "graph",
  alertsAll: false,
  timelineDir: "all",
  timelineTerm: "",
  models: null,
};
const graphs = {};

const VIEWS = {
  overview: {
    label: "Overview",
    icon: "overview",
    title: "Overview",
  },
  investigate: {
    label: "Investigate",
    icon: "investigate",
    title: "Investigation desk",
  },
  networks: {
    label: "Networks",
    icon: "networks",
    title: "Network analysis",
  },
  onboarding: {
    label: "Onboarding",
    icon: "payments",
    title: "Onboarding",
  },
  datasets: {
    label: "Datasets",
    icon: "datasets",
    title: "Dataset intake",
  },
};

// Indicator families shown separately so rules, relationships and the model
// are never conflated. Points and ceilings come from the API.
const FAMILIES = [
  {
    name: "Transaction rules",
    icon: "zap",
    note: "Deterministic checks on this account's own ledger rows",
    members: [
      "Rapid onward transfers",
      "Multiple funding sources",
      "Distribution to multiple recipients",
      "Departure from account history",
      "Linked complaints",
    ],
  },
  {
    name: "Relationship signals",
    icon: "link",
    note: "Inferred from counterparties, devices and flow structure",
    members: [
      "Layering chain",
      "Complaint-linked counterparties",
      "Shared device",
      "Circular fund flow",
    ],
  },
  {
    name: "Model",
    icon: "model",
    note: "Isolation Forest peer comparison (heuristic)",
    members: ["Peer anomaly"],
  },
];

/* ---------------------------------------------------------------- utils */
const money = (paise) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(paise / 100);
const stamp = (iso) =>
  new Date(iso).toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
const safe = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
      c
      ],
  );
const lc = (s) => String(s || "").toLowerCase();
const base = () => "/api/datasets/" + encodeURIComponent(state.key);
const accountUrl = (id) => base() + "/accounts/" + encodeURIComponent(id);
const link = (id) =>
  '<button type="button" class="link" data-account="' +
  safe(id) +
  '">' +
  safe(id) +
  "</button>";
const badge = (text, cls = text) =>
  '<span class="badge ' + safe(lc(cls)) + '">' + safe(text) + "</span>";
const scorePill = (a) =>
  '<span class="score-pill ' +
  safe(lc(a.level)) +
  '" title="Risk score out of 100">' +
  a.score +
  "</span>";
const empty = (text, action = "") =>
  '<div class="empty"><p>' + safe(text) + "</p>" + action + "</div>";
const jsonOptions = (data) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});
const accountById = (id) => state.overview?.accounts.find((a) => a.id === id);
const plural = (n, word) =>
  n +
  " " +
  (n === 1
    ? word
    : word.endsWith("y")
      ? word.slice(0, -1) + "ies"
      : word + "s");
const shortHash = (h) => (h ? h.slice(0, 12) + "…" : "—");

// CSP forbids inline style attributes; widths are applied through the DOM.
function applyWidths(root) {
  root.querySelectorAll("[data-w]").forEach((e) => {
    e.style.width = Math.max(0, Math.min(100, Number(e.dataset.w))) + "%";
  });
  root.querySelectorAll("[data-left]").forEach((e) => {
    e.style.left = e.dataset.left + "%";
  });
}

function table(headers, rows, fallback, caption = "") {
  if (!rows.length) return empty(fallback);
  return (
    '<div class="table-wrap"><table>' +
    (caption
      ? '<caption class="sr-only">' + safe(caption) + "</caption>"
      : "") +
    "<thead><tr>" +
    headers
      .map((h) => {
        const [text, cls] = Array.isArray(h) ? h : [h, ""];
        return (
          '<th scope="col"' +
          (cls ? ' class="' + cls + '"' : "") +
          ">" +
          safe(text) +
          "</th>"
        );
      })
      .join("") +
    "</tr></thead><tbody>" +
    rows
      .map(
        (row) =>
          "<tr>" +
          row
            .map((cell, i) => {
              const h = headers[i];
              const cls = Array.isArray(h) ? h[1] : "";
              return (
                "<td" +
                (cls ? ' class="' + cls + '"' : "") +
                ">" +
                cell +
                "</td>"
              );
            })
            .join("") +
          "</tr>",
      )
      .join("") +
    "</tbody></table></div>"
  );
}

function setBusy(busy) {
  for (const el of [document.querySelector("main"), $("sidebar")]) {
    el.inert = busy;
  }
  document.querySelector("main").setAttribute("aria-busy", String(busy));
}
function notice(text, kind = "error") {
  $("notice-text").textContent = text;
  $("notice").classList.toggle("info", kind === "info");
  $("notice").hidden = !text;
}
async function api(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const detail = (await resp.json().catch(() => ({}))).detail;
    const message = Array.isArray(detail)
      ? detail
        .map((x) => x.loc.join(".") + " " + x.msg)
        .slice(0, 3)
        .join(" · ")
      : detail;
    throw new Error(message || "Request failed (" + resp.status + ")");
  }
  return resp.json();
}

function confirmAction(title, bodyHtml, okLabel) {
  const dialog = $("confirm");
  $("confirm-title").textContent = title;
  $("confirm-body").innerHTML = bodyHtml;
  $("confirm-ok").textContent = okLabel;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) =>
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "ok"),
      {
        once: true,
      },
    ),
  );
}

const swatch = (cls, label) =>
  '<span><svg viewBox="0 0 26 12" aria-hidden="true"><circle cx="6" cy="6" r="5" class="' +
  cls +
  '"/></svg>' +
  label +
  "</span>";
const LEGEND =
  swatch("lg-critical", "Critical ≥ 75") +
  swatch("lg-high", "High 55–74") +
  swatch("lg-review", "Review 35–54") +
  swatch("lg-low", "Low &lt; 35") +
  swatch("lg-context", "Counterparty") +
  '<span><svg viewBox="0 0 26 12" aria-hidden="true"><path d="M1 6h18" class="lg-flow" stroke-width="2"/><path d="M18 2l7 4-7 4z" class="lg-flow"/></svg>Payment (width ∝ amount)</span>' +
  '<span><svg viewBox="0 0 26 12" aria-hidden="true"><path d="M1 6h24" class="lg-device" stroke-width="2" stroke-dasharray="5 4"/></svg>Shared device (correlation)</span>' +
  "<span>Node size ∝ risk score</span>";

/* --------------------------------------------------------- graph data */
function nodeFor(id, core) {
  const a = accountById(id) || { id, name: id, score: 0, level: "Low" };
  return {
    id,
    name: a.name,
    score: a.score,
    level: a.level,
    role: a.role,
    core,
  };
}
function networkData(net) {
  const nodes = [
    ...net.members.map((id) => nodeFor(id, true)),
    ...net.context.map((id) => nodeFor(id, false)),
  ];
  const edges = [...net.flows, ...net.boundary_flows].map((f) => ({
    source: f.sender,
    target: f.receiver,
    kind: "flow",
    amount: f.amount_paise,
    count: f.count,
  }));
  net.device_links.forEach((group, i) => {
    for (let j = 1; j < group.length; j++)
      edges.push({
        source: group[j - 1],
        target: group[j],
        kind: "device",
        device: net.devices[i],
      });
  });
  return { nodes, edges };
}
function egoData(d) {
  const layer = new Map([[d.id, 0]]);
  const pairs = new Map(),
    seen = new Set();
  const addTx = (t) => {
    if (seen.has(t.id)) return;
    seen.add(t.id);
    const k = t.sender + "|" + t.receiver;
    const p = pairs.get(k) || {
      source: t.sender,
      target: t.receiver,
      kind: "flow",
      amount: 0,
      count: 0,
    };
    p.amount += t.amount_paise;
    p.count += 1;
    pairs.set(k, p);
  };
  for (const t of d.trail) {
    if (!layer.has(t.receiver)) layer.set(t.receiver, t.hop);
    addTx(t);
  }
  for (const t of d.source_trail) {
    if (!layer.has(t.sender)) layer.set(t.sender, -t.hop);
    addTx(t);
  }
  const edges = [...pairs.values()];
  for (const peer of d.device_peers) {
    if (!layer.has(peer)) layer.set(peer, 0);
    edges.push({ source: d.id, target: peer, kind: "device" });
  }
  const nodes = [...layer.keys()].map((id) => {
    const a = accountById(id);
    return {
      ...nodeFor(id, id === d.id || (a && a.score >= 35)),
      layer: layer.get(id),
    };
  });
  return { nodes, edges };
}
function mountGraph(slot, host, data, opts) {
  graphs[slot] = createGraph(host, {
    id: "g-" + slot,
    money,
    onOpen: (id) => openAccount(id),
    ...opts,
  });
  graphs[slot].update(data);
}

/* ------------------------------------------------------------ routing */
function navigate(view, entity) {
  const hash = "#/" + view + (entity ? "/" + encodeURIComponent(entity) : "");
  if (location.hash === hash) route();
  else location.hash = hash;
}
function openAccount(id) {
  state.tab = state.view === "investigate" ? state.tab : "Evidence";
  navigate("investigate", id);
}
async function route() {
  const [, view = "overview", entity = ""] = location.hash.split("/");
  const name = VIEWS[view] ? view : "overview";
  const id = decodeURIComponent(entity);
  show(name);
  if (!state.overview) return;
  if (name === "investigate" && id && id !== state.account) {
    if (!accountById(id)) notice("Account " + id + " is not in this dataset");
    else await selectAccount(id);
  }
  if (name === "networks" && id) {
    const i = state.overview.networks.findIndex((n) => n.id === id);
    if (i >= 0) state.network = i;
    drawNetworks();
  }
  if (name === "onboarding") await refreshOnboarding(id || undefined);
  setCrumb(
    name,
    name === "investigate" || name === "networks" || name === "onboarding"
      ? name === "investigate"
        ? state.account
        : id
      : "",
  );
}
function setCrumb(view, entity) {
  $("crumb-view").textContent = VIEWS[view].label;
  $("crumb-entity").hidden = !entity;
  $("crumb-entity").textContent = entity || "";
  $("crumb-view").classList.toggle("current", !entity);
  $("crumb-entity").classList.toggle("current", !!entity);
}
function show(name) {
  if (name !== "onboarding") stopOnboardingCamera();
  closeDrawer();
  const changed = state.view !== name;
  state.view = name;
  for (const id of Object.keys(VIEWS)) $(id).hidden = id !== name;
  document.querySelectorAll("#nav [data-view]").forEach((b) => {
    if (b.dataset.view === name) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  $("title").textContent = VIEWS[name].title;
  document.title = VIEWS[name].label + " · MULEX";
  setCrumb(name, "");
  if (!state.overview) return;
  if (name === "overview") drawOverviewGraph();
  if (name === "investigate" && state.detail) drawDetail();
  if (name === "networks") drawNetworks();
  if (name === "datasets") drawWorkspaces();
  if (changed) {
    window.scrollTo(0, 0);
    $("content").focus({ preventScroll: true });
  }
}

/* -------------------------------------------------------------- shell */
function drawNav() {
  $("nav").innerHTML = Object.entries(VIEWS)
    .map(
      ([id, v]) =>
        '<li><button type="button" data-view="' +
        id +
        '" title="' +
        v.label +
        '">' +
        icon(v.icon) +
        '<span class="label">' +
        v.label +
        '</span><span class="nav-count" id="nav-count-' +
        id +
        '" hidden></span></button></li>',
    )
    .join("");
  $("menu-toggle").innerHTML = icon("menu");
  $("notice-close").innerHTML = icon("close");
  $("search-icon").innerHTML = icon("search");
  $("dz-icon").innerHTML = icon("upload");
  $("bell").innerHTML =
    icon("bell") + '<span class="bell-dot" id="bell-count" hidden></span>';
  $("alerts-icon").innerHTML = icon("bell");
  $("network-icon").innerHTML = icon("networks");
  setCollapsed(localStorage.getItem("mulex.nav") === "collapsed");
}
function setCollapsed(collapsed) {
  document.querySelector(".app").classList.toggle("nav-collapsed", collapsed);
  const b = $("collapse-toggle");
  b.innerHTML = icon(collapsed ? "chevronsRight" : "chevronsLeft");
  b.setAttribute(
    "aria-label",
    collapsed ? "Expand navigation" : "Collapse navigation",
  );
  b.setAttribute("aria-expanded", String(!collapsed));
  b.title = b.getAttribute("aria-label");
  localStorage.setItem("mulex.nav", collapsed ? "collapsed" : "expanded");
}
const THEME_KEY = "mulex.theme";
function initialTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  return saved === "light" ? "light" : "dark";
}
function applyTheme(theme) {
  const mode = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = mode;
  const t = $("theme-toggle");
  if (t) {
    t.innerHTML = icon(mode === "dark" ? "sun" : "moon");
    const label =
      mode === "dark"
        ? "Switch to light brief theme"
        : "Switch to dark forensics theme";
    t.setAttribute("aria-label", label);
    t.setAttribute("aria-pressed", String(mode === "light"));
    t.title = label;
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = mode === "light" ? "#EEF2F6" : "#10243A";
}
function setTheme(theme) {
  const mode = theme === "light" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, mode);
  applyTheme(mode);
}
function navCount(view, n, label) {
  const el = $("nav-count-" + view);
  el.hidden = !n;
  el.textContent = n;
  el.setAttribute("aria-label", n + " " + label);
}
function openDrawer() {
  $("sidebar").classList.add("open");
  $("scrim").hidden = false;
  $("menu-toggle").setAttribute("aria-expanded", "true");
  $("sidebar").querySelector("nav button").focus();
}
function closeDrawer() {
  if (!$("sidebar").classList.contains("open")) return;
  $("sidebar").classList.remove("open");
  $("scrim").hidden = true;
  $("menu-toggle").setAttribute("aria-expanded", "false");
}

/* ---------------------------------------------------------- data load */
async function datasets(selected) {
  state.datasets = await api("/api/datasets");
  $("dataset").replaceChildren(
    ...state.datasets.map((d) => new Option(d.name, d.id)),
  );
  const key =
    state.datasets.find((d) => d.id === selected)?.id || state.datasets[0]?.id;
  if (key) {
    $("dataset").value = key;
    await load(key);
  }
}
async function load(key, desired) {
  notice("");
  const overview = await api("/api/datasets/" + encodeURIComponent(key));
  if (!overview.onboarding) {
    notice(
      "The running server is an older MULEX version. Stop it (Ctrl+C) and start it again with python -m app, then reload this page.",
    );
    overview.onboarding = {
      sessions: 0,
      blocked: 0,
      review: 0,
      verified: 0,
      opened: 0,
      network_hits: 0,
      recent: [],
    };
    overview.alerts = overview.alerts || [];
  }
  const switched = key !== state.key;
  state.key = key;
  state.overview = overview;
  if (switched) {
    state.detail = null;
    state.account = "";
    state.network = 0;
    state.ovNetwork = 0;
  }
  state.ovNetwork = Math.min(
    state.ovNetwork,
    Math.max(0, overview.networks.length - 1),
  );
  state.network = Math.min(
    state.network,
    Math.max(0, overview.networks.length - 1),
  );
  const all = overview.accounts;
  const urgent = overview.alerts.filter(
    (a) => a.severity === "Critical" || a.severity === "High",
  ).length;
  $("bell-count").hidden = !urgent;
  $("bell-count").textContent = urgent;
  $("bell").setAttribute(
    "aria-label",
    "Priority alerts: " + urgent + " critical or high",
  );
  navCount(
    "overview",
    overview.alerts.filter((a) => a.severity === "Critical").length,
    "critical alerts",
  );
  navCount(
    "onboarding",
    overview.onboarding.review,
    "onboarding sessions in manual review",
  );
  if (switched) resetOnboarding();
  drawKpis();
  drawOverview();
  drawQueue();
  if (state.view === "onboarding") drawOnboarding();
  if (state.view === "networks") drawNetworks();
  if (state.view === "datasets") drawWorkspaces();
  const focus =
    (desired && accountById(desired) && desired) ||
    (state.account && accountById(state.account) && state.account) ||
    all[0].id;
  await selectAccount(focus);
}
async function selectAccount(id) {
  state.account = id;
  state.detail = null;
  drawQueue();
  const key = state.key;
  $("detail").innerHTML =
    '<div class="card"><p class="muted">Loading account evidence…</p></div>';
  const detail = await api(accountUrl(id));
  if (key !== state.key || id !== state.account) return;
  state.detail = detail;
  if (state.view === "investigate") {
    drawDetail();
    setCrumb("investigate", id);
  }
}

/* ----------------------------------------------------------- overview */
function drawKpis() {
  const o = state.overview,
    all = o.accounts;
  const crit = all.filter((a) => a.level === "Critical").length;
  const high = all.filter((a) => a.level === "High").length;
  const entry = o.onboarding;
  const cards = [
    [
      "users",
      "Monitored accounts",
      all.length,
      plural(o.count_transactions, "transfer") + " analysed",
      "",
    ],
    [
      "alert",
      "High or critical leads",
      crit + high,
      crit + " critical · " + high + " high",
      crit ? "warn" : "",
    ],
    [
      "file",
      "Linked complaints",
      o.count_complaints,
      plural(o.incidents.length, "correlated incident"),
      "",
    ],
    [
      "networks",
      "Suspicious networks",
      o.networks.length,
      plural(o.cycles.length, "circular flow"),
      "",
    ],
    [
      "payments",
      "Onboarding blocked",
      entry.blocked,
      entry.sessions
        ? entry.review + " in review · " + entry.opened + " opened"
        : "No V-CIP sessions yet",
      entry.blocked ? "warn" : "",
    ],
    ["folder", "Open cases", o.open_cases, "Active reviews", ""],
  ];
  $("kpis").innerHTML = cards
    .map(
      ([ic, label, value, note, cls]) =>
        '<div class="kpi ' +
        cls +
        '"><span class="kpi-icon">' +
        icon(ic) +
        "</span><small>" +
        label +
        "</small><strong>" +
        value +
        '</strong><span class="kpi-note">' +
        safe(note) +
        "</span></div>",
    )
    .join("");
}
function signalBars(a, compact = false) {
  const reasons = new Map(a.reasons.map((r) => [r.indicator, r]));
  const out = FAMILIES.map((fam) => {
    const rows = fam.members
      .filter((m) => reasons.has(m))
      .map((m) => reasons.get(m));
    if (!rows.length) return "";
    return (
      '<div class="signal-group"><h3>' +
      icon(fam.icon) +
      safe(fam.name) +
      "</h3>" +
      (compact ? "" : '<p class="basis">' + safe(fam.note) + "</p>") +
      rows
        .map((r) => {
          const pct = (100 * r.points) / r.maximum;
          return (
            '<div class="signal"><span class="signal-name">' +
            safe(r.indicator) +
            '</span><span class="signal-points">+' +
            r.points +
            "<small>of " +
            r.maximum +
            '</small></span><span class="bar" role="img" aria-label="' +
            r.points +
            " of " +
            r.maximum +
            ' points"><span data-w="' +
            pct.toFixed(1) +
            '"></span></span>' +
            (r.evidence
              ? '<p class="signal-evidence">' + safe(r.evidence) + "</p>"
              : "") +
            "</div>"
          );
        })
        .join("") +
      "</div>"
    );
  }).join("");
  return out || empty("No positive indicators for this account.");
}
function drawOverview() {
  const o = state.overview;
  // Alerts
  const alerts = state.alertsAll ? o.alerts : o.alerts.slice(0, 4);
  $("alerts-all").hidden = o.alerts.length <= 4;
  $("alerts-all").innerHTML = state.alertsAll
    ? "Show fewer"
    : "View all alerts (" + o.alerts.length + ") " + icon("arrowRight");
  $("alerts").innerHTML =
    alerts
      .map(
        (a) =>
          '<article class="alert sev-' +
          lc(a.severity) +
          '">' +
          badge(a.severity) +
          '<span class="alert-kind">' +
          safe(a.kind) +
          "</span><strong>" +
          safe(a.title) +
          "</strong><p>" +
          safe(a.detail) +
          '</p><button type="button" class="link open-link" ' +
          (a.session
            ? 'data-session="' + safe(a.session) + '">Open ' + safe(a.session)
            : 'data-account="' +
            safe(a.account) +
            '">Open ' +
            safe(a.account)) +
          icon("external") +
          "</button></article>",
      )
      .join("") || empty("No alerts for this dataset.");
  // Network selector
  $("ov-net-select").innerHTML = o.networks
    .map(
      (n, i) =>
        '<button type="button" data-ov-net="' +
        i +
        '" aria-pressed="' +
        (i === state.ovNetwork) +
        '">' +
        safe(n.id) +
        "</button>",
    )
    .join("");
  $("ov-net-select").hidden = o.networks.length < 2;
  $("ov-legend").innerHTML = LEGEND;
  drawOverviewGraph();
  // Risk signals for the top lead
  const top = o.accounts[0];
  $("ov-signals-sub").innerHTML = top
    ? "Why " +
    link(top.id) +
    " scores " +
    top.score +
    " (" +
    safe(top.level) +
    ")"
    : "";
  $("ov-signals").innerHTML = top
    ? signalBars(top, true)
    : empty("No accounts.");
  applyWidths($("ov-signals"));
  // Priority leads
  const leads = o.accounts.slice(0, 8);
  $("ov-leads").innerHTML = table(
    [
      "Account",
      "Role",
      ["Risk", "num"],
      "Severity",
      ["Complaints", "num"],
      "Case",
    ],
    leads.map((a) => [
      link(a.id) + '<br><small class="muted">' + safe(a.name) + "</small>",
      safe(a.role || "—"),
      scorePill(a),
      badge(a.level),
      a.complaints,
      badge(a.case_status, a.case_status === "New" ? "plain" : a.case_status),
    ]),
    "No accounts in this dataset.",
    "Priority leads",
  );
  // Incidents
  $("incidents").innerHTML =
    o.incidents
      .map(
        (i) =>
          '<article class="incident" id="inc-' +
          safe(i.id) +
          '"><div class="incident-head"><strong>' +
          safe(i.id) +
          "</strong><span>" +
          plural(i.complaints.length, "complaint") +
          " · " +
          money(i.reported_value) +
          ' reported</span></div><div class="exports-inline"><a class="btn small" href="' +
          base() +
          "/incidents/" +
          encodeURIComponent(i.id) +
          '/report">' +
          icon("download") +
          'Referral PDF</a><a class="btn small" href="' +
          base() +
          "/incidents/" +
          encodeURIComponent(i.id) +
          '/evidence.json">' +
          icon("download") +
          'Referral JSON</a></div><p><span class="muted">Reported accounts</span> ' +
          i.reported_accounts.map(link).join(", ") +
          '</p><p><span class="muted">Network</span> ' +
          i.accounts.map(link).join(", ") +
          "</p>" +
          (i.common_beneficiaries.length
            ? '<p><span class="muted">Common beneficiary</span> ' +
            i.common_beneficiaries
              .map(
                (b) =>
                  link(b.account) +
                  " (paid by " +
                  b.payers.map(safe).join(", ") +
                  ")",
              )
              .join("; ") +
            "</p>"
            : "") +
          '<p class="muted small">' +
          i.categories.map(safe).join(" · ") +
          " · filed " +
          stamp(i.first) +
          " – " +
          stamp(i.last) +
          "</p></article>",
      )
      .join("") || empty("No complaints recorded in this dataset.");
  // Prevention summary: V-CIP onboarding
  const e = o.onboarding;
  const stageClass = (st) =>
    ({
      VERIFIED: "pass",
      APPROVED: "pass",
      MANUAL_REVIEW: "high",
      ESCALATED: "high",
      REJECTED: "critical",
      REJECTED_BY_OFFICER: "critical",
    })[st] || "plain";
  $("ov-prevention").innerHTML =
    '<div class="prevention-counts">' +
    [
      ["Sessions", e.sessions],
      ["Blocked", e.blocked],
      ["In review", e.review],
      ["Accounts opened", e.opened],
    ]
      .map(
        ([l, n]) =>
          '<div><small class="muted">' +
          l +
          "</small><strong>" +
          n +
          "</strong></div>",
      )
      .join("") +
    "</div>" +
    (e.network_hits
      ? '<p class="small"><b>' +
      plural(e.network_hits, "session") +
      "</b> matched mule-network intelligence (reused identity, reused face or a mule device).</p>"
      : "") +
    (e.recent.length
      ? '<ul class="mini-list">' +
      e.recent
        .slice(0, 4)
        .map(
          (r) =>
            "<li>" +
            badge(
              (r.stage || "").replace(/_/g, " ").toLowerCase(),
              stageClass(r.stage),
            ) +
            '<button type="button" class="link" data-session="' +
            safe(r.id) +
            '">' +
            safe(r.id) +
            "</button>" +
            '<span class="muted">' +
            safe(r.applicant) +
            "</span>" +
            (r.trust != null
              ? '<span class="amount">' + r.trust.toFixed(1) + "</span>"
              : "") +
            "</li>",
        )
        .join("") +
      "</ul>"
      : empty(
        "No onboarding sessions yet. Stop mule accounts before they open.",
        '<button type="button" class="btn small" data-view="onboarding">Start a V-CIP session</button>',
      ));
}
function drawOverviewGraph() {
  if (state.view !== "overview" || !state.overview) return;
  const o = state.overview,
    net = o.networks[state.ovNetwork];
  $("ov-graph").hidden = state.ovMode !== "graph";
  $("ov-table").hidden = state.ovMode !== "table";
  document
    .querySelectorAll("[data-ov-mode]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.ovMode === state.ovMode)),
    );
  if (!net) {
    $("ov-graph").innerHTML = empty(
      "No suspicious networks: no two accounts scoring 40+ share a transfer or device.",
    );
    $("ov-table").innerHTML = "";
    $("ov-stats").innerHTML = "";
    return;
  }
  $("ov-view-network").dataset.networkOpen = net.id;
  $("ov-view-network").innerHTML =
    "View " + safe(net.id) + " " + icon("arrowRight");
  const data = networkData(net);
  if (state.ovMode === "graph") {
    mountGraph("ov", $("ov-graph"), data, {
      label: net.id + " fund-flow graph. A table view is available.",
      compact: true,
      focus: net.members.reduce((a, b) =>
        accountById(a).score >= accountById(b).score ? a : b,
      ),
    });
  } else {
    $("ov-table").innerHTML = flowTable(data, net.id);
  }
  $("ov-stats").innerHTML = [
    ["Accounts in network", net.size],
    ["Internal flow", money(net.internal_flow_paise)],
    ["Counterparties", net.context.length],
    ["Shared device", net.devices.map(safe).join(", ") || "None"],
  ]
    .map(([k, v]) => "<div><dt>" + k + "</dt><dd>" + v + "</dd></div>")
    .join("");
}
function flowTable(data, caption) {
  return table(
    ["From", "To", "Relationship", ["Transfers", "num"], ["Amount", "num"]],
    data.edges
      .slice()
      .sort((a, b) => (b.amount || 0) - (a.amount || 0))
      .map((e) =>
        e.kind === "flow"
          ? [
            link(e.source),
            link(e.target),
            "Payment",
            e.count,
            money(e.amount),
          ]
          : [
            link(e.source),
            link(e.target),
            "Shared device " + safe(e.device || ""),
            "—",
            "—",
          ],
      ),
    "No relationships.",
    caption + " relationships",
  );
}

/* -------------------------------------------------------- investigate */
function drawQueue() {
  if (!state.overview) return;
  const term = lc($("search").value);
  const status = $("case-filter").value;
  const level = $("level-filter").value;
  const leads = state.overview.accounts.filter(
    (a) =>
      lc(a.id + " " + a.name).includes(term) &&
      (!status || a.case_status === status) &&
      (!level || a.level === level),
  );
  $("queue-count").textContent = plural(leads.length, "account");
  $("queue").innerHTML =
    leads
      .slice(0, state.count)
      .map(
        (a) =>
          '<button type="button" class="lead" data-account="' +
          safe(a.id) +
          '" aria-pressed="' +
          (a.id === state.account) +
          '"><span><strong>' +
          safe(a.id) +
          "</strong><small>" +
          safe(a.name) +
          (a.role ? " · " + safe(a.role) : "") +
          (a.case_status !== "New" ? " · " + safe(a.case_status) : "") +
          '</small></span><span class="lead-score">' +
          scorePill(a) +
          badge(a.level) +
          "</span></button>",
      )
      .join("") || empty("No accounts match these filters.");
  $("more").hidden = leads.length <= state.count;
}
function drawDetail() {
  const d = state.detail;
  if (!d) return;
  const tabs = [
    "Evidence",
    "Money trail",
    "Timeline",
    "Case file",
    "Onboarding",
  ];
  const net = state.overview.networks.find((n) => n.members.includes(d.id));
  const status = accountById(d.id)?.case_status || "New";
  const head =
    '<section class="card dossier-head" aria-labelledby="dossier-h"><div><p class="kicker">Account dossier</p><div class="dossier-id"><h2 id="dossier-h">' +
    safe(d.id) +
    "</h2>" +
    badge(d.level) +
    (d.role ? badge(d.role, "plain") : "") +
    badge("Case: " + status, status === "New" ? "plain" : status) +
    '</div><p class="muted">' +
    safe(d.name) +
    '</p></div><div class="dossier-actions"><a class="btn small" href="' +
    accountUrl(d.id) +
    '/report">' +
    icon("download") +
    'Export PDF</a><a class="btn small" href="' +
    accountUrl(d.id) +
    '/evidence.json">' +
    icon("download") +
    'Evidence JSON</a></div><div class="meter"><span class="meter-score">' +
    d.score +
    '<small>/100</small></span><div class="meter-track ' +
    lc(d.level) +
    '" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
    d.score +
    '" aria-label="Risk score ' +
    d.score +
    " of 100, " +
    safe(d.level) +
    '"><span class="fill" data-w="' +
    d.score +
    '"></span>' +
    [
      [35, "Review 35"],
      [55, "High 55"],
      [75, "Critical 75"],
    ]
      .map(
        ([v, l]) =>
          '<span class="tick" data-left="' +
          v +
          '"><span>' +
          l +
          "</span></span>",
      )
      .join("") +
    '</div></div><dl class="facts">' +
    [
      ["Incoming", money(d.incoming)],
      ["Outgoing", money(d.outgoing)],
      ["Forwarded within 15 min", money(d.rapid)],
      ["Complaints", d.filings.length],
      ["Incident", d.incident ? safe(d.incident.id) : "None"],
      [
        "Network",
        net
          ? '<button type="button" class="link" data-network-open="' +
          safe(net.id) +
          '">' +
          safe(net.id) +
          "</button>"
          : "None",
      ],
    ]
      .map(([k, v]) => "<div><dt>" + k + "</dt><dd>" + v + "</dd></div>")
      .join("") +
    "</dl></section>";
  let content = "";
  if (state.tab === "Evidence") content = evidenceTab(d);
  else if (state.tab === "Money trail") content = trailTab(d);
  else if (state.tab === "Timeline") content = timelineTab(d);
  else if (state.tab === "Case file") content = caseTab(d);
  else content = onboardingTab(d);
  $("detail").innerHTML =
    head +
    '<section class="card"><div class="tabs" role="tablist" aria-label="Dossier sections">' +
    tabs
      .map(
        (t) =>
          '<button type="button" role="tab" id="tab-' +
          t.replace(/\s/g, "") +
          '" data-tab="' +
          t +
          '" aria-selected="' +
          (t === state.tab) +
          '" tabindex="' +
          (t === state.tab ? 0 : -1) +
          '">' +
          t +
          "</button>",
      )
      .join("") +
    '</div><div role="tabpanel" aria-labelledby="tab-' +
    state.tab.replace(/\s/g, "") +
    '">' +
    content +
    "</div></section>";
  applyWidths($("detail"));
  if (state.tab === "Money trail") {
    mountGraph("trail", $("trail-graph"), egoData(d), {
      label:
        "Money trail around " + d.id + ". Tables below list every transfer.",
      focus: d.id,
    });
  }
}
function evidenceTab(d) {
  const reasonsSum = d.reasons.reduce((s, r) => s + r.points, 0);
  return (
    "<h3>Why this account scores " +
    d.score +
    '</h3><p class="basis">Contributions sum to ' +
    reasonsSum.toFixed(1) +
    " points. Scores prioritise review; they are not a finding of wrongdoing.</p>" +
    signalBars(d) +
    (d.incident
      ? '<h3>Correlated incident</h3><article class="incident"><div class="incident-head"><strong>' +
      safe(d.incident.id) +
      "</strong><span>" +
      plural(d.incident.complaints.length, "complaint") +
      " across " +
      plural(d.incident.reported_accounts.length, "reported account") +
      "</span></div><p>" +
      d.incident.complaints.map(safe).join(", ") +
      '</p><p><span class="muted">Network</span> ' +
      d.incident.accounts.map(link).join(", ") +
      "</p></article>"
      : "") +
    "<h3>Complaints against this account</h3>" +
    table(
      ["Complaint", "Category", "Linked transfer", "Filed"],
      d.filings.map((c) => [
        '<span class="mono">' + safe(c.id) + "</span>",
        safe(c.category),
        c.transfer ? '<span class="mono">' + safe(c.transfer) + "</span>" : "Account-level",
        stamp(c.timestamp),
      ]),
      "No complaints filed against this account.",
      "Complaints",
    ) +
    '<details><summary>Record a complaint</summary><form id="complaint-form" class="inline-form"><label class="field"><span>Complaint ID</span><input name="complaint_id" required maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]*"></label><label class="field"><span>Category</span><input name="category" required maxlength="120"></label><label class="field"><span>Linked transfer</span><select name="transfer"><option value="">Account-level</option>' +
    d.transactions.map((t) => "<option>" + safe(t.id) + "</option>").join("") +
    '</select></label><button class="btn primary">Record complaint</button></form></details>' +
    '<h3>Connected entities</h3><dl class="entity-list"><div><dt>' +
    icon("device") +
    "Shared device</dt><dd>" +
    (d.device_peers.map(link).join(", ") || "None") +
    '<br><small class="muted">Correlation signal, not proof of common control</small></dd></div><div><dt>' +
    icon("networks") +
    "Review cluster</dt><dd>" +
    (d.cluster.map(link).join(", ") || "None") +
    "</dd></div><div><dt>" +
    icon("repeat") +
    "Circular flow</dt><dd>" +
    (d.cycles
      .map(
        (c) =>
          c.members.concat(c.members[0]).map(link).join(" → ") +
          " in " +
          c.hours +
          " h",
      )
      .join("<br>") || "None") +
    "</dd></div></dl>"
  );
}
function trailRows(rows) {
  return rows.map((t) => [
    t.hop,
    '<span class="mono">' + safe(t.id) + "</span>",
    link(t.sender) + " → " + link(t.receiver),
    money(t.amount_paise),
    stamp(t.timestamp),
  ]);
}
function trailTab(d) {
  const cols = [
    ["Hop", "num"],
    "Transfer",
    "From → To",
    ["Amount", "num"],
    "Time",
  ];
  return (
    '<h3>Relationship graph</h3><div id="trail-graph" class="graph-host"></div><div class="legend">' +
    LEGEND +
    "<span>Columns: sources (left) → " +
    safe(d.id) +
    " → onward hops (right)</span></div>" +
    "<h3>Onward transfers · up to three hops</h3>" +
    table(
      cols,
      trailRows(d.trail),
      "No onward transfers.",
      "Onward transfers",
    ) +
    (d.trail_truncated
      ? '<p class="muted small">Showing the first 140 matching transfers.</p>'
      : "") +
    "<h3>Source of funds · up to three hops back</h3>" +
    table(
      cols,
      trailRows(d.source_trail),
      "No earlier inbound transfers.",
      "Source transfers",
    ) +
    (d.source_truncated
      ? '<p class="muted small">Showing the first 140 source transfers.</p>'
      : "") +
    '<p class="muted small">Onward hops are strictly later and source hops strictly earlier than the linking transfer. They are candidate routes; each row keeps its ledger reference. Times in ' +
    safe(TZ) +
    ".</p>"
  );
}
function timelineTab(d) {
  const term = lc(state.timelineTerm);
  const rows = d.transactions.filter((t) => {
    const incoming = t.receiver === d.id;
    if (state.timelineDir === "in" && !incoming) return false;
    if (state.timelineDir === "out" && incoming) return false;
    return !term || lc(t.id + " " + t.sender + " " + t.receiver).includes(term);
  });
  return (
    '<div class="toolbar"><div class="segmented" role="group" aria-label="Direction">' +
    [
      ["all", "All"],
      ["in", "Incoming"],
      ["out", "Outgoing"],
    ]
      .map(
        ([k, l]) =>
          '<button type="button" data-dir="' +
          k +
          '" aria-pressed="' +
          (state.timelineDir === k) +
          '">' +
          l +
          "</button>",
      )
      .join("") +
    '</div><label class="sr-only" for="timeline-term">Filter transfers</label><input id="timeline-term" type="search" placeholder="Filter by transfer or account" value="' +
    safe(state.timelineTerm) +
    '"><span class="tz">' +
    icon("clock") +
    " Newest first · " +
    safe(TZ) +
    "</span></div>" +
    table(
      ["Time", "Transfer", "Direction", "Counterparty", ["Amount", "num"]],
      rows.map((t) => {
        const incoming = t.receiver === d.id;
        return [
          stamp(t.timestamp),
          '<span class="mono">' + safe(t.id) + "</span>",
          incoming
            ? '<span class="dir-in">← Incoming</span>'
            : '<span class="dir-out">→ Outgoing</span>',
          link(incoming ? t.sender : t.receiver),
          money(t.amount_paise),
        ];
      }),
      "No transfers match.",
      "Transaction timeline",
    ) +
    '<p class="muted small">' +
    plural(rows.length, "transfer") +
    " of " +
    d.transactions.length +
    "</p>"
  );
}
function caseTab(d) {
  const current = d.case_history[0];
  const options = ["Investigating", "Escalated", "Closed"]
    .map(
      (s) =>
        "<option" +
        (current?.status === s ? " selected" : "") +
        ">" +
        s +
        "</option>",
    )
    .join("");
  return (
    '<h3>Record a review</h3><form id="case-form" class="inline-form" data-event="' +
    crypto.randomUUID() +
    '"><label class="field"><span>Analyst name</span><input name="analyst" required maxlength="120" autocomplete="name"></label><label class="field"><span>Case status</span><select name="status">' +
    options +
    '</select></label><label class="field span-all"><span>Review note</span><textarea name="note" required minlength="4" maxlength="1000" rows="3"></textarea></label><button class="btn primary">Save review</button></form>' +
    '<p class="muted small">Entries are append-only and revisioned. If someone else saved first you will be asked to reload.</p><h3>Audit history</h3>' +
    table(
      [["Revision", "num"], "Time", "Analyst", "Status", "Note"],
      d.case_history.map((e) => [
        e.revision,
        stamp(e.created),
        safe(e.analyst),
        badge(e.status),
        safe(e.note),
      ]),
      "No analyst entries yet. This lead is New.",
      "Case history",
    )
  );
}
function onboardingTab(d) {
  const o = d.onboarding;
  if (!o)
    return (
      "<h3>How this account entered the bank</h3>" +
      empty(
        "No V-CIP onboarding session is linked to this account. It was present in the imported ledger before onboarding screening.",
      )
    );
  return (
    '<h3>V-CIP onboarding record</h3><p class="muted small">' +
    safe(o.id) +
    " · " +
    safe(o.applicant) +
    " · " +
    safe(o.doc_type) +
    " " +
    safe(o.id_masked || "") +
    " · device " +
    safe(o.device_id) +
    "</p>" +
    decisionCard(o) +
    (o.officer
      ? '<p class="audit">' +
      icon("check") +
      " Officer " +
      safe(o.officer.officer) +
      " · " +
      safe(o.officer.action) +
      " (" +
      safe(o.officer.reason_code) +
      ") · " +
      stamp(o.officer.decided_at) +
      " · “" +
      safe(o.officer.note) +
      "”</p>"
      : "") +
    '<button type="button" class="btn small" data-session="' +
    safe(o.id) +
    '">Open in officer queue</button>'
  );
}

/* ----------------------------------------------------------- networks */
function drawNetworks() {
  const o = state.overview;
  if (!o || state.view !== "networks") return;
  const nets = o.networks;
  $("network-list").innerHTML =
    nets
      .map(
        (n, i) =>
          '<button type="button" class="lead" data-network="' +
          i +
          '" aria-pressed="' +
          (i === state.network) +
          '"><span><strong>' +
          safe(n.id) +
          "</strong><small>" +
          plural(n.size, "account") +
          " · " +
          plural(n.complaints, "complaint") +
          (n.devices.length ? " · device link" : "") +
          '</small></span><span class="lead-score"><span class="score-pill ' +
          (n.max_score >= 75
            ? "critical"
            : n.max_score >= 55
              ? "high"
              : "review") +
          '" title="Highest member risk">' +
          n.max_score +
          '</span><small class="muted">max risk</small></span></button>',
      )
      .join("") || empty("No connected high-risk accounts in this dataset.");
  const n = nets[state.network];
  const rings =
    "<h3>Circular fund flows</h3>" +
    table(
      ["Ring", ["Completed in", "num"], "Transfers"],
      o.cycles.map((c) => [
        c.members.concat(c.members[0]).map(link).join(" → "),
        c.hours + " h",
        c.transfers.map(safe).join(", "),
      ]),
      "No time-ordered rings within 72 hours.",
      "Circular flows",
    );
  if (!n) {
    $("network-detail").innerHTML = rings;
    return;
  }
  const data = networkData(n);
  $("network-detail").innerHTML =
    '<div class="card-head"><div><p class="kicker">Network</p><h2>' +
    safe(n.id) +
    '</h2><p class="card-sub">' +
    plural(n.size, "member") +
    " · " +
    plural(n.context.length, "direct counterparty") +
    " · internal flow " +
    money(n.internal_flow_paise) +
    (n.devices.length ? " · shared " + n.devices.map(safe).join(", ") : "") +
    '</p></div><div class="segmented" role="group" aria-label="View as"><button type="button" data-net-mode="graph" aria-pressed="' +
    (state.netMode !== "table") +
    '">Graph</button><button type="button" data-net-mode="table" aria-pressed="' +
    (state.netMode === "table") +
    '">Table</button></div></div>' +
    '<div id="net-graph" class="graph-host"' +
    (state.netMode === "table" ? " hidden" : "") +
    "></div>" +
    (state.netMode === "table" ? flowTable(data, n.id) : "") +
    '<div class="legend">' +
    LEGEND +
    "</div>" +
    "<h3>Members</h3>" +
    table(
      [
        "Account",
        "Role",
        ["Risk", "num"],
        "Severity",
        ["Complaints", "num"],
        "Case",
      ],
      n.members.map((m) => {
        const a = accountById(m);
        return [
          link(m),
          safe(a.role || "—"),
          scorePill(a),
          badge(a.level),
          a.complaints,
          badge(
            a.case_status,
            a.case_status === "New" ? "plain" : a.case_status,
          ),
        ];
      }),
      "",
      "Members",
    ) +
    "<h3>Payments inside the network</h3>" +
    table(
      ["From → To", ["Transfers", "num"], ["Total", "num"]],
      n.flows.map((f) => [
        link(f.sender) + " → " + link(f.receiver),
        f.count,
        money(f.amount_paise),
      ]),
      "No direct payments between members; linked by shared device.",
      "Internal payments",
    ) +
    rings;
  if (state.netMode !== "table") {
    const host = $("net-graph");
    mountGraph("net", host, data, {
      label:
        n.id +
        " network graph. Use the Table view for a list of relationships.",
    });
    host.classList.add("tall");
  }
}

/* ----------------------------------------------------------- datasets */
function drawWorkspaces() {
  $("workspaces").innerHTML =
    state.datasets
      .map(
        (d) =>
          '<div class="workspace-item"><span class="kpi-icon">' +
          icon("datasets") +
          "</span><div><strong>" +
          safe(d.name) +
          "</strong><small>Imported " +
          stamp(d.created) +
          "</small></div>" +
          (d.id === state.key
            ? badge("Active", "pass")
            : '<button type="button" class="btn small" data-dataset="' +
            safe(d.id) +
            '">Open</button>') +
          "</div>",
      )
      .join("") || empty("No workspaces yet.");
}
const CSV_ALIASES = {
  id: ["id", "txn_id", "transaction_id"],
  sender: ["sender", "sender_id", "from_account", "from"],
  receiver: ["receiver", "receiver_id", "to_account", "to"],
  amount: ["amount_paise", "amount"],
  timestamp: ["timestamp"],
};
function splitCsvLine(line) {
  const out = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        quoted = !quoted;
      }
    } else if (ch === "," && !quoted) {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}
async function checkFile(file) {
  const checks = [];
  const ok = (good, text) => checks.push([good, text]);
  const ext = lc(file.name).split(".").pop();
  ok(
    ext === "csv" || ext === "json",
    "File type is CSV or JSON (." + safe(ext) + ")",
  );
  ok(
    file.size <= 4_000_000,
    "Size within 4 MB (" + (file.size / 1e6).toFixed(2) + " MB)",
  );
  if (file.size <= 4_000_000 && ext === "csv") {
    const text = await file.text();
    const lines = text
      .replace(/^\uFEFF/, "")
      .split(/\r?\n/)
      .filter((l) => l.trim().length > 0);
    const header = lines[0] ? splitCsvLine(lines[0]).map((c) => c.trim()) : [];
    const matched = {};
    const missing = [];
    const duplicates = [];
    for (const [field, candidates] of Object.entries(CSV_ALIASES)) {
      const found = candidates.find((c) => header.includes(c));
      if (!found) missing.push(candidates[0]);
      else {
        matched[field] = found;
        if (header.filter((h) => h === found).length > 1) duplicates.push(found);
      }
    }
    const usedCols = new Set(Object.values(matched));
    const extraCols = header.filter((c) => c && !usedCols.has(c));
    const headerOk = !missing.length && !duplicates.length;
    ok(
      headerOk,
      headerOk
        ? extraCols.length
          ? "Required CSV columns found (" +
          plural(extraCols.length, "extra column") +
          " will be dropped)"
          : "All 5 required CSV columns found"
        : missing.length
          ? "Missing required columns: " + safe(missing.join(", "))
          : "Duplicate required columns: " + safe(duplicates.join(", ")),
    );
    const rows = lines.slice(1);
    ok(
      rows.length > 0 && rows.length <= 20_000,
      rows.length <= 20_000
        ? plural(rows.length, "data row") + " found"
        : plural(rows.length, "data row") +
        " found (exceeds 20,000 transfer limit)",
    );
    if (headerOk) {
      const amtCol = matched.amount;
      const amtIdx = header.indexOf(amtCol);
      const tsIdx = header.indexOf(matched.timestamp);
      const amtRe = amtCol === "amount_paise" ? /^\d+$/ : /^\d+(\.\d+)?$/;
      const bad = rows.findIndex((r) => {
        const c = splitCsvLine(r);
        const amt = (c[amtIdx] ?? "").trim();
        const ts = (c[tsIdx] ?? "").trim();
        return (
          c.length < header.length ||
          !amtRe.test(amt) ||
          Number(amt) <= 0 ||
          Number.isNaN(Date.parse(ts))
        );
      });
      ok(
        bad < 0,
        bad < 0
          ? "Amounts and timestamps are valid"
          : "Row " + (bad + 2) + " has an invalid amount or timestamp",
      );
    }
  } else if (file.size <= 4_000_000 && ext === "json") {
    try {
      const data = JSON.parse(await file.text());
      ok(true, "Valid JSON");
      const accounts = Array.isArray(data.accounts) ? data.accounts : [];
      const transactions = Array.isArray(data.transactions)
        ? data.transactions
        : Array.isArray(data.transfers)
          ? data.transfers
          : null;
      ok(
        accounts.length > 0 && accounts.length <= 3000,
        accounts.length > 3000
          ? "Accounts list (" + accounts.length + ") exceeds 3,000 limit"
          : "Has an accounts list (" + accounts.length + ")",
      );
      ok(
        Array.isArray(transactions) && transactions.length <= 20_000,
        transactions && transactions.length > 20_000
          ? "Transactions list (" + transactions.length + ") exceeds 20,000 limit"
          : "Has a transactions list (" + (transactions?.length ?? 0) + ")",
      );
      const datasetName =
        typeof data.name === "string" && data.name.trim()
          ? data.name.trim()
          : (
            file.name.replace(/\.json$/i, "").trim() || "Imported ledger"
          ).slice(0, 120);
      ok(
        true,
        typeof data.name === "string" && data.name.trim()
          ? "Has a dataset name (" + safe(datasetName) + ")"
          : "No dataset name in JSON; using filename (" +
          safe(datasetName) +
          ")",
      );
      const hasExtra =
        Object.keys(data).some(
          (k) =>
            !["name", "accounts", "transactions", "complaints"].includes(k),
        ) ||
        (Array.isArray(data.accounts) &&
          data.accounts.some((a) =>
            Object.keys(a || {}).some(
              (k) => !["id", "name", "device"].includes(k),
            ),
          )) ||
        (Array.isArray(data.transactions) &&
          data.transactions.some((t) =>
            Object.keys(t || {}).some(
              (k) =>
                !["id", "sender", "receiver", "amount_paise", "timestamp"].includes(
                  k,
                ),
            ),
          )) ||
        (Array.isArray(data.complaints) &&
          data.complaints.some((c) =>
            Object.keys(c || {}).some(
              (k) =>
                !["id", "account", "category", "timestamp", "transfer"].includes(
                  k,
                ),
            ),
          ));
      if (hasExtra) {
        ok(
          true,
          "Extra fields outside schema.py will be dropped during validation",
        );
      }
    } catch (e) {
      ok(false, "Valid JSON: " + safe(e.message));
    }
  }
  return checks;
}
async function onFileChosen() {
  const file = $("source-file").files[0];
  $("import-status").innerHTML = "";
  if (!file) {
    $("file-check").innerHTML = "";
    $("import-btn").disabled = true;
    return;
  }
  const checks = await checkFile(file);
  const valid = checks.every(([good]) => good);
  $("file-check").innerHTML =
    '<div class="status-box"><p class="file-summary">' +
    icon("file") +
    safe(file.name) +
    '</p><ul class="checks">' +
    checks
      .map(
        ([good, text]) =>
          '<li class="' +
          (good ? "ok" : "bad") +
          '">' +
          icon(good ? "check" : "alert") +
          "<span>" +
          text +
          "</span></li>",
      )
      .join("") +
    "</ul></div>";
  $("import-btn").disabled = !valid;
}

/* ------------------------------------------------------------- search */
let searchIndex = [],
  searchActive = -1,
  searchHits = [];
function buildSearch() {
  const o = state.overview;
  searchIndex = [
    ...o.accounts.map((a) => ({
      kind: "Account",
      id: a.id,
      text: a.name,
      extra: a.score + " · " + a.level,
    })),
    ...o.incidents.map((i) => ({
      kind: "Incident",
      id: i.id,
      text: plural(i.complaints.length, "complaint"),
      extra: i.reported_accounts.join(", "),
    })),
    ...o.networks.map((n) => ({
      kind: "Network",
      id: n.id,
      text: plural(n.size, "member"),
      extra: n.members.join(", "),
    })),
  ];
}
function runSearch() {
  const q = lc($("global-search").value.trim());
  const list = $("search-results");
  if (!q || !state.overview) {
    list.hidden = true;
    $("global-search").setAttribute("aria-expanded", "false");
    return;
  }
  buildSearch();
  searchHits = searchIndex
    .filter((r) => lc(r.id + " " + r.text + " " + r.extra).includes(q))
    .slice(0, 8);
  searchActive = searchHits.length ? 0 : -1;
  list.innerHTML =
    searchHits
      .map(
        (r, i) =>
          '<li role="option" id="sr-' +
          i +
          '" data-hit="' +
          i +
          '" aria-selected="' +
          (i === searchActive) +
          '"><strong>' +
          safe(r.id) +
          "</strong><small>" +
          safe(r.text) +
          '</small><span class="kind">' +
          r.kind +
          "</span></li>",
      )
      .join("") ||
    '<li role="option" aria-disabled="true"><small>No matches</small></li>';
  list.hidden = false;
  $("global-search").setAttribute("aria-expanded", "true");
  $("global-search").setAttribute(
    "aria-activedescendant",
    searchActive >= 0 ? "sr-" + searchActive : "",
  );
}
function chooseHit(i) {
  const r = searchHits[i];
  if (!r) return;
  $("global-search").value = "";
  runSearch();
  if (r.kind === "Account") openAccount(r.id);
  else if (r.kind === "Network") navigate("networks", r.id);
  else {
    navigate("overview");
    requestAnimationFrame(() => {
      const el = $("inc-" + r.id);
      if (el) {
        el.scrollIntoView({ block: "center" });
        el.classList.remove("flash");
        void el.offsetWidth;
        el.classList.add("flash");
      }
    });
  }
}

/* ------------------------------------------------------------- events */
document.addEventListener("click", async (e) => {
  const hit = e.target.closest("[data-hit]");
  if (hit) {
    chooseHit(Number(hit.dataset.hit));
    return;
  }
  const btn = e.target.closest("button");
  if (!btn) {
    if (!e.target.closest(".search")) $("search-results").hidden = true;
    return;
  }
  try {
    if (btn.dataset.view) return navigate(btn.dataset.view);
    if (btn.dataset.account) return openAccount(btn.dataset.account);
    if (btn.dataset.networkOpen)
      return navigate("networks", btn.dataset.networkOpen);
    if (btn.dataset.tab) {
      state.tab = btn.dataset.tab;
      drawDetail();
      $("tab-" + state.tab.replace(/\s/g, "")).focus();
      return;
    }
    if (btn.dataset.network) {
      state.network = Number(btn.dataset.network);
      return navigate("networks", state.overview.networks[state.network].id);
    }
    if (btn.dataset.ovNet) {
      state.ovNetwork = Number(btn.dataset.ovNet);
      document
        .querySelectorAll("[data-ov-net]")
        .forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      return drawOverviewGraph();
    }
    if (btn.dataset.ovMode) {
      state.ovMode = btn.dataset.ovMode;
      return drawOverviewGraph();
    }
    if (btn.dataset.netMode) {
      state.netMode = btn.dataset.netMode;
      return drawNetworks();
    }
    if (btn.dataset.dir) {
      state.timelineDir = btn.dataset.dir;
      return drawDetail();
    }
    if (btn.dataset.dataset) {
      $("dataset").value = btn.dataset.dataset;
      return $("dataset").dispatchEvent(new Event("change"));
    }
    if (btn.id === "alerts-all") {
      state.alertsAll = !state.alertsAll;
      return drawOverview();
    }
    if (btn.id === "more") {
      state.count += 25;
      return drawQueue();
    }
    if (btn.id === "menu-toggle") return openDrawer();
    if (btn.id === "collapse-toggle")
      return setCollapsed(
        !document.querySelector(".app").classList.contains("nav-collapsed"),
      );
    if (btn.id === "bell") {
      navigate("overview");
      return requestAnimationFrame(() => {
        $("ov-alerts-h").scrollIntoView({ block: "start" });
        $("alerts").querySelector("button")?.focus({ preventScroll: true });
      });
    }
    if (btn.id === "theme-toggle") {
      return setTheme(
        document.documentElement.dataset.theme === "light" ? "dark" : "light",
      );
    }
    if (btn.id === "notice-close") return notice("");
  } catch (err) {
    notice(err.message);
  }
});
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest("input, textarea, select");
  if (e.key === "/" && !typing) {
    e.preventDefault();
    $("global-search").focus();
  } else if (e.key === "Escape") {
    closeDrawer();
    $("search-results").hidden = true;
  }
  const tab = e.target.closest('[role="tab"]');
  if (tab && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
    const all = [...document.querySelectorAll('[role="tab"]')];
    const next =
      all[
      (all.indexOf(tab) + (e.key === "ArrowRight" ? 1 : all.length - 1)) %
      all.length
      ];
    next.click();
  }
});
$("global-search").addEventListener("input", runSearch);
$("global-search").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!searchHits.length) return;
    searchActive =
      (searchActive + (e.key === "ArrowDown" ? 1 : searchHits.length - 1)) %
      searchHits.length;
    document
      .querySelectorAll("#search-results [data-hit]")
      .forEach((li, i) =>
        li.setAttribute("aria-selected", String(i === searchActive)),
      );
    $("global-search").setAttribute(
      "aria-activedescendant",
      "sr-" + searchActive,
    );
  } else if (e.key === "Enter") {
    e.preventDefault();
    chooseHit(searchActive);
  }
});
$("scrim").addEventListener("click", closeDrawer);
document.addEventListener("input", (e) => {
  if (e.target.id === "timeline-term") {
    state.timelineTerm = e.target.value;
    const pos = e.target.selectionStart;
    drawDetail();
    const input = $("timeline-term");
    input.focus();
    input.setSelectionRange(pos, pos);
  }
});
$("dataset").onchange = async () => {
  setBusy(true);
  try {
    await load($("dataset").value);
    await route();
  } catch (e) {
    $("dataset").value = state.key;
    notice(e.message);
  } finally {
    setBusy(false);
  }
};
for (const id of ["case-filter", "level-filter"])
  $(id).onchange = () => {
    state.count = 25;
    drawQueue();
  };
$("search").oninput = () => {
  state.count = 25;
  drawQueue();
};
$("source-file").addEventListener("change", onFileChosen);
const dz = $("dropzone");
dz.addEventListener("dragover", (e) => {
  e.preventDefault();
  dz.classList.add("over");
});
dz.addEventListener("dragleave", () => dz.classList.remove("over"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dz.classList.remove("over");
  if (e.dataTransfer.files.length) {
    $("source-file").files = e.dataTransfer.files;
    onFileChosen();
  }
});

document.addEventListener("submit", async (e) => {
  const form = e.target,
    btn = e.submitter;
  if (form.method === "dialog") return; // native <dialog> closes itself
  e.preventDefault();
  if (document.querySelector("main").inert) return;
  if (!form.checkValidity()) {
    form.reportValidity();
    return;
  }
  setBusy(true);
  if (btn) btn.disabled = true;
  notice("");
  try {
    if (form.id === "import-form") {
      const file = $("source-file").files[0];
      $("import-status").innerHTML =
        '<div class="status-box">Importing ' + safe(file.name) + "…</div>";
      let result;
      try {
        if (lc(file.name).endsWith(".csv")) {
          const fd = new FormData();
          fd.set("file", file);
          result = await api("/api/datasets/csv", { method: "POST", body: fd });
        } else {
          const parsed = JSON.parse(await file.text());
          if (typeof parsed.name !== "string" || !parsed.name.trim()) {
            parsed.name = (
              file.name.replace(/\.json$/i, "").trim() || "Imported ledger"
            ).slice(0, 120);
          }
          result = await api("/api/datasets", jsonOptions(parsed));
        }
      } catch (err) {
        $("import-status").innerHTML =
          '<div class="status-box error"><strong>Import rejected; nothing was written.</strong><p>' +
          safe(err.message) +
          "</p></div>";
        return;
      }
      $("search").value = "";
      $("case-filter").value = "";
      $("level-filter").value = "";
      state.tab = "Evidence";
      await datasets(result.id);
      drawWorkspaces();
      $("import-status").innerHTML =
        '<div class="status-box success"><strong>Opened workspace “' +
        safe(state.overview.name) +
        "”.</strong><p>" +
        plural(state.overview.accounts.length, "account") +
        " · " +
        plural(state.overview.count_transactions, "transfer") +
        " · " +
        plural(state.overview.count_complaints, "complaint") +
        '</p><button type="button" class="btn small" data-view="overview">View overview</button></div>';
    } else if (form.id === "case-form") {
      const data = new FormData(form);
      await api(
        accountUrl(state.account) + "/case",
        jsonOptions({
          event_id: form.dataset.event,
          expected_revision: state.detail.case_history[0]?.revision ?? 0,
          analyst: data.get("analyst"),
          status: data.get("status"),
          note: data.get("note"),
        }),
      );
      await load(state.key, state.account);
    } else if (form.id === "complaint-form") {
      const formData = new FormData(form);
      await api(
        base() + "/complaints",
        jsonOptions({
          id: formData.get("complaint_id"),
          category: formData.get("category"),
          transfer: formData.get("transfer") || null,
          account: state.account,
          timestamp: new Date().toISOString(),
        }),
      );
      await load(state.key, state.account);
    }
  } catch (err) {
    notice(err.message);
  } finally {
    if (btn) btn.disabled = false;
    setBusy(false);
  }
});
window.addEventListener("pagehide", stopOnboardingCamera);
window.addEventListener("hashchange", () =>
  route().catch((e) => notice(e.message)),
);

async function start() {
  applyTheme(initialTheme());
  drawNav();
  initOnboarding({
    state,
    api,
    base,
    jsonOptions,
    safe,
    badge,
    stamp,
    empty,
    table,
    notice,
    setBusy,
    applyWidths,
    confirmAction,
    link,
    navigate,
    reload: () => load(state.key, state.account),
  });
  show(state.view);
  const [health] = await Promise.all([api("/api/health"), datasets()]);
  state.models = health.models;
  await route();
}
setBusy(true);
start()
  .catch((e) => notice(e.message))
  .finally(() => setBusy(false));
