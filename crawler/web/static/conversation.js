/* Source text is always textContent, never interpreted as DOM or instructions. */
"use strict";
const $ = (id) => document.getElementById(id),
  el = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    return n;
  };
let workspaceGraph = null;
let csrf = "",
  cid = null,
  turns = [],
  controller = null,
  epoch = 0,
  graph = null;
const labels = {
  ANSWERED: "Belegte Quellenantwort",
  PARTIAL: "Teilantwort · offene Aspekte",
  INSUFFICIENT_EVIDENCE: "Evidenzlücke",
  CLARIFICATION_REQUIRED: "Rückfrage nötig",
  ERROR: "Kontrollierter Fehler",
};
async function api(path, method = "GET", body, signal) {
  const r = await fetch("/api/chat/" + path, {
    method,
    signal,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) throw Error(d.detail || d.error || "Anfrage nicht verfügbar");
  return d;
}
function message(text) {
  $("status").textContent = text;
}
function download(name, value) {
  const u = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    ),
    a = el("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 500);
}
function action(text, run) {
  const b = el("button", text);
  b.type = "button";
  b.addEventListener("click", () =>
    Promise.resolve()
      .then(run)
      .catch((e) => message(e.message)),
  );
  return b;
}
async function list() {
  const chats = await api("conversations");
  $("chats").replaceChildren(
    ...chats.map((c, i) => {
      const li = el("li");
      li.append(action(c.title || "Unterhaltung " + (chats.length - i), () => open(c.id)));
      return li;
    }),
  );
}
async function open(id) {
  epoch++;
  controller?.abort();
  cid = id;
  const version = epoch;
  const history = await api("conversations/" + id);
  if (version !== epoch) return;
  turns = history;
  render();
  if (location.pathname !== "/" && location.pathname !== "/research") await workspace();
  $("question").focus();
}
async function create() {
  epoch++;
  controller?.abort();
  const c = await api("conversations", "POST");
  cid = c.id;
  turns = [];
  render();
  await list();
  if (document.body.classList.contains("workbench")) await workspace();
  $("question").focus();
}
function render() {
  const target = $("turns");
  target.replaceChildren();
  for (const t of turns) {
    const article = el("article");
    article.append(
      el("p", t.question),
      el("span", labels[t.answer_status] || t.answer_status),
    );
    article.firstChild.className = "question";
    article.children[1].className = "badge";
    if (!t.claims.length) article.append(el("p", t.answer));
    for (const c of t.claims) {
      const p = el("div");
      const markdown = window.markdownit?.({html:false, linkify:false, typographer:false});
      if (markdown) {
        // HTML disabled; library validates dangerous URL protocols. Sources stay textContent.
        markdown.disable("image");
        p.innerHTML = markdown.render(c.text);
        p.querySelectorAll("a").forEach(a => a.rel = "noreferrer noopener");
      } else p.textContent = c.text;
      p.className = "claim";
      for (const e of c.evidence) {
        const b = action("Beleg prüfen ↗", () => inspect(t, e));
        b.className = "citation";
        p.append(b);
      }
      article.append(p);
    }
    for (const m of t.missing_aspects) article.append(el("p", "Offen: " + m));
    article.append(
      el(
        "p",
        "Geltungsbereich: " +
          (t.context_scope.municipality_ids.join(", ") || "nicht festgelegt") +
          " · " +
          (t.context_scope.reference_year || "kein Bezugsjahr") +
          (t.inherited_scope ? " · aus vorheriger Frage übernommen" : ""),
      ),
    );
    const tools = el("div");
    tools.className = "tools";
    tools.append(
      action("Kopieren", () => navigator.clipboard.writeText(t.answer)),
      action("Export", () => download("research-turn.json", t)),
      action("Graph öffnen", () => location.assign(`/graph?chat=${cid}&turn=${t.turn_id}`)),
      action("Diagramm / Daten", () => location.assign(`/visualizations?chat=${cid}&turn=${t.turn_id}`)),
      action("Bearbeiten / neue Version", () => {
        $("question").value = t.question;
        $("question").dataset.parent = t.turn_id;
        $("question").focus();
        message("Neue abgeleitete Version; alter Turn bleibt erhalten.");
      }),
      action("Erneut versuchen", () => send(t.question, t.turn_id)),
      action("Unklar melden", () =>
        api(
          `conversations/${cid}/feedback/${t.turn_id}`,
          "POST",
          "unclear",
        ).then(() => message("Produktfeedback gespeichert; kein Human Gold.")),
      ),
    );
    const trace = el("details");
    trace.append(el("summary", "Research-Trace und Provenienz"));
    trace.append(
      el(
        "p",
        "Snapshot: " +
          t.dataset_snapshot +
          " · " +
          t.generation_provider +
          " · " +
          t.model_version,
      ),
      action("Maschinenlesbaren Trace herunterladen", () =>
        download("research-trace.json", t.trace),
      ),
    );
    if (t.query_plan) trace.append(el("p", `Query Plan: ${t.query_plan.intent} · ${t.query_plan.method} · ${t.query_plan.selection}`));
    trace.append(el("p", `Prompt: ${t.prompt_version} · Evidenzbudget: 8 Elemente / 24000 Zeichen · Claimprüfung: technische Spannen-/Scopeprüfung; kein semantischer Wahrheitsnachweis.`));
    if (t.claims.some(c => c.evidence_kind === "PLAN")) tools.append(action("Welche wurden bereits umgesetzt?", () => send("Welche wurden bereits umgesetzt?", t.turn_id)));
    article.append(tools, trace);
    target.append(article);
  }
  target.lastElementChild?.scrollIntoView({ block: "nearest" });
}
async function inspect(t, e) {
  const activeChat = cid, version = epoch;
  const current = await api(
    `conversations/${cid}/evidence/${t.turn_id}/${encodeURIComponent(e.evidence_id)}`,
  );
  if (cid !== activeChat || version !== epoch) return;
  $("quote").textContent = current.quote;
  $("evidence-notice").textContent =
    "Extrahierte Passage, nicht unabhängig fachlich validiert. Original-Bodyhash/Seite nur anzeigen, wenn tatsächlich vorhanden.";
  $("provenance").replaceChildren();
  for (const [k, v] of Object.entries({
    Beleg: current.evidence_id,
    Dokument: current.document_id || "NOT_KNOWN",
    Unit: current.segment_id || "NOT_KNOWN",
    Chunk: current.chunk_id || "NOT_AVAILABLE",
    Extraktionsläufe: (current.extraction_run_ids || []).join(", ") || "NOT_KNOWN",
    Passagenhash: current.passage_sha256,
    Originalhash: current.source_sha256 || "NOT_KNOWN",
    Fundstelle: current.locator_kind,
    Gemeinde: current.municipality_ids.join(", "),
  }))
    $("provenance").append(el("dt", k), el("dd", v));
  $("graph-accessible").replaceChildren();
  const g = t.graph || { nodes: [], edges: [] };
  $("graph-accessible").append(
    el(
      "p",
      `Begrenzter Antwortgraph: ${g.nodes.length} Knoten, ${g.edges.length} Kanten. Keine Vollständigkeitsbehauptung.`,
    ),
  );
  for (const edge of g.edges.slice(0, 24))
    $("graph-accessible").append(
      action(
        (edge.label || edge.relation_type || edge.kind || "Evidenzbindung") +
          " · EXTRACTED_UNVERIFIED",
        () => {
          const anchor = t.claims
            .flatMap((c) => c.evidence)
            .find((a) => (edge.evidence_ids || []).includes(a.evidence_id));
          if (anchor) return inspect(t, anchor);
        },
      ),
    );
  if (!$("evidence-dialog").open) $("evidence-dialog").showModal();
  if (graph) graph.destroy();
  if (window.cytoscape) {
    graph = cytoscape({
      container: $("graph-canvas"),
      elements: [
        ...g.nodes.slice(0, 32).map((n) => ({ data: { ...n, id: n.id } })),
        ...g.edges.slice(0, 24).map((n) => ({ data: { ...n, id: n.id } })),
      ],
      layout: { name: "circle" },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "#165941",
            "font-size": 10,
          },
        },
        {
          selector: "edge",
          style: {
            "line-color": "#879b8b",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
      ],
    });
    graph.on("tap", "edge", () =>
      message("Originalgebundene Belegstelle oben; Kantenstatus unverified."),
    );
  }
}
async function send(question, parent) {
  if (controller) return;
  if (!cid) await create();
  const id = cid,
    version = ++epoch;
  controller = new AbortController();
  $("send").disabled = true;
  $("cancel").hidden = false;
  message("Belege werden abgerufen und qualifiziert …");
  try {
    const d = await api(
      `conversations/${id}/turns`,
      "POST",
      {
        question,
        parent_turn_id: parent || null,
        municipality_ids: $("scope")
          .value.split(",")
          .map((x) => x.trim())
          .filter(Boolean),
        reference_year: Number($("year").value) || null,
        mode: $("method").value || null,
      },
      controller.signal,
    );
    if (version !== epoch) return;
    turns.push(d);
    render();
    $("question").value = "";
    delete $("question").dataset.parent;
    message(
      "Quellengebundene Antwort abgeschlossen · kein Streaming, keine Humanvalidierung.",
    );
  } catch (e) {
    message(
      e.name === "AbortError"
        ? "Anzeige abgebrochen. Der Server kann den Turn noch abschließen; Verlauf später neu laden."
        : e.message,
    );
  } finally {
    controller = null;
    $("send").disabled = false;
    $("cancel").hidden = true;
  }
}
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send(
    $("question").value,
    $("question").dataset.parent || turns.at(-1)?.turn_id,
  );
});
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
$("new-chat").addEventListener("click", () =>
  create().catch((e) => message(e.message)),
);
$("cancel").addEventListener("click", () => {
  epoch++;
  controller?.abort();
});
$("close-evidence").addEventListener("click", () =>
  $("evidence-dialog").close(),
);
$("delete-chat").addEventListener("click", async () => {
  if (
    cid &&
    confirm(
      "Diese lokale Unterhaltung und ihr Produktfeedback löschen? Unabhängige Review-Audits bleiben getrennt.",
    )
  ) {
    const deletedChat = cid;
    epoch++; controller?.abort();
    await api("conversations/" + deletedChat, "DELETE");
    if (cid !== deletedChat) return;
    cid = null;
    turns = [];
    render();
    await list();
    if (document.body.classList.contains("workbench")) await workspace();
  }
});
document.querySelectorAll("[data-question]").forEach((b) =>
  b.addEventListener("click", () => {
    $("question").value = b.dataset.question;
    $("question").focus();
  }),
);
api("session")
  .then(async (s) => {
    csrf = s.csrf;
    $("dataset-notice").textContent = s.fixture
      ? "Synthetische Forschungsumgebung · keine echten kommunalen Quellen."
      : s.notice || "Gebundener lokaler Bestand";
    $("snapshot").textContent = s.snapshot;
    $("provider-notice").textContent = "Aktive Antwortmethode: " + s.provider + ". Qualifikation und Grenzen unter Settings. Keine Qualitätsrangfolge aus Trefferzahlen.";
    if (location.pathname === "/research") {
      $("expert").open = true;
      document
        .querySelector("nav [aria-current]")
        .removeAttribute("aria-current");
      document
        .querySelector('nav a[href="/research"]')
        .setAttribute("aria-current", "page");
    }
    await list();
    if (!["/", "/research"].includes(location.pathname)) {
      document.body.classList.add("workbench");
      document.querySelector("nav [aria-current]")?.removeAttribute("aria-current");
      document.querySelector(`nav a[href="${location.pathname}"]`)?.setAttribute("aria-current", "page");
      const requested = new URLSearchParams(location.search).get("chat");
      const chats = await api("conversations");
      if (requested || chats.length) await open(requested || chats[0].id);
      else await workspace();
    }
  })
  .catch((e) => message(e.message));

let searchEpoch = 0;
$("chat-search").addEventListener("input", async () => {
  const version = ++searchEpoch;
  try {
    const chats = await api("conversations?q=" + encodeURIComponent($("chat-search").value));
    if(version !== searchEpoch) return;
    $("chats").replaceChildren(...chats.map(c => {
      const li = el("li"); li.append(action(c.title || c.at, () => open(c.id))); return li;
    }));
  } catch(e) { message(e.message); }
});
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault(); $("question").focus();
  }
});
function dataTable(headers, rows) {
  const table = el("table"), head = el("thead"), tr = el("tr"), body = el("tbody");
  for (const name of headers) { const th = el("th", name); th.scope = "col"; tr.append(th); }
  head.append(tr); table.append(head, body);
  for (const row of rows) {
    const line = el("tr");
    for (const value of row) {
      const td = el("td"); td.append(value instanceof Node ? value : document.createTextNode(String(value ?? "UNKNOWN"))); line.append(td);
    }
    body.append(line);
  }
  return table;
}
async function workspace() {
  const path = location.pathname, workspaceVersion = epoch, workspaceChat = cid;
  if (workspaceGraph) workspaceGraph.destroy(); workspaceGraph = null;
  $("workspace-graph").replaceChildren();
  const content = $("workspace-content"), controls = $("workspace-controls");
  content.replaceChildren(); controls.replaceChildren();
  $("workspace-panel").hidden = false;
  const wanted = new URLSearchParams(location.search).get("turn");
  const t = turns.find(t => t.turn_id === wanted) || turns.at(-1);
  $("workspace-notice").textContent = "Gebundener Antwortstand; keine unabhängig geprüfte Vollständigkeit.";
  if (path === "/settings") {
    $("workspace-title").textContent = "Aktive Methoden und Modelle";
    const config = await api("settings");
    if (workspaceVersion !== epoch || workspaceChat !== cid) return;
    const names = {answer_generation:"Antwortmodell", information_extraction:"Information Extraction und aktiver Index", retrieval_ranking:"Retrieval und Ranking", verification_evaluation:"Verifikation und Evaluation"};
    content.append(el("p", "Snapshot: " + config.snapshot));
    for (const [key, title] of Object.entries(names)) {
      const section = el("section"); section.append(el("h3", title));
      section.append(dataTable(["Einstellung", "Aktiver Wert"], Object.entries(config[key]).map(([field, value]) => [field, value === null ? "Nicht bekannt" : typeof value === "object" ? JSON.stringify(value) : String(value)])));
      content.append(section);
    }
    content.append(el("p", "Modelländerungen sind erst nach expliziter Qualifikation verfügbar. Derzeit keine interaktive Modellfreigabe; Konfiguration über den dokumentierten lokalen Providervertrag."));
    content.append(el("p", "IE-Modellwechsel erfordern einen separaten qualifizierten Lauf. Bestehende Indizes werden niemals durch diese Ansicht verändert."));
    return;
  }
  if (path === "/coverage") {
    $("workspace-title").textContent = "Datenabdeckung";
    const coverageConfig = await api("settings");
    if (workspaceVersion !== epoch || workspaceChat !== cid) return;
    content.append(el("p", coverageConfig.fixture ? "Gemeinden mit Text sind nicht automatisch fachlich abgedeckt. Der aktive synthetische Bestand erlaubt keine Aussage über Bayern oder München-Hitzevorsorge." : "Gemeinden mit Text sind nicht automatisch fachlich abgedeckt. Für diesen Snapshot ist keine aktuelle qualifizierte Coverage-Auswertung eingebunden."));
    content.append(dataTable(["Dimension", "Aktiver Bestand"], [
      ["Gemeinderahmen", "NOT_AVAILABLE"], ["Beschaffung versucht", "NOT_AVAILABLE"],
      ["Autoritative Quellen", "NOT_QUALIFIED"], ["Originale", coverageConfig.fixture ? "SYNTHETIC_ONLY" : "NOT_AUDITED"],
      ["Parsing", coverageConfig.fixture ? "CONTROLLED_FIXTURE" : "SEE_MANIFEST"], ["Index", coverageConfig.fixture ? "CONTROLLED_FIXTURE" : "SEE_MANIFEST"], ["Themenbelege", "NOT_HUMAN_QUALIFIED"],
      ["Claim-Scope", "TECHNICAL_ONLY"], ["Human Reviewed", "NOT_ESTABLISHED"],
      ["Antwortbarkeit auditiert", "NOT_EVALUATED"]]));
    return;
  }
  $("workspace-title").textContent = path === "/graph" ? "Knowledge Graph" : "Visualizations";
  if (!t) { content.append(el("p", "Noch keine Antwort vorhanden. Stellen Sie im Chat eine Frage und öffnen Sie deren Graph oder Daten.")); return; }
  if (path === "/visualizations") {
    const spec = await api(`conversations/${cid}/visualization/${t.turn_id}`);
    if (workspaceVersion !== epoch || workspaceChat !== cid) return;
    content.append(el("h3", spec.title), el("p", `${spec.status} · Snapshot ${spec.snapshot} · Zeitraum ${spec.period} · Aggregation ${spec.aggregation}`));
    controls.append(action("Datensatz exportieren", () => download("visualization.json", spec)));
    if (spec.chart_allowed) {
      const row = spec.rows[0], chart = el("figure"), bar = el("div");
      bar.className = "observation-bar"; bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", "Einzelwert: " + row.original_value);
      chart.append(bar, el("figcaption", `${row.original_value} · ${row.modality} · Einzelbeobachtung, keine Vergleichsskala`)); content.append(chart);
    }
    content.append(dataTable(["Wert", "Einheit", "Modalität", "Gemeinde", "Beleg"], spec.rows.map(row => [
      row.value, row.unit, row.modality, row.municipality_ids.join(", "),
      action("Originalspanne", () => inspect(t, t.claims.flatMap(c => c.evidence).find(e => e.evidence_id === row.evidence_id)))
    ])));
    if (!spec.rows.length) content.append(el("p", "Keine sicher typisierten Zahlen in den qualifizierten Passagen. Keine Zeitreihe erzeugt."));
    spec.caveats.forEach(c => content.append(el("p", c)));
    return;
  }
  const g = t.graph || {nodes: [], edges: []};
  const search = el("input"); search.placeholder = "Knoten suchen"; search.setAttribute("aria-label", "Knoten suchen");
  const relation = el("select"); relation.setAttribute("aria-label", "Relationstyp");
  for (const name of ["Alle Relationen", ...new Set(g.edges.map(e => e.label || e.kind))]) relation.append(el("option", name));
  const layout = el("select"); layout.setAttribute("aria-label", "Graphlayout");
  for (const name of ["circle", "grid", "breadthfirst"]) layout.append(el("option", name));
  controls.append(search, relation, layout, action("Graph exportieren", () => download("answer-graph.json", g)));
  const redraw = () => {
    const matching = new Set(g.nodes.filter(n => String(n.label || n.id).toLocaleLowerCase().includes(search.value.toLocaleLowerCase())).map(n => n.id));
    const edges = g.edges.filter(e => (matching.has(e.source) || matching.has(e.target)) && (relation.selectedIndex === 0 || (e.label || e.kind) === relation.value));
    const ids = new Set(edges.flatMap(e => [e.source, e.target]));
    const nodes = g.nodes.filter(n => ids.has(n.id));
    if (workspaceGraph) workspaceGraph.destroy(); workspaceGraph = null;
    if (window.cytoscape && nodes.length) workspaceGraph = cytoscape({container: $("workspace-graph"),
      elements: [...nodes.map(n => ({data:n})), ...edges.map(e => ({data:e}))],
      layout: {name:layout.value}, style:[{selector:"node",style:{label:"data(label)","background-color":"#165941","font-size":12}},{selector:"edge",style:{"target-arrow-shape":"triangle","curve-style":"bezier","line-color":"#879b8b"}}]});
    if (workspaceGraph && nodes.length) workspaceGraph.on("tap", "edge", event => {
      const edge = event.target.data();
      const anchor = t.claims.flatMap(c=>c.evidence).find(a => (edge.evidence_ids || []).includes(a.evidence_id));
      if (anchor) inspect(t, anchor).catch(e => message(e.message));
    });
    content.replaceChildren(el("p", `${nodes.length} Knoten · ${edges.length} Kanten · ${g.cutoff ? "Cutoff aktiv" : "nur dieser Antwortstand"}. Pan/Zoom im Graph. Keine Kausalitätsinferenz.`));
    content.append(dataTable(["Von", "Relation", "Nach", "Status", "Beleg"], edges.map(e => [
      nodes.find(n=>n.id===e.source)?.label || e.source, e.label || e.kind,
      nodes.find(n=>n.id===e.target)?.label || e.target, e.validation_status,
      action("Beleg prüfen", () => {
        const anchor = t.claims.flatMap(c=>c.evidence).find(a=>(e.evidence_ids||[]).includes(a.evidence_id));
        if(anchor) return inspect(t, anchor);
      })
    ])));
  };
  search.addEventListener("input", redraw); relation.addEventListener("change", redraw); layout.addEventListener("change", redraw); redraw();
}
