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
  const reportText=$('.footer-tip span');
  if(reportText) reportText.textContent='Work orders and notes are saved to the selected V3.1 shift.';
  const username=$('#v31-user-name');
  if(username)username.textContent=context.name||'Shift Associate';
  const initial=$('#v31-user-initial');
  if(initial)initial.textContent=(context.name||'?').trim().charAt(0).toUpperCase()||'?';
  const userrole=$('#v31-user-role');
  if(userrole)userrole.textContent=(context.role==='manager'?'Management':
    (context.assigned_shift||'Shift')+' · '+(context.role==='mst'?'MST':'Technician'))+' · IND3';
  const fmtTime = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:'America/Indiana/Indianapolis',hour:'numeric',minute:'2-digit'}).format(new Date(iso));
  const fmtDay = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:'America/Indiana/Indianapolis',weekday:'short',month:'short',day:'numeric',year:'numeric'}).format(new Date(iso));
  const fmtFullDay = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:'America/Indiana/Indianapolis',weekday:'long',month:'long',day:'numeric',year:'numeric'}).format(new Date(iso));
  const draw=async()=>{
    context=await request('/api/me');
    window.__v31ShiftContext = context.selected || null;
    window.__v31Facility = context.facility || null;
    const clock=$('#v31-sidebar-clock');
    if(clock)clock.textContent=fmtTime(context.now);
    const today=$('#v31-sidebar-today');
    if(today)today.textContent=fmtFullDay(context.now);
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
  };
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
  // Keeps the sidebar clock ticking and catches an expired/closed shift
  // (or reporting-window grace period) so it redirects without needing a
  // manual reload.
  setInterval(()=>{ if(!document.hidden) draw().catch(()=>{}); },60000);
})();