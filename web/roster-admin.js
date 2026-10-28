/* IND3 V3.1 Administration dashboard — local, management-gated. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const api = async (url, body) => {
    const opts={credentials:'same-origin',headers:{Accept:'application/json'}};
    if(body!==undefined){
      opts.method='POST';opts.headers['Content-Type']='application/json';
      opts.body=JSON.stringify(body);
    }
    const res=await fetch(url,opts);
    const out=await res.json().catch(()=>({}));
    if(!res.ok)throw Error(out.detail||'Request failed');
    return out;
  };
  let details=null;
  let user=null;
  let currentSearch='';
  let initialized=false;
  let pending=false;
  const localAdminPage=()=>{
    // Available even when a manager has not selected a shift yet.
    window.location.assign('/administration');
  };
  window.v31OpenDraxAdmin=localAdminPage;
  const notify=(message,error=false)=>{
    const element=$('v31-admin-notice');
    if(!element)return;
    element.textContent=message;
    element.classList.toggle('error',error);
  };
  const el=(tag,text,cls)=>{
    const node=document.createElement(tag);
    if(text!==undefined)node.textContent=text;
    if(cls)node.className=cls;
    return node;
  };
  const busy=async fn=>{
    if(pending)return;
    pending=true;
    for(const id of ['v31-admin-refresh','v31-admin-reload']){
      const control=$(id);if(control)control.disabled=true;
    }
    try{await fn();}
    catch(error){notify(error.message,true);}
    finally{
      pending=false;
      for(const id of ['v31-admin-refresh','v31-admin-reload']){
        const control=$(id);if(control)control.disabled=false;
      }
    }
  };
  async function reload(force=false){
    details=await api(force?'/api/admin/roster/reload':'/api/admin/roster',
                      force?{}:undefined);
    draw();
    if(force)notify(details.error?'Drax sync could not complete: '+details.error:
      'Drax roster refreshed successfully.',!!details.error);
  }
  function draw(){
    if(!details)return;
    const rows=details.associates||[];
    $('v31-stat-total').textContent=String(rows.length);
    $('v31-stat-msts').textContent=String(rows.filter(r=>r.is_mst&&r.active).length);
    $('v31-stat-manual').textContent=String(rows.filter(r=>r.manual).length);
    $('v31-stat-overrides').textContent=String(rows.filter(r=>r.overridden).length);
    $('v31-sync-source').textContent=details.source||'Not connected';
    $('v31-sync-last').textContent=details.last_synced||'Not synced';
    $('v31-sync-count').textContent=String(details.entries||0);
    const status=$('v31-source-status');
    status.className='v31-admin-status '+(details.error||!details.last_synced?'bad':'good');
    status.textContent=details.error?'Sync issue':details.last_synced?'Connected':'Not connected';
    $('v31-sync-warning').textContent=details.error
      ? 'Drax source issue: '+details.error+' The last local roster snapshot is retained.'
      : 'Workbook reading is enabled. Local MST/shift changes are V3.1 overrides, not automatic edits to the Drax Excel file. Approved two-way write-back is a separate controlled process.';
    drawPeople(rows);
  }
  function drawPeople(rows){
    const filter=currentSearch.trim().toLocaleLowerCase();
    const filtered=rows.filter(p=>!filter||
      String(p.name||'').toLocaleLowerCase().includes(filter)||
      String(p.number||'').includes(filter));
    const tbody=$('v31-roster-rows');
    tbody.replaceChildren();
    for(const person of filtered){
      const tr=el('tr');
      const who=el('td');
      who.append(el('span',person.name,'v31-person'),
                 el('small',person.number,'v31-person-id'));
      const shiftCell=el('td');
      const shift=el('select');
      shift.setAttribute('aria-label','Assigned shift for '+person.name);
      for(const code of ['','S1','S2','S3','S4','S5']){
        const option=el('option',code||'Not assigned');
        option.value=code;option.selected=code===(person.shift||'');
        shift.append(option);
      }
      shift.addEventListener('change',()=>{
        const code=shift.value;
        if(!code){notify('Choose S1–S5 to set an assigned shift.',true);draw();return;}
        busy(async()=>{
          details=await api('/api/admin/roster/override',
                            {number:person.number,shift:code});
          draw();notify('Shift assignment saved in V3.1 for '+person.name+'.');
        });
      });
      shiftCell.append(shift);
      const access=el('td');
      access.append(el('span',person.is_mst?'MST Enabled':'Not MST',
                       'v31-admin-chip '+(person.is_mst?'mst':'other')));
      const source=el('td');
      source.append(el('span',person.manual?'Local entry':person.active?'Drax source':'Not in Drax',
                       'v31-admin-chip '+(person.active?'mst':'other')));
      if(person.overridden)source.append(el('small','Management override','v31-person-id'));
      const actions=el('td');
      const group=el('div',undefined,'v31-row-actions');
      const makeAction=(name,callback)=>{
        const b=el('button',name);b.type='button';b.addEventListener('click',()=>busy(callback));group.append(b);
      };
      makeAction(person.is_mst?'Disable MST':'Enable MST',async()=>{
        details=await api('/api/admin/roster/override',
                   {number:person.number,is_mst:!person.is_mst});
        draw();notify('MST access updated for '+person.name+'.');
      });
      makeAction('Edit Name',async()=>{
        const answer=window.prompt('Associate display name',person.name);
        if(answer===null)return;
        if(answer.trim().length<2){notify('Enter a valid associate name.',true);return;}
        details=await api('/api/admin/roster/edit',
                     {number:person.number,full_name:answer.trim()});
        draw();notify('Associate name updated for '+person.number+'.');
      });
      if(person.manual){
        makeAction('Edit Number',async()=>{
          const answer=window.prompt('Associate number',person.number);
          if(answer===null)return;
          if(!/^[0-9]{1,12}$/.test(answer)){notify('Enter a valid numeric associate number.',true);return;}
          details=await api('/api/admin/roster/edit',
                {number:person.number,new_number:answer,full_name:person.name});
          draw();notify('Local associate number updated.');
        });
      }
      if(person.overridden){
        makeAction('Reset',async()=>{
          details=await api('/api/admin/roster/override',
                     {number:person.number,reset:true});
          draw();notify('Source defaults restored for '+person.name+'.');
        });
      }
      actions.append(group);
      tr.append(who,shiftCell,access,source,actions);
      tbody.append(tr);
    }
    if(!filtered.length){
      const tr=el('tr');
      const td=el('td',rows.length?'No matches found.':'No associates found in the roster.');
      td.colSpan=5;tr.append(td);tbody.append(tr);
    }
    $('v31-roster-count').textContent='Showing '+filtered.length+
       ' of '+rows.length+' associates. Drax-sourced numbers are controlled by the workbook.';
  }
  async function open(){
    if(!$('v31-admin-screen'))return;
    if(!initialized){
      try{user=await api('/api/me');}
      catch(e){notify('Unable to verify management access: '+e.message,true);return;}
      initialized=true;
    }
    const allowed=user?.role==='manager';
    $('v31-admin-denied').hidden=allowed;
    $('v31-admin-content').hidden=!allowed;
    $('v31-admin-refresh').hidden=!allowed;
    $('v31-admin-reload').hidden=!allowed;
    if(!allowed){notify('This section requires management authorization.',true);return;}
    await busy(()=>reload());
  }
  if($('v31-admin-screen')){
    $('v31-admin-refresh')?.addEventListener('click',()=>busy(()=>reload()));
    $('v31-admin-reload')?.addEventListener('click',()=>busy(()=>reload(true)));
    $('v31-roster-search')?.addEventListener('input',e=>{
      currentSearch=e.target.value;if(details)drawPeople(details.associates||[]);
    });
    $('v31-admin-add-form')?.addEventListener('submit',e=>{
      e.preventDefault();
      busy(async()=>{
        const form={
          number:$('v31-add-number').value.trim(),
          name:$('v31-add-name').value.trim(),
          shift:$('v31-add-shift').value,
          is_mst:$('v31-add-mst').checked
        };
        details=await api('/api/admin/roster/add',form);
        $('v31-admin-add-form').reset();
        draw();notify('Local associate added to V3.1.');
      });
    });
    window.addEventListener('v31:administration',()=>open());
  }
})();
