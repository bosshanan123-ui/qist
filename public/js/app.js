/* Live installment calculator. Mirrors calc() in app.py: total = price*(1+markup), min down = 20% of total. */
(function () {
  var f = document.querySelector('[data-calc]');
  if (!f) return;
  var plans = window.PLANS || [], MIN = window.MIN_DOWN || 20;
  var $ = function (s) { return f.querySelector(s); };
  var price = $('[name=price]'), down = $('[name=down]'), touched = false;
  var fmt = function (n) { return 'Rs ' + Math.round(n).toLocaleString('en-PK'); };
  function months() { var c = f.querySelector('[name=months]:checked'); return c ? +c.value : (plans[0] || {}).months; }
  function run() {
    var p = +price.value || 0, m = months();
    var pl = plans.filter(function (x) { return x.months == m; })[0] || { markup_pct: 0 };
    var total = Math.round(p * (1 + pl.markup_pct / 100)), min = Math.ceil(total * MIN / 100);
    if (!touched || +down.value < min) down.value = min;
    var d = Math.min(Math.max(+down.value || min, min), Math.max(total - 1, 0));
    var monthly = Math.ceil(Math.max(total - d, 0) / m);
    $('[data-out=monthly]').textContent = fmt(monthly);
    $('[data-out=months]').textContent = m;
    $('[data-out=total]').textContent = fmt(total);
    $('[data-out=min]').textContent = fmt(min);
    $('[data-out=markup]').textContent = pl.markup_pct + '%';
  }
  f.addEventListener('input', function (e) { if (e.target === down) touched = true; run(); });
  f.addEventListener('change', run);
  run();
})();

/* Product search on the catalogue page */
(function () {
  var s = document.getElementById('psearch');
  if (!s) return;
  s.addEventListener('input', function () {
    var q = s.value.toLowerCase();
    document.querySelectorAll('[data-pname]').forEach(function (c) {
      c.style.display = c.dataset.pname.indexOf(q) > -1 ? '' : 'none';
    });
  });
})();
