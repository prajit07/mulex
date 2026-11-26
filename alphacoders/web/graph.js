// Accessible, dependency-free fund-flow graph.
// Directed edges are payments (arrowheads, width ~ log of amount); dashed
// undirected edges are shared-device correlations. Node size encodes risk.
import { icon } from "./icons.js";

const NS = "http://www.w3.org/2000/svg";
const COL = 220;
const ROW = 96;

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

const radius = (n) => (n.core ? 9 + (n.score || 0) / 7 : 6.5);

function layout(nodes, edges, COL, ROW) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const layer = new Map();
  if (nodes.every((n) => Number.isFinite(n.layer))) {
    nodes.forEach((n) => layer.set(n.id, n.layer));
  } else {
    // Shortest distance from accounts with no inbound payment in view.
    const flows = edges.filter((e) => e.kind === "flow");
    const out = new Map(nodes.map((n) => [n.id, []]));
    const indeg = new Map(nodes.map((n) => [n.id, 0]));
    for (const e of flows) {
      out.get(e.source).push(e.target);
      indeg.set(e.target, indeg.get(e.target) + 1);
    }
    let queue = nodes.filter((n) => !indeg.get(n.id)).map((n) => n.id);
    const seed = [...nodes].sort((a, b) => (b.score || 0) - (a.score || 0));
    while (layer.size < nodes.length) {
      if (!queue.length) queue = [seed.find((n) => !layer.has(n.id)).id];
      queue.forEach((id) => layer.has(id) || layer.set(id, 0));
      while (queue.length) {
        const id = queue.shift();
        for (const next of out.get(id)) {
          if (!layer.has(next)) {
            layer.set(next, layer.get(id) + 1);
            queue.push(next);
          }
        }
      }
    }
  }
  const min = Math.min(...layer.values());
  const columns = new Map();
  for (const n of nodes) {
    const l = layer.get(n.id) - min;
    if (!columns.has(l)) columns.set(l, []);
    columns.get(l).push(n.id);
  }
  const keys = [...columns.keys()].sort((a, b) => a - b);
  const rank = new Map();
  for (const l of keys) {
    const ids = columns.get(l);
    ids.sort((a, b) => {
      const ka = byId.get(a).core ? 0 : 1,
        kb = byId.get(b).core ? 0 : 1;
      return ka - kb || (byId.get(b).score || 0) - (byId.get(a).score || 0);
    });
    ids.forEach((id, i) => rank.set(id, i));
  }
  // Two barycentre sweeps reduce crossings between neighbouring columns.
  const neighbours = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    neighbours.get(e.source).push(e.target);
    neighbours.get(e.target).push(e.source);
  }
  for (let sweep = 0; sweep < 2; sweep++) {
    for (const l of keys.slice(1)) {
      const ids = columns.get(l);
      const centre = (id) => {
        const ref = neighbours
          .get(id)
          .filter((m) => layer.get(m) - min < l)
          .map((m) => rank.get(m));
        return ref.length
          ? ref.reduce((a, b) => a + b, 0) / ref.length
          : rank.get(id);
      };
      ids.sort((a, b) => centre(a) - centre(b));
      ids.forEach((id, i) => rank.set(id, i));
    }
  }
  // Counterparties pack tighter than scored accounts so core nodes stay large.
  const pos = new Map();
  for (const l of keys) {
    const ids = columns.get(l);
    const ys = [0];
    for (let i = 1; i < ids.length; i++) {
      const tight = !byId.get(ids[i]).core && !byId.get(ids[i - 1]).core;
      ys.push(ys[i - 1] + (tight ? ROW * 0.62 : ROW));
    }
    const mid = ys[ys.length - 1] / 2;
    ids.forEach((id, i) => pos.set(id, { x: l * COL, y: ys[i] - mid }));
  }
  return pos;
}

function curve(a, b, bend, ra, rb, tip) {
  const dx = b.x - a.x,
    dy = b.y - a.y,
    len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len,
    ny = dx / len;
  const cx = (a.x + b.x) / 2 + nx * bend,
    cy = (a.y + b.y) / 2 + ny * bend;
  const start = toward(a, { x: cx, y: cy }, ra);
  const end = toward(b, { x: cx, y: cy }, rb + tip);
  return {
    d: `M${start.x},${start.y} Q${cx},${cy} ${end.x},${end.y}`,
    mid: { x: (a.x + 2 * cx + b.x) / 4, y: (a.y + 2 * cy + b.y) / 4 },
  };
}

