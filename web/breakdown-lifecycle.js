/* V3-style global Breakdown lifecycle, separate from manual OOS incident creation.
   Statuses are truthful: In Process=OOS, Completed=review, Closed=RTS unless held. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);
 const root=()=>$('v31-breakdown-lifecycle');
 const el=(tag,txt,cls)=>{const node=document.createElement(tag);
   if(txt!==undefined)node.textContent=txt;if(cls)node.className=cls;return node;};
 const api=async(path,body=null)=>{
   const opts={credentials:'same-origin',method:body===null?'GET':'POST'};
   if(body!==null){opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(body);}
   const response=await fetch(path,opts);
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw Error(data.detail||'Breakdown action failed');
   return data;
 };
 const notify=msg=>{
   window.v31ReportView?.toast?.(msg);
 };
 async function load(){
   const host=root();if(!host)return;
   host.replaceChildren();
   try{
     const state=await api('/api/breakdowns');
     const heading=el('div',undefined,'pane-header');
     const title=el('div');
     title.append(el('h3','Cetaris Breakdown Lifecycle'),
       el('p','V3 policy: In Process → OOS, Completed → review, Closed → RTS only when safe.'));
     const refresh=el('button','Refresh','btn btn-light btn-small');
     refresh.type='button';refresh.addEventListener('click',()=>load());
     heading.append(title,refresh);host.append(heading);
     const pending=state.pending||[];
     if(pending.length){
       host.append(el('h4','Completed Breakdown — verify actual equipment status'));
       for(const b of pending){
         const item=el('div',undefined,'item-card');
         item.append(el('strong',(b.asset||'Unknown asset')+' · WO# '+b.wo_number));
         const actions=el('div');
         const choose=(choice,label)=>{
           const button=el('button',label,'btn btn-light btn-small');
           button.type='button';
           button.addEventListener('click',async()=>{
             if(!window.confirm(choice==='rts'?
                'Confirm '+b.asset+' is actually returned to service?':
                'Keep '+b.asset+' out of service and include it in passdown?'))return;
             button.disabled=true;
             try{
               const result=await api('/api/breakdowns/'+encodeURIComponent(b.wo_number)+'/decision',
                                      {decision:choice});
               notify(result.resolved?'Breakdown RTS recorded':'Equipment remains OOS / decision saved');
               await load();await window.V31Reporting?.refresh?.();
             }catch(err){notify(err.message);button.disabled=false;}
           });
           return button;
         };
         actions.append(choose('rts','Confirm RTS'),choose('keep_oos','Keep OOS'));
         item.append(actions);host.append(item);
       }
     }else{
       host.append(el('p','No Breakdown WOs currently need an RTS decision.','field-help'));
     }
     const open=state.open||[];
     if(open.length){
       host.append(el('h4','Still OOS / Awaiting Verified Closure'));
       for(const b of open){
         const card=el('div',undefined,'item-card');
         card.append(el('strong',(b.asset||'Unknown asset')+' · WO# '+b.wo_number));
         card.append(el('small',b.status==='completed'?'Completed — '+(b.decision==='keep_oos'?'keep OOS':'decision pending'):'In Process — OOS'));
         host.append(card);
       }
     }
     const holds=state.holds||[];
     host.append(el('h4','Manual Holds · '+holds.length));
     const form=el('form',undefined,'v31-oos-hold-form');
     form.style.cssText='display:flex;flex-wrap:wrap;align-items:center;gap:8px';
     const asset=el('input');
     asset.type='text';asset.maxLength=120;asset.placeholder='Asset ID to hold OOS';
     asset.required=true;asset.style.cssText='flex:1;min-width:130px;padding:9px;border:1px solid #d3dfea;border-radius:6px';
     const reason=el('input');reason.type='text';reason.maxLength=500;reason.placeholder='Reason (optional)';
     reason.style.cssText='flex:2;min-width:170px;padding:9px;border:1px solid #d3dfea;border-radius:6px';
     const button=el('button','Keep Asset OOS','btn btn-light btn-small');button.type='submit';
     form.append(asset,reason,button);
     form.addEventListener('submit',async e=>{
       e.preventDefault();button.disabled=true;
       try{await api('/api/breakdowns/hold',{asset:asset.value.trim(),enabled:true,reason:reason.value.trim()});
         notify('Manual OOS hold saved');await load();}
       catch(err){notify(err.message);button.disabled=false;}
     });
     host.append(form);
     for(const entry of holds){
       const line=el('div',undefined,'item-card');
       line.append(el('strong',entry.asset),el('small',entry.reason||'Held out of service manually'));
       const unhold=el('button','Release Hold','btn btn-light btn-small');
       unhold.type='button';
       unhold.addEventListener('click',async()=>{
         if(!window.confirm('Release OOS hold for '+entry.asset+'? Verify equipment status first.'))return;
         try{await api('/api/breakdowns/hold',{asset:entry.asset,enabled:false});
           notify('Manual OOS hold cleared');await load();}
         catch(err){notify(err.message);}
       });
       line.append(unhold);host.append(line);
     }
     const rts=state.resolved_this_shift||[];
     if(rts.length){
       host.append(el('h4','RTS Verified in This Shift'));
       for(const item of rts){
         host.append(el('p',item.asset+' · WO# '+item.wo_number+' · '+item.actor,'field-help'));
       }
     }
   }catch(err){host.replaceChildren(el('p','Breakdown state unavailable: '+err.message,'field-help'));}
 }
 window.addEventListener('v31:breakdown-tab',load);
})();
