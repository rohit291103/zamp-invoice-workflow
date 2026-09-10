/* UI for the AP invoice process.
   Renders whatever the pipeline streams; contains no process logic of its own. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Status icon + label pairing - meaning never rests on colour alone. */
const DEC_ICON  = { AUTO_APPROVE: "✓", NEEDS_REVIEW: "◑", HOLD: "⏸", REJECT: "✕" };
const DEC_LABEL = { AUTO_APPROVE: "Auto-approved", NEEDS_REVIEW: "Needs review",
                    HOLD: "On hold", REJECT: "Rejected" };
/* Plain-language gloss for anyone who has never seen an AP queue. */
const DEC_PLAIN = {
  AUTO_APPROVE: "Paid without anyone touching it",
  NEEDS_REVIEW: "A person signs it off first",
  HOLD:         "Stuck until someone outside answers",
  REJECT:       "Must not be paid",
};
const SEV_ICON  = { info: "•", warn: "▲", block: "■" };
const DEC_ORDER = ["AUTO_APPROVE", "NEEDS_REVIEW", "HOLD", "REJECT"];

/* What each stage is actually doing, in words a non-specialist reads once. */
const STAGE_PLAIN = {
  ingest:    "Opens the PDF and works out whether it holds real text or is just a picture of a page. Everything downstream depends on this.",
  extract:   "Claude reads the document and returns the fields as structured data — supplier, invoice number, order reference, amounts. This is the only step that uses AI.",
  normalize: "Cleans what came back: strips currency symbols, parses dates, and works out any figure the document implied but never stated.",
  identify:  "Matches the letterhead to a supplier in our master list — by tax ID first, because that is the one thing a supplier cannot mistype their way out of.",
  match:     "Finds the purchase order this invoice belongs to, and checks how much of that order has already been billed.",
  validate:  "Runs all seventeen checks. Each one either passes quietly or raises a finding, and every finding is written for a person, not a developer.",
  decide:    "Turns the findings into a single decision. The worst finding wins, so the outcome can always be explained by naming the rules behind it.",
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n, ccy) => n == null ? "—"
  : (ccy ? ccy + " " : "") + Number(n).toLocaleString(undefined,
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ago = (iso) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
};
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2400);
}
const pill = (d) => d
  ? `<span class="pill ${esc(d)}">${DEC_ICON[d] || "?"} ${esc(DEC_LABEL[d] || d)}</span>`
  : `<span class="pill error">— pending</span>`;

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* blocked storage */ } },
};

/* ---------------- tabs ---------------- */
function showView(name) {
  $$(".tab").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.view === name)));
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  if (name === "dash") loadDashboard();
  if (name === "ref") loadReference();
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

/* ---------------- invoice picker ---------------- */
const GROUPS = [
  ["happy", "Start here"],
  ["edge", "The tricky ones"],
  ["rule", "Everyday controls"],
  ["upload", "Your uploads"],
];
let CATALOGUE = {};

async function loadInvoices() {
  const items = await (await fetch("/api/invoices")).json();
  CATALOGUE = Object.fromEntries(items.map((i) => [i.file, i]));
  const box = $("#inv-list");
  box.innerHTML = "";
  for (const [kind, label] of GROUPS) {
    const group = items.filter((i) => i.kind === kind);
    if (!group.length) continue;
    box.insertAdjacentHTML("beforeend", `<div class="group-label">${esc(label)}</div>`);
    const note = group.find((g) => g.group_note);
    if (note) box.insertAdjacentHTML("beforeend",
      `<p class="group-note">${esc(note.group_note)}</p>`);
    for (const it of group) {
      const b = document.createElement("button");
      b.className = "inv-card";
      b.dataset.file = it.file;
      b.innerHTML =
        `<div class="t"><span>${esc(it.title)}</span>${it.edge_no
          ? `<span class="edge-tag">EDGE ${it.edge_no}</span>` : ""}</div>
         <div class="b">${esc(it.blurb)}</div>`;
      b.addEventListener("click", () => selectInvoice(it.file));
      box.appendChild(b);
    }
  }
}

/* ---------------- upload ---------------- */
const dz = $("#dropzone"), fi = $("#file-input");
dz.addEventListener("click", () => fi.click());
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
dz.addEventListener("dragleave", () => dz.classList.remove("over"));
dz.addEventListener("drop", (e) => {
  e.preventDefault(); dz.classList.remove("over");
  if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
});
fi.addEventListener("change", () => fi.files[0] && upload(fi.files[0]));

