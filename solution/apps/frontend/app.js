import { escapeHtml as esc, renderMarkdown } from "./markdown.js";

const API = (window.COPILOT_API_BASE || "") + "/api";
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };

const EXAMPLES = [
  "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence.",
  "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.",
  "Why are compressor discharge pressure alarms repeatedly occurring?",
  "Which alarm has the highest priority in EastRefinery, and why?",
  "What related assets should be inspected for this motor trip alarm?",
  "Are the API recommendations consistent with the maintenance manual?",
];

const state = {
  conversationId: sessionStorageGet("conversationId"),
  last: null,
  busy: false,
};

function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function sessionStorageSet(k, v) { try { v ? sessionStorage.setItem(k, v) : sessionStorage.removeItem(k); } catch { /* ignore */ } }

async function api(path, options = {}) {
  const res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...options });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const detail = body?.detail?.message || body?.detail || body?.error || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

// ------------------------------------------------------------------ header status
async function refreshHealth() {
  try {
    const h = await api("/health");
    const mcp = h.mcp_servers || {};
    const alarm = mcp["alarm-management"];
    const names = Object.entries(mcp).map(([n, s]) => `${n}${s.connected ? ` (${s.tools})` : " ✗"}`).join(", ");
    setPill("status-mcp", alarm?.connected ? "ok" : "err", `MCP: ${names || "none"}`);
    setPill("status-rag", h.rag?.status === "ok" ? "ok" : "err",
      h.rag?.status === "ok" ? `RAG: ${h.rag.documents} docs / ${h.rag.chunks} chunks` : "RAG: unavailable");
    setPill("status-llm", "muted", `LLM: ${h.llm.startsWith("disabled") ? "off (deterministic)" : h.llm}`);
  } catch (e) {
    setPill("status-mcp", "err", "Backend unreachable");
  }
}
function setPill(id, kind, text) { const p = $("#" + id); p.className = `pill pill-${kind}`; p.textContent = text; }