function toward(p, q, r) {
  const dx = q.x - p.x,
    dy = q.y - p.y,
    len = Math.hypot(dx, dy) || 1;
  return { x: p.x + (dx / len) * r, y: p.y + (dy / len) * r };
}

export function createGraph(host, opts) {
  const money = opts.money || String;
  host.innerHTML = "";
  host.classList.add("graph");
  host.classList.toggle("compact", !!opts.compact);
  const tools = document.createElement("div");
  tools.className = "graph-tools";
  tools.setAttribute("role", "toolbar");
  tools.setAttribute("aria-label", "Graph view controls");
  const tool = (name, label, fn) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "icon-btn";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = icon(name);
    b.onclick = fn;
    tools.appendChild(b);
  };
  const svg = el("svg", {
    class: "graph-svg",
    role: "group",
    "aria-label": opts.label || "Account relationship graph",
  });
  const defs = el("defs", {}, svg);
  for (const [id, cls] of [
    ["arrow", "arrow"],
    ["arrow-hl", "arrow hl"],
  ]) {
    const m = el(
      "marker",
      {
        id: opts.id + "-" + id,
        viewBox: "0 0 10 10",
        refX: "9",
        refY: "5",
        markerWidth: "9",
        markerHeight: "9",
        markerUnits: "userSpaceOnUse",
        orient: "auto-start-reverse",
      },
      defs,
    );
    el("path", { d: "M0,0 L10,5 L0,10 z", class: cls }, m);
  }
  const viewport = el("g", { class: "graph-viewport" }, svg);
  const edgeLayer = el("g", { class: "edges" }, viewport);
  const nodeLayer = el("g", { class: "nodes" }, viewport);
  const tip = document.createElement("div");
  tip.className = "graph-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  const status = document.createElement("div");
  status.className = "graph-status";
  status.setAttribute("aria-live", "polite");
  const hint = document.createElement("p");
  hint.className = "graph-hint";
  hint.textContent =
    "Drag to pan · scroll to zoom · Tab or arrow keys move between accounts · Enter opens the dossier";
  host.append(tools, svg, tip, status, hint);

  const view = { k: 1, x: 0, y: 0, moved: false };
  let data = { nodes: [], edges: [] },
    pos = new Map(),
    nodeEls = new Map(),
    edgeEls = [],
    selected = null;

  const apply = () =>
    viewport.setAttribute(
      "transform",
      `translate(${view.x},${view.y}) scale(${view.k})`,
    );
  function fit() {
    const w = svg.clientWidth || 600,
      h = svg.clientHeight || 400;
    if (!pos.size) return;
    const xs = [...pos.values()].map((p) => p.x),
      ys = [...pos.values()].map((p) => p.y);
    const x0 = Math.min(...xs) - 70,
      x1 = Math.max(...xs) + 70,
      y0 = Math.min(...ys) - 50,
      y1 = Math.max(...ys) + 62;
    view.k = Math.min(w / (x1 - x0), h / (y1 - y0), 1.35);
    view.x = (w - (x1 + x0) * view.k) / 2;
    view.y = (h - (y1 + y0) * view.k) / 2;
    if (view.k < 0.6) {
      // Too small to read: keep labels legible and centre the riskiest account.
      const anchor =
        data.nodes.find((n) => n.id === opts.focus) ||
        [...data.nodes].sort((a, b) => (b.score || 0) - (a.score || 0))[0];
      const p = pos.get(anchor.id);
      view.k = 0.6;
      view.x = w / 2 - p.x * view.k;
      view.y = h / 2 - p.y * view.k;
    }
    view.moved = false;
    apply();
  }
  function zoom(factor, cx = svg.clientWidth / 2, cy = svg.clientHeight / 2) {
    const k = Math.min(3, Math.max(0.25, view.k * factor));
    view.x = cx - ((cx - view.x) * k) / view.k;
    view.y = cy - ((cy - view.y) * k) / view.k;
    view.k = k;
    view.moved = true;
    apply();
  }
  tool("plus", "Zoom in", () => zoom(1.25));
  tool("minus", "Zoom out", () => zoom(0.8));
  tool("fit", "Fit graph to view", fit);
  tool("target", "Clear selection", () => select(null));

  svg.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      zoom(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    },
    { passive: false },
  );
  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".node")) return;
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    svg.setPointerCapture(e.pointerId);
    svg.classList.add("panning");
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    view.x = drag.vx + e.clientX - drag.x;
    view.y = drag.vy + e.clientY - drag.y;
    view.moved = true;
    apply();
  });
  const endDrag = () => {
    drag = null;
    svg.classList.remove("panning");
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  new ResizeObserver(() => view.moved || fit()).observe(svg);

  function describe(n) {
    return (
      n.id +
      (n.name && n.name !== n.id ? ", " + n.name : "") +
      ", risk " +
      (n.score ?? 0) +
      " " +
      (n.level || "") +
      (n.role ? ", " + n.role : "") +
      (n.core ? "" : ", counterparty")
    );
  }
  function showTip(html, anchor) {
    tip.innerHTML = html;
    tip.hidden = false;
    const hr = host.getBoundingClientRect(),
      ar = anchor.getBoundingClientRect();
    const left = Math.min(
      Math.max(8, ar.left - hr.left + ar.width / 2 - tip.offsetWidth / 2),
      hr.width - tip.offsetWidth - 8,
    );
    tip.style.left = left + "px";
    tip.style.top = Math.max(8, ar.top - hr.top - tip.offsetHeight - 8) + "px";
  }
  const hideTip = () => (tip.hidden = true);
  const esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );

  function select(id) {
    selected = id;
    const near = new Set(id ? [id] : []);
    if (id)
      for (const e of data.edges) {
        if (e.source === id) near.add(e.target);
        if (e.target === id) near.add(e.source);
      }
    for (const [nid, g] of nodeEls) {
      g.classList.toggle("selected", nid === id);
      g.classList.toggle("dim", !!id && !near.has(nid));
      g.setAttribute("aria-pressed", String(nid === id));
    }
    for (const { e, g } of edgeEls) {
      const on = id && (e.source === id || e.target === id);
      g.classList.toggle("hl", !!on);
      g.classList.toggle("dim", !!id && !on);
      const path = g.querySelector(".edge-line");
      if (e.kind === "flow")
        path.setAttribute(
          "marker-end",
          `url(#${opts.id}-${on ? "arrow-hl" : "arrow"})`,
        );
    }
    if (!id) {
      status.innerHTML =
        '<span class="muted">Select an account to highlight its direct payments and device links.</span>';
      return;
    }
    const n = data.nodes.find((x) => x.id === id);
    let inAmt = 0,
      outAmt = 0,
      ins = 0,
      outs = 0,
      dev = 0;
    for (const e of data.edges) {
      if (e.kind === "device" && (e.source === id || e.target === id)) dev++;
      else if (e.target === id) {
        inAmt += e.amount;
        ins++;
      } else if (e.source === id) {
        outAmt += e.amount;
        outs++;
      }
    }
    status.innerHTML =
      "<span><b>" +
      esc(n.id) +
      "</b> · risk " +
      esc(n.score ?? 0) +
      " " +
      esc(n.level || "") +
      (n.role ? " · " + esc(n.role) : "") +
      "</span><span>In " +
      money(inAmt) +
      " from " +
      ins +
      " · Out " +
      money(outAmt) +
      " to " +
      outs +
      (dev ? " · " + dev + " shared-device link" + (dev > 1 ? "s" : "") : "") +
      " (within this view)</span>" +
      (opts.onOpen
        ? '<button type="button" class="btn small" data-graph-open="' +
          esc(n.id) +
          '">Open dossier ' +
          icon("arrowRight") +
          "</button>"
        : "");
  }
  status.addEventListener("click", (e) => {
    const b = e.target.closest("[data-graph-open]");
    if (b) opts.onOpen(b.dataset.graphOpen);
  });

  function move(from, key) {
    const p = pos.get(from);
    const dir = {
      ArrowRight: [1, 0],
      ArrowLeft: [-1, 0],
      ArrowDown: [0, 1],
      ArrowUp: [0, -1],
    }[key];
    let best = null,
      bestCost = Infinity;
    for (const [id, q] of pos) {
      if (id === from) continue;
      const dx = q.x - p.x,
        dy = q.y - p.y;
      const along = dx * dir[0] + dy * dir[1];
      if (along <= 0) continue;
      const cost = along + 2.5 * Math.abs(dx * dir[1] + dy * dir[0]);
      if (cost < bestCost) {
        bestCost = cost;
        best = id;
      }
    }
    if (best) nodeEls.get(best).focus();
  }

  function update(next) {
    data = next;
    pos = layout(
      data.nodes,
      data.edges,
      opts.compact ? 150 : COL,
      opts.compact ? 80 : ROW,
    );
    edgeLayer.replaceChildren();
    nodeLayer.replaceChildren();
    nodeEls = new Map();
    edgeEls = [];
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    const max = Math.max(
      2,
      ...data.edges.filter((e) => e.kind === "flow").map((e) => e.amount),
    );
    const pairs = new Set(
      data.edges
        .filter((e) => e.kind === "flow")
        .map((e) => e.source + "|" + e.target),
    );
    for (const e of data.edges) {
      const a = pos.get(e.source),
        b = pos.get(e.target);
      if (!a || !b) continue;
      const flow = e.kind === "flow";
      const reverse = pairs.has(e.target + "|" + e.source);
      const back = b.x <= a.x;
      const bend = flow
        ? (reverse ? 22 : 0) + (back ? 70 : 0) + (a.x === b.x ? 40 : 0)
        : 34;
      const c = curve(
        a,
        b,
        bend,
        radius(byId.get(e.source)) + 2,
        radius(byId.get(e.target)) + 2,
        flow ? 1 : 0,
      );
      const g = el(
        "g",
        { class: "edge " + (flow ? "flow" : "device") },
        edgeLayer,
      );
      el("path", { d: c.d, class: "edge-hit" }, g);
      const line = el("path", { d: c.d, class: "edge-line" }, g);
      if (flow) {
        line.setAttribute(
          "stroke-width",
          (
            1.2 +
            2.6 * (Math.log(e.amount + 1) / Math.log(max + 1)) ** 3
          ).toFixed(2),
        );
        line.setAttribute("marker-end", `url(#${opts.id}-arrow)`);
      }
      const label = flow
        ? `${esc(e.source)} → ${esc(e.target)}<br><b>${money(e.amount)}</b> in ${e.count} transfer${e.count > 1 ? "s" : ""}`
        : `${esc(e.source)} ↔ ${esc(e.target)}<br>Shared device${e.device ? " " + esc(e.device) : ""} · correlation signal, not proof of identity`;
      g.addEventListener("pointerenter", () => showTip(label, line));
      g.addEventListener("pointerleave", hideTip);
      edgeEls.push({ e, g });
    }
    for (const n of data.nodes) {
      const p = pos.get(n.id);
      const g = el(
        "g",
        {
          class:
            "node lvl-" +
            (n.core ? (n.level || "low").toLowerCase() : "context") +
            (n.core ? " core" : "") +
            (n.id === opts.focus ? " focus" : ""),
          transform: `translate(${p.x},${p.y})`,
          tabindex: "0",
          role: "button",
          "aria-label": describe(n) + ". Enter opens the dossier.",
          "aria-pressed": "false",
        },
        nodeLayer,
      );
      const r = radius(n);
      el("circle", { r: r + 6, class: "halo" }, g);
      el("circle", { r, class: "dot" }, g);
      const id = el("text", { y: r + 15, class: "node-id" }, g);
      id.textContent = n.id;
      if (n.core) {
        const s = el("text", { y: r + 29, class: "node-score" }, g);
        s.textContent = `${n.score} · ${n.level}`;
      }
      const tipHtml = `<b>${esc(n.id)}</b>${n.name && n.name !== n.id ? " · " + esc(n.name) : ""}<br>Risk ${esc(n.score ?? 0)} (${esc(n.level || "Low")})${n.role ? " · " + esc(n.role) : ""}${n.core ? "" : "<br>Counterparty outside the network"}`;
      g.addEventListener("pointerenter", () => showTip(tipHtml, g));
      g.addEventListener("pointerleave", hideTip);
      g.addEventListener("focus", () => showTip(tipHtml, g));
      g.addEventListener("blur", hideTip);
      g.addEventListener("click", () =>
        select(selected === n.id ? null : n.id),
      );
      g.addEventListener("dblclick", () => opts.onOpen && opts.onOpen(n.id));
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          opts.onOpen && opts.onOpen(n.id);
        } else if (e.key === " ") {
          e.preventDefault();
          select(selected === n.id ? null : n.id);
        } else if (e.key.startsWith("Arrow")) {
          e.preventDefault();
          move(n.id, e.key);
        } else if (e.key === "Escape") select(null);
        else if (e.key === "+" || e.key === "=") zoom(1.25);
        else if (e.key === "-") zoom(0.8);
        else if (e.key === "0") fit();
      });
      nodeEls.set(n.id, g);
    }
    select(null);
    requestAnimationFrame(fit);
  }

  return { update, fit, select };
}
