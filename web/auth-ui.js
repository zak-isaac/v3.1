/* IND3 V3.1 local associate-number-only login (development use only). */
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
  field.addEventListener('input', () => error.hidden = true);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const associate = field.value.trim();
    if (!/^\d{1,12}$/.test(associate)) {
      error.textContent = 'Enter your associate number using digits only.';
      error.hidden = false;
      return;
    }
    const submit = form.querySelector('button[type="submit"]');
    error.hidden = true;
    if (submit) submit.disabled = true;
    try {
      const response = await fetch('/api/login', {
        method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({associate})
      });
      if (!response.ok) {
        if (response.status === 401) throw new Error('Associate number is not authorized for this local preview.');
        throw new Error('Sign-in could not be completed. Please try again.');
      }
      window.location.assign('/shift-report.html');
    } catch(err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally { if (submit) submit.disabled = false; }
  });
})();
