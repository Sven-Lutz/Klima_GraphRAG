'use strict';
(() => {
  let overview, subgraph, cy;
  let navigationVersion = 0, inspectionVersion = 0;
  const el = id => document.getElementById(id);
  const text = (tag, value) => { const n = document.createElement(tag); n.textContent = value; return n; };
  async function get(url) {
    const response = await fetch(url);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.code || 'Graph nicht verfügbar');
    return body;
  }
  function option(parent, value, label) { const o = new Option(label, value); parent.append(o); }
  function rootId() {
    return overview.entity_index.find(e => `${e.label} (${e.id})` === el('graph-search').value)?.id;
  }
  function style() {
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue('--ink').trim(), paper = css.getPropertyValue('--paper').trim();
    return [
      { selector: 'node', style: { label: 'data(label)', 'background-color': css.getPropertyValue('--accent').trim(),
        color: ink, width: 30, height: 30, 'text-valign': 'bottom', 'text-margin-y': 8,
        'text-wrap': 'ellipsis', 'text-max-width': 150, 'font-size': 12,
        'text-background-color': paper, 'text-background-opacity': 0.9, 'text-background-padding': '3px' } },
      { selector: 'node[type="Organization"], node[type="AdministrativeBody"]', style: { shape: 'round-rectangle' } },
      { selector: 'node[type="Project"], node[type="Measure"]', style: { shape: 'rectangle', 'background-color': '#009260' } },
      { selector: 'edge', style: { width: 1.8, 'line-color': '#78928d', 'target-arrow-color': '#78928d',
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier', label: 'data(predicate)',
        'font-size': 10, color: ink, 'text-rotation': 'autorotate', 'text-background-color': paper,
        'text-background-opacity': 1, 'text-background-padding': '2px' } },
      { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#c27b22' } },
      { selector: 'edge:selected', style: { 'line-color': '#c27b22', width: 4 } },
      { selector: '.chain', style: { 'line-color': '#c27b22', 'target-arrow-color': '#c27b22', width: 5 } }
    ];
  }
  function inspectNode(node) {
    ++inspectionVersion;
    el('graph-evidence-export').hidden = true;
    el('graph-evidence-export').removeAttribute('href');
    el('graph-state').textContent = `${node.label}: Entitätsidentität vorläufig.`;
    const panel = el('inspector'); panel.replaceChildren(text('h2', node.label),
      text('p', 'Identität vorläufig'),
      text('p', 'Diese Entität wurde aus den Quellen abgeleitet. Ob gleich benannte Einträge dieselbe Institution, Maßnahme oder dasselbe Projekt bezeichnen, ist nicht unabhängig fachlich geprüft.'));
    const identity = document.createElement('details');
    identity.append(text('summary', 'Identitätsgrundlage'), text('p', node.type), text('p', node.identity_basis));
    panel.append(identity);
    const b = text('button', 'Diese Entität als Mittelpunkt öffnen'); b.type = 'button';
    b.onclick = () => { selectRoot(node.id); load(); }; panel.append(b);
    panel.append(text('h3', 'Verbundene Quellenbelege'));
    for (const edge of subgraph.edges.filter(e => e.source === node.id || e.target === node.id)) {
      const other = subgraph.nodes.find(n => n.id === (edge.source === node.id ? edge.target : edge.source));
      const sourceButton = text('button', `${edge.predicate} · ${other.label}`);
      sourceButton.type = 'button'; sourceButton.onclick = () => inspectEdge(edge); panel.append(sourceButton);
    }
    panel.append(text('p', 'Die Quellenbelege prüfen die gespeicherte Beziehung, nicht automatisch die Identität über verschiedene Dokumente.'));
    panel.focus();
  }
  async function inspectEdge(edge) {
    const version = navigationVersion, inspection = ++inspectionVersion;
    el('inspector').replaceChildren(text('p', 'Kantenbeleg wird geladen …'));
    el('graph-evidence-export').hidden = true;
    el('graph-state').textContent = 'Quellenidentität und Belegkontext werden geladen …';
    try {
      const result = await get(`/api/graph/evidence/${encodeURIComponent(edge.id)}`);
      if (version !== navigationVersion || inspection !== inspectionVersion) return;
      const item = result.evidence.find(e => e.assertions.some(a => a.assertion_id === edge.id));
      if (!item) throw new Error('Keine vollständige Kantenprovenienz');
      inspect(item, false);
      const panel = el('inspector');
      panel.append(text('p', 'Vorläufige agentengestützte Extraktion. Keine unabhängige menschliche Fachprüfung.'),
        text('p', `In der Quelle genannte Referenzzeit: ${edge.reference_time || 'unbekannt'} · kein Gültigkeitsnachweis.`));
      if (edge.visual_original_recommended) {
        const warning = text('p', 'Statusangabe aus einer PDF: Bitte die Originaltabelle einschließlich Farben und Legende prüfen. Der Textausschnitt allein kann den Status nicht belegen.');
        warning.className = 'notice'; panel.append(warning);
      }
      const metadata = document.createElement('details');
      metadata.append(text('summary', 'Extraktionsidentität'), text('p', `${edge.extraction_method} · ${edge.prompt_version} · ${edge.validation}`));
      panel.append(metadata);
      for (const context of result.evidence.filter(e => e.evidence_id !== item.evidence_id)) {
        const button = text('button', `Kontextbeleg: ${context.document.heading_context || context.source_id}`);
        button.type = 'button'; button.onclick = () => inspect(context, true); panel.append(button);
      }
      const exportLink = el('graph-evidence-export'); exportLink.hidden = false;
      exportLink.href = `/api/export/${result.result_id}.json`; exportLink.textContent = 'Kantenbeleg exportieren';
      panel.scrollIntoView({ block: 'nearest' }); panel.focus();
      el('graph-state').textContent = `Kante ${edge.id}: Quellenbezug verfügbar, fachliche Validierung ausstehend.`;
      cy.edges().unselect(); cy.getElementById(edge.id).select();
    } catch (error) {
      if (version !== navigationVersion || inspection !== inspectionVersion) return;
      el('graph-state').textContent = `Beleg nicht verfügbar: ${error.message}`;
      el('inspector').replaceChildren(text('h2', 'Beleg nicht verfügbar'), text('p', 'Bitte die Kante erneut auswählen. Es wird kein alter Beleg angezeigt.'));
    }
  }
  function selectRoot(id) {
    const e = overview.entity_index.find(e => e.id === id);
    el('graph-search').value = `${e.label} (${e.id})`;
  }
  function positionFor(node) {
    if (el('graph-canvas').clientWidth < 600) {
      const ordered = [...subgraph.nodes].sort((a,b) => a.distance-b.distance || a.id.localeCompare(b.id));
      return { x: 150, y: 70 + ordered.findIndex(n => n.id === node.id) * 100 };
    }
    const peers = subgraph.nodes.filter(n => n.distance === node.distance);
    return { x: 150 + node.distance * 260, y: 90 + peers.findIndex(n => n.id === node.id) * 120 };
  }
  async function load() {
    const root = rootId();
    const version = ++navigationVersion;
    ++inspectionVersion;
    el('graph-evidence-export').hidden = true;
    el('graph-evidence-export').removeAttribute('href');
    el('graph-export').disabled = true;
    el('inspector').replaceChildren(text('h2', 'Entität oder Kante wählen'), text('p', 'Der bisherige Beleg wurde zurückgesetzt.'));
    el('graph-accessible').replaceChildren();
    if (cy) { cy.destroy(); cy = undefined; }
    subgraph = undefined;
    window.klimaGraph = undefined;
    if (!root) { el('graph-state').textContent = 'Bitte eine gespeicherte Entität aus der Liste wählen.'; return; }
    const params = new URLSearchParams({ root, depth: el('graph-depth').value,
      predicate: el('graph-predicate').value, entity_type: el('graph-type').value });
    if (el('graph-year').value) params.set('year', el('graph-year').value);
    if (el('graph-target').value) params.set('target', el('graph-target').value);
    el('graph-state').textContent = 'Teilgraph wird geladen …';
    try {
      const next = await get('/api/graph?' + params);
      if (version !== navigationVersion) return;
      subgraph = next;
      el('graph-evidence-export').hidden = true;
      el('inspector').replaceChildren(text('h2', 'Entität oder Kante wählen'), text('p', 'Die Auswahl öffnet Identität und Originalbeleg des aktuellen Teilgraphen.'));
      const elements = [];
      for (const node of subgraph.nodes) {
        elements.push({ group: 'nodes', data: node, position: positionFor(node) });
      }
      for (const edge of subgraph.edges) elements.push({ group: 'edges', data: edge });
      if (cy) cy.destroy();
      cy = cytoscape({ container: el('graph-canvas'), elements, style: style(),
        layout: { name: 'preset' }, minZoom: 0.12, maxZoom: 3, wheelSensitivity: 0.2 });
      cy.on('tap', 'node', event => inspectNode(event.target.data()));
      cy.on('tap', 'edge', event => inspectEdge(event.target.data()));
      for (const id of subgraph.chain) cy.getElementById(id).addClass('chain');
      const list = el('graph-accessible'); list.replaceChildren();
      for (const node of subgraph.nodes) {
        const b = text('button', `${node.type}: ${node.label}`); b.type = 'button';
        b.onclick = () => { cy.nodes().unselect(); cy.getElementById(node.id).select(); inspectNode(node); }; list.append(b);
      }
      for (const edge of subgraph.edges) {
        const source = subgraph.nodes.find(n => n.id === edge.source), target = subgraph.nodes.find(n => n.id === edge.target);
        const b = text('button', `${source.label} → ${edge.predicate} → ${target.label}`); b.type = 'button';
        b.dataset.relationId = edge.id; b.onclick = () => inspectEdge(edge); list.append(b);
      }
      el('graph-state').textContent = `${subgraph.nodes.length} Entitäten · ${subgraph.edges.length} Relationen` +
        (subgraph.truncated ? ' · Größenlimit erreicht' : '') +
        (subgraph.target ? ` · ${subgraph.chain.length ? subgraph.chain.length + ' Kanten in der markierten Kette' : 'Kein Pfad im gewählten Ausschnitt'}` : '');
      window.klimaGraph = { graph: cy, data: subgraph };
      el('graph-export').disabled = false;
    } catch (error) {
      if (version !== navigationVersion) return;
      el('graph-state').textContent = `Graph nicht verfügbar: ${error.message}`;
    }
  }
  window.openGraphExplorer = async () => {
    try {
      if (!overview) {
        overview = await get('/api/graph');
        el('graph-overview').textContent = `${overview.entities} Entitäten · ${overview.relations} Relationen · ${overview.source_versions} Originalversionen mit Kanten · vorläufige Extraktion`;
        for (const e of overview.entity_index) {
          option(el('graph-entities'), `${e.label} (${e.id})`, e.label);
          option(el('graph-target'), e.id, e.label);
        }
        for (const key of Object.keys(overview.relation_types).sort()) option(el('graph-predicate'), key, `${key} (${overview.relation_types[key]})`);
        for (const key of Object.keys(overview.entity_types).sort()) option(el('graph-type'), key, key);
        for (const year of overview.reference_years) option(el('graph-year'), year, `${year} · Referenznennung`);
        selectRoot(overview.default_entity);
      }
      await load();
    } catch (error) { el('graph-overview').textContent = `Graphbestand nicht verfügbar: ${error.message}`; }
  };
  el('graph-form').onsubmit = event => { event.preventDefault(); load(); };
  el('graph-zoom-in').onclick = () => cy && cy.zoom(cy.zoom() * 1.3);
  el('graph-zoom-out').onclick = () => cy && cy.zoom(cy.zoom() / 1.3);
  el('graph-fit').onclick = () => cy && cy.fit(undefined, 45);
  el('graph-export').onclick = () => {
    if (!subgraph) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(subgraph, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = `klima-subgraph-${subgraph.subgraph_sha256.slice(0,12)}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  new ResizeObserver(() => { if (cy) { cy.resize(); cy.nodes().positions(n => positionFor(n.data())); cy.fit(undefined, 45); } }).observe(el('graph-canvas'));
})();
