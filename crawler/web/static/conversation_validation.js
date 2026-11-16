"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let csrf = "",
    selected = null,
    queue = [], openedAt = 0, saveTimer = null, previous = [], reviewEpoch = 0, offset = 0, total = 0, queueRole = "reviewer", loadEpoch = 0;
  function msg(s) {
    $("review-status").textContent = s;
  }
  async function api(path, method = "GET", body) {
    const r = await fetch("/api/chat-review/" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) { const error = Error(d.detail || "Prüfvorgang nicht verfügbar"); error.status = r.status; throw error; }
    return d;
  }
  function run(f) {
    return (e) => {
      e?.preventDefault();
      Promise.resolve()
        .then(() => f(e))
        .catch((e) => msg(e.message));
    };
  }
  async function load() {
    const version = ++loadEpoch;
    const d = await api(
      "queue?limit=50&offset=" + offset + "&kind=" + encodeURIComponent($("review-kind-filter").value) + "&q=" + encodeURIComponent($("review-filter").value),
    );
    if (version !== loadEpoch) return;
    queue = d.items || []; total = d.total; queueRole = d.role;
    sessionStorage.setItem("klima-review-filters", JSON.stringify({q:$("review-filter").value, kind:$("review-kind-filter").value}));
    $("review-page-prev").disabled = offset === 0;
    $("review-page-next").disabled = offset + 50 >= total;
    $("review-queue").replaceChildren();
    for (const item of queue) {
      const li = document.createElement("li"),
        b = document.createElement("button");
      b.textContent =
        (item.label || item.kind || "Prüffall") + " · " + item.state;
      b.addEventListener(
        "click",
        run(() => open(item.object_id)),
      );
      li.append(b);
      $("review-queue").append(li);
    }
    msg(`Prüfkonto ${d.actor} · ${d.role} · ${d.progress.submitted} / ${d.progress.total} abgegeben · ${queue.length} auf Seite ${Math.floor(offset / 50) + 1}. Priorisierung: Operatorheuristik, keine zufällige Evaluationsstichprobe.`);
  }
  async function open(id) {
    clearTimeout(saveTimer);
    if (selected) previous.push(selected.object_id);
    openedAt = performance.now();
    const version = ++reviewEpoch;
    const value = await api("objects/" + encodeURIComponent(id));
    if (version !== reviewEpoch) return;
    selected = value;
    $("review-case").hidden = false;
    $("review-title").textContent = selected.payload.label || selected.key;
    $("review-kind").textContent =
      selected.kind +
      " · " +
      selected.review_state +
      " · " +
      (selected.technical
        ? "TECHNICAL_FIXTURE"
        : "Unabhängige Fachprüfung erforderlich");
    $("review-guide").textContent = ({
      source: "Source Screening: Relevanz, Autorität und kommunale Zuordnung prüfen. Kein Claim-Entailment ableiten.",
      claim: "Claim Validation: Trägt die Originalstelle diese Aussage einschließlich Zeit, Einheit und Gemeinde?",
      relation: "Relation Validation: Beide Endpunkte, Rollen, Prädikat und Geltung am Original prüfen.",
      extraction: "IE Validation: Wert, Einheit, Zeit, Scope und Unknown getrennt prüfen.",
      answer_evidence: "Answer Validation: Fragebezug, Vollständigkeit und angemessene Abstention separat beurteilen."
    })[selected.kind] || "Dimension des konkreten Prüfobjekts am Original beurteilen.";
    $("review-dimensions").hidden = selected.kind !== "claim";
    $("review-proposal").textContent = selected.payload.proposed_text || "";
    $("review-original").textContent = "Original noch nicht geöffnet.";
    $("review-locator").textContent = "";
    $("review-form").reset();
    $("review-unit-fields").replaceChildren();
    const dimensionLabels = {relevance:"Relevanz", authority:"Autorität", municipality_scope:"Gemeindegeltung", version_match:"Quellenfassung", value:"Wert", unit:"Einheit", actor_role:"Akteursrolle", scope:"Geltung", time:"Zeit", endpoints:"Beide Endpunkte", relation_type:"Relationstyp", actor_roles:"Akteursrollen", completeness:"Vollständigkeit", abstention:"Abstention"};
    for (const [key, values] of Object.entries(selected.unit_schema || {})) {
      const label = document.createElement("label"), select = document.createElement("select");
      label.append(document.createTextNode(dimensionLabels[key] || key));
      select.dataset.unitDimension = key;
      for (const value of ["", ...values]) { const option = document.createElement("option"); option.value = value; option.textContent = value || "Nicht beurteilt"; select.append(option); }
      label.append(select); $("review-unit-fields").append(label);
    }
    $("review-unit-dimensions").hidden = !Object.keys(selected.unit_schema || {}).length;
    $("review-history").textContent = JSON.stringify(selected.history, null, 2);
    const draft = selected.history
      .filter((e) => ["protocol_draft", "protocol_review", "protocol_adjudication_draft", "protocol_adjudication"].includes(e.kind))
      .at(-1)?.payload;
    if (draft) {
      $("review-decision").value = draft.decision || "";
      $("review-comment").value = draft.comment || "";
      document.querySelectorAll("[data-unit-dimension]").forEach(s => s.value = draft.corrections?.unit_judgments?.[s.dataset.unitDimension] || "");
      document.querySelectorAll("[data-metric]").forEach(s => {
        const value = draft.metric_judgments?.[s.dataset.metric];
        s.value = typeof value === "boolean" ? String(value) : "";
      });
    }
    const finalized = selected.review_state === "SUBMITTED" || selected.review_state === "ADJUDICATED" || selected.review_state === "CONSENSUS";
    $("review-form").querySelectorAll("input, select, textarea, button").forEach(n => n.disabled = finalized);
    $("review-submit").disabled = finalized || (queueRole === "adjudicator" && selected.review_state !== "CONFLICT");
    $("review-independent").parentElement.lastChild.textContent = queueRole === "adjudicator" ? "Eigenständige Entscheidung nach Originalprüfung und Vergleich der Ersturteile." : "Ohne fremde Urteile unabhängig geprüft.";
    $("review-comment").required = queueRole === "adjudicator" || ($("review-decision").value !== "SUPPORTED" && $("review-decision").value !== "");
    $("review-title").tabIndex = -1;
    $("review-title").focus();
  }
  $("review-login").addEventListener(
    "submit",
    run(async () => {
      const d = await api("session", "POST", {
        username: $("review-user").value,
        password: $("review-password").value,
      });
      $("review-password").value = "";
      $("review-login").hidden = true;
      $("review-logout").hidden = false;
      msg(
        d.technical
          ? "Technischer Prüfstore — kein Human Gold."
          : "Persönliches Prüfkonto aktiv.",
      );
      await load();
    }),
  );
  $("review-logout").addEventListener(
    "click",
    run(async () => {
      sessionStorage.removeItem("klima-review-filters");
      await api("session", "DELETE");
      location.reload();
    }),
  );
  $("review-refresh").addEventListener("click", run(() => {offset = 0; return load();}));
  $("review-kind-filter").addEventListener("change", run(() => {offset = 0; return load();}));
  $("review-page-prev").addEventListener("click", run(() => {offset = Math.max(0, offset - 50); return load();}));
  $("review-page-next").addEventListener("click", run(() => {if (offset + 50 < total) offset += 50; return load();}));
  $("review-back").addEventListener("click", run(() => {
    const id = previous.pop(); if (id) return open(id);
  }));
  $("review-open-original").addEventListener(
    "click",
    run(async () => {
      const version = reviewEpoch, objectId = selected.object_id;
      const d = await api(
        "objects/" + encodeURIComponent(objectId) + "/original",
      );
      if (version !== reviewEpoch || objectId !== selected?.object_id) return;
      $("review-original").textContent = d.sources
        .map((s) => s.quote)
        .join("\n\n");
      $("review-locator").textContent = d.sources
        .map(
          (s) =>
            s.sha256 +
            " · " +
            (s.page_ref || s.unit_id || "synthetisches Dokument"),
        )
        .join("\n");
      $("review-original").focus();
    }),
  );
  async function submit(draft) {
    if (!selected) throw Error("Prüffall wählen");
    const version = reviewEpoch;
    if (!draft && !confirm("Eigenes Urteil unveränderlich abgeben?")) return;
    const form = {
      decision: $("review-decision").value,
      comment: $("review-comment").value,
      evidence_inspected: $("review-inspected").checked,
      independence_confirmed: $("review-independent").checked,
      human_confirmed: $("review-human").checked,
      corrections: Object.keys(selected.unit_schema || {}).length ? {
        review_schema: selected.unit_schema_version,
        unit_judgments: Object.fromEntries([...document.querySelectorAll("[data-unit-dimension]")].map(s => [s.dataset.unitDimension, s.value || null]))
      } : {},
      metric_judgments: selected.kind === "claim" ? Object.fromEntries(
        [...document.querySelectorAll("[data-metric]")].map(s => [s.dataset.metric, s.value === "" ? null : s.value === "true"])
      ) : {},
      interaction_seconds: Math.round((performance.now() - openedAt) / 1000),
    };
    await api(
      "objects/" + encodeURIComponent(selected.object_id) + "/review",
      "POST",
      { draft, form },
    );
    if (version !== reviewEpoch) return;
    if (draft) msg("Entwurf gespeichert (Interaktionsdauer, kein Nachweis fachlichen Lesens).");
    else {
      msg("Eigenes Urteil gespeichert. Fremdurteile bleiben verborgen.");
      await load();
      const next = queue.find(
        (r) =>
          r.object_id !== selected.object_id && r.state !== "SUBMITTED",
      );
      if (next) await open(next.object_id);
      else if (offset + 50 < total) {
        offset += 50; await load();
        const pending = queue.find(r => r.state !== "SUBMITTED");
        if (pending) await open(pending.object_id);
      } else $("review-submit").disabled = true;
    }
  }
  $("review-save").addEventListener(
    "click",
    run(() => submit(true)),
  );
  $("review-form").addEventListener(
    "submit",
    run(() => submit(false)),
  );
  $("review-decision").addEventListener("input", () => {
    $("review-comment").required = queueRole === "adjudicator" || $("review-decision").value !== "SUPPORTED";
  });
  $("review-form").addEventListener("input", () => {
    clearTimeout(saveTimer);
    const id = selected?.object_id;
    saveTimer = setTimeout(() => {
      if (id && selected?.object_id === id && !$("review-submit").disabled) run(() => submit(true))();
    }, 1200);
  });
  document.addEventListener("keydown", (e) => {
    if (e.altKey && /^[1-6]$/.test(e.key) && selected) {
      e.preventDefault(); $("review-decision").selectedIndex = Number(e.key);
      $("review-decision").dispatchEvent(new Event("input", {bubbles:true}));
    }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      run(() => submit(true))();
    }
  });
  document.querySelectorAll("[data-demo]").forEach(b => b.addEventListener("click", run(async () => {
    document.querySelectorAll("[data-demo]").forEach(button => button.disabled = true);
    try {
    ++loadEpoch;
    ++reviewEpoch; clearTimeout(saveTimer); previous = []; offset = 0;
    sessionStorage.removeItem("klima-review-filters"); $("review-filter").value = ""; $("review-kind-filter").value = "";
    selected = null; queue = []; $("review-form").reset();
    $("review-case").hidden = true; $("review-queue").replaceChildren();
    await api("demo-session/" + b.dataset.demo, "POST");
    $("review-login").hidden = true; $("review-logout").hidden = false;
    $("review-case").hidden = true; selected = null;
    await load();
    } finally { document.querySelectorAll("[data-demo]").forEach(button => button.disabled = false); }
  })));
  try {
    const filters = JSON.parse(sessionStorage.getItem("klima-review-filters") || "{}");
    $("review-filter").value = String(filters.q || "").slice(0,200);
    $("review-kind-filter").value = filters.kind || "";
  } catch (_) { sessionStorage.removeItem("klima-review-filters"); }
  fetch("/api/chat/session")
    .then((r) => r.json())
    .then(async (d) => {
      csrf = d.csrf;
      $("review-demo").hidden = !d.fixture;
      $("review-login-button").disabled = false;
      try { await load(); $("review-login").hidden = true; $("review-logout").hidden = false; }
      catch (error) { if (error.status !== 401) msg(error.message); }
    })
    .catch((e) => msg(e.message));
})();
