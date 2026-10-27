/* IND3 V3.1: approved login appearance, new isolated backend. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  for (const id of ['preview-dock', 'prototype-modal']) {
    const node = document.getElementById(id);
    if (node) node.hidden = true;
  }
  const login = $('#screen-login');
  const active = $('#screen-active');
  const handoff = $('#screen-handoff');
  login.hidden = false; active.hidden = true; handoff.hidden = true;
  const form = $('#login-form');
  const field = $('#associate-input');
  const error = $('#login-error');
  const dialog = document.createElement('dialog');
  dialog.id = 'credential-dialog';
  dialog.style.cssText = 'width:min(420px,calc(100vw - 32px));border:1px solid #d9e4f2;border-radius:18px;padding:30px;box-shadow:0 25px 80px #0a275b50;color:#0b1d3f;font-family:Segoe UI,Arial,sans-serif';
  dialog.innerHTML = `<form id="credential-form" style="display:grid;gap:14px">
    <h2 style="font-size:24px;margin:0">Verify your access</h2>
    <p style="margin:0;color:#536781;line-height:1.5">Enter your private access passphrase to continue to Shift Report.</p>
    <label for="access-passphrase" style="font-weight:700">Access passphrase</label>
    <input type="password" id="access-passphrase" autocomplete="current-password" required minlength="12" maxlength="256" style="height:46px;border:1px solid #bdcee1;border-radius:9px;padding:10px;font-size:16px;width:100%">
    <p id="credential-error" role="alert" hidden style="color:#b42318;font-size:13px;margin:0"></p>
    <button type="submit" style="background:#0872e2;border:0;border-radius:10px;color:white;font-size:16px;font-weight:700;padding:14px">Sign In to Shift Report</button>
    <button id="cancel-credential" type="button" style="background:transparent;border:0;color:#496681;font-size:14px;padding:8px">Back</button>
  </form>`;
  document.body.append(dialog);
  $('#cancel-credential').addEventListener('click', () => dialog.close());
  form.addEventListener('submit', e => {
    e.preventDefault();
    if (!/^\d{1,12}$/.test(field.value.trim())) {
      error.textContent = 'Enter your associate number using digits only.';
      error.hidden = false;
      return;
    }
    error.hidden = true;
    dialog.showModal();
    $('#access-passphrase').focus();
  });
  field.addEventListener('input', () => error.hidden = true);
  $('#credential-form').addEventListener('submit', async e => {
    e.preventDefault();
    const submit = e.target.querySelector('button[type="submit"]');
    const feedback = $('#credential-error');
    feedback.hidden = true;
    submit.disabled = true;
    try {
      const response = await fetch('/api/login', {
        method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({associate:field.value.trim(),passphrase:$('#access-passphrase').value})
      });
      if(!response.ok) throw new Error('Associate number or access passphrase was not accepted.');
      window.location.assign('/shift-report.html');
    } catch(err) {
      feedback.textContent = err.message;
      feedback.hidden = false;
      $('#access-passphrase').value = '';
    } finally { submit.disabled = false; }
  });
})();