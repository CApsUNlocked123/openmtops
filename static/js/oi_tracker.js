/* OI Tracker — SocketIO live updates + duration timer + charts */

function initOITracker() {
  // ── Chart state ────────────────────────────────────────────────────────────
  const MAX_POINTS  = 120;
  const oiHistory   = { labels: [], ceOI: [], peOI: [] };
  let oiLineChart   = null;
  let lvoBarChart   = null;
  let lastChartMin  = null;   // "HH:MM" of the last plotted point

  // ── SocketIO connection ────────────────────────────────────────────────────
  const socket = io();

  socket.on("connect", () => {
    socket.emit("oi_tracker_join", {});
  });

  socket.on("oi_update", (data) => {
    applyUpdate(data);
  });

  // ── Duration timer (client-side, updates every second) ────────────────────
  const durEl = document.getElementById("duration");
  if (OI_START_TIME && durEl) {
    const startMs = new Date(OI_START_TIME).getTime();
    setInterval(() => {
      const secs = Math.floor((Date.now() - startMs) / 1000);
      durEl.textContent = `${Math.floor(secs / 60)}m ${secs % 60}s`;
    }, 1000);
  }

  // ── Chart initialization ───────────────────────────────────────────────────
  function initCharts() {
    const GRID = "rgba(255,255,255,0.06)";
    const TICK = { color: "#6c757d", font: { size: 10 } };

    // Line chart — CE Δ vs PE Δ over time (both start at 0; Y-axis stays small & readable)
    const lineCtx = document.getElementById("oi-line-chart");
    if (lineCtx) {
      oiLineChart = new Chart(lineCtx, {
        type: "line",
        data: {
          labels: oiHistory.labels,
          datasets: [
            { label: "Call OI Δ", data: oiHistory.ceOI, borderColor: "rgba(220,53,69,0.85)", borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false },
            { label: "Put OI Δ",  data: oiHistory.peOI, borderColor: "rgba(25,135,84,0.85)",  borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: ctx => ctx.dataset.label + ": " + fmtOI(ctx.parsed.y) } },
          },
          scales: {
            x: { ticks: { ...TICK, maxTicksLimit: 6 }, grid: { color: GRID } },
            y: { ticks: { ...TICK, callback: v => fmtOI(v) }, grid: { color: GRID } },
          },
        },
      });
    }

    // Bar chart — cumulative large-order qty per strike (CE vs PE)
    const barCtx = document.getElementById("lvo-bar-chart");
    if (barCtx) {
      lvoBarChart = new Chart(barCtx, {
        type: "bar",
        data: {
          labels: [],
          datasets: [
            { label: "CE Vol", data: [], backgroundColor: "rgba(220,53,69,0.65)", borderWidth: 0 },
            { label: "PE Vol", data: [], backgroundColor: "rgba(25,135,84,0.65)",  borderWidth: 0 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: { labels: { color: "#adb5bd", font: { size: 11 }, boxWidth: 10, padding: 10 } },
            tooltip: { callbacks: { label: ctx => ctx.dataset.label + ": " + ctx.parsed.y.toLocaleString() } },
          },
          scales: {
            x: { ticks: { color: "#adb5bd", font: { size: 11 } }, grid: { display: false } },
            y: { ticks: { ...TICK, callback: v => v >= 1000 ? (v / 1000).toFixed(0) + "k" : v }, grid: { color: GRID } },
          },
        },
      });
    }
  }

  initCharts();

  // ── Apply full KPI update from server ─────────────────────────────────────
  function applyUpdate(data) {
    // PCR card — base stays from server, now+bias computed from row totals below
    setText("pcr-base", data.pcr_base);

    // Recompute totals from selected strike rows only
    let totalCeOI = 0, totalPeOI = 0, totalCeDelta = 0, totalPeDelta = 0;
    (data.rows || []).forEach(row => {
      totalCeOI    += (row.ce_oi    || 0);
      totalPeOI    += (row.pe_oi    || 0);
      totalCeDelta += (row.ce_delta || 0);
      totalPeDelta += (row.pe_delta || 0);
    });

    // CE/PE delta inline text above line chart
    const ceDeltaEl = document.getElementById("total-ce-delta");
    if (ceDeltaEl) {
      ceDeltaEl.textContent = (totalCeDelta >= 0 ? "+" : "") + fmtOI(totalCeDelta);
      ceDeltaEl.className   = "fw-bold " + (totalCeDelta >= 0 ? "text-danger" : "text-success");
    }
    const ceLabelEl = document.getElementById("ce-delta-label");
    if (ceLabelEl) {
      ceLabelEl.textContent = totalCeDelta > 0 ? "writing ↑ bearish"
                            : totalCeDelta < 0 ? "unwinding ↓ bullish" : "";
    }
    const peDeltaEl = document.getElementById("total-pe-delta");
    if (peDeltaEl) {
      peDeltaEl.textContent = (totalPeDelta >= 0 ? "+" : "") + fmtOI(totalPeDelta);
      peDeltaEl.className   = "fw-bold " + (totalPeDelta >= 0 ? "text-success" : "text-danger");
    }
    const peLabelEl = document.getElementById("pe-delta-label");
    if (peLabelEl) {
      peLabelEl.textContent = totalPeDelta > 0 ? "writing ↑ bullish"
                            : totalPeDelta < 0 ? "unwinding ↓ bearish" : "";
    }

    // PCR — recalculated from selected strike OI only
    if (totalCeOI > 0) {
      const pcrNowLive = Math.round((totalPeOI / totalCeOI) * 1000) / 1000;
      setText("pcr-now", pcrNowLive);
      const biasEl = document.getElementById("pcr-bias");
      if (biasEl) {
        const bias = pcrNowLive >= 1.2 ? "BULLISH" : pcrNowLive <= 0.8 ? "BEARISH" : "NEUTRAL";
        biasEl.textContent = bias;
        biasEl.className   = "badge " + (bias === "BULLISH" ? "bg-success" : bias === "BEARISH" ? "bg-danger" : "bg-secondary");
      }
      const pcrChgEl = document.getElementById("pcr-change");
      if (pcrChgEl) {
        const pcrBase = parseFloat(document.getElementById("pcr-base").textContent) || 0;
        const pcrChg  = Math.round((pcrNowLive - pcrBase) * 1000) / 1000;
        pcrChgEl.textContent = (pcrChg >= 0 ? "+" : "") + pcrChg;
        setColor(pcrChgEl, pcrChg >= 0);
      }
    }

    // Update OI line chart (delta from baseline — both lines start at 0)
    updateOILineChart(totalCeDelta, totalPeDelta);

    // Spot price (static reference, sent by server)
    if (data.ultp) setText("spot-price", "₹" + Number(data.ultp).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

    // IV skew card
    setText("avg-ce-iv",     (data.avg_ce_iv || 0) + "%");
    setText("avg-pe-iv",     (data.avg_pe_iv || 0) + "%");
    setText("iv-skew-ratio", data.iv_skew_ratio || "—");
    setText("iv-skew-label", data.iv_skew_label  || "");

    // Straddle cost in header
    const straddleCostEl = document.getElementById("straddle-cost");
    const straddlePctEl  = document.getElementById("straddle-pct");
    if (straddleCostEl && data.straddle_cost) straddleCostEl.textContent = "₹" + data.straddle_cost.toFixed(2);
    if (straddlePctEl  && data.straddle_pct)  straddlePctEl.textContent  = data.straddle_pct + "%";

    // Strike rows
    (data.rows || []).forEach(row => {
      const s = row.strike;
      setCell(`ce-ltp-${s}`,    row.ce_ltp > 0 ? "₹" + row.ce_ltp.toFixed(2) : "—");
      setCell(`ce-oi-${s}`,     fmtOI(row.ce_oi));
      setDeltaCell(`ce-delta-${s}`, row.ce_delta, false);
      setPctCell  (`ce-pct-${s}`,   row.ce_pct,  false);
      setPctCell  (`pe-pct-${s}`,   row.pe_pct,  true);
      setDeltaCell(`pe-delta-${s}`, row.pe_delta, true);
      setCell(`pe-oi-${s}`,     fmtOI(row.pe_oi));
      setCell(`pe-ltp-${s}`,    row.pe_ltp > 0 ? "₹" + row.pe_ltp.toFixed(2) : "—");
      setInnerHTML(`ce-pattern-${s}`, patternBadge(row.ce_pattern));
      setInnerHTML(`pe-pattern-${s}`, patternBadge(row.pe_pattern));
    });

    // Large orders — update count + bar chart
    const orders  = data.large_orders || [];
    const countEl = document.getElementById("lv-count");
    if (countEl) countEl.textContent = orders.length;
    const emptyEl = document.getElementById("lv-empty");
    if (emptyEl) emptyEl.style.display = orders.length ? "none" : "";
    updateLVOBarChart(orders);
  }

  // ── Chart updaters ─────────────────────────────────────────────────────────
  function updateOILineChart(ceOI, peOI) {
    if (!oiLineChart) return;
    const d   = new Date();
    const min = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
    if (min === lastChartMin) return;   // same minute — skip
    lastChartMin = min;
    oiHistory.labels.push(min);
    oiHistory.ceOI.push(ceOI);
    oiHistory.peOI.push(peOI);
    if (oiHistory.labels.length > MAX_POINTS) {
      oiHistory.labels.shift();
      oiHistory.ceOI.shift();
      oiHistory.peOI.shift();
    }
    oiLineChart.update("none");
  }

  function updateLVOBarChart(orders) {
    if (!lvoBarChart) return;
    const byStrike = {};
    orders.forEach(o => {
      const k = String(o.strike);
      if (!byStrike[k]) byStrike[k] = { ce: 0, pe: 0 };
      if (o.type === "CE") byStrike[k].ce += o.qty;
      else                 byStrike[k].pe += o.qty;
    });
    const strikes = Object.keys(byStrike).sort((a, b) => Number(a) - Number(b));
    lvoBarChart.data.labels            = strikes;
    lvoBarChart.data.datasets[0].data  = strikes.map(s => byStrike[s].ce);
    lvoBarChart.data.datasets[1].data  = strikes.map(s => byStrike[s].pe);
    lvoBarChart.update("none");
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }
  function setCell(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  function setInnerHTML(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }
  function setDeltaCell(id, val, positiveGood) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = (val >= 0 ? "+" : "") + fmtOI(val);
    el.className   = "text-end fw-semibold " + colorClass(val, positiveGood);
  }
  function setPctCell(id, val, positiveGood) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = (val >= 0 ? "+" : "") + val + "%";
    el.className   = "text-end " + colorClass(val, positiveGood);
  }
  function colorClass(val, positiveGood) {
    if (val === 0) return "text-secondary";
    return (val > 0) === positiveGood ? "text-success" : "text-danger";
  }
  function setColor(el, positive) {
    el.classList.toggle("text-success", positive);
    el.classList.toggle("text-danger",  !positive);
  }
  function patternBadge(p) {
    const map = {
      "Long Buildup":   `<span class="badge bg-success">↑ Long Build</span>`,
      "Short Buildup":  `<span class="badge bg-danger">↓ Short Build</span>`,
      "Long Unwinding": `<span class="badge bg-warning text-dark">↓ Long Unwind</span>`,
      "Short Covering": `<span class="badge bg-info text-dark">↑ Short Cover</span>`,
    };
    return map[p] || `<span class="text-muted">—</span>`;
  }
  function fmtOI(v) {
    const a = Math.abs(v);
    if (a === 0)       return "—";
    if (a >= 10000000) return (v / 10000000).toFixed(2) + "Cr";
    if (a >= 100000)   return (v / 100000).toFixed(2) + "L";
    return v.toLocaleString();
  }

  // Expose openTradeModal globally so inline onclick handlers can call it
  // ltp param is the server-rendered fallback; live DOM cell is preferred
  window.openTradeModal = function(strike, optType, ltp) {
    document.getElementById("qt-strike").value      = strike;
    document.getElementById("qt-option-type").value = optType;
    document.getElementById("qt-label").textContent = `${strike} ${optType}`;
    // Prefer the live DOM cell value (updated by WebSocket) over the static param
    const ltpCell = document.getElementById(`${optType.toLowerCase()}-ltp-${strike}`);
    const liveLtp = ltpCell ? parseFloat(ltpCell.textContent.replace("₹", "")) || ltp : ltp;
    document.getElementById("qt-entry").value   = liveLtp > 0 ? liveLtp.toFixed(2) : "";
    document.getElementById("qt-sl").value      = "";
    document.getElementById("qt-targets").value = "";
    document.getElementById("qt-lots-auto").checked          = true;
    document.getElementById("qt-lots-manual-input").disabled = true;
    bootstrap.Modal.getOrCreateInstance(document.getElementById("quick-trade-modal")).show();
  };
}

function qtToggleLots(radio) {
  const inp = document.getElementById("qt-lots-manual-input");
  inp.disabled = (radio.value !== "manual");
}
