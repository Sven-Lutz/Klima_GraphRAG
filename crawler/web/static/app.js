'use strict';
const $=id=>document.getElementById(id);
const state={
  status:null,result:null,view:'workbench',selected:null,history:[],busy:false,relationFilter:''
}
;
function node(tag,text,cls){
  const n=document.createElement(tag);
  if(text!==undefined)n.textContent=text;
  if(cls)n.className=cls;
  return n;
}
function clear(n){
  n.replaceChildren();
  return n;
}
const number=n=>new Intl.NumberFormat('de-DE').format(n);
function button(text,fn,cls=''){
  const n=node('button',text,cls);
  n.type='button';
  n.onclick=fn;
  return n;
}
function safeURL(value){
  try{
    const u=new URL(value);
    if(!['https:','http:'].includes(u.protocol)||u.username||u.password||!u.hostname.includes('.')||u.hostname.endsWith('.local')||/^(127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)/.test(u.hostname))return null;
    return u;
  }
  catch{
    return null;
  }
}
function route(view,focus=false){
  const previous=state.view;
  if(view==='relations' && previous!=='relations') state.workbenchMode=$('mode').value;
  if(view!=='relations' && previous==='relations' && state.workbenchMode) {
    $('mode').value=state.workbenchMode;
  }
  state.view=view;
  $('mode').disabled=view==='relations';
  $('year').disabled=view==='relations'||$('mode').value==='graph';
  updateYearHint();
  for(const id of ['home','workspace','transparency','graph','feedback','validation','methods'])$(id).hidden=id!==(view==='workbench'||view==='relations'?'workspace':view);
  for(const b of document.querySelectorAll('nav [data-view]')){
    if(b.dataset.view===view)b.setAttribute('aria-current','page');
    else b.removeAttribute('aria-current');
  }
  if(view==='relations'){
    $('workspace-heading').textContent='Beziehungen';
    $('workspace-kicker').textContent='Akteur → Relation → Original';
    $('result-heading').textContent='Dokumentierte Beziehungen';
    $('results').hidden=true;
    $('relations').hidden=false;
    if(state.status?.supported_modes.includes('graph'))$('mode').value='graph';
    if(!state.busy) displayResult(state.result);
  }
  else if(view==='workbench'){
    $('workspace-heading').textContent='Recherche';
    $('workspace-kicker').textContent='Frage → Belegstelle → Original';
    $('result-heading').textContent='Belegstellen';
    $('results').hidden=false;
    $('relations').hidden=true;
    if(!state.busy) displayResult(state.result);
  }
  if(view==='graph') { $('graph-inspector-slot').append($('inspector')); window.openGraphExplorer?.(); }
  else if($('inspector').parentElement.id==='graph-inspector-slot') $('research-result').append($('inspector'));
  if(focus)$('main').focus();
  history.replaceState(null,'','#'+view);
}
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>route(b.dataset.view,true);
document.querySelector('.brand').onclick=e=>{
  e.preventDefault();
  route('workbench',true);
}
;
$('begin').onclick=()=>{
  route('workbench');
  $('question').focus();
}
;
function dl(parent,entries){
  const list=parent.tagName==='DL'?parent:node('dl');
  for(const [term,value] of entries){
    list.append(node('dt',term),node('dd',value==null||value===''?'Nicht qualifiziert / unbekannt':String(value)));
  }
  if(list!==parent)parent.append(list);
  return list;
}
async function request(url,options={
}
){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),17000);
  try{
    const r=await fetch(url,{
      ...options,signal:controller.signal,credentials:'same-origin'
    }
    );
    let data;
    try{
      data=await r.json();
    }
    catch{
      throw Object.assign(new Error('Die Antwort konnte nicht gelesen werden.'),{
        code:'error'
      }
      );
    }
    if(!r.ok)throw Object.assign(new Error(data.error?.message||'Die Anfrage ist fehlgeschlagen.'),{
      code:data.error?.code||'error'
    }
    );
    return data;
  }
  finally{
    clearTimeout(timer);
  }
}
function setStatus(kind,message){
  $('query-status').dataset.state=kind;
  $('query-status').textContent=message;
  $('query-form').classList.toggle('busy',kind==='searching');
  $('query-form').setAttribute('aria-busy',String(kind==='searching'));
}
function municipalityLabel(m){return `${m.name} · ${m.district} · ${m.region}`;}
function municipalityId(value){
  const matches=(state.status?.municipalities||[]).filter(m=>municipalityLabel(m)===value||m.name===value||m.ags===value.match(/\((\d{8})\)$/)?.[1]);
  return matches.length===1?matches[0].ags:null;
}
function municipalityValue(name){
  const m=state.status?.municipalities?.find(m=>m.name===name);
  return m?municipalityLabel(m):'';
}
async function init(){
  try{
    const s=await request('/api/status');
    state.status=s;
    $('local-mode').textContent=s.pilot_mode?'Research Preview · fachliche Validierung ausstehend':s.local_preview?'Nur lokal · keine Modellaufrufe':'Lokale Snapshot-Komposition';
    const modes={
      lexical:'Lexikalisch · kontextangereichertes BM25',lexical_expanded:'Lexikalisch erweitert · experimentell',graph:'Relationsgraph · quellengebundene Projektion',semantic:'Semantisch · Snapshot-Embedding',hybrid:'Hybrid · kanonisches Retrieval'
    }
    ;
    if(s.lexical_execution)modes.lexical='BM25 · persistenter Termindex (globale IDF)';
    clear($('mode'));
    for(const key of (s.research_product?.supported_modes||s.supported_modes)){
      const o=node('option',modes[key]||key);
      o.value=key;
      $('mode').append(o);
    }
    if(s.supported_modes.includes('lexical'))$('mode').value=s.local_validation_studio?'lexical_expanded':'lexical';
    $('method-note').textContent=s.local_preview?'Semantische und hybride Suche sind für diesen Bayern-Snapshot nicht eingerichtet. Graphrelationen bilden einen begrenzten, vorläufigen Teilkorpus ab.':'Kontrollierte Fixture-Komposition. Diese Daten belegen keine kommunale Klimapolitik.';
    const ready=Boolean(s.research_product)||s.provider_gate?.availability==='answer_generation_ready';
    $('generate').disabled=!ready;
    $('generate').closest('label').hidden=!ready;
    $('generate').checked=Boolean(s.research_product);
    if(!ready)$('generate').parentElement.append(node('small',' — hier nicht freigegeben'));
    for(const region of [...new Set((s.municipalities||[]).map(m=>m.region).filter(Boolean))].sort()){const o=node('option',region);o.value=region;$('research-region').append(o);}
    const c=s.coverage;
    if(c){
      $('frame-count').textContent=number(c.municipalities)+' Gemeinden';
      $('home-coverage').textContent=`${number(c.original_versions)} Quellenfassungen und ${number(c.text_units)} Texteinheiten. Textzuordnung für ${number(c.municipalities_with_text)} Gemeindeframes — keine Aussage über vollständige Klimapolitikabdeckung.`;
      dl(clear($('coverage')),[['Amtlicher Gemeinderahmen',number(c.municipalities)],['Frames mit zugeordnetem Text',number(c.municipalities_with_text)],['Quellenfassungen',number(c.original_versions)],['Texteinheiten',number(c.text_units)]]);
    }
    else{
      $('frame-count').textContent='Kontrollierter Testbestand';
      $('home-coverage').textContent=s.demo_notice||'Fixture, keine realen kommunalen Ergebnisse.';
      $('scope-label').textContent='Kontrollierte Fixture · keine reale Kommunalforschung';
    }
    for(const m of s.municipalities||[]){
      const o=node('option');
      o.value=municipalityLabel(m);
      $('municipalities').append(o);
    }
    clear($('examples'));
    const examples=s.examples||[{
      question:'Welche Quellen nennen Solarpläne?',municipality:null
    }
    ];
    for(const ex of examples){
      const b=button('',()=>{
        route('workbench');
        $('question').value=ex.question;
        $('municipality').value=municipalityValue(ex.municipality);
        $('topic').value='';
        $('year').value='';
        if(s.supported_modes.includes('lexical'))$('mode').value='lexical';
        $('mode').onchange();
        submit();
      }
      ,'example');
      b.append(node('span',ex.municipality||'Kontrollierte Fixture','place'),node('span',ex.question),node('span','Belegstellen finden ↗','arrow'));
      $('examples').append(b);
    }
    $('snapshot-short').textContent='Snapshot '+s.database_snapshot_id.slice(-16);
    dl(clear($('snapshot-meta')),[['Snapshot',s.database_snapshot_id],['Verfügbare Verfahren',s.supported_modes.join(' · ')],['Generierung',ready?'Nur nach strukturellem Grounding-Gate':'Nicht konfiguriert / deaktiviert'],['Graphprojektion',s.projection_id||'Nicht eingerichtet'],['Öffentliche Freigabe',s.public_release_cleared?'Freigabe separat nachgewiesen':s.pilot_mode?'Geschlossener Pilot; keine öffentliche Freigabe':'Nicht erteilt']]);
    $('scope-label').textContent=s.demo_mode?'Kontrollierte Fixture · keine reale Kommunalforschung':s.pilot_mode?'Freigegebene Pilot-Teilmenge · Bayern':'Bayern · versionierter Quellenbestand';
    if(s.local_validation_studio){$('validation-entity').hidden=false;initValidation().catch(e=>{$('validation-status').textContent=e.message;});}
    $('footer-state').textContent='Snapshotgebunden · keine automatische Fachvalidierung';
    if(s.research_preview?.source_presentation==='minimal_external_preview'){
      document.querySelector('.hero .lead').textContent='Fragen an einen kleinen realen Quellenbestand stellen. Die Ansicht zeigt eigene sachliche Darstellungen und vorläufige Beziehungen; Originalpassagen lesen Sie auf den verlinkten Veröffentlichungsseiten.';
      $('method-note').textContent='BM25 durchsucht intern den gebundenen Quelltext. Sichtbar sind ausschließlich vier eigene Tatsachendarstellungen und vier vorläufige Relationen. Eine gefundene Quelle bestätigt keine Antwort auf Ihre Frage.';
      $('footer-state').textContent='Privater Forschungspilot · eigenverantwortliche Einzelperson · keine Hochschulbereitstellung';
    }
    route(['graph','validation','methods','transparency','feedback'].includes(location.hash.slice(1))?location.hash.slice(1):'workbench');
    window.initResearchRelease?.(s);
    window.initProductResearch?.(s);
  }
  catch(e){
    $('frame-count').textContent='Nicht verfügbar';
    $('home-coverage').textContent='Der gebundene Forschungsdienst ist nicht erreichbar. Bitte starten oder später erneut versuchen.';
    const n=node('div',undefined,'notice error');
    n.setAttribute('role','alert');
    n.append(node('p','Verbindung zum Forschungsdienst nicht verfügbar.'),button('Verbindung erneut prüfen',()=>{
      n.remove();
      init();
    }
    ));
    $('home').prepend(n);
    route('home');
  }
}
function renderAnswer(r){
  clear($('answer-panel'));
  $('answer-panel').hidden=false;
  const a=r.answer_result;
  if(!a){
    $('answer-panel').append(node('strong','Belegsuche, keine generierte Antwort.'),node('p','Die folgenden Passagen sind Recherchehinweise. Zuständigkeit, zeitliche Geltung und Inhalt wurden nicht unabhängig fachlich bestätigt.'));
    return;
  }
  const g=a.grounding;
  const passed=a.status==='answered'&&g?.citation_resolution_success===true&&['malformed_citations','unknown_citations','unsupported_citations','uncited_claims'].every(k=>Array.isArray(g[k])&&g[k].length===0);
  if(!passed&&a.status==='insufficient_evidence'&&r.research_product){
    $('answer-panel').append(node('strong','Keine hinreichend belegbare Antwort.'),node('p','Die verfügbaren Passagen tragen keinen ausreichenden Antwortaspekt. Das belegt keine fehlende kommunale Maßnahme. Prüfen Sie Quellenumfang und Methodentrace.'));
    window.renderResearchAssessment?.(r);return;
  }
  if(!passed){
    $('answer-panel').append(node('strong','Keine freigegebene Antwort.'),node('p','Generierter Text wird ohne bestandenes strukturelles Grounding-Gate nicht angezeigt. Belegstellen bleiben verfügbar.'));
    return;
  }
  $('answer-panel').append(node('strong',r.research_product?'Zitierte Quellensynthese · vorläufig':'Ausformulierte Antwort · strukturell geprüft, nicht semantisch validiert'));
  const p=node('p');
  for(const part of r.inline_answer_parts||[]){
    if(part.kind==='citation'){
      p.append(button(part.text,()=>{
        const item=r.evidence.find(x=>x.citation?.label===part.label);
        if(item)inspect(item,true);
      }
      ));
    }
    else p.append(document.createTextNode(part.text));
  }
  $('answer-panel').append(p);
  window.renderResearchAssessment?.(r);
}
function renderResults(r){
  clear($('results'));
  $('result-count').textContent=`${r.evidence.length} ${r.external_contract?'eigene Tatsachendarstellungen':'Belegstellen'}`;
  if(state.status?.local_validation_studio)$('results').append(button('Rechercheergebnis validieren',()=>openValidation({kind:'retrieval',result_id:r.result_id})));
  if(!r.evidence.length){
    const empty=node('div',undefined,'empty');
    empty.append(node('h3','Keine Belegstellen im Suchumfang.'),node('p','Das ist kein Nachweis fehlender kommunaler Maßnahmen. Prüfen Sie Kommune, Formulierung, Methode oder Textjahr.'));
    $('results').append(empty);
    window.renderSearchDiagnosis?.(r,empty);
    clear($('inspector')).append(node('h2','Keine Belegstelle ausgewählt'),node('p','Eine nicht beantwortete Frage ist keine Widerlegung.'));
    if(state.status?.local_validation_studio&&$('mode').value==='lexical')$('results').append(button('Explizite lexikalische Erweiterung versuchen',()=>{$('mode').value='lexical_expanded';submit();}));
    return;
  }
  for(const item of r.evidence){
    const card=node('article',undefined,'evidence-card');
    card.dataset.id=item.evidence_id;
    const meta=node('div',undefined,'card-meta');
    meta.append(node('span',item.citation?.label||'Beleg '+item.rank),node('span',item.assertions.length?'Abgeleitete Relation · vorläufig':'Originalabschnitt · ungeprüfte Zuständigkeit'));
    card.append(meta,node('h3',(item.document.heading_context||'Originalabschnitt ohne qualifizierten Titel').slice(0,160)),node('p',item.evidence_text.slice(0,340)+(item.evidence_text.length>340?' …':'')),button('Quelle & Fundstelle prüfen →',()=>inspect(item,true)));
    $('results').append(card);
  }
  inspect(r.evidence[0],false);
}
function inspect(item,focus){
  state.selected=item.evidence_id;
  for(const c of document.querySelectorAll('.evidence-card'))c.classList.toggle('selected',c.dataset.id===item.evidence_id);
  const p=clear($('inspector'));
  const title=node('h2','Original & Fundstelle');
  title.id='inspector-title';
  p.append(node('p','Evidence Inspector · '+(item.citation?.label||'Beleg'),'eyebrow'),title);
  const status=item.assertions.length?'Abgeleitete Beziehung: vorläufige, nicht unabhängig validierte Extraktion.':'Archivierte Quellenfassung: Hashreferenz vorhanden. Keine automatische inhaltliche Bestätigung.';
  p.append(node('p',status,'notice'));
  const ownFact=item.source_metadata?.presentation==='OWN_FACTUAL_RESTATEMENT_NOT_SOURCE_QUOTE';
  p.append(node('h3',ownFact?'Eigene sachliche Darstellung':'Quellenausschnitt'));
  if(ownFact)p.append(node('p',item.source_metadata.association_notice,'notice'));
  const quote=node('div',item.evidence_text,'excerpt');
  quote.tabIndex=0;
  quote.setAttribute('aria-label',ownFact?'Eigene sachliche Darstellung; kein Originalzitat':'Vollständiger verfügbarer Quellenausschnitt');
  p.append(quote);
  if(state.status?.local_validation_studio){
    const sha=item.document.document_id.replace('source-version:','');
    p.append(button('Quelle validieren',()=>openValidation({kind:'source',key:sha})));
    const reviewStatus=node('p','Reviewstatus wird geprüft …','small');p.append(reviewStatus);
    request('/api/validation/status?'+new URLSearchParams({kind:'source',key:sha})).then(r=>reviewStatus.textContent='Explorativer Reviewstatus: '+r.decision+' · kein Human Gold').catch(()=>reviewStatus.textContent='Reviewstatus noch nicht geladen.');
    for(const assertion of item.assertions||[])if(assertion.assertion_id)p.append(button('Relation validieren',()=>openValidation({kind:'relation',key:assertion.assertion_id})));
  }
  const metadata=item.source_metadata||{
  }
  ,loc=metadata.locator;
  if(metadata.archive_url?.startsWith('/api/source/')){
    const a=node('a','Archivierte Quellenfassung öffnen ↗');
    a.href=metadata.archive_url+(loc?.kind==='pdf_page'&&/^\d+$/.test(String(loc.page_ref||''))?'#page='+loc.page_ref:'');
    a.target='_blank';
    a.rel='noopener noreferrer';
    a.className='archived-link';
    p.append(a,node('p','Archivversion wird vor der Auslieferung per SHA-256 geprüft. HTML wird passiv dargestellt; Bilder und Web-CSS können fehlen.','small'));
  }
  if(['linked_originals','minimal_external_preview'].includes(metadata.source_presentation))p.append(node('p','Keine Archivdatei wird bereitgestellt. Originalbytes wurden beim Export geprüft, hier nicht erneut geladen. Die verlinkte Website kann eine andere Version zeigen.','notice'));
  const u=safeURL(item.document.source_url);
  if(u){
    if(loc&&/^\d+$/.test(String(loc.page_ref||''))&&loc.kind==='pdf_page')u.hash='page='+loc.page_ref;
    const link=node('a','Veröffentlichungsseite öffnen ↗');
    link.href=u.href;
    link.target='_blank';
    link.rel='noopener noreferrer';
    link.className='original-link';
    p.append(link,node('p','Externer Link: Die Website kann inzwischen eine neuere Fassung zeigen.','small'));
  }
  else p.append(node('p','Kein sicherer öffentlicher Original-Link verfügbar.'));
  dl(p,[['Abschnitt / Kontext',item.document.heading_context],['Herausgeber',metadata.publisher],['Publikation',metadata.publication_date],['Abrufprotokoll',metadata.retrieved_at],['Quellendomäne',u?.hostname],['Fundstelle',loc?(loc.ordinal===null?(loc.page_ref?'Physische Seite '+loc.page_ref:'Originalveröffentlichung; keine Passage ausgeliefert'):`Einheit ${loc.ordinal}${loc.page_ref?' · physische Seite '+loc.page_ref:''}`):'Siehe Kontext und stabile Quellen-ID'],['Gemeinderahmen',item.document.municipality_ids.join(', ')],['Aussagegeltung','Nicht aus dem Quellenrahmen abgeleitet'],['Fachprüfung','Nicht unabhängig menschlich validiert']]);
  const details=node('details');
  details.append(node('summary','Version, Identität & Retrieval-Trace'));
  dl(details,[['Original-SHA-256',metadata.original_sha256||item.document.document_id],['Evidence-ID',item.evidence_id],['Source-ID',item.source_id],['Snapshot',item.database_snapshot_id],['Identitätsprüfung',metadata.identity_status==='EXPORT_VERIFIED_NO_RUNTIME_ORIGINAL'?'Originalbytes beim Export geprüft; hier nicht erneut geladen':metadata.identity_status==='ORIGINAL_REHASHED'?'Originalbytes in dieser Relationsabfrage geprüft':'Ledgerreferenz; Originalbytes hier nicht erneut geprüft']]);
  details.append(node('pre',JSON.stringify(item.retrieval_traces,null,2)));
  p.append(details,button('Zur Ergebnisliste ↑',()=>{
    $('result-heading').scrollIntoView({
      block:'start'
    }
    );
    const b=document.querySelector('.evidence-card.selected button');
    if(b)b.focus();
  }
  ,'text-button'));
  if(focus){
    p.scrollIntoView({
      block:'start',behavior:'auto'
    }
    );
    p.focus();
  }
}
function renderRelations(){
  clear($('relations'));
  const r=state.result;
  const all=(r?.graph?.edges||[]).filter(x=>x.kind==='assertion');
  const edges=all.filter(x=>!state.relationFilter||x.label===state.relationFilter);
  if(all.length){
    const label=node('label','Relationsart');
    const select=node('select');
    select.append(new Option('Alle dokumentierten Relationsarten',''));
    for(const kind of [...new Set(all.map(x=>x.label))].sort())select.append(new Option(relationLabel(kind),kind));
    select.value=state.relationFilter;
    select.onchange=()=>{
      state.relationFilter=select.value;
      renderRelations();
    }
    ;
    label.append(select);
    $('relations').append(label);
  }
  if(!edges.length){
    const empty=node('div',undefined,'empty');
    empty.append(node('h3','Noch keine fachlichen Beziehungen in dieser Auswahl.'),node('p','Dokument- und Passageverbindungen sind keine Maßnahmenrelationen. Der verfügbare Relationsbestand ist begrenzt und vorläufig.'));
    if(state.status?.supported_modes.includes('graph'))empty.append(button('Beziehungen im Teilkorpus suchen',()=>{
      $('mode').value='graph';
      $('municipality').value='';
      $('topic').value='';
      $('question').value=state.status.graph_example||'Welche Förderbeziehungen nennen die Quellen?';
      submit();
    }
    ));
    $('relations').append(empty);
    return;
  }
  $('relations').append(node('p',`${Math.min(edges.length,80)} von ${edges.length} Beziehungen dieser Auswahl · vorläufig, keine Wahrheitsgarantie.`,'small'));
  for(const e of edges.slice(0,80)){
    const source=r.graph.nodes.find(x=>x.id===e.source),target=r.graph.nodes.find(x=>x.id===e.target);
    const box=node('article',undefined,'relation');
    const row=node('div',undefined,'edge');
    row.append(node('strong',source?.label||e.source),node('span','→'),node('strong',target?.label||e.target));
    box.append(node('p',entityLabel(source?.entity_type)+' → '+entityLabel(target?.entity_type),'small'),row,node('p',relationLabel(e.label),'predicate'),node('p','Referenzzeit: '+(e.reference_time||'nicht belegt')+' · Aussagegeltung: '+(e.claim_scope_ags?.join(', ')||'nicht qualifiziert'),'small'),node('p','Akteurs-/Maßnahmenrolle nur entsprechend der benannten Relation; Identitätsauflösung vorläufig.','small'),button('Relation zur Originalevidenz verfolgen →',()=>{
      const item=r.evidence.find(x=>e.evidence_ids.includes(x.evidence_id));
      if(item){
        inspect(item,true);
        const others=r.evidence.filter(x=>e.context_evidence_ids?.includes(x.evidence_id)&&x.evidence_id!==item.evidence_id);
        if(others.length){
          const section=node('section');
          section.append(node('h3','Zusätzlich erforderlicher Quellenkontext'));
          for(const extra of others)section.append(button(extra.document.heading_context||'Kontextbeleg',()=>inspect(extra,true)));
          $('inspector').append(section);
        }
      }
    }
    ));
    $('relations').append(box);
  }
}
function displayResult(r){
  const isGraphView=state.view==='relations';
  if(!r || (isGraphView && r.retrieval_result.mode!=='graph')) {
    $('exports').hidden=true;
    $('effective-scope').hidden=true;
    $('answer-panel').hidden=true;
    $('result-count').textContent='';
    $('error').hidden=true;
    clear($('inspector')).append(node('h2','Die Quelle im Blick'),
      node('p','Wählen Sie nach der Recherche eine Belegstelle oder Beziehung.'));
    if(isGraphView) renderRelations();
    else if(!r) clear($('results')).append(node('p','Noch keine Recherche.','empty'));
    setStatus('idle',isGraphView?'Eine Beziehungsabfrage starten.':'Stellen Sie eine Frage.');
    return;
  }
  $('error').hidden=true;
  renderResults(r);
  renderAnswer(r);
  renderRelations();
  $('export-json').href=r.research_product?'/api/research/history/'+encodeURIComponent(r.result_id)+'/export/json':'/api/export/'+r.result_id+'.json';
  $('export-md').href=r.research_product?'/api/research/history/'+encodeURIComponent(r.result_id)+'/export/md':'/api/export/'+r.result_id+'.md';
  $('exports').hidden=false;
  const retrieval=r.retrieval_result, scope=retrieval.search_scope;
  const labels={explicit:'Ausgewählt',from_query:'Aus Frage erkannt',all:'Kein AGS-Filter'};
  const basis=scope?.basis==='provisional_claim_scope'?'vorläufige Aussagegeltung im Teilgraph':'Quellenzuordnung';
  const ids=scope?.effective_ags?.map(ags=>{
    const name=state.status?.municipalities?.find(m=>m.ags===ags)?.name;
    return name||ags;
  }).join(', ');
  $('effective-scope').hidden=false;
  $('effective-scope').textContent=`Letzter Suchlauf · ${retrieval.mode}`+
    (scope?` · ${labels[scope.resolution]}${ids?': '+ids:''} · ${basis}`:'')+
    (scope?.text_year?` · Textjahr ${scope.text_year}`:'');
  $('trace').textContent=JSON.stringify({diagnostics:retrieval.diagnostics,snapshot:retrieval.database_snapshot_id,
    mode:retrieval.mode,search_scope:scope,warnings:retrieval.warnings,
    source_ids:r.evidence.map(x=>x.source_id)},null,2);
  setStatus(!r.evidence.length?'empty':r.partial?'partial':'completed',
    !r.evidence.length?'Keine Belegstellen im begrenzten Suchumfang.':r.partial?
    'Teilmenge nach Textjahr gefiltert; keine Prüfung zeitlicher Gültigkeit.':
    `${r.evidence.length} Belegstellen gefunden · keine abgeschlossene fachliche Antwort.`);
}
async function submit(){
  if(!$('query-form').reportValidity())return;
  if(state.busy)return;
  const question=$('question').value.trim();
  if(!question){
    $('question').reportValidity();
    return;
  }
  state.busy=true;
  $('search').disabled=true;
  $('error').hidden=true;
  $('exports').hidden=true;
  $('effective-scope').hidden=true;
  setStatus('searching','Quellen werden im gebundenen Snapshot durchsucht …');
  const municipalityAgs=municipalityId($('municipality').value);
  const match=municipalityAgs?[null,municipalityAgs]:null;
  if($('municipality').value&&!match){
    $('error').hidden=false;
    $('error').textContent='Bitte eine eindeutige Gemeinde aus der Liste wählen.';
    setStatus('error','Gemeinde nicht eindeutig ausgewählt.');
    state.busy=false;
    $('search').disabled=false;
    return;
  }
  const previousResearchId=state.result?.result_id;
  state.result=null;
  clear($('results'));
  $('result-count').textContent='Recherche läuft …';
  clear($('relations'));
  clear($('inspector')).append(node('h2','Recherche läuft'));
  $('answer-panel').hidden=true;
  let selectedMunicipalities=match?[match[1]]:[];
  const comparison=$('research-compare').value.trim();
  if(comparison){const ids=comparison.split(',').map(x=>x.trim());if(ids.length>8||ids.some(id=>!state.status.municipalities.some(m=>m.ags===id))){state.busy=false;$('search').disabled=false;setStatus('error','Bitte bis zu acht gültige Gemeinde-AGS angeben.');return;}selectedMunicipalities=[...new Set([...selectedMunicipalities,...ids])];}
  const region=$('research-region').value;
  if(region){const regional=state.status.municipalities.filter(m=>m.region===region).map(m=>m.ags);selectedMunicipalities=selectedMunicipalities.length?selectedMunicipalities.filter(id=>regional.includes(id)):regional;if(!selectedMunicipalities.length){state.busy=false;$('search').disabled=false;setStatus('error','Gemeindeauswahl liegt außerhalb der gewählten Region.');return;}}
  const body={
    question,mode:$('mode').value,top_k:Number($('top-k').value)||8,max_evidence_items:8,max_evidence_characters:12000,generate_answer:$('generate').checked,municipality_ids:selectedMunicipalities,topic:$('topic').value,reference_year:!$('year').disabled&&$('year').value?Number($('year').value):null
  }
  ;
  if(state.status?.research_product&&$('research-followup')?.checked&&previousResearchId)body.previous_id=previousResearchId;
  try{
    let r=await request(state.status?.research_product?'/api/research/ask':'/api/query',{
      method:'POST',headers:{
        'Content-Type':'application/json'
      }
      ,body:JSON.stringify(body)
    }
    );
    if(r.status==='RESEARCH_PENDING'){
      const jobUrl='/api/research/jobs/'+encodeURIComponent(r.job_id);
      const deadline=Date.now()+150000;
      while(r.status==='RESEARCH_PENDING'){
        if(Date.now()>deadline)throw new Error('Lokale Synthese läuft länger als erwartet. Später den Forschungsverlauf öffnen.');
        setStatus('searching','Lokale Modellsynthese läuft; Quellen bleiben auf diesem Rechner.');
        await new Promise(resolve=>setTimeout(resolve,2000));
        r=await request(jobUrl);
      }
    }
    if(r.status==='CLARIFICATION_REQUIRED'){window.renderResearchClarification?.(r);setStatus('empty',r.message);return;}
    state.result=r;
    window.dispatchEvent(new CustomEvent('research-result',{detail:r}));
    state.relationFilter='';
    displayResult(r);
    state.history.unshift({
      ...body,municipality:$('municipality').value
    }
    );
    state.history=state.history.slice(0,8);
    clear($('history'));
    for(const h of state.history)$('history').append(button(h.question,()=>{
      route(h.mode==='graph'?'relations':'workbench');
      $('question').value=h.question;
      $('municipality').value=h.municipality;
      $('mode').value=h.mode;
      $('topic').value=h.topic;
      $('year').value=h.reference_year||'';
      $('top-k').value=h.top_k;
      $('generate').checked=h.generate_answer;
      $('year').disabled=h.mode==='graph';
      updateYearHint();
      submit();
    }
    ));
    $('history-panel').hidden=false;
  }
  catch(e){
    const code=e.name==='AbortError'?'timeout':e.code||'unavailable';
    const message=code==='ambiguous_municipality'?'Der Gemeindename ist mehrdeutig. Bitte die konkrete Gemeinde mit AGS aus der Liste auswählen.':code==='timeout'?'Die Suche hat das Antwortzeitbudget überschritten. Ein laufender Serverauftrag kann noch abschließen.':code==='rate_limited'||code==='busy'?'Das Suchbudget ist belegt. Bitte später erneut versuchen.':code==='method_unavailable_for_snapshot'?'Dieses Verfahren ist für den Snapshot nicht verfügbar.':'Die Recherche ist momentan nicht verfügbar. Ihre Frage bleibt erhalten.';
    $('result-count').textContent='Kein abgeschlossenes Ergebnis';
    clear($('inspector')).append(node('h2','Recherche nicht abgeschlossen'),node('p','Der technische Fehler ist kein Befund zur Evidenz. Wiederholen Sie die Suche, sobald der Dienst erreichbar ist.'));
    $('error').textContent=message;
    $('error').hidden=false;
    setStatus(code==='timeout'?'timeout':code==='error'?'error':'unavailable',message);
  }
  finally{
    state.busy=false;
    $('search').disabled=false;
  }
}
$('query-form').onsubmit=e=>{
  e.preventDefault();
  submit();
}
;
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'&&!$('workspace').hidden){
    e.preventDefault();
    submit();
  }
}
);
$('mode').onchange=()=>{
  const graph=$('mode').value==='graph';
  $('year').disabled=graph;
  updateYearHint();
  // Keep the prior lexical year while the graph method disables it.
}
;
init();

