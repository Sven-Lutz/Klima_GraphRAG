"use strict";

const state = { result: null, status: null };
const $ = (selector) => document.querySelector(selector);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  });
  children.flat().forEach((child) => node.append(child instanceof Node ? child : document.createTextNode(String(child))));
  return node;
};
const replace = (target, ...children) => target.replaceChildren(...children.flat());
const shortId = (value) => value ? (value.length > 24 ? `${value.slice(0, 16)}…${value.slice(-6)}` : value) : "—";
const human = (value) => String(value ?? "—").replaceAll("_", " ");

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 4500);
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error("status unavailable");
    state.status = await response.json();
    const gate = state.status.provider_gate;
    const label = gate.availability === "answer_generation_ready" ? "Generation configured" : "Retrieval-only ready";
    const system = $("#system-state");
    replace(system, el("span", { class: "state-dot", "aria-hidden": "true" }),
      el("div", {}, el("strong", {}, label), el("small", {}, gate.message)));
    if (state.status.demo_mode) {
      const banner = $("#demo-banner");
      banner.textContent = `${state.status.demo_notice} Live generation provider acceptance remains open.`;
      banner.hidden = false;
      $("#question").value = "Welche Solarprojekte planen die Stadtwerke?";
    }
  } catch (_error) {
    replace($("#system-state"), el("span", { class: "state-dot", "aria-hidden": "true" }),
      el("div", {}, el("strong", {}, "Backend unavailable"), el("small", {}, "Check application configuration")));
  }
}

function statusDescriptor(result) {
  if (result.result_kind === "retrieval") {
    return result.evidence.length ? ["Retrieval complete", "Canonical evidence is ready for inspection.", "success"]
      : ["No evidence", "No canonical evidence matched this query.", "warning"];
  }
  const answer = result.answer_result;
  if (answer.status === "answered") return ["Grounded answer", "Structural grounding validation accepted the answer.", "success"];
  if (answer.status === "insufficient_evidence") return ["Insufficient evidence", "The selected evidence cannot support a substantive answer.", "warning"];
  if (answer.status === "grounding_failed") return ["Grounding failure", "Generated output was rejected and is not shown as an answer.", "danger"];
  const code = answer.failure?.code;
  if (code === "provider_timeout") return ["Provider timeout", "Retrieval succeeded; generation timed out. Evidence remains available.", "warning"];
  if (code === "provider_unavailable") return ["Answer generation unavailable", "Retrieval succeeded; the provider is unavailable.", "warning"];
  return ["Generation failure", "Retrieval succeeded, but no valid answer was generated.", "danger"];
}

function renderStatus(result) {
  const [title, message, tone] = statusDescriptor(result);
  const box = $("#result-status");
  box.dataset.tone = tone;
  replace(box, el("strong", {}, title), el("span", {}, message));
}

function openCitation(label) {
  const evidence = state.result.evidence.find((item) => item.citation?.label === label);
  if (evidence) openEvidence(evidence);
}

function renderAnswer(result) {
  const panel = $("#answer-panel");
  if (result.result_kind === "retrieval") {
    replace(panel, el("h3", {}, "Retrieval-only result"),
      el("p", { class: "answer-text" }, "Answer generation was disabled. Inspect the ranked evidence and graph context below."));
    return;
  }
  const answer = result.answer_result;
  const content = el("p", { class: "answer-text" });
  if (answer.status === "grounding_failed") {
    content.textContent = "Generated text was rejected by grounding validation.";
  } else if (!answer.answer_text) {
    content.textContent = "No answer was generated.";
  } else {
    result.inline_answer_parts.forEach((part) => {
      if (part.kind === "citation") {
        content.append(el("button", { class: "citation-link", type: "button", "aria-label": `Inspect citation ${part.label}`,
          onclick: () => openCitation(part.label) }, part.text));
      } else content.append(document.createTextNode(part.text));
    });
  }
  const children = [el("h3", {}, "Canonical AnswerResult"), content];
  const warnings = [...(answer.warnings || [])];
  if (answer.failure) warnings.unshift(`${human(answer.failure.code)}: ${answer.failure.message}`);
  if (warnings.length) children.push(el("div", { class: "limitations" }, el("strong", {}, "Warnings and limitations"),
    el("ul", {}, warnings.map((warning) => el("li", {}, warning)))));
  replace(panel, children);
}

