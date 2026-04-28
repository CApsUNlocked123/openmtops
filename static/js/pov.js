(function () {
  'use strict';

  var _pollTimer = null;
  var _newTimers = {};
  var _audioCtx  = null;

  // ── Audio beep ────────────────────────────────────────────────────────────
  function _beep() {
    try {
      if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var osc  = _audioCtx.createOscillator();
      var gain = _audioCtx.createGain();
      osc.connect(gain);
      gain.connect(_audioCtx.destination);
      osc.type = 'sine'; osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.18, _audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + 0.35);
      osc.start(_audioCtx.currentTime);
      osc.stop(_audioCtx.currentTime + 0.35);
    } catch (e) {}
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _el(id) { return document.getElementById(id); }

  function _actionBadge(action) {
    if (action === 'STRONG')
      return '<span class="badge-buy" style="font-size:.78rem">⚡ STRONG</span>';
    if (action === 'WATCH')
      return '<span class="badge-wait" style="font-size:.78rem">👁 WATCH</span>';
    return '<span class="badge-neutral" style="font-size:.78rem">— WAIT</span>';
  }

  function _dots(sig) {
    return ['c1','c2','c3','c4','c5'].map(function (k, i) {
      return '<span title="C'+(i+1)+'" style="font-size:.95rem;color:' +
        (sig[k] ? 'var(--green)' : 'var(--border-strong)') + '">' +
        (sig[k] ? '●' : '○') + '</span>';
    }).join(' ');
  }

  function _fmt(v) { return (v == null) ? '—' : v; }

  function _setErr(msg) {
    var el = _el('pov-error');
    if (!el) return;
    el.textContent  = msg || '';
    el.style.display = msg ? 'block' : 'none';
  }

  // ── Auto-fetch ATM info ───────────────────────────────────────────────────
  function _fetchAtmInfo(symbol) {
    var atmEl    = _el('pov-atm');
    var expEl    = _el('pov-expiry');
    var gapEl    = _el('pov-gap');
    var startBtn = _el('pov-start-btn');
    var preview  = _el('pov-strike-preview');
    var labels   = _el('pov-preview-labels');
    var spotLbl  = _el('pov-spot-label');

    if (atmEl)    atmEl.placeholder    = 'loading…';
    if (expEl)    expEl.placeholder    = 'loading…';
    if (startBtn) startBtn.disabled    = true;
    if (preview)  preview.style.display = 'none';
    _setErr('');

    fetch('/api/pov/atm_info?symbol=' + encodeURIComponent(symbol))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { _setErr(d.error); return; }

        if (atmEl)  { atmEl.value = d.atm_strike; atmEl.placeholder = ''; }
        if (expEl)  { expEl.value = d.expiry;     expEl.placeholder = ''; }
        if (gapEl)  { gapEl.value = d.strike_gap; }
        if (spotLbl) { spotLbl.textContent = 'Spot: ' + d.spot; }

        if (labels && d.strikes) {
          labels.textContent = d.strikes.map(function (s) { return s.label; }).join('  ·  ');
        }
        if (preview)  preview.style.display  = 'block';
        if (startBtn) startBtn.disabled = false;
      })
      .catch(function (e) { _setErr('Could not fetch ATM info: ' + e); });
  }

  // ── Card renderer ─────────────────────────────────────────────────────────
  function _renderCard(strike) {
    var sig = strike.signal || {};
    var lc  = strike.last_candle    || {};
    var cc  = strike.current_candle || {};

    var showLevels = (sig.action === 'STRONG' || sig.action === 'WATCH');
    var ltp        = cc.close || lc.close || 0;
    var oi         = cc.oi    || lc.oi    || 0;
    var oiChg      = lc.oi_change;
    var oiChgStr   = oiChg == null ? '—'
      : (oiChg >= 0 ? '<span style="color:var(--green)">+'+oiChg+'</span>'
                    : '<span style="color:var(--red)">'  +oiChg+'</span>');

    var borderCol = sig.action === 'STRONG' ? 'var(--green-border)'
                  : sig.action === 'WATCH'  ? 'var(--yellow-border)'
                  : 'var(--border)';

    return '<div class="stat-card mb-3" id="pov-card-'+strike.sid+'"'
      +' style="border-color:'+borderCol+';position:relative;transition:border-color .3s">'
      +'<div class="d-flex align-items-center justify-content-between mb-2">'
      +  '<span style="font-size:.95rem;font-weight:600;color:var(--text-primary)">'+strike.label+'</span>'
      +  '<div class="d-flex align-items-center gap-2">'
      +    _actionBadge(sig.action)
      +    '<span id="pov-new-'+strike.sid+'" style="display:none;background:var(--green-bg);'
      +         'color:var(--green);border:1px solid var(--green-border);border-radius:var(--radius-pill);'
      +         'font-size:.7rem;font-weight:700;padding:2px 8px">NEW</span>'
      +  '</div>'
      +'</div>'
      +'<div class="d-flex gap-2 mb-2">'+_dots(sig)+'</div>'
      +'<div class="d-flex gap-3 mb-1" style="font-size:.8rem">'
      +  '<div><span class="stat-label">LTP</span><br>'
      +    '<span style="color:var(--text-primary);font-weight:600">'+(ltp ? ltp.toFixed(2) : '—')+'</span></div>'
      +  '<div><span class="stat-label">OI</span><br>'
      +    '<span style="color:var(--text-secondary)">'+(oi ? oi.toLocaleString() : '—')+'</span></div>'
      +  '<div><span class="stat-label">OI Δ</span><br>'+oiChgStr+'</div>'
      +  '<div><span class="stat-label">Bars</span><br>'
      +    '<span style="color:var(--text-secondary)">'+(strike.candle_count||0)+'</span></div>'
      +'</div>'
      +(showLevels ? '<div class="d-flex flex-wrap gap-2 mt-2 pt-2"'
          +' style="border-top:1px solid var(--border);font-size:.78rem">'
          +'<div><span class="stat-label">Entry</span><br>'
          +  '<span style="color:var(--blue);font-weight:600">'+_fmt(sig.entry)+'</span></div>'
          +'<div><span class="stat-label">SL</span><br>'
          +  '<span style="color:var(--red)">'+_fmt(sig.sl)+'</span></div>'
          +'<div><span class="stat-label">T1</span><br>'
          +  '<span style="color:var(--green)">'+_fmt(sig.t1)+'</span></div>'
          +'<div><span class="stat-label">T2</span><br>'
          +  '<span style="color:var(--green)">'+_fmt(sig.t2)+'</span></div>'
          +'<div><span class="stat-label">T3</span><br>'
          +  '<span style="color:var(--green)">'+_fmt(sig.t3)+'</span></div>'
          +'</div>' : '')
      +'<div style="font-size:.7rem;color:var(--text-label);margin-top:6px">'
      +  'Fired: '+(sig.fired_at||'—')+'&nbsp;|&nbsp;Expiry: '+(strike.expiry||'—')
      +'</div>'
      +'</div>';
  }

  // ── NEW badge (30s) ───────────────────────────────────────────────────────
  function _flashNew(sid) {
    var el = _el('pov-new-' + sid);
    if (!el) return;
    el.style.display = 'inline-block';
    if (_newTimers[sid]) clearTimeout(_newTimers[sid]);
    _newTimers[sid] = setTimeout(function () { el.style.display = 'none'; }, 30000);
  }

  // ── Signal log ────────────────────────────────────────────────────────────
  function _renderLog(log) {
    var tbody  = _el('pov-log-tbody');
    var empty  = _el('pov-log-empty');
    var countEl = _el('pov-log-count');
    if (!tbody) return;
    if (!log || !log.length) { if (empty) empty.style.display = ''; return; }
    if (empty)   empty.style.display  = 'none';
    if (countEl) countEl.textContent  = '(' + log.length + ')';
    tbody.innerHTML = log.map(function (s) {
      var c = s.action === 'STRONG' ? 'var(--green)' : 'var(--yellow)';
      return '<tr style="border-color:var(--border)">'
        +'<td style="border-color:var(--border);color:var(--text-secondary)">'+(s.time||'—')+'</td>'
        +'<td style="border-color:var(--border);color:var(--text-primary);font-weight:600">'+(s.label||s.strike+' '+s.option_type)+'</td>'
        +'<td style="border-color:var(--border);color:'+c+';font-weight:600">'+(s.action||'—')+'</td>'
        +'<td style="border-color:var(--border);color:var(--text-secondary)">'+(s.score!=null?s.score+'/5':'—')+'</td>'
        +'<td style="border-color:var(--border);color:var(--blue)">'+_fmt(s.entry)+'</td>'
        +'<td style="border-color:var(--border);color:var(--red)">'+_fmt(s.sl)+'</td>'
        +'<td style="border-color:var(--border);color:var(--green)">'+_fmt(s.t1)+'</td>'
        +'</tr>';
    }).join('');
  }

  // ── Poll status ───────────────────────────────────────────────────────────
  function _poll() {
    fetch('/api/pov/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.active) return;
        var ceHTML = '', peHTML = '', anyNew = false;
        (data.strikes || []).forEach(function (s) {
          var html = _renderCard(s);
          if (s.option_type === 'CE') ceHTML += html; else peHTML += html;
          if (s.signal && s.signal.is_new) { anyNew = true; _flashNew(s.sid); }
        });
        var ceEl = _el('pov-ce-cards'); if (ceEl) ceEl.innerHTML = ceHTML;
        var peEl = _el('pov-pe-cards'); if (peEl) peEl.innerHTML = peHTML;
        if (anyNew) _beep();
        _renderLog(data.signal_log || []);
      })
      .catch(function () {});
  }

  // ── Start feed ────────────────────────────────────────────────────────────
  function _startFeed() {
    var startBtn = _el('pov-start-btn');
    var atm = parseInt((_el('pov-atm') || {}).value, 10);
    var gap = parseInt((_el('pov-gap') || {}).value, 10) || 50;

    if (!atm) { _setErr('ATM Strike is required.'); return; }
    _setErr('');
    if (startBtn) startBtn.disabled = true;

    fetch('/api/pov/setup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbol:     (_el('pov-symbol') || {}).value,
        expiry:     (_el('pov-expiry') || {}).value,
        atm_strike: atm,
        strike_gap: gap,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (startBtn) startBtn.disabled = false;
        if (d.error) { _setErr(d.error); return; }
        _el('pov-cards-section').style.display = 'block';
        _el('pov-active-badge').style.display  = 'inline-flex';
        _el('pov-stop-btn').style.display      = 'inline-block';
        _el('pov-start-btn').style.display     = 'none';
        if (!_pollTimer) { _poll(); _pollTimer = setInterval(_poll, 5000); }
      })
      .catch(function (e) {
        if (startBtn) startBtn.disabled = false;
        _setErr('Request failed: ' + e);
      });
  }

  // ── Stop feed ─────────────────────────────────────────────────────────────
  function _stopFeed() {
    fetch('/api/pov/stop', {method: 'POST'}).catch(function () {});
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    _el('pov-cards-section').style.display = 'none';
    _el('pov-active-badge').style.display  = 'none';
    _el('pov-stop-btn').style.display      = 'none';
    _el('pov-start-btn').style.display     = 'inline-block';
    _el('pov-start-btn').disabled          = false;
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    var symEl    = _el('pov-symbol');
    var startBtn = _el('pov-start-btn');
    var stopBtn  = _el('pov-stop-btn');
    var refBtn   = _el('pov-refresh-btn');
    var atmEl    = _el('pov-atm');

    // Auto-fetch on symbol change
    if (symEl) symEl.addEventListener('change', function () {
      _fetchAtmInfo(symEl.value);
    });

    // Re-fetch on gap change → recompute ATM
    if (refBtn) refBtn.addEventListener('click', function () {
      _fetchAtmInfo((symEl || {}).value || 'NIFTY');
    });

    if (startBtn) startBtn.addEventListener('click', _startFeed);
    if (stopBtn)  stopBtn.addEventListener('click',  _stopFeed);

    // Allow Enter on ATM field to start
    if (atmEl) atmEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') _startFeed();
    });

    // If already active, resume polling immediately
    fetch('/api/pov/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.active) {
          _el('pov-cards-section').style.display = 'block';
          _el('pov-active-badge').style.display  = 'inline-flex';
          _el('pov-stop-btn').style.display      = 'inline-block';
          _el('pov-start-btn').style.display     = 'none';
          _poll();
          _pollTimer = setInterval(_poll, 5000);
        } else {
          // Auto-load ATM info for default symbol
          _fetchAtmInfo((symEl || {}).value || 'NIFTY');
        }
      })
      .catch(function () {
        _fetchAtmInfo((symEl || {}).value || 'NIFTY');
      });
  });

})();
