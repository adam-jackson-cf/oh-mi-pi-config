// Jev lab UI. All dynamic text goes through textContent (audit data is untrusted).
const TOKEN = document.querySelector('meta[name="jev-lab-token"]').content;
const LIVE = document.querySelector('meta[name="jev-lab-live"]').content === "1";
const $ = id => document.getElementById(id);

const state = { source: "", version: "current", cases: [], selected: null, detail: null, tab: "cases", queue: [] };

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (v === null || v === undefined) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const child of children.flat()) if (child != null) el.append(child);
  return el;
}

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json", "x-jev-lab-token": TOKEN }, body: JSON.stringify(body),
  };
  const res = await fetch(path, init);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
  return data;
}

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}
const guarded = fn => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message); } };

const pct = p => (p === undefined || p === null ? "–" : p.toFixed(3));
const isScope = () => state.source === "jev-scope";

function bar(name, p) {
  return h("div", { class: "bar" }, h("span", { style: "width:110px" }, name), h("div", { class: "track" },
    h("div", { class: "fill", style: `width:${Math.round(Math.max(0, Math.min(1, p)) * 100)}%` })), h("span", {}, pct(p)));
}

// ---- sources ----
async function loadSources() {
  const { sources } = await api("/api/sources");
  const sel = $("source");
  sel.replaceChildren(...sources.map(s => h("option", { value: s.id }, `${s.title} (${s.count})`)));
  if (!state.source || !sources.some(s => s.id === state.source)) state.source = sources[0]?.id ?? "";
  sel.value = state.source;
  await loadCases();
}

function fillVersions(versions) {
  const sel = $("version");
  sel.hidden = !versions;
  if (!versions) { state.version = "current"; return; }
  const legacy = v => `${v} (legacy)`;
  sel.replaceChildren(h("option", { value: "current" }, versions.current ? `current: ${versions.current}` : "current"),
    ...versions.versions.filter(v => v !== versions.current).map(v => h("option", { value: v }, legacy(v))));
  if (![...sel.options].some(o => o.value === state.version)) state.version = "current";
  sel.value = state.version;
}

async function loadCases() {
  state.selected = null; state.detail = null;
  $("detail").replaceChildren(h("p", { class: "muted" }, "Select a case."));
  if (!state.source) { state.cases = []; renderList(); return; }
  const query = `/api/cases?source=${encodeURIComponent(state.source)}&version=${encodeURIComponent(state.version)}`;
  const loaded = await api(query);
  state.cases = loaded.cases;
  fillVersions(loaded.versions);
  const fill = (id, values, label) => {
    const sel = $(id);
    const keep = sel.value;
    sel.replaceChildren(h("option", { value: "" }, label), ...[...new Set(values)].filter(Boolean).sort().map(v => h("option", { value: v }, v)));
    sel.value = keep;
  };
  fill("f-verdict", state.cases.map(c => c.verdict), "all verdicts");
  fill("f-stage", state.cases.map(c => c.stage), "any stage");
  renderList();
  if (state.tab === "queue") await loadQueue();
  if (state.tab === "metrics") await loadMetrics();
}

function filtered() {
  const label = $("f-label").value, verdict = $("f-verdict").value, stage = $("f-stage").value, band = $("f-band").value;
  let list = state.cases.filter(c =>
    (!label || (label === "agent" ? c.labelBy === "agent" : (label === "labelled") === (c.label !== null))) &&
    (!verdict || c.verdict === verdict) && (!stage || c.stage === stage));
  if (band) {
    const [lo, hi] = band.split("-").map(Number);
    list = list.filter(c => c.score !== undefined && c.score >= lo && c.score < hi);
  }
  return list.sort($("f-sort").value === "uncertainty"
    ? (a, b) => b.uncertainty - a.uncertainty
    : (a, b) => (b.timestamp ?? "").localeCompare(a.timestamp ?? ""));
}

