/* IND3 V3.1 Reliability Analytics. Only actual shift/report records. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('v31-analytics-screen')) return;
  const kpis = [
    ['work_orders','Reported Work Orders','Included WO entries across shift reports'],
    ['completed_pms','Completed PMs','Completed Cetaris preventive-maintenance WOs'],
    ['breakdowns','Breakdown WOs','Included work categorized as Breakdown'],
    ['oos','OOS Report Entries','Historical OOS statuses, not current downtime'],
    ['pending_closure','Pending WOs','Closures needing follow-up']
  ];

  const el=(tag,cls,value)=>{
    const node=document.createElement(tag);
    if(cls)node.className=cls;
    if(value!==undefined)node.textContent=String(value);
    return node;
  };
  const num=value=>Math.max(0,Number(value||0)).toLocaleString('en-US');
  const empty=(id,label)=>{
    const target=$(id);target.replaceChildren(el('p','v31-analytics-empty',label));
  };
  function cards(totals){
    const box=$('v31-analytics-kpis');box.replaceChildren();
    for(const [key,name,note] of kpis){
      const card=el('article','v31-analytics-kpi');
      card.append(el('span','v31-kpi-label',name),
                  el('strong','v31-kpi-value',num(totals[key])),
                  el('span','v31-kpi-detail',note));
      box.append(card);
    }
  }
  function bars(id,values,labelKey,valueKey,limit=12){
    const target=$(id);target.replaceChildren();
    const entries=(values||[]).filter(x=>Number(x[valueKey]||0)>0).slice(0,limit);
    if(!entries.length){
      empty(id,'No qualifying work recorded in this period.');
      return;
    }
    const max=Math.max(1,...entries.map(x=>Number(x[valueKey])));
    for(const item of entries){
      const n=Number(item[valueKey]||0);
      const row=el('div','v31-analytics-bar-row');
      const label=el('span','v31-analytics-bar-label',item[labelKey]||'Unclassified');
      label.title=label.textContent;
      const track=el('div','v31-analytics-bar-track');
      const fill=el('div','v31-analytics-bar-fill');
      fill.style.width=(100*n/max).toFixed(1)+'%';
      track.append(fill);
      row.append(label,track,el('span','v31-analytics-bar-value',num(n)));
      target.append(row);
    }
  }
  function trend(days){
    const target=$('v31-analytics-trend');target.replaceChildren();
    if(!days.length){
      empty('v31-analytics-trend','No recorded shift dates in this period.');
      return;
    }
    const legend=el('div','v31-analytics-legend');
    for(const [cls,label] of [['v31-legend-dot','Reported WOs'],
                               ['v31-legend-dot v31-legend-pm','Completed PMs']]){
      const item=el('span');
      item.append(el('span',cls),document.createTextNode(label));
      legend.append(item);
    }
    const series=el('div','v31-analytics-trend');
    const max=Math.max(1,...days.map(d=>Number(d.work_orders||0)+Number(d.completed_pms||0)));
    for(const day of days){
      const wo=Number(day.work_orders||0),pm=Number(day.completed_pms||0);
      const col=el('div','v31-trend-day');
      const pmBar=el('div','v31-trend-pm');
      const woBar=el('div','v31-trend-bar');
      pmBar.style.height=(pm/max*152).toFixed(1)+'px';
      woBar.style.height=(wo/max*152).toFixed(1)+'px';
      col.title=day.date+': '+wo+' WOs and '+pm+' completed PMs';
      col.append(pmBar,woBar,el('span','v31-trend-label',day.date.slice(5)));
      series.append(col);
    }
    target.append(legend,series);
  }
  function table(id,headers,rows,emptyText){
    const target=$(id);target.replaceChildren();
    if(!rows.length){empty(id,emptyText);return;}
    const wrapper=el('div','v31-analytics-table-wrap');
    const tbl=el('table','v31-analytics-table');
    const thead=el('thead'),tr=el('tr');
    for(const name of headers)tr.append(el('th',null,name));
    thead.append(tr);
    const tbody=el('tbody');
    for(const cells of rows){
      const line=el('tr');
      for(const [index,value] of cells.entries()){
        const cell=el('td',index===0?'v31-analytics-cell-primary':null,value);
        line.append(cell);
      }
      tbody.append(line);
    }
    tbl.append(thead,tbody);wrapper.append(tbl);target.append(wrapper);
  }
  function equipment(data){
    table('v31-analytics-assets',
      ['Asset ID','Repair WOs','Breakdowns','Proactive','Last Report'],
      (data.top_assets||[]).map(r=>[
        r.asset,num(r.work_orders),num(r.breakdowns),
        num(r.proactive_repairs),r.last_shift_day||'—'
      ]),
      'No included repairs with an Asset ID in this period.');
    table('v31-analytics-repeats',
      ['Asset ID','Breakdown WOs','Additional WOs','Last Report'],
      (data.repeat_breakdown_assets||[]).map(r=>[
        r.asset,num(r.breakdowns),num(r.repeat_breakdown_wos),
        r.last_shift_day||'—'
      ]),
      'No asset has two distinct breakdown WOs in this period.');
    bars('v31-analytics-causes',
         data.common_breakdown_descriptions||[],'description','work_orders',8);
  }
  function shifts(data){
    table('v31-analytics-shifts',
      ['Shift','Reports','WO Entries','Completed PMs','Breakdowns','Pending','Saved Passdowns'],
      (data.by_shift||[]).map(r=>[
        r.code,num(r.shifts),num(r.work_orders),num(r.completed_pms),
        num(r.breakdowns),num(r.pending_closure),num(r.saved_passdowns)
      ]),
      'No shift reports recorded yet.');
  }
  function notes(data){
    const target=$('v31-analytics-notes');target.replaceChildren();
    const t=data.totals||{};
    const coverage=t.report_coverage_pct===null?'Not applicable':
      t.report_coverage_pct+'%';
    const messages=[
      'Saved passdown coverage: '+coverage+' of '+num(t.shifts)+' recorded shifts.',
      num(t.closed_without_passdown)+' closed shifts lack a saved passdown; '+
        num(t.pending_import_reviews)+' Cetaris import review items remain.',
      num(t.proactive_repairs)+' proactive-repair entries; '+
        num(t.manual_happenings)+' manual happenings; '+
        num(t.pm_needing_completion)+' PMs needing completion reported.',
      'Equipment records: '+num(t.assets_reported)+' assets with repairs; '+
        num(t.repeat_breakdown_assets)+' with repeat breakdown WOs; '+
        num(t.missing_asset_entries)+' work-order entries lack an Asset ID.',
      ...(data.notes||[])
    ];
    for(const message of messages)target.append(el('p','v31-analytics-note',message));
  }
  let loading=false,queued=false;
  async function load(){
    if(loading){queued=true;return;}
    loading=true;
    const button=$('v31-analytics-refresh'),error=$('v31-analytics-error');
    error.hidden=true;button.disabled=true;
    try{
      const period=$('v31-analytics-period').value;
      const response=await fetch('/api/analytics/summary?period='+encodeURIComponent(period),
                                 {credentials:'same-origin'});
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw Error(data.detail||'Analytics could not load.');
      const bounds=data.from?' · '+data.from+'–'+data.through:
        ' · All shift dates through '+data.through;
      $('v31-analytics-scope').textContent=data.scope+bounds;
      cards(data.totals||{});
      bars('v31-analytics-categories',data.by_category||[],'name','count');
      trend(data.trend||[]);
      equipment(data);
      shifts(data);
      notes(data);
    }catch(err){
      error.textContent='Analytics unavailable: '+err.message;error.hidden=false;
    }finally{
      button.disabled=false;loading=false;
      if(queued){queued=false;load();}
    }
  }
  $('v31-analytics-period').addEventListener('change',load);
  $('v31-analytics-refresh').addEventListener('click',load);
  window.addEventListener('v31:analytics',load);
})();