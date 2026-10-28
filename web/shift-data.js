/* V3.1 real Cetaris reporting controller. */
(() => {
'use strict';
const view=window.v31ReportView;
if(!view)return;
let snapshot=null,saveTimer;
const closureChoices={};
async function api(url,body){
 const options={credentials:'same-origin',headers:{Accept:'application/json'}};
 if(body!==undefined){
   options.method='POST';
   if(body instanceof FormData)options.body=body;
   else{options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 }
 const response=await fetch(url,options);
 const data=await response.json().catch(()=>({}));
 if(!response.ok)throw Error(data.detail||'Reporting request failed');
 return data;
}
async function refresh(){
 snapshot=await api('/api/reporting/state');
 view.applySnapshot(snapshot);
 const audit=document.querySelector('#audit > div');
 if(audit){
  audit.replaceChildren();
  const title=document.createElement('strong');
  title.textContent='Cetaris Import Audit';
  audit.append(title);
  const counts=snapshot.audit.summary||{};
  const p=document.createElement('p');
  p.textContent='Added '+(counts.added||0)+' · Updated '+(counts.updated||0)+
  ' · Edits preserved '+(counts.preserved_edits||0)+' · PM '+snapshot.pm_count+
  ' · Pending review '+snapshot.audit.pending.length+
  ' · Ignored '+snapshot.audit.ignored.length;
  audit.append(p);
  for(const r of [...snapshot.audit.pending,...snapshot.audit.ignored].slice(0,35)){
    const line=document.createElement('p');
    line.textContent='WO# '+r.wo_number+' — '+r.reason;
    audit.append(line);
  }
  const pendingClosed=snapshot.audit.pending.filter(x=>
    String(x.reason||'').startsWith('Completed but not Closed'));
  if(pendingClosed.length){
    const button=document.createElement('button');
    button.type='button';button.className='btn btn-primary btn-small';
    button.textContent='Review completed work orders';
    button.onclick=()=>reviewCompleted(pendingClosed);
    audit.append(button);
  }
 }
 if(snapshot.stage.staged){
  document.querySelector('#replace-file').textContent='Replace Cetaris File';
  document.querySelector('#cetaris-state').textContent='Cetaris file parsed';
 }
 return snapshot;
}
async function upload(file){
 if(!/\.(xlsx|csv)$/i.test(file.name)){view.toast('Upload a Cetaris XLSX or CSV file.');return;}
 const button=document.querySelector('#replace-file');button.disabled=true;
 try{
  const form=new FormData();form.append('file',file);
  const data=await api('/api/reporting/stage',form);
  await refresh();
  view.toast(data.stats.parsed_rows+' Cetaris labor rows parsed. Choose technicians.');
  chooseTechs();
 }catch(e){view.toast(e.message);}
 finally{button.disabled=false;}
}
function chooseTechs(){
 if(!snapshot?.stage?.staged){view.toast('Upload Cetaris before choosing technicians.');return;}
 const list=snapshot.stage.technicians||[];
 const selected=new Set(snapshot.technicians||[]);
 const body=document.createElement('div');
 body.style.cssText='display:grid;gap:10px;max-height:340px;overflow:auto';
 for(const person of list){
  const label=document.createElement('label');
  label.style.cssText='display:flex;align-items:center;gap:10px';
  const checkbox=document.createElement('input');checkbox.type='checkbox';
  checkbox.className='v31-tech';checkbox.value=person.name;
  checkbox.checked=selected.has(person.name);
  label.append(checkbox,document.createTextNode(person.name+' ('+person.count+' WOs)'));
  body.append(label);
 }
 view.showModal('Choose technicians','Select actual technicians for this shift.', '',[
  {text:'Cancel',onClick:view.closeModal},
  {text:'Import selected',kind:'btn-primary',onClick:async()=>{
   const names=[...document.querySelectorAll('#modal-body .v31-tech:checked')].map(e=>e.value);
   if(!names.length){view.toast('Select at least one technician');return;}
   try{
    const result=await api('/api/reporting/import',{technicians:names});
    view.closeModal();await refresh();
    view.toast(result.summary.added+' work orders saved; '+snapshot.pm_count+' PMs');
    if(snapshot.audit.pending.length)document.querySelector('#audit').hidden=false;
   }catch(e){view.toast(e.message);}
  }}
 ]);
 document.querySelector('#modal-body').append(body);
}
function reviewCompleted(items){
 const block=document.createElement('div');
 block.style.cssText='display:grid;gap:10px;max-height:320px;overflow:auto';
 for(const item of items){
  const label=document.createElement('label');
  label.style.cssText='display:flex;gap:10px;align-items:center';
  const check=document.createElement('input');
  check.type='checkbox';check.className='v31-closure-review';
  check.value=item.wo_number;check.checked=closureChoices[item.wo_number]==='add';
  label.append(check,document.createTextNode('WO# '+item.wo_number+
    ' — Add closure reminder to passdown'));
  block.append(label);
 }
 view.showModal('Completed Work Orders','Choose Add to Passdown for closure reminders; unchecked WOs are ignored until Cetaris is closed.','',[
   {text:'Cancel',onClick:view.closeModal},
   {text:'Apply review',kind:'btn-primary',onClick:async()=>{
     const chosen=new Set([...document.querySelectorAll('#modal-body .v31-closure-review:checked')].map(e=>e.value));
     for(const item of items)closureChoices[item.wo_number]=chosen.has(item.wo_number)?'add':'ignore';
     try{
       await api('/api/reporting/import',{technicians:snapshot.technicians,closure_reviews:closureChoices});
       view.closeModal();await refresh();view.toast('Closure decisions saved');
     }catch(e){view.toast(e.message);}
   }}
 ]);
 document.querySelector('#modal-body').append(block);
}
async function updateWO(wo,changes){
 return api('/api/reporting/work-orders/'+encodeURIComponent(wo),changes);
}
async function saveDraft(notify=false){
 try{
  await api('/api/reporting/draft',view.draft());
  document.querySelector('#last-saved').textContent='Draft saved to selected shift';
  if(notify)view.toast('Shift draft saved');
 }catch(e){view.toast(e.message);}
}
function queueDraft(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveDraft(false),650);}
async function flushDraft(){clearTimeout(saveTimer);await saveDraft(false);}
window.V31Reporting={upload,chooseTechs,updateWO,saveDraft,queueDraft,flushDraft,refresh};
refresh().catch(e=>view.toast('Reporting data could not load: '+e.message));
})();
