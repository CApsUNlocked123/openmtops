/* Option Analyzer — fetches 1-min series for nearest 7 strikes and renders
   4 charts (CE/PE Time-vs-IV, CE/PE Spot-vs-Option-Price). Each chart has its
   own strike toggle so you can isolate one strike or compare a subset. */

function initAnalyzer() {
  const instrSel  = document.getElementById("instrument-sel");
  const expirySel = document.getElementById("expiry-sel");
  const loadBtn   = document.getElementById("load-btn");
  const statusEl  = document.getElementById("status-msg");
  const spotEl    = document.getElementById("spot-val");
  const dteEl     = document.getElementById("dte-val");
  const strikesEl = document.getElementById("strikes-val");

  const charts = { ceIv: null, peIv: null, cePrice: null, pePrice: null };
  const maxPainEl     = document.getElementById("max-pain-val");
  const neutralZoneEl = document.getElementById("neutral-zone-val");
  const diagCard      = document.getElementById("diag-card");
  const diagBody      = document.getElementById("diag-body");

  // One color per strike index (0..6). 7 strikes: 3 below + ATM + 3 above.
  const PALETTE = ["#4dabf7", "#51cf66", "#ffd43b", "#fa5252",
                   "#ff922b", "#e599f7", "#22b8cf"];

  // Latest payload (for re-render on toggle) and per-chart visibility map
  let lastData = null;
  const visibleStrikes = {
    ceIv: new Set(), peIv: new Set(), cePrice: new Set(), pePrice: new Set(),
  };

  // ── Load expiries on instrument change ─────────────────────────────────────
  function loadExpiries() {
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
        opt.value = exp;
        opt.textContent = exp.slice(0, 10);
        expirySel.appendChild(opt);
      });
      expirySel.disabled = false;
      loadBtn.disabled   = false;
    })
    .catch(() => { expirySel.innerHTML = "<option>Error</option>"; });
  }

  instrSel.addEventListener("change", loadExpiries);
  if (instrSel.value) loadExpiries();

  // ── Load series ────────────────────────────────────────────────────────────
  loadBtn.addEventListener("click", () => {
    loadBtn.disabled    = true;
    loadBtn.textContent = "Loading…";
    statusEl.textContent = "Fetching 1-min series for 7 strikes (14 legs)…";

    fetch("/analyzer/series", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({instrument: instrSel.value, expiry: expirySel.value}),
    })
    .then(r => r.json())
    .then(d => {
      if (d.error) {
        statusEl.textContent = "Error: " + d.error;
        loadBtn.disabled = false;
        loadBtn.textContent = "Load Charts";
        return;
      }
      lastData = d;
      // Default: show all strikes on every chart
      Object.keys(visibleStrikes).forEach(k => {
        visibleStrikes[k] = new Set(d.strikes.map(s => s.strike));
      });
      buildStrikePickers(d.strikes);
      renderAll();
      loadBtn.disabled = false;
      loadBtn.textContent = "Reload";
    })
    .catch(e => {
      statusEl.textContent = "Network error: " + e;
      loadBtn.disabled = false;
      loadBtn.textContent = "Load Charts";
    });
  });

  // ── Build per-chart strike toggle buttons ─────────────────────────────────
  function buildStrikePickers(strikes) {
    document.querySelectorAll(".strike-picker").forEach(picker => {
      const chartKey = picker.dataset.chart;
      picker.innerHTML = "";

      strikes.forEach((s, i) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-outline-light btn-sm active";
        btn.style.borderColor = PALETTE[i];
        btn.style.color = PALETTE[i];
        btn.style.fontSize = "0.7rem";
        btn.style.padding = "0.1rem 0.4rem";
        btn.textContent = s.strike;
        btn.dataset.strike = s.strike;

        btn.addEventListener("click", () => {
          const set = visibleStrikes[chartKey];
          if (set.has(s.strike)) {
            set.delete(s.strike);
            btn.classList.remove("active");
            btn.style.opacity = "0.35";
          } else {
            set.add(s.strike);
            btn.classList.add("active");
            btn.style.opacity = "1";
          }
          renderChart(chartKey);
        });
        picker.appendChild(btn);
      });
    });
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function renderAll() {
    const d = lastData;
    spotEl.textContent        = d.spot ? d.spot.toLocaleString("en-IN", {maximumFractionDigits: 2}) : "—";
    dteEl.textContent         = d.days_left;
    maxPainEl.textContent     = d.max_pain ? d.max_pain.toLocaleString() : "—";
    neutralZoneEl.textContent = d.neutral_zone ? d.neutral_zone.toLocaleString() : "—";

    const totalPoints = d.strikes.reduce((acc, s) =>
      acc + (s.ce.timestamps.length || 0) + (s.pe.timestamps.length || 0), 0);
    statusEl.textContent = totalPoints > 0
      ? `Loaded ${totalPoints} option ticks across ${d.strikes.length} strikes.`
      : "No intraday data available (market may be closed).";

    renderDiagnostics();

    renderChart("ceIv");
    renderChart("peIv");
    renderChart("cePrice");
    renderChart("pePrice");
  }

  // ── Diagnostics table ──────────────────────────────────────────────────────
  const OI_BADGE = {
    "LONG_BUILDUP":   ["success", "Long Buildup",  "Buyers adding — bullish for direction"],
    "SHORT_BUILDUP":  ["danger",  "Short Buildup", "Writers adding — bearish for direction"],
    "SHORT_COVERING": ["info",    "Short Cover",   "Writers exiting — bullish unwind"],
    "LONG_UNWINDING": ["warning", "Long Unwind",   "Buyers exiting — bearish unwind"],
    "NEUTRAL":        ["secondary", "—",           "No dominant action"],
  };
  const IV_BADGE = {
    "STABLE":      ["success",   "Stable",      "Writers confident — level holding"],
    "RISING":      ["danger",    "Rising",      "Demand outpacing supply — directional pressure"],
    "FALLING":     ["info",      "Falling",     "Premium decay / writer dominance"],
    "FLUCTUATING": ["warning",   "Fluctuating", "Indecision — possible reversal"],
    "UNKNOWN":     ["secondary", "—",           "Insufficient data"],
  };

  function _oiCell(summary, align) {
    const dom = (summary && summary.dominant) || "NEUTRAL";
    const [color, label, tip] = OI_BADGE[dom] || OI_BADGE["NEUTRAL"];
    return `<td class="text-${align}">
      <span class="badge bg-${color}" title="${tip}">${label}</span>
    </td>`;
  }
  function _ivCell(state, align) {
    const s = (state && state.state) || "UNKNOWN";
    const [color, label, tip] = IV_BADGE[s] || IV_BADGE["UNKNOWN"];
    const extra = state && state.mean != null
      ? `<span class="text-secondary ms-1" style="font-size:0.7rem;">μ${state.mean}% σ${state.stdev}</span>`
      : "";
    return `<td class="text-${align}">
      <span class="badge bg-${color}" title="${tip}">${label}</span>${extra}
    </td>`;
  }
  function _volDivCell(vd, align) {
    if (!vd || vd.n_bars === 0) {
      return `<td class="text-${align}"><span class="text-muted">—</span></td>`;
    }
    const ratio = vd.count / vd.n_bars;
    const color = ratio >= 0.5 ? "danger" : ratio >= 0.25 ? "warning" : "secondary";
    const star  = vd.latest ? " ⚠" : "";
    return `<td class="text-${align}">
      <span class="badge bg-${color}" title="${vd.count} divergent bars of ${vd.n_bars} checked">
        ${vd.count}/${vd.n_bars}${star}
      </span>
    </td>`;
  }

  function renderDiagnostics() {
    const d = lastData;
    diagBody.innerHTML = "";
    d.strikes.forEach(s => {
      const atm = d.spot && Math.abs(s.strike - d.spot) === Math.min(...d.strikes.map(x => Math.abs(x.strike - d.spot)));
      const tr = document.createElement("tr");
      tr.innerHTML = [
        _volDivCell(s.ce.vol_div,    "end"),
        _ivCell(s.ce.iv_state,       "end"),
        _oiCell(s.ce.oi_summary,     "end"),
        `<td class="text-center fw-bold ${atm ? "text-warning" : ""}">
          ${atm ? "▶ " : ""}${s.strike.toLocaleString()}
        </td>`,
        _oiCell(s.pe.oi_summary,     "start"),
        _ivCell(s.pe.iv_state,       "start"),
        _volDivCell(s.pe.vol_div,    "start"),
      ].join("");
      diagBody.appendChild(tr);
    });
    diagCard.style.display = "";
  }

  function renderChart(key) {
    if (!lastData) return;
    if (key === "ceIv")    drawIvChart("chart-ce-iv",      "ceIv",    "ce");
    if (key === "peIv")    drawIvChart("chart-pe-iv",      "peIv",    "pe");
    if (key === "cePrice") drawPriceChart("chart-ce-price","cePrice", "ce");
    if (key === "pePrice") drawPriceChart("chart-pe-price","pePrice", "pe");
  }

  function drawIvChart(canvasId, key, side) {
    const visible = visibleStrikes[key];
    const datasets = lastData.strikes
      .map((s, i) => ({ s, i }))
      .filter(({s}) => visible.has(s.strike))
      .map(({s, i}) => {
        const ts  = s[side].timestamps;
        const ivs = s[side].iv;
        const data = ts.map((t, j) => ({ x: t * 1000, y: ivs[j] })).filter(p => p.y != null);
        return {
          label: String(s.strike),
          data,
          borderColor: PALETTE[i],
          backgroundColor: PALETTE[i],
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          spanGaps: true,
        };
      });

    const canvasEl = document.getElementById(canvasId);
    if (charts[key]) charts[key].destroy();
    const existing = Chart.getChart(canvasEl);
    if (existing) existing.destroy();
    charts[key] = new Chart(canvasEl, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        animation: false,
        scales: {
          x: { type: "time", time: { unit: "minute", displayFormats: { minute: "HH:mm" } },
               ticks: { color: "#adb5bd" }, grid: { color: "#343a40" } },
          y: { title: { display: true, text: "IV %", color: "#adb5bd" },
               ticks: { color: "#adb5bd" }, grid: { color: "#343a40" } },
        },
        plugins: {
          legend: { labels: { color: "#dee2e6", font: { size: 11 } } },
          tooltip: { mode: "index", intersect: false },
        },
      },
    });
  }

  function drawPriceChart(canvasId, key, side) {
    const visible = visibleStrikes[key];
    const spotMap = {};
    lastData.spot_series.timestamps.forEach((t, i) => {
      spotMap[t] = lastData.spot_series.close[i];
    });

    const datasets = lastData.strikes
      .map((s, i) => ({ s, i }))
      .filter(({s}) => visible.has(s.strike))
      .map(({s, i}) => {
        const ts     = s[side].timestamps;
        const prices = s[side].price;
        const data = ts.map((t, j) => {
          const spot = spotMap[t];
          if (spot == null) return null;
          return { x: spot, y: prices[j] };
        }).filter(Boolean);
        return {
          label: String(s.strike),
          data,
          borderColor: PALETTE[i],
          backgroundColor: PALETTE[i],
          showLine: false,
          pointRadius: 2,
        };
      });

    // Vertical reference lines at max-pain and neutral-zone.
    // Use the y-range of plotted points; if none, skip.
    const allY = datasets.flatMap(ds => ds.data.map(p => p.y)).filter(v => v != null);
    if (allY.length && (lastData.max_pain || lastData.neutral_zone)) {
      const yMin = Math.min(...allY), yMax = Math.max(...allY);
      if (lastData.max_pain) {
        datasets.push({
          label: `Max Pain ${lastData.max_pain}`,
          data: [{ x: lastData.max_pain, y: yMin }, { x: lastData.max_pain, y: yMax }],
          borderColor: "#ffd43b",
          backgroundColor: "#ffd43b",
          borderDash: [6, 4],
          showLine: true,
          pointRadius: 0,
          borderWidth: 1.5,
        });
      }
      if (lastData.neutral_zone && lastData.neutral_zone !== lastData.max_pain) {
        datasets.push({
          label: `Neutral Zone ${lastData.neutral_zone}`,
          data: [{ x: lastData.neutral_zone, y: yMin }, { x: lastData.neutral_zone, y: yMax }],
          borderColor: "#4dabf7",
          backgroundColor: "#4dabf7",
          borderDash: [2, 4],
          showLine: true,
          pointRadius: 0,
          borderWidth: 1.5,
        });
      }
    }

    const canvasEl2 = document.getElementById(canvasId);
    if (charts[key]) charts[key].destroy();
    const existing2 = Chart.getChart(canvasEl2);
    if (existing2) existing2.destroy();
    charts[key] = new Chart(canvasEl2, {
      type: "scatter",
      data: { datasets },
      options: {
        responsive: true,
        animation: false,
        scales: {
          x: { title: { display: true, text: "Index Spot", color: "#adb5bd" },
               ticks: { color: "#adb5bd" }, grid: { color: "#343a40" } },
          y: { title: { display: true, text: "Option Price", color: "#adb5bd" },
               ticks: { color: "#adb5bd" }, grid: { color: "#343a40" } },
        },
        plugins: {
          legend: { labels: { color: "#dee2e6", font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => `${ctx.dataset.label}: spot ${ctx.parsed.x.toFixed(2)} → ₹${ctx.parsed.y.toFixed(2)}`,
            },
          },
        },
      },
    });
  }
}
