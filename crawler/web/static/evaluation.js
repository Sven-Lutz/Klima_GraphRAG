(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let observationOffset=0;
  let offset = 0, total = 0, reference = null, automaticReferences = {}, manualQuestions = new Set();
  const names = {development:'Entwicklung',regression:'Regression',holdout:'Holdout · gesperrt',human_candidates:'Menschliche Prüfkandidaten',adversarial:'Adversarial / Negativ'};
  const status = message => { $('evaluation-status').textContent = message; };
  const el = (tag, text) => { const node = document.createElement(tag); if (text != null) node.textContent = text; return node; };
  async function api(path, value) {
    const response = await fetch(path, value === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(typeof result.detail === 'string' ? result.detail : result.error?.message || `Vorgang fehlgeschlagen (${response.status}).`),{status:response.status});
    return result;
  }
  async function catalog() {
    const value = await api('/api/evaluation/catalog');
    $('catalog-counts').replaceChildren(...Object.entries(value.splits).map(([split,row]) => el('p',`${names[split]}: ${row.count}`)));
    const details = $('catalog-strata'); details.replaceChildren();
    Object.entries(value.strata).forEach(([key,counts]) => { details.append(el('h3',key)); const list=el('ul'); Object.entries(counts).forEach(([name,n]) => list.append(el('li',`${name}: ${n}`))); details.append(list); });
    details.append(el('p','Deduplikation: exakte Frage und strukturierte semantische Intention; getrennte Gemeinde-/Quellkomponenten. Keine unabhängige semantische Qualitätsabnahme.'));
    status(`${value.questions} generierte Fragen; Snapshot gebunden. Holdout wird weder angezeigt noch ausgeführt.`);
  }
  async function questions() {
    const split=$('evaluation-split').value;
    const result=await api(`/api/evaluation/questions?split=${encodeURIComponent(split)}&offset=${offset}&limit=25&search=${encodeURIComponent($('evaluation-search').value)}`);
    total=result.total; $('question-page').textContent=total ? `${offset+1}–${Math.min(offset+25,total)} von ${total}` : 'Keine passenden Fragen.';
    const body=$('evaluation-questions'); body.replaceChildren();
    result.items.forEach(question => {
      const row=el('tr'), pick=el('td'), label=el('label'); const input=el('input'); input.type='checkbox'; input.value=question.question_id; input.name='question'; input.disabled=split==='human_candidates'&&!referenceReady(question.question_id); input.setAttribute('aria-label',`Frage auswählen: ${question.question}`); label.append(input);pick.append(label); row.append(pick);
      [question.question,`${question.municipality_name} · ${question.region}`,`${question.topic} · ${question.question_type} · ${question.hops} Hop(s)`,`${question.evidence_availability==='CANDIDATE_SEED'?'Gebundene Kandidatenpassage':'Kein Themen-Seed'} · nicht fachlich bewertet`].forEach(value=>row.append(el('td',value)));body.append(row);
    });
    $('questions-previous').disabled=offset===0; $('questions-next').disabled=offset+25>=total;
    $('evaluation-run-button').disabled=split==='human_candidates'&&!document.querySelector('input[name=question]:not(:disabled)');
    $('candidate-handoff').hidden=split!=='human_candidates';
  }
  function referenceReady(id){return reference?manualQuestions.has(id):(automaticReferences[$('evaluation-split').value]?.question_ids||[]).includes(id);}
  async function observations(){
    try{
      const value=await api(`/api/evaluation/review-observations?offset=${observationOffset}`);
      $('review-observations').hidden=false;
      $('observation-status').textContent=value.state==='READY'?`${value.cases} geprüfte Antwortstände · Goldversion ${value.source_gold_version} · ${value.technical?'synthetische Testurteile, kein Human Gold':'unabhängige menschliche Urteile'}.`:'Noch keine aktuellen konsolidierten Beobachtungen; geändertes Material benötigt neue Prüfung.';
      const target=$('observation-results');target.replaceChildren();const titles={answer_support:'Stützung der Aussagen',citation_correctness:'Korrekte Zitate',scope_correctness:'Geltungsbereich',temporal_validity:'Zeitliche Gültigkeit',hallucination_rate:'Anteil erfundener Tatsachen'};const origins={synthetic:'Synthetischer Test',independent_human:'Unabhängige menschliche Prüfung'};
      for(const row of value.results||[]){const section=el('article');section.append(el('h3',row.question));const list=el('dl');list.className='observation-metrics';for(const metric of ['answer_support','citation_correctness','scope_correctness','temporal_validity','hallucination_rate']){const m=row.metrics[metric],term=el('dt',titles[metric]),value=el('dd');value.append(el('span',`Wert: ${m.value===null?'Nicht bewertet':m.value}`),el('span',`Bewertete Claims: ${m.denominator??0}`),el('span',`Herkunft: ${origins[m.label_origin]||m.label_origin||'unbekannt'}`));list.append(term,value);}section.append(list);target.append(section);}
      $('observation-prev').disabled=observationOffset===0;$('observation-next').disabled=observationOffset+(value.results||[]).length>=(value.cases||0);$('observation-export').disabled=value.state!=='READY';
    }catch(error){if(error.status===403){$('review-observations').hidden=true;return;}$('review-observations').hidden=false;$('observation-status').textContent='Gespeicherte Review-Auswertung nicht prüfbar; keine Validierung angenommen.';}
  }
  $('observation-prev').addEventListener('click',()=>{observationOffset=Math.max(0,observationOffset-20);observations();});
  $('observation-next').addEventListener('click',()=>{observationOffset+=20;observations();});
  $('observation-export').addEventListener('click',async()=>{try{const value=await api('/api/evaluation/review-observations?download=true');const link=el('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));link.download='reviewed-research-observations.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}catch(error){status(error.message);}});
  async function automaticFeedback(){
    try{
      const value=await api('/api/evaluation/review-feedback');automaticReferences=value.references||{};
      const selected=automaticReferences[$('evaluation-split').value];
      $('automatic-reference-status').textContent=value.state==='READY'?
        `Konsolidierter Goldsnapshot v${value.gold_version}: ${selected?.tasks||0} passende Referenzaufgaben · ${value.technical?'synthetischer technischer Test, kein Human Gold':'unabhängige menschliche Urteile'}. Neue Läufe verwenden diese Referenzen automatisch.`:
        value.state==='STALE_MATERIAL_REVIEW_REQUIRED'?'Prüfmaterial geändert: alte automatische Referenzen gesperrt; aktuelle Version unabhängig prüfen.':
        'Noch keine konsolidierten automatischen Referenzen für diesen Betrieb.';
    }catch(error){
      automaticReferences={};$('automatic-reference-status').textContent=error.status===403?
        'Für automatische Referenzen mit einem berechtigten Review-Account anmelden.':
        'Automatischer Reviewstatus nicht prüfbar; keine Validierung angenommen.';
    }
    await observations();
    document.querySelectorAll('input[name=question]').forEach(input=>input.disabled=$('evaluation-split').value==='human_candidates'&&!referenceReady(input.value));
    $('evaluation-run-button').disabled=$('evaluation-split').value==='human_candidates'&&!document.querySelector('input[name=question]:not(:disabled)');
  }
  function showRun(container,item) {
    const summary=item.summary; const section=el('details');section.append(el('summary',`${summary.configuration.method}: ${summary.succeeded}/${summary.cases} technisch ausgeführt, ${summary.failed} fehlgeschlagen`));
    section.append(el('p',`Datensatz: ${names[summary.configuration.split]||summary.configuration.split}. Human-Gold-Metriken verfügbar: ${summary.independent_gold_available?'ja':'nein'}.`));
    const errors=el('ul'); Object.entries(summary.errors||{}).forEach(([name,count])=>{ const line=el('li',`${name}: ${count} `), meter=el('meter');meter.min=0;meter.max=Math.max(1,summary.cases);meter.value=count;line.append(meter);errors.append(line); });section.append(errors);
    if (!Object.keys(summary.errors||{}).length) section.append(el('p','Keine Fehlerklassifikation erfasst; das ist kein Nachweis fehlerfreier Antworten.'));
    const wrap=el('div');wrap.className='evaluation-table-wrap';wrap.tabIndex=0;wrap.setAttribute('aria-label','Evaluationsmetriken horizontal scrollen');const table=el('table'),head=el('thead'),hrow=el('tr');['Metrik','Labelursprung','Wert','Bewertet / Gesamt','Fehlende Referenzen'].forEach(name=>hrow.append(el('th',name)));head.append(hrow);table.append(head);const body=el('tbody');
    summary.metrics.forEach(metric=>{const row=el('tr');[metric.metric,metric.label_origin||'keine Referenz',metric.value==null?'Nicht bewertbar':Number(metric.value).toFixed(3),`${metric.evaluated} / ${metric.total}`,Object.entries(metric.missing||{}).map(([reason,n])=>`${reason}: ${n}`).join('; ')||'—'].forEach(value=>row.append(el('td',value)));body.append(row);});table.append(body);wrap.append(table);section.append(wrap);
    const link=el('a','Zusammenfassung exportieren');link.href=`/api/evaluation/export/${encodeURIComponent(item.run_id)}`;link.download=`${item.run_id}.json`;section.append(link);container.append(section);
  }
  async function reports() {const result=await api('/api/evaluation/runs');const target=$('evaluation-runs');target.replaceChildren();result.items.forEach(item=>showRun(target,item));if(!result.items.length)target.append(el('p','Noch keine Läufe für diesen Datensatz.'));}
  function handle(action){return async event=>{event?.preventDefault();try{await action(event);}catch(error){status(error.message);}};}
  $('evaluation-filters').addEventListener('submit',handle(async()=>{offset=0;await questions();}));
  $('evaluation-split').addEventListener('change',handle(async()=>{offset=0;reference=null;manualQuestions.clear();$('reference-status').textContent='Keine manuell importierten Referenzen ausgewählt.';await questions();await automaticFeedback();}));
  $('questions-previous').addEventListener('click',handle(async()=>{offset=Math.max(0,offset-25);await questions();}));
  $('questions-next').addEventListener('click',handle(async()=>{offset+=25;await questions();}));
  $('evaluation-refresh').addEventListener('click',handle(async()=>{await reports();await automaticFeedback();}));
  $('automatic-reference-refresh').addEventListener('click',handle(automaticFeedback));
  $('candidate-handoff').addEventListener('click',handle(async()=>{const packet=await api(`/api/evaluation/annotation-packet?offset=${offset}&limit=25`);const result=await api('/api/review-protocol/import',{objects:packet.objects});status(`${result.object_ids?.length||result.imported||packet.objects.length} Kandidaten im Human Validation Studio vorbereitet. Noch keine fachlichen Urteile erzeugt.`);}));
  $('evaluation-login').addEventListener('submit',handle(async event=>{const form=new FormData(event.target);await api('/api/review-protocol/session',Object.fromEntries(form));event.target.reset();status('Persönlicher Review-Account angemeldet. Berechtigungen werden je Vorgang geprüft.');await automaticFeedback();}));
  $('evaluation-reference').addEventListener('submit',handle(async()=>{const file=$('evaluation-gold-file').files[0];if(!file||file.size>1024*1024)throw new Error('Bitte einen Goldexport bis 1 MiB wählen.');const payload=JSON.parse(await file.text());const result=await api('/api/evaluation/gold',{split:$('evaluation-split').value,export:payload});reference=result.reference_id;manualQuestions=new Set(result.question_ids||[]);await automaticFeedback();$('reference-status').textContent=`${result.tasks} Referenzaufgaben übernommen; Ursprung: ${result.origin}. Recall-Universum bleibt unvollständig.`;status('Versionierte Referenzen geprüft.');}));
  $('evaluation-run').addEventListener('submit',handle(async()=>{const ids=[...document.querySelectorAll('input[name="question"]:checked')].map(node=>node.value);if(!ids.length||ids.length>5)throw new Error('Bitte eine bis fünf Fragen auswählen.');const button=$('evaluation-run-button');button.disabled=true;status('Produktlauf läuft. Antworten ohne Referenz werden fachlich nicht automatisch bewertet.');try{let value=await api('/api/evaluation/run',{question_ids:ids,split:$('evaluation-split').value,method:$('evaluation-method').value,reference_id:reference,generate_answer:$('evaluation-answer').checked});const pollUrl=value.poll_url;while(value.status==='EVALUATION_PENDING'){status('Evaluationslauf arbeitet weiter. Status wird ohne erneute Berechnung abgefragt.');await new Promise(resolve=>setTimeout(resolve,2000));value=await api(pollUrl);}status(`Lauf beendet: ${value.summary.succeeded} erfolgreich, ${value.summary.failed} fehlgeschlagen.`);await reports();}finally{button.disabled=false;}}));
  Promise.all([catalog(),questions(),reports(),automaticFeedback()]).catch(error=>status(error.message));
})();
