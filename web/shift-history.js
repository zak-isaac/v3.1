/* V3.1 private legacy V2/V3 shift history and new login activity viewer. */
(() => {
  'use strict';
  const get = async (p) => {
    const resp=await fetch(p,{credentials:'same-origin'});
    if (!resp.ok) throw Error((await resp.json().catch(()=>({}))).detail || 'History unavailable');
    return resp.json();
  };
  const dlg=document.createElement('dialog');
  dlg.id="v31-history-dialog";
  dlg.style.cssText="width:min(930px,calc(100vw - 25px));max-height:88dvh;overflow:auto;padding:22px;border:1px solid #d8e4ee;border-radius:16px;background:#fff;color:#243447;box-shadow:0 20px 75px #0d213b50";
  const header=document.createElement('div');
  header.style.cssText="display:flex;justify-content:space-between;align-items:center;gap:12px";
  const heading=document.createElement('h2');
  heading.textContent="Shift History — V2, V3 & V3.1";heading.style.margin="0";
  const close=document.createElement('button');
  close.textContent="Close";close.type="button";
  close.style.cssText="background:#eff4fa;color:#174577;border:0;border-radius:8px;padding:10px 15px;cursor:pointer";
  close.onclick=()=>dlg.close();
  header.append(heading,close);
  const intro=document.createElement('p');
  intro.textContent="Legacy shifts remain in an exact-schema archive. V3.1 login events are recorded separately. Original records are read-only.";
  intro.style.cssText="font-size:14px;color:#5b7187";
  const notice=document.createElement('p');notice.setAttribute("role","status");
  const list=document.createElement('div');list.style.cssText="display:grid;gap:8px";
  const more=document.createElement('button');more.textContent="Load 50 more legacy shifts";
  more.style.cssText="padding:12px;border-radius:8px;border:1px solid #c5d8e7;background:#f8fbfe;color:#163d66;cursor:pointer";
  const audit=document.createElement('div');audit.style.cssText="margin-top:14px";
  dlg.append(header,intro,notice,list,more,audit);
  document.body.append(dlg);
  let offset=0;
  const showRows=(data,append=false)=>{
    if(!append)list.replaceChildren();
    if(!data.connected){
      notice.textContent="No V2/V3 archive imported yet. Use the private history importer on an approved machine.";
      more.hidden=true;return;
    }
    notice.textContent="Historical shift records: "+data.total+" (original IDs preserved)";
    for(const row of data.rows){
      const item=document.createElement('div');
      item.style.cssText="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;padding:12px;border:1px solid #e1e8f2;border-radius:10px;background:#fff";
      const name=document.createElement('strong');
      name.textContent=(row.shift_type||"Shift")+" · "+(row.date||"")+" · Original #"+row.id;
      const detail=document.createElement('span');detail.style.cssText="font-size:12px;color:#62758a";
      detail.textContent=(row.lead_name||"")+" · Opened "+(row.created_at||"")+
        " · "+row.entry_count+" entries · "+row.report_count+" reports";
      item.append(name,detail);list.append(item);
    }
    offset += data.rows.length;
    more.hidden=offset>=data.total;
  };
  const load=async(reset=false)=>{
    if(reset)offset=0;
    try{
      const data=await get("/api/history/legacy?offset="+offset+"&limit=50");
      showRows(data,!reset);
    }catch(e){notice.textContent=e.message;more.hidden=true;}
  };
  more.addEventListener('click',()=>load());
  const open=async()=>{
    dlg.showModal();await load(true);
    audit.replaceChildren();
    const label=document.createElement("h3");label.textContent="New V3.1 login events";
    audit.append(label);
    try{
      const events=(await get("/api/history/login-events?limit=35")).events;
      const grid=document.createElement('div');grid.style.cssText="display:grid;gap:5px";
      for(const event of events){
        const p=document.createElement('div');
        p.style.cssText="font-size:13px;border-bottom:1px solid #edf2f6;padding:7px";
        p.textContent=event.recorded_at+" · "+event.event+" · Associate "+event.associate+
          (event.shift_id?" · Shift #"+event.shift_id:"");
        grid.append(p);
      }
      if(!events.length)grid.textContent="No new login events recorded yet.";
      audit.append(grid);
    }catch(e){
      const p=document.createElement('p');p.textContent=e.message;audit.append(p);
    }
  };
  const historyButton=document.querySelector('[data-nav="history"]');
  if(historyButton){
    historyButton.addEventListener('click',(e)=>{
      e.preventDefault();e.stopImmediatePropagation();open();
    },true);
  }
})();
