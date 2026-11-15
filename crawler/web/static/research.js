'use strict';
(() => {
  const selected = new Map();
  function compareList(){
    const list=clear($('comparison-selection'));
    for(const [id,label] of selected){const li=node('li',label);li.append(button('Entfernen',()=>{selected.delete(id);compareList();}));list.append(li);}
    $('research-compare').value=[...selected.keys()].join(',');
  }
  $('comparison-add').onclick=()=>{
    const value=$('comparison-municipality').value;
    const id=municipalityId(value);
    const m=state.status?.municipalities.find(m=>m.ags===id);
    if(!m||selected.size+($('municipality').value?1:0)>=8){$('comparison-municipality').setCustomValidity('Bitte eine Gemeinde aus der Liste auswählen; maximal acht Gemeinden.');$('comparison-municipality').reportValidity();return;}
    selected.set(id,m.name);$('comparison-municipality').value='';compareList();
  };
  $('comparison-municipality').oninput=()=>$('comparison-municipality').setCustomValidity('');
  $('reset-filters').onclick=()=>{
    for(const id of ['municipality','research-region','topic','year','comparison-municipality'])$(id).value='';
    selected.clear();compareList();$('top-k').value='8';$('effective-scope').hidden=true;
    setStatus('idle','Filter zurückgesetzt. Die nächste Recherche verwendet die neue Auswahl.');
  };
  function currentScope(){
    const name=$('municipality').value, id=municipalityId(name);
    if(name&&!id)throw Error('Bitte eine Gemeinde aus der Rechercheauswahl wählen.');
    let ids=[...new Set([...(id?[id]:[]),...selected.keys()])];
    const region=$('research-region').value;
    if(region){const frame=state.status.municipalities.filter(m=>m.region===region).map(m=>m.ags);ids=ids.length?ids.filter(a=>frame.includes(a)):frame;if(!ids.length)throw Error('Gemeindeauswahl liegt außerhalb des Regierungsbezirks.');}
    return ids;
  }
  window.renderSearchDiagnosis=(r,root)=>{
    const d=r.retrieval_result.diagnostics;
    if(!d)return;
    if(r.research_product){window.renderProductDiagnosis?.(r,root);return;}
    if(r.retrieval_result.mode==='graph'){
      root.append(node('p',`Der Graph enthält ${d.graph_relations} vorläufige Relationen. Für diese Anfrage wurde keine belegte Relation gefunden. Der Teilgraph ist kein bayernweites Inventar; es wurde kein anderes Verfahren ausgeführt.`));return;
    }
    const expansion=d.expansion?.expanded_run;
    const list=node('ul');
    for(const text of [
      `Geprüfter Quellenrahmen: ${d.scope_sources} Quellenversionen, ${number(d.scope_indexed_units)} indexierte Texteinheiten.`,
      `Basissuche: ${d.candidates_after_municipality} lexikalische Kandidaten im gewählten Rahmen.`,
      ...(expansion?[`Explizite Erweiterung: ${expansion.candidates_after_municipality} Kandidaten im selben Gemeinderahmen. Begriffe: ${d.expansion.terms.join(', ')}.`]:[]),
      ...(d.text_year?[`Textjahr ${d.text_year}: ${d.after_year_filter} Passagen nach dem begrenzten Top-k-Filter.`]:[]),
    ])list.append(node('li',text));
    root.append(list,node('p','Der archivierte Bestand kann fachlich relevante Dokumente nicht enthalten. Prüfen Sie die Quellenabdeckung oder recherchieren Sie verwandte Begriffe. Eine Datenlücke ist keine Aussage über die tatsächliche kommunale Vorsorge.'));
    root.append(button('Quellenabdeckung prüfen',()=>{$('validation-catalog-ags').value=d.effective_ags.length===1?d.effective_ags[0]:'';route('validation',true);$('validation-catalog-details').open=true;loadValidationCatalog().catch(e=>{$('validation-status').textContent=e.message;});}));
  };
  window.initResearchRelease=s=>{
    const catalog=s.method_catalog;
    if(!catalog)return;
    $('feedback-save').hidden=false;$('feedback-history').hidden=false;
    const table=$('method-catalog'),head=node('thead'),row=node('tr');
    for(const title of ['Verfahren','Verfügbarkeit / Abdeckung','Ausführung']){const th=node('th',title);th.scope='col';row.append(th);}head.append(row);table.append(head);
    const body=node('tbody');
    for(const m of catalog.methods){
      const tr=node('tr');for(const value of [m.label,m.available?`${number(m.coverage)} ${m.unit}`:'Nicht als Freifrage-Verfahren verfügbar',m.description])tr.append(node('td',value));body.append(tr);
      if(m.available){const label=node('label'),input=node('input');input.type='checkbox';input.value=m.id;input.checked=m.id!=='graph';label.append(input,document.createTextNode(m.label));$('comparison-methods').append(label);}
    }
    table.append(body);
    for(const m of catalog.methods.filter(m=>m.available)){const option=[...$('mode').options].find(o=>o.value===m.id);if(option)option.textContent=m.label;}
    for(const m of catalog.methods.filter(m=>!m.available)){if(![...$('mode').options].some(o=>o.value===m.id)){const o=node('option',m.label+' — nicht verfügbar');o.disabled=true;o.value=m.id;$('mode').append(o);}}
    $('ie-methods').append(node('p',`${catalog.graph_entities} Entitäten, ${catalog.graph_relations} Relationen aus ${catalog.graph_sources} Quellenversionen. Vorläufige Extraktion; keine unabhängige fachliche Freigabe.`));
    dl($('ie-methods'),[['Graphversion',catalog.graph_version],['Korpussnapshot',catalog.snapshot]]);
    for(const run of catalog.ie_runs)dl($('ie-methods'),[['Extraktionsmodell / Regelpipeline',run.model],['Prompt- / Extraktionsversion',run.prompt_version]]);
    const details=node('details');details.append(node('summary','Verarbeitete Quellen-IDs'),node('pre',catalog.ie_source_ids.join('\n')));$('ie-methods').append(details);
    $('mode').addEventListener('change',()=>{const m=catalog.methods.find(m=>m.id===$('mode').value);$('method-note').textContent=m?`${m.description} Abdeckung: ${number(m.coverage)} ${m.unit}. Keine automatische Ausweichmethode.`:'';});
    $('mode').dispatchEvent(new Event('change'));
    for(const m of s.municipalities){const o=node('option',m.name+' · '+m.region);o.value=m.ags;$('validation-queue-ags').append(o);}
    $('comparison-question').value='Welche Quellen beschreiben die kommunale Wärmeplanung in Nennslingen?';
  };
  $('coverage-open').onclick=()=>$('validation-coverage').click();
  $('validation-coverage').addEventListener('click',()=>route('transparency',true));
  $('method-comparison').onsubmit=async e=>{
    e.preventDefault();const control=e.submitter;control.disabled=true;const output=clear($('comparison-results'));
    try{
      const methods=[...$('comparison-methods').querySelectorAll('input:checked')].map(x=>x.value);
      if(!methods.length)throw Error('Mindestens ein verfügbares Verfahren auswählen.');
      const ids=currentScope(), question=$('comparison-question').value.trim();
      const table=node('table'),head=node('tr');for(const label of ['Verfahren','Befund','Laufzeit','Quellenrahmen / Ergebnis'])head.append(node('th',label));table.append(head);output.append(table);
      for(const mode of methods){
        $('comparison-status').textContent=`Suche mit ${mode} …`;
        const start=performance.now();const result=await request('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,mode,municipality_ids:ids,topic:$('topic').value,reference_year:mode==='graph'?null:Number($('year').value)||null,top_k:Number($('top-k').value)||8})});
        const tr=node('tr');tr.append(node('td',state.status.method_catalog.methods.find(m=>m.id===mode).label),node('td',`${result.evidence.length} Passagen; fachlich unvalidiert`),node('td',`${Math.round(performance.now()-start)} ms`));
        const td=node('td'),scope=result.retrieval_result.search_scope;td.append(node('p',scope?.effective_ags.map(id=>state.status.municipalities.find(m=>m.ags===id)?.name||id).join(', ')||'Bayern'),node('p',mode==='graph'?'Teilgraph; kein Textjahrfilter ausgeführt':'Textjahr: '+($('year').value||'ohne Filter')));
        td.append(button('Evidenz untersuchen',()=>{state.result=result;route('workbench',true);$('mode').value=mode;$('mode').dispatchEvent(new Event('change'));$('question').value=question;displayResult(result);}));
        const detail=node('details');detail.append(node('summary','Laufdiagnose und Quellen-IDs'),node('pre',JSON.stringify(result.retrieval_result,null,2)));td.append(detail);tr.append(td);table.append(tr);
      }
      $('comparison-status').textContent='Vergleich abgeschlossen. Alle Ergebnisse stammen aus getrennten realen Suchläufen; keine Qualitätsrangliste.';
    }catch(err){$('comparison-status').textContent=err.message;}finally{control.disabled=false;}
  };
})();