async function upload(file) {
  const fd = new FormData(); fd.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  if (!r.ok) return toast("Upload failed — PDFs only");
  const { file: name } = await r.json();
  await loadInvoices();
  selectInvoice(name);
}

/* ---------------- select (does NOT run) ---------------- */
let selected = null;
let es = null;

function selectInvoice(file) {
  if (es) { es.close(); es = null; }
  selected = file;
  const meta = CATALOGUE[file] || { title: file, story: "", hard: "", watch: "", why: "" };

  $$(".inv-card").forEach((c) => c.setAttribute("aria-current", String(c.dataset.file === file)));
  $("#run-empty").hidden = true;
  $("#run-body").hidden = false;
  $("#run-title").textContent = meta.title || file;
  $("#run-sub").textContent = file;
  $("#run-status").hidden = true;

  $("#brief").hidden = false;
  $("#brief-story").textContent = meta.story || "An invoice you supplied.";
  $("#brief-hard").textContent  = meta.hard  || "Unknown — this is your own file, so there is no script for it.";
  $("#brief-watch").textContent = meta.watch || "Watch each stage as it runs.";
  $("#brief-foot").innerHTML = meta.expect
    ? `<span>Expected outcome</span>
       <span class="pill ${esc(meta.expect)}">${DEC_ICON[meta.expect]} ${esc(DEC_LABEL[meta.expect])}</span>
       <span class="muted">${esc(DEC_PLAIN[meta.expect])}. ${esc(meta.why || "")}</span>`
    : `<span class="muted">Your own file — no expected outcome to compare against.</span>`;

  $("#run-cta").hidden = false;
  $("#run-btn").disabled = false;
  $("#run-btn").textContent = "Run the process";
  $("#stages-wrap").hidden = true;
  $("#stage-list").innerHTML = "";
  $("#decision-slot").innerHTML = "";

  showDocument(file, meta);
}

function showDocument(file, meta) {
  const panel = $("#doc-panel");
  panel.hidden = false;
  const url = `/api/invoices/file?name=${encodeURIComponent(file)}`;
  $("#doc-open").href = url;
  $("#doc-meta").innerHTML =
    `<span class="chip">${esc(file.replace(/^uploads\//, ""))}</span>` +
    (meta.size ? `<span class="chip">${(meta.size / 1024).toFixed(0)} KB</span>` : "");
  // A server-rendered page image, not an embedded PDF - see api_invoice_preview.
  const preview = `/api/invoices/preview?name=${encodeURIComponent(file)}`;
  $("#doc-frame").innerHTML =
    `<a href="${esc(url)}" target="_blank" rel="noopener" title="Open the PDF">
       <img src="${esc(preview)}" alt="First page of ${esc(file)}">
     </a>`;
}

/* ---------------- run ---------------- */
$("#run-btn").addEventListener("click", () => startRun(selected));

function startRun(file) {
  if (!file) return Promise.resolve();
  if (es) { es.close(); es = null; }

  $("#run-btn").disabled = true;
  $("#run-btn").textContent = "Running…";
  $("#run-status").hidden = false;
  $("#run-status").className = "pill";
  $("#run-status").textContent = "running…";
  $("#stages-wrap").hidden = false;
  $("#stage-list").innerHTML = "";
  $("#decision-slot").innerHTML = "";

  return new Promise((resolve) => {
    es = new EventSource(`/api/run/stream?file=${encodeURIComponent(file)}`);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      handleEvent(ev);
      if (ev.type === "run_complete" || ev.type === "run_failed") resolve(ev);
    };
    es.onerror = () => { if (es) { es.close(); es = null; } resolve(null); };
  });
}

