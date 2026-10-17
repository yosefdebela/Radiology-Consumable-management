// Quantity boxes follow the chosen unit: whole numbers by default, decimals only for units that allow them.
(function () {
  function sync(select) {
    var box = select.closest('tr, form, .add-row');
    var qty = box && box.querySelector('input.qty');
    var opt = select.options[select.selectedIndex];
    if (!qty || !opt) return;
    var dec = opt.dataset.dec === '1';
    qty.step = dec ? '0.01' : '1';
    qty.min = dec ? '0.01' : '1';
  }
  document.addEventListener('change', function (e) {
    if (e.target.classList && e.target.classList.contains('unit-sel')) sync(e.target);
  });
  // an optional item can say which required item it replaces: only show that choice for optional items
  document.addEventListener('change', function (e) {
    var id = e.target.dataset && e.target.dataset.hides;
    var target = id && document.getElementById(id);
    if (target) target.hidden = e.target.value === 'on';
  });
  document.querySelectorAll('select.unit-sel').forEach(sync);
  window.syncQuantityStep = sync;

  // One Save button per page: remember that something was changed, and warn before it would be lost.
  var tracked = document.querySelector('form[data-track]');
  if (tracked) {
    var dirty = false, saving = false;
    var bar = document.getElementById('savebar'), note = document.getElementById('savenote');
    function setDirty(on) {
      dirty = on;
      if (bar) bar.classList.toggle('dirty', on);
      if (note) note.textContent = on ? 'You have unsaved changes' : 'No unsaved changes';
    }
    ['input', 'change'].forEach(function (type) {
      document.addEventListener(type, function (e) {
        if (e.isTrusted && e.target.form === tracked) setDirty(true);     // only real edits, not scripted ones
      });
    });
    tracked.addEventListener('submit', function (e) {
      var n = document.querySelectorAll('input.rowremove:checked').length;
      if (n && !confirm('Remove ' + n + ' consumable' + (n > 1 ? 's' : '') + ' when you save? This cannot be undone.')) {
        e.preventDefault();
        return;
      }
      saving = true;
    });
    // other buttons on the page (remove, use instead, apply a set...) reload the page: do not lose edits silently
    document.addEventListener('submit', function (e) {
      if (dirty && e.target !== tracked && !confirm('You have unsaved changes on this page. They will be lost if you continue. Continue?')) {
        e.preventDefault();
      }
    }, true);
    window.addEventListener('beforeunload', function (e) {
      if (dirty && !saving) { e.preventDefault(); e.returnValue = ''; }
    });
    if (tracked.dataset.dirtyStart) setDirty(true);                       // page shown again after a failed save
  }
})();
