/* V3.1 Shift History -- V3.1's own recorded shifts (not the V2/V3 archive;
   that importer lives at /api/history/legacy and isn't surfaced in this UI
   pass). Routed screen like Administration, not a cramped dialog. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('v31-history-rows')) return;
  const PAGE_SIZE = 25;
  let offset = 0;

  const get = async (path) => {
    const resp = await fetch(path, {credentials: 'same-origin'});
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw Error(data.detail || 'Shift history unavailable');
    return data;
  };

  const notice = (message, isError = false) => {
    const el = $('v31-history-notice');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('error', isError);
  };

  const countBadge = n => {
    const span = document.createElement('span');
    span.className = 'v31-history-count';
    span.textContent = String(n);
    return span;
  };

  const row = (shift) => {
    const tr = document.createElement('tr');

    const idCell = document.createElement('td');
    const idBadge = document.createElement('span');
    idBadge.className = 'v31-history-id';
    idBadge.textContent = '#' + shift.id;
    idCell.append(idBadge);

    const dateCell = document.createElement('td');
    dateCell.textContent = shift.date;

    const codeCell = document.createElement('td');
    codeCell.textContent = shift.code;

    const ownerCell = document.createElement('td');
    ownerCell.className = 'v31-person';
    ownerCell.textContent = shift.owner;

    const happeningsCell = document.createElement('td');
    happeningsCell.append(countBadge(shift.happenings));

    const reportsCell = document.createElement('td');
    reportsCell.append(countBadge(shift.reports));

    const statusCell = document.createElement('td');
    const statusChip = document.createElement('span');
    statusChip.className = 'v31-admin-status ' + (shift.status === 'Closed' ? 'good' : 'bad');
    statusChip.textContent = shift.status;
    statusCell.append(statusChip);

    const actionsCell = document.createElement('td');
    if (shift.has_report) {
      const link = document.createElement('a');
      link.className = 'v31-ad-btn primary';
      link.href = '/api/history/shifts/' + shift.id + '/eml';
      link.textContent = 'Download .eml';
      actionsCell.append(link);
    } else {
      const span = document.createElement('span');
      span.className = 'v31-admin-footnote';
      span.textContent = 'No saved passdown';
      actionsCell.append(span);
    }

    tr.append(idCell, dateCell, codeCell, ownerCell, happeningsCell, reportsCell, statusCell, actionsCell);
    return tr;
  };

  const load = async (reset = false) => {
    if (reset) offset = 0;
    const more = $('v31-history-more');
    try {
      const data = await get(`/api/history/shifts?offset=${offset}&limit=${PAGE_SIZE}`);
      const tbody = $('v31-history-rows');
      if (reset) tbody.replaceChildren();
      for (const shift of data.rows) tbody.append(row(shift));
      offset += data.rows.length;
      $('v31-history-empty').hidden = offset > 0;
      if (more) more.hidden = offset >= data.total;
      notice('');
    } catch (e) {
      notice(e.message, true);
      if (more) more.hidden = true;
    }
  };

  $('v31-history-more')?.addEventListener('click', () => load());
  window.addEventListener('v31:history', () => load(true));
})();