function relationLabel(key) {
  const labels={FUNDS:'Fördert / unterstützt',SUPPORTED_BY:'Unterstützt durch',
    PART_OF:'Teil von',OFFERS:'Bietet an',HAS_REPORTED_STATUS:'Berichteter Status',
    ON_SITE_DATE:'Vor-Ort-Termin',TELEPHONE_DATE:'Telefontermin',UPDATES:'Schreibt fort',
    REPORTED_DEVELOPMENT_YEAR:'Berichtetes Entwicklungsjahr',
    REPORTS_IMPLEMENTING:'Berichtet Umsetzung',CREDITS:'Nennt als Beteiligte',
    TARGETS:'Benanntes Ziel',ADMINISTERS:'Verwaltet',PLANS:'Plant'};
  return labels[key]?labels[key]+' · '+key:key;
}
function entityLabel(key) {
  return ({Organization:'Organisation',Project:'Projekt',Measure:'Maßnahme',
    Strategy:'Strategie',PolicyDocument:'Dokument',Municipality:'Gemeinde',
    Date:'Datum',Status:'Status',Actor:'Akteur',FundingProgramme:'Förderprogramm',
    AdministrativeBody:'Verwaltungsstelle',Objective:'Ziel'})[key]||key||'Entität';
}

function updateYearHint(){
  $('year-hint').textContent=$('year').disabled
    ?'Im Beziehungsmodus nicht angewendet'
    :'Jahresnennung, keine Gültigkeitsprüfung';
}

window.pilotFeedbackContext = () => ({
  software: state.status?.release_id || 'local-working-copy',
  snapshot: state.status?.database_snapshot_id || null,
  projection: state.status?.projection_id || null,
  question: $('question').value,
  result_id:state.result?.result_id||null,
  filters: {region:$('research-region').value, comparison:$('research-compare').value,municipality: $('municipality').value, topic: $('topic').value, method: $('mode').value, reference_year: $('year').value},
  evidence_ids: (state.result?.evidence || []).map(e => e.evidence_id),
  selected_relation_ids: window.klimaGraph?.graph?.edges(':selected').map(e=>e.id()) || [],
  graph: window.klimaGraph?.data ? {root:window.klimaGraph.data.root, subgraph_sha256:window.klimaGraph.data.subgraph_sha256} : null
});
