/* IND3 V3.1 Administration — Facility & Email Settings.
   Separate from roster-admin.js on purpose: roster CRUD and facility/email
   configuration are different responsibilities even though they share one
   Administration screen. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('v31-facility-form')) return;
  const api = async (url, body) => {
    const opts = {credentials: 'same-origin', headers: {Accept: 'application/json'}};
    if (body !== undefined) {
      opts.method = 'POST'; opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw Error(out.detail || 'Request failed');
    return out;
  };
  const notify = (message, error = false) => {
    const element = $('v31-admin-notice');
    if (!element) return;
    element.textContent = message;
    element.classList.toggle('error', error);
  };
  function applySettings(data) {
    $('v31-facility-dc').value = data.distribution_center || 'IND3';
    $('v31-facility-recipients').value = data.recipients || '';
    $('v31-facility-sender').value = data.sender_name || 'IND3 Shift Passdown';
  }
  async function load() {
    try { applySettings(await api('/api/admin/settings')); }
    catch (e) { /* Non-management sessions 403 here; the whole admin
                   content block is already hidden for them. */ }
  }
  $('v31-facility-form').addEventListener('submit', e => {
    e.preventDefault();
    api('/api/admin/settings', {
      distribution_center: $('v31-facility-dc').value.trim(),
      recipients: $('v31-facility-recipients').value.trim(),
      sender_name: $('v31-facility-sender').value.trim(),
    }).then(data => { applySettings(data); notify('Facility & email configuration saved.'); })
      .catch(e => notify(e.message, true));
  });
  window.addEventListener('v31:administration', () => load());
})();
