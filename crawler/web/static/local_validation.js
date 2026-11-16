"use strict";
const $ = id => document.getElementById(id);
let csrf = "", items = [], events = [], index = 0, timer, version = 0, labels = {}, activeKind = "claim";
async function api(path, method="GET", data, retried=false) {
  const r=await fetch("/api/chat/"+path,{method,headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:data?JSON.stringify(data):undefined});
  if(r.status===401){ await init(); throw Error("Sitzung erneuert. Bitte einen aktuellen Beleg auswählen."); }
  if(r.status===403 && !retried) {
    const sessionResponse=await fetch('/api/chat/session');
    if(!sessionResponse.ok) throw Error('Lokale Sitzung derzeit nicht verfügbar.');
    const session=await sessionResponse.json(); csrf=session.csrf;
    if(session.created || session.recovered){await init();throw Error('Neue Sitzung. Bitte einen aktuellen Beleg auswählen.');}
    return api(path,method,data,true);
  }
  if(!r.ok) throw Error("Die lokale Bewertung konnte nicht gespeichert werden. Bitte erneut versuchen.");
  return r.json();
}
const guides={source:"Relevanz, Zuständigkeit, Gemeinde und Quellenfassung prüfen; das ist kein Claim-Support-Urteil.",claim:"Trägt die Originalpassage genau diese Aussage einschließlich Ort und Zeit?",extraction:"Wert, Einheit, Akteursrolle, Ort und Zeit der Extraktion prüfen. Unknown nicht ergänzen.",relation:"Endpunkte, Relationstyp, Rolle, Zeit und Geltung prüfen. Graphverbindung ist keine Kausalität.",answer_evidence:"Beantwortet die gesamte Antwort die Frage vollständig, relevant und im richtigen Geltungsbereich?"};
function show(){activeKind=$("kind").value;$("label").replaceChildren(new Option('Noch keine Bewertung',''),...(labels[activeKind]||[]).map(x=>new Option(x,x)));version++;clearTimeout(timer); const item=items[index]; $("progress").textContent=items.length?`${index+1} / ${items.length} Belege · ${events.filter(e=>e.state==='judgment').length} lokale Urteilsversionen`:"Noch keine Belege. Zuerst im Chat eine Frage stellen."; $("next").disabled=!item; $("previous").disabled=index===0;
 if(!item)return;
 $("question").textContent=item.question; $("claim").textContent=$("kind").value==='answer_evidence'?item.answer:item.claim.text;
 $("original").textContent=item.claim.evidence.map(e=>e.quote).join("\n\n");
 $("provenance").textContent=`Snapshot: ${item.snapshot} · ${item.claim.evidence_kind} · UNVALIDATED`;
 $("details").textContent=JSON.stringify(item.claim.evidence,null,2);
 const url=item.claim.evidence[0]?.source_url; $("source-link").hidden=true;
 try {const a=item.claim.evidence[0]; const u=new URL(url);
  if(a.source_sha256){$("source-link").href=`/api/chat/conversations/${item.conversation_id}/original/${item.turn_id}/${encodeURIComponent(a.evidence_id)}`;$("source-link").textContent="Archiviertes Original öffnen";$("source-link").hidden=false;}
  else if(['https:','http:'].includes(u.protocol)&&!u.hostname.endsWith('.test')){$("source-link").href=u.href;$("source-link").textContent="Onlinequelle öffnen (Fassung kann abweichen)";$("source-link").hidden=false;}
 }catch{}
 $("task-guide").textContent=guides[$("kind").value];
 const last=events.findLast(e=>e.turn_id===item.turn_id&&e.claim_id===item.claim.claim_id&&e.kind===$("kind").value);
 $("label").value=last?.label||"";$("comment").value=last?.comment||"";
}
async function save(state, kind = activeKind){ const item=items[index]; if(!item)return;
 const event=await api("local-review","POST",{conversation_id:item.conversation_id,turn_id:item.turn_id,claim_id:item.claim.claim_id,kind,label:$("label").value||null,comment:$("comment").value,state});events.push(event);$("status").textContent=state==='draft'?"Entwurf gespeichert · kein abgegebenes Urteil":"Lokales Urteil versioniert gespeichert · kein Human Gold";
}
async function init(){const r=await fetch('/api/chat/session');if(!r.ok)throw Error('Lokale Sitzung derzeit nicht verfügbar.');csrf=(await r.json()).csrf;const q=await api('local-review');items=q.items;events=q.events;index=0;labels=q.labels;show();$("status").textContent='Lokale Prüfansicht bereit · keine Anmeldung erforderlich';}
function failure(e){$("status").textContent=e.message;}
for(const id of ['label','comment'])$(id).addEventListener('input',()=>{clearTimeout(timer);const v=version;timer=setTimeout(()=>{if(v===version)save('draft').catch(failure);},500);});
$("kind").addEventListener('change',()=>{save('draft',activeKind).catch(failure);show();});
$("previous").onclick=async()=>{try{await save('draft');if(index>0)index--;show();}catch(e){failure(e);}};
$("next").onclick=async()=>{if(!$("label").value){failure(Error('Bitte ein Label wählen.'));return;}try{clearTimeout(timer);await save('judgment');if(index<items.length-1)index++;show();}catch(e){failure(e);}};
$("export").onclick=async()=>{try{const data=await api('local-review/export');const u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='local-reviews.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),500);}catch(e){failure(e);}};
document.addEventListener('keydown',e=>{if(e.altKey&&/^[1-6]$/.test(e.key)){e.preventDefault();$("label").selectedIndex=Number(e.key);$("label").dispatchEvent(new Event('input'));}if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$("next").click();}});
init().catch(failure);
