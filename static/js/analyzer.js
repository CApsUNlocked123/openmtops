/* Option Analyzer — chain loading + SocketIO live updates */

function initAnalyzer(subscribedSids, subscribedStrikes) {
  const instrSel    = document.getElementById("instrument-sel");
  const expirySel   = document.getElementById("expiry-sel");
  const loadBtn     = document.getElementById("load-btn");
  const subBtn      = document.getElementById("subscribe-btn");
  const trackOiBtn  = document.getElementById("track-oi-btn");
  const chainWrap   = document.getElementById("chain-wrap");
  const chainBody   = document.getElementById("chain-body");
  const ultpEl      = document.getElementById("ultp-val");
  const rowCntEl    = document.getElementById("row-count");
  const selectAll   = document.getElementById("select-all");
  const feedBadge   = document.getElementById("feed-badge");
  const feedSids    = document.getElementById("feed-sids");
  const pcrValEl    = document.getElementById("pcr-val");
  const pcrBiasEl   = document.getElementById("pcr-bias-badge");
  const maxPainEl   = document.getElementById("max-pain-val");
  const clarityEl   = document.getElementById("clarity-badge");

  let loadedRows      = [];
  let lotSize         = 65;
  let currentUltp     = 0;
  let ulSecurityId    = "";
  let socket          = null;
  let sidToCell       = {};   // sid → {ltp: td, oi: td}
  let liveOI          = {};   // strike(int) → { ce_oi, pe_oi, ce_ltp, pe_ltp }
  let sidToStrike     = {};   // security_id → { strike, side: "ce"|"pe" }
  const signalPanel   = document.getElementById("signal-panel");

  // ── On load: if already subscribed, show feed badge and reconnect SocketIO
  if (subscribedSids && Object.keys(subscribedSids).length > 0) {
    feedBadge.classList.remove("d-none");
    const sids = Object.values(subscribedSids).flatMap(v => [v.ce_sid, v.pe_sid]).filter(Boolean);
    feedSids.textContent = `(${sids.length} instruments)`;
    connectSocketIO(sids);
  }

  // ── Instrument change → load expiries ────────────────────────────────────
  instrSel.addEventListener("change", () => {
    expirySel.disabled = true;
    loadBtn.disabled   = true;
    expirySel.innerHTML = "<option>Loading…</option>";

    fetch("/analyzer/expiries", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({instrument: instrSel.value}),
    })
    .then(r => r.json())
    .then(d => {
      expirySel.innerHTML = "";
      (d.expiries || []).forEach(exp => {
        const opt = document.createElement("option");
        opt.value = exp; opt.textContent = exp.slice(0, 10);
        expirySel.appendChild(opt);
      });
      expirySel.disabled = false;
      loadBtn.disabled   = false;
    })
    .catch(() => { expirySel.innerHTML = "<option>Error</option>"; });
  });

  // Auto-trigger expiry load for pre-selected instrument
  if (instrSel.value) instrSel.dispatchEvent(new Event("change"));

  // ── Load chain button ─────────────────────────────────────────────────────
  loadBtn.addEventListener("click", () => {
    loadBtn.textContent = "Loading…";
    loadBtn.disabled    = true;

    fetch("/analyzer/chain", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({instrument: instrSel.value, expiry: expirySel.value}),
    })
    .then(r => r.json())
    .then(d => {
      loadedRows    = d.rows || [];
      lotSize       = d.lot_size || 65;
      currentUltp   = d.ultp || 0;
      ulSecurityId  = d.ul_security_id || "";

      // Info bar
      ultpEl.textContent    = currentUltp ? "₹" + currentUltp.toFixed(2) : "—";
      rowCntEl.textContent  = loadedRows.length;

      if (pcrValEl)   pcrValEl.textContent  = d.pcr ? d.pcr.toFixed(3) : "—";
      if (maxPainEl)  maxPainEl.textContent = d.max_pain ? d.max_pain.toLocaleString() : "—";
      if (pcrBiasEl)  renderBiasBadge(pcrBiasEl, d.pcr_bias);
      if (clarityEl)  renderClarityBadge(clarityEl, d.clarity);

      // Build live OI map from ALL rows (not just rendered ATM±10 slice)
      liveOI = {};
      sidToStrike = {};
      loadedRows.forEach(r => {
        liveOI[r.strike] = { ce_oi: r.ce.oi, pe_oi: r.pe.oi,
                              ce_ltp: r.ce.ltp, pe_ltp: r.pe.ltp };
        if (r.ce.security_id) sidToStrike[r.ce.security_id] = { strike: r.strike, side: "ce" };
        if (r.pe.security_id) sidToStrike[r.pe.security_id] = { strike: r.strike, side: "pe" };
      });

      renderChain(loadedRows, currentUltp, d.max_pain);

      signalPanel.style.display = "";
      recomputeKPIs();

      chainWrap.classList.remove("d-none");
      loadBtn.textContent = "Reload";
      loadBtn.disabled    = false;
    })
    .catch(e => {
      alert("Failed to load chain: " + e);
      loadBtn.textContent = "Load Chain";
      loadBtn.disabled    = false;
    });
  });

  // ── Select-all checkbox ───────────────────────────────────────────────────
  selectAll.addEventListener("change", () => {
    chainBody.querySelectorAll(".strike-chk").forEach(c => c.checked = selectAll.checked);
    updateSubBtn();
  });

  // ── Subscribe button ──────────────────────────────────────────────────────
  subBtn.addEventListener("click", () => {
    const strikes = [...chainBody.querySelectorAll(".strike-chk:checked")]
                    .map(c => parseInt(c.dataset.strike));
    if (!strikes.length) return;

    subBtn.textContent = "Subscribing…";
    subBtn.disabled    = true;

    fetch("/analyzer/subscribe", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({strikes}),
    })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { alert(d.error || "Subscribe failed"); return; }
      const sids = Object.values(d.sids_map).flatMap(v => [v.ce_sid, v.pe_sid]).filter(Boolean);
      feedBadge.classList.remove("d-none");
      feedSids.textContent = `(${sids.length} instruments)`;
      connectSocketIO(sids);
      subBtn.textContent = "Subscribed";
    })
    .catch(e => { alert("Subscribe error: " + e); subBtn.textContent = "Subscribe Selected →"; subBtn.disabled = false; });
  });

  // ── Track OI button ───────────────────────────────────────────────────────
  trackOiBtn.addEventListener("click", () => {
    const strikes = [...chainBody.querySelectorAll(".strike-chk:checked")]
                    .map(c => parseInt(c.dataset.strike));
    if (!strikes.length) return;

    trackOiBtn.textContent = "Starting…";
    trackOiBtn.disabled    = true;

    fetch("/oi_tracker/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({strikes, lot_size: lotSize, ultp: currentUltp, ul_security_id: ulSecurityId}),
    })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { alert(d.error || "Failed to start tracking"); trackOiBtn.disabled = false; return; }
      window.location.href = "/oi_tracker";
    })
    .catch(e => { alert("Error: " + e); trackOiBtn.disabled = false; });
  });

  // ── Render chain table ────────────────────────────────────────────────────
  function renderChain(rows, ultp, maxPain) {
    chainBody.innerHTML = "";
    sidToCell = {};
    let step = 50;
    if (rows.length >= 2) step = rows[1].strike - rows[0].strike;
    const atmStrike = (ultp && step) ? Math.round(ultp / step) * step : null;

    // Slice to ATM ± 10 strikes
    if (atmStrike) {
      const atmIdx = rows.findIndex(r => r.strike >= atmStrike);
      if (atmIdx !== -1) rows = rows.slice(Math.max(0, atmIdx - 10), atmIdx + 11);
    }

    rows.forEach(r => {
      const isAtm     = atmStrike && r.strike === atmStrike;
      const isMaxPain = maxPain && r.strike === maxPain;
      const tier      = r.tier;
      const tr        = document.createElement("tr");

      if (isAtm)   tr.classList.add("atm-row");
      if (tier === 1) tr.style.borderLeft = "2px solid #ffc107";

      const ceOI = fmtOI(r.ce.oi);
      const peOI = fmtOI(r.pe.oi);

      const strikeLabel =
        (isMaxPain ? "⚡ " : "") +
        (isAtm     ? "▶ " : "") +
        r.strike.toLocaleString();

      tr.innerHTML = `
        <td class="text-center">
          <input type="checkbox" class="form-check-input strike-chk" data-strike="${r.strike}">
        </td>
        <td class="text-end" id="ce-ltp-${r.strike}">${fmtPrice(r.ce.ltp)}</td>
        <td class="text-end text-info" id="ce-oi-${r.strike}">${ceOI}</td>
        <td class="text-end text-secondary">${r.ce.iv}%</td>
        <td class="text-end text-secondary">${r.ce.delta.toFixed(2)}</td>
        <td class="text-center fw-bold ${isAtm ? "text-primary" : ""}">
          ${strikeLabel}
        </td>
        <td class="text-start text-secondary">${r.pe.delta.toFixed(2)}</td>
        <td class="text-start text-secondary">${r.pe.iv}%</td>
        <td class="text-start text-info" id="pe-oi-${r.strike}">${peOI}</td>
        <td class="text-start" id="pe-ltp-${r.strike}">${fmtPrice(r.pe.ltp)}</td>
        <td class="text-center">${wallBadge(r.wall)}</td>
      `;

      if (r.ce.security_id) {
        sidToCell[r.ce.security_id] = {
          ltp: tr.querySelector(`#ce-ltp-${r.strike}`),
          oi:  tr.querySelector(`#ce-oi-${r.strike}`),
        };
      }
      if (r.pe.security_id) {
        sidToCell[r.pe.security_id] = {
          ltp: tr.querySelector(`#pe-ltp-${r.strike}`),
          oi:  tr.querySelector(`#pe-oi-${r.strike}`),
        };
      }

      tr.querySelector(".strike-chk").addEventListener("change", updateSubBtn);
      chainBody.appendChild(tr);
    });

    updateSubBtn();
  }

  // ── SocketIO for live ticks ───────────────────────────────────────────────
  function connectSocketIO(sids) {
    if (!socket) socket = io();

    socket.on("connect", () => {
      sids.forEach(sid => socket.emit("analyzer_join", {sid}));
    });

    socket.on("az_tick", (data) => {
      const sid  = String(data.sid);
      // Update chain table cells
      const cell = sidToCell[sid];
      if (cell) {
        if (cell.ltp) cell.ltp.textContent = fmtPrice(parseFloat(data.ltp));
        if (cell.oi && data.oi) cell.oi.textContent = fmtOI(parseInt(data.oi));
      }
      // Update liveOI and recompute KPIs
      const info = sidToStrike[sid];
      if (info) {
        const entry = liveOI[info.strike];
        if (entry) {
          if (info.side === "ce") {
            if (data.oi  > 0) entry.ce_oi  = parseInt(data.oi);
            if (data.ltp > 0) entry.ce_ltp = parseFloat(data.ltp);
          } else {
            if (data.oi  > 0) entry.pe_oi  = parseInt(data.oi);
            if (data.ltp > 0) entry.pe_ltp = parseFloat(data.ltp);
          }
          recomputeKPIs();
        }
      }
    });

    if (socket.connected) {
      sids.forEach(sid => socket.emit("analyzer_join", {sid}));
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function updateSubBtn() {
    const n = chainBody.querySelectorAll(".strike-chk:checked").length;
    subBtn.disabled        = n === 0;
    trackOiBtn.disabled    = n === 0;
    subBtn.textContent     = n > 0 ? `Subscribe ${n} Strike(s) →` : "Subscribe Selected →";
    trackOiBtn.textContent = n > 0 ? `Track OI (${n}) →` : "Track OI →";
  }

  function wallBadge(wall) {
    if (!wall) return "<span class='text-muted'>—</span>";
    const map = {
      "FORTRESS":   ["warning",  "🏰 FORT"],
      "CALL_WALL":  ["danger",   "CALL"],
      "PUT_WALL":   ["success",  "PUT"],
      "RESISTANCE": ["secondary","RES"],
      "SUPPORT":    ["info",     "SUP"],
    };
    const [color, label] = map[wall] || ["secondary", wall];
    return `<span class="badge bg-${color}">${label}</span>`;
  }

  function renderBiasBadge(el, bias) {
    const map = {
      "BULLISH":       ["success",   "BULLISH"],
      "MILDLY_BULLISH":["success",   "MILD BULL"],
      "NEUTRAL":       ["secondary", "NEUTRAL"],
      "MILDLY_BEARISH":["danger",    "MILD BEAR"],
      "BEARISH":       ["danger",    "BEARISH"],
    };
    const [color, label] = map[bias] || ["secondary", bias || "—"];
    el.className  = `badge ms-1 bg-${color}`;
    el.textContent = label;
  }

  // ── Real-time KPI computation ─────────────────────────────────────────────

  function recomputeKPIs() {
    if (!Object.keys(liveOI).length) return;
    const mp   = computeMaxPain(liveOI);
    const sigA = computeSetupA(liveOI, currentUltp, mp);
    renderSignalKPI(sigA);
    renderMaxPainKPI(mp, currentUltp);
  }

  function computeMaxPain(oi) {
    const strikes = Object.keys(oi).map(Number).sort((a, b) => a - b);
    let minPain = Infinity, result = null;
    for (const s of strikes) {
      let pain = 0;
      for (const k of strikes) {
        pain += Math.max(0, s - k) * oi[k].ce_oi;
        pain += Math.max(0, k - s) * oi[k].pe_oi;
      }
      if (pain < minPain) { minPain = pain; result = s; }
    }
    return result;
  }

  function computeSetupA(oi, spot, maxPain) {
    if (!spot || spot <= 0) return null;
    let totalCe = 0, totalPe = 0;
    Object.values(oi).forEach(v => { totalCe += v.ce_oi; totalPe += v.pe_oi; });
    const pcr = totalCe > 0 ? totalPe / totalCe : 0;

    const levels = Object.entries(oi).map(([s, v]) => {
      const strike = parseInt(s);
      const rCe = v.pe_oi > 0 ? v.ce_oi / v.pe_oi : Infinity;
      const rPe = v.ce_oi > 0 ? v.pe_oi / v.ce_oi : Infinity;
      const cls  = (rCe >= 2 && rPe >= 2) ? "FORTRESS"
                 : rCe >= 2 ? "CALL_WALL"
                 : rPe >= 2 ? "PUT_WALL"
                 : v.ce_oi >= v.pe_oi ? "RESISTANCE" : "SUPPORT";
      return { strike, ce_oi: v.ce_oi, pe_oi: v.pe_oi,
               total_oi: v.ce_oi + v.pe_oi, cls };
    }).sort((a, b) => b.total_oi - a.total_oi);

    const top3    = levels.slice(0, 3);
    const clarity = top3.length >= 2 && top3[0].total_oi >= 2 * top3[1].total_oi
                    ? "CLEAR" : top3.length === 1 ? "CLEAR" : "MIXED";

    let best = null;
    for (const lv of levels.slice(0, 8)) {
      const dist  = (lv.strike - spot) / spot;
      const adist = Math.abs(dist);
      if (adist < 0.005 || adist > 0.05) continue;
      const direction = dist > 0 ? "LONG" : "SHORT";
      const valid = (direction === "LONG"  && (lv.cls === "PUT_WALL"  || lv.cls === "FORTRESS")) ||
                    (direction === "SHORT" && (lv.cls === "CALL_WALL" || lv.cls === "FORTRESS")) ||
                    Math.abs(lv.strike - maxPain) / Math.max(spot, 1) <= 0.005;
      if (!valid) continue;
      if (direction === "LONG"  && pcr < 0.6) continue;
      if (direction === "SHORT" && pcr > 1.4) continue;
      const blocked = top3.some(o =>
        o.strike !== lv.strike && (
          (direction === "LONG"  && spot < o.strike && o.strike < lv.strike && o.cls === "CALL_WALL") ||
          (direction === "SHORT" && lv.strike < o.strike && o.strike < spot  && o.cls === "PUT_WALL")
        )
      );
      if (blocked) continue;

      let conf = 0;
      const met = [], failed = [];
      met.push(`${lv.cls} @ ${lv.strike.toLocaleString()}`);
      if (levels[0].total_oi > 1.8 * (levels[1] ? levels[1].total_oi : 0)) { conf++; met.push("OI dominant"); }
      else failed.push("Not dominant");
      if ((direction === "LONG" && pcr >= 1.3) || (direction === "SHORT" && pcr <= 0.7)) { conf++; met.push("PCR " + pcr.toFixed(2)); }
      else failed.push("PCR " + pcr.toFixed(2));
      if ((direction === "LONG" && maxPain > spot) || (direction === "SHORT" && maxPain < spot)) { conf++; met.push("MP " + maxPain.toLocaleString()); }
      else failed.push("MP misaligned");
      if (clarity === "CLEAR") { conf++; met.push("CLEAR"); }
      else failed.push("MIXED");
      if (adist <= 0.02) { conf++; met.push((adist * 100).toFixed(1) + "% away"); }
      else failed.push((adist * 100).toFixed(1) + "% away");

      const stopPct  = Math.max(0.004, Math.min(0.015, adist * 0.5));
      const sl       = direction === "LONG" ? spot * (1 - stopPct) : spot * (1 + stopPct);
      const target   = direction === "LONG" ? lv.strike * 0.997 : lv.strike * 1.003;
      const cand = { direction, confidence: Math.round(conf / 5 * 100),
                     strike: lv.strike, cls: lv.cls,
                     entry: spot, target: Math.round(target),
                     sl: Math.round(sl * 100) / 100, met, failed };
      if (!best || cand.confidence > best.confidence) best = cand;
    }
    return best;
  }

  function renderSignalKPI(sig) {
    const dirEl   = document.getElementById("kpi-dir-badge");
    const confEl  = document.getElementById("kpi-conf");
    const wallEl  = document.getElementById("kpi-wall");
    const entEl   = document.getElementById("kpi-entry");
    const tgtEl   = document.getElementById("kpi-target");
    const slEl    = document.getElementById("kpi-sl");
    const condEl  = document.getElementById("kpi-conditions");
    if (!dirEl) return;

    if (!sig) {
      dirEl.textContent  = "No signal";
      dirEl.className    = "fs-5 fw-bold mb-1 text-secondary";
      confEl.textContent = "";
      wallEl.textContent = "—";
      entEl.textContent  = "—";
      tgtEl.textContent  = "—";
      slEl.textContent   = "—";
      condEl.innerHTML   = "";
      return;
    }

    dirEl.textContent = sig.direction === "LONG" ? "🟢 LONG" : "🔴 SHORT";
    dirEl.className   = "fs-5 fw-bold mb-1 " + (sig.direction === "LONG" ? "text-success" : "text-danger");
    confEl.textContent = sig.confidence + "% confidence";
    wallEl.textContent = sig.cls + " @ " + sig.strike.toLocaleString();
    entEl.textContent  = "₹" + sig.entry.toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    tgtEl.textContent  = "₹" + sig.target.toLocaleString();
    slEl.textContent   = "₹" + sig.sl.toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    condEl.innerHTML   = sig.met.map(c => `<span class="badge bg-success me-1">${c}</span>`).join("") +
                         sig.failed.map(c => `<span class="badge bg-secondary me-1">${c}</span>`).join("");
  }

  function renderMaxPainKPI(mp, spot) {
    const mpEl   = document.getElementById("kpi-maxpain");
    const distEl = document.getElementById("kpi-mp-dist");
    const biasEl = document.getElementById("kpi-mp-bias");
    if (!mpEl || !mp) return;

    mpEl.textContent = mp.toLocaleString();
    if (spot > 0) {
      const pct  = ((mp - spot) / spot * 100).toFixed(2);
      const sign = pct >= 0 ? "+" : "";
      distEl.textContent = sign + pct + "% from spot";
      const dir  = mp > spot ? "→ LONG gravity" : mp < spot ? "→ SHORT gravity" : "→ At spot";
      const col  = mp > spot ? "text-success" : mp < spot ? "text-danger" : "text-secondary";
      biasEl.textContent = dir;
      biasEl.className   = "small mt-1 fw-semibold " + col;
    } else {
      distEl.textContent = "";
      biasEl.textContent = "";
    }
  }

  function renderClarityBadge(el, clarity) {
    const map = {
      "CLEAR":  ["success", "CLEAR"],
      "MIXED":  ["warning", "MIXED"],
      "NO_MAP": ["secondary","NO MAP"],
    };
    const [color, label] = map[clarity] || ["secondary", clarity || "—"];
    el.className  = `badge bg-${color}`;
    el.textContent = label;
  }

  function fmtPrice(v) {
    return v > 0 ? "₹" + v.toFixed(2) : "—";
  }

  function fmtOI(v) {
    if (!v) return "—";
    return v >= 1e7 ? (v/1e7).toFixed(2)+"Cr" :
           v >= 1e5 ? (v/1e5).toFixed(2)+"L"  :
           v.toLocaleString();
  }
}