function caseItem(c) {
  return h("li", { class: `item${c.id === state.selected ? " sel" : ""}`, onclick: guarded(() => select(c.id)) },
    h("div", {}, h("span", { class: `tag${c.labelBy === "agent" ? " agent" : c.label ? " labelled" : ""}` },
      c.labelBy === "agent" ? `agent: ${c.label}` : c.label ?? "unlabelled"), h("span", { class: "tag" }, c.verdict),
      c.score !== undefined ? `P=${pct(c.score)}` : ""),
    h("div", { class: "sub" }, `${c.timestamp?.slice(0, 19) ?? ""} ${c.subject}`));
}

function renderList() {
  const list = filtered();
  $("case-count").textContent = `${list.length} of ${state.cases.length} cases`;
  $("case-list").replaceChildren(...list.map(caseItem));
}

async function select(id) {
  state.selected = id;
  state.detail = (await api(`/api/case?source=${encodeURIComponent(state.source)}&id=${encodeURIComponent(id)}`)).case;
  renderList();
  renderDetail();
}

// ---- detail ----
function proposalPanel(c) {
  const p = c.proposal;
  if (!p) return "";
  const owner = c.label === null ? "" : c.labelBy === "agent"
    ? h("span", { class: "tag agent" }, `agent: ${c.label} (unconfirmed)`)
    : c.label === p.label
      ? h("span", { class: "tag labelled" }, `owner: ${c.label} (confirmed)`)
      : h("span", { class: "tag overridden" }, `owner: ${c.label} (overridden)`);
  return h("div", { class: "proposal" },
    h("h4", {}, "First-pass proposal"),
    h("div", {}, h("span", { class: "tag" }, p.label), h("span", { class: `tag ${p.agreement}` }, p.agreement), owner),
    h("div", {}, p.rationale),
    p.namedChange ? h("div", {}, h("strong", {}, "Named change: "), p.namedChange) : "",
    p.evidence ? h("div", { class: "muted" }, `evidence: ${p.evidence}`) : "",
    h("div", { class: "muted" }, Object.entries(p.labellers).map(([k, v]) => `${k}: ${v}`).join(" · ")),
    h("button", { id: "accept", disabled: c.labelBy === "human" ? "" : null, onclick: guarded(acceptProposal) }, "a Accept proposal"));
}

async function acceptProposal() {
  const c = state.detail;
  if (!c?.proposal || c.labelBy === "human") return;
  await api("/api/label", { source: state.source, id: c.id, label: c.proposal.label, note: "accepted first-pass proposal" });
  toast(`accepted ${c.proposal.label}`);
  await refreshAfterLabel(c.id);
}

