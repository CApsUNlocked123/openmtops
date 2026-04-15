/**
 * Strategy Dashboard — polling + DOM update logic.
 *
 * Polls /api/dashboard/snapshot every 5s and /api/dashboard/oi_map every 30s.
 * Automatically starts on DOMContentLoaded; switches instruments without reload.
 */

"use strict";

// ── State ────────────────────────────────────────────────────────────────────
let snapshotTimer = null;
let oiMapTimer    = null;
let oiChart       = null;
let phaseChart    = null;

// ── Helpers ───────────────────────────────────────────────────────────────────
function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value ?? "—";
}

function currentInstrument() {
    return document.getElementById("instrument-sel")?.value ?? "NIFTY";
}

function clearTimers() {
    if (snapshotTimer) { clearInterval(snapshotTimer); snapshotTimer = null; }
    if (oiMapTimer)    { clearTimeout(oiMapTimer);     oiMapTimer    = null; }
    _oiAvailable = false;
}

// ── Chart initialisation ──────────────────────────────────────────────────────
function initOIChart() {
    const ctx = document.getElementById("oi-chart");
    if (!ctx) return;
    oiChart = new Chart(ctx, {
        type: "bar",
        data: {
            labels: [],
            datasets: [
                {
                    // CE OI — negative values so bars extend LEFT
                    label: "CE OI",
                    data: [],
                    backgroundColor: [],
                    borderColor: "rgba(220,53,69,0.9)",
                    borderWidth: 1,
                    borderSkipped: false,
                    barPercentage: 0.85,
                },
                {
                    // PE OI — positive values so bars extend RIGHT
                    label: "PE OI",
                    data: [],
                    backgroundColor: [],
                    borderColor: "rgba(25,135,84,0.9)",
                    borderWidth: 1,
                    borderSkipped: false,
                    barPercentage: 0.85,
                },
            ],
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            // Canvas fills the position:relative wrapper set in the template
            animation: { duration: 300 },
            plugins: {
                legend: { labels: { color: "#adb5bd", boxWidth: 12 } },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const abs = Math.abs(ctx.raw);
                            return `${ctx.dataset.label}: ${abs >= 1000000
                                ? (abs / 1000000).toFixed(2) + "M"
                                : (abs / 1000).toFixed(1) + "K"}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    stacked: false,
                    ticks: {
                        color: "#adb5bd",
                        callback: v => {
                            const abs = Math.abs(v);
                            return abs >= 1000000
                                ? (abs / 1000000).toFixed(1) + "M"
                                : (abs / 1000).toFixed(0) + "K";
                        },
                    },
                    grid: { color: "rgba(255,255,255,.06)" },
                    // Centre divider at x=0 — bright white line
                    afterBuildTicks(axis) {
                        axis.ticks = axis.ticks.filter(t => t.value !== 0);
                    },
                },
                y: {
                    ticks: { color: "#e9ecef", font: { size: 11 } },
                    grid: {
                        color: ctx => ctx.tick?.value === 0
                            ? "rgba(255,255,255,0.35)"
                            : "rgba(255,255,255,.06)",
                    },
                },
            },
        },
    });
}

// ── Phase colours + segment helpers ──────────────────────────────────────────
const PHASE_COLORS = {
    "BASE":       "#6c757d",
    "BREAKOUT":   "#ffc107",
    "EXHAUSTION": "#fd7e14",
    "REVERSAL":   "#0dcaf0",
};

function _phaseColor(phase, closes, startIdx, endIdx) {
    if (phase === "TREND_RIDE") {
        return (closes[endIdx] ?? 0) >= (closes[startIdx] ?? 0) ? "#198754" : "#dc3545";
    }
    return PHASE_COLORS[phase] ?? "#6c757d";
}

// RLE-compress phases_per_candle → [{phase, startIdx, endIdx, color}]
function _buildSegments(phases, closes) {
    if (!phases || phases.length === 0) return [];
    const segs = [];
    let s = 0;
    for (let i = 1; i <= phases.length; i++) {
        if (i === phases.length || phases[i] !== phases[s]) {
            const e = i - 1;
            segs.push({ phase: phases[s], startIdx: s, endIdx: e,
                        color: _phaseColor(phases[s], closes, s, e) });
            s = i;
        }
    }
    return segs;
}

// ── Global plugin guarded to phaseChart — beforeDraw/afterDraw only ───────────
// beforeDatasetsDraw / afterDatasetsDraw are unreliable for chart-local plugins
// in Chart.js 4.4; using beforeDraw + afterDraw is 100% stable.
const _phaseChartPlugin = {
    id: "phaseChartPlugin",
    beforeDraw(chart) {
        if (chart !== phaseChart) return;
        const segs = chart._phaseSegs;
        if (!segs || !segs.length) return;
        const { ctx, chartArea, scales } = chart;
        if (!scales.x || !chartArea) return;
        const n = chart.data.labels.length;
        ctx.save();
        segs.forEach(r => {
            const x0 = scales.x.getPixelForValue(r.startIdx);
            const x1 = r.endIdx < n - 1
                ? scales.x.getPixelForValue(r.endIdx + 1)
                : chartArea.right;
            ctx.fillStyle = r.color + "28";
            ctx.fillRect(x0, chartArea.top, x1 - x0, chartArea.bottom - chartArea.top);
        });
        ctx.restore();
    },
    afterDraw(chart) {
        if (chart !== phaseChart) return;
        const ohlc = chart._ohlc;
        if (!ohlc || !ohlc.length) return;
        const { ctx, chartArea, scales } = chart;
        if (!scales.x || !scales.y || !chartArea) return;
        const step = ohlc.length > 1
            ? (chartArea.right - chartArea.left) / ohlc.length : 12;
        const barW = Math.max(2, step * 0.6);
        ctx.save();
        ctx.beginPath();
        ctx.rect(chartArea.left, chartArea.top,
                 chartArea.right - chartArea.left, chartArea.bottom - chartArea.top);
        ctx.clip();
        ohlc.forEach((d, i) => {
            const x     = scales.x.getPixelForValue(i);
            const yH    = scales.y.getPixelForValue(d.h);
            const yL    = scales.y.getPixelForValue(d.l);
            const yO    = scales.y.getPixelForValue(d.o);
            const yC    = scales.y.getPixelForValue(d.c);
            const bull  = d.c >= d.o;
            const clr   = bull ? "#26a65b" : "#e74c3c";
            const bodyY = Math.min(yO, yC);
            const bodyH = Math.max(1.5, Math.abs(yC - yO));
            ctx.strokeStyle = clr;
            ctx.lineWidth   = 1;
            ctx.beginPath();
            ctx.moveTo(x, yH);
            ctx.lineTo(x, yL);
            ctx.stroke();
            if (bull) {
                ctx.strokeRect(x - barW / 2, bodyY, barW, bodyH);
            } else {
                ctx.fillStyle = clr;
                ctx.fillRect(x - barW / 2, bodyY, barW, bodyH);
            }
        });
        ctx.restore();
    },
};
Chart.register(_phaseChartPlugin);

// ── Phase chart init ──────────────────────────────────────────────────────────
function initPhaseChart() {
    const ctx = document.getElementById("phase-chart");
    if (!ctx) return;
    phaseChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    // Close line — drives y-scale; faint so candlesticks dominate visually
                    label: "Close",
                    data: [],
                    borderColor: "rgba(200,200,200,0.25)",
                    borderWidth: 1,
                    pointRadius: 0,
                    pointHoverRadius: 0,
                    fill: false,
                    tension: 0,
                    order: 10,
                },
                {
                    label: "EMA 9",
                    data: [],
                    borderColor: "#ffc107",
                    borderWidth: 1.5,
                    borderDash: [4, 3],
                    pointRadius: 0,
                    pointHoverRadius: 3,
                    tension: 0.3,
                    fill: false,
                    spanGaps: true,
                    order: 2,
                },
                {
                    // Colored dot at the start of each phase segment
                    label: "Phase",
                    data: [],
                    backgroundColor: [],
                    borderColor: [],
                    borderWidth: 1.5,
                    pointRadius: 6,
                    pointHoverRadius: 8,
                    pointStyle: "circle",
                    showLine: false,
                    spanGaps: false,
                    order: 0,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 300 },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: "#adb5bd", boxWidth: 12, font: { size: 11 },
                        filter: item => item.datasetIndex === 1,
                    },
                },
                tooltip: {
                    callbacks: {
                        title: items => items[0]?.label ?? "",
                        label: item => {
                            if (item.datasetIndex === 0) {
                                const d = phaseChart._ohlc?.[item.dataIndex];
                                if (!d) return null;
                                return `O ${d.o.toFixed(1)}  H ${d.h.toFixed(1)}  L ${d.l.toFixed(1)}  C ${d.c.toFixed(1)}`;
                            }
                            if (item.datasetIndex === 1 && item.raw != null)
                                return `EMA9: ${item.raw.toFixed(2)}`;
                            if (item.datasetIndex === 2 && item.raw != null) {
                                const lbl = phaseChart._phasePointLabels?.[item.dataIndex];
                                return lbl ? `● ${lbl}` : null;
                            }
                            return null;
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: "#adb5bd", font: { size: 10 }, maxRotation: 0,
                             autoSkip: true, maxTicksLimit: 12 },
                    grid: { color: "rgba(255,255,255,.05)" },
                },
                y: {
                    position: "right",
                    ticks: { color: "#adb5bd", font: { size: 10 } },
                    grid:  { color: "rgba(255,255,255,.05)" },
                },
            },
        },
    });
}

// ── Phase chart update ────────────────────────────────────────────────────────
function updatePhaseChart(candles, emaValues, phasesPerCandle) {
    if (!phaseChart) return;
    if (!candles || candles.length === 0) return;

    const labels = candles.map(c => c.t);
    const closes = candles.map(c => c.c);

    // Y-axis: derive min/max from actual high/low across all candles
    const allHighs = candles.map(c => c.h ?? c.c);
    const allLows  = candles.map(c => c.l ?? c.c);
    const yMax = Math.max(...allHighs);
    const yMin = Math.min(...allLows);
    const pad  = (yMax - yMin) * 0.05;
    phaseChart.options.scales.y.min = yMin - pad;
    phaseChart.options.scales.y.max = yMax + pad;

    phaseChart.data.labels           = labels;
    phaseChart.data.datasets[0].data = closes;
    phaseChart.data.datasets[1].data = emaValues ?? [];
    phaseChart._ohlc                 = candles;

    const segs = (phasesPerCandle && phasesPerCandle.length === closes.length)
        ? _buildSegments(phasesPerCandle, closes) : [];
    phaseChart._phaseSegs = segs;

    // Colored dot at the first candle of each phase segment
    const ptData   = new Array(labels.length).fill(null);
    const ptColors = new Array(labels.length).fill("transparent");
    const ptLabels = new Array(labels.length).fill(null);
    segs.forEach(s => {
        ptData[s.startIdx]   = closes[s.startIdx];
        ptColors[s.startIdx] = s.color;
        ptLabels[s.startIdx] = s.phase;
    });
    phaseChart.data.datasets[2].data            = ptData;
    phaseChart.data.datasets[2].backgroundColor = ptColors;
    phaseChart.data.datasets[2].borderColor     = ptColors.map(col =>
        col === "transparent" ? "transparent" : "#fff");
    phaseChart._phasePointLabels = ptLabels;

    phaseChart.update("none");
}

// ── Polling ───────────────────────────────────────────────────────────────────
function startPolling() {
    pollSnapshot();
    pollOIMap();
    snapshotTimer = setInterval(pollSnapshot, 5000);
    // OI map timer is managed dynamically in applyOIMap — don't set it here
}

function pollSnapshot() {
    fetch(`/api/dashboard/snapshot?instrument=${currentInstrument()}`)
        .then(r => r.json())
        .then(applySnapshot)
        .catch(err => console.error("[dashboard] snapshot error:", err));
}

function pollOIMap() {
    fetch(`/api/dashboard/oi_map?instrument=${currentInstrument()}`)
        .then(r => r.json())
        .then(applyOIMap)
        .catch(err => console.error("[dashboard] oi_map error:", err));
}

// ── OI map polling — fast retry until available, then slow ────────────────────
let _oiAvailable = false;

function scheduleOIMap() {
    if (oiMapTimer) { clearTimeout(oiMapTimer); oiMapTimer = null; }
    const delay = _oiAvailable ? 30000 : 5000;
    oiMapTimer = setTimeout(() => {
        pollOIMap();
    }, delay);
}

// ── Apply snapshot ────────────────────────────────────────────────────────────
function applySnapshot(data) {
    if (!data || !data.active) {
        setText("status-badge", "Inactive");
        return;
    }
    if (!data.ready) {
        const n = data.candle_count ?? 0;
        setText("status-badge", `Collecting candles (${n}/6)`);
        setText("regime-label", "Waiting for data…");
        return;
    }

    updateRegimeBanner(data.regime, data.phase, data.velocity, data.spot);
    updateGreeksMomentum(data);
    updatePhaseTimeline(data.phases_per_candle ?? [], data.candles_chart ?? []);
    updatePhaseChart(data.candles_chart ?? [], data.ema_chart ?? [], data.phases_per_candle ?? []);
    updateLinearScorecard(data.linear_score, data.day_character);
    updateLiveCandle(data.live_candle ?? null);
    updateSignalCard(data.signal ?? null);
    // Spec v1.0 overlays: day char, ATR, confidence, dynamic levels, guards
    updateSpecV1(data);

    const badge = document.getElementById("status-badge");
    if (badge) {
        badge.className = "badge bg-success fs-6 px-3";
        const lc = data.live_candle;
        badge.textContent = lc
            ? `Active · ${data.candle_count} candles + live ${lc.minutes_elapsed}m`
            : `Active · ${data.candle_count} candles`;
    }
}

// ── Regime Banner ─────────────────────────────────────────────────────────────
function updateRegimeBanner(regime, phase, velocity, spot) {
    const banner = document.getElementById("regime-banner");
    if (!banner) return;

    const map = {
        "IMPULSE_UP":     ["alert-success",   "↑ IMPULSE UP"],
        "IMPULSE_DOWN":   ["alert-danger",    "↓ IMPULSE DOWN"],
        "CONSOLIDATION":  ["alert-secondary", "◆ CONSOLIDATION"],
        "REVERSAL_WATCH": ["alert-warning",   "⚠ REVERSAL WATCH"],
    };
    const [cls, label] = map[regime] ?? ["alert-secondary", regime ?? "—"];
    banner.className = `alert ${cls} fw-bold text-center fs-4 py-3 mb-3`;
    setText("regime-label", label);
    setText("phase-label",    phase ?? "—");
    setText("velocity-label", velocity ? `${velocity.type} · ${velocity.velocity} pts/bar` : "—");
    setText("spot-price",     spot != null ? spot.toFixed(2) : "—");
}

// ── Greeks Momentum ───────────────────────────────────────────────────────────
function _fmtOIDelta(v) {
    if (v == null) return "—";
    const sign = v >= 0 ? "+" : "-";
    const abs  = Math.abs(v);
    return sign + (abs >= 1_000_000
        ? (abs / 1_000_000).toFixed(2) + "M"
        : (abs / 1_000).toFixed(1) + "K");
}

function updateGreeksMomentum(data) {
    // CE / PE OI deltas — available only when OI tracker is running
    if (data.oi_available) {
        setText("kpi-ce-delta", _fmtOIDelta(data.total_ce_delta));
        setText("kpi-pe-delta", _fmtOIDelta(data.total_pe_delta));
    } else {
        setText("kpi-ce-delta", "—");
        setText("kpi-pe-delta", "—");
    }

    // KPI cards
    setText("kpi-velocity", data.velocity ? `${data.velocity.velocity} / ${data.velocity.type}` : "—");

    const health = data.trend_health ?? {};
    const score  = health.score ?? 0;
    setText("kpi-health-score", score + "/100");
    setText("health-score-text", score + "/100");
    setText("candle-count", data.candle_count ?? "—");

    // Health bar colour
    const bar = document.getElementById("health-bar");
    if (bar) {
        bar.style.width = score + "%";
        bar.className = "progress-bar " + (score >= 70 ? "bg-success" : score >= 40 ? "bg-warning" : "bg-danger");
    }

    // Warnings list
    const warnEl = document.getElementById("health-warnings");
    if (warnEl) {
        warnEl.innerHTML = "";
        const warnings = health.warnings ?? [];
        if (warnings.length === 0) {
            warnEl.innerHTML = `<li class="text-secondary">No warnings</li>`;
        } else {
            warnings.forEach(w => {
                const li = document.createElement("li");
                li.textContent = "⚠ " + w;
                warnEl.appendChild(li);
            });
        }
    }
}

// ── Phase Timeline bar ────────────────────────────────────────────────────────
function updatePhaseTimeline(phasesPerCandle, candles) {
    const container = document.getElementById("phase-timeline");
    if (!container || !phasesPerCandle || phasesPerCandle.length === 0) return;

    const closes = (candles ?? []).map(c => c.c);
    const segs   = _buildSegments(phasesPerCandle, closes);
    const total  = phasesPerCandle.length || 1;

    container.innerHTML = "";
    segs.forEach(s => {
        const barCount = s.endIdx - s.startIdx + 1;
        const pct      = Math.max(2, barCount / total * 100);
        const startLbl = candles?.[s.startIdx]?.t ?? "";
        const endLbl   = candles?.[s.endIdx]?.t   ?? "";
        const pts      = (closes[s.endIdx] ?? 0) - (closes[s.startIdx] ?? 0);
        const sign     = pts >= 0 ? "+" : "";

        const div = document.createElement("div");
        div.style.cssText = [
            `width:${pct.toFixed(1)}%`,
            `background:${s.color}`,
            "display:flex", "align-items:center", "justify-content:center",
            "font-size:10px", "color:#fff", "font-weight:600",
            "border-right:1px solid rgba(0,0,0,.3)", "overflow:hidden",
        ].join(";");
        div.title = `${s.phase} · ${startLbl}–${endLbl} · ${sign}${pts.toFixed(1)} pts · ${barCount} bars`;
        if (pct > 4) div.textContent = s.phase.substring(0, 4).toUpperCase();
        container.appendChild(div);
    });
}

// ── Linear Move Scorecard ─────────────────────────────────────────────────────
const ENTER_THRESHOLD_BY_DAY = {
    "TREND_DAY":    65,
    "RANGE_DAY":    70,
    "VOLATILE_DAY": 85,
};

function updateLinearScorecard(ls, dayCharacter) {
    if (!ls) return;
    const score     = ls.score ?? 0;
    const threshold = ENTER_THRESHOLD_BY_DAY[dayCharacter] ?? 70;

    const thrEl = document.getElementById("enter-threshold");
    if (thrEl) thrEl.textContent = threshold;

    const scoreEl = document.getElementById("linear-score");
    if (scoreEl) {
        scoreEl.textContent = score;
        scoreEl.style.color = score >= threshold ? "#198754" : score >= 40 ? "#ffc107" : "#dc3545";
    }

    const banner = document.getElementById("move-banner");
    if (banner) {
        const signal = ls.signal ?? (score >= threshold ? "ENTER" : score >= 40 ? "WAIT" : "AVOID");
        if (signal === "ENTER") {
            banner.className = "alert alert-success text-center fs-6 fw-bold mt-2 py-2";
        } else if (signal === "WAIT") {
            banner.className = "alert alert-warning text-center fs-6 fw-bold mt-2 py-2";
        } else {
            banner.className = "alert alert-danger text-center fs-6 fw-bold mt-2 py-2";
        }
        banner.textContent = signal;
    }

    const bd = ls.breakdown ?? {};
    Object.entries(bd).forEach(([key, val]) => {
        const barEl = document.getElementById(`score-${key}`);
        if (barEl) barEl.style.width = `${val}%`;
        const numEl = document.getElementById(`score-num-${key}`);
        if (numEl) numEl.textContent = val;
    });
}

// ── OI Map ────────────────────────────────────────────────────────────────────
function applyOIMap(data) {
    const unavailEl  = document.getElementById("oi-unavailable");
    const availEl    = document.getElementById("oi-available-content");

    if (!data || !data.available) {
        _oiAvailable = false;
        if (unavailEl) unavailEl.classList.remove("d-none");
        if (availEl)   availEl.classList.add("d-none");
        scheduleOIMap();   // retry in 5s
        return;
    }

    _oiAvailable = true;
    if (unavailEl) unavailEl.classList.add("d-none");
    if (availEl)   availEl.classList.remove("d-none");

    updateOIChart(data);
    updateWallMarkers(data.wall);

    const pcr = data.pcr_now;
    setText("pcr-value", pcr != null ? pcr.toFixed(2) : "—");

    const pcrBadge = document.getElementById("pcr-label");
    if (pcrBadge) {
        pcrBadge.textContent = data.pcr_label ?? "—";
        const bearish = (data.pcr_label ?? "").includes("BEARISH");
        const bullish = (data.pcr_label ?? "").includes("BULLISH");
        pcrBadge.className = `badge mt-1 ${bullish ? "bg-success" : bearish ? "bg-danger" : "bg-secondary"}`;
    }

    scheduleOIMap();   // next poll in 30s
}

function updateOIChart(data) {
    if (!oiChart) return;
    const strikes = (data.strikes ?? []).map(String);
    const atm     = data.atm_strike;
    const atmIdx  = atm ? strikes.indexOf(String(atm)) : -1;

    // CE OI: negate so bars extend LEFT; PE OI: positive so bars extend RIGHT
    oiChart.data.labels           = strikes;
    oiChart.data.datasets[0].data = (data.ce_oi ?? []).map(v => -v);
    oiChart.data.datasets[1].data = data.pe_oi ?? [];

    // ATM strike: bright solid; others: semi-transparent
    oiChart.data.datasets[0].backgroundColor = strikes.map((_, i) =>
        i === atmIdx ? "rgba(220,53,69,1)" : "rgba(220,53,69,0.45)"
    );
    oiChart.data.datasets[1].backgroundColor = strikes.map((_, i) =>
        i === atmIdx ? "rgba(25,135,84,1)" : "rgba(25,135,84,0.45)"
    );

    // Make the ATM bar border stand out
    oiChart.data.datasets[0].borderWidth = strikes.map((_, i) => i === atmIdx ? 2 : 1);
    oiChart.data.datasets[1].borderWidth = strikes.map((_, i) => i === atmIdx ? 2 : 1);

    oiChart.update("none");
}

function updateWallMarkers(wall) {
    if (!wall) return;
    setText("wall-resistance", wall.resistance_strike ?? "—");
    setText("wall-support",    wall.support_strike    ?? "—");
    const dist = wall.nearest_wall_distance;
    setText("wall-distance", dist != null ? dist.toFixed(0) + " pts" : "—");
}

// ── Signal Card ───────────────────────────────────────────────────────────────
function updateSignalCard(sig) {
    const card         = document.getElementById("signal-card");
    const badge        = document.getElementById("signal-action-badge");
    const levelsEl     = document.getElementById("signal-levels");
    const symbolEl     = document.getElementById("signal-symbol");
    const entryEl      = document.getElementById("signal-entry");
    const targetEl     = document.getElementById("signal-target");
    const slEl         = document.getElementById("signal-sl");
    const reasonEl     = document.getElementById("signal-reason");
    const timeEl       = document.getElementById("signal-time");
    const counterWrap  = document.getElementById("signal-counter-wrap");
    const counterList  = document.getElementById("signal-counter-list");

    if (!card || !sig) return;

    const action    = sig.action ?? "WAIT";
    const direction = sig.direction;
    const isNew     = sig.is_new ?? false;

    // Card always visible once we have data
    card.classList.remove("d-none");

    // ── Border + badge ────────────────────────────────────────────────────────
    card.classList.remove("border-success", "border-danger", "border-warning", "border-secondary");
    badge.classList.remove("bg-success", "bg-danger", "bg-warning", "text-dark");

    if (action === "BUY") {
        card.classList.add("border-success");
        badge.classList.add("bg-success");
        badge.textContent = direction === "CE" ? "📈 BUY CE" : "📉 BUY PE";
    } else if (action === "NO_TRADE") {
        card.classList.add("border-danger");
        badge.classList.add("bg-danger");
        badge.textContent = "🚫 NO TRADE";
    } else {
        card.classList.add("border-secondary");
        badge.classList.add("bg-secondary");
        badge.textContent = "⏳ WAIT";
    }

    // ── Entry / target / SL (BUY only) ───────────────────────────────────────
    if (action === "BUY" && sig.entry != null) {
        levelsEl.classList.remove("d-none");
        const strike = sig.atm_strike ?? "ATM";
        symbolEl.textContent = `${sig.instrument} ${strike} ${direction}`;
        entryEl.textContent  = `₹${sig.entry}`;
        targetEl.textContent = `₹${sig.target}`;
        slEl.textContent     = `₹${sig.sl}`;
    } else {
        levelsEl.classList.add("d-none");
    }

    // ── Reason ────────────────────────────────────────────────────────────────
    if (reasonEl) reasonEl.textContent = sig.reason ?? "";
    if (timeEl)   timeEl.textContent   = sig.generated_at ? `@ ${sig.generated_at}` : "";

    // ── Counter reasons ───────────────────────────────────────────────────────
    const counters = sig.counter_reasons ?? [];
    if (action === "NO_TRADE" && counters.length > 0) {
        counterWrap.classList.remove("d-none");
        counterList.innerHTML = "";
        counters.forEach(r => {
            const li = document.createElement("li");
            li.textContent = r;
            counterList.appendChild(li);
        });
    } else {
        counterWrap.classList.add("d-none");
    }

    // ── Flash on new signal ───────────────────────────────────────────────────
    if (isNew) {
        card.classList.remove("signal-flash");
        void card.offsetWidth;  // force reflow so animation restarts
        card.classList.add("signal-flash");
    }
}

// ── Spec v1.0 overlays ────────────────────────────────────────────────────────
// Renders day_character, ATR, confidence, dynamic levels and guard chips using
// fields emitted by build_signal_output() on the snapshot.
const DAY_CHAR_CLASS = {
    "TREND_DAY":    "bg-success",
    "RANGE_DAY":    "bg-secondary",
    "VOLATILE_DAY": "bg-warning text-dark",
};

function _show(id, on) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("d-none", !on);
}

function updateSpecV1(data) {
    // Reveal the signal card whenever we have spec v1 data to show, even
    // if the legacy `signal` field was null (keeps confidence + guards
    // visible while the market warms up).
    const card = document.getElementById("signal-card");
    if (card && (data.confidence || data.dynamic_levels || data.block_reason
                 || data.late_entry || data.whipsaw_lockout)) {
        card.classList.remove("d-none");
    }

    // ── Day character chip ────────────────────────────────────────────────
    const dayChip  = document.getElementById("day-char-chip");
    const dayLabel = document.getElementById("day-char-label");
    const dayChar  = data.day_character;
    if (dayChip && dayLabel) {
        if (dayChar) {
            dayChip.classList.remove("d-none", "bg-secondary", "bg-success", "bg-warning", "text-dark");
            (DAY_CHAR_CLASS[dayChar] ?? "bg-secondary").split(" ").forEach(c => dayChip.classList.add(c));
            dayChip.classList.add("badge");
            dayLabel.textContent = dayChar.replace("_", " ");
        } else {
            dayChip.classList.add("d-none");
        }
    }

    // ── ATR chip ──────────────────────────────────────────────────────────
    const atrChip = document.getElementById("atr-chip");
    const atrVal  = document.getElementById("atr-val");
    if (atrChip && atrVal) {
        if (data.atr != null) {
            atrChip.classList.remove("d-none");
            atrVal.textContent = Number(data.atr).toFixed(0);
        } else {
            atrChip.classList.add("d-none");
        }
    }

    // ── Guard chips ───────────────────────────────────────────────────────
    _show("late-entry-chip", !!data.late_entry);
    _show("whipsaw-chip",    !!data.whipsaw_lockout);

    // ── Confidence pill + bar ─────────────────────────────────────────────
    const conf      = data.confidence ?? null;
    const pill      = document.getElementById("confidence-pill");
    const pillLabel = document.getElementById("confidence-label");
    const barWrap   = document.getElementById("confidence-bar-wrap");
    const bar       = document.getElementById("confidence-bar");
    const pctText   = document.getElementById("confidence-pct-text");

    if (conf && conf.total_factors > 0) {
        if (pill && pillLabel) {
            pill.classList.remove("d-none", "bg-success", "bg-warning", "bg-danger", "text-dark");
            const mod = conf.modifier;
            if (mod === "HIGH") {
                pill.classList.add("bg-success");
                pillLabel.textContent = `🟢 HIGH · ${conf.confidence_pct}%`;
            } else if (mod === "LOW") {
                pill.classList.add("bg-warning", "text-dark");
                pillLabel.textContent = `🟡 LOW · ${conf.confidence_pct}%`;
            } else {
                pill.classList.add("bg-danger");
                pillLabel.textContent = `🔴 CONTRADICT · ${conf.confidence_pct}%`;
            }
        }
        if (barWrap && bar && pctText) {
            barWrap.classList.remove("d-none");
            bar.style.width = conf.confidence_pct + "%";
            const color = conf.modifier === "HIGH" ? "#198754"
                        : conf.modifier === "LOW"  ? "#ffc107" : "#dc3545";
            bar.style.background = color;
            pctText.textContent  = `${conf.confidence_pct}%  (${conf.factors_agree}/${conf.total_factors} factors)`;
        }
    } else {
        if (pill)    pill.classList.add("d-none");
        if (barWrap) barWrap.classList.add("d-none");
    }

    // ── Dynamic levels ────────────────────────────────────────────────────
    const dl      = data.dynamic_levels;
    const dlWrap  = document.getElementById("dynamic-levels-wrap");
    const t3Wrap  = document.getElementById("dl-t3-wrap");
    const trailEl = document.getElementById("dl-trail-chip");
    if (dlWrap) {
        if (dl) {
            dlWrap.classList.remove("d-none");
            setText("dl-sl", dl.sl != null ? dl.sl.toFixed(2) : "—");
            setText("dl-t1", dl.t1 != null ? dl.t1.toFixed(2) : "—");
            setText("dl-t2", dl.t2 != null ? dl.t2.toFixed(2) : "—");
            setText("dl-rr", `1 : ${dl.rr_t1 ?? "—"}`);
            if (dl.t3 != null) {
                if (t3Wrap) t3Wrap.classList.remove("d-none");
                setText("dl-t3", dl.t3.toFixed(2));
            } else if (t3Wrap) {
                t3Wrap.classList.add("d-none");
            }
            if (trailEl) trailEl.classList.toggle("d-none", !dl.trail_after_t2);
        } else {
            dlWrap.classList.add("d-none");
        }
    }

    // ── Block reason ──────────────────────────────────────────────────────
    const brWrap = document.getElementById("block-reason-wrap");
    const brText = document.getElementById("block-reason-text");
    if (brWrap && brText) {
        if (data.block_reason) {
            brWrap.classList.remove("d-none");
            brText.textContent = data.block_reason;
        } else {
            brWrap.classList.add("d-none");
        }
    }

    // ── Final signal: stamp the existing action badge so it's visible even
    //     when the legacy signal_engine returns WAIT. Only upgrade the badge
    //     to ENTER_HIGH/ENTER_LOW — don't overwrite NO_TRADE/BUY paths.
    const finalSig = data.final_signal;
    const badge    = document.getElementById("signal-action-badge");
    if (badge && finalSig && (finalSig === "ENTER_HIGH" || finalSig === "ENTER_LOW")) {
        // Only enhance when the legacy badge isn't already showing BUY
        const txt = (badge.textContent || "").toUpperCase();
        if (!txt.includes("BUY")) {
            const card = document.getElementById("signal-card");
            if (card) {
                card.classList.remove("d-none", "border-danger", "border-warning", "border-secondary");
                card.classList.add("border-success");
            }
            badge.classList.remove("bg-danger", "bg-warning", "bg-secondary", "text-dark");
            badge.classList.add("bg-success");
            const dir = data.signal_direction === "SHORT" ? "PE" : "CE";
            badge.textContent = finalSig === "ENTER_HIGH"
                ? `📈 ENTER ${dir} · HIGH`
                : `📈 ENTER ${dir} · LOW`;
        }
    }
}

// ── OI auto-start ─────────────────────────────────────────────────────────────
function startOITracking(instrument) {
    fetch("/api/dashboard/start_oi", {
        method:  "POST",
        headers: {"Content-Type": "application/json"},
        body:    JSON.stringify({ instrument }),
    })
    .then(r => r.json())
    .then(d => {
        if (d.error) console.warn("[dashboard] start_oi:", d.error);
    })
    .catch(err => console.error("[dashboard] start_oi error:", err));
}

// ── Live candle indicator ──────────────────────────────────────────────────────
function updateLiveCandle(liveCandle) {
    const el = document.getElementById("live-candle-info");
    if (!el) return;
    if (!liveCandle) {
        el.textContent = "";
        el.classList.add("d-none");
        return;
    }
    el.classList.remove("d-none");
    el.textContent = `▶ Live bar: ${liveCandle.minutes_elapsed}m · C ${liveCandle.close?.toFixed(2) ?? "—"}`;
}

// ── Entry point ───────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initOIChart();
    initPhaseChart();
    startPolling();
    startOITracking(currentInstrument());

    const sel = document.getElementById("instrument-sel");
    if (sel) {
        sel.addEventListener("change", () => {
            clearTimers();
            startPolling();
            startOITracking(currentInstrument());
        });
    }
});
