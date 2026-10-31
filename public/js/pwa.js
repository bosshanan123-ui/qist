/* Qist PWA: service worker, install button, offline queue with async sync. */
(function () {
  'use strict';
  var role = document.body.dataset.role || '';
  var $ = function (s) { return document.querySelector(s); };

  /* ---------- service worker + install ---------- */
  if ('serviceWorker' in navigator) window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js').catch(function () {}); });
  var deferred = null, installBtn = $('#installApp');
  window.addEventListener('beforeinstallprompt', function (e) { e.preventDefault(); deferred = e; if (installBtn) installBtn.hidden = false; });
  if (installBtn) installBtn.addEventListener('click', async function () {
    if (!deferred) return; deferred.prompt(); await deferred.userChoice; deferred = null; installBtn.hidden = true;
  });
  window.addEventListener('appinstalled', function () { if (installBtn) installBtn.hidden = true; });

  /* ---------- tiny IndexedDB queue ---------- */
  function idb() {
    return new Promise(function (res, rej) {
      var r = indexedDB.open('qist-offline', 1);
      r.onupgradeneeded = function () { r.result.createObjectStore('queue', { keyPath: 'id' }); };
      r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
    });
  }
  async function tx(mode, fn) {
    var db = await idb();
    return new Promise(function (res, rej) {
      var t = db.transaction('queue', mode), req = fn(t.objectStore('queue'));
      t.oncomplete = function () { res(req && req.result); }; t.onerror = function () { rej(t.error); };
    });
  }
  var all = function () { return tx('readonly', function (s) { return s.getAll(); }).then(function (r) { return r || []; }); };
  var put = function (it) { return tx('readwrite', function (s) { return s.put(it); }); };
  var del = function (id) { return tx('readwrite', function (s) { return s['delete'](id); }); };

  /* ---------- toast + status chip + panel ---------- */
  function toast(msg, type) {
    var t = document.createElement('div'); t.className = 'toast ' + (type || 'info'); t.setAttribute('role', 'status'); t.textContent = msg;
    document.body.appendChild(t); setTimeout(function () { t.classList.add('out'); setTimeout(function () { t.remove(); }, 400); }, 4200);
  }
  var chip = $('#netstate');
  async function refresh() {
    if (!chip) return;
    var items = await all().catch(function () { return []; });
    var p = items.filter(function (i) { return i.status === 'pending'; }).length, f = items.filter(function (i) { return i.status === 'failed'; }).length;
    chip.classList.toggle('off', !navigator.onLine);
    chip.textContent = (navigator.onLine ? 'Online' : 'Offline') + (p ? ' · ' + p + ' waiting' : '') + (f ? ' · ' + f + ' failed' : '');
    return items;
  }
  async function openPanel() {
    var old = $('#qpanel'); if (old) { old.remove(); return; }
    var items = (await all()).sort(function (a, b) { return a.created - b.created; });
    var box = document.createElement('div'); box.id = 'qpanel'; box.className = 'glass qpanel';
    var h = document.createElement('h3'); h.textContent = 'Saved on this device'; box.appendChild(h);
    if (!items.length) { var e = document.createElement('p'); e.className = 'mute small'; e.textContent = 'Nothing waiting. Everything is synced.'; box.appendChild(e); }
    items.forEach(function (it) {
      var row = document.createElement('div'); row.className = 'qrow';
      var t = document.createElement('div'); t.innerHTML = '<b></b><br><span class="small mute"></span>';
      t.querySelector('b').textContent = it.label;
      t.querySelector('span').textContent = new Date(it.created).toLocaleString() + ' · ' + (it.status === 'failed' ? 'Failed: ' + (it.error || '') : 'Waiting to sync');
      row.appendChild(t);
      var b = document.createElement('button'); b.className = 'btn ghost sm'; b.textContent = it.status === 'failed' ? 'Discard' : 'Cancel';
      b.onclick = async function () { await del(it.id); box.remove(); refresh(); openPanel(); }; row.appendChild(b); box.appendChild(row);
    });
    var close = document.createElement('button'); close.className = 'btn sm'; close.textContent = 'Sync now';
    close.onclick = function () { flush(true); box.remove(); }; box.appendChild(close);
    document.body.appendChild(box);
  }
  if (chip) chip.addEventListener('click', openPanel);

  /* ---------- sending + flushing ---------- */
  function uuid() { return crypto.randomUUID ? crypto.randomUUID() : 'id-' + Date.now() + '-' + Math.random().toString(16).slice(2); }
  async function getCsrf() {
    var r = await fetch('/api/csrf', { credentials: 'same-origin', cache: 'no-store' });
    if (r.status === 401) return null;
    return (await r.json()).csrf;
  }
  async function send(item, tok) {
    if (!navigator.onLine) return { kind: 'queue' };
    try {
      tok = tok || await getCsrf();
      if (tok === null) return { kind: 'queue', auth: true };
      var body = new URLSearchParams(item.fields); body.set('_csrf', tok);
      var res = await fetch(item.url, { method: 'POST', body: body, headers: { 'X-Sync': '1' }, credentials: 'same-origin' });
      if (res.status === 401) return { kind: 'queue', auth: true };
      var j = null; try { j = await res.json(); } catch (_) {}
      if (res.ok && j && j.ok) return { kind: 'ok', message: j.message };
      if (res.status >= 500) return { kind: 'queue' };
      return { kind: 'fail', message: (j && j.message) || 'Request failed (' + res.status + ')' };
    } catch (_) { return { kind: 'queue' }; }
  }
  var flushing = false;
  async function flush(manual) {
    if (flushing || !navigator.onLine) return;
    flushing = true;
    try {
      var items = (await all()).filter(function (i) { return i.status === 'pending'; }).sort(function (a, b) { return a.created - b.created; });
      if (!items.length) { if (manual) toast('Everything is already synced.', 'success'); return; }
      var tok = await getCsrf();
      if (tok === null) { toast('Log in again to send your saved items.', 'error'); return; }
      var done = 0;
      for (var i = 0; i < items.length; i++) {
        var r = await send(items[i], tok);
        if (r.kind === 'ok') { await del(items[i].id); done++; }
        else if (r.kind === 'fail') { items[i].status = 'failed'; items[i].error = r.message; await put(items[i]); toast(items[i].label + ': ' + r.message, 'error'); }
        else break;
      }
      if (done) { toast(done + ' saved item' + (done > 1 ? 's' : '') + ' sent to the server.', 'success'); setTimeout(function () { if (!document.querySelector('form[data-busy]')) location.reload(); }, 1500); }
    } catch (_) {} finally { flushing = false; refresh(); }
  }

  /* ---------- form handling ---------- */
  document.addEventListener('submit', async function (e) {
    var f = e.target;
    if (!f.classList || (f.method || '').toLowerCase() !== 'post') return;
    if (/\/logout$/.test(f.getAttribute('action') || '')) {
      var n = (await all().catch(function () { return []; })).length;
      if (n && !confirm('You have ' + n + ' item(s) not yet sent to the server. Log out anyway? They stay on this device until you log in again.')) { e.preventDefault(); return; }
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage('clear-pages');
      return;
    }
    if (!f.classList.contains('js-offline')) {
      if (!navigator.onLine) { e.preventDefault(); toast('This action needs an internet connection.', 'error'); }
      return;
    }
    e.preventDefault();
    if (f.dataset.busy) return;
    if (f.dataset.confirm && !confirm(f.dataset.confirm)) return;
    f.dataset.busy = '1';
    var fields = {}; new FormData(f, e.submitter || undefined).forEach(function (v, k) { if (typeof v === 'string') fields[k] = v; });  /* read BEFORE disabling the clicked button */
    var btns = f.querySelectorAll('button'); btns.forEach(function (b) { b.disabled = true; });
    fields.client_id = uuid();
    var item = { id: fields.client_id, url: f.action, fields: fields, label: f.dataset.label || 'Saved action', created: Date.now(), status: 'pending' };
    var r = await send(item);
    var next = f.dataset.next || '';
    if (r.kind === 'ok') { toast(r.message, 'success'); setTimeout(function () { next ? (location.href = next) : location.reload(); }, 700); }
    else if (r.kind === 'fail') { toast(r.message, 'error'); delete f.dataset.busy; btns.forEach(function (b) { b.disabled = false; }); }
    else {
      await put(item); refresh();
      toast(r.auth ? 'Saved on this device. Log in again to send it.' : 'You are offline. Saved on this device and it will sync automatically.', 'info');
      btns.forEach(function (b) { if (b.type !== 'button') b.textContent = 'Saved offline'; });
      if (next) setTimeout(function () { location.href = next; }, 1000);
    }
  });

  /* ---------- keep pages ready for offline use (admin and officer) ---------- */
  async function warm() {
    if (!navigator.onLine) return;
    var base = role === 'admin' ? ['/admin', '/admin/counter', '/admin/products', '/admin/users', '/admin/reports'] : role === 'officer' ? ['/officer'] : [];
    var links = Array.prototype.map.call(document.querySelectorAll('a[href^="/application/"]'), function (a) { return a.getAttribute('href'); });
    var urls = Array.from(new Set(base.concat(links))).slice(0, 60);
    for (var i = 0; i < urls.length; i++) {
      try { await fetch(urls[i], { credentials: 'same-origin', headers: { 'X-Warm': '1' } }); } catch (_) { break; }
      await new Promise(function (r) { setTimeout(r, 150); });
    }
  }
  if (role === 'admin' || role === 'officer') window.addEventListener('load', function () { setTimeout(warm, 2500); });

  /* ---------- connection events ---------- */
  window.addEventListener('online', function () { toast('Back online. Syncing…', 'success'); refresh(); flush(); });
  window.addEventListener('offline', function () { toast('You are offline. You can keep working; changes are saved here.', 'info'); refresh(); });
  window.addEventListener('load', function () { refresh(); flush(); setInterval(function () { flush(); }, 45000); });
})();
