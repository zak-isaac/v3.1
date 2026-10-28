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
function feedback(message,kind='info'){
 const node=document.querySelector('#v31-import-feedback');
 if(!node)return;
 node.textContent=message||'';
 node.className=kind==='warning'?'warning':kind==='error'?'error':'';
}
function revealAudit(){
 const panel=document.querySelector('#audit');if(panel)panel.hidden=false;
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
  ' · Ignored '+snapshot.audit.ignored.length+
  ' · Next-day WOs '+(snapshot.audit.next_day||[]).length;
  audit.append(p);
  for(const r of [...snapshot.audit.pending,...snapshot.audit.ignored].slice(0,35)){
    const line=document.createElement('p');
    line.textContent='WO# '+r.wo_number+' — '+r.reason;
    audit.append(line);
  }
  // Next-day WOs are audit visibility, not automatic shift attribution.
  const nextDay=snapshot.audit.next_day||[];
  const nextWrap=document.createElement('details');
  nextWrap.style.cssText='margin-top:14px;padding:10px;border:1px solid #dbe5f0;border-radius:10px;background:#fff';
  const heading=document.createElement('summary');
  heading.style.cssText='cursor:pointer;font-weight:700;color:#224d77';
  heading.textContent='Next-Day Work Orders ('+nextDay.length+')';
  nextWrap.append(heading);
  const description=document.createElement('p');
  description.style.cssText='margin:9px 0;font-size:12px;color:#536c83';
  description.textContent='Following calendar day; work timestamps and administrative closures are different. S3/S5 overnight WOs may already belong to this shift. This list does not add WOs to Shift Happenings.';
  nextWrap.append(description);
  if(nextDay.length===0){
    const empty=document.createElement('p');
    empty.textContent='No next-calendar-day work orders detected in this import.';
    nextWrap.append(empty);
  }
  const list=document.createElement('div');
  list.style.cssText='display:grid;gap:8px;max-height:320px;overflow:auto';
  for(const row of nextDay){
    const item=document.createElement('div');
    item.style.cssText='border:1px solid #e3eaf2;padding:9px;border-radius:8px;font-size:12px';
    const title=document.createElement('strong');
    title.textContent=(row.asset_id||'No asset')+' · WO# '+row.wo_number;
    const meta=document.createElement('p');
    meta.style.cssText='margin:5px 0 0;color:#4b647e';
    meta.textContent=row.date+' · '+row.timestamp_type+' · '+row.disposition+
      ' · Status: '+(row.status||'Not provided')+
      ' · Techs: '+(row.technicians||[]).join('; ');
    item.append(title,meta);
    if(row.work_timestamps?.length || row.admin_timestamps?.length){
      const times=document.createElement('p');
      times.style.cssText='margin:4px 0 0;color:#687e94';
      times.textContent=(row.work_timestamps?.length?'Work: '+row.work_timestamps.join('; ')+' · ':'')+
        (row.admin_timestamps?.length?'Administrative: '+row.admin_timestamps.join('; '):'');
      item.append(times);
    }
    list.append(item);
  }
  nextWrap.append(list);
  audit.append(nextWrap);
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
 const label=document.querySelector('#v31-import-btn-label');
 if(label)label.textContent=snapshot.stage.staged?'Replace Cetaris File':'Import Cetaris';
 if(snapshot.stage.staged){
  const count=snapshot.stage.stats?.parsed_rows||0;
  const selected=snapshot.technicians?.length||0;
  document.querySelector('#cetaris-state').textContent=snapshot.stage.imported
   ? 'Cetaris Imported · '+count+' labor rows'
   : 'Cetaris Parsed · Confirm Technicians';
  if(!snapshot.stage.imported){
    feedback(count+' labor rows staged. Select/confirm technicians to finish importing this file.','warning');
  } else if(!(snapshot.rows.length+snapshot.pm_count)){
    feedback('No work orders qualified for this selected shift. Check Import Audit for technician, date, status and overlap reasons.','warning');
  } else {
    feedback('Imported '+snapshot.rows.length+' work orders and '+snapshot.pm_count+
      ' PMs. View Import Audit for excluded, next-day or pending records.');
  }
 }else{
  feedback('');
  document.querySelector('#cetaris-state').textContent='No Cetaris File Imported';
 }
 return snapshot;
}
async function upload(file){
 if(!/\.(xlsx|csv)$/i.test(file.name)){
   feedback('Choose an XLSX or CSV Cetaris work order export.','error');
   view.toast('Only .xlsx or .csv Cetaris exports are supported.');return;
 }
 if(file.size>12*1024*1024){
   feedback('File exceeds the 12 MB limit. Narrow the Cetaris export.','error');return;
 }
 const button=document.querySelector('#replace-file');
 if(button)button.disabled=true;
 feedback('Reading and checking '+file.name+'…');
 try{
   const form=new FormData();form.append('file',file);
   const result=await api('/api/reporting/stage',form);
   await refresh();
   const n=result.stats.parsed_rows||0;
   if(!n){
     feedback('File accepted but no technician labor rows were found. Review the Cetaris export and header columns.','warning');
     revealAudit();return;
   }
   feedback(n+' labor rows parsed. Drax-matched technicians are selected below; confirm to import.');
   chooseTechs();
 }catch(e){
   feedback(e.message,'error');view.toast(e.message);
 }finally{if(button)button.disabled=false;}
}
function chooseTechs(){
 if(!snapshot?.stage?.staged){
   feedback('Upload a Cetaris file before choosing technicians.','warning');
   view.toast('Upload Cetaris before choosing technicians.');return;
 }
 const suggested=snapshot.tech_suggestions||snapshot.stage.technicians?.map(x=>({...x,group:'unmatched'}))||[];
 if(!suggested.length){
   feedback('No technicians were identified in the staged Cetaris file.','warning');return;
 }
 // Existing confirmed selections survive reopening the modal. For a new
 // shift, preselect only exact, active Drax MST matches to this shift.
 const previously=(snapshot.technicians||[]).filter(n=>suggested.some(t=>t.name===n));
 const checked=new Set(snapshot.stage.imported?previously:
    previously.length?previously:suggested.filter(t=>t.preselected).map(t=>t.name));
 const selectedShift=snapshot.shift?.code||'selected shift';
 const container=document.createElement('div');
 container.className='v31-tech-roster';
 const count=document.createElement('p');
 count.style.cssText='margin:0;color:#234d73;font-weight:700';
 container.append(count);
 const hint=document.createElement('p');
 hint.style.cssText='margin:0;color:#627c93;font-size:12px;line-height:1.4';
 hint.textContent='Drax-assigned MSTs are checked automatically. Add extra technicians working overtime or assisting this shift. Selecting a technician does not automatically change work-order ownership.';
 container.append(hint);
 const search=document.createElement('input');
 search.className='v31-roster-search';search.type='search';
 search.placeholder='Find a technician from Cetaris…';search.setAttribute('aria-label','Search Cetaris technicians');
 container.append(search);
 const tools=document.createElement('div');tools.className='v31-tech-tools';
 const selectAssigned=document.createElement('button');
 selectAssigned.type='button';selectAssigned.textContent='Select Drax Shift MSTs';
 const clear=document.createElement('button');
 clear.type='button';clear.textContent='Clear selection';
 tools.append(selectAssigned,clear);container.append(tools);
 const groups=[
  {key:'assigned',title:'Assigned to '+selectedShift+' · Drax MSTs',
   desc:'Preselected when their Cetaris name matches an active Drax MST on this shift.'},
  {key:'other_shift',title:'Additional / Overtime Technicians',
   desc:'Other Drax MST shifts. Select only if they actually worked this shift.'},
  {key:'unmatched',title:'Other Cetaris Technicians',
   desc:'Not matched uniquely to an active Drax MST. Confirm identity before selecting.'}
 ];
 const groupNodes=[];
 const checkboxes=[];
 for(const group of groups){
   const people=suggested.filter(x=>x.group===group.key);
   if(!people.length)continue;
   const section=document.createElement('section');section.className='v31-tech-group';
   const title=document.createElement('h3');title.textContent=group.title+' ('+people.length+')';
   const description=document.createElement('p');description.textContent=group.desc;
   const list=document.createElement('div');list.className='v31-tech-list';
   for(const person of people){
     const label=document.createElement('label');label.className='v31-tech-person';
     const checkbox=document.createElement('input');checkbox.type='checkbox';
     checkbox.className='v31-tech';checkbox.value=person.name;
     checkbox.checked=checked.has(person.name);
     const content=document.createElement('span');
     const name=document.createElement('strong');name.textContent=person.name;
     const info=document.createElement('small');
     info.textContent=person.count+' WOs'+(person.drax_shift?' · Drax '+person.drax_shift:' · Drax match unavailable');
     content.append(name,info);label.append(checkbox,content);list.append(label);
     checkboxes.push({checkbox,label,group:group.key,name:person.name});
     checkbox.addEventListener('change',()=>updateCount());
   }
   section.append(title,description,list);container.append(section);
   groupNodes.push(section);
 }
 function updateCount(){
   count.textContent=checkboxes.filter(x=>x.checkbox.checked).length+
     ' of '+suggested.length+' technicians selected';
 }
 search.addEventListener('input',()=>{
   const q=search.value.trim().toLowerCase();
   for(const item of checkboxes)item.label.hidden=!item.name.toLowerCase().includes(q);
   for(const section of groupNodes)section.hidden=
      [...section.querySelectorAll('.v31-tech-person')].every(x=>x.hidden);
 });
 selectAssigned.addEventListener('click',()=>{
   for(const item of checkboxes)if(item.group==='assigned')item.checkbox.checked=true;
   updateCount();
 });
 clear.addEventListener('click',()=>{
   for(const item of checkboxes)item.checkbox.checked=false;updateCount();
 });
 updateCount();
 const matched=suggested.filter(x=>x.preselected).length;
 const message=matched
   ? matched+' matching Drax MSTs were preselected. Review others before importing.'
   : 'No exact Drax technician matches for this shift. Check the roster connection and select actual technicians manually.';
 view.showModal('Select Shift Technicians',message,'',[
  {text:'Cancel',onClick:view.closeModal},
  {text:'Import Selected Work Orders',kind:'btn-primary',onClick:async()=>{
   const names=checkboxes.filter(x=>x.checkbox.checked).map(x=>x.name);
   if(!names.length){
     feedback('Select at least one technician before importing.','warning');
     view.toast('Select at least one technician.');return;
   }
   try{
    feedback('Attributing selected technician work orders to '+selectedShift+'…');
    const result=await api('/api/reporting/import',{technicians:names});
    view.closeModal();await refresh();
    const sum=result.summary||{};
    const n=snapshot.rows.length+snapshot.pm_count;
    if(!n){
      feedback('Cetaris imported, but 0 WOs qualified. Check Import Audit for missing timestamps, unclosed records, or work assigned to another shift.','warning');
      revealAudit();
    }else{
      feedback('Imported '+snapshot.rows.length+' work orders and '+snapshot.pm_count+
        ' PMs. '+(sum.pending_review||0)+' items need review.');
      if(sum.pending_review||sum.ignored)revealAudit();
    }
    view.toast(n+' qualifying work orders/PMs saved to '+selectedShift+'.');
   }catch(e){feedback(e.message,'error');view.toast(e.message);}
  }}
 ]);
 const body=document.querySelector('#modal-body');
 if(body)body.append(container);
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
