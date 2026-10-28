/* V3.1 signed shift workspace: V3-style sidebar navigation, shift sessions and reporting context. */
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
  if(reportText) reportText.textContent='Work orders and notes are saved to the selected V3.1 shift.';
  const bar=document.createElement('section');
  bar.id='v31-context-bar';
  bar.setAttribute('aria-label','Selected shift');
  bar.innerHTML=
    '<div id="v31-context-label" class="v31-session-heading">Selected Shift</div>'+
    '<div class="v31-session-meta">'+
      '<div><strong>Shift Date</strong><span id="v31-sidebar-date">—</span></div>'+
      '<div><strong>Scheduled Hours</strong><span id="v31-sidebar-schedule">—</span></div>'+
      '<div><strong>Reporting Window</strong><span id="v31-sidebar-window">—</span></div>'+
      '<div><strong>Current Time</strong><span id="v31-sidebar-clock">—</span></div>'+
    '</div>';
  const anchor=$('#v31-sidebar-controls');
  if(anchor)anchor.append(bar);
  const username=$('#v31-user-name');
  if(username)username.textContent=context.name||'MST Associate';
  const userrole=$('#v31-user-role');
  if(userrole)userrole.textContent=(context.role==='manager'?'Management':(context.assigned_shift||'MST')+' · MST')+' · IND3';
  const accountMenu=$('#v31-menu-choose');
  if(accountMenu)accountMenu.hidden=context.role!=='manager';
  const adminMenu=$('#v31-menu-administration');
  adminMenu?.addEventListener('click',()=>{
    const menu=$('#account-menu');if(menu)menu.hidden=true;
    window.v31Navigate?.('admin');
  });
  const modal=document.createElement('dialog');
  modal.id='v31-shift-dialog';
  modal.style.cssText='width:min(560px,calc(100vw - 32px));max-height:85vh;overflow:auto;border:1px solid #cfdef2;border-radius:16px;padding:24px;box-shadow:0 28px 80px #07265755;font-family:Segoe UI,Arial,sans-serif';
  modal.innerHTML='<h2 style="margin:0 0 10px;color:#082d68">Choose Shift</h2><p style="color:#526782">Your account can only access authorized shift sessions. Starting a shift does not close other shifts.</p><div id="v31-shift-list" style="display:grid;gap:9px"></div><button id="v31-dialog-close" style="display:block;margin:16px 0 0 auto;border:0;background:#eaf0f8;padding:10px 16px;border-radius:8px">Close</button>';
  document.body.append(modal);
  $('#v31-dialog-close').addEventListener('click',()=>modal.close());
  const fmtTime = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:'America/Indiana/Indianapolis',hour:'numeric',minute:'2-digit'}).format(new Date(iso));
  const fmtDay = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:'America/Indiana/Indianapolis',weekday:'short',month:'short',day:'numeric',year:'numeric'}).format(new Date(iso));
  const draw=async()=>{
    context=await request('/api/me');
    window.__v31ShiftContext = context.selected || null;
    if (!context.selected) {
      // Never keep displaying a fake or expired shift as the active report.
      window.location.replace('/');
      return;
    }
    const meta=$('.shift-meta');
    if (meta) {
      const spans=meta.querySelectorAll('span');
      if(spans[0]) spans[0].textContent=fmtDay(context.selected.scheduled_start);
      if(spans[1]) spans[1].textContent=fmtTime(context.selected.scheduled_start)+' – '+fmtTime(context.selected.scheduled_end);
      if(spans[2]) spans[2].textContent='Reporting window closes '+fmtTime(context.selected.closes_at);
    }
    $('#v31-context-label').textContent=context.selected.code+' · Active Shift';
    $('#v31-sidebar-date').textContent=fmtDay(context.selected.scheduled_start);
    $('#v31-sidebar-schedule').textContent=
      fmtTime(context.selected.scheduled_start)+' – '+fmtTime(context.selected.scheduled_end);
    $('#v31-sidebar-window').textContent=
      fmtTime(context.selected.opens_at)+' – '+fmtTime(context.selected.closes_at);
    $('#v31-sidebar-clock').textContent=fmtTime(context.now);
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
          if(window.V31Reporting?.refresh)await window.V31Reporting.refresh();
        }catch(e){alert(e.message);}finally{b.disabled=false;}
      });
      row.append(label,b);list.append(row);
    }
  };
  accountMenu?.addEventListener('click',async()=>{
    if(context.role!=='manager')return;
    const menu=$('#account-menu');if(menu)menu.hidden=true;
    try{await draw();modal.showModal();}catch(e){window.alert(e.message);}
  });
  async function signout(){
    try{await request('/api/logout',{});}
    finally {
      // The public entry screen evaluates all current open and ready shifts.
      // Sign Out never closes a shift and never assumes only one is active.
      window.location.replace('/?shift-selection=1');
    }
  }
  $('#v31-signout')?.addEventListener('click',signout);
  const endButton=$('#end-shift');
  if (endButton) endButton.addEventListener('click',async e=>{
    e.stopImmediatePropagation();
    e.preventDefault();
    const selected=context?.selected;
    if(!selected) {window.location.replace('/');return;}
    if(!window.confirm('End '+selected.code+' report? This closes the selected shift session, not any overlapping shifts.')) return;
    endButton.disabled=true;
    try {
      await request('/api/shifts/end',{});
      window.location.replace('/');
    } catch(err) {window.alert(err.message);endButton.disabled=false;}
  },true);
  $('#go-to-login')?.addEventListener('click',e=>{e.stopImmediatePropagation();signout();},true);
  await draw();
  // Apply the real clock when the reporting grace window expires.
  setInterval(()=>{ if(!document.hidden) draw().catch(()=>{}); },60000);
})();