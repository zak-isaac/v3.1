/* Approved V3.1 login screens, driven by real local shift windows. */
(() => {
  'use strict';
  const $ = q => document.querySelector(q);
  const screens = {
    login: $('#screen-login'), active: $('#screen-active'), handoff: $('#screen-handoff')
  };
  ['preview-dock', 'prototype-modal'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
  const form = $('#login-form'), field = $('#associate-input'), error = $('#login-error');
  const zone = 'America/Indiana/Indianapolis';
  let signedIn = false, pending = null, flow = null, account = null;
  const show = name => {
    for (const [key, element] of Object.entries(screens)) element.hidden = key !== name;
    document.getElementById('v31-preload')?.remove();
    if (name === 'login') field.focus();
  };
  const dateLabel = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:zone,weekday:'long',month:'long',day:'numeric',year:'numeric'}).format(new Date(iso));
  const timeLabel = iso => new Intl.DateTimeFormat('en-US',
    {timeZone:zone,hour:'numeric',minute:'2-digit'}).format(new Date(iso));
  const timeRange = shift => timeLabel(shift.scheduled_start) + ' – ' + timeLabel(shift.scheduled_end);
  const grace = shift => 'Report window: ' + timeLabel(shift.opens_at) + ' – ' + timeLabel(shift.closes_at) + ' (+1 hour after shift)';
  const rosterHealth = status => {
    const health = status?.roster_health || 'unavailable';
    const label = health === 'connected' ? 'Drax Synced' :
      health === 'stale' ? 'Drax Sync Needs Attention' : 'Drax Not Connected';
    for (const dot of document.querySelectorAll('.page-footer .online-dot')) {
      const text = dot.nextElementSibling;
      if (text && text.tagName === 'SPAN') text.textContent = label;
      dot.style.backgroundColor = health === 'connected' ? '#28a962' :
        health === 'stale' ? '#db8e19' : '#ba3c42';
    }
  };
  async function api(path, data) {
    const opt = {credentials:'same-origin',headers:{Accept:'application/json'}};
    if (data !== undefined) {
      opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(data);
    }
    const response = await fetch(path, opt);
    if (!response.ok) {
      const json = await response.json().catch(() => ({}));
      throw Error(json.detail || 'Could not complete request');
    }
    return response.json();
  }
  const setDetails = (card, shift) => {
    const title = card.querySelector('.shift-detail-title h3');
    if (title) title.textContent = shift.code + ' – ' + shift.label;
    const rows = card.querySelectorAll('.detail-row span');
    if (rows[0]) rows[0].textContent = dateLabel(shift.scheduled_start);
    if (rows[1]) rows[1].textContent = timeRange(shift) + ' · ' + grace(shift);
  };
  // One associate-number popup shared by Resume and Start on the approved
  // Active Shift and New Shift Ready screens. Never bounce to screen one.
  const gate = document.createElement('dialog');
  gate.id = 'v31-associate-dialog';
  gate.setAttribute('aria-labelledby', 'v31-gate-title');
  gate.style.cssText = 'width:min(440px,calc(100vw - 28px));border:1px solid #d7e1ee;border-radius:18px;padding:0;box-shadow:0 25px 90px rgba(4,23,54,.27);color:#172b49;font-family:Segoe UI,Arial,sans-serif';
  gate.innerHTML = `<form id="v31-gate-form" style="padding:27px 30px 28px;display:grid;gap:13px">
    <h2 id="v31-gate-title" style="font-size:23px;margin:0;color:#071e42">Confirm associate number</h2>
    <p id="v31-gate-description" style="margin:0;color:#56667b;font-size:14px;line-height:1.5"></p>
    <label for="v31-gate-associate" style="font-weight:700;font-size:13px">Associate Number</label>
    <input id="v31-gate-associate" inputmode="numeric" autocomplete="off" maxlength="12" required
      placeholder="Enter your associate number"
      style="width:100%;height:49px;border-radius:9px;border:1px solid #b7c7db;padding:12px;font-size:18px;color:#142c50;background:white">
    <p id="v31-gate-error" role="alert" hidden style="margin:0;color:#a52024;font-size:13px"></p>
    <button type="submit" id="v31-gate-confirm" style="background:#0872e2;color:#fff;padding:13px;border:0;border-radius:10px;font-weight:700;font-size:15px">Continue to Shift Report</button>
    <button type="button" id="v31-gate-cancel" style="border:0;background:transparent;color:#536681;padding:8px;cursor:pointer">Cancel</button>
  </form>`;
  document.body.append(gate);
  const gateField = $('#v31-gate-associate'), gateError = $('#v31-gate-error');
  const gateButton = $('#v31-gate-confirm');
  let chosen = null;
  $('#v31-gate-cancel').addEventListener('click', () => gate.close());
  gate.addEventListener('close', () => {
    chosen = null; gateField.value = ''; gateError.hidden = true;
    refreshEntry();
  });
  gateField.addEventListener('input', () => { gateError.hidden = true; });
  const enterReport = () => {
    // Hide the two-pane login page during navigation to avoid a split-page flash.
    document.body.style.visibility = 'hidden';
    document.documentElement.style.background = '#fff';
    window.location.replace('/shift-report.html');
  };
  const useDifferent = async () => {
    chosen = null;
    if (signedIn) {
      try { await api('/api/logout', {}); } catch (e) { console.warn(e); }
      signedIn = false; account = null;
    }
    field.value = '';
    error.hidden = true;
    show('login');
  };
  const resume = shift => choose({kind:'resume', shift});
  const start = shift => choose({kind:'start', shift});
  function choose(choice) {
    chosen = choice;
    gateError.hidden = true;
    const label = (choice.kind === 'resume' ? 'resume ' : 'start ') + choice.shift.code +
      ' (' + dateLabel(choice.shift.scheduled_start) + ')';
    $('#v31-gate-description').textContent = 'Enter your associate number to ' +
      label + '. You will open that shift\'s reporting workspace.';
    gateButton.textContent = (choice.kind === 'resume' ? 'Resume ' : 'Start ') + choice.shift.code + ' Shift';
    gate.showModal();
    gateField.focus();
  }
  $('#v31-gate-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!chosen) return;
    const associate = gateField.value.trim();
    if (!/^\d{1,12}$/.test(associate)) {
      gateError.textContent = 'Enter a valid associate number using digits only.';
      gateError.hidden = false; return;
    }
    gateButton.disabled = true; gateError.hidden = true;
    const choice = chosen;
    try {
      await api('/api/shifts/enter', {
        associate, action: choice.kind,
        ...(choice.kind === 'resume' ? {shift_id:choice.shift.id} : {code:choice.shift.code})
      });
      signedIn = true;
      enterReport();
    } catch (e) {
      gateError.textContent = e.message;
      gateError.hidden = false;
    } finally { gateButton.disabled = false; }
  });


  // A manager's FIRST login always opens this chooser, even with one active
  // shift. Shift permissions remain checked by the backend for every action.
  const managerPicker = document.createElement('dialog');
  managerPicker.id = 'v31-manager-shift-picker';
  managerPicker.style.cssText = 'width:min(640px,calc(100vw - 28px));max-height:min(85dvh,740px);overflow:auto;border:1px solid #d6e2f0;border-radius:18px;padding:0;background:#fff;color:#132f52;box-shadow:0 25px 90px #0b275444;font-family:Segoe UI,Arial,sans-serif';
  managerPicker.innerHTML = `<div style="padding:25px 27px 24px">
    <h2 style="font-size:24px;margin:0 0 7px">Choose your shift</h2>
    <p style="font-size:14px;color:#60728a;margin:0 0 16px;line-height:1.5">Management access · Select an available S1–S5 shift report. Start and Resume are separate sessions.</p>
    <div id="v31-manager-choices" style="display:grid;gap:9px"></div>
    <p role="alert" id="v31-manager-error" hidden style="color:#aa242b;font-size:13px"></p>
    <button id="v31-manager-roster" type="button" style="border:1px solid #d6e2ef;background:white;color:#194e86;border-radius:8px;padding:10px 13px;font-weight:700;cursor:pointer">Administration</button>
    <button id="v31-manager-different" type="button" style="border:0;background:transparent;color:#35628d;font-weight:650;padding:13px 0 0;cursor:pointer">Use a different associate number</button>
  </div>`;
  document.body.append(managerPicker);
  $('#v31-manager-roster').addEventListener('click',()=> {
    if(window.v31OpenDraxAdmin) window.v31OpenDraxAdmin();
  });
  $('#v31-manager-different').addEventListener('click', () => {
    managerPicker.close();
    useDifferent();
  });
  async function showManagerPicker(updatedAccount = null) {
    // A manager may leave this screen open while shifts change.
    account = updatedAccount || await api('/api/me');
    const list = $('#v31-manager-choices');
    list.replaceChildren();
    const errorNode = $('#v31-manager-error');
    errorNode.hidden = true;
    for (const entry of (account.catalog || [])) {
      const row = document.createElement('div');
      row.style.cssText='display:flex;gap:12px;align-items:center;justify-content:space-between;padding:13px;border:1px solid #e1e8f1;border-radius:12px;background:#fff';
      const label = document.createElement('div');
      label.style.cssText='display:grid;gap:4px;min-width:0';
      const heading = document.createElement('strong');
      heading.textContent = entry.code + ' — ' + entry.label;
      const status = document.createElement('small');
      status.style.cssText='color:#687a90;font-size:12px';
      status.textContent = entry.action === 'resume' ? 'Open report · ' + entry.date :
        entry.action === 'start' ? 'Ready to start · ' + entry.date : 'Outside reporting window';
      label.append(heading,status);
      const button=document.createElement('button');
      button.type='button';
      button.textContent=entry.action==='resume'?'Resume':entry.action==='start'?'Start':'Unavailable';
      button.disabled=entry.action==='unavailable';
      button.style.cssText='min-width:98px;background:#0968d5;color:white;border:0;border-radius:9px;padding:10px 13px;font-weight:700;cursor:pointer';
      button.addEventListener('click', async () => {
        button.disabled=true;
        errorNode.hidden=true;
        try {
          await api(entry.action==='resume'?'/api/shifts/resume':'/api/shifts/start',
            entry.action==='resume'?{shift_id:entry.id}:{code:entry.code});
          enterReport();
        } catch(err) {
          errorNode.textContent=err.message;
          errorNode.hidden=false;
          button.disabled=false;
        }
      });
      row.append(label,button);
      list.append(row);
    }
    show('login');
    if (!managerPicker.open) managerPicker.showModal();
  }

  function updateActive(shift) {
    const box = $('#screen-active .active-shift-box');
    box.querySelector('h3').textContent = shift.code + ' — ' + shift.label;
    const rows = box.querySelectorAll('.shift-card-details > p');
    if (rows[0]) rows[0].textContent = dateLabel(shift.scheduled_start);
    if (rows[1]) rows[1].textContent = timeRange(shift) + ' · ' + grace(shift);
    $('#associate-display').textContent = signedIn && account ? account.associate : 'Sign in to resume';
    const verification = $('#screen-active .verified');
    if (verification) verification.textContent = signedIn ? 'Signed in' : 'Shift available';
    const owner = box.querySelector('.shift-card-metadata strong');
    if (owner) owner.textContent = 'IND3 shift session';
    $('#resume-active').onclick = () => resume(shift);
  }
  function updateHandoff(previous, next) {
    const openCount=(flow?.open||[]).length;
    const cards = document.querySelectorAll('#screen-handoff .choice-card');
    setDetails(cards[0], previous);
    setDetails(cards[1], next);
    const older = cards[0].querySelector('.choice-heading h2');
    if (older) older.textContent = previous.action === 'resume' ? 'Current Open Session' : 'Available Shift';
    const title = $('#handoff-title');
    title.textContent = previous.action === 'resume' ? 'New Shift Ready' : 'Choose Your Shift';
    const subtitle = $('#screen-handoff .handoff-heading > p');
    if (subtitle) subtitle.textContent = 'Choose which shift report to open. Each shift remains independent.';
    const oldStatus = cards[0].querySelector('.status-pill');
    if (oldStatus) oldStatus.textContent = previous.action === 'resume' ? 'Open' : 'Ready';
    $('#resume-previous').textContent = previous.action === 'resume' ? 'Resume Existing Shift' : 'Start This Shift';
    $('#start-new').textContent = next.action === 'resume' ? 'Resume This Shift' : 'Start New Shift';
    const nextStatus=cards[1].querySelector('.status-pill');
    if(nextStatus)nextStatus.textContent=next.action === 'resume'?'Open':'Ready';
    if(openCount===2){
      $('#handoff-title').textContent='Two Active Shifts';
      if(subtitle)subtitle.textContent='Both reports are active. Choose which shift to resume. Signing out does not end either report.';
    }
    $('#resume-previous').onclick = () => (previous.action === 'resume' ? resume(previous) : start(previous));
    $('#start-new').onclick = () => (next.action === 'resume' ? resume(next) : start(next));
    const oldTrack = $('.timeline-old'), newTrack = $('.timeline-new');
    if (oldTrack) oldTrack.textContent = previous.code + ' – ' + previous.label;
    if (newTrack) newTrack.textContent = next.code + ' – ' + next.label;
    const timelineNote = $('#screen-handoff .timeline-card > p');
    if (timelineNote) timelineNote.textContent = 'Reporting windows overlap. New shifts open 1 hour early; prior shifts close 1 hour late.';
    const times = $('#screen-handoff .timeline-times');
    if (times) {
      const cells = times.querySelectorAll(':scope > div');
      [
        [previous.scheduled_start, previous.scheduled_start],
        [next.scheduled_start, next.scheduled_start],
        [next.scheduled_end, next.scheduled_end]
      ].forEach(([timestamp, day], i) => {
        if (!cells[i]) return;
        const small = document.createElement('small');
        small.textContent = new Intl.DateTimeFormat('en-US',
          {timeZone:zone,month:'short',day:'numeric'}).format(new Date(day));
        cells[i].replaceChildren(timeLabel(timestamp), small);
      });
    }
    // Keep all additional eligible shift choices available without altering the approved two cards.
    const choices = $('#screen-handoff .handoff-choices');
    const existing = document.getElementById('v31-extra-choices');
    if (existing) existing.remove();
    const all = [...(flow?.open || []), ...(flow?.ready || [])];
    const extra = all.filter(x => !(x.code === previous.code && x.date === previous.date) &&
      !(x.code === next.code && x.date === next.date));
    if (extra.length) {
      const section = document.createElement('div');
      section.id = 'v31-extra-choices';
      section.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin:16px 0';
      extra.forEach(item => {
        const button = document.createElement('button');
        button.className = 'button button-outline';
        button.type = 'button';
        button.textContent = (item.action === 'resume' ? 'Resume ' : 'Start ') + item.code + ' · ' + timeRange(item);
        button.addEventListener('click', () => item.action === 'resume' ? resume(item) : start(item));
        section.append(button);
      });
      choices.insertAdjacentElement('afterend', section);
    }
  }
  function render() {
    rosterHealth(flow);
    const open = flow?.open || [], ready = flow?.ready || [];
    // Treat two already-open shifts as a real overlap (e.g. S4+S5,
    // S1+S2). Previously this rendered only the Active Shift page.
    const choices=[...open,...ready];
    document.getElementById('v31-active-extra')?.remove();
    if (choices.length >= 2) {
      const pair = open.length && ready.length
        ? [open[open.length-1],ready[0]]
        : [choices[0],choices[1]];
      updateHandoff(pair[0],pair[1]);
      show('handoff');
    } else if (open.length === 1) {
      updateActive(open[0]);
      show('active');
    } else {
      show('login');
    }
  }
  $('#different-associate').addEventListener('click', useDifferent);
  $('#handoff-different').addEventListener('click', useDifferent);
  field.addEventListener('input', () => error.hidden = true);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const assoc = field.value.trim();
    if (!/^\d{1,12}$/.test(assoc)) {
      error.textContent = 'Enter your associate number using digits only.';
      error.hidden = false; return;
    }
    const btn = form.querySelector('[type=submit]');
    error.hidden = true; btn.disabled = true;
    try {
      const answer = await api('/api/login', {associate:assoc});
      signedIn = true;
      account = await api('/api/me');
      if (answer.role === 'manager' || answer.manager_selection_required) {
        await showManagerPicker();
        return;
      }
      if (answer.destination === '/shift-report.html') {
        enterReport(); return;
      }
      flow = {open:account.open, ready:account.ready};
      render();
      if (!flow.open.length && !flow.ready.length) {
        error.textContent = 'No shift is within its reporting window. A shift opens 1 hour before its start and closes 1 hour after its end.';
        error.hidden = false;
      }
    } catch (err) {
      error.textContent = err.message; error.hidden = false; show('login');
    } finally { btn.disabled = false; }
  });
  async function init() {
    try {
      flow = await api('/api/entry');
      rosterHealth(flow);
      const current = await fetch('/api/me', {credentials:'same-origin'});
      if (current.ok) {
        account = await current.json(); signedIn = true;
        flow = {open:account.open, ready:account.ready};
        if (account.role === 'manager' && !account.selected) {
          await showManagerPicker();
          return;
        }
      }
      render();
    } catch (e) {
      rosterHealth({roster_health:'unavailable'});
      error.textContent = 'Could not check active shifts: ' + e.message;
      error.hidden = false; show('login');
    }
  }
  // Keep an unattended login display synchronized at shift handoff.
  // Do not disrupt an open identity confirmation or manager dialog.
  async function refreshEntry() {
    if (document.hidden || gate.open || pending) return;
    if (signedIn && !managerPicker.open) return;
    pending = true;
    try {
      if (signedIn && managerPicker.open) {
        const latestAccount = await api('/api/me');
        if (JSON.stringify(latestAccount.catalog || []) !== JSON.stringify(account?.catalog || [])) {
          await showManagerPicker(latestAccount);
        } else {
          account = latestAccount;
        }
        return;
      }
      const latest = await api('/api/entry');
      const oldChoices = JSON.stringify([flow?.open, flow?.ready]);
      const newChoices = JSON.stringify([latest.open, latest.ready]);
      flow = latest;
      rosterHealth(latest);
      if (oldChoices !== newChoices) render();
    } catch (error) {
      if (managerPicker.open) {
        const node = $('#v31-manager-error');
        node.textContent = 'Could not refresh shift choices: ' + error.message;
        node.hidden = false;
      } else {
        rosterHealth({roster_health:'unavailable'});
      }
    } finally {
      pending = false;
    }
  }
  window.addEventListener('focus', () => { refreshEntry(); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshEntry();
  });
  setInterval(refreshEntry, 30000);
  init();
})();
