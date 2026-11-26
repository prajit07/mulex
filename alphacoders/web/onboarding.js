// Deepfake-resilient V-CIP onboarding: the prevention layer.
// Applicant flow (camera gate -> document -> live capture -> Trust Engine)
// plus the officer queue with a signed audit trail.
import { icon } from "./icons.js";

let ctx;
const v = {
  session: null,
  step: 0,
  mode: "camera",
  stream: null,
  document: null,
  capture: null,
  sessions: [],
  selected: "",
  filter: "OPEN",
  reasonCodes: null,
  models: null,
  rev: 0,
  drawn: "",
};
const STEPS = [
  "Camera integrity",
  "Identity document",
  "Live capture",
  "Trust decision",
];
const STAGE_LABEL = {
  AWAITING_DOCUMENT: "Awaiting document",
  AWAITING_CAPTURE: "Awaiting capture",
  VERIFIED: "Verified",
  MANUAL_REVIEW: "Manual review",
  REJECTED: "Rejected",
  APPROVED: "Approved · account opened",
  ESCALATED: "Escalated",
  REJECTED_BY_OFFICER: "Rejected by officer",
};
const STAGE_CLASS = {
  VERIFIED: "pass",
  APPROVED: "pass",
  MANUAL_REVIEW: "high",
  ESCALATED: "high",
  REJECTED: "critical",
  REJECTED_BY_OFFICER: "critical",
};
const stageBadge = (stage) =>
  ctx.badge(STAGE_LABEL[stage] || stage, STAGE_CLASS[stage] || "plain");
const url = (path = "") => ctx.base() + "/onboarding" + path;
const deviceId = () => {
  let id = localStorage.getItem("mulex.device");
  if (!id) {
    id = "DEV-" + crypto.randomUUID().slice(0, 8).toUpperCase();
    localStorage.setItem("mulex.device", id);
  }
  return id;
};

export function stopOnboardingCamera() {
  if (v.stream) {
    v.stream.getTracks().forEach((t) => t.stop());
    v.stream = null;
  }
}

export function initOnboarding(context) {
  ctx = context;
  document.addEventListener("click", onClick);
  document.addEventListener("submit", onSubmit, true);
  document.addEventListener("change", (e) => {
    if (e.target.id === "officer-action") drawReasonCodes();
    if (e.target.name === "capture_mode") v.mode = e.target.value;
  });
}

export async function refreshOnboarding(selected) {
  if (!ctx.state.key) return;
  const [list, status] = await Promise.all([api(url()), api(url("/status"))]);
  v.sessions = list.sessions_list;
  v.reasonCodes = status.reason_codes;
  v.models = status.models;
  if (selected) v.selected = selected;
  drawOnboarding();
}

export function resetOnboarding() {
  stopOnboardingCamera();
  Object.assign(v, {
    session: null,
    step: 0,
    document: null,
    capture: null,
    selected: "",
  });
  changed();
}

function api(path, opts) {
  return ctx.api(path, opts);
}

export function drawOnboarding() {
  if (!$("vcip-body")) return;
  drawSteps();
  drawStep();
  drawQueue();
  drawOfficer();
}
const $ = (id) => document.getElementById(id);

function drawSteps() {
  $("vcip-steps").innerHTML = STEPS.map(
    (s, i) =>
      '<li class="' +
      (i < v.step ? "done" : i === v.step ? "current" : "") +
      '"' +
      (i === v.step ? ' aria-current="step"' : "") +
      "><span>" +
      (i < v.step ? icon("check") : i + 1) +
      "</span>" +
      s +
      "</li>",
  ).join("");
  $("vcip-session-id").hidden = !v.session;
  $("vcip-session-id").textContent = v.session ? v.session.id : "";
}

function modelChips() {
  const m = v.models || {};
  return (
    '<div class="model-state">' +
    [
      ["RetinaFace", m.retinaface],
      ["ArcFace", m.arcface],
      ["Liveness", m.liveness],
      ["Deepfake", m.deepfake],
    ]
      .map(([k, s]) =>
        ctx.badge(
          k + ": " + (s === "unavailable" ? "unavailable" : "ready"),
          s === "unavailable" ? "review" : "pass",
        ),
      )
      .join("") +
    "</div>"
  );
}