function renderDetail(replay) {
  const c = state.detail;
  const box = $("detail");
  if (!c) return;
  const answers = c.answers.map(a => h("div", {},
    h("strong", {}, `${a.id} (${a.type}) `),
    a.type === "noul" ? bar("yes", a.noul ?? 0) : "",
    a.type === "score" ? h("div", {}, `score ${a.score?.toFixed(2)} `) : "",
    a.choice ? h("div", {}, `choice: ${a.choice}, confidence ${pct(a.confidence)}`) : "",
    ...Object.entries(a.probabilities ?? {}).map(([k, v]) => bar(k, v))));
  const labels = h("div", { class: "labels" }, c.labelOptions.map((l, i) =>
    h("button", { disabled: c.labelBy === "human" ? "" : null, onclick: guarded(() => label(l)) }, `${i + 1} ${l}`)));
  if (c.label !== null) {
    labels.append(c.labelBy === "agent"
      ? h("span", { class: "tag agent" }, `labelled: ${c.label} (agent first-pass, unconfirmed)`)
      : h("span", { class: "tag labelled" }, `labelled: ${c.label}`), c.labelNote ?? "");
  }
  box.replaceChildren(
    h("h3", {}, c.subject),
    h("div", { class: "muted" }, [c.id, c.stage, c.version, c.taskSource ? `task source: ${c.taskSource}` : "",
      c.sufficient === undefined ? "" : c.sufficient ? "sufficient input" : "insufficient input"].filter(Boolean).join(" · ")),
    h("div", { class: "muted" }, [c.resolvedModel, c.costUsd !== undefined ? `$${c.costUsd}` : "", c.latencyMs !== undefined ? `${c.latencyMs} ms` : "",
      c.error ? `error: ${c.error}` : ""].filter(Boolean).join(" · ")),
    c.transcriptPath ? h("div", { class: "muted" }, `transcript: ${c.transcriptPath}`) : "",
    c.note ? h("div", { class: "muted" }, c.note) : "",
    proposalPanel(c),
    h("h4", {}, "Answers"), ...answers,
    c.expected ? h("div", {}, "expected: ", JSON.stringify(c.expected)) : "",
    h("h4", {}, "Label"), labels,
    h("input", { id: "note", placeholder: "note (n)", value: c.labelNote ?? "" }),
    h("div", { class: "row" }, h("button", { id: "replay", disabled: LIVE ? null : "", onclick: guarded(replayCase) }, "Replay live")),
    replay ?? "",
    h("h4", {}, "State"), h("pre", {}, JSON.stringify(c.state, null, 2)),
    h("h4", {}, "Questions"), h("pre", {}, JSON.stringify(c.questions ?? null, null, 2)));
}

async function label(value) {
  const c = state.detail;
  await api("/api/label", { source: state.source, id: c.id, label: value, note: $("note")?.value || undefined });
  toast(`labelled ${value}`);
  await refreshAfterLabel(c.id);
}

async function refreshAfterLabel(id) {
  const inQueue = state.tab === "queue";
  const order = filtered().map(c => c.id);
  await loadCases();
  if (inQueue) { await loadQueue(); return; }
  await select(order[order.indexOf(id) + 1] ?? id);
}

async function replayCase() {
  const c = state.detail;
  const out = await api("/api/replay", { source: state.source, id: c.id });
  const fresh = out.fresh.ok ? out.fresh.answers : [];
  const col = (title, answers) => h("div", {}, h("h4", {}, title), ...answers.map(a => h("div", {}, h("strong", {}, `${a.id} `),
    a.noul !== undefined ? bar("yes", a.noul) : "", a.choice ? `choice: ${a.choice}` : "", ...Object.entries(a.probabilities ?? {}).map(([k, v]) => bar(k, v)))));
  renderDetail(h("div", {}, h("div", { class: "warn" }, out.warning),
    out.fresh.ok ? "" : h("div", { class: "notice" }, `Replay failed: ${out.fresh.error}`),
    h("div", { class: "two" }, col("Stored", out.stored), col("Fresh", fresh)),
    out.fresh.ok ? h("div", { class: "muted" }, `${out.fresh.resolvedModel} · $${out.fresh.costUsd} · ${out.fresh.latencyMs} ms`) : ""));
}

// ---- queue ----
async function loadQueue() {
  const { progress, queue } = await api(`/api/queue?order=${$("queue-order").value}`);
  state.queue = queue;
  const frac = Math.min(1, progress.labelledSufficient / progress.minSufficient);
  $("progress").replaceChildren(
    h("div", {}, `Labelled sufficient-input cases: ${progress.labelledSufficient} / ${progress.minSufficient}; overreach labels: ${progress.positives} / ${progress.minPositive}`,
      progress.done ? " — minimum reached" : ""),
    h("div", {}, `human-confirmed: ${progress.humanConfirmed} · agent-labelled, awaiting confirmation: ${progress.agentLabelled}`),
    progress.proposalsTotal ? h("div", {}, `proposals reviewed: ${progress.proposalsReviewed} / ${progress.proposalsTotal}`) : "",
    h("div", { class: "progress-bar" }, h("div", { style: `width:${Math.round(frac * 100)}%` })));
  $("queue-list").replaceChildren(...queue.map(c => h("li", { class: "item", onclick: guarded(async () => {
    setTab("cases"); await select(c.id);
  }) }, h("div", {}, h("span", { class: "tag" }, c.verdict), `P=${pct(c.score)}`,
    c.proposal ? [h("span", { class: "tag" }, `proposed ${c.proposal.label}`), h("span", { class: `tag ${c.proposal.agreement}` }, c.proposal.agreement)] : ""),
    h("div", { class: "sub" }, c.subject))));
}

