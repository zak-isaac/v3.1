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
  const useDifferent = async () => {
    pending = null;
    if (signedIn) {
      try { await api('/api/logout', {}); } catch (e) { console.warn(e); }
      signedIn = false; account = null;
    }
    field.value = '';
    error.hidden = true;
    show('login');
  };
  const resume = async shift => choose({kind:'resume', shift});
  const start = async shift => choose({kind:'start', shift});
  async function choose(choice) {
    pending = choice;
    if (!signedIn) { error.hidden = true; show('login'); return; }
    try {
      await api(choice.kind === 'resume' ? '/api/shifts/resume' : '/api/shifts/start',
        choice.kind === 'resume' ? {shift_id:choice.shift.id} : {code:choice.shift.code});
      window.location.assign('/shift-report.html');
    } catch (e) {
      error.textContent = e.message; error.hidden = false;
      show('login');
    }
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
    if (times) times.textContent = timeLabel(previous.scheduled_start) + ' · ' +
      timeLabel(next.scheduled_start) + ' · ' + timeLabel(next.scheduled_end);
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
      if (pending) {
        const choice = pending; pending = null;
        await api(choice.kind === 'resume' ? '/api/shifts/resume' : '/api/shifts/start',
          choice.kind === 'resume' ? {shift_id:choice.shift.id} : {code:choice.shift.code});
        window.location.assign('/shift-report.html');
        return;
      }
      if (answer.destination === '/shift-report.html') {
        window.location.assign(answer.destination); return;
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