// Bump whenever the applicant flow changes, so background refreshes never
// re-render (and wipe) a form the applicant is filling in.
function changed() {
  v.rev += 1;
}

function drawStep(force = false) {
  const key = v.step + "|" + (v.session?.id || "") + "|" + v.rev;
  if (!force && key === v.drawn && $("vcip-body").childElementCount) return;
  v.drawn = key;
  const s = ctx.safe;
  let html = "";
  if (v.step === 0) {
    html =
      modelChips() +
      '<form id="vcip-start" class="form">' +
      '<div class="inline-form">' +
      '<label class="field"><span>Applicant name (as declared)</span><input name="applicant" required maxlength="120" autocomplete="off"></label>' +
      '<label class="field"><span>Identity document</span><select name="doc_type"><option value="AADHAAR">Aadhaar</option><option value="PAN">PAN</option></select></label>' +
      "</div>" +
      '<fieldset class="choice"><legend>Capture mode</legend>' +
      '<label><input type="radio" name="capture_mode" value="camera"' +
      (v.mode === "camera" ? " checked" : "") +
      "><span>Live camera<small>Full V-CIP flow with the camera integrity gate</small></span></label>" +
      '<label><input type="radio" name="capture_mode" value="upload"' +
      (v.mode === "upload" ? " checked" : "") +
      "><span>Upload images<small>For evaluation without a webcam. Uploaded frames cannot prove liveness, so the decision is capped at manual review.</small></span></label>" +
      "</fieldset>" +
      '<details class="sim-box"><summary>Evaluator simulation</summary>' +
      '<p class="muted small">These controls simulate attacker conditions for demonstration. Each use is written to the audit trail.</p>' +
      '<label class="field"><span>Device fingerprint</span><input name="device_id" value="' +
      s(deviceId()) +
      '" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{0,63}" maxlength="64"><small class="help">This browser\'s persistent ID. Enter a device from the dataset, such as DEVICE-X17, to test the mule-device link.</small></label>' +
      '<label class="check"><input type="checkbox" name="simulate_virtual"> Report an "OBS Virtual Camera" device (camera injection attack)</label>' +
      "</details>" +
      '<button class="btn primary">' +
      icon("payments") +
      "Check camera and start session</button></form>";
  } else if (v.step === 1) {
    html =
      '<p class="muted">Upload a clear photo of the full ' +
      (v.session.doc_type === "PAN" ? "PAN card" : "Aadhaar card") +
      ". MULEX reads it with RapidOCR, validates the number and extracts the photo for face matching. The image is not stored.</p>" +
      '<form id="vcip-doc" class="form"><label class="field"><span>Document image (JPEG or PNG, up to 3 MB)</span><input type="file" name="image" accept="image/jpeg,image/png" required></label><button class="btn primary">Read document</button></form>' +
      (v.document ? documentResult(v.document) : "");
  } else if (v.step === 2) {
    html =
      (v.mode === "camera"
        ? '<div class="capture"><video id="vcip-video" autoplay muted playsinline></video><div class="capture-actions"><button type="button" class="btn primary" id="vcip-snap">' +
          icon("target") +
          "Capture frame</button></div></div>"
        : '<form id="vcip-upload" class="form"><label class="field"><span>Live frame image (JPEG or PNG)</span><input type="file" name="frame" accept="image/jpeg,image/png" required></label><button class="btn primary">Analyse frame</button></form>') +
      (v.capture ? captureResult(v.capture) : "") +
      (v.capture?.accepted
        ? '<div class="toolbar"><button type="button" class="btn primary" id="vcip-submit">Submit to Trust Engine ' +
          icon("arrowRight") +
          '</button><span class="muted small">You can retake the frame before submitting.</span></div>'
        : "");
  } else {
    html =
      decisionCard(v.session) +
      '<div class="toolbar"><button type="button" class="btn" id="vcip-new">Start a new session</button><button type="button" class="btn ghost" data-session="' +
      s(v.session.id) +
      '">Open in officer queue</button></div>';
  }
  $("vcip-body").innerHTML = html;
  ctx.applyWidths($("vcip-body"));
  if (v.step === 2 && v.mode === "camera" && v.stream) {
    $("vcip-video").srcObject = v.stream;
  }
}

