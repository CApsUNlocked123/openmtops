/* Option Analyzer — Advanced (Narrative + Verification Charts).
   Eight time-series charts (CE/PE × IV/OI/Price/Volume). Clicking a narrative
   bullet does three things on each chart:
     1. Filters to the strike(s) named in the bullet.
     2. Draws a translucent vertical band over the bullet's time range.
     3. Restricts the relevant chart's metrics list (so a "volume spike" bullet
        scrolls the user to the volume chart, etc.).
   The bullet's `text` already prints all supporting numbers — the charts are
   purely for visual confirmation. */

function initAnalyzerAdvanced() {
  const instrSel        = document.getElementById("instrument-sel");
  const expirySel       = document.getElementById("expiry-sel");
  const loadBtn         = document.getElementById("load-btn");
  const statusEl        = document.getElementById("status-msg");
  const spotEl          = document.getElementById("spot-val");
  const dteEl           = document.getElementById("dte-val");
  const maxPainEl       = document.getElementById("max-pain-val");
  const neutralZoneEl   = document.getElementById("neutral-zone-val");
  const narrativeCard   = document.getElementById("narrative-card");
  const narrativeList   = document.getElementById("narrative-list");
  const clearBtn        = document.getElementById("clear-highlight-btn");

  // 7 distinct colors, one per strike (matches the regular analyzer)
  const PALETTE = ["#4dabf7", "#51cf66", "#ffd43b", "#fa5252",
                   "#ff922b", "#e599f7", "#22b8cf"];

  // Category → badge color
  const CAT_COLOR = {
    DIVERG:  "danger",
    WALL:    "warning",
    VOLUME:  "info",
    SPOT:    "success",
    CLUSTER: "secondary",
    REGIME:  "primary",
  };
  const SEVERITY_OPACITY = { strong: 1, moderate: 0.85, mild: 0.65 };

  // Chart registry: key → Chart instance
  const charts = {
    ceIv:    null, peIv:    null,
    ceOi:    null, peOi:    null,
    cePrice: null, pePrice: null,
    ceVol:   null, peVol:   null,
  };

  // Mapping: chart key → (side, metric, canvas id, y-axis label)
  const CHART_DEFS = [
    {key: "ceIv",    side: "ce", metric: "iv",     canvas: "chart-ce-iv",    ylabel: "IV %"},
    {key: "peIv",    side: "pe", metric: "iv",     canvas: "chart-pe-iv",    ylabel: "IV %"},
    {key: "ceOi",    side: "ce", metric: "oi",     canvas: "chart-ce-oi",    ylabel: "OI"},
    {key: "peOi",    side: "pe", metric: "oi",     canvas: "chart-pe-oi",    ylabel: "OI"},
    {key: "cePrice", side: "ce", metric: "price",  canvas: "chart-ce-price", ylabel: "Price (₹)"},
    {key: "pePrice", side: "pe", metric: "price",  canvas: "chart-pe-price", ylabel: "Price (₹)"},
    {key: "ceVol",   side: "ce", metric: "volume", canvas: "chart-ce-vol",   ylabel: "Volume"},
    {key: "peVol",   side: "pe", metric: "volume", canvas: "chart-pe-vol",   ylabel: "Volume"},
  ];

  let lastData = null;
  let activeBullet = null;          // currently-highlighted bullet
  let visibleStrikes = new Set();   // strikes currently rendered (default = all)

  // ── Custom Chart.js plugin: draws a translucent vertical band for the
  //    active bullet's [time_from, time_to] range. Registered globally once.
  const highlightPlugin = {
    id: "narrativeHighlight",
    afterDraw(chart) {
      if (!activeBullet || !activeBullet.time_from || !activeBullet.time_to) return;
      const xScale = chart.scales.x;
      if (!xScale) return;
      const xLo = xScale.getPixelForValue(activeBullet.time_from * 1000);
      const xHi = xScale.getPixelForValue(activeBullet.time_to   * 1000);
      const yTop = chart.chartArea.top, yBot = chart.chartArea.bottom;
      const ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = "rgba(255, 212, 59, 0.10)";   // soft yellow band
      ctx.strokeStyle = "rgba(255, 212, 59, 0.6)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.rect(xLo, yTop, xHi - xLo, yBot - yTop);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }
  };
  if (typeof Chart !== "undefined") Chart.register(highlightPlugin);

  // ── Load expiries ──────────────────────────────────────────────────────────
  function loadExpiries() {
    expirySel.disabled = true; loadBtn.disabled = true;
    expirySel.innerHTML = "<option>Loading…</option>";
    fetch("/analyzer/expiries", {
      method: "POST", headers: {"Content-Type": "application/json"},
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
      expirySel.disabled = false; loadBtn.disabled = false;
    })
    .catch(() => { expirySel.innerHTML = "<option>Error</option>"; });
  }
  instrSel.addEventListener("change", loadExpiries);
  if (instrSel.value) loadExpiries();

  // ── Load data + narrative ──────────────────────────────────────────────────
  loadBtn.addEventListener("click", () => {
    loadBtn.disabled = true; loadBtn.textContent = "Reading…";
    statusEl.textContent = "Fetching 7 strikes × 14 legs and generating narrative…";

    fetch("/analyzer/advanced/data", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({instrument: instrSel.value, expiry: expirySel.value}),
    })
    .then(r => r.json())
    .then(d => {
      if (d.error) {
        statusEl.textContent = "Error: " + d.error;
        loadBtn.disabled = false; loadBtn.textContent = "Read the Tape";
        return;
      }
      lastData = d;
      visibleStrikes = new Set(d.strikes.map(s => s.strike));   // show all initially
      renderHeader();
      renderNarrative();
      renderAllCharts();
      loadBtn.disabled = false; loadBtn.textContent = "Re-read";
    })
    .catch(e => {
      statusEl.textContent = "Network error: " + e;
      loadBtn.disabled = false; loadBtn.textContent = "Read the Tape";
    });
  });

  // ── Render header ──────────────────────────────────────────────────────────
  function renderHeader() {
    const d = lastData;
    spotEl.textContent        = d.spot ? d.spot.toLocaleString("en-IN", {maximumFractionDigits: 2}) : "—";
    dteEl.textContent         = d.days_left;
    maxPainEl.textContent     = d.max_pain ? d.max_pain.toLocaleString() : "—";
    neutralZoneEl.textContent = d.neutral_zone ? d.neutral_zone.toLocaleString() : "—";
    statusEl.textContent      = `Loaded ${d.strikes.length} strikes — ${d.narrative.length} narrative bullets.`;
  }

  // ── Render narrative bullets ───────────────────────────────────────────────
  function renderNarrative() {
    narrativeList.innerHTML = "";
    const bullets = lastData.narrative || [];
    if (!bullets.length) {
      narrativeList.innerHTML = `
        <li class="text-secondary small">
          No narrative bullets generated — chain looks quiet, or there's not
          enough intraday data yet (need ~15 minutes after market open).
        </li>`;
      narrativeCard.style.display = "";
      return;
    }
    bullets.forEach((b, idx) => {
      const li = document.createElement("li");
      li.className = "narrative-bullet d-flex align-items-start gap-2 py-2 px-2 border-bottom border-secondary";
      li.dataset.idx = idx;
      const color = CAT_COLOR[b.category] || "secondary";
      const opacity = SEVERITY_OPACITY[b.severity] || 1;
      li.innerHTML = `
        <span class="cat-badge badge bg-${color}" style="opacity:${opacity}; min-width:60px;">
          ${b.category}
        </span>
        <span class="flex-grow-1 small">${escapeHtml(b.text)}</span>
      `;
      li.addEventListener("click", () => activateBullet(idx));
      narrativeList.appendChild(li);
    });
    narrativeCard.style.display = "";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ── Activate / clear a bullet ─────────────────────────────────────────────
  function activateBullet(idx) {
    const b = lastData.narrative[idx];
    if (activeBullet === b) {
      clearActive();
      return;
    }
    activeBullet = b;
    narrativeList.querySelectorAll(".narrative-bullet").forEach(el => {
      el.classList.toggle("active", parseInt(el.dataset.idx) === idx);
    });
    clearBtn.style.display = "";
    // Restrict visible strikes to those named by the bullet (if any)
    if (b.strikes && b.strikes.length) {
      visibleStrikes = new Set(b.strikes);
    } else {
      visibleStrikes = new Set(lastData.strikes.map(s => s.strike));
    }
    renderAllCharts();

    // Scroll the most-relevant chart into view based on metrics + sides
    const relevantSide = (b.sides && b.sides[0]) || "ce";
    const primaryMetric = (b.metrics && b.metrics[0]) || "iv";
    const targetKey = `${relevantSide}${primaryMetric.charAt(0).toUpperCase() + primaryMetric.slice(1)}`;
    // Map "oi" → "Oi" etc; our keys are e.g. ceIv / ceOi / cePrice / ceVol
    const keyMap = { iv: "Iv", oi: "Oi", price: "Price", volume: "Vol" };
    const fixedKey = `${relevantSide}${keyMap[primaryMetric] || "Iv"}`;
    const def = CHART_DEFS.find(d => d.key === fixedKey);
    if (def) {
      const el = document.getElementById(def.canvas);
      if (el) el.scrollIntoView({behavior: "smooth", block: "center"});
    }
  }

  function clearActive() {
    activeBullet = null;
    narrativeList.querySelectorAll(".narrative-bullet.active").forEach(el => el.classList.remove("active"));
    clearBtn.style.display = "none";
    visibleStrikes = new Set(lastData.strikes.map(s => s.strike));
    renderAllCharts();
  }
  clearBtn.addEventListener("click", clearActive);

  // ── Render all charts ──────────────────────────────────────────────────────
  function renderAllCharts() {
    CHART_DEFS.forEach(def => drawChart(def));
  }

  function drawChart(def) {
    const { key, side, metric, canvas, ylabel } = def;
    const datasets = lastData.strikes
      .map((s, i) => ({ s, i }))
      .filter(({s}) => visibleStrikes.has(s.strike))
      .map(({s, i}) => {
        const leg = s[side];
        const ts  = leg.timestamps;
        const ys  = leg[metric];
        const data = ts.map((t, j) => {
          const y = ys[j];
          if (y == null) return null;
          return { x: t * 1000, y };
        }).filter(p => p);
        return {
          label: String(s.strike),
          data,
          borderColor: PALETTE[i],
          backgroundColor: PALETTE[i] + "33",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          spanGaps: true,
        };
      });

    if (charts[key]) charts[key].destroy();
    charts[key] = new Chart(document.getElementById(canvas), {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        animation: false,
        scales: {
          x: { type: "time", time: { unit: "minute", displayFormats: { minute: "HH:mm" } },
               ticks: { color: "#adb5bd" }, grid: { color: "#343a40" } },
          y: { title: { display: true, text: ylabel, color: "#adb5bd" },
               ticks: { color: "#adb5bd" }, grid: { color: "#343a40" } },
        },
        plugins: {
          legend: { labels: { color: "#dee2e6", font: { size: 10 } } },
          tooltip: { mode: "index", intersect: false },
        },
      },
    });
  }
}
