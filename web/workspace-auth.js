/* V3.1 signed shift workspace: navigation and switcher only.
 * Existing Shift Report data/actions are illustrative frontend previews.
 */
(async () => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const request = async (path, body=null) => {
    const options = {credentials:'same-origin',method:body===null?'GET':'POST',headers:{'Accept':'application/json'}};
    if (body !== null) {options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
    const r=await fetch(path,options);
    if(r.status===401) {window.location.replace('/');throw Error('Sign in required');}
    const data=await r.json();
    if(!r.ok) throw Error(data.detail||'Could not complete request');
    return data;
  };
  let context;
  try {context=await request('/api/me');}catch(e){return;}
  const account = $('#account-button');
  if (account) {
    const label=account.querySelector('span:not(.avatar-icon)');
    if(label) label.textContent=context.name;
  }
  const reportTitle=document.querySelector('.shift-heading h1');
  const originalTitle=reportTitle?.textContent||'Shift Report';
  const reportText=$('.footer-tip span');
  if(reportText) reportText.textContent='New V3.1 project. Sample report content; no production work orders are connected.';
  const bar=document.createElement('div');
  bar.id='v31-context-bar';
  bar.style.cssText='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;background:#f4f8ff;border:1px solid #cfdef2;border-radius:12px;padding:12px 16px;margin:12px 0;color:#193d69;font:600 14px Segoe UI,Arial,sans-serif';
  bar.innerHTML='<span id="v31-context-label">Select a shift to start reporting</span><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button id="v31-select-shift" type="button" style="border:1px solid #1763bd;background:white;color:#0755ad;border-radius:8px;padding:9px 13px;font-weight:700;cursor:pointer">Choose Shift</button><button id="v31-signout" type="button" style="border:1px solid #d2dff1;background:#f6f9ff;color:#395473;border-radius:8px;padding:9px 13px;cursor:pointer">Sign Out</button></div>';
  const anchor=$('.shift-heading')||$('#report-screen')||document.querySelector('main');
  if(anchor) anchor.insertAdjacentElement('afterend',bar); else document.body.prepend(bar);
  const modal=document.createElement('dialog');
  modal.id='v31-shift-dialog';
  modal.style.cssText='width:min(560px,calc(100vw - 32px));max-height:85vh;overflow:auto;border:1px solid #cfdef2;border-radius:16px;padding:24px;box-shadow:0 28px 80px #07265755;font-family:Segoe UI,Arial,sans-serif';
  modal.innerHTML='<h2 style="margin:0 0 10px;color:#082d68">Choose Shift</h2><p style="color:#526782">Your account can only access authorized shift sessions. Starting a shift does not close other shifts.</p><div id="v31-shift-list" style="display:grid;gap:9px"></div><button id="v31-dialog-close" style="display:block;margin:16px 0 0 auto;border:0;background:#eaf0f8;padding:10px 16px;border-radius:8px">Close</button>';
  document.body.append(modal);
  $('#v31-dialog-close').addEventListener('click',()=>modal.close());
  const draw=async()=>{
    context=await request('/api/me');
    $('#v31-context-label').textContent=context.selected
      ? `${context.selected.code} — ${context.selected.label} · ${context.selected.date}`
      : 'No reporting shift selected';
    if(reportTitle) reportTitle.textContent=context.selected?`${context.selected.code} Shift Report`:originalTitle;
    const list=$('#v31-shift-list');list.replaceChildren();
    if(!context.shifts.length){
      const p=document.createElement('p');p.textContent='No open or eligible shifts at this time.';list.append(p);
    }
    for (const shift of context.shifts) {
      const row=document.createElement('div');
      row.style.cssText='border:1px solid #d9e5f4;border-radius:10px;padding:12px;display:flex;justify-content:space-between;align-items:center;gap:10px';
      const label=document.createElement('div');
      label.innerHTML=`<strong>${shift.code} — ${shift.label}</strong><div style="font-size:12px;color:#5c6f87">${shift.date} · ${shift.action==='resume'?'Open report':'Ready to start'}</div>`;
      const b=document.createElement('button');b.type='button';b.textContent=shift.action==='resume'?'Resume':'Start';
      b.style.cssText='padding:10px 16px;color:white;background:#0872e2;border:0;border-radius:8px;font-weight:700;cursor:pointer';
      b.addEventListener('click',async()=>{
        b.disabled=true;
        try {
          await request(shift.action==='resume'?'/api/shifts/resume':'/api/shifts/start',
            shift.action==='resume'?{shift_id:shift.id}:{code:shift.code});
          modal.close();await draw();
        }catch(e){alert(e.message);}finally{b.disabled=false;}
      });
      row.append(label,b);list.append(row);
    }
  };
  $('#v31-select-shift').addEventListener('click',async()=>{try{await draw();modal.showModal();}catch(e){alert(e.message);}});
  async function signout(){try{await request('/api/logout',{});}finally{window.location.replace('/');}}
  $('#v31-signout').addEventListener('click',signout);
  $('#go-to-login')?.addEventListener('click',e=>{e.stopImmediatePropagation();signout();},true);
  await draw();
})();