// ------------------------------------------------------------------ tabs
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${name}`));
}

// ------------------------------------------------------------------ chat
function addUserMessage(text) {
  $("#empty-state")?.remove();
  const m = el("div", "msg msg-user");
  m.textContent = text;
  $("#messages").appendChild(m);
  scrollDown();
}
function addLoading() {
  const m = el("div", "msg msg-bot loading", `<div class="spinner"></div><span>Discovering tools, calling MCP and retrieving documents…</span>`);
  $("#messages").appendChild(m);
  scrollDown();
  return m;
}
function scrollDown() { const box = $("#messages"); box.scrollTop = box.scrollHeight; }

const STATUS_PILL = { complete: "ok", partial: "warn", needs_clarification: "warn", failed: "err" };

function addBotMessage(r) {
  const m = el("div", "msg msg-bot");
  const okTools = r.tool_trace.filter((t) => t.status === "success").length;
  m.appendChild(el("div", "msg-meta",
    `<span class="pill pill-${STATUS_PILL[r.status] || "muted"}">${esc(r.status.replace("_", " "))}</span>
     <span class="pill pill-muted" title="${esc(r.intent.rationale)}">intent: ${esc(r.intent.name)} (${Math.round(r.intent.confidence * 100)}%)</span>
     <span class="pill pill-muted">${okTools}/${r.tool_trace.length} MCP calls</span>
     <span class="pill pill-muted">${r.citations.length} citations</span>
     <span class="pill pill-muted">${esc(r.answer.generated_by)}</span>
     <span class="pill pill-muted">${Math.round(r.timings.total_ms)} ms</span>`));
  const body = el("div", "md", renderMarkdown(r.answer.markdown));
  m.appendChild(body);
  for (const p of r.proposed_actions || []) {
    const box = el("div", "proposed", `<strong>Proposed work request</strong> (requires your confirmation)<br>
      <small>${esc(p.asset_id)} · ${esc(p.priority)} · ${esc(p.title)}</small><br>`);
    const b = el("button", "btn btn-small", "Review & create…");
    b.addEventListener("click", () => draftWorkRequest(p, b));
    box.appendChild(b);
    m.appendChild(box);
  }
  if (r.suggested_follow_ups?.length) {
    const f = el("div", "followups");
    r.suggested_follow_ups.forEach((q) => {
      const b = el("button", "btn btn-small btn-ghost");
      b.textContent = q;
      b.addEventListener("click", () => send(q));
      f.appendChild(b);
    });
    m.appendChild(f);
  }
  $("#messages").appendChild(m);
  bindRefs(m);
  scrollDown();
}

function addErrorMessage(text) {
  const m = el("div", "msg msg-bot msg-error", `<strong>Request failed.</strong> ${esc(text)}<br><small class="muted">Check that the backend and MCP server are running (see header status).</small>`);
  $("#messages").appendChild(m);
  scrollDown();
}

function bindRefs(root) {
  root.querySelectorAll(".ref").forEach((r) => {
    const go = () => focusRef(r.dataset.ref);
    r.addEventListener("click", go);
    r.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
}

function focusRef(ref) {
  const isDoc = ref.startsWith("D");
  showTab(isDoc ? "sources" : "trace");
  const target = document.getElementById(`ref-${ref}`);
  if (target) {
    if (target.tagName === "DETAILS") target.open = true;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("flash");
    setTimeout(() => target.classList.remove("flash"), 1600);
  }
}

async function send(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  $("#send").disabled = true;
  addUserMessage(text);
  const loading = addLoading();
  try {
    const r = await api("/chat", { method: "POST", body: JSON.stringify({ message: text, conversation_id: state.conversationId }) });
    state.conversationId = r.conversation_id;
    sessionStorageSet("conversationId", r.conversation_id);
    state.last = r;
    loading.remove();
    addBotMessage(r);
    renderPanels(r);
  } catch (e) {
    loading.remove();
    addErrorMessage(e.message);
  } finally {
    state.busy = false;
    $("#send").disabled = false;
    $("#input").focus();
  }
}

// ------------------------------------------------------------------ panels
function sevBadge(s) { return `<span class="sev sev-${esc(s)}">${esc(s)}</span>`; }
function refs(list) { return (list || []).filter(Boolean).map((r) => `<span class="ref ${r.startsWith("D") ? "ref-d" : ""}" data-ref="${esc(r)}" role="button" tabindex="0">${esc(r)}</span>`).join(""); }

function renderPanels(r) {
  renderInvestigation(r);
  renderSources(r);
  renderTrace(r);
  renderRetrieval(r);
  $("#count-sources").textContent = r.citations.length ? `(${r.citations.length})` : "";
  $("#count-trace").textContent = r.tool_trace.length ? `(${r.tool_trace.length})` : "";
}

function renderInvestigation(r) {
  const inv = r.investigation;
  const pane = $("#pane-investigation");
  pane.innerHTML = "";
  if (r.warnings.length) {
    pane.appendChild(el("div", r.status === "partial" ? "warn-box" : "warn-box",
      `<strong>Warnings / degraded steps</strong><ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`));
  }
  if (r.status === "needs_clarification") {
    pane.appendChild(el("p", "muted", "The copilot needs more information to scope the investigation."));
    return;
  }
  const s = inv.alarm_summary;
  if (s || inv.active_alarms.length || inv.priority) {
    const sec = el("div", "section", "<h4>Alarm summary</h4>");
    const k = el("div", "kpis");
    const kpi = (v, l) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`;
    let html = "";
    if (s) {
      html += kpi(s.total_alarms, `alarms${s.severity_filter ? " (" + s.severity_filter.join("/") + ")" : ""}`);
      html += kpi(`${Math.round((s.kpis.recurring_rate || 0) * 100)}%`, "recurring rate");
      if (s.kpis.avg_ack_delay_seconds != null) html += kpi(`${(s.kpis.avg_ack_delay_seconds / 60).toFixed(1)} min`, "avg ack delay");
    }
    if (inv.trend) html += kpi(inv.trend.direction, "trend");
    html += kpi(inv.active_alarms.length, "active alarms");
    if (inv.priority) html += kpi(inv.priority.priority_score, `priority (${inv.priority.priority_band})`);
    k.innerHTML = html;
    sec.appendChild(k);
    if (s?.top_alarms?.length) {
      const max = Math.max(...s.top_alarms.map((t) => t.alarm_count));
      const bars = el("div", "bars");
      bars.style.marginTop = "10px";
      s.top_alarms.slice(0, 6).forEach((t) => {
        const b = el("div", "bar", `<span>${esc(t.alarm_name)} ${t.max_severity ? sevBadge(t.max_severity) : ""}</span><div class="track"><div class="fill"></div></div><span>${t.alarm_count}</span>`);
        $(".fill", b).style.width = `${Math.max(3, (100 * t.alarm_count) / max)}%`;
        bars.appendChild(b);
      });
      sec.appendChild(bars);
    }
    pane.appendChild(sec);
  }
  if (inv.focal_alarm) {
    const f = inv.focal_alarm;
    pane.appendChild(el("div", "section", `<h4>Focus alarm</h4><div class="item"><div class="item-head">
      <strong>${esc(f.alarm_name)}</strong>${sevBadge(f.severity)}</div>
      <small>${esc(f.asset_name)} · ${esc(f.alarm_id)} · ${esc(f.status)} · started ${esc(f.start_time)}</small>
      ${inv.priority ? `<div><small>${esc(inv.priority.rationale || "")}</small></div>` : ""}</div>`));
  }
  if (inv.ranked_alarms.length) {
    pane.appendChild(el("div", "section", `<h4>Priority ranking</h4><div class="list">${inv.ranked_alarms.map((a) =>
      `<div class="item"><div class="item-head"><span>#${a.rank} <strong>${esc(a.alarm_name)}</strong> · ${esc(a.asset_name)}</span>
      <span>${esc(a.priority_score)} (${esc(a.priority_band)})</span></div></div>`).join("")}</div>`));
  }
  if (inv.active_alarms.length) {
    pane.appendChild(el("div", "section", `<h4>Active alarms</h4><div class="list">${inv.active_alarms.slice(0, 8).map((a) =>
      `<div class="item"><div class="item-head"><span><strong>${esc(a.alarm_name)}</strong> · ${esc(a.asset_name)}</span>${sevBadge(a.severity)}</div>
      <small>${esc(a.alarm_id)} · since ${esc(a.start_time)} · ${a.acknowledged ? "acknowledged" : "UNACKNOWLEDGED"}</small></div>`).join("")}</div>`));
  }
  if (inv.likely_causes.length) {
    pane.appendChild(el("div", "section", `<h4>Likely causes</h4><div class="list">${inv.likely_causes.map((c) =>
      `<div class="item"><div class="item-head"><span>${esc(c.cause)}</span><small>${esc(c.confidence)} confidence</small></div>
      <ul>${c.evidence.map((e) => `<li><small>${esc(e)}</small></li>`).join("")}</ul>${refs(c.refs)}</div>`).join("")}</div>`));
  }
  if (inv.actions.length) {
    const label = { supported: "✅ consistent with documents", conflict: "⚠️ conflicts with documents", not_covered: "➖ not covered by documents" };
    pane.appendChild(el("div", "section", `<h4>Recommendations</h4><div class="list">${inv.actions.map((a) =>
      `<div class="item"><div class="item-head"><span>${esc(a.action)}</span><small>${esc(a.source)}${a.urgency ? " · " + esc(a.urgency) : ""}</small></div>
      ${a.consistency ? `<div class="badge-${esc(a.consistency)}">${label[a.consistency]}</div>` : ""}
      ${a.note ? `<small>${esc(a.note)}</small><br>` : ""}${refs(a.refs)}</div>`).join("")}</div>`));
  }
  if (inv.related_assets.length) {
    pane.appendChild(el("div", "section", `<h4>Related assets to inspect</h4><div class="list">${inv.related_assets.map((a) =>
      `<div class="item"><div class="item-head"><strong>${esc(a.asset_name)}</strong><small>${esc((a.relationship || "").replace(/_/g, " "))}</small></div>
      <small>${esc(a.reason || "")}</small> ${refs(a.refs)}</div>`).join("")}</div>`));
  }
  if (inv.work_orders.length) {
    pane.appendChild(el("div", "section", `<h4>Open work orders (secondary MCP server)</h4><div class="list">${inv.work_orders.map((w) =>
      `<div class="item"><div class="item-head"><strong>${esc(w.work_order_id)}</strong><small>${esc(w.priority)} · ${esc(w.status)}</small></div><small>${esc(w.asset_id)} · ${esc(w.title)}</small></div>`).join("")}</div>`));
  }
  if (inv.kpis.length) {
    pane.appendChild(el("div", "section", `<h4>KPIs</h4><div class="kpis">${inv.kpis.map((k) =>
      `<div class="kpi"><div class="v">${esc(k.value)}</div><div class="l">${esc(k.name)} (${esc(k.unit)})</div></div>`).join("")}</div>`));
  }
  if (!pane.children.length) pane.appendChild(el("p", "muted", "No structured alarm data for this question (document-only answer)."));
  bindRefs(pane);
}

