(function () {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────────
  var _pollTimer  = null;
  var _newTimers  = {};   // sid → setTimeout handle for NEW badge removal
  var _audioCtx   = null;

  // ── Audio beep (Web Audio API — no external deps) ─────────────────────────
  function _beep() {
    try {
      if (!_audioCtx) {
        _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
      var osc  = _audioCtx.createOscillator();
      var gain = _audioCtx.createGain();
      osc.connect(gain);
      gain.connect(_audioCtx.destination);
      osc.type            = 'sine';
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.18, _audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + 0.35);
      osc.start(_audioCtx.currentTime);
      osc.stop(_audioCtx.currentTime + 0.35);
    } catch (e) { /* audio blocked — ignore */ }
  }

  // ── Badge helpers ─────────────────────────────────────────────────────────
  function _actionBadge(action) {
    if (action === 'STRONG') {
      return '<span class="badge-buy" style="font-size:.78rem">⚡ STRONG</span>';
    }
    if (action === 'WATCH') {
      return '<span class="badge-wait" style="font-size:.78rem">👁 WATCH</span>';
    }
    return '<span class="badge-neutral" style="font-size:.78rem">— WAIT</span>';
  }

  function _dots(signal) {
    var keys = ['c1', 'c2', 'c3', 'c4', 'c5'];
    return keys.map(function (k, i) {
      var on = signal[k];
      return '<span title="C' + (i + 1) + '" style="font-size:.95rem;color:' +
        (on ? 'var(--green)' : 'var(--border-strong)') + '">' +
        (on ? '●' : '○') + '</span>';
    }).join(' ');
  }

  function _fmt(v) {
    return (v == null || v === undefined) ? '—' : v;
  }

  // ── Card renderer ─────────────────────────────────────────────────────────
  function _renderCard(strike) {
    var sig = strike.signal || {};
    var lc  = strike.last_candle || {};
    var cc  = strike.current_candle || {};

    var showLevels = (sig.action === 'STRONG' || sig.action === 'WATCH');
    var ltp        = cc.close || lc.close || 0;
    var oi         = cc.oi    || lc.oi    || 0;
    var oiChange   = lc.oi_change != null ? lc.oi_change : null;
    var oiChgStr   = oiChange == null ? '—'
      : (oiChange >= 0 ? '<span style="color:var(--green)">+' + oiChange + '</span>'
                       : '<span style="color:var(--red)">'  + oiChange + '</span>');

    var borderCol = sig.action === 'STRONG' ? 'var(--green-border)'
                  : sig.action === 'WATCH'  ? 'var(--yellow-border)'
                  : 'var(--border)';

    return [
      '<div class="stat-card mb-3 pov-card" id="pov-card-' + strike.sid + '"',
      ' style="border-color:' + borderCol + ';position:relative;transition:border-color .3s">',
      '  <div class="d-flex align-items-center justify-content-between mb-2">',
      '    <span style="font-size:.95rem;font-weight:600;color:var(--text-primary)">' + strike.label + '</span>',
      '    <div class="d-flex align-items-center gap-2">',
      '      ' + _actionBadge(sig.action),
      '      <span id="pov-new-' + strike.sid + '" class="pov-new-badge" style="display:none;',
      '            background:var(--green-bg);color:var(--green);border:1px solid var(--green-border);',
      '            border-radius:var(--radius-pill);font-size:.7rem;font-weight:700;padding:2px 8px">NEW</span>',
      '    </div>',
      '  </div>',
      '  <div class="d-flex gap-2 mb-2">' + _dots(sig) + '</div>',
      '  <div class="d-flex gap-3 mb-2" style="font-size:.8rem">',
      '    <div><span class="stat-label">LTP</span><br><span style="color:var(--text-primary);font-weight:600">' + (ltp ? ltp.toFixed(2) : '—') + '</span></div>',
      '    <div><span class="stat-label">OI</span><br><span style="color:var(--text-secondary)">' + (oi ? oi.toLocaleString() : '—') + '</span></div>',
      '    <div><span class="stat-label">OI Δ</span><br>' + oiChgStr + '</div>',
      '    <div><span class="stat-label">Candles</span><br><span style="color:var(--text-secondary)">' + (strike.candle_count || 0) + '</span></div>',
      '  </div>',
      showLevels ? [
        '<div class="d-flex flex-wrap gap-2 mt-2 pt-2" style="border-top:1px solid var(--border);font-size:.78rem">',
        '  <div><span class="stat-label">Entry</span><br><span style="color:var(--blue);font-weight:600">' + _fmt(sig.entry) + '</span></div>',
        '  <div><span class="stat-label">SL</span><br><span style="color:var(--red)">' + _fmt(sig.sl) + '</span></div>',
        '  <div><span class="stat-label">T1</span><br><span style="color:var(--green)">' + _fmt(sig.t1) + '</span></div>',
        '  <div><span class="stat-label">T2</span><br><span style="color:var(--green)">' + _fmt(sig.t2) + '</span></div>',
        '  <div><span class="stat-label">T3</span><br><span style="color:var(--green)">' + _fmt(sig.t3) + '</span></div>',
        '</div>',
      ].join('') : '',
      '  <div style="font-size:.7rem;color:var(--text-label);margin-top:6px">',
      '    Fired: ' + (sig.fired_at || '—'),
      '    &nbsp;|&nbsp; Expiry: ' + (strike.expiry || '—'),
      '  </div>',
      '</div>',
    ].join('\n');
  }

  // ── NEW badge flash (30 s) ────────────────────────────────────────────────
  function _flashNew(sid) {
    var el = document.getElementById('pov-new-' + sid);
    if (!el) return;

    el.style.display = 'inline-block';
    if (_newTimers[sid]) clearTimeout(_newTimers[sid]);
    _newTimers[sid] = setTimeout(function () {
      el.style.display = 'none';
    }, 30000);
  }

  // ── Signal log renderer ───────────────────────────────────────────────────
  function _renderLog(log) {
    var tbody = document.getElementById('pov-log-tbody');
    if (!tbody) return;

    var empty = document.getElementById('pov-log-empty');
    if (!log || !log.length) {
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';

    document.getElementById('pov-log-count').textContent =
      '(' + log.length + ')';

    tbody.innerHTML = log.map(function (s) {
      var acol = s.action === 'STRONG' ? 'var(--green)' : 'var(--yellow)';
      return '<tr style="border-color:var(--border)">' +
        '<td style="color:var(--text-secondary);border-color:var(--border)">' + (s.time || '—') + '</td>' +
        '<td style="color:var(--text-primary);font-weight:600;border-color:var(--border)">' + (s.label || s.strike + ' ' + s.option_type) + '</td>' +
        '<td style="color:' + acol + ';font-weight:600;border-color:var(--border)">' + (s.action || '—') + '</td>' +
        '<td style="color:var(--text-secondary);border-color:var(--border)">' + (s.score != null ? s.score + '/5' : '—') + '</td>' +
        '<td style="color:var(--blue);border-color:var(--border)">' + _fmt(s.entry) + '</td>' +
        '<td style="color:var(--red);border-color:var(--border)">'  + _fmt(s.sl)    + '</td>' +
        '<td style="color:var(--green);border-color:var(--border)">' + _fmt(s.t1)   + '</td>' +
        '</tr>';
    }).join('');
  }

  // ── Poll /api/pov/status ──────────────────────────────────────────────────
  function _poll() {
    fetch('/api/pov/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.active) return;

        var ceContainer = document.getElementById('pov-ce-cards');
        var peContainer = document.getElementById('pov-pe-cards');
        if (!ceContainer || !peContainer) return;

        var ceHTML = '';
        var peHTML = '';
        var anyNew = false;

        (data.strikes || []).forEach(function (strike) {
          var html = _renderCard(strike);
          if (strike.option_type === 'CE') {
            ceHTML += html;
          } else {
            peHTML += html;
          }
          if (strike.signal && strike.signal.is_new) {
            anyNew = true;
            _flashNew(strike.sid);
          }
        });

        ceContainer.innerHTML = ceHTML;
        peContainer.innerHTML = peHTML;

        if (anyNew) _beep();

        _renderLog(data.signal_log || []);
      })
      .catch(function () {});
  }

  // ── Form submit ───────────────────────────────────────────────────────────
  function _onFormSubmit(e) {
    e.preventDefault();

    var errEl  = document.getElementById('pov-error');
    var infoEl = document.getElementById('pov-info');
    errEl.style.display  = 'none';
    infoEl.style.display = 'none';

    var payload = {
      symbol:     document.getElementById('pov-symbol').value,
      expiry:     document.getElementById('pov-expiry').value,
      atm_strike: parseInt(document.getElementById('pov-atm').value, 10) || 0,
      strike_gap: parseInt(document.getElementById('pov-gap').value, 10) || 50,
    };

    if (!payload.atm_strike) {
      errEl.textContent    = 'ATM Strike is required.';
      errEl.style.display  = 'block';
      return;
    }

    document.getElementById('pov-start-btn').disabled = true;

    fetch('/api/pov/setup', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById('pov-start-btn').disabled = false;
        if (d.error) {
          errEl.textContent   = d.error;
          errEl.style.display = 'block';
          return;
        }
        // Success — show cards section, start polling
        infoEl.textContent   = 'Watching ' + (d.strikes || []).join(', ') +
          (d.expiry ? '  ·  Expiry: ' + d.expiry : '');
        infoEl.style.display = 'block';

        document.getElementById('pov-cards-section').style.display = 'block';
        document.getElementById('pov-status-pill').style.display   = 'block';
        document.getElementById('pov-stop-btn').style.display      = 'inline-block';

        if (!_pollTimer) {
          _poll();
          _pollTimer = setInterval(_poll, 5000);
        }
      })
      .catch(function (err) {
        document.getElementById('pov-start-btn').disabled = false;
        errEl.textContent   = 'Request failed: ' + err;
        errEl.style.display = 'block';
      });
  }

  // ── Stop button ───────────────────────────────────────────────────────────
  function _onStop() {
    fetch('/api/pov/stop', {method: 'POST'}).catch(function () {});
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    document.getElementById('pov-cards-section').style.display = 'none';
    document.getElementById('pov-status-pill').style.display   = 'none';
    document.getElementById('pov-stop-btn').style.display      = 'none';
    document.getElementById('pov-info').style.display          = 'none';
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    var form    = document.getElementById('pov-form');
    var stopBtn = document.getElementById('pov-stop-btn');

    if (form)    form.addEventListener('submit', _onFormSubmit);
    if (stopBtn) stopBtn.addEventListener('click', _onStop);

    // If already active (e.g. page refresh mid-session), resume polling
    fetch('/api/pov/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.active) {
          document.getElementById('pov-cards-section').style.display = 'block';
          document.getElementById('pov-status-pill').style.display   = 'block';
          document.getElementById('pov-stop-btn').style.display      = 'inline-block';
          _poll();
          _pollTimer = setInterval(_poll, 5000);
        }
      })
      .catch(function () {});
  });

})();