function documentResult(r) {
  const s = ctx.safe;
  if (!r.accepted)
    return (
      '<div class="status-box error"><strong>' +
      s(r.reason) +
      "</strong>" +
      (r.ocr?.issues?.length
        ? "<p>" + r.ocr.issues.map(s).join(" · ") + "</p>"
        : "") +
      "</div>"
    );
  const d = r.document;
  return (
    '<div class="status-box success"><strong>Document read · photo found (confidence ' +
    d.face_score +
    ')</strong></div><dl class="kv">' +
    [
      ["Name", d.fields.name || "—"],
      ["ID number", d.id_masked || "—"],
      ["Birth year", d.fields.birth_year || "—"],
      ["Gender", d.fields.gender || "—"],
      ["OCR score", d.ocr_score ?? "—"],
      ["Read time", d.latency_ms + " ms"],
    ]
      .map(([k, val]) => "<div><dt>" + k + "</dt><dd>" + s(val) + "</dd></div>")
      .join("") +
    "</dl>" +
    (d.issues.length
      ? '<ul class="rules">' +
        d.issues
          .map((i) => "<li>" + icon("alert") + "<span>" + s(i) + "</span></li>")
          .join("") +
        "</ul>"
      : "") +
    '<div class="toolbar"><button type="button" class="btn primary" id="vcip-next">Continue to live capture ' +
    icon("arrowRight") +
    "</button></div>"
  );
}

function meterRow(label, value, note, invert = false) {
  if (value === null || value === undefined)
    return (
      '<div class="signal"><span class="signal-name">' +
      label +
      '</span><span class="signal-points">—</span><p class="signal-evidence">Model unavailable</p></div>'
    );
  const pct = Math.round(100 * value);
  return (
    '<div class="signal"><span class="signal-name">' +
    label +
    '</span><span class="signal-points">' +
    value.toFixed(2) +
    '</span><span class="bar' +
    (invert ? " invert" : "") +
    '" role="img" aria-label="' +
    label +
    " " +
    value.toFixed(2) +
    '"><span data-w="' +
    pct +
    '"></span></span><p class="signal-evidence">' +
    note +
    "</p></div>"
  );
}

function captureResult(r) {
  const s = ctx.safe;
  if (!r.accepted)
    return (
      '<div class="status-box error"><strong>' + s(r.reason) + "</strong></div>"
    );
  const c = r.capture;
  return (
    (c.quality.prompts.length
      ? '<div class="status-box"><strong>Capture guidance</strong><ul class="rules">' +
        c.quality.prompts
          .map((p) => "<li>" + icon("info") + "<span>" + s(p) + "</span></li>")
          .join("") +
        "</ul></div>"
      : "") +
    '<div class="signal-group"><h3>' +
    icon("investigate") +
    "Signals from this frame</h3>" +
    meterRow(
      "Face match (ArcFace)",
      Math.max(0, c.identity_similarity),
      "Cosine similarity to the document photo. A match needs at least 0.40.",
    ) +
    meterRow(
      "Liveness (MiniFASNet v2)",
      c.liveness,
      "Probability the face is live, not a print, screen or replay.",
    ) +
    meterRow(
      "Deepfake probability",
      c.deepfake,
      s(c.deepfake_model || "") + ". Lower is better.",
      true,
    ) +
    '</div><p class="muted small">Analysed in ' +
    c.latency_ms +
    " ms on CPU.</p>"
  );
}

