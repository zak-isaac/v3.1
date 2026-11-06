/* In Process panel: non-Breakdown Cetaris WOs (most commonly Proactive
 * Repair) still open in the shop. Deliberately NOT auto-OOS -- an open
 * Proactive Repair does not always mean the asset is unavailable. This
 * panel exists so these stop vanishing silently, with a management-only
 * approval step onto the live Asset Status / OOS board above it.
 */
(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const root = () => $('#v31-in-process-root');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const toast = msg => window.v31ReportView?.toast?.(msg);
  let rows = null;

  const api = async (path, data) => {
    const opts = {credentials: 'same-origin', headers: {Accept: 'application/json'}};
    if (data !== undefined) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(data);
    }
    const response = await fetch(path, opts);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(result.detail || 'In Process request failed');
    return result;
  };

  const card = item => {
    const details = [item.category, 'WO# ' + item.wo, item.techs?.join(', ')]
      .filter(Boolean).map(esc).join(' · ');
    return `<div class="item-card"><div><strong>${esc(item.asset)}</strong>
      <small>${details}</small>${item.cause ? `<p class="field-help">${esc(item.cause)}</p>` : ''}</div>
      <button type="button" class="btn btn-light btn-small" data-approve-oos="${esc(item.wo)}">Approve as OOS</button></div>`;
  };

  function render() {
    const host = root();
    if (!host) return;
    if (!rows) {
      host.innerHTML = '<p class="field-help">In-process work is unavailable. Refresh to retry.</p>';
      return;
    }
    host.innerHTML = `<div class="pane-header">
        <div><h3>In Process (Pending Approval) · ${rows.length}</h3>
        <p>Open Proactive Repair / other non-Breakdown work still in the shop. Not yet Out of Service.</p></div>
      </div>
      <div class="item-list" id="v31-in-process-list">${rows.length ? rows.map(card).join('') :
        '<p class="field-help">Nothing currently in process outside the Breakdown board.</p>'}</div>`;
    host.querySelectorAll('[data-approve-oos]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const wo = btn.dataset.approveOos;
        if (!window.confirm('Approve this asset as Out of Service? Only do this once you have confirmed it is actually unavailable.')) return;
        try {
          await api(`/api/reporting/work-orders/${encodeURIComponent(wo)}/approve-oos`, {});
          toast('Approved as Out of Service');
          await reload();
          window.v31ReportView?.reload?.();
        } catch (e) {
          toast(e.message);
        }
      });
    });
  }

  async function reload() {
    try {
      const state = await api('/api/reporting/state');
      rows = (state.rows || []).filter(r => r.status === 'IN_PROCESS');
    } catch (e) {
      rows = null;
    }
    render();
  }

  document.addEventListener('DOMContentLoaded', reload);
  window.__v31InProcess = {reload};
})();
