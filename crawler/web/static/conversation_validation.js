"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let csrf = "",
    selected = null,
    queue = [], openedAt = 0, saveTimer = null, previous = [], reviewEpoch = 0;
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
    if (!r.ok) throw Error(d.detail || "Prüfvorgang nicht verfügbar");
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
    const d = await api(
      "queue?q=" + encodeURIComponent($("review-filter").value),
    );
    queue = d.items || [];
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
    msg(`${d.progress.submitted} / ${d.progress.total} abgegeben · ${queue.length} in dieser Queue. Priorisierung: Operatorheuristik, keine zufällige Evaluationsstichprobe.`);
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
    $("review-history").textContent = JSON.stringify(selected.history, null, 2);
    const draft = selected.history
      .filter((e) => e.kind === "protocol_draft")
      .at(-1)?.payload;
    if (draft) {
      $("review-decision").value = draft.decision || "";
      $("review-comment").value = draft.comment || "";
      document.querySelectorAll("[data-metric]").forEach(s => {
        const value = draft.metric_judgments?.[s.dataset.metric];
        s.value = typeof value === "boolean" ? String(value) : "";
      });
    }
    $("review-submit").disabled = selected.review_state === "SUBMITTED";
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
      await api("session", "DELETE");
      location.reload();
    }),
  );
  $("review-refresh").addEventListener("click", run(load));
  $("review-back").addEventListener("click", run(() => {
    const id = previous.pop(); if (id) return open(id);
  }));
  $("review-open-original").addEventListener(
    "click",
    run(async () => {
      const d = await api(
        "objects/" + encodeURIComponent(selected.object_id) + "/original",
      );
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
    if (!draft && !confirm("Eigenes Urteil unveränderlich abgeben?")) return;
    const form = {
      decision: $("review-decision").value,
      comment: $("review-comment").value,
      evidence_inspected: $("review-inspected").checked,
      independence_confirmed: $("review-independent").checked,
      human_confirmed: $("review-human").checked,
      corrections: {},
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
    if (draft) msg("Entwurf gespeichert (Interaktionsdauer, kein Nachweis fachlichen Lesens).");
    else {
      msg("Eigenes Urteil gespeichert. Fremdurteile bleiben verborgen.");
      await load();
      const next = queue.find(
        (r) =>
          r.object_id !== selected.object_id && r.state !== "SUBMITTED",
      );
      if (next) await open(next.object_id);
      else $("review-submit").disabled = true;
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
    ++reviewEpoch; clearTimeout(saveTimer); previous = [];
    selected = null; queue = []; $("review-form").reset();
    $("review-case").hidden = true; $("review-queue").replaceChildren();
    await api("demo-session/" + b.dataset.demo, "POST");
    $("review-login").hidden = true; $("review-logout").hidden = false;
    $("review-case").hidden = true; selected = null;
    await load();
  })));
  fetch("/api/chat/session")
    .then((r) => r.json())
    .then((d) => {
      csrf = d.csrf;
      $("review-demo").hidden = !d.fixture;
      $("review-login-button").disabled = false;
    })
    .catch((e) => msg(e.message));
})();
