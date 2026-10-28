/* Management Administration without a selected shift. */
(async()=>{
'use strict';
const $=selector=>document.querySelector(selector);
const api=async(url,data)=>{
  const opts={credentials:'same-origin',headers:{Accept:'application/json'}};
  if(data!==undefined){
    opts.method='POST';opts.headers['Content-Type']='application/json';
    opts.body=JSON.stringify(data);
  }
  const resp=await fetch(url,opts);
  const result=await resp.json().catch(()=>({}));
  if(!resp.ok)throw Error(result.detail||'Request failed');
  return result;
};
let who;
try{who=await api('/api/me');}
catch(_){window.location.replace('/');return;}
if(who.role!=='manager'){window.location.replace('/');return;}
const username=$('#v31-user-name');
if(username)username.textContent=who.name||'Management';
const role=$('#v31-user-role');
if(role)role.textContent='Management · IND3';
const button=$('#account-button');
const display=button?.querySelector('span:not(.avatar-icon)');
if(display)display.textContent=who.name||'Management';
const choose=$('#v31-menu-choose');
if(choose){
 choose.hidden=false;
 choose.addEventListener('click',()=>{
   if(who.selected)window.location.assign('/shift-report.html?open-shift-chooser=1');
   else window.location.assign('/?shift-selection=1');
 });
}
$('#v31-menu-administration')?.addEventListener('click',()=>{
  $('#account-menu').hidden=true;
  window.v31Navigate?.('admin');
});
$('#v31-signout')?.addEventListener('click',async()=>{
 try{await api('/api/logout',{});}
 finally{window.location.replace('/?shift-selection=1');}
});
document.querySelector('[data-nav="report"]')?.addEventListener('click',e=>{
 e.preventDefault();e.stopImmediatePropagation();
 window.location.assign('/');
},true);
window.v31Navigate?.('admin');
})();