// ---- metrics ----
function table(headers, rows) {
  return h("table", {}, h("tr", {}, headers.map(x => h("th", {}, x))), rows.map(r => h("tr", {}, r.map(x => h("td", {}, String(x))))));
}

async function loadMetrics() {
  const threshold = Number($("threshold").value);
  const suff = $("suff").checked ? "1" : "0";
  const version = encodeURIComponent(state.version);
  const { metrics: m } = await api(`/api/metrics?source=${encodeURIComponent(state.source)}&version=${version}&threshold=${threshold}&sufficientOnly=${suff}`);
  const out = [h("div", {}, `${m.total} cases, ${m.unlabelled} unlabelled; labels: ${JSON.stringify(m.labelCounts)}; excluded as uncertain: ${m.excludedUncertain}`)];
  if (m.confusion) {
    const c = m.confusion;
    out.push(h("h4", {}, `Confusion at threshold ${threshold} (positive = ${m.positive}, ${m.scored} scored)`),
      table(["", `label ${m.positive}`, "label other"], [["predicted positive", c.tp, c.fp], ["predicted negative", c.fn, c.tn]]),
      h("div", {}, `precision ${c.precision === null ? "–" : c.precision.toFixed(3)} (${c.tp}/${c.tp + c.fp}), recall ${c.recall === null ? "–" : c.recall.toFixed(3)} (${c.tp}/${c.tp + c.fn})`),
      h("h4", {}, "Threshold sweep"),
      table(["threshold", "TP", "FP", "FN", "TN", "precision", "recall"], m.sweep.map(r =>
        [r.threshold.toFixed(2), r.tp, r.fp, r.fn, r.tn, r.precision === null ? "–" : r.precision.toFixed(3), r.recall === null ? "–" : r.recall.toFixed(3)])));
  }
  const labelCols = [...new Set(Object.values(m.verdictByLabel).flatMap(r => Object.keys(r)))].sort();
  out.push(h("h4", {}, "Verdict × label"),
    table(["verdict", ...labelCols], Object.entries(m.verdictByLabel).map(([v, r]) => [v, ...labelCols.map(l => r[l] ?? 0)])),
    h("h4", {}, "Cost and latency"),
    table(["calls (cost)", "total $", "mean $", "calls (latency)", "mean ms", "p50 ms", "p95 ms"],
      [[m.cost.calls, m.cost.totalUsd.toFixed(6), m.cost.meanUsd?.toFixed(6) ?? "–", m.latency.calls, m.latency.meanMs?.toFixed(0) ?? "–", m.latency.p50Ms ?? "–", m.latency.p95Ms ?? "–"]]));
  $("metrics").replaceChildren(...out);
  $("suff-wrap").hidden = !isScope();
}

// ---- playground ----
function parseField(id) {
  try { return JSON.parse($(id).value); } catch { throw new Error(`${id} is not valid JSON`); }
}

function renderOutcome(o) {
  if (!o.ok) return h("div", { class: "notice" }, `Failed: ${o.error}`);
  return h("div", {}, ...o.answers.map(a => h("div", {}, h("strong", {}, `${a.id} (${a.type}) `),
    a.noul !== undefined ? bar("yes", a.noul) : "", a.choice ? `choice: ${a.choice}` : "", a.score !== undefined ? `score ${a.score.toFixed(2)}` : "",
    ...Object.entries(a.probabilities ?? {}).map(([k, v]) => bar(k, v)))), h("div", { class: "muted" }, `${o.resolvedModel} · $${o.costUsd} · ${o.latencyMs} ms`));
}

