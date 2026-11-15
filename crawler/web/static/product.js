'use strict';
window.initProductResearch = async function(s) {
  if(!s.research_product)return;
  $('local-mode').textContent='Lokal · ohne externe Modellaufrufe';
  if(s.research_product.supported_modes.includes('hybrid'))$('mode').value='hybrid';
  const sourceMode=$('generate').closest('label');
  const usesModel=s.research_product.answer_provider==='ollama_grounded';
  sourceMode.append(node('small',usesModel?
    ' · experimentelle lokale Modellsynthese ('+s.research_product.answer_model+')':
    ' · lokale extraktive Synthese, keine LLM-Inferenz'));
  document.querySelector('.research-intro').textContent=usesModel?
    'Stellen Sie eine freie Forschungsfrage. Das explizit konfigurierte lokale Modell erstellt eine vorläufige Synthese mit überprüfbaren Quellenpassagen. Inhaltliche Stützung und Vollständigkeit sind nicht unabhängig validiert.':
    'Stellen Sie eine freie Forschungsfrage. Die lokale extraktive Synthese stellt zitierte Originalformulierungen zusammen. Sie ist keine vollständige generative Fachantwort.';
  $('privacy-description').textContent='Fragen, Antworten und Belegreferenzen werden lokal für diese Browsersitzung gespeichert und können exportiert werden. Ein Serverneustart löscht die Historie nicht. Andere Sitzungen haben keinen Zugriff auf diesen Verlauf. Es werden keine externen Modelle aufgerufen. Ohne die dokumentierten Freigaben bleibt der externe Betrieb gesperrt.';
  $('method-note').textContent='Die Recherche plant Ortsrahmen und Teilfragen und erstellt belegte Quellenauszüge. Original, zitierte Aussage und Geltungsgrenzen bleiben getrennt. Wissenschaftliche Vergleichsmethoden sind im Methodenvergleich unverändert verfügbar.';
  const label=node('label',undefined,'check');
  const check=node('input');check.type='checkbox';check.id='research-followup';
  label.append(check,document.createTextNode(' Rückfrage zum letzten Rechercheergebnis (Kontext übernehmen)'));
  $('query-form').append(label);
  const actions=node('div',undefined,'graph-toolbar');
  for(const [text,path] of [['Unabhängige Human-Validierung','/review'],['Evaluation & Diagnosen','/evaluation'],['Bestand & Quellenlücken','/coverage']]) {
    const a=node('a',text);a.href=path;actions.append(a);
  }
  const historyPanel=node('details');historyPanel.id='persistent-research';
  historyPanel.append(node('summary','Gespeicherte Forschungshistorie dieses Browserprofils'),node('p','Fragen, Antworten und Belegreferenzen werden lokal unter dieser Browsersitzung gespeichert. Andere Sitzungen sehen diese Historie nicht. Die Datei enthält unvalidiertes Forschungsmaterial.','small'));
  const list=node('div');list.id='persistent-research-list';historyPanel.append(list);
  $('history-panel').after(historyPanel);historyPanel.before(actions);
  await loadResearchHistory();
};
async function loadResearchHistory(){
  if(!$('persistent-research-list'))return;
  try {
    const history=await request('/api/research/history');
    clear($('persistent-research-list'));
    if(!history.items.length)$('persistent-research-list').append(node('p','Noch keine gespeicherte Recherche in dieser Sitzung.'));
    for(const h of history.items)$('persistent-research-list').append(button(h.question,async()=>{
      try{
        const r=await request('/api/research/history/'+encodeURIComponent(h.id));
        state.result=r; $('question').value=r.query_plan.original;
        route('workbench');displayResult(r);
      }catch(e){setStatus('error',e.message);}
    }));
  }catch(e){$('persistent-research-list').textContent=e.message;}
}
window.addEventListener('research-result',()=>loadResearchHistory());
window.renderResearchClarification=function(r){
  clear($('results')).append(node('h2','Ort präzisieren'),node('p',r.message));
  for(const c of r.candidates)$('results').append(button(c.name+(c.district?' · '+c.district:''),()=>{
    const m=state.status.municipalities.find(m=>m.ags===c.ags);
    $('municipality').value=municipalityLabel(m);submit();
  }));
};
window.renderResearchAssessment=function(r){
  if(!r.research_product)return;
  const a=r.answer_assessment;
  const notes=node('aside',undefined,'notice');
  const generative=a?.method?.startsWith('evidence-bound-claims-json-');
  notes.append(node('strong','Vorläufige Quellensynthese'),node('p',generative?
    'Experimentelle lokale Modellsynthese. Die Zitatpassagen sind mechanisch geprüft; die inhaltliche Stützung, Vollständigkeit sowie geografische und zeitliche Geltung sind nicht unabhängig validiert.':
    'Die Abschnitte sind extraktiv zusammengestellte Originalformulierungen. Sie liefern einzelne Antwortaspekte. Vollständigkeit, individuelle Gemeinde- und zeitliche Geltung sind damit nicht bestätigt.'));
  for(const warning of a?.warnings||[])notes.append(node('p',warning,'small'));
  const trace=node('details');trace.append(node('summary','Query-Plan und Methodentrace'));
  trace.append(node('pre',JSON.stringify({query_plan:r.query_plan,retrieval:r.retrieval_result.diagnostics,answer_synthesis:{method:a?.method,sentence_selection:a?.sentence_selection,model_validation:a?.model_validation}},null,2)));
  const claims=node('details');claims.append(node('summary',`${a?.claims?.length||0} Aussagen einzeln prüfen und zur Validierung übergeben`));
  const claimTypes={TARGET_OR_PLAN:'Ziel oder Planung',PROJECTION_OR_POTENTIAL:'Prognose oder Potenzial',REPORTED_IMPLEMENTATION:'Berichtete Umsetzung',REPORTED_MEASUREMENT_NOT_CAUSAL_PROOF:'Berichtete Messung · kein Kausalnachweis',REPORTED_QUANTITY_MEASUREMENT_STATUS_UNCONFIRMED:'Berichteter Wert · Messstatus ungeprüft',DOCUMENTED_SOURCE_STATEMENT:'Dokumentierte Quellenaussage'};
  for(const [i,c] of (a?.claims||[]).entries()){
    const item=node('article',undefined,'evidence-card');
    const reviewText=c.review?.state==='CONFLICTING_CONSOLIDATED_REVIEWS'?
      'widersprüchliche konsolidierte Urteile · kein gültiges Goldurteil':
      c.review?.state==='REVIEW_DEFERRED'?'Prüfung zurückgestellt · kein belastbares Inhaltsurteil':
      c.review?.human_gold?'unabhängig annotiert'+(c.review.decision?' · '+c.review.decision:' · Urteil nur im berechtigten Prüfzugang'):
      c.review?'synthetisches Testurteil · kein Human Gold':'nicht unabhängig geprüft';
    item.append(node('p',c.text),node('p',(claimTypes[c.type]||c.type)+(generative?' · lokale Modellsynthese':' · automatisch extraktiv')+' · '+reviewText,'small'));
    const link=node('a','Unbelegte oder unklare Aussage zur Prüfung übergeben');
    link.href='/review#result='+encodeURIComponent(r.result_id)+'&claim='+i;
    item.append(link);claims.append(item);
  }
  const exportLink=node('a','Antwort, Evidenz und Query-Plan als JSON exportieren');
  exportLink.href='/api/research/history/'+encodeURIComponent(r.result_id)+'/export/json';
  exportLink.download='klima-research.json';
  $('answer-panel').append(notes,claims,trace,exportLink);
};

window.renderProductDiagnosis=function(r,root){
  root.append(node('p','Kein hinreichender Beleg im gewählten Verfahren. Der Nichtfund beschreibt den gebundenen Quellenbestand und belegt keine fehlende kommunale Maßnahme.'));
  const list=node('ul');
  for(const arm of r.retrieval_result.diagnostics.arms||[]){
    const d=arm.diagnostics;
    list.append(node('li',arm.method+': '+(d?`${d.scope_sources??'unbekannt'} Quellen, ${d.scope_indexed_units??'unbekannt'} Texteinheiten, ${d.candidates_after_municipality??'unbekannt'} Kandidaten`:`${arm.selected_before} Relationen, ${arm.selected_after} mit thematischem Anker`)));
  }
  root.append(list,button('Quellenabdeckung prüfen',()=>{
    const scope=r.query_plan.municipality_ids;
    $('validation-catalog-ags').value=scope.length===1?scope[0]:'';route('validation',true);
    $('validation-catalog-details').open=true;loadValidationCatalog();
  }));
};
