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

// ── Phase chart ───────────────────────────────────────────────────────────────
function initPhaseChart() {
    const ctx = document.getElementById("phase-chart");
    if (!ctx) return;
    phaseChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    label: "Close",
                    data: [],
                    borderColor: "#e9ecef",
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    tension: 0.3,
                    fill: false,
                    order: 1,
                },
                {
                    label: "EMA 9",
                    data: [],
                    borderColor: "#ffc107",
                    borderWidth: 1.5,
                    borderDash: [4, 3],
                    pointRadius: 0,
                    tension: 0.3,
                    fill: false,
                    spanGaps: true,
                    order: 2,
                },
                {
                    // Phase-start markers: colored dots at each phase transition candle
                    label: "Phase Start",
                    data: [],
                    backgroundColor: [],
                    borderColor: "#fff",
                    borderWidth: 1.5,
                    pointRadius: 7,
                    pointHoverRadius: 9,
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
                        color: "#adb5bd",
                        boxWidth: 12,
                        font: { size: 11 },
                        filter: item => item.datasetIndex < 2,   // hide "Phase Start" from legend
                    },
                },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            if (ctx.datasetIndex === 2 && ctx.raw != null) {
                                const lbl = phaseChart._phasePointLabels?.[ctx.dataIndex] ?? "Phase";
                                return `${lbl}: ₹${ctx.raw.toFixed(2)}`;
                            }
                            return `${ctx.dataset.label}: ${ctx.raw?.toFixed(2) ?? "—"}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: "#adb5bd", font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
                    grid: { color: "rgba(255,255,255,.05)" },
                },
                y: {
                    position: "right",
                    ticks: { color: "#adb5bd", font: { size: 10 } },
                    grid: { color: "rgba(255,255,255,.05)" },
                },
            },
        },
    });
}

function updatePhaseChart(candles, emaValues, timeline) {
    if (!phaseChart) return;
    if (!candles || candles.length === 0) return;

    const labels = candles.map(c => c.t);
    const closes = candles.map(c => c.c);

    phaseChart.data.labels           = labels;
    phaseChart.data.datasets[0].data = closes;
    phaseChart.data.datasets[1].data = emaValues ?? [];

    // Phase-start scatter markers: one colored dot per phase transition
    const phasePoints  = new Array(labels.length).fill(null);
    const ptColors     = new Array(labels.length).fill("transparent");
    const ptLabels     = new Array(labels.length).fill(null);
    if (timeline) {
        for (const t of timeline) {
            const idx = _nearestLabelIdx(labels, t.start_time);
            if (idx >= 0 && closes[idx] != null) {
                phasePoints[idx] = closes[idx];
                ptColors[idx]    = t.color;
                ptLabels[idx]    = t.phase;
            }
        }
    }
    phaseChart.data.datasets[2].data            = phasePoints;
    phaseChart.data.datasets[2].backgroundColor = ptColors;
    phaseChart.data.datasets[2].borderColor     = ptColors.map(c => c === "transparent" ? "transparent" : "#fff");
    phaseChart._phasePointLabels                = ptLabels;

    // Background phase bands (colored regions)
    phaseChart._phaseRegions = _buildPhaseRegions(labels, timeline);
    phaseChart.update("none");
}

function _buildPhaseRegions(labels, timeline) {
    // Map each timeline entry to { startIdx, endIdx, color }
    if (!timeline || timeline.length === 0) return [];
    const regions = [];
    for (const t of timeline) {
        const startIdx = _nearestLabelIdx(labels, t.start_time);
        if (startIdx < 0) continue;
        const endLabel = t.end_time ?? labels[labels.length - 1];
        const endIdx   = _nearestLabelIdx(labels, endLabel);
        regions.push({ startIdx, endIdx: endIdx < 0 ? labels.length - 1 : endIdx, color: t.color + "33", label: t.phase });
    }
    return regions;
}

// Custom Chart.js plugin: draw phase background bands + label
const phaseRegionPlugin = {
    id: "phaseRegions",
    beforeDraw(chart) {
        const regions = chart._phaseRegions;
        if (!regions || regions.length === 0) return;
        const { ctx: c, chartArea, scales } = chart;
        const xScale = scales.x;
        if (!xScale) return;

        regions.forEach(r => {
            const x0 = xScale.getPixelForIndex(r.startIdx);
            const x1 = xScale.getPixelForIndex(r.endIdx);
            if (x0 == null || x1 == null) return;

            // Shaded band
            c.save();
            c.fillStyle = r.color;
            c.fillRect(x0, chartArea.top, x1 - x0, chartArea.bottom - chartArea.top);

            // Phase label at top of band
            c.fillStyle = r.color.replace("33", "cc");
            c.font = "bold 10px sans-serif";
            c.textAlign = "center";
            const midX = (x0 + x1) / 2;
            if (x1 - x0 > 28) {
                c.fillText(r.label.substring(0, 5).toUpperCase(), midX, chartArea.top + 12);
            }
            c.restore();
        });
    },
};
Chart.register(phaseRegionPlugin);

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
    updatePhaseTimeline(data.timeline ?? []);
    updatePhaseChart(data.candles_chart ?? [], data.ema_chart ?? [], data.timeline ?? []);
    updateLinearScorecard(data.linear_score);
    updateLiveCandle(data.live_candle ?? null);

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

// ── Nearest label index helper ────────────────────────────────────────────────
function _nearestLabelIdx(labels, time) {
    if (!time || labels.length === 0) return -1;
    const exact = labels.indexOf(time);
    if (exact >= 0) return exact;
    // First label >= time (rounds phase start up to the next available candle)
    const fwd = labels.findIndex(l => l >= time);
    if (fwd >= 0) return fwd;
    return labels.length - 1;
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

// ── Phase Timeline ────────────────────────────────────────────────────────────
function updatePhaseTimeline(timeline) {
    const container = document.getElementById("phase-timeline");
    if (!container) return;
    if (!timeline || timeline.length === 0) return;

    container.innerHTML = "";
    const totalPts = timeline.reduce((s, t) => s + Math.abs(t.points_moved ?? 1), 0) || 1;

    timeline.forEach(t => {
        const pct  = Math.max(4, Math.abs(t.points_moved ?? 1) / totalPts * 100);
        const div  = document.createElement("div");
        const sign = (t.points_moved ?? 0) >= 0 ? "+" : "";
        div.style.cssText = [
            `width:${pct.toFixed(1)}%`,
            `background:${t.color}`,
            "display:flex",
            "align-items:center",
            "justify-content:center",
            "font-size:11px",
            "color:#fff",
            "font-weight:600",
            "border-right:1px solid rgba(0,0,0,.3)",
        ].join(";");
        div.title = `${t.phase} · ${t.start_time}–${t.end_time ?? "now"} · ${sign}${(t.points_moved ?? 0).toFixed(1)} pts`;
        div.textContent = t.phase.substring(0, 4).toUpperCase();
        container.appendChild(div);
    });
}

// ── Linear Move Scorecard ─────────────────────────────────────────────────────
function updateLinearScorecard(ls) {
    if (!ls) return;
    const score = ls.score ?? 0;

    const scoreEl = document.getElementById("linear-score");
    if (scoreEl) {
        scoreEl.textContent = score;
        scoreEl.style.color = score >= 70 ? "#198754" : score >= 40 ? "#ffc107" : "#dc3545";
    }

    const banner = document.getElementById("move-banner");
    if (banner) {
        const signal = ls.signal ?? (score >= 70 ? "ENTER" : score >= 40 ? "WAIT" : "AVOID");
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