function renderSources(r) {
  const pane = $("#pane-sources");
  if (!r.citations.length) { pane.innerHTML = `<p class="muted">No document passages were used for this answer.</p>`; return; }
  pane.innerHTML = `<div class="list">${r.citations.map((c) => `
    <div class="item" id="ref-${esc(c.citation_id)}">
      <div class="item-head"><span><span class="ref ref-d">${esc(c.citation_id)}</span> <strong>${esc(c.title)}</strong></span>
        <small>${esc(c.doc_type.replace(/_/g, " "))} · ${esc(c.trust_level)}</small></div>
      <small>${esc(c.doc_id)} ${c.section_number ? "§" + esc(c.section_number) : ""} – ${esc(c.section)}${c.revision ? " · rev " + esc(c.revision) : ""}${c.effective_date ? " · effective " + esc(c.effective_date) : ""}</small>
      <p>${esc(c.snippet)}</p>
      <small class="muted">${esc(c.source_path)} · chunk ${esc(c.chunk_id)} · score ${esc(c.score)}</small>
    </div>`).join("")}</div>`;
}

function renderTrace(r) {
  const pane = $("#pane-trace");
  pane.innerHTML = `<dl class="kv"><dt>trace_id</dt><dd><code>${esc(r.trace_id)}</code></dd>
    <dt>conversation</dt><dd><code>${esc(r.conversation_id)}</code></dd>
    <dt>tools discovered</dt><dd>${r.discovered_tools.length}: ${r.discovered_tools.map(esc).join(", ")}</dd>
    <dt>timings</dt><dd>total ${r.timings.total_ms} ms · tools ${r.timings.tools_ms} ms · retrieval ${r.timings.retrieval_ms} ms${r.timings.llm_ms ? " · LLM " + r.timings.llm_ms + " ms" : ""}</dd></dl><br>`;
  if (!r.tool_trace.length) { pane.appendChild(el("p", "muted", "No MCP tools were invoked for this turn.")); return; }
  r.tool_trace.forEach((t) => {
    const d = el("details", "trace");
    d.id = `ref-${t.ref}`;
    const apiCalls = t.api_calls || [];
    const retries = apiCalls.reduce((n, c) => n + (c.retries?.length || 0), 0) + (t.retries || 0);
    d.innerHTML = `<summary><span class="ref">${esc(t.ref)}</span><strong>${esc(t.tool)}</strong>
      <span class="st-${esc(t.status)}">● ${esc(t.status)}</span><small class="muted">${esc(t.server)} · ${esc(t.step)} · ${t.duration_ms} ms</small>
      ${retries ? `<span class="pill pill-warn">${retries} retr${retries > 1 ? "ies" : "y"}</span>` : ""}
      ${apiCalls.length ? `<span class="pill pill-muted">${apiCalls.length} API call${apiCalls.length > 1 ? "s" : ""}</span>` : ""}</summary>
      <div class="trace-body">
        <h4>Request (MCP tools/call arguments)</h4><pre class="json">${esc(JSON.stringify(t.arguments, null, 2))}</pre>
        ${t.error ? `<h4>Error</h4><pre class="json">${esc(JSON.stringify(t.error, null, 2))}</pre>` : ""}
        ${apiCalls.length ? `<h4>Underlying Alarm API calls</h4><pre class="json">${esc(JSON.stringify(apiCalls, null, 2))}</pre>` : ""}
        ${t.result ? `<h4>Response (structured content)</h4><pre class="json">${esc(JSON.stringify(t.result, null, 2).slice(0, 20000))}</pre>` : ""}
      </div>`;
    pane.appendChild(d);
  });
}