export function decisionCard(session) {
  const s = ctx.safe;
  const d = session.decision;
  if (!d) return ctx.empty("No Trust Engine decision yet.");
  const comp = d.components || {};
  const parts = [
    ["Identity · ArcFace", "identity", 45],
    ["Liveness · MiniFASNet", "liveness", 30],
    ["Deepfake · ViT", "deepfake", 20],
    ["OCR · RapidOCR", "ocr", 5],
  ];
  return (
    '<div class="decision-card ' +
    (STAGE_CLASS[d.decision] || "") +
    '"><div class="decision-head"><div><p class="kicker">Trust Engine</p><div class="decision-title">' +
    stageBadge(d.decision) +
    (d.trust === null || d.trust === undefined
      ? '<strong class="trust">Blocked</strong>'
      : '<strong class="trust">' +
        d.trust.toFixed(1) +
        "<small>/100</small></strong>") +
    "</div></div>" +
    (d.trust === null || d.trust === undefined
      ? ""
      : '<div class="meter-track trust-track ' +
        (d.decision === "VERIFIED"
          ? "low"
          : d.decision === "REJECTED"
            ? "critical"
            : "high") +
        '" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
        d.trust +
        '" aria-label="Trust ' +
        d.trust +
        '"><span class="fill" data-w="' +
        d.trust +
        '"></span><span class="tick" data-left="70"><span>Review 70</span></span><span class="tick" data-left="90"><span>Verified 90</span></span></div>') +
    "</div>" +
    (Object.keys(comp).length
      ? '<div class="signal-group"><h3>' +
        icon("model") +
        "Weighted contributions</h3>" +
        parts
          .map(([label, k, max]) => {
            const val = comp[k];
            return (
              '<div class="signal"><span class="signal-name">' +
              label +
              '</span><span class="signal-points">' +
              (val === null ? "—" : "+" + val) +
              "<small>of " +
              max +
              '</small></span><span class="bar"><span data-w="' +
              (val === null ? 0 : (100 * val) / max).toFixed(1) +
              '"></span></span></div>'
            );
          })
          .join("") +
        "</div>"
      : "") +
    '<h3>Why</h3><ul class="rules">' +
    d.reasons
      .map((r) => "<li>" + icon("alert") + "<span>" + s(r) + "</span></li>")
      .join("") +
    "</ul>" +
    (d.network_flags?.length
      ? '<h3>Mule-network intelligence</h3><ul class="rules network">' +
        d.network_flags
          .map(
            (r) => "<li>" + icon("networks") + "<span>" + s(r) + "</span></li>",
          )
          .join("") +
        "</ul>"
      : "") +
    (d.linked_accounts?.length
      ? '<p class="small">Accounts already on this device: ' +
        d.linked_accounts.map(ctx.link).join(", ") +
        "</p>"
      : "") +
    "</div>"
  );
}

function drawQueue() {
  const open = (x) =>
    ["VERIFIED", "MANUAL_REVIEW", "ESCALATED"].includes(x.stage);
  const counts = {
    OPEN: v.sessions.filter(open).length,
    ALL: v.sessions.length,
  };
  $("officer-filter").innerHTML = [
    ["OPEN", "Needs decision"],
    ["ALL", "All sessions"],
  ]
    .map(
      ([k, l]) =>
        '<button type="button" data-officer-filter="' +
        k +
        '" aria-pressed="' +
        (v.filter === k) +
        '">' +
        l +
        '<span class="count">' +
        counts[k] +
        "</span></button>",
    )
    .join("");
  const rows = v.sessions.filter((x) => v.filter === "ALL" || open(x));
  $("officer-list").innerHTML =
    rows
      .map(
        (x) =>
          '<button type="button" class="lead" data-session="' +
          ctx.safe(x.id) +
          '" aria-pressed="' +
          (x.id === v.selected) +
          '"><span><strong>' +
          ctx.safe(x.id) +
          "</strong><small>" +
          ctx.safe(x.applicant) +
          " · " +
          ctx.stamp(x.created) +
          '</small></span><span class="lead-score">' +
          (x.decision?.trust != null
            ? '<span class="score-pill">' +
              x.decision.trust.toFixed(0) +
              "</span>"
            : "") +
          stageBadge(x.stage) +
          "</span></button>",
      )
      .join("") ||
    ctx.empty(
      v.filter === "OPEN"
        ? "No sessions awaiting an officer decision."
        : "No onboarding sessions yet.",
    );
}