function handleEvent(ev) {
  if (ev.type === "run_started") {
    $("#stage-list").innerHTML = ev.stages.map((s, i) => `
      <div class="stage pending" id="st-${s.key}">
        <div class="rail"><div class="dot">${i + 1}</div><div class="line"></div></div>
        <div class="body">
          <div class="name">${esc(s.label)} <span class="hint">${esc(s.description)}</span></div>
          <div class="detail">waiting…</div>
          <div class="extra"></div>
        </div>
      </div>`).join("");
    return;
  }

  if (ev.type === "stage") {
    const el = $(`#st-${ev.stage}`);
    if (!el) return;
    el.className = "stage " + (ev.status === "started" ? "running" : ev.status);
    if (ev.status !== "started") {
      const n = el.querySelector(".dot");
      n.textContent = { done: "✓", failed: "✕", degraded: "!" }[ev.status] || n.textContent;
    }
    el.querySelector(".detail").textContent = ev.detail || "";
    el.querySelector(".extra").innerHTML = renderStageExtra(ev);
    return;
  }

  if (ev.type === "run_complete") {
    const d = ev.decision;
    $("#run-status").className = `pill ${d.decision}`;
    $("#run-status").innerHTML = `${DEC_ICON[d.decision]} ${esc(DEC_LABEL[d.decision])}`;
    $("#decision-slot").innerHTML = decisionCard(d, ev);
    wireActions(ev.run_id);
    $("#run-btn").disabled = false;
    $("#run-btn").textContent = "Run it again";
    if (es) { es.close(); es = null; }
    return;
  }

  if (ev.type === "run_failed") {
    $("#run-status").className = "pill REJECT";
    $("#run-status").textContent = "✕ Error";
    $("#decision-slot").innerHTML =
      `<div class="card"><h4>Run failed</h4><pre class="json">${esc(ev.error)}</pre></div>`;
    $("#run-btn").disabled = false;
    $("#run-btn").textContent = "Try again";
    if (es) { es.close(); es = null; }
  }
}