async function refreshCasesets() {
  const { sources } = await api("/api/sources");
  $("pg-existing").replaceChildren(...sources.filter(s => s.kind === "caseset").map(s => h("option", { value: s.id.slice(8) }, s.title)));
}

async function playgroundRun() {
  const { outcome } = await api("/api/playground/run", { state: parseField("pg-state"), questions: parseField("pg-questions") });
  $("pg-out").replaceChildren(renderOutcome(outcome));
}

async function playgroundSave() {
  const expectedText = $("pg-expected").value.trim();
  const row = { id: $("pg-id").value.trim(), state: parseField("pg-state"), questions: parseField("pg-questions") };
  if (expectedText) row.expected = parseField("pg-expected");
  await api("/api/caseset/save", { name: $("pg-set").value.trim(), case: row });
  toast("case saved");
  await refreshCasesets();
}

async function playgroundRunSet() {
  const name = $("pg-existing").value;
  const out = await api("/api/caseset/run", { name });
  $("pg-out").replaceChildren(h("div", {}, `${name}: ${out.passed}/${out.checked} expected checks passed across ${out.cases} cases`),
    table(["case", "result", "checks"], out.results.map(r => [r.id, r.outcome.ok ? "ok" : r.outcome.error,
      r.checks.map(c => `${c.question}: ${c.pass ? "pass" : "FAIL"} (want ${c.expected}, got ${c.actual})`).join("; ")])));
}

// ---- wiring ----
function setTab(tab) {
  state.tab = tab;
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("active", b.dataset.tab === tab);
  for (const name of ["cases", "queue", "metrics", "playground"]) $(`tab-${name}`).hidden = name !== tab;
}

for (const b of document.querySelectorAll("#tabs button")) {
  b.addEventListener("click", guarded(async () => {
    setTab(b.dataset.tab);
    if (b.dataset.tab === "queue") await loadQueue();
    if (b.dataset.tab === "metrics") await loadMetrics();
    if (b.dataset.tab === "playground") await refreshCasesets();
  }));
}
$("source").addEventListener("change", guarded(async () => { state.source = $("source").value; state.version = "current"; await loadCases(); }));
$("version").addEventListener("change", guarded(async () => { state.version = $("version").value; await loadCases(); }));
$("queue-order").addEventListener("change", guarded(loadQueue));
for (const id of ["f-label", "f-verdict", "f-band", "f-stage", "f-sort"]) $(id).addEventListener("change", renderList);
$("threshold").addEventListener("change", guarded(loadMetrics));
$("suff").addEventListener("change", guarded(loadMetrics));
$("pg-run").addEventListener("click", guarded(playgroundRun));
$("pg-save").addEventListener("click", guarded(playgroundSave));
$("pg-runset").addEventListener("click", guarded(playgroundRunSet));
if (!LIVE) {
  $("notice").hidden = false;
  for (const id of ["pg-run", "pg-runset"]) $(id).disabled = true;
}

document.addEventListener("keydown", guarded(async event => {
  if (event.target.matches("input, textarea, select") || state.tab !== "cases") return;
  const list = filtered();
  const at = list.findIndex(c => c.id === state.selected);
  if (event.key === "j" && list[at + 1]) await select(list[at + 1].id);
  else if (event.key === "k" && list[at - 1]) await select(list[at - 1].id);
  else if (event.key === "a") await acceptProposal();
  else if (event.key === "n") { event.preventDefault(); $("note")?.focus(); }
  else if (/^[1-4]$/.test(event.key) && state.detail && state.detail.labelBy !== "human") {
    const option = state.detail.labelOptions[Number(event.key) - 1];
    if (option) await label(option);
  }
}));

guarded(loadSources)();
