/* V2-style PMs Needing Completion file workflow, separate from completed Cetaris PM WOs. */
(()=>{
  'use strict';
  const picker=document.querySelector('#pending-picker');
  const btn=document.querySelector('#upload-pending');
  const target=document.querySelector('#pending-filename');
  const area=document.querySelector('.pm-action');
  if(!picker||!btn||!target||!area)return;
  const tools=document.createElement('div');
  tools.style.cssText='display:flex;gap:7px;flex-wrap:wrap;margin-top:8px';
  const download=document.createElement('button');
  download.type='button';download.className='btn btn-light btn-small';download.textContent='Download File';
  const remove=document.createElement('button');
  remove.type='button';remove.className='btn btn-light btn-small';remove.textContent='Remove File';
  tools.append(download,remove);area.append(tools);
  const fetchState=async(method='GET',data=null,url='/api/pm-needing-completion')=>{
    const opts={method,credentials:'same-origin'};
    if(data)opts.body=data;
    const response=await fetch(url,opts);
    const body=await response.json().catch(()=>({}));
    if(!response.ok)throw Error(body.detail||'PM report request failed');
    return body;
  };
  async function refresh(){
    const pm=await fetchState();
    target.textContent=pm.present ? pm.filename+' · '+(pm.parsed?pm.count+' PM work orders':'reference attachment') :
      'No PMs Needing Completion report uploaded';
    download.hidden=remove.hidden=!pm.present;
    document.querySelector('.pm-action small:not(#pending-filename)')?.replaceChildren(
      document.createTextNode(pm.present?
        (pm.parsed?'Work orders imported from the separate unfinished-PM report':'Reference file saved; contents not parsed') :
        'Upload the separate V2-style PMs Needing Completion report'));
    return pm;
  }
  picker.addEventListener('change',async()=>{
    const file=picker.files?.[0];picker.value='';
    if(!file)return;
    const form=new FormData();form.append('file',file);
    btn.disabled=true;target.textContent='Uploading PM report…';
    try{
      const pm=await fetchState('POST',form,'/api/pm-needing-completion/upload');
      await refresh();
      window.V31Reporting?.refresh?.().catch(()=>{});
      document.querySelector('#last-saved').textContent=
        'PMs Needing Completion: '+(pm.parsed?pm.count+' work orders saved':'reference saved');
    }catch(error){target.textContent=error.message;window.alert(error.message);}
    finally{btn.disabled=false;}
  });
  download.addEventListener('click',()=>{window.location.href='/api/pm-needing-completion/download';});
  remove.addEventListener('click',async()=>{
    if(!window.confirm('Remove the uploaded PMs Needing Completion report for this shift?'))return;
    try{await fetchState('DELETE');await refresh();window.V31Reporting?.refresh?.().catch(()=>{});}
    catch(error){window.alert(error.message);}
  });
  refresh().catch(error=>{target.textContent='PM report not loaded: '+error.message;});
})();