function evidenceSearchText(item) {
  return [item.evidence_text, item.source_id, item.document.document_id,
    ...(item.document.municipality_ids || [])].join(" ").toLowerCase();
}

function evidenceCard(item) {
  const label = item.citation?.label || `R${item.rank}`;
  const kind = item.evidence_kind === "derived_graph_assertion" ? "Source + derived assertions" : "Authoritative source";
  return el("button", { class: "evidence-card", type: "button", "data-search": evidenceSearchText(item),
    onclick: () => openEvidence(item), "aria-label": `Inspect ${label}, rank ${item.rank}` },
    el("span", { class: "evidence-label" }, label),
    el("span", { class: "evidence-body" },
      el("strong", {}, item.document.heading_context || item.document.document_id),
      el("p", {}, item.evidence_text), el("span", { class: "kind-tag" }, kind)),
    el("span", { class: "evidence-meta" }, `rank ${item.rank}\n${item.retrieval_score.toFixed(5)}\n${item.source_type}`));
}

function renderEvidence(result) {
  $("#evidence-count").textContent = `${result.evidence.length} items`;
  replace($("#evidence-list"), result.evidence.length ? result.evidence.map(evidenceCard)
    : el("p", { class: "empty-state" }, "No canonical evidence was returned."));
}

function metadataList(entries) {
  const list = el("dl", { class: "metadata-list" });
  entries.forEach(([term, value]) => list.append(el("dt", {}, term), el("dd", {}, value ?? "—")));
  return list;
}

function openEvidence(item) {
  const citation = item.citation;
  $("#evidence-dialog-title").textContent = citation ? `${citation.label} · ${item.source_id}` : item.source_id;
  const sourceValue = item.document.source_url
    ? el("a", { class: "source-link", href: item.document.source_url, target: "_blank", rel: "noopener noreferrer" }, item.document.source_url)
    : "No canonical URL";
  const metadata = metadataList([
    ["Citation identity", citation?.citation_id], ["Evidence identity", item.evidence_id],
    ["Source type", item.source_type], ["Source identity", item.source_id],
    ["Document", item.document.document_id], ["Segment", item.segment_id],
    ["Chunk", item.chunk_id], ["Municipalities", item.document.municipality_ids.join(", ")],
    ["Retrieval", `${item.retrieval_traces.map((trace) => trace.method).join(" + ")} · rank ${item.rank} · ${item.retrieval_score}`],
    ["Extraction runs", item.extraction_run_ids.join(", ") || "—"],
    ["Graph artifacts", item.graph_artifact_ids.join(", ") || "—"],
    ["Snapshot", item.database_snapshot_id], ["Canonical source", sourceValue],
  ]);
  const assertions = item.assertions.length ? el("section", {}, el("h3", {}, "Derived graph assertions"),
    el("p", { class: "limitations" }, "Assertions are extracted, derived claims. Inspect their source evidence before use."),
    item.assertions.map((assertion) => el("div", { class: "assertion" },
      el("strong", {}, `${assertion.subject_name} —${assertion.relation_type}→ ${assertion.object_name}`),
      el("p", {}, assertion.evidence_excerpt || "No assertion excerpt."),
      el("code", {}, assertion.assertion_id)))) : el("p", {}, "No derived graph assertion is attached to this evidence.");
  replace($("#evidence-detail"), el("h3", {}, "Authoritative source evidence"), el("p", { class: "excerpt" }, item.evidence_text), metadata, assertions);
  $("#evidence-dialog").showModal();
}

