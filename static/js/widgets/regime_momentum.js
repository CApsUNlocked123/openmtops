/**
 * RegimeMomentum widget — window.WidgetRegistry global.
 *
 * Loaded via <script src> on scan.html.  Must NOT use ES module import/export
 * because innerHTML assignment does not execute <script> tags — the JS must
 * already be in scope before mount() is called.
 *
 * Contract:
 *   window.WidgetRegistry["regime_momentum"].mount(container, socket, instrument)
 *   window.WidgetRegistry["regime_momentum"].unmount()
 */
(function () {
  'use strict';

  var _interval    = null;
  var _tickHandler = null;
  var _socket      = null;
  var _instrument  = null;
  var _container   = null;

  // ── Helpers ────────────────────────────────────────────────────────────

  function _q(id) {
    return _container ? _container.querySelector('#' + id) : null;
  }

  function _setText(id, value) {
    var el = _q(id);
    if (el) el.textContent = (value != null && value !== '') ? value : '—';
  }

  function _fmt2(v) {
    return (v != null) ? parseFloat(v).toFixed(2) : '—';
  }

  function _actionBadgeHtml(action, direction) {
    if (action === 'BUY') {
      return '<span class="badge-buy" id="rm-action">BUY ' + (direction || '') + '</span>';
    }
    if (action === 'NO_TRADE') {
      return '<span class="badge-sell" id="rm-action">NO TRADE</span>';
    }
    return '<span class="badge-wait" id="rm-action">WAIT</span>';
  }

  // ── DOM update ─────────────────────────────────────────────────────────

  var _ACTION_BORDER = {
    BUY:      '#22C55E',   /* --green  */
    WAIT:     '#F59E0B',   /* --yellow */
    NO_TRADE: 'rgba(255,255,255,0.15)',
  };

  function _applyData(d) {
    if (!_container) return;

    var action = d.action || 'WAIT';

    // Signal color top-border on the widget card
    _container.style.borderTopColor = _ACTION_BORDER[action] || 'rgba(255,255,255,0.06)';
    _container.style.borderTopWidth = '3px';
    _container.style.borderTopStyle = 'solid';

    // Error state
    if (d.error) {
      var errEl = _q('rm-error');
      if (!errEl) {
        var div = document.createElement('div');
        div.id = 'rm-error';
        div.className = 'alert alert-warning';
        div.style.cssText = 'font-size:.80rem;padding:8px 12px;';
        _container.appendChild(div);
        errEl = div;
      }
      errEl.textContent = 'Could not fetch signal: ' + d.error;
      return;
    }

    // Action badge
    var badgeEl = _q('rm-action');
    if (badgeEl) {
      badgeEl.outerHTML = _actionBadgeHtml(action, d.direction);
    }

    // Scalar fields
    _setText('rm-instrument', d.instrument || _instrument);
    _setText('rm-ts',         d.generated_at || '');
    _setText('rm-regime',     d.regime || '—');
    _setText('rm-phase',      d.phase  || '—');
    _setText('rm-health',     d.health_score != null ? d.health_score : '—');
    _setText('rm-lin',        d.lin_score    != null ? d.lin_score    : '—');
    _setText('rm-reason',     d.reason || '');

    // BUY levels block — create or remove
    var levelsEl = _q('rm-levels');
    if (action === 'BUY') {
      if (!levelsEl) {
        var reasonEl = _q('rm-reason');
        var lDiv = document.createElement('div');
        lDiv.className = 'row g-2 mb-3';
        lDiv.id = 'rm-levels';
        lDiv.innerHTML =
          '<div class="col-4">' +
            '<div style="font-size:.68rem;color:var(--text-label);text-transform:uppercase;letter-spacing:.06em;">Entry</div>' +
            '<div style="font-size:.92rem;font-weight:700;color:var(--text-primary);" id="rm-entry">—</div>' +
          '</div>' +
          '<div class="col-4">' +
            '<div style="font-size:.68rem;color:var(--text-label);text-transform:uppercase;letter-spacing:.06em;">Target</div>' +
            '<div style="font-size:.92rem;font-weight:700;color:var(--green);" id="rm-target">—</div>' +
          '</div>' +
          '<div class="col-4">' +
            '<div style="font-size:.68rem;color:var(--text-label);text-transform:uppercase;letter-spacing:.06em;">SL</div>' +
            '<div style="font-size:.92rem;font-weight:700;color:var(--red);" id="rm-sl">—</div>' +
          '</div>' +
          '<div class="col-12 mt-1">' +
            '<div style="display:flex;justify-content:space-between;font-size:.68rem;color:var(--text-label);margin-bottom:3px;">' +
              '<span id="rm-rr-sl-lbl">SL</span>' +
              '<span id="rm-rr-en-lbl">Entry</span>' +
              '<span id="rm-rr-t1-lbl">T1</span>' +
            '</div>' +
            '<div style="height:5px;background:rgba(255,255,255,0.07);border-radius:99px;overflow:hidden;">' +
              '<div style="width:34%;height:100%;background:#22C55E;border-radius:99px;"></div>' +
            '</div>' +
            '<div style="text-align:right;font-size:.68rem;color:var(--text-label);margin-top:2px;">2:1 R:R</div>' +
          '</div>';
        if (reasonEl) {
          reasonEl.parentNode.insertBefore(lDiv, reasonEl);
        } else {
          _container.appendChild(lDiv);
        }
        levelsEl = lDiv;
      }
      _setText('rm-entry',  _fmt2(d.entry));
      _setText('rm-target', _fmt2(d.target));
      _setText('rm-sl',     _fmt2(d.sl));
    } else if (levelsEl) {
      levelsEl.remove();
    }

    // Counter reasons
    var countersEl = _q('rm-counters');
    if (d.counter_reasons && d.counter_reasons.length) {
      var items = d.counter_reasons.map(function (r) { return '<li>' + r + '</li>'; }).join('');
      if (countersEl) {
        countersEl.innerHTML = items;
      } else {
        var ul = document.createElement('ul');
        ul.id = 'rm-counters';
        ul.style.cssText = 'font-size:.75rem;color:var(--text-secondary);padding-left:1.1rem;margin-top:6px;margin-bottom:0;';
        ul.innerHTML = items;
        _container.appendChild(ul);
      }
    } else if (countersEl) {
      countersEl.remove();
    }
  }

  // ── Fetch ───────────────────────────────────────────────────────────────

  function _refresh() {
    if (!_instrument) return;
    fetch('/api/scan/data/regime_momentum?instrument=' + _instrument)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) _applyData(d); })
      .catch(function () { /* silent retry next tick */ });
  }

  // ── Public API (window.WidgetRegistry) ────────────────────────────────

  window.WidgetRegistry = window.WidgetRegistry || {};
  window.WidgetRegistry['regime_momentum'] = {

    mount: function (container, socket, instrument) {
      _container  = container;
      _socket     = socket;
      _instrument = instrument ? instrument.toUpperCase() : 'NIFTY';

      // Refresh immediately (HTML fragment already rendered), then every 8 s
      _refresh();
      _interval = setInterval(_refresh, 8000);

      // Live tick — update a LTP badge if the widget has one
      _tickHandler = function (tick) {
        if (!tick || !_container) return;
        var sym = ((tick.trading_symbol || tick.instrument || '')).toUpperCase();
        if (!sym.startsWith(_instrument)) return;
        var ltpEl = _container.querySelector('#rm-ltp');
        if (ltpEl && tick.ltp != null) {
          ltpEl.textContent = parseFloat(tick.ltp).toFixed(2);
        }
      };
      socket.on('tick', _tickHandler);
    },

    unmount: function () {
      clearInterval(_interval);
      _interval = null;
      if (_socket && _tickHandler) {
        _socket.off('tick', _tickHandler);
      }
      _tickHandler = null;
      _socket      = null;
      _instrument  = null;
      _container   = null;
    },
  };

})();