function renderRetrieval(r) {
  const pane = $("#pane-retrieval");
  if (!r.retrieval.length) { pane.innerHTML = `<p class="muted">No retrieval was performed.</p>`; return; }
  pane.innerHTML = `<div class="list">` + r.retrieval.map((q) => `
    <div class="item">
      <div class="item-head"><strong>${esc(q.purpose)}</strong><small>confidence: <span class="${q.confidence === "high" ? "st-success" : q.confidence === "none" ? "st-error" : "st-invalid_arguments"}">${esc(q.confidence)}</span> · ${q.duration_ms} ms</small></div>
      <small>query: <code>${esc(q.query)}</code></small><br>
      <small>filters: <code>${esc(JSON.stringify(q.filters))}</code></small><br>
      <small class="muted">${esc(q.confidence_reason)}</small>
      <ul>${q.hits.map((h) => `<li><small>${esc(h.doc_id)} – ${esc(h.section || "")} · fused ${h.score} · dense ${h.dense} · bm25 ${h.bm25}${h.sanitized ? " · sanitized" : ""}</small></li>`).join("")}</ul>
      ${q.quarantined.length ? `<div class="err-box"><small>Quarantined (prompt-injection suspected): ${q.quarantined.map((x) => esc(x.chunk_id)).join(", ")}</small></div>` : ""}
    </div>`).join("") + `</div>`;
}

