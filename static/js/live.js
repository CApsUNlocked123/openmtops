/* Live trade page — SocketIO client */

function initLivePage(securityId, entry, sl, target1, quantity, opts) {
  // opts.afterTradeUrl — where to navigate after trade_done.
  //   undefined / omitted → "/" (home, default behaviour for /live page)
  //   null               → stay on page (used by /activetrade)
  const afterTradeUrl = (opts && "afterTradeUrl" in opts) ? opts.afterTradeUrl : "/";

  const socket = io();

  let currentLtp  = null;
  let currentState = "watching";
  let buyPrice    = null;

  const ltpEl       = document.getElementById("ltp-val");
  const bannerEl    = document.getElementById("state-banner");
  const stateTxtEl  = document.getElementById("state-text");
  const feedEl      = document.getElementById("feed-status");
  const activeEl    = document.getElementById("active-section");
  const buyPriceEl  = document.getElementById("buy-price-val");
  const pnlEl       = document.getElementById("pnl-val");
  const orderIdEl   = document.getElementById("order-id-val");
  const progressEl  = document.getElementById("progress-bar");
  const exitLtpIn   = document.getElementById("exit-ltp-input");

  // ── Connection events ────────────────────────────────────────────────────
  socket.on("connect", () => {
    console.log("[socket] connected, id=", socket.id);
    feedEl.innerHTML = '<span class="badge bg-success"><span class="live-dot"></span>Feed Connected</span>';
    socket.emit("live_join", {});
  });

  socket.on("connect_error", (err) => {
    console.error("[socket] connect_error", err);
  });

  socket.on("disconnect", () => {
    console.warn("[socket] disconnected");
    feedEl.innerHTML = '<span class="badge bg-warning text-dark">Reconnecting…</span>';
  });

  // ── Price tick ───────────────────────────────────────────────────────────
  socket.on("tick", (data) => {
    console.log("[tick]", data);
    if (String(data.sid) !== String(securityId)) return;

    const ltp = parseFloat(data.ltp);
    if (ltp <= 0) return;
    currentLtp = ltp;

    ltpEl.textContent = "₹" + ltp.toFixed(2);
    if (exitLtpIn) exitLtpIn.value = ltp.toFixed(2);

    if (currentState === "watching") {
      const buyMid = target1 ? (entry + target1) / 2 : null;
      if (buyMid && ltp > entry && ltp < buyMid) {
        setStateBanner("watching", `✅ Buy condition met — LTP ₹${ltp.toFixed(2)} in zone ₹${entry}–₹${buyMid.toFixed(2)}`);
      } else {
        const limit = buyMid ? `> ₹${entry.toFixed(2)} and < ₹${buyMid.toFixed(2)}` : `> ₹${entry.toFixed(2)}`;
        setStateBanner("watching", `Watching… LTP must be ${limit}`);
      }
    }

    if (currentState === "active" && buyPrice !== null) {
      const pnl = (ltp - buyPrice) * quantity;
      pnlEl.textContent    = (pnl >= 0 ? "+" : "") + "₹" + pnl.toFixed(0);
      pnlEl.className      = "metric-value " + (pnl >= 0 ? "text-success" : "text-danger");

      if (progressEl && entry && target1) {
        const pct = Math.max(0, Math.min(100, (ltp - entry) / (target1 - entry) * 100));
        progressEl.style.width = pct.toFixed(1) + "%";
      }
    }
  });

  // ── Trade state update from server ──────────────────────────────────────
  socket.on("trade_update", (data) => {
    currentState = data.state;

    if (data.state === "ordering") {
      setStateBanner("ordering", "Placing buy order…");
    } else if (data.state === "watching" && data.error) {
      setStateBanner("watching", `Order failed: ${data.error} — still watching.`);
    } else if (data.state === "active") {
      if (data.buy_price != null) {
        buyPrice = parseFloat(data.buy_price);
        buyPriceEl.textContent = "₹" + buyPrice.toFixed(2);
      }
      if (orderIdEl && data.order_id) orderIdEl.textContent = data.order_id;
      activeEl.classList.remove("d-none");
      setStateBanner("active", "Position OPEN — monitoring SL and target…");
    } else if (data.state === "exiting") {
      setStateBanner("exiting", `Exiting (${data.reason})… placing sell order.`);
    }
  });

  // ── Trade done ────────────────────────────────────────────────────────────
  socket.on("trade_done", (data) => {
    const pnl = data.pnl >= 0 ? `+₹${data.pnl.toFixed(0)}` : `-₹${Math.abs(data.pnl).toFixed(0)}`;
    const msg = `Trade closed (${data.reason}) — P&L: ${pnl}`;
    setStateBanner("exiting", msg);

    if (afterTradeUrl) {
      // Default: navigate away (e.g. home from /live page)
      setTimeout(() => { window.location.href = afterTradeUrl; }, 2500);
    } else {
      // Stay on page (activetrade) — clear the watching session so a refresh
      // shows "no active trade" instead of reinitialising the same instrument.
      fetch("/activetrade/clear", { method: "POST" }).catch(() => {});
    }
  });

  // ── Helper ───────────────────────────────────────────────────────────────
  function setStateBanner(state, text) {
    bannerEl.className = `alert mb-3 ${stateClass(state)}`;
    stateTxtEl.textContent = text;
  }

  function stateClass(s) {
    return { watching: "alert-info", ordering: "alert-warning",
             active: "alert-success", exiting: "alert-danger" }[s] || "alert-secondary";
  }
}