function renderProvenance(result) {
  const retrieval = result.retrieval_result;
  const answer = result.answer_result;
  const grounding = !answer ? "not run"
    : answer.status === "answered" ? "structural grounding accepted"
      : answer.status === "grounding_failed" ? "grounding validation failed"
        : answer.status === "insufficient_evidence" ? "not required — insufficient evidence"
          : "not performed — generation failed";
  const extractionRuns = [...new Set(result.evidence.flatMap((item) => item.extraction_run_ids))];
  const entries = [
    ["Database snapshot", retrieval.database_snapshot_id], ["Retrieval mode", retrieval.mode],
    ["Embedding lineage", retrieval.embedding_artifact_id], ["Projection", retrieval.projection_id],
    ["Extraction runs", extractionRuns.join(", ") || "—"],
    ["Provider / model", answer ? `${answer.generation.provider} / ${answer.generation.model}` : "Generation disabled"],
    ["Prompt version", answer?.generation.prompt_version],
    ["Generation parameters", answer ? JSON.stringify(answer.generation.parameters) : "—"],
    ["Grounding", grounding],
    ["Provider acceptance gate", result.provider_gate.acceptance_gate],
  ];
  replace($("#provenance-panel"), metadataList(entries),
    el("p", { class: "limitations" }, result.provider_gate.message));
}

function renderTrace(result) {
  const items = result.evidence.map((item) => {
    const trace = {
      final_rank: item.rank, final_score: item.retrieval_score,
      retrieval_traces: item.retrieval_traces,
      deduplication_identity: item.evidence_id,
      source_identity: `${item.source_type}:${item.source_id}`,
      snapshot_identity: item.database_snapshot_id,
      projection_identity: result.retrieval_result.projection_id,
      extraction_run_scope: item.extraction_run_ids,
    };
    return el("details", { class: "trace-item" },
      el("summary", {}, `${item.citation?.label || `R${item.rank}`} · final rank ${item.rank}`),
      el("pre", {}, JSON.stringify(trace, null, 2)));
  });
  replace($("#trace-panel"), items.length ? items : el("p", {}, "No retrieval trace is available."));
}

function renderGraph(result) {
  const graph = result.graph;
  $("#graph-panel .graph-notice").textContent = graph.notice;
  const canvas = $("#graph-canvas");
  if (!graph.nodes.length) { replace(canvas, el("p", { class: "empty-state" }, "No graph context was retrieved.")); return; }
  const width = 620, height = Math.max(320, graph.nodes.length * 55);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Canonical graph neighborhood");
  const positions = new Map();
  graph.nodes.forEach((node, index) => {
    const column = node.kind === "entity" ? 0 : node.kind === "evidence" ? 1 : 2;
    const peers = graph.nodes.filter((candidate) => candidate.kind === node.kind);
    const peerIndex = peers.findIndex((candidate) => candidate.id === node.id);
    positions.set(node.id, { x: 100 + column * 205, y: 65 + peerIndex * 105 });
  });
  graph.edges.forEach((edge) => {
    const from = positions.get(edge.source), to = positions.get(edge.target);
    if (!from || !to) return;
    const line = document.createElementNS(svg.namespaceURI, "line");
    line.setAttribute("x1", from.x); line.setAttribute("y1", from.y);
    line.setAttribute("x2", to.x); line.setAttribute("y2", to.y);
    line.setAttribute("class", `graph-edge ${edge.kind}`);
    line.setAttribute("tabindex", "0"); line.setAttribute("role", "button");
    line.setAttribute("aria-label", `${edge.kind} ${edge.label || "evidence link"}`);
    const select = () => selectGraphEdge(edge);
    line.addEventListener("click", select);
    line.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) select(); });
    svg.append(line);
  });
  graph.nodes.forEach((node) => {
    const position = positions.get(node.id);
    const group = document.createElementNS(svg.namespaceURI, "g");
    group.setAttribute("class", `graph-node ${node.kind}`); group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button"); group.setAttribute("aria-label", `${node.kind}: ${node.label}`);
    const circle = document.createElementNS(svg.namespaceURI, "circle");
    circle.setAttribute("cx", position.x); circle.setAttribute("cy", position.y); circle.setAttribute("r", "25");
    const text = document.createElementNS(svg.namespaceURI, "text");
    text.setAttribute("x", position.x); text.setAttribute("y", position.y + 40);
    text.setAttribute("text-anchor", "middle"); text.setAttribute("font-size", "11");
    text.textContent = node.label.length > 25 ? `${node.label.slice(0, 23)}…` : node.label;
    const select = () => selectGraphNode(node);
    group.addEventListener("click", select);
    group.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) select(); });
    group.append(circle, text); svg.append(group);
  });
  const graphControls = el("div", { class: "graph-controls", "aria-label": "Graph relationships" },
    graph.edges.map((edge) => el("button", { class: "secondary-button", type: "button",
      onclick: () => selectGraphEdge(edge) }, edge.kind === "assertion" ? `${edge.label} · assertion` : "Evidence → document")));
  replace(canvas, svg, graphControls);
}

