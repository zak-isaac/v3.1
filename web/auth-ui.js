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
  gate.addEventListener('close', () => { chosen = null; gateField.value = ''; gateError.hidden = true; });
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
    if (!/^\\d{1,12}$/.test(associate)) {
      gateError.textContent = 'Enter a valid associate number using digits only.';
      gateError.hidden = false; return;
    }
    gateButton.disabled = true; gateError.hidden = true;
    const choice = chosen;
    try {
      await api('/api/login', {associate});
      await api(choice.kind === 'resume' ? '/api/shifts/resume' : '/api/shifts/start',
        choice.kind === 'resume' ? {shift_id:choice.shift.id} : {code:choice.shift.code});
      enterReport();
    } catch (e) {
      gateError.textContent = e.message;
      gateError.hidden = false;
    } finally { gateButton.disabled = false; }
  });

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
    const open = flow?.open || [], ready = flow?.ready || [];
    if (open.length && ready.length) {
      updateHandoff(open[open.length-1], ready[0]);
      show('handoff');
    } else if (open.length) {
      updateActive(open[open.length-1]);
      show('active');
      if (open.length > 1) {
        let extra = document.getElementById('v31-active-extra');
        if (!extra) {
          extra = document.createElement('div');
          extra.id = 'v31-active-extra';
          extra.style.cssText = 'display:grid;gap:8px;margin:12px 0';
          $('#resume-active').insertAdjacentElement('afterend', extra);
        }
        extra.replaceChildren();
        for (const shift of open.slice(0,-1)) {
          const button = document.createElement('button');
          button.className = 'button button-outline';
          button.textContent = 'Resume ' + shift.code + ' · ' + timeRange(shift);
          button.onclick = () => resume(shift);
          extra.append(button);
        }
      }
    } else if (ready.length >= 2) {
      updateHandoff(ready[0], ready[1]);
      show('handoff');
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
      const current = await fetch('/api/me', {credentials:'same-origin'});
      if (current.ok) {
        account = await current.json(); signedIn = true;
        flow = {open:account.open, ready:account.ready};
      }
      render();
    } catch (e) {
      error.textContent = 'Could not check active shifts: ' + e.message;
      error.hidden = false; show('login');
    }
  }
  init();
})();