async function drawOfficer() {
  const host = $("officer-detail");
  if (!v.selected) {
    host.innerHTML = "";
    return;
  }
  const x = await api(url("/" + encodeURIComponent(v.selected)));
  if (x.id !== v.selected) return;
  // Skip re-rendering an unchanged session so a half-filled decision survives.
  const key = x.id + "|" + x.stage + "|" + x.audit.length;
  if (key === v.officerKey && host.childElementCount) return;
  v.officerKey = key;
  const s = ctx.safe;
  const final =
    ["APPROVED", "REJECTED_BY_OFFICER"].includes(x.stage) || !x.decision;
  host.innerHTML =
    '<div class="officer-card"><div class="card-head"><div><p class="kicker">Session</p><h3>' +
    s(x.id) +
    " · " +
    s(x.applicant) +
    '</h3><p class="muted small">' +
    s(x.doc_type) +
    " " +
    s(x.id_masked || "") +
    " · device " +
    s(x.device_id) +
    " · camera " +
    (x.camera.mode === "upload"
      ? "upload mode"
      : x.camera.passed
        ? "passed"
        : "failed") +
    "</p></div>" +
    stageBadge(x.stage) +
    "</div>" +
    decisionCard(x) +
    (x.account_id
      ? '<p class="status-box success">Customer record created: ' +
        ctx.link(x.account_id) +
        "</p>"
      : "") +
    (final
      ? ""
      : '<form id="officer-form" class="form" data-session="' +
        s(x.id) +
        '"><h3>Officer decision</h3><div class="inline-form"><label class="field"><span>Action</span><select id="officer-action" name="action">' +
        (x.decision.decision === "REJECTED"
          ? ""
          : '<option value="APPROVE">Approve and open account</option>') +
        '<option value="ESCALATE">Escalate</option><option value="REJECT">Reject</option></select></label><label class="field"><span>Reason code</span><select id="officer-reason" name="reason_code"></select></label><label class="field"><span>Officer name</span><input name="officer" required maxlength="120" autocomplete="name"></label><label class="field span-all"><span>Note (recorded in the audit trail)</span><textarea name="note" rows="2" required minlength="4" maxlength="500"></textarea></label><button class="btn primary">Record decision</button></div></form>') +
    "<h3>Signed audit trail</h3>" +
    ctx.table(
      [["#", "num"], "Event", "Time", "Hash"],
      x.audit.map((a) => [
        a.seq,
        s(a.event.replace(/_/g, " ").toLowerCase()),
        ctx.stamp(a.created),
        "<code>" + s(a.hash) + "…</code>",
      ]),
      "No events.",
      "Audit trail",
    ) +
    "</div>";
  ctx.applyWidths(host);
  drawReasonCodes();
}

function drawReasonCodes() {
  const action = $("officer-action")?.value;
  if (!action || !v.reasonCodes) return;
  $("officer-reason").innerHTML = v.reasonCodes[action]
    .map((c) => "<option>" + c + "</option>")
    .join("");
}

async function startCamera() {
  stopOnboardingCamera();
  v.stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user", width: { ideal: 1280 } },
    audio: false,
  });
  const devices = await navigator.mediaDevices.enumerateDevices();
  return {
    cameras: devices.filter((d) => d.kind === "videoinput").map((d) => d.label),
    selected: v.stream.getVideoTracks()[0]?.label || null,
  };
}

async function onClick(e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  try {
    if (btn.dataset.session) {
      v.selected = btn.dataset.session;
      if (ctx.state.view !== "onboarding")
        return ctx.navigate("onboarding", v.selected);
      drawQueue();
      await drawOfficer();
      $("officer-detail").scrollIntoView({ block: "nearest" });
    } else if (btn.dataset.officerFilter) {
      v.filter = btn.dataset.officerFilter;
      drawQueue();
    } else if (btn.id === "vcip-next") {
      v.step = 2;
      changed();
      drawOnboarding();
    } else if (btn.id === "vcip-new") {
      resetOnboarding();
      changed();
      drawOnboarding();
    } else if (btn.id === "audit-verify") {
      const r = await api(url("/audit/verify"));
      ctx.notice(
        r.valid
          ? "Audit chain verified: " +
              r.records +
              " signed records, no tampering detected."
          : "Audit chain broken at record " + r.broken_at + ".",
        r.valid ? "info" : "error",
      );
    } else if (btn.id === "vcip-snap") {
      const video = $("vcip-video");
      if (!video.videoWidth) throw new Error("Wait for the camera preview");
      const c = document.createElement("canvas");
      c.width = video.videoWidth;
      c.height = video.videoHeight;
      c.getContext("2d").drawImage(video, 0, 0);
      const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.92));
      await sendCapture(blob);
    } else if (btn.id === "vcip-submit") {
      ctx.setBusy(true);
      try {
        v.session = await api(url("/" + v.session.id + "/submit"), {
          method: "POST",
        });
        stopOnboardingCamera();
        v.step = 3;
        changed();
        v.selected = v.session.id;
        await ctx.reload();
        await refreshOnboarding();
      } finally {
        ctx.setBusy(false);
      }
    }
  } catch (err) {
    ctx.notice(err.message);
  }
}

