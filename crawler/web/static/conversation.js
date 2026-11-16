/* Source text is always textContent, never interpreted as DOM or instructions. */
"use strict";
const $ = (id) => document.getElementById(id),
  el = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    return n;
  };
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
      li.append(action("Unterhaltung " + (chats.length - i), () => open(c.id)));
      return li;
    }),
  );
}
async function open(id) {
  epoch++;
  controller?.abort();
  cid = id;
  turns = await api("conversations/" + id);
  render();
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
      const p = el("p", c.text);
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
    article.append(tools, trace);
    target.append(article);
  }
  target.lastElementChild?.scrollIntoView({ block: "nearest" });
}
async function inspect(t, e) {
  const current = await api(
    `conversations/${cid}/evidence/${t.turn_id}/${encodeURIComponent(e.evidence_id)}`,
  );
  $("quote").textContent = current.quote;
  $("evidence-notice").textContent =
    "Extrahierte Passage, nicht unabhängig fachlich validiert. Original-Bodyhash/Seite nur anzeigen, wenn tatsächlich vorhanden.";
  $("provenance").replaceChildren();
  for (const [k, v] of Object.entries({
    Beleg: current.evidence_id,
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
    await api("conversations/" + cid, "DELETE");
    cid = null;
    turns = [];
    render();
    await list();
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
  })
  .catch((e) => message(e.message));
