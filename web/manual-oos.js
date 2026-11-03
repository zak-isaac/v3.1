/* V3.1 manually-reported Breakdown/OOS tracking.
 * Independent of Cetaris-imported work orders: an MST can log an asset as
 * Down/Degraded with no WO behind it at all. The record stays open across
 * shifts (global, not scoped to the logging shift) until any signed-in
 * associate marks it RTS -- the fix might come from a different shift or
 * source entirely than whoever first logged it down. */
(() => {
  'use strict';
  const root = () => document.querySelector('#v31-manual-oos-root');
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const api = async (path, body) => {
    const options = {credentials:'same-origin', headers:{Accept:'application/json'}};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const r = await fetch(path, options);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(data.detail || 'Request failed');
    return data;
  };

  async function refresh() {
    try {
      const data = await api('/api/oos');
      window.__v31ManualOOS = {open: data.open || [], rts_this_shift: data.resolved_this_shift || []};
    } catch (e) {
      window.__v31ManualOOS = {open: [], rts_this_shift: []};
    }
  }

  function card(item) {
    const who = esc(item.logged_by_name || 'Unknown');
    const when = item.logged_at ? new Date(item.logged_at).toLocaleString() : '';
    return `<div class="item-card" data-oos-id="${item.id}">
      <div><strong>${esc(item.asset)}</strong><small>${esc(item.cause) || 'No cause specified'}</small>
      <small>Logged by ${who}${when ? ' \u00b7 ' + esc(when) : ''} (${esc(item.logged_shift_code)})</small></div>
      <button type="button" class="btn btn-light btn-small" data-mark-rts="${item.id}">Mark RTS</button>
    </div>`;
  }

  function render() {
    const node = root();
    if (!node) return;
    const items = window.__v31ManualOOS?.open || [];
    node.innerHTML = `<div class="pane-header">
        <div><h3>Manually Reported</h3>
        <p>Breakdown/OOS with no Cetaris work order behind it. Stays open across shifts until any associate marks it RTS.</p></div>
        <button type="button" class="btn btn-primary" id="v31-oos-add-btn">+ Report Breakdown / OOS</button>
      </div>
      <form id="v31-oos-add-form" hidden>
        <div class="field"><label for="v31-oos-asset">Asset ID</label>
          <input id="v31-oos-asset" maxlength="120" required placeholder="e.g. CONV-12"></div>
        <div class="field"><label for="v31-oos-cause">Cause</label>
          <input id="v31-oos-cause" maxlength="500" placeholder="e.g. Jammed belt"></div>
        <div class="field"><label for="v31-oos-notes">Notes (optional)</label>
          <textarea id="v31-oos-notes" maxlength="1000"></textarea></div>
        <div style="display:flex;gap:8px;">
          <button type="submit" class="btn btn-primary">Save entry</button>
          <button type="button" class="btn btn-light" id="v31-oos-cancel">Cancel</button>
        </div>
        <p id="v31-oos-error" class="field-help" role="alert"></p>
      </form>
      <div class="item-list" id="v31-oos-list">${items.length ? items.map(card).join('')
        : '<p class="field-help">No open manually-reported Breakdown/OOS records.</p>'}</div>`;

    node.querySelector('#v31-oos-add-btn')?.addEventListener('click', () => {
      node.querySelector('#v31-oos-add-form').hidden = false;
      node.querySelector('#v31-oos-add-btn').hidden = true;
      node.querySelector('#v31-oos-asset')?.focus();
    });
    node.querySelector('#v31-oos-cancel')?.addEventListener('click', () => {
      node.querySelector('#v31-oos-add-form').hidden = true;
      node.querySelector('#v31-oos-add-btn').hidden = false;
    });
    node.querySelector('#v31-oos-add-form')?.addEventListener('submit', async e => {
      e.preventDefault();
      const asset = node.querySelector('#v31-oos-asset').value.trim();
      const errorNode = node.querySelector('#v31-oos-error');
      errorNode.textContent = '';
      if (!asset) { errorNode.textContent = 'Asset ID is required.'; return; }
      try {
        await api('/api/oos', {
          asset,
          cause: node.querySelector('#v31-oos-cause').value.trim(),
          notes: node.querySelector('#v31-oos-notes').value.trim(),
        });
        await refresh();
        render();
      } catch (err) {
        errorNode.textContent = err.message || 'Could not save this entry.';
      }
    });
    node.querySelectorAll('[data-mark-rts]').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await api(`/api/oos/${btn.dataset.markRts}/rts`, {notes: ''});
          await refresh();
          render();
        } catch (err) {
          btn.disabled = false;
          alert(err.message || 'Could not mark this record RTS.');
        }
      });
    });
  }

  window.addEventListener('v31:breakdown-tab', async () => {
    const node = root();
    if (node) node.textContent = 'Loading breakdown/OOS records\u2026';
    await refresh();
    render();
  });

  // Also load once up-front so Preview Report / the EML reflect current
  // manual OOS state even if the user never opens the Breakdown tab.
  refresh();
})();
