// Add/Edit consumable form.
//  - Name field (add only): suggests items from the imported Excel list while you type, fills the rest on pick,
//    and turns RED when what you typed is not in the list. Typing on is always accepted: it is saved as typed.
//  - Alternative-item fields: the same, against existing consumables and the list.
(function () {
  var form = document.querySelector('form[data-suggest-url]');
  if (!form) return;
  var url = form.dataset.suggestUrl;
  var altRows = document.getElementById('alt-rows');
  var uid = 0;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  // typed part shown in bold inside each suggestion, so the spelling you typed stays visible
  function highlighted(text, q) {
    var span = el('span', 'sg-name');
    var at = text.toLowerCase().indexOf(q.toLowerCase());
    if (at < 0 || !q) { span.textContent = text; return span; }
    span.appendChild(document.createTextNode(text.slice(0, at)));
    span.appendChild(el('mark', null, text.slice(at, at + q.length)));
    span.appendChild(document.createTextNode(text.slice(at + q.length)));
    return span;
  }

  function attach(input, cfg) {
    var id = ++uid;
    var wrap = input.parentNode;
    wrap.style.position = 'relative';
    var list = el('ul', 'suggest');
    list.id = 'sg-list-' + id;
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    var status = el('div', 'sg-status');
    status.id = 'sg-status-' + id;
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    wrap.appendChild(list);
    wrap.appendChild(status);
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-controls', list.id);
    input.setAttribute('aria-describedby', status.id);
    input.setAttribute('aria-expanded', 'false');

    var items = [], active = -1, timer = null, seq = 0;

    function setStatus(kind, text) {            // kind: '', 'ok', 'warn', 'red'
      status.className = 'sg-status' + (kind ? ' sg-' + kind : '');
      status.textContent = text || '';
      input.classList.toggle('not-in-list', kind === 'red');
    }
    function close() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; }

    function render() {
      list.innerHTML = '';
      var q = input.value.trim();
      items.forEach(function (it, i) {
        var li = el('li', 'sg' + (it.exists ? ' sg-off' : '') + (i === active ? ' sg-active' : ''));
        li.setAttribute('role', 'option');
        li.setAttribute('aria-disabled', it.exists ? 'true' : 'false');
        li.appendChild(highlighted(it.name, q));
        var meta = it.exists ? 'already added'
          : (it.source === 'consumable' ? 'existing consumable' : [it.category_name, it.unit_name].filter(Boolean).join(' - '));
        li.appendChild(el('span', 'sg-meta', meta));
        li.addEventListener('mousedown', function (e) { e.preventDefault(); pick(it); });
        list.appendChild(li);
      });
      list.hidden = !items.length;
      input.setAttribute('aria-expanded', String(items.length > 0));
    }

    function evaluate(q, data, openList) {
      items = data.results || [];
      active = -1;
      var exact = items.filter(function (i) { return i.name.toLowerCase() === q.toLowerCase(); })[0];
      if (!items.length) {
        if (!data.total) {
          setStatus('', cfg.alt ? '' : 'No consumables list loaded yet, so there is nothing to compare with. What you type will be saved as typed.');
        } else {
          setStatus('red', cfg.alt
            ? 'Not in your consumables or your list. It will be created as a new consumable, spelled as you typed it.'
            : 'Not in your consumables list. It will be saved exactly as you typed it.');
        }
      } else if (exact && exact.exists) {
        setStatus('warn', 'Already added as a consumable.');
      } else if (exact) {
        setStatus('ok', cfg.alt ? 'Found.' : 'In your list. Pick it to fill in the details.');
      } else {
        setStatus('', items.length + ' possible match' + (items.length > 1 ? 'es' : '') + ' in your list. Pick one, or keep typing.');
      }
      if (openList) render(); else { list.hidden = true; }
    }

    function check(openList) {
      var q = input.value.trim();
      if (q.length < 2) { items = []; render(); setStatus('', ''); return; }
      var mine = ++seq;
      var qs = '?q=' + encodeURIComponent(q) + (cfg.alt ? '&kind=alt' : '');
      fetch(url + qs, { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) { if (data && mine === seq) evaluate(q, data, openList); })
        .catch(function () { /* suggestions are optional: typing still works */ });
    }
    input._sgCheck = check;

    function pick(it) {
      if (it.exists) return;
      input.value = it.name;
      close();
      setStatus('ok', 'Selected from the list.');
      if (cfg.fill) cfg.fill(it);
    }

    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(function () { check(true); }, 150); });
    input.addEventListener('blur', function () { setTimeout(close, 120); });
    input.addEventListener('keydown', function (e) {
      if (list.hidden) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(items[active]); }
      else if (e.key === 'Escape') { close(); }
    });
    if (input.value.trim().length >= 2) check(false);
  }

  // ---- alternative rows
  function addAltRow(value) {
    if (!altRows) return null;
    var row = el('div', 'alt-row');
    var field = el('div', 'alt-field');
    var input = el('input', 'input alt-input');
    input.type = 'text'; input.name = 'alt_name'; input.maxLength = 150;
    input.placeholder = 'Alternative item name'; input.value = value || '';
    input.setAttribute('aria-label', 'Alternative item');
    field.appendChild(input);
    var del = el('button', 'btn small ghost alt-del', 'Remove');
    del.type = 'button';
    del.addEventListener('click', function () {
      if (altRows.children.length > 1) row.remove(); else { input.value = ''; input.dispatchEvent(new Event('input')); }
    });
    row.appendChild(field); row.appendChild(del);
    altRows.appendChild(row);
    attach(input, { alt: true });
    return input;
  }
  if (altRows) {
    var existing = (altRows.dataset.names ? JSON.parse(altRows.dataset.names) : []);
    existing.forEach(function (n) { addAltRow(n); });
    if (!existing.length) addAltRow('');
    var addBtn = document.getElementById('add-alt');
    if (addBtn) addBtn.addEventListener('click', function () { var i = addAltRow(''); if (i) i.focus(); });
  }

  function setAlternatives(names) {
    if (!altRows || !names || !names.length) return;
    var inputs = Array.prototype.slice.call(altRows.querySelectorAll('.alt-input'));
    var empties = inputs.filter(function (i) { return !i.value.trim(); });
    names.forEach(function (n) {
      var target = empties.shift() || addAltRow('');
      target.value = n;
      if (target._sgCheck) target._sgCheck(false);
    });
  }

  // ---- name field (add form only)
  var name = form.querySelector('#id_name');
  if (name && form.dataset.suggest === '1') {
    var note = el('div', 'filled-note');
    note.hidden = true;
    name.parentNode.appendChild(note);
    function setValue(selector, value) {
      var f = form.querySelector(selector);
      if (f && value !== null && value !== undefined && value !== '') f.value = value;
    }
    attach(name, { fill: function (it) {
      setValue('#id_category', it.category_id);
      setValue('#id_default_unit', it.unit_id);
      setValue('#id_description', it.description);
      setAlternatives(it.alternatives);
      var bits = [it.category_name, it.unit_name].filter(Boolean);
      note.textContent = 'Filled in from your consumables list' + (bits.length ? ' (' + bits.join(', ') + ')' : '') +
        (it.alternatives && it.alternatives.length ? ' with ' + it.alternatives.length + ' alternative(s)' : '') + '. Check the details, then save.';
      note.hidden = false;
    } });
    name.addEventListener('input', function () { note.hidden = true; });
  }
})();