function selectGraphEdge(edge) {
  const box = $("#graph-selection");
  const title = edge.kind === "assertion" ? `${edge.label} · derived assertion` : "Evidence → document link";
  const button = el("button", { class: "secondary-button", type: "button", onclick: () => {
    const item = state.result.evidence.find((candidate) => edge.evidence_ids.includes(candidate.evidence_id));
    if (item) openEvidence(item);
  } }, "Inspect supporting evidence");
  replace(box, el("strong", {}, title), el("p", {}, edge.evidence_excerpt || shortId(edge.id)), button);
}

function selectGraphNode(node) {
  const box = $("#graph-selection");
  const children = [el("strong", {}, `${human(node.kind)} · ${node.label}`), el("p", {}, shortId(node.id))];
  if (node.municipality_ids?.length) children.push(el("p", {}, `Municipalities: ${node.municipality_ids.join(", ")}`));
  if (node.source_url) children.push(el("a", { class: "source-link", href: node.source_url,
    target: "_blank", rel: "noopener noreferrer" }, "Open canonical source"));
  if (node.evidence_id) children.push(el("button", { class: "secondary-button", type: "button", onclick: () => {
    const item = state.result.evidence.find((candidate) => candidate.evidence_id === node.evidence_id);
    if (item) openEvidence(item);
  } }, "Inspect evidence"));
  replace(box, children);
}

function renderExports(result) {
  replace($("#export-actions"),
    el("a", { class: "secondary-button", href: `/api/export/${result.result_id}.md`, download: "graphrag-result.md" }, "Markdown"),
    el("a", { class: "secondary-button", href: `/api/export/${result.result_id}.json`, download: "graphrag-result.json" }, "JSON"));
}

function renderResult(result) {
  state.result = result;
  $("#empty-state").hidden = true; $("#research-result").hidden = false;
  renderStatus(result); renderAnswer(result); renderEvidence(result); renderProvenance(result);
  renderTrace(result); renderGraph(result); renderExports(result);
  $("#research-result").focus();
}

async function executeQuery(event) {
  event.preventDefault();
  const button = $(".execute"); button.disabled = true; button.querySelector("span").textContent = "Running…";
  const payload = {
    question: $("#question").value.trim(), mode: $("#mode").value,
    top_k: Number($("#top-k").value), max_evidence_items: Number($("#max-items").value),
    max_evidence_characters: Number($("#max-characters").value), generate_answer: $("#generate-answer").checked,
  };
  try {
    const response = await fetch("/api/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "Research request failed safely.");
    renderResult(data);
  } catch (error) {
    $("#empty-state").hidden = true; $("#research-result").hidden = false;
    const box = $("#result-status"); box.dataset.tone = "danger";
    replace(box, el("strong", {}, "Request failed safely"), el("span", {}, error.message));
    replace($("#answer-panel"), el("p", {}, "No partial or unvalidated result is displayed."));
    replace($("#evidence-list"));
  } finally { button.disabled = false; button.querySelector("span").textContent = "Execute"; }
}

$("#query-form").addEventListener("submit", executeQuery);
$("#question").addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") $("#query-form").requestSubmit(); });
$("#close-dialog").addEventListener("click", () => $("#evidence-dialog").close());
$("#evidence-filter").addEventListener("input", (event) => {
  const query = event.target.value.toLowerCase();
  document.querySelectorAll(".evidence-card").forEach((card) => { card.hidden = !card.dataset.search.includes(query); });
});
document.querySelectorAll('[role="tab"]').forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll('[role="tab"]').forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
  document.querySelectorAll('[role="tabpanel"]').forEach((panel) => { panel.hidden = panel.id !== tab.getAttribute("aria-controls"); });
}));
loadStatus();
