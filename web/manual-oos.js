/* Unified IND3 asset status: one Add flow, one persistent OOS list,
 * one shift RTS list. Historical records and Cetaris status rules stay in
 * their existing backend tables. All asset names are HTML-escaped.
 */
(() => {
 'use strict';
 const $=(selector,root=document)=>root.querySelector(selector);
 const root=()=>$('#v31-manual-oos-root');
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let status=null,catalog=null,loading=false;
 const api=async(path,data)=>{
   const opts={credentials:'same-origin',headers:{Accept:'application/json'}};
   if(data!==undefined){opts.method='POST';opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data);}
   const response=await fetch(path,opts);
   const result=await response.json().catch(()=>({}));
   if(!response.ok)throw Error(result.detail||'Asset status request failed');
   return result;
 };
 const toast=msg=>window.v31ReportView?.toast?.(msg);
 const statusCard=(item,type)=>{
   const details=[item.source,item.wo_number?'WO# '+item.wo_number:'',item.reason]
     .filter(Boolean).map(esc).join(' · ');
   const actions=[];
   if(type==='oos'){
     if(item.pending_decision && item.wo_number){
       actions.push(`<button type="button" class="btn btn-light btn-small" data-breakdown-rts="${esc(item.wo_number)}" data-asset="${esc(item.asset)}">Confirm RTS</button>`);
       actions.push(`<button type="button" class="btn btn-light btn-small" data-breakdown-oos="${esc(item.wo_number)}">Keep OOS</button>`);
     }else if(item.hold){
       actions.push(`<button type="button" class="btn btn-light btn-small" data-release-hold="${esc(item.asset)}">Release Hold</button>`);
     }else if(item.source!=='Cetaris breakdown'){
       actions.push(`<button type="button" class="btn btn-light btn-small" data-rts-asset="${esc(item.asset)}">Mark RTS</button>`);
     }
   }
   return `<div class="item-card"><div><strong>${esc(item.asset)}</strong>
     <small>${details}</small></div>${actions.join('')}</div>`;
 };
 function render(){
   const host=root();if(!host)return;
   if(!status){host.innerHTML='<p class="field-help">Asset status is unavailable. Refresh to retry.</p>';return;}
   const oos=status.oos||[],rts=status.rts||[];
   host.innerHTML=`<div class="pane-header">
       <div><h3>Asset Status</h3><p>OOS persists across shifts. RTS shows this shift's confirmed returns.</p></div>
       <button type="button" class="btn btn-primary" id="v31-oos-add-btn">+ Add</button>
     </div>
     <div id="v31-oos-add-form" hidden style="padding:14px;border:1px solid #d9e4f3;border-radius:10px;margin:12px 0;">
       <div class="field"><label for="v31-oos-asset">Asset ID</label>
         <input id="v31-oos-asset" list="v31-oos-asset-suggestions" maxlength="120" autocomplete="off"
          placeholder="Search V2 assets or type an Asset ID" aria-describedby="v31-asset-help" />
         <datalist id="v31-oos-asset-suggestions"></datalist>
         <small id="v31-asset-help">Choose from the facility catalog or enter a new asset.</small></div>
       <div id="v31-oos-status-actions" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;" hidden>
         <button type="button" class="btn btn-light" id="v31-oos-mark-oos">Mark OOS</button>
         <button type="button" class="btn btn-primary" id="v31-oos-mark-rts">Mark RTS</button>
       </div>
       <button type="button" class="btn btn-light btn-small" id="v31-oos-cancel" style="margin-top:10px;">Cancel</button>
       <p id="v31-oos-error" class="field-help" role="alert"></p>
     </div>
     <div class="pane-header"><div><h3>Currently Out of Service · ${oos.length}</h3></div></div>
     <div class="item-list" id="v31-oos-list">${oos.length?oos.map(x=>statusCard(x,'oos')).join(''):
       '<p class="field-help">No equipment currently marked OOS.</p>'}</div>
     <div class="pane-header"><div><h3>Returned to Service (this shift) · ${rts.length}</h3></div></div>
     <div class="item-list" id="v31-oos-rts-list">${rts.length?rts.map(x=>statusCard(x,'rts')).join(''):
       '<p class="field-help">No equipment marked RTS this shift.</p>'}</div>`;
   const box=$('#v31-oos-asset',host),choice=$('#v31-oos-status-actions',host);
   $('#v31-oos-add-btn',host)?.addEventListener('click',async()=>{
     $('#v31-oos-add-form',host).hidden=false;
     $('#v31-oos-add-btn',host).hidden=true;
     box.focus();
     try{
       if(!catalog)catalog=(await api('/api/assets/catalog')).assets||[];
       suggestions();
     }catch(e){$('#v31-asset-help',host).textContent='Catalog unavailable; type a new Asset ID.';}
   });
   $('#v31-oos-cancel',host)?.addEventListener('click',()=>{
     $('#v31-oos-add-form',host).hidden=true;
     $('#v31-oos-add-btn',host).hidden=false;
   });
   function suggestions(){
     const list=$('#v31-oos-asset-suggestions',host);list.replaceChildren();
     const query=box.value.trim().toLowerCase();
     if(!query)return;
     const exact=[],partial=[];
     for(const asset of (catalog||[])){
       if(asset.toLowerCase().startsWith(query))exact.push(asset);
       else if(asset.toLowerCase().includes(query))partial.push(asset);
       if(exact.length>=16)break;
     }
     for(const asset of exact.concat(partial).slice(0,16)){
       const opt=document.createElement('option');opt.value=asset;list.append(opt);
     }
   }
   box.addEventListener('input',()=>{choice.hidden=!box.value.trim();suggestions();});
   const perform=async action=>{
     const asset=box.value.trim();
     if(!asset){$('#v31-oos-error',host).textContent='Asset ID is required.';return;}
     if(action==='rts' && !window.confirm('Confirm '+asset+' is physically safe and returned to service?'))return;
     const buttons=[...host.querySelectorAll('#v31-oos-status-actions button')];
     buttons.forEach(b=>b.disabled=true);
     $('#v31-oos-error',host).textContent='';
     try{
       await api(action==='oos'?'/api/oos':'/api/oos/rts',{asset});
       await reloadAfterChange();
       toast(asset+(action==='oos'?' marked OOS':' marked RTS'));
     }catch(e){$('#v31-oos-error',host).textContent=e.message;buttons.forEach(b=>b.disabled=false);}
   };
   $('#v31-oos-mark-oos',host)?.addEventListener('click',()=>perform('oos'));
   $('#v31-oos-mark-rts',host)?.addEventListener('click',()=>perform('rts'));
   host.querySelectorAll('[data-rts-asset]').forEach(button=>button.addEventListener('click',async()=>{
     const asset=button.dataset.rtsAsset;
     if(!window.confirm('Verify '+asset+' is safe and physically back in service?'))return;
     button.disabled=true;
     try{await api('/api/oos/rts',{asset});await reloadAfterChange();toast(asset+' marked RTS');}
     catch(e){button.disabled=false;toast(e.message);}
   }));
   host.querySelectorAll('[data-release-hold]').forEach(button=>button.addEventListener('click',async()=>{
     const asset=button.dataset.releaseHold;
     if(!window.confirm('Release the explicit OOS hold on '+asset+'? This does not mark the asset RTS.'))return;
     button.disabled=true;
     try{await api('/api/breakdowns/hold',{asset,enabled:false});await reloadAfterChange();toast('OOS hold released');}
     catch(e){button.disabled=false;toast(e.message);}
   }));
   host.querySelectorAll('[data-breakdown-rts], [data-breakdown-oos]').forEach(button=>button.addEventListener('click',async()=>{
     const wo=button.dataset.breakdownRts||button.dataset.breakdownOos;
     const decision=button.hasAttribute('data-breakdown-rts')?'rts':'keep_oos';
     if(!window.confirm(decision==='rts'?
       'Confirm this repaired Breakdown WO# '+wo+' is physically safe and RTS?':
       'Keep Breakdown WO# '+wo+' out of service?'))return;
     button.disabled=true;
     try{
       const result=await api('/api/breakdowns/'+encodeURIComponent(wo)+'/decision',{decision});
       await reloadAfterChange();
       toast(result.resolved?'Breakdown RTS confirmed':'Breakdown decision recorded; OOS restrictions remain active');
     }catch(e){button.disabled=false;toast(e.message);}
   }));
 }
 async function refresh(){
   if(loading)return;
   loading=true;
   try{
     const [newStatus,manual]=await Promise.all([api('/api/assets/status'),api('/api/oos')]);
     status=newStatus;
     window.__v31ManualOOS={open:manual.open||[],rts_this_shift:manual.resolved_this_shift||[]};
     window.v31ReportView?.setAssetStatus?.(status);
     render();
   }catch(e){
     if(!status){window.v31ReportView?.setAssetStatus?.(null);render();}
     toast('Asset status could not load: '+e.message);
   }finally{loading=false;}
 }
 async function reloadAfterChange(){
   await refresh();
   // Report readiness changes after manual status updates; import state does
   // not need mutation. Refresh it without chaining from refresh() itself.
   if(window.V31Reporting?.refresh)await window.V31Reporting.refresh();
 }
 window.v31AssetManager={refresh};
 window.addEventListener('v31:breakdown-tab',()=>refresh());
 refresh();
})();