function chips(pairs) {
  const out = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<span class="chip">${esc(k)} <b>${esc(v)}</b></span>`).join("");
  return out ? `<div class="kv">${out}</div>` : "";
}

function renderStageExtra(ev) {
  const d = ev.data || {};
  if (ev.stage === "ingest" && ev.status === "done")
    return chips([["read as", d.mode === "image_vision" ? "page image" : "real text"],
                  ["pages", d.page_count], ["characters found", d.char_count]]);

  if (ev.stage === "extract" && ev.status === "done") {
    const f = d.fields || {}, u = d.usage || {};
    return chips([["supplier", f.vendor_name], ["invoice no.", f.invoice_number],
                  ["order ref", f.po_reference], ["currency", f.currency],
                  ["total", f.invoice_total],
                  ["read by", d.extractor === "heuristic" ? "fallback (no AI)" : "Claude"],
                  ["model", u.model], ["tokens in", u.input_tokens]]);
  }
  if (ev.stage === "normalize" && ev.status === "done" && (d.notes || []).length)
    return `<div class="kv">${d.notes.map((n) =>
      `<span class="chip">${esc(n)}</span>`).join("")}</div>`;

  if (ev.stage === "identify" && ev.status !== "started")
    return chips([["supplier id", d.vendor_id], ["standing", d.vendor_status],
                  ["matched by", d.how]]);

  if (ev.stage === "match" && ev.status !== "started") {
    const v = d.variance || {};
    let html = chips([["order", d.po_number],
                      ["how", (d.method || "").replace(/_/g, " ")],
                      ["checking", v.basis === "cumulative" ? "everything billed so far" : "this invoice"],
                      ["this invoice", v.invoice_total == null ? null : money(v.invoice_total)],
                      ["already billed", v.already_consumed == null ? null : money(v.already_consumed)],
                      ["order value", v.po_amount == null ? null : money(v.po_amount)],
                      ["difference", v.variance_abs == null ? null : money(v.variance_abs)],
                      ["allowed", v.tolerance_abs == null ? null : money(v.tolerance_abs)]]);
    if ((d.candidates || []).length > 1)
      html += `<div class="kv">${d.candidates.map((c) =>
        `<span class="chip">could be <b>${esc(c.po_number)}</b> — ${esc(c.basis)}</span>`).join("")}</div>`;
    if (d.ledger && d.ledger.entries.length)
      html += `<div class="kv">${d.ledger.entries.map((e) =>
        `<span class="chip">earlier: ${esc(e.invoice_number || e.run_id.slice(0, 8))} `
        + `<b>${money(e.amount)}</b> ${esc(e.state)}</span>`).join("")}</div>`;
    return html;
  }

  if (ev.stage === "validate" && ev.status === "done")
    return renderFindings(d.findings || []);

  if (ev.status === "degraded")
    return chips([["fallback", "deterministic extractor, no AI"]]);
  return "";
}

/* Findings used to arrive as one undifferentiated wall. Split them: what needs
   attention is open, what quietly passed is folded away but still there. */
function renderFindings(findings) {
  const attention = findings.filter((f) => f.severity !== "info");
  const passed = findings.filter((f) => f.severity === "info");
  const blocks = findings.filter((f) => f.severity === "block").length;
  const warns = findings.filter((f) => f.severity === "warn").length;

  const summary = `<div class="checks-summary">
    <span class="count-pill pass">✓ ${passed.length} passed</span>
    ${warns ? `<span class="count-pill warn">▲ ${warns} to review</span>` : ""}
    ${blocks ? `<span class="count-pill block">■ ${blocks} blocking</span>` : ""}
    ${!warns && !blocks ? `<span class="muted">Nothing to flag.</span>` : ""}
  </div>`;

  const attentionHtml = attention.length
    ? `<div class="findings">${attention
        .sort((a, b) => (b.severity === "block") - (a.severity === "block"))
        .map(findingRow).join("")}</div>`
    : "";

  const passedHtml = passed.length
    ? `<details class="passed"><summary>${passed.length} checks that passed quietly</summary>
       <div class="findings">${passed.map(findingRow).join("")}</div></details>`
    : "";

  return summary + attentionHtml + passedHtml;
}

function findingRow(f) {
  const ev = f.evidence && Object.keys(f.evidence).length
    ? `<details><summary>the numbers behind this</summary><pre class="json">${
        esc(JSON.stringify(f.evidence, null, 2))}</pre></details>` : "";
  const outcome = f.outcome
    ? `<span class="rid">→ ${esc(DEC_LABEL[f.outcome] || f.outcome)}</span>` : "";
  return `<div class="finding ${esc(f.severity)}">
    <div class="icon">${SEV_ICON[f.severity]}</div>
    <div>
      <div class="hdr">
        <span class="sev">${esc(f.severity === "block" ? "blocks" : f.severity)}</span>
        <span class="rid">${esc(f.rule_id)} · ${esc(f.rule_name)}</span>${outcome}
      </div>
      <div class="msg">${esc(f.message)}</div>${ev}
    </div></div>`;
}

function decisionCard(d, ev) {
  const canAct = d.decision !== "AUTO_APPROVE";
  return `<div class="decision d-${esc(d.decision)}">
    <div class="top">
      <span class="badge">${DEC_ICON[d.decision]} ${esc(DEC_LABEL[d.decision])}</span>
      <span class="meaning">${esc(DEC_PLAIN[d.decision])}.</span>
      <span style="flex:1"></span>
      <span class="muted mono">${ev.duration_ms} ms</span>
    </div>
    <div class="why">${esc(d.reason)}</div>
    <div class="next">
      <span><b>What happens next:</b> ${esc(d.next_action)}</span>
      <span style="flex:1"></span>
      ${canAct ? `
        <button class="act-btn approve" data-act="approved">Approve anyway</button>
        <button class="act-btn reject" data-act="rejected">Reject</button>` : ""}
    </div>
  </div>`;
}

function wireActions(runId) {
  $$("#decision-slot .act-btn").forEach((b) => b.addEventListener("click", async () => {
    await fetch(`/api/runs/${runId}/action`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: b.dataset.act }),
    });
    toast(b.dataset.act === "approved"
      ? "Approved — this amount now counts against its order"
      : "Rejected — released from the order");
    $$("#decision-slot .act-btn").forEach((x) => { x.disabled = true; x.style.opacity = .45; });
  }));
}

/* ---------------- dashboard ---------------- */
async function loadDashboard() {
  const [stats, runs] = await Promise.all([
    (await fetch("/api/stats")).json(),
    (await fetch("/api/runs")).json(),
  ]);

  const by = stats.by_decision || {};
  const decided = DEC_ORDER.reduce((a, k) => a + (by[k] || 0), 0);
  const exceptions = (by.HOLD || 0) + (by.NEEDS_REVIEW || 0);

  $("#kpis").innerHTML = [
    ["Invoices processed", stats.total_runs, "every run on this instance"],
    ["Handled with no human", `${stats.touchless_rate}%`,
      `${by.AUTO_APPROVE || 0} of ${decided} went straight through`],
    ["Waiting on a person", exceptions,
      `${by.NEEDS_REVIEW || 0} to review · ${by.HOLD || 0} on hold`],
    ["Blocked from payment", by.REJECT || 0, "duplicates and blocked suppliers"],
    ["Median run", `${(stats.median_duration_ms / 1000).toFixed(1)}s`, "opening the PDF to deciding"],
  ].map(([label, value, foot]) => `<div class="kpi">
      <div class="label">${esc(label)}</div>
      <div class="value">${esc(value)}</div>
      <div class="foot">${esc(foot)}</div>
    </div>`).join("");

  $("#mix-meter").innerHTML = decided
    ? DEC_ORDER.filter((k) => by[k]).map((k) =>
        `<i class="seg-${k}" style="flex:${by[k]}" title="${DEC_LABEL[k]}: ${by[k]}"></i>`).join("")
    : `<i style="flex:1;background:var(--grid)"></i>`;
  $("#mix-legend").innerHTML = DEC_ORDER.map((k) =>
    `<span><i class="seg-${k}"></i> ${DEC_ICON[k]} ${DEC_LABEL[k]} · <b>${by[k] || 0}</b>
      <span class="muted">— ${DEC_PLAIN[k].toLowerCase()}</span></span>`).join("");

  $("#runs-body").innerHTML = runs.length ? runs.map((r) => `
    <tr class="clickable" data-run="${esc(r.id)}">
      <td class="muted">${esc(ago(r.created_at))}</td>
      <td><div class="mono">${esc(r.invoice_number || "—")}</div>
          <div class="muted" style="font-size:11px">${esc(r.scenario || r.source_file)}</div></td>
      <td>${esc(r.vendor_name || "—")}</td>
      <td class="mono">${esc(r.po_number || "—")}</td>
      <td class="num">${money(r.invoice_total, r.currency)}</td>
      <td class="muted" style="font-size:11.5px">${r.extraction_mode === "image_vision"
        ? "page image" : "real text"}</td>
      <td>${r.status === "error" ? `<span class="pill error">✕ error</span>` : pill(r.decision)}</td>
      <td class="muted">${r.human_action ? esc(r.human_action) : "—"}</td>
      <td class="num muted">${r.duration_ms ? (r.duration_ms / 1000).toFixed(1) + "s" : "—"}</td>
    </tr>`).join("")
    : `<tr><td colspan="9" class="muted" style="padding:20px;text-align:center">
         Nothing yet. Run an invoice from the Run tab.</td></tr>`;

  $$("#runs-body tr.clickable").forEach((tr) =>
    tr.addEventListener("click", () => openDrawer(tr.dataset.run)));
}

/* ---------------- run drawer ---------------- */
async function openDrawer(runId) {
  const r = await (await fetch(`/api/runs/${runId}`)).json();
  const inv = r.extracted || {}, m = r.match || {};
  $("#drawer-body").innerHTML = `
    <h3 style="margin:0 0 4px;font-size:17px">${esc(r.scenario || r.source_file)}</h3>
    <div class="muted mono" style="font-size:11.5px;margin-bottom:16px">
      run ${esc(r.id.slice(0, 12))} · ${esc(r.created_at)}</div>
    ${r.decision ? `<div class="decision d-${esc(r.decision)}">
      <div class="top">
        <span class="badge">${DEC_ICON[r.decision]} ${esc(DEC_LABEL[r.decision])}</span>
        <span class="meaning">${esc(r.human_action
          ? `A person then marked it ${r.human_action}.` : DEC_PLAIN[r.decision] + ".")}</span>
      </div>
      <div class="why">${esc(r.decision_reason || "")}</div>
      <div class="next"><span><b>What happens next:</b> ${esc(r.next_action || "")}</span></div>
    </div>` : ""}
    <div class="card"><h4>What was read off the document</h4>
      <table><tbody>
      ${[["Supplier", inv.vendor_name], ["Tax ID", inv.vendor_tax_id],
         ["Invoice number", inv.invoice_number], ["Invoice date", inv.invoice_date],
         ["Order reference", inv.po_reference], ["Currency", inv.currency],
         ["Subtotal", inv.subtotal == null ? null : money(inv.subtotal)],
         ["Tax", inv.tax_amount == null ? null : money(inv.tax_amount)],
         ["Total", inv.invoice_total == null ? null : money(inv.invoice_total, inv.currency)],
         ["Read as", r.extraction_mode === "image_vision" ? "page image (no text)" : "real text"],
         ["Read by", r.extractor === "heuristic" ? "fallback extractor (no AI)" : "Claude"]]
        .map(([k, v]) =>
        `<tr><td class="muted" style="width:38%">${esc(k)}</td>
             <td class="mono">${v == null || v === "" ? "—" : esc(v)}</td></tr>`).join("")}
      </tbody></table>
      ${inv.extraction_notes ? `<div class="muted" style="margin-top:10px;font-size:12px">
        Note from the model: ${esc(inv.extraction_notes)}</div>` : ""}
    </div>
    ${m.variance ? `<div class="card"><h4>Purchase order match</h4>
      ${chips([["order", m.po_number], ["how", (m.method || "").replace(/_/g, " ")],
               ["checking", m.variance.basis === "cumulative"
                 ? "everything billed so far" : "this invoice"],
               ["this invoice", money(m.variance.invoice_total)],
               ["already billed", money(m.variance.already_consumed)],
               ["order value", money(m.variance.po_amount)],
               ["difference", money(m.variance.variance_abs)],
               ["allowed", money(m.variance.tolerance_abs)]])}</div>` : ""}
    <div class="card"><h4>Every check that ran</h4>
      ${renderFindings(r.findings || [])}
    </div>
    <div class="card"><h4>Stage log</h4>
      <details><summary>raw event stream (${(r.stages || []).length} events)</summary>
      <pre class="json">${esc(JSON.stringify(r.stages, null, 2))}</pre></details>
    </div>
    <div style="margin-top:8px">
      <a href="/api/invoices/file?name=${encodeURIComponent(r.source_file)}" target="_blank">
        Open the source PDF ↗</a>
    </div>`;
  $("#drawer").classList.add("open");
  $("#drawer-bg").classList.add("open");
}
const closeDrawer = () => {
  $("#drawer").classList.remove("open");
  $("#drawer-bg").classList.remove("open");
};
$("#drawer-close").addEventListener("click", closeDrawer);
$("#drawer-bg").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

/* ---------------- master data ---------------- */
async function loadReference() {
  const d = await (await fetch("/api/reference")).json();

  $("#po-list").innerHTML = d.purchase_orders.map((p) => {
    const over = p.over;
    const scale = Math.max(p.po_amount, p.consumed) || 1;
    const pct = (v) => (100 * v / scale);
    return `<div class="po-row">
      <div class="po-head">
        <span class="n">${esc(p.po_number)}</span>
        <span class="d">${esc(p.description)}</span>
        <span class="a">${money(p.consumed, p.currency)} / ${money(p.po_amount)}</span>
      </div>
      <div class="po-bar">
        ${p.billed ? `<i class="billed" style="width:${pct(p.billed)}%"></i>` : ""}
        ${p.encumbered ? `<i class="encumbered" style="width:${pct(p.encumbered)}%"></i>` : ""}
        ${over ? `<i class="over" style="width:${pct(over)}%"></i>` : ""}
      </div>
      <div class="po-foot">
        <span>${esc(p.vendor_id)}</span>
        <span>${p.status}${p.allow_partial
          ? " · can be billed in parts" : " · one invoice only"}</span>
        <span>billed ${money(p.billed)}</span>
        <span>pending ${money(p.encumbered)}</span>
        <span>${over
          ? `<b style="color:var(--critical)">■ over by ${money(over)}</b>`
          : p.remaining < 0
            ? `fully used · ${money(-p.remaining)} inside the ${money(p.tolerance)} allowance`
            : `${money(p.remaining)} left`}</span>
      </div>
    </div>`;
  }).join("");

  $("#vendor-body").innerHTML = d.vendors.map((v) => `<tr>
    <td class="mono">${esc(v.vendor_id)}</td>
    <td>${esc(v.legal_name)}${v.status_reason
      ? `<div class="muted" style="font-size:11px">${esc(v.status_reason)}</div>` : ""}</td>
    <td>${v.status === "approved" ? `<span class="pill AUTO_APPROVE">✓ approved</span>`
        : v.status === "on_hold" ? `<span class="pill NEEDS_REVIEW">◑ on hold</span>`
        : `<span class="pill REJECT">✕ blocked</span>`}</td>
    <td class="mono">${esc(v.tax_id)}</td>
    <td class="mono">${esc(v.default_currency)}</td>
    <td class="muted">${esc(v.payment_terms)}</td>
  </tr>`).join("");
}

/* ---------------- reset ---------------- */
$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Clear all run history? Duplicate detection and order balances reset too.")) return;
  await fetch("/api/reset", { method: "POST" });
  toast("Run history cleared");
  loadDashboard();
});

/* ================= GUIDED TOUR =================
   Four dim panels form a frame around the target's bounding box, so the
   highlighted element shows through untouched. That beats z-index juggling,
   which breaks the moment a target sits inside its own stacking context. */

const HAPPY = "01_happy_path.pdf";

const TOUR = [
  { el: "#picker", side: "right",
    before: async () => {
      // Cleared so the walkthrough is reproducible: re-running an invoice is
      // genuinely a duplicate, and the tour would otherwise reject the very
      // invoice it just promised would sail through.
      await fetch("/api/reset", { method: "POST" });
    },
    title: "Eleven scenarios",
    body: "Each one is a situation an accounts-payable team actually hits. Start here is the ordinary case; the tricky ones are where the judgement lives. (I've cleared the run history so this walkthrough starts from a clean slate.)" },
  { el: "#brief", side: "bottom",
    before: async () => { selectInvoice(HAPPY); await sleep(250); },
    title: "Context before anything runs",
    body: "Picking an invoice doesn't run it. First you get the situation in plain English, why it's tricky, and what to watch for." },
  { el: "#doc-panel", side: "left",
    title: "The actual document",
    body: "This is the real PDF the process is about to read — the same file an AP clerk would open. Every scenario has one, including a scan with no text in it at all." },
  { el: "#run-cta", side: "bottom",
    title: "Now run it",
    body: "Seven stages, about seven seconds. Only one of them uses AI. Hit Next and I'll run it for you." },
  { el: "#st-ingest", side: "bottom",
    before: async () => {
      $("#tour-body").textContent = "Running… watch each stage light up as it finishes.";
      await startRun(HAPPY); await sleep(300);
    },
    title: "1 · Open the file",
    body: STAGE_PLAIN.ingest },
  { el: "#st-extract", side: "bottom", title: "2 · Read it", body: STAGE_PLAIN.extract },
  { el: "#st-normalize", side: "bottom", title: "3 · Tidy it up", body: STAGE_PLAIN.normalize },
  { el: "#st-identify", side: "bottom", title: "4 · Who is billing us?", body: STAGE_PLAIN.identify },
  { el: "#st-match", side: "bottom", title: "5 · What did we agree to buy?", body: STAGE_PLAIN.match },
  { el: "#st-validate", side: "top",
    title: "6 · Run the checks",
    body: "Seventeen checks. Anything needing attention is shown open; the ones that passed quietly are folded away, so you're not reading a wall of green." },
  { el: "#decision-slot", side: "top",
    title: "7 · Decide",
    body: "Four outcomes, not two. Needs review costs a person five minutes; on hold means something has to come back from outside the building. Collapsing those hides the only number an AP lead cares about." },
  { el: '.tab[data-view="dash"]', side: "bottom",
    before: async () => { showView("dash"); await sleep(300); },
    title: "History is part of the process",
    body: "Not a log — the process reads it. A duplicate invoice, or a third delivery that overruns its order, is only catchable because earlier runs are remembered." },
  { el: '.tab[data-view="ref"]', side: "bottom",
    before: async () => { showView("ref"); await sleep(300); },
    title: "Where each order stands",
    body: "How much of every purchase order has been used. This screen is what exposed the fifth edge case — an order billed to twice its value with nothing flagged." },
  { el: "#inv-list", side: "right",
    before: async () => { showView("run"); await sleep(300); },
    title: "Your turn",
    body: "Try the tricky ones in order. Split billing 1, 2 then 3 is the clearest — the third invoice looks perfectly normal and still gets stopped." },
];

let tourIdx = -1, tourRunning = false;

function positionTour(rect) {
  const pad = 6, vw = innerWidth, vh = innerHeight;
  const top = Math.max(0, rect.top - pad), left = Math.max(0, rect.left - pad);
  const right = Math.min(vw, rect.right + pad), bottom = Math.min(vh, rect.bottom + pad);
  const set = (side, s) => Object.assign($(`.tour-dim[data-side="${side}"]`).style, s);
  set("top",    { top: "0px", left: "0px", width: vw + "px", height: top + "px" });
  set("bottom", { top: bottom + "px", left: "0px", width: vw + "px", height: Math.max(0, vh - bottom) + "px" });
  set("left",   { top: top + "px", left: "0px", width: left + "px", height: (bottom - top) + "px" });
  set("right",  { top: top + "px", left: right + "px", width: Math.max(0, vw - right) + "px", height: (bottom - top) + "px" });
  Object.assign($("#tour-ring").style, {
    top: top + "px", left: left + "px",
    width: (right - left) + "px", height: (bottom - top) + "px",
  });
}

function placeCard(rect, side) {
  const card = $("#tour-card");
  const cw = card.offsetWidth, ch = card.offsetHeight, gap = 14;
  let top, left;
  if (side === "right")      { left = rect.right + gap; top = rect.top; }
  else if (side === "left")  { left = rect.left - cw - gap; top = rect.top; }
  else if (side === "top")   { top = rect.top - ch - gap; left = rect.left; }
  else                       { top = rect.bottom + gap; left = rect.left; }
  // Flip if it would fall off, then clamp inside the viewport.
  if (top + ch > innerHeight - 8) top = Math.max(8, rect.top - ch - gap);
  if (top < 8) top = Math.min(innerHeight - ch - 8, rect.bottom + gap);
  if (left + cw > innerWidth - 8) left = innerWidth - cw - 8;
  if (left < 8) left = 8;
  Object.assign(card.style, { top: Math.max(8, top) + "px", left: left + "px" });
}

async function showTourStep(i) {
  if (i < 0 || i >= TOUR.length) return endTour();
  tourIdx = i;
  const step = TOUR[i];

  $("#tour-count").textContent = `Step ${i + 1} of ${TOUR.length}`;
  $("#tour-title").textContent = step.title;
  $("#tour-body").textContent = step.body;
  $("#tour-back").disabled = i === 0;
  $("#tour-next").textContent = i === TOUR.length - 1 ? "Finish" : "Next";

  if (step.before) {
    $("#tour-next").disabled = true;
    try { await step.before(); } catch (e) { /* keep the tour alive */ }
    $("#tour-next").disabled = false;
    if (!tourRunning) return;            // skipped while we were waiting
    $("#tour-body").textContent = step.body;
  }

  const el = $(step.el);
  if (!el) return showTourStep(i + 1);
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(340);
  if (!tourRunning) return;

  const rect = el.getBoundingClientRect();
  positionTour(rect);
  placeCard(rect, step.side || "bottom");
}

function startTour() {
  $("#welcome").hidden = true;
  showView("run");
  tourRunning = true;
  $("#tour").hidden = false;
  store.set("zamp-seen-welcome", "1");
  showTourStep(0);
}
function endTour() {
  tourRunning = false;
  $("#tour").hidden = true;
}

$("#tour-next").addEventListener("click", () => showTourStep(tourIdx + 1));
$("#tour-back").addEventListener("click", () => showTourStep(tourIdx - 1));
$("#tour-skip").addEventListener("click", endTour);
$("#tour-btn").addEventListener("click", startTour);
$("#empty-tour-btn").addEventListener("click", startTour);
document.addEventListener("keydown", (e) => {
  if (!tourRunning) return;
  if (e.key === "Escape") endTour();
  if (e.key === "ArrowRight") showTourStep(tourIdx + 1);
  if (e.key === "ArrowLeft" && tourIdx > 0) showTourStep(tourIdx - 1);
});
addEventListener("resize", () => {
  if (!tourRunning) return;
  const el = $(TOUR[tourIdx]?.el);
  if (!el) return;
  const rect = el.getBoundingClientRect();
  positionTour(rect);
  placeCard(rect, TOUR[tourIdx].side || "bottom");
});

/* ---------------- welcome ---------------- */
$("#welcome-tour").addEventListener("click", startTour);
$("#welcome-close").addEventListener("click", () => {
  $("#welcome").hidden = true;
  store.set("zamp-seen-welcome", "1");
});

(async function init() {
  await loadInvoices();
  if (!store.get("zamp-seen-welcome")) $("#welcome").hidden = false;
})();
