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

  function openCard(item) {
    return `<div class="item-card" data-oos-id="${item.id}">
      <div><strong>${esc(item.asset)}</strong>
      <small>Logged by ${esc(item.logged_by_name || 'Unknown')} (${esc(item.logged_shift_code)})</small></div>
      <button type="button" class="btn btn-light btn-small" data-mark-rts="${item.id}">Mark RTS</button>
    </div>`;
  }

  function rtsCard(item) {
    return `<div class="item-card">
      <div><strong>${esc(item.asset)}</strong><small>Returned to Service</small>
      <small>Marked by ${esc(item.rts_by_name || 'Unknown')}</small></div>
      <span class="status-pill rts">RTS</span>
    </div>`;
  }

  function render() {
    const node = root();
    if (!node) return;
    const open = window.__v31ManualOOS?.open || [];
    const rts = window.__v31ManualOOS?.rts_this_shift || [];
    node.innerHTML = `<div class="pane-header">
        <div><h3>Manually Reported</h3></div>
        <button type="button" class="btn btn-primary" id="v31-oos-add-btn">+ Report Breakdown / OOS</button>
      </div>
      <form id="v31-oos-add-form" hidden>
        <div class="field"><label for="v31-oos-asset">Asset ID</label>
          <input id="v31-oos-asset" maxlength="120" required placeholder="e.g. CONV-12"></div>
        <div style="display:flex;gap:8px;">
          <button type="submit" class="btn btn-primary">Save entry</button>
          <button type="button" class="btn btn-light" id="v31-oos-cancel">Cancel</button>
        </div>
        <p id="v31-oos-error" class="field-help" role="alert"></p>
      </form>
      <div class="item-list" id="v31-oos-list">${open.length ? open.map(openCard).join('')
        : '<p class="field-help">No open manually-reported Breakdown/OOS records.</p>'}</div>
      <div class="pane-header"><div><h3>Returned to Service (this shift)</h3></div></div>
      <div class="item-list" id="v31-oos-rts-list">${rts.length ? rts.map(rtsCard).join('')
        : '<p class="field-help">None marked RTS yet this shift.</p>'}</div>`;

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
        await api('/api/oos', {asset});
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
          await api(`/api/oos/${btn.dataset.markRts}/rts`, {});
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