async function renderTools() {
  const pane = $("#pane-tools");
  try {
    const t = await api("/tools");
    pane.innerHTML = `<div class="section"><h4>MCP servers</h4><div class="list">${t.servers.map((s) =>
      `<div class="item"><div class="item-head"><strong>${esc(s.name)}</strong><span class="${s.connected ? "st-success" : "st-error"}">● ${s.connected ? "connected" : "unavailable"}</span></div>
      <small>${s.tools} tools ${s.server_info?.version ? "· v" + esc(s.server_info.version) : ""} ${s.error ? "· " + esc(s.error) : ""}</small></div>`).join("")}</div></div>
      <div class="section"><h4>Discovered tools (${t.tools.length})</h4>${t.tools.map((x) => `
      <details class="trace"><summary><strong>${esc(x.name)}</strong><small class="muted">${esc(x.server)}</small>
        ${x.read_only ? `<span class="pill pill-ok">read-only</span>` : `<span class="pill pill-warn">write · approval required</span>`}</summary>
        <div class="trace-body"><p>${esc(x.description)}</p><h4>Input schema</h4><pre class="json">${esc(JSON.stringify(x.input_schema, null, 2))}</pre>
        ${x.output_schema ? `<h4>Output schema</h4><pre class="json">${esc(JSON.stringify(x.output_schema, null, 2).slice(0, 6000))}</pre>` : ""}</div></details>`).join("")}</div>`;
  } catch (e) {
    pane.innerHTML = `<div class="err-box">Tool discovery failed: ${esc(e.message)}</div>`;
  }
}

