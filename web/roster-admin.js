/* V3.1 local-only management console, separate from Workshop production. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const getJSON = async (url, body) => {
    const opts = {credentials:'same-origin',headers:{Accept:'application/json'}};
    if (body !== undefined) {
      opts.method='POST'; opts.headers['Content-Type']='application/json';
      opts.body=JSON.stringify(body);
    }
    const response=await fetch(url,opts);
    const json=await response.json().catch(()=>({}));
    if (!response.ok) throw Error(json.detail||'Request failed.');
    return json;
  };
  const button = (title, callback, primary=false) => {
    const b=document.createElement('button');
    b.type='button';b.textContent=title;
    b.style.cssText='padding:9px 12px;border-radius:8px;border:1px solid '+
      (primary?'#1764c5':'#d3dfed')+';background:'+(primary?'#1169d3':'#fff')+
      ';color:'+(primary?'white':'#16406a')+';font-weight:650;cursor:pointer';
    b.addEventListener('click',callback);return b;
  };
  const input = (placeholder,type='text') => {
    const i=document.createElement('input');i.type=type;i.placeholder=placeholder;
    i.style.cssText='padding:10px;border:1px solid #d6e0ec;border-radius:9px;background:white;color:#152b43;min-width:0';
    return i;
  };
  const dropdown = initial => {
    const s=document.createElement('select');
    s.style.cssText='padding:9px;border:1px solid #d6e0ec;border-radius:8px;background:white;color:#152b43';
    ['','S1','S2','S3','S4','S5'].forEach(k=>{
      const o=document.createElement('option');o.value=k;o.textContent=k||'No assigned shift';
      o.selected=k===initial;s.append(o);
    });
    return s;
  };
  let details=null;
  const dlg=document.createElement('dialog');dlg.id='v31-admin-roster';
  dlg.style.cssText='width:min(890px,calc(100vw - 26px));max-height:90dvh;overflow-y:auto;padding:0;border:1px solid #d4dfec;border-radius:18px;box-shadow:0 28px 90px #071b3c66;background:#fff;color:#19334d;font-family:Segoe UI,Arial,sans-serif';
  dlg.innerHTML='<div style="padding:24px;display:grid;gap:16px">'+
    '<header style="display:flex;justify-content:space-between;gap:10px;align-items:center">'+
    '<div><h2 style="margin:0;font-size:22px">Drax MST Roster</h2>'+
    '<small>Independent V3.1 · Local management controls</small></div>'+
    '<button id="v31-admin-close" type="button" style="border:0;background:#edf2f8;border-radius:7px;padding:9px">Close</button></header>'+
    '<div id="v31-admin-health" style="font-size:13px;color:#4d6886"></div>'+
    '<div id="v31-admin-actions" style="display:flex;flex-wrap:wrap;gap:8px"></div>'+
    '<h3 style="margin:4px 0 0;font-size:17px">Associates</h3>'+
    '<div id="v31-admin-people" style="display:grid;gap:8px;max-height:260px;overflow:auto"></div>'+
    '<h3 style="margin:4px 0 0;font-size:17px">Add local associate</h3>'+
    '<div id="v31-admin-add" style="display:flex;flex-wrap:wrap;gap:8px"></div>'+
    '<p id="v31-admin-message" role="status" style="margin:0;font-size:13px;color:#21557d"></p>'+
    '</div>';
  document.body.append(dlg);
  $('#v31-admin-close').addEventListener('click',()=>dlg.close());
  function notice(value,isError=false){
    const box=$('#v31-admin-message');box.textContent=value;box.style.color=isError?'#ba2525':'#21557d';
  }
  const run=async action=>{
    try{await action();}catch(e){notice(e.message,true);}
  };
  async function refresh(force=false){
    details=await getJSON(force?'/api/admin/roster/reload':'/api/admin/roster',force?{}:undefined);
    render();
  }
  function render(){
    $('#v31-admin-health').textContent=
      'Drax source: '+(details.source||'Not connected')+' · '+details.entries+' records'+
      (details.last_synced?' · Last synced '+details.last_synced:'')+
      (details.error?' · ATTENTION: '+details.error:'');
    $('#v31-admin-health').style.color=details.error?'#a32b2b':'#4d6886';
    const actions=$('#v31-admin-actions');actions.replaceChildren();
    actions.append(button('Reload Drax now',()=>run(()=>refresh(true)),true));
    actions.append(button('Refresh view',()=>run(()=>refresh())));
    const people=$('#v31-admin-people');people.replaceChildren();
    if(!(details.associates||[]).length){
      const p=document.createElement('p');p.textContent='No roster loaded yet. Configure an authorized local Drax workbook.';people.append(p);
    }
    for(const person of (details.associates||[])){
      const row=document.createElement('div');
      row.style.cssText='display:flex;flex-wrap:wrap;gap:9px;align-items:center;justify-content:space-between;border:1px solid #e2eaf3;border-radius:10px;padding:10px';
      const name=document.createElement('div');name.style.cssText='flex:1;min-width:160px';
      const title=document.createElement('strong');title.textContent=person.name;
      const sub=document.createElement('small');sub.style.cssText='display:block;color:#58718a';
      sub.textContent=person.number+' · '+(person.active?'Active':'Not in Drax')+
        (person.overridden?' · Management override':'');
      name.append(title,sub);
      const shift=dropdown(person.shift);
      shift.addEventListener('change',()=>run(async()=>{
        if(!shift.value){notice('Select S1–S5 to override the shift.',true);return;}
        details=await getJSON('/api/admin/roster/override',{number:person.number,shift:shift.value});
        render();notice('Shift override saved.');
      }));
      const flag=button(person.is_mst?'MST ✓':'Not MST',()=>run(async()=>{
        details=await getJSON('/api/admin/roster/override',
          {number:person.number,is_mst:!person.is_mst});
        render();notice('MST status updated.');
      }));
      const reset=button('Reset',()=>run(async()=>{
        details=await getJSON('/api/admin/roster/override',
          {number:person.number,reset:true});
        render();notice('Drax defaults restored.');
      }));
      const edit=button('Edit',()=>run(async()=>{
        const editedName=window.prompt('Associate display name',person.name);
        if(editedName===null)return;
        const editedNumber=window.prompt(
          person.manual?'Associate number (manual record)':'Associate number (Drax-controlled; change in Excel)',
          person.number);
        if(editedNumber===null)return;
        details=await getJSON('/api/admin/roster/edit',{
          number:person.number,full_name:editedName.trim(),new_number:editedNumber.trim()
        });
        render();notice('Associate information updated.');
      }));
      row.append(name,shift,flag,edit,reset);people.append(row);
    }
    const add=$('#v31-admin-add');add.replaceChildren();
    const assoc=input('Associate number');assoc.inputMode='numeric';
    const name=input('Full name');
    const day=dropdown('S1');
    const mst=document.createElement('label');mst.style.cssText='display:flex;align-items:center;gap:5px';
    const tick=document.createElement('input');tick.type='checkbox';tick.checked=true;
    mst.append(tick,document.createTextNode('MST'));
    add.append(assoc,name,day,mst,button('Add',()=>run(async()=>{
      details=await getJSON('/api/admin/roster/add',{
        number:assoc.value.trim(),name:name.value.trim(),shift:day.value,is_mst:tick.checked
      });render();notice('Associate added to the private V3.1 roster.');
    }),true));

  }
  async function open(){
    try{
      const me=await getJSON('/api/me');
      if(me.role!=='manager'){notice('Management access required',true);return;}
      await refresh();dlg.showModal();
    }catch(e){window.alert(e.message);}
  }
  window.v31OpenDraxAdmin = open;
  async function mount(){
    let who;
    try{who=await getJSON('/api/me');}catch(_){return;}
    const bar=$('#v31-context-bar');
    if(who.role==='manager'&&bar){
      const manage=button('Manage Drax Roster',open);
      manage.id='v31-admin-open';
      const actions=bar.querySelector('.v31-context-actions');
      (actions||bar).prepend(manage);
    }
    const adminNav=document.querySelector('button[data-nav="admin"]');
    if(adminNav)adminNav.addEventListener('click',e=>{
      e.preventDefault();e.stopImmediatePropagation();
      if(who.role!=='manager'){window.alert('Management access is required for roster administration.');return;}
      open();
    },true);
  }
  // Workspace initialization is asynchronous, so wait for the context bar to appear.
  (async()=>{
    for(let n=0;n<30;n++){
      if($('#v31-context-bar'))break;
      await new Promise(resolve=>setTimeout(resolve,80));
    }
    await mount();
  })();
})();
