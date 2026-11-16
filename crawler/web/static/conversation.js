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
  ANSWERED: "Quellenbaseline verfügbar",
  PARTIAL: "Teilantwort · offene Aspekte",
  INSUFFICIENT_EVIDENCE: "Evidenzlücke",
  CLARIFICATION_REQUIRED: "Rückfrage nötig",
  ERROR: "Kontrollierter Fehler",
};
let recovery = null;
async function renewSession() {
  if (!recovery) recovery = (async () => {
    const r = await fetch("/api/chat/session", {cache: "no-store"});
    if (!r.ok) throw Error("Die lokale Sitzung ist derzeit nicht verfügbar. Bitte erneut versuchen.");
    const session = await r.json();
    session.recovered = session.recovered || (session.created && Boolean(csrf));
    csrf = session.csrf;
    if (session.recovered) {
      epoch++; cid = null; turns = []; render();
      $("main").classList.remove("comparing"); $("comparison-panel").hidden = true;
      $("comparison-results").replaceChildren();
      $("workspace-content").replaceChildren(); $("workspace-controls").replaceChildren();
      if (workspaceGraph) workspaceGraph.destroy(); workspaceGraph = null;
      $("chats").replaceChildren();
      message("Neue lokale Sitzung gestartet. Der abgelaufene Verlauf wurde gemäß Aufbewahrungsregel gelöscht.");
    }
    return session;
  })().finally(() => { recovery = null; });
  return recovery;
}
async function api(path, method = "GET", body, signal, retried = false) {
  const r = await fetch("/api/chat/" + path, {
    method, signal,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok && !retried && (r.status === 401 ||
      (r.status === 403 && d.error === "csrf_or_session_required"))) {
    const session = await renewSession();
    if (session.recovered && path.startsWith("conversations/")) {
      const error = Error("Die Sitzung wurde erneuert. Eine neue Unterhaltung ist bereit.");
      error.sessionRenewed = true; throw error;
    }
    return api(path, method, body, signal, true);
  }
  if (!r.ok) throw Error(r.status === 429
    ? "Die Recherche ist noch beschäftigt. Bitte gleich erneut versuchen."
    : r.status === 404 || r.status === 410
      ? "Dieser lokale Verlauf ist nicht mehr verfügbar. Bitte eine neue Unterhaltung starten."
      : "Die Anfrage konnte nicht abgeschlossen werden. Bitte Eingabe und verfügbare Methode prüfen.");
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
  if (!["/", "/research", "/compare"].includes(location.pathname)) await workspace();
  if (location.pathname === "/compare") await restoreComparison();
  else { $("main").classList.remove("comparing"); $("comparison-panel").hidden = true; }
  $("question").focus();
}
async function create() {
  epoch++;
  controller?.abort();
  const c = await api("conversations", "POST");
  cid = c.id;
  turns = [];
  $("main").classList.remove("comparing"); $("comparison-panel").hidden = true;
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
    article.append(el("p", `${t.generation_label || "Extraktive Baseline"} · ${t.query_plan?.method || "unbekannt"} · UNVALIDATED · ${Number(t.elapsed_seconds || 0).toFixed(3)} s`));
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
  if (current.source_sha256) {
    const archive = el("a", "Archiviertes Original öffnen");
    archive.href = `/api/chat/conversations/${cid}/original/${t.turn_id}/${encodeURIComponent(e.evidence_id)}`;
    archive.target = "_blank"; archive.rel = "noreferrer noopener";
    $("provenance").append(el("dt", "Originalfassung"), el("dd")); $("provenance").lastChild.append(archive);
  }
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
async function send(question, parent, retried = false) {
  if (controller) return;
  $("main").classList.remove("comparing"); $("comparison-panel").hidden = true;
  if (!cid) await create();
  const id = cid,
    version = ++epoch;
  controller = new AbortController();
  $("send").disabled = true;
  $("cancel").hidden = false;
  message("Belege werden abgerufen und qualifiziert …");
  let retryRecovered = false;
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
    retryRecovered = e.sessionRenewed === true;
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
  if (retryRecovered && !retried) await send(question, null, true);
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
      "Diese lokale Unterhaltung und ihr Produktfeedback löschen? Lokale Notizen und Vergleichsbelege bleiben separat gespeichert.",
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
renewSession()
  .then(async (s) => {
    csrf = s.csrf;
    $("dataset-notice").textContent = s.fixture
      ? "Synthetische Forschungsumgebung · keine echten kommunalen Quellen."
      : s.notice || "Gebundener lokaler Bestand";
    if (!s.fixture) {
      $("question").placeholder = "Wer wurde mit der Wärmeplanung in Nennslingen beauftragt?";
      const starters = document.querySelectorAll("[data-question]");
      if (starters[0]) { starters[0].dataset.question = $("question").placeholder; starters[0].textContent = "Reale Wärmeplanung untersuchen"; }
      if (starters[1]) { starters[1].dataset.question = "Welche Hitzeinformationen sind in München dokumentiert?"; starters[1].textContent = "Hitzeinformationen München recherchieren"; }
    }
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
    await loadMethods();
    await list();
    if (["/", "/research", "/compare"].includes(location.pathname)) {
      const chats = await api("conversations");
      if (chats.length) await open(chats[0].id);
    }
    if (!["/", "/research", "/compare"].includes(location.pathname)) {
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
  if (path === "/experiments") {
    $("workspace-title").textContent = "Experimente und Evaluationsgrenzen";
    const overview = await api("experiments");
    if (workspaceVersion !== epoch || workspaceChat !== cid) return;
    content.append(el("p", "UNVALIDATED · gemessene Ausführung ist keine fachliche Validierung. Historische Studien werden hier nicht zusammengeführt."));
    controls.append(action("Experimentübersicht exportieren", () => download("experiments.json", overview)));
    content.append(dataTable(["Versuch", "Snapshot", "Methoden", "Laufzeit", "Fehler"], overview.experiments.map(r=>[
      r.id, r.configuration.snapshot, r.order.join(", "),
      r.results.map(a=>a.method+": "+a.elapsed_seconds.toFixed(3)+" s").join(" · "),
      r.results.filter(a=>a.status==="ERROR").length
    ])));
    content.append(dataTable(["Metrik", "Wert", "Referenzstatus"],Object.entries(overview.quality_metrics).map(([name,m])=>[name,"Nicht ausgewertet",m.reason])));
    content.append(el("p", overview.timing_limits),el("p", "GraphRAG-Überlegenheit: nicht nachgewiesen. Gleiche SQL-/Graphrelationen bleiben ein begrenzender historischer Befund."));
    return;
  }
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
  const relationNames = {COMMISSIONED_FOR:"beauftragt für", FUNDS:"finanziert", AUTHORS:"erstellt", ADOPTED_ON:"beschlossen am", FUNDING_DURING:"Förderzeitraum", TARGETS:"Zielbezug", IMPLEMENTS:"setzt um"};
  const originalGraph = t.graph || {nodes: [], edges: []};
  const anchors = t.claims.flatMap(c => c.evidence);
  const anchorFor = edge => anchors.find(a => (edge.evidence_ids || []).includes(a.evidence_id));
  const g = { ...originalGraph,
    nodes: originalGraph.nodes.map((n, i) => ({...n, label: n.kind === "evidence" ? "Quellenpassage " + (i + 1) : n.label || "Entität " + (i + 1)})),
    edges: originalGraph.edges.filter(e => e.evidence_ids?.length && e.evidence_ids.every(id => anchors.some(a => a.evidence_id === id)))
      .map(e => ({...e, label: relationNames[e.label || e.relation_type] || e.label || e.relation_type || (e.kind === "evidence_link" ? "Quellenherkunft" : e.kind)}))
  };
  const search = el("input"); search.placeholder = "Knoten suchen"; search.setAttribute("aria-label", "Knoten suchen");
  const relation = el("select"); relation.setAttribute("aria-label", "Relationstyp");
  for (const name of ["Alle Relationen", ...new Set(g.edges.map(e => e.label || e.kind))]) relation.append(el("option", name));
  const layout = el("select"); layout.setAttribute("aria-label", "Graphlayout");
  for (const name of ["circle", "grid", "breadthfirst"]) layout.append(el("option", name));
  const focus = el("select"); focus.setAttribute("aria-label", "Nachbarschaft einer Entität"); focus.append(new Option("Gesamter Antwortgraph", ""));
  g.nodes.forEach(n => focus.append(new Option(n.label, n.id)));
  const depth = el("select"); depth.setAttribute("aria-label", "Nachbarschaftstiefe");
  [1,2,3].forEach(d => depth.append(new Option(d + " Beziehungsschritte", String(d))));
  const type = el("select"); type.setAttribute("aria-label", "Knotentyp"); type.append(new Option("Alle Knotentypen", ""));
  [...new Set(g.nodes.map(n => n.kind))].filter(Boolean).forEach(k => type.append(new Option(k,k)));
  const municipality = el("select"); municipality.setAttribute("aria-label", "Gemeindegrenze"); municipality.append(new Option("Alle belegten Gemeinden", ""));
  [...new Set(anchors.flatMap(a=>a.municipality_ids))].forEach(k=>municipality.append(new Option(k,k)));
  const year = el("select"); year.setAttribute("aria-label", "Jahresnennung im Beleg"); year.append(new Option("Alle Zeitbezüge", ""));
  [...new Set(anchors.flatMap(a=>a.quote.match(/\b(?:19|20)\d{2}\b/g)||[]))].sort().forEach(y=>year.append(new Option(y,y)));
  const filters = el("details"), filterTitle = el("summary", "Filter und Nachbarschaft");
  filters.append(filterTitle, relation, type, municipality, year, focus, depth);
  controls.append(search, layout, filters,
    action("Graph exportieren", () => download("answer-graph.json", {...g, active_filters:{search:search.value,relation:relation.value,type:type.value,municipality:municipality.value,year:year.value,root:focus.value,depth:depth.value}})),
    action("PNG exportieren", () => { if (!workspaceGraph) return; const u=URL.createObjectURL(workspaceGraph.png({output:"blob",bg:"#ffffff",full:true,maxWidth:2048,maxHeight:2048})); const a=el("a");a.href=u;a.download="answer-graph.png";a.click();setTimeout(()=>URL.revokeObjectURL(u),500); }));

  const redraw = () => {
    const matching = new Set(g.nodes.filter(n => String(n.label || n.id).toLocaleLowerCase().includes(search.value.toLocaleLowerCase())).map(n => n.id));
    let edges = g.edges.filter(e => (matching.has(e.source) || matching.has(e.target)) && (relation.selectedIndex === 0 || (e.label || e.kind) === relation.value));
    edges = edges.filter(e => {
      const a = anchorFor(e);
      return a && (!municipality.value || a.municipality_ids.includes(municipality.value)) &&
        (!year.value || (a.quote.match(/\b(?:19|20)\d{2}\b/g)||[]).includes(year.value)) &&
        (!type.value || [e.source,e.target].some(id=>g.nodes.find(n=>n.id===id)?.kind===type.value));
    });
    if (focus.value) {
      const visited = new Set([focus.value]);
      for (let step=0;step<Number(depth.value);step++) {
        const next = new Set(visited);
        edges.forEach(e=>{if(visited.has(e.source)||visited.has(e.target)){next.add(e.source);next.add(e.target);}});
        next.forEach(n=>visited.add(n));
      }
      edges = edges.filter(e=>visited.has(e.source)&&visited.has(e.target));
    }
    const ids = new Set(edges.flatMap(e => [e.source, e.target]));
    const nodes = g.nodes.filter(n => ids.has(n.id));
    if (workspaceGraph) workspaceGraph.destroy(); workspaceGraph = null;
    if (window.cytoscape && nodes.length) workspaceGraph = cytoscape({container: $("workspace-graph"),
      elements: [...nodes.map(n => ({data:n})), ...edges.map(e => ({data:e}))],
      layout: {name:layout.value}, style:[{selector:"node",style:{label:"data(label)","background-color":"#165941","font-size":12,"text-wrap":"wrap","text-max-width":100}},{selector:'node[kind="evidence"]',style:{"background-color":"#7150a0",shape:"roundrectangle"}},{selector:'node[kind="document"]',style:{"background-color":"#365e91",shape:"rectangle"}},{selector:"edge",style:{label:"data(label)","font-size":9,"target-arrow-shape":"triangle","curve-style":"bezier","line-color":"#879b8b"}}]});
    if (workspaceGraph && nodes.length) workspaceGraph.on("tap", "edge", event => {
      const edge = event.target.data();
      const anchor = t.claims.flatMap(c=>c.evidence).find(a => (edge.evidence_ids || []).includes(a.evidence_id));
      if (anchor) inspect(t, anchor).catch(e => message(e.message));
    });
    content.replaceChildren(el("p", `${nodes.length} Knoten · ${edges.length} Kanten · ${g.cutoff ? "Cutoff aktiv" : "nur dieser Antwortstand"}. Pan/Zoom im Graph. Keine Kausalitätsinferenz.`));
    content.append(el("p", "Legende: Blau = Dokument · Violett = Quellenpassage · Grün = extrahierte Entität. Farben zeigen Typen, keine fachliche Validierung. Nachbarschaft nur innerhalb dieses Antwortstands; Jahresfilter = Textnennung, keine gemessene Ereigniszeit."));
    content.append(dataTable(["Von", "Relation", "Nach", "Status", "Beleg"], edges.map(e => [
      nodes.find(n=>n.id===e.source)?.label || e.source, e.label || e.kind,
      nodes.find(n=>n.id===e.target)?.label || e.target, e.validation_status,
      action("Beleg prüfen", () => {
        const anchor = t.claims.flatMap(c=>c.evidence).find(a=>(e.evidence_ids||[]).includes(a.evidence_id));
        if(anchor) return inspect(t, anchor);
      })
    ])));
  };
  search.addEventListener("input", redraw); [relation,layout,type,municipality,year,focus,depth].forEach(control=>control.addEventListener("change",redraw)); redraw();
}

// Keep progressive answer graphs visible after responsive container changes.
new ResizeObserver(() => {
  if (workspaceGraph) { workspaceGraph.resize(); workspaceGraph.fit(undefined, 24); }
}).observe($("workspace-graph"));

let methodOptions = [];
async function loadMethods() {
  const config = await api("methods"); methodOptions = config.methods;
  const picker = $("method"); picker.replaceChildren(new Option("Automatisch", ""));
  const group = $("compare-methods"); group.replaceChildren();
  for (const method of methodOptions) {
    const option = new Option(method.label + (method.available ? "" : " · nicht verfügbar"), method.id);
    option.disabled = !method.available; option.title = method.reason; picker.append(option);
    const label = el("label"), check = el("input"); check.type = "checkbox"; check.value = method.id;
    check.disabled = !method.available; check.checked = method.available; check.name = "compare-method";
    label.append(check, document.createTextNode(method.label + " — " + method.reason)); group.append(label);
  }
  picker.addEventListener("change", () => {
    $("method-reason").textContent = methodOptions.find(m => m.id === picker.value)?.reason || "Automatische Auswahl unter tatsächlich verfügbaren Adaptern. Keine Qualitätsrangliste.";
  });
  picker.dispatchEvent(new Event("change"));
}
function renderComparison(record) {
  $("comparison-panel").hidden = false;
  $("main").classList.add("comparing");
  const target = $("comparison-results"); target.replaceChildren();
  target.append(el("p", "UNVALIDATED · technische Messung, keine unabhängig geprüfte Antwortqualität. Snapshot: " + record.configuration.snapshot));
  target.append(action("Zurück zum Gespräch", () => { $("main").classList.remove("comparing"); $("comparison-panel").hidden = true; }));
  target.append(action("Vergleich exportieren", () => download("method-comparison.json", record)));
  const grid = el("div"); grid.className = "comparison-grid";
  for (const arm of record.results) {
    const card = el("article"), t = arm.turn;
    card.append(el("h3", methodOptions.find(m => m.id === arm.method)?.label || arm.method));
    card.append(el("p", `${arm.status === "COMPLETED" ? "Ausgeführt" : "Fehler"} · ${t ? (labels[t.answer_status] || "Antwortstatus unbekannt") : "Keine Antwort"} · ${Number(arm.elapsed_seconds).toFixed(3)} s`));
    if (t) {
      card.append(el("p", t.answer), el("p", t.generation_label || t.generation_provider));
      for (const claim of t.claims) for (const anchor of claim.evidence) card.append(action("Quelle: " + anchor.citation, () => inspect(t, anchor)));
      card.append(el("p", `${t.claims.length} Quellenclaims · keine Qualitätswertung aus der Anzahl.`));
      card.append(action("Graph untersuchen", () => location.assign(`/graph?chat=${cid}&turn=${t.turn_id}`)));
      card.append(action("Daten prüfen", () => location.assign(`/visualizations?chat=${cid}&turn=${t.turn_id}`)));
      card.append(el("p", `Modell: ${t.model_version} · Prompt: ${t.prompt_version}`));
    } else card.append(el("p", "Diese Methode konnte nicht sicher ausgeführt werden. Andere Ergebnisse bleiben sichtbar."));
    card.append(el("p", `CPU: ${arm.process_cpu_seconds == null ? "nicht gemessen" : arm.process_cpu_seconds.toFixed(3) + " s"} · RAM: je Arm nicht gemessen · Token: ${t?.generation_receipt?.total_tokens ?? "nicht gemessen / keine LLM-Generierung"} · Kosten: keine externen kostenpflichtigen Aufrufe`));
    grid.append(card);
  }
  target.append(grid);
  for (const caveat of record.limits) target.append(el("p", caveat));
}
async function restoreComparison() {
  if (!cid) { $("comparison-panel").hidden = true; return; }
  const current = cid, serial = epoch;
  const records = await api(`conversations/${cid}/comparisons`);
  if (current !== cid || serial !== epoch) return;
  const latest = records.at(-1);
  if (!latest) { $("comparison-panel").hidden = true; return; }
  renderComparison(latest);
}
$("run-compare").addEventListener("click", async () => {
  if (controller) return;
  const question = $("question").value.trim() || turns.at(-1)?.question;
  if (!question) { message("Bitte zuerst eine Forschungsfrage eingeben."); return; }
  const methods = [...document.querySelectorAll('[name="compare-method"]:checked')].map(n => n.value);
  if (methods.length < 2) { message("Bitte mindestens zwei verfügbare Suchmethoden auswählen."); return; }
  $("run-compare").disabled = true;
  try {
    if (!cid) await create();
    const comparisonChat = cid, comparisonEpoch = epoch;
    const record = await api(`conversations/${comparisonChat}/compare`, "POST", {question, methods,
      parent_turn_id: question === turns.at(-1)?.question ? turns.at(-1).parent_turn_id : turns.at(-1)?.turn_id || null,
      municipality_ids: $("scope").value.split(",").map(x=>x.trim()).filter(Boolean), reference_year:Number($("year").value)||null});
    if (cid !== comparisonChat || epoch !== comparisonEpoch) return;
    const history = await api("conversations/" + comparisonChat);
    if (cid !== comparisonChat || epoch !== comparisonEpoch) return;
    turns = history; render(); renderComparison(record);
    $("research-results").scrollTop = 0;
    message("Methodenvergleich abgeschlossen. Qualität bleibt UNVALIDATED.");
  } catch (e) { message(e.message); }
  finally { $("run-compare").disabled = false; }
});
