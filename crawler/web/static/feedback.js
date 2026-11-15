'use strict';
(() => {
  const get = id => document.getElementById(id);
  function payload() {
    const context = window.pilotFeedbackContext();
    const include = get('feedback-context').checked;
    return {schema:'klima-pilot-feedback-v1', technical_test:false,
      category:get('feedback-kind').value, message:get('feedback-message').value.trim(),
      created_at:new Date().toISOString(), software:context.software,
      snapshot_id:context.snapshot, projection_id:context.projection,
      context_included:include, context:include ? context : null,
      transfer:'MANUAL_ONLY', research_gold:false};
  }
  get('feedback-save').onclick = async () => {
    try {
      const data=payload();
      const result=await request('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      get('feedback-status').textContent='Feedback persistent gespeichert: '+result.feedback_id+'. Pilotfeedback, kein Human Gold.';
    } catch(error) {get('feedback-status').textContent=error.message;}
  };
  get('feedback-history').onclick = async () => {
    try {const data=await request('/api/feedback');get('feedback-stored').textContent='';
      for(const e of data.items){const article=document.createElement('article');const title=document.createElement('h2');title.textContent=e.payload.category+' · '+e.at;const message=document.createElement('p');message.textContent=e.payload.message;article.append(title,message);get('feedback-stored').append(article);}
      if(!data.items.length)get('feedback-stored').textContent='Noch kein gespeichertes Feedback in dieser Revieweransicht.';
    }catch(error){get('feedback-status').textContent=error.message;}
  };
  get('feedback-preview').onclick = () => {
    get('feedback-json').textContent = JSON.stringify(payload(), null, 2);
  };
  get('feedback-form').onsubmit = event => {
    event.preventDefault();
    const data = payload();
    if (!data.message) { get('feedback-message').focus(); return; }
    get('feedback-json').textContent = JSON.stringify(data, null, 2);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)], {type:'application/json'}));
    const link = document.createElement('a'); link.href=url; link.download='klima-feedback-'+Date.now()+'.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
    get('feedback-status').textContent='Datei zum Download bereitgestellt. Bitte den Download prüfen; es wurde nichts versendet. Nach dem Schließen dieser Seite ist ungesicherter Freitext verloren.';
  };
})();
