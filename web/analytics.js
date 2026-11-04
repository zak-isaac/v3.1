/* IND3 V3.1 database-backed Reliability Analytics. No illustrative data. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const root=$('v31-analytics-screen');
  if(!root)return;
  const kpis=[
    ['shifts','Shifts Recorded','Distinct S1–S5 shift-date reports'],
    ['work_orders','Included Work Orders','Excludes hidden and manually excluded records'],
    ['completed_pms','Completed PMs','Completed/Closed Cetaris PM WOs'],
    ['pm_needing_completion','PMs Needing Completion','From separate uploaded PM report'],
    ['breakdowns','Breakdown WOs','Imported and manual Breakdown category'],
    ['oos','Open OOS Statuses','Status in report, not downtime hours'],
    ['rts','RTS Statuses','Reported returned to service'],
    ['manual_happenings','Manual Happenings','Proactive / PM findings / work requests'],
    ['saved_passdowns','Saved Passdowns','Canonical snapshot saved'],
    ['closed_without_passdown','Closed Without Passdown','Operational exception to review']
  ];
  const el=(tag,cls,value)=>{
    const x=document.createElement(tag);
    if(cls)x.className=cls;
    if(value!==undefined)x.textContent=value;
    return x;
  };
  function empty(target,message){
    const body=$(target);
    body.replaceChildren(el('p','v31-analytics-empty',message));
  }
  function displayKpis(values){
    const target=$('v31-analytics-kpis');
    target.replaceChildren();
    kpis.forEach(([id,label,note])=>{
      const card=el('article','v31-analytics-kpi');
      card.append(el('span','v31-kpi-label',label),
                  el('strong','v31-kpi-value',Number(values[id]||0).toLocaleString('en-US')),
                  el('span','v31-kpi-detail',note));
      target.append(card);
    });
  }
  function bars(targetId,data,keyName,keyCount){
    const target=$(targetId);target.replaceChildren();
    const nonzero=data.filter(x=>Number(x[keyCount]||0)>0);
    if(!nonzero.length){empty(targetId,'No qualifying recorded work in this period.');return;}
    const high=Math.max(1,...data.map(x=>Number(x[keyCount]||0)));
    for(const item of data){
      const n=Math.max(0,Number(item[keyCount]||0));
      const r=el('div','v31-analytics-bar-row');
      const label=el('span','v31-analytics-bar-label',String(item[keyName]||'Unclassified'));
      label.title=label.textContent;
      const track=el('div','v31-analytics-bar-track');
      const fill=el('div','v31-analytics-bar-fill');
      fill.style.width=(n/high*100).toFixed(2)+'%';
      track.append(fill);
      r.append(label,track,el('span','v31-analytics-bar-value',String(n)));
      target.append(r);
    }
  }
  function trend(data){
    const target=$('v31-analytics-trend');target.replaceChildren();
    if(!data.length){empty('v31-analytics-trend','No recorded shifts in this period.');return;}
    const legend=el('div','v31-analytics-legend');
    legend.append(el('span',null,'Work Orders'),el('span',null,'Completed PMs'));
    for(const [i,child] of [...legend.children].entries()){
      const key=el('span','v31-legend-dot'+(i===1?' v31-legend-pm':''));
      child.prepend(key);
    }
    target.append(legend);
    const bars=el('div','v31-analytics-trend');
    bars.style.cssText='width:100%;min-width:max-content;margin:0;overflow:visible';
    const max=Math.max(1,...data.map(x=>Number(x.work_orders||0)+Number(x.completed_pms||0)));
    for(const day of data){
      const wo=Number(day.work_orders||0),pm=Number(day.completed_pms||0);
      const col=el('div','v31-trend-day');
      const pmbar=el('div','v31-trend-pm');
      const wob=el('div','v31-trend-bar');
      pmbar.style.height=(pm/max*155)+'px';
      wob.style.height=(wo/max*155)+'px';
      col.title=day.date+': '+wo+' WOs; '+pm+' completed PMs';
      const dateLabel=day.date.slice(5);
      col.append(pmbar,wob,el('span','v31-trend-label',dateLabel));
      bars.append(col);
    }
    target.append(bars);
  }
  function notes(data){
    const target=$('v31-analytics-notes');target.replaceChildren();
    const t=data.totals||{};
    const lines=[
      'Passdown coverage: '+(t.report_coverage_pct===null?'Not applicable':t.report_coverage_pct+'%')+
        ' of recorded shifts have a saved snapshot.',
      'Open shifts: '+(t.active||0)+'; closed shifts without a saved passdown: '+(t.closed_without_passdown||0)+'.',
      'WO review items: '+(t.pending_import_reviews||0)+
        '; PENDING closures: '+(t.pending_closure||0)+'.',
      ...(data.notes||[])
    ];
    lines.forEach(line=>target.append(el('p','v31-analytics-note',line)));
  }
  let busy=false;
  async function load(){
    if(busy)return;
    busy=true;
    const error=$('v31-analytics-error');error.hidden=true;
    const btn=$('v31-analytics-refresh');btn.disabled=true;
    try{
      const period=$('v31-analytics-period').value;
      const resp=await fetch('/api/analytics/summary?period='+encodeURIComponent(period),
                             {credentials:'same-origin'});
      const data=await resp.json().catch(()=>({}));
      if(!resp.ok)throw Error(data.detail||'Could not retrieve analytics');
      const bounds=data.from?' · '+data.from+' to '+data.through:' · All recorded dates through '+data.through;
      $('v31-analytics-scope').textContent=data.scope+bounds;
      displayKpis(data.totals||{});
      bars('v31-analytics-shifts',data.by_shift||[],'code','work_orders');
      bars('v31-analytics-categories',data.by_category||[],'name','count');
      trend(data.trend||[]);
      notes(data);
    }catch(err){
      error.textContent='Analytics unavailable: '+err.message;
      error.hidden=false;
    }finally{btn.disabled=false;busy=false;}
  }
  $('v31-analytics-period').addEventListener('change',load);
  $('v31-analytics-refresh').addEventListener('click',load);
  window.addEventListener('v31:analytics',load);
})();
