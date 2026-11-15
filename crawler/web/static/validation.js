'use strict';
const validationState={ready:false,object:null,session:null,offset:0,total:0};
async function validationSession(code){
  validationState.session=await request('/api/validation/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(code?{code}:{})});
  validationState.ready=true;
  $('validation-role').textContent=`Explorative Prüfung · Rolle ${validationState.session.role} · ${validationState.session.technical?'TECHNISCHER TEST, human=false':'Selbsterklärte persönliche Prüfung; kein Human Gold'}`;
}
async function openValidation(body){
  try{
    if(!validationState.ready)await validationSession();
    const r=await request('/api/validation/handoff',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    route('validation',true);
    await loadValidationObject(r.object_id);
    await loadValidationQueue();
  }catch(e){$('validation-status').textContent=e.message;}
}
async function loadValidationObject(id){
  const obj=await request('/api/validation/object/'+encodeURIComponent(id));
  validationState.object=obj;
  $('validation-catalog-details').open=false;
  renderValidationSummary(obj);
  $('validation-object-id').textContent=id;
  $('validation-kind').textContent=`${obj.kind} · ${obj.split} · keine unabhängige Goldvalidierung · Technische Integrität: ${obj.technical_integrity}`;
  $('validation-payload').textContent=JSON.stringify(obj.payload,null,2);
  const links=clear($('validation-originals'));
  const shas=new Set();
  if(obj.kind==='source')shas.add(obj.key);
  for(const e of obj.payload.evidence||[])if(e.source_sha256)shas.add(e.source_sha256);
  for(const sha of shas){
    const a=node('a','SHA-geprüftes Original öffnen ↗');a.href='/api/validation/original/'+sha;a.target='_blank';a.rel='noopener noreferrer';links.append(a);
  }
  validationState.unitOffset=0;
  await loadValidationUnits();
  const last=obj.history.at(-1)?.payload;
  $('validation-quote').value=last?.evidence_quote||'';
  const ratings=clear($('validation-ratings'));
  for(const dimension of validationState.session.dimensions[obj.kind]){
    const label=node('label',validationLabels[dimension]||dimension.replaceAll('_',' '));
    const select=node('select');select.name=dimension;select.setAttribute('aria-label',dimension);
    for(const value of validationState.session.options?.[obj.kind]?.[dimension]||validationState.session.ratings){const option=node('option',validationLabels[value]||value);option.value=value;select.append(option);}
    select.value=last?.ratings[dimension]||'NOT_JUDGED';label.append(select);ratings.append(label);
  }
  $('validation-decision').value=last?.decision||'NOT_JUDGED';
  $('validation-reviewer').value=last?.reviewer||$('validation-reviewer').value;
  $('validation-comment').value=last?.comment||'';
  $('validation-corrections').value=last?.corrections||'';
  $('validation-confirm').checked=false;
  $('validation-form').hidden=false;
  $('validation-adjudicate').hidden=validationState.session.role!=='C'||!obj.conflict;
  $('validation-history').textContent=JSON.stringify({own_or_owner_visible:obj.history,adjudications:obj.adjudications},null,2);
  $('validation-kind').tabIndex=-1;$('validation-kind').focus();
  $('validation-status').textContent=`Objekt geöffnet · ${obj.history.length} sichtbare Entscheidungsversionen${obj.conflict?' · Konflikt zur Adjudikation':''}.`;
}
async function loadValidationQueue(){
  const query=new URLSearchParams({status:$('validation-queue-filter').value,kind:$('validation-kind-filter').value,ags:$('validation-queue-ags').value,region:$('validation-queue-region').value,document_type:$('validation-queue-type').value});
  const data=await request('/api/validation/queue?'+query);
  const list=clear($('validation-queue'));
  const kindLabels={source:'Quelle',entity:'Entität',relation:'Relation',retrieval:'Rechercheergebnis'},statusLabels={NOT_JUDGED:'Offen',REVIEWED:'Geprüft',CONFLICT:'Konflikt',OVERDUE:'Überfällig',ADJUDICATED:'Adjudiziert'};
  for(const row of data.items)list.append(button(`${row.label||row.key.slice(0,45)} · ${kindLabels[row.kind]||row.kind} · ${statusLabels[row.status]||row.status}`,()=>row.registered===false?openValidation({kind:row.kind,key:row.key}):loadValidationObject(row.object_id)));
  if(!data.items.length)list.append(node('p','Keine Aufgaben in diesem Filter. Quellen können aus dem gesamten Katalog hinzugefügt werden.'));
}
async function loadValidationCatalog(){
  if(!validationState.ready)await validationSession();
  const query=new URLSearchParams({q:$('validation-catalog-q').value,ags:$('validation-catalog-ags').value,region:$('validation-catalog-region').value,kind:$('validation-catalog-kind').value,suspect:$('validation-catalog-suspect').checked,offset:validationState.offset});
  const data=await request('/api/validation/catalog?'+query);
  validationState.total=data.total;
  $('validation-catalog-count').textContent=`${data.total} Quellen · SHA-Reihenfolge, keine repräsentative Zufallsstichprobe · ab ${data.offset+1}`;
  const list=clear($('validation-catalog'));
  for(const row of data.items){const article=node('article',undefined,'evidence-card');article.append(node('h3',row.url||row.sha256),node('p',`${row.document_type_hint} · ${row.unit_count} Texteinheiten · ${row.status}`),button('Quelle zur Prüfung öffnen',()=>openValidation({kind:'source',key:row.sha256})));list.append(article);}
  $('validation-next').disabled=validationState.offset+30>=data.total;
  $('validation-prev').disabled=validationState.offset===0;
}
async function initValidation(){
  if(!state.status?.local_validation_studio)return;
  $('validation-nav').hidden=false;
  await validationSession();
  for(const [ags,m] of state.status.municipalities.map(m=>[m.ags,m])){const option=node('option',`${m.name} (${ags})`);option.value=ags;$('validation-catalog-ags').append(option);}
  const regions=[...new Set(state.status.municipalities.map(m=>m.region))].sort();
  for(const region of regions){const option=node('option',region);option.value=region;$('validation-catalog-region').append(option);$('validation-queue-region').append(option.cloneNode(true));}
  await loadValidationQueue();
}
$('validation-catalog-form').onsubmit=async e=>{e.preventDefault();try{validationState.offset=0;await loadValidationCatalog();}catch(err){$('validation-status').textContent=err.message;}};
$('validation-next').onclick=async()=>{validationState.offset+=30;await loadValidationCatalog();};
$('validation-prev').onclick=async()=>{validationState.offset=Math.max(0,validationState.offset-30);await loadValidationCatalog();};
$('validation-queue-filter').onchange=loadValidationQueue;
$('validation-kind-filter').onchange=loadValidationQueue;
$('validation-refresh').onclick=loadValidationQueue;
$('validation-login').onsubmit=async e=>{e.preventDefault();try{await validationSession($('validation-code').value);$('validation-code').value='';await loadValidationQueue();if(validationState.object)await loadValidationObject(validationState.object.object_id);}catch(err){$('validation-status').textContent=err.message;}};
$('validation-form').onsubmit=async e=>{
 e.preventDefault();try{
 const ratings={};for(const input of $('validation-ratings').querySelectorAll('select'))ratings[input.name]=input.value;
 const form={reviewer:$('validation-reviewer').value,independence:$('validation-independence').value,human_confirmed:$('validation-confirm').checked,decision:$('validation-decision').value,ratings,comment:$('validation-comment').value,corrections:$('validation-corrections').value,evidence_quote:$('validation-quote').value,adjudicate:e.submitter?.id==='validation-adjudicate'};
 await request('/api/validation/review/'+encodeURIComponent(validationState.object.object_id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(form)});
 await loadValidationObject(validationState.object.object_id);await loadValidationQueue();$('validation-status').textContent='Entscheidung persistent gespeichert. Originaldaten unverändert; kein Human Gold.';
 }catch(err){$('validation-status').textContent=err.message;}
};
$('validation-export').onclick=async()=>{try{const data=await request('/api/validation/export');const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const a=node('a');a.href=URL.createObjectURL(blob);a.download='klima-exploratory-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}catch(e){$('validation-status').textContent=e.message;}};
$('validation-import').onchange=async e=>{try{const file=e.target.files[0];if(!file||file.size>8*1024*1024)throw new Error('Import maximal 8 MiB.');await request('/api/validation/import',{method:'POST',headers:{'Content-Type':'application/json'},body:await file.text()});await loadValidationQueue();if(validationState.object)await loadValidationObject(validationState.object.object_id);$('validation-status').textContent='Snapshot geprüft und importiert; keine Gold-Promotion.';}catch(err){$('validation-status').textContent=err.message;}};
$('validation-coverage').onclick=async()=>{
 const data=await request('/api/validation/coverage'),root=clear($('validation-coverage-result'));
 root.closest('details').open=true;
 root.append(node('p',`${data.municipalities.length} Gemeinden · ${data.municipalities.filter(m=>m.searchable).length} mit indexiertem Text. Dateien vorhanden, Hashprüfung und Human Review sind getrennte Nachweise.`));
 root.append(node('p','Quellenarten: '+(data.source_types||[]).map(t=>`${t.document_type}: ${t.source_versions} Versionen`).join(' · ')));
 const regionLabel=node('label','Regierungsbezirk'),region=node('select');region.append(node('option','Alle Regierungsbezirke'));region.options[0].value='';for(const value of [...new Set(data.municipalities.map(m=>m.region))].sort()){const o=node('option',value);o.value=value;region.append(o);}regionLabel.append(region);root.append(regionLabel);
 const label=node('label','Gemeinde oder AGS filtern'),query=node('input');query.type='search';label.append(query);root.append(label);
 const gapsLabel=node('label','Nur Gemeinden ohne indexierten Text','check'),gaps=node('input');gaps.type='checkbox';gapsLabel.prepend(gaps);root.append(gapsLabel);
 const info=node('p'),scroller=node('div',undefined,'coverage-scroll'),table=node('table');scroller.tabIndex=0;scroller.setAttribute('aria-label','Gemeindeabdeckung; Tabelle bei Bedarf horizontal scrollen');scroller.append(table);root.append(info,scroller);
 const controls=node('div',undefined,'graph-toolbar'),previous=button('Vorherige Gemeinden',()=>{offset=Math.max(0,offset-30);draw();}),next=button('Weitere Gemeinden',()=>{offset+=30;draw();});controls.append(previous,next);root.append(controls);
 let offset=0;
 function draw(){
  const rows=data.municipalities.filter(m=>(!region.value||m.region===region.value)&&(!gaps.checked||!m.searchable)&&(!query.value||(m.name+' '+m.ags+' '+m.region).toLocaleLowerCase('de').includes(query.value.toLocaleLowerCase('de'))));
  clear(table);const caption=node('caption','Quellenrahmen im eingefrorenen Bayern-Snapshot'),head=node('thead'),tr=node('tr');
  for(const text of ['Gemeinde','Region','Quellen','Dateien vorhanden','Texteinheiten / Index','Graphquellen / vorläufige Relationen','Persönliche Quellenreviews']){const th=node('th',text);th.scope='col';tr.append(th);}head.append(tr);table.append(caption,head);
  const body=node('tbody');for(const m of rows.slice(offset,offset+30)){const row=node('tr');for(const text of [m.name+' ('+m.ags+')',m.region,m.sources,m.archive_files_present??'Nicht geprüft',m.indexed_units,`${m.graph_source_versions} / ${m.graph_claim_relations}`,m.human_source_reviews])row.append(node('td',String(text)));body.append(row);}table.append(body);
  info.textContent=`${rows.length} passende Gemeinden · ${Math.min(offset+1,rows.length)}–${Math.min(offset+30,rows.length)}. Fehlender Text ist keine Aussage über fehlende Klimapolitik.`;previous.disabled=offset===0;next.disabled=offset+30>=rows.length;
 }
 region.onchange=()=>{offset=0;draw();};query.oninput=()=>{offset=0;draw();};gaps.onchange=()=>{offset=0;draw();};draw();
};
$('validation-entity').onclick=()=>{const key=$('graph-search').value.match(/\(([^()]+)\)$/)?.[1];if(key)openValidation({kind:'entity',key});};

async function loadValidationUnits(){
 const obj=validationState.object;
 if(obj?.kind!=='source'){$('validation-units').textContent='Evidenz für dieses Objekt steht in der unveränderten Objektansicht.';return;}
 const data=await request('/api/validation/units/'+obj.key+'?offset='+validationState.unitOffset);
 $('validation-units').textContent=data.units.map(u=>`Unit-ID ${u.unit_id} · Einheit ${u.ordinal} · Seite ${u.page_ref||'—'}\n${u.heading||''}\n${u.text}`).join('\n\n');
 $('validation-units-prev').disabled=validationState.unitOffset===0;
 $('validation-units-next').disabled=data.units.length<10;
}
$('validation-units-prev').onclick=async()=>{validationState.unitOffset=Math.max(0,validationState.unitOffset-10);await loadValidationUnits();};
$('validation-units-next').onclick=async()=>{validationState.unitOffset+=10;await loadValidationUnits();};

const validationLabels={publisher:'Institutioneller Herausgeber',municipality:'Gemeindezuordnung',document_identity:'Dokumentidentität',temporal:'Zeitbezug',document_type:'Dokumenttyp',provenance:'Provenienz',duplicates:'Dubletten',extraction:'Extraktionsqualität',scientific_suitability:'Wissenschaftliche Eignung',identity:'Entitätsidentität',type:'Entitätstyp',geography:'Geografische Geltung',evidence_support:'Tragfähigkeit der Evidenz',entity_roles:'Akteursrollen',relation_type:'Relationstyp',missing_relations:'Fehlende Relationen',relevance:'Relevanz',completeness:'Vollständigkeit',factual_correctness:'Faktische Richtigkeit',citation_support:'Quellenstützung',contradictions:'Widersprüche',missing_evidence:'Fehlende Evidenz',unsupported_claims:'Unbelegte Aussagen',NOT_JUDGED:'Noch nicht beurteilt',SUPPORTED:'Belegt',CONTRADICTED:'Widersprochen',UNCERTAIN:'Unklar',NOT_APPLICABLE:'Nicht anwendbar',RELEVANT:'Relevant',PARTIAL:'Teilweise',IRRELEVANT:'Nicht relevant',VALID:'Zutreffend',INVALID:'Fehlerhaft',MISSING:'Fehlt',COMPLETE:'Vollständig im geprüften Rahmen',INCOMPLETE:'Unvollständig',DETECTED:'In dieser Prüfung festgestellt',NOT_DETECTED:'In dieser Prüfung nicht festgestellt'};
function renderValidationSummary(obj){
 const root=clear($('validation-summary')),p=obj.payload;
 const frame=ids=>(ids||[]).map(id=>{const m=state.status.municipalities.find(m=>m.ags===id);return m?`${m.name} (${id})`:id;}).join(' · ');
 if(obj.kind==='source'){
   root.append(node('h2','Quellenfassung prüfen'));
   const link=node('a',p.url||'Original-Link unbekannt');const safe=safeURL(p.url);if(safe){link.href=safe.href;link.target='_blank';link.rel='noopener noreferrer';}root.append(link);
   dl(root,[['Quellenrahmen',frame(p.municipality_ids)],['Texteinheiten',p.unit_count],['Parserstatus',p.status],['Herausgeber / Datum','Im Original prüfen; nicht automatisch qualifiziert'],['Dokumentversion',obj.key],['Wissenschaftliche Validierung','Noch nicht beurteilt'],['Externe Weitergabe','Separat ungeprüft; ausschließlich lokal']]);
 }else if(obj.kind==='entity'){
   root.append(node('h2',p.entity.label),node('p','Entitätstyp: '+p.entity.entity_type),node('p','Vorläufige Extraktion; die Rolle ist im Original zu überprüfen.'));
 }else if(obj.kind==='relation'){
   root.append(node('h2',p.subject.label+' → '+p.object.label),node('p','Gespeicherte Relation: '+p.relation.predicate),node('p','Vorläufige Extraktion; Richtung, Akteursrollen, Zeit und Geltung getrennt prüfen.'));
 }else{
   root.append(node('h2',p.query||p.retrieval_result?.query||'Rechercheergebnis'),node('p','Relevanz, Vollständigkeit und Quellenstützung getrennt beurteilen. Eine Rangposition ist kein fachliches Urteil.'));
 }
 for(const item of p.evidence||p.items||[]){
   const card=node('details');card.append(node('summary',item.heading||item.document?.heading_context||'Evidenzstelle '+(item.unit_id||item.source_id||'')),node('p',item.text||item.evidence_text||''));
   if(item.unit_id||item.source_id)card.append(node('p','Stabile Evidenzreferenz: '+(item.unit_id||item.source_id),'small'));
   const sha=item.source_sha256||item.document?.document_id?.replace('source-version:','');
   if(sha){const a=node('a','Originalquelle öffnen ↗');a.href='/api/validation/original/'+sha;a.target='_blank';a.rel='noopener noreferrer';card.append(a);}
   root.append(card);
 }
}
$('validation-use-selection').onclick=()=>{const quote=window.getSelection()?.toString().trim();if(quote&&quote.length<=4000){$('validation-quote').value=quote;$('validation-status').textContent='Span übernommen. Zugehörigkeit wird beim Speichern geprüft.';}else $('validation-status').textContent='Bitte bis zu 4.000 Zeichen aus der Evidenz markieren.';};

for(const id of ['validation-queue-ags','validation-queue-region','validation-queue-type'])$(id).onchange=()=>loadValidationQueue().catch(e=>{$('validation-status').textContent=e.message;});