// ------------------------------------------------------------------ write operation with explicit approval
let pendingDraft = null;
async function draftWorkRequest(p, button) {
  button.disabled = true;
  try {
    const r = await api("/work-requests/draft", { method: "POST", body: JSON.stringify({ asset_id: p.asset_id, title: p.title, description: p.description, priority: p.priority }) });
    pendingDraft = r.draft;
    $("#wr-draft").textContent = JSON.stringify(r.draft.draft, null, 2);
    $("#wr-dialog").showModal();
  } catch (e) {
    alert(`Could not draft work request: ${e.message}`);
  } finally {
    button.disabled = false;
  }
}

async function confirmWorkRequest() {
  const approver = $("#wr-approver").value.trim();
  if (!pendingDraft || approver.length < 2) return;
  try {
    const r = await api("/work-requests/confirm", { method: "POST", body: JSON.stringify({ draft_id: pendingDraft.draft_id, approval_token: pendingDraft.approval_token, approved_by: approver, confirm: true }) });
    const wo = r.result.work_order;
    const m = el("div", "msg msg-bot", `✅ Work request <strong>${esc(wo.work_order_id)}</strong> created for ${esc(wo.asset_id)} (approved by ${esc(r.result.approved_by)}).`);
    $("#messages").appendChild(m);
    scrollDown();
  } catch (e) {
    addErrorMessage(`Work request not created: ${e.message}`);
  } finally {
    pendingDraft = null;
  }
}

// ------------------------------------------------------------------ demo controls
const FAULTS = [
  { label: "Transient: 2× HTTP 503 on correlation (retry succeeds)", body: { path_prefix: "/alarms/correlation", mode: "error", status_code: 503, remaining: 2 } },
  { label: "Outage: correlation endpoint down (partial failure)", body: { path_prefix: "/alarms/correlation", mode: "error", status_code: 503 } },
  { label: "Outage: recommendations endpoint down", body: { path_prefix: "/recommendations", mode: "error", status_code: 500 } },
  { label: "Rate limit: 1× HTTP 429 on alarm summary", body: { path_prefix: "/alarms/summary", mode: "error", status_code: 429, remaining: 1 } },
  { label: "Slow API: 12 s latency on trends (timeout + retry)", body: { path_prefix: "/alarms/trends", mode: "latency", delay_ms: 12000, remaining: 1 } },
];
async function renderDemo() {
  const pane = $("#pane-demo");
  pane.innerHTML = `<p class="muted">Inject failures into the Alarm Management API simulator to demonstrate retries, error mapping and degraded answers. (Test harness only - disabled unless ENABLE_DEMO_CONTROLS=true.)</p><div class="list" id="fault-buttons"></div><br><button class="btn" id="fault-clear">Clear all faults</button><pre class="json" id="fault-state"></pre>`;
  FAULTS.forEach((f) => {
    const b = el("button", "btn");
    b.textContent = f.label;
    b.addEventListener("click", async () => { $("#fault-state").textContent = JSON.stringify(await api("/demo/faults", { method: "POST", body: JSON.stringify(f.body) }), null, 2); });
    $("#fault-buttons").appendChild(b);
  });
  $("#fault-clear").addEventListener("click", async () => { $("#fault-state").textContent = JSON.stringify(await api("/demo/faults", { method: "DELETE" }), null, 2); });
}

// ------------------------------------------------------------------ boot
function boot() {
  const ex = $("#examples");
  EXAMPLES.forEach((q) => {
    const b = el("button", "btn example");
    b.textContent = q;
    b.addEventListener("click", () => send(q));
    ex.appendChild(b);
  });
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#input").value; $("#input").value = ""; send(v); });
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  $("#btn-new").addEventListener("click", () => { state.conversationId = null; sessionStorageSet("conversationId", null); location.reload(); });
  $("#wr-form").addEventListener("submit", (e) => { if (e.submitter?.value === "confirm") confirmWorkRequest(); });
  refreshHealth();
  setInterval(refreshHealth, 30000);
  renderTools();
  api("/config").then((c) => { if (c.demo_controls) { $("#tab-demo").classList.remove("hidden"); renderDemo(); } }).catch(() => {});
}
boot();
