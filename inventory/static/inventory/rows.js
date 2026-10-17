// Examination / set pages: rows of new items inside the page's single form, and the "replaces" choices.
(function () {
  var body = document.getElementById('newitems');
  var tpl = document.getElementById('new-row-tpl');
  var unitsEl = document.getElementById('consumable-units');
  var units = unitsEl ? JSON.parse(unitsEl.textContent) : {};

  // ---- what can an optional item replace? any item that is Required once everything is saved,
  //      including required items typed into the new rows below.
  function liveRequired() {
    var list = [];
    document.querySelectorAll('select[data-cid]').forEach(function (sel) {
      if (sel.value === 'on') list.push({ id: sel.dataset.cid, name: sel.dataset.cname });
    });
    document.querySelectorAll('tr.newrow').forEach(function (tr) {
      var c = tr.querySelector('select.newcons'), t = tr.querySelector('select.newtype');
      if (c && c.value && t && t.value === 'on') list.push({ id: c.value, name: c.options[c.selectedIndex].textContent });
    });
    return list;
  }
  function ownId(sel) {
    if (sel.dataset.own) return sel.dataset.own;
    var tr = sel.closest('tr.newrow'), c = tr && tr.querySelector('select.newcons');
    return c ? c.value : '';
  }
  function refresh(initial) {
    var req = liveRequired();
    document.querySelectorAll('select.replaces-sel').forEach(function (sel) {
      var want = initial ? (sel.dataset.value || '') : sel.value, own = ownId(sel);
      sel.innerHTML = '';
      var none = document.createElement('option');
      none.value = ''; none.textContent = 'Not linked'; sel.appendChild(none);
      req.forEach(function (r) {
        if (r.id === own) return;
        var o = document.createElement('option');
        o.value = r.id; o.textContent = 'Replaces ' + r.name; sel.appendChild(o);
      });
      sel.value = want;
      if (sel.value !== want) sel.value = '';
    });
  }

  document.addEventListener('change', function (e) {
    var t = e.target;
    if (t.matches && t.matches('select.newcons')) {              // pick a consumable: use its usual unit
      var unit = t.closest('tr').querySelector('select[name$="-unit"]');
      if (unit && units[t.value]) { unit.value = units[t.value]; unit.dispatchEvent(new Event('change', { bubbles: true })); }
      refresh(false);
    } else if (t.matches && t.matches('select.newtype, select[data-cid]')) {
      refresh(false);
    }
  });

  // ---- more rows / clear a row
  function nextIndex() {
    var max = -1;
    body.querySelectorAll('tr.newrow').forEach(function (tr) {
      var m = /^new(\d+)$/.exec(tr.dataset.prefix || '');
      if (m) max = Math.max(max, parseInt(m[1], 10));
    });
    return max + 1;
  }
  var addBtn = document.getElementById('add-new-row');
  if (addBtn && body && tpl) {
    addBtn.addEventListener('click', function () {
      body.insertAdjacentHTML('beforeend', tpl.innerHTML.replace(/new__i__/g, 'new' + nextIndex()));
      refresh(false);
      var last = body.querySelector('tr.newrow:last-child select.newcons');
      if (last) last.focus();
    });
  }
  if (body) {
    body.addEventListener('click', function (e) {
      if (!e.target.classList || !e.target.classList.contains('newdel')) return;
      var rows = body.querySelectorAll('tr.newrow'), tr = e.target.closest('tr.newrow');
      if (rows.length > 1) { tr.remove(); }
      else {
        tr.querySelectorAll('select, input').forEach(function (f) {
          if (f.tagName === 'SELECT') f.selectedIndex = 0; else f.value = f.type === 'number' ? 1 : '';
        });
        var rep = tr.querySelector('[id$="-rep"]'); if (rep) rep.hidden = true;
      }
      refresh(false);
    });
  }
  // rows ticked "Remove" are shown struck through until Save changes removes them
  function markRemoval(box) { var tr = box.closest('tr'); if (tr) tr.classList.toggle('will-remove', box.checked); }
  document.addEventListener('change', function (e) {
    if (e.target.classList && e.target.classList.contains('rowremove')) markRemoval(e.target);
  });
  document.querySelectorAll('input.rowremove').forEach(markRemoval);
  refresh(true);
})();
