"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let csrf = "",
    selected = null,
    queue = [];
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
        (item.label || item.kind || "Prüffall") + " · " + item.review_state;
      b.addEventListener(
        "click",
        run(() => open(item.object_id)),
      );
      li.append(b);
      $("review-queue").append(li);
    }
    msg(queue.length + " verfügbare Prüfaufträge.");
  }
  async function open(id) {
    selected = await api("objects/" + encodeURIComponent(id));
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
    };
    await api(
      "objects/" + encodeURIComponent(selected.object_id) + "/review",
      "POST",
      { draft, form },
    );
    if (draft) msg("Entwurf gespeichert.");
    else {
      msg("Eigenes Urteil gespeichert. Fremdurteile bleiben verborgen.");
      await load();
      const next = queue.find(
        (r) =>
          r.object_id !== selected.object_id && r.review_state !== "SUBMITTED",
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
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      run(() => submit(true))();
    }
  });
  fetch("/api/chat/session")
    .then((r) => r.json())
    .then((d) => {
      csrf = d.csrf;
    })
    .catch((e) => msg(e.message));
})();