function working(text) {
  const el = document.createElement("p");
  el.className = "status-box working";
  el.setAttribute("role", "status");
  el.textContent = text;
  $("vcip-body").querySelector(".status-box.working")?.remove();
  $("vcip-body").append(el);
}

async function sendCapture(blob) {
  working("Running RetinaFace, ArcFace, MiniFASNet and deepfake screening…");
  const fd = new FormData();
  fd.set("frame", blob, "frame.jpg");
  ctx.setBusy(true);
  try {
    v.capture = await api(url("/" + v.session.id + "/capture"), {
      method: "POST",
      body: fd,
    });
    changed();
    drawStep();
  } finally {
    ctx.setBusy(false);
  }
}

async function onSubmit(e) {
  const form = e.target;
  if (
    !["vcip-start", "vcip-doc", "vcip-upload", "officer-form"].includes(form.id)
  )
    return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (!form.checkValidity()) return form.reportValidity();
  const data = new FormData(form);
  ctx.notice("");
  ctx.setBusy(true);
  try {
    if (form.id === "vcip-start") {
      let env = { cameras: [], selected: null };
      if (v.mode === "camera") env = await startCamera();
      if (data.get("simulate_virtual")) {
        env.cameras = [...env.cameras, "OBS Virtual Camera"];
      }
      v.session = await api(
        url(),
        ctx.jsonOptions({
          applicant: data.get("applicant"),
          doc_type: data.get("doc_type"),
          device_id: data.get("device_id"),
          cameras: env.cameras,
          selected_camera: env.selected,
          automated: !!navigator.webdriver,
          capture_mode: v.mode,
          simulated:
            !!data.get("simulate_virtual") ||
            data.get("device_id") !== deviceId(),
        }),
      );
      v.selected = v.session.id;
      if (v.session.stage === "REJECTED") {
        stopOnboardingCamera();
        v.step = 3;
      } else v.step = 1;
      changed();
      await ctx.reload();
      await refreshOnboarding();
    } else if (form.id === "vcip-doc") {
      working("Reading the document with RapidOCR and locating the photo…");
      const fd = new FormData();
      fd.set("image", data.get("image"));
      v.document = await api(url("/" + v.session.id + "/document"), {
        method: "POST",
        body: fd,
      });
      changed();
      drawStep();
    } else if (form.id === "vcip-upload") {
      await sendCapture(data.get("frame"));
    } else if (form.id === "officer-form") {
      const action = data.get("action");
      const ok = await ctx.confirmAction(
        {
          APPROVE: "Approve and open the account?",
          ESCALATE: "Escalate this session?",
          REJECT: "Reject this applicant?",
        }[action],
        "<p>Session <b>" +
          ctx.safe(form.dataset.session) +
          "</b>, reason <b>" +
          ctx.safe(data.get("reason_code")) +
          "</b>.</p><p>" +
          (action === "APPROVE"
            ? "A customer record is created in this dataset, and the detection engine monitors it from now on. "
            : "") +
          "The decision is signed into the audit trail.</p>",
        { APPROVE: "Approve", ESCALATE: "Escalate", REJECT: "Reject" }[action],
      );
      if (!ok) return;
      await api(
        url("/" + encodeURIComponent(form.dataset.session) + "/decision"),
        ctx.jsonOptions({
          action,
          reason_code: data.get("reason_code"),
          officer: data.get("officer"),
          note: data.get("note"),
        }),
      );
      ctx.notice("Decision recorded for " + form.dataset.session, "info");
      await ctx.reload();
      await refreshOnboarding();
    }
  } catch (err) {
    ctx.notice(err.message);
  } finally {
    ctx.setBusy(false);
  }
}
