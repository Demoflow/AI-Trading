// WebSocket client for VWAP Stock Scalper Dashboard
let ws = null;
let reconnectTimer = null;

function connect() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${window.location.host}/ws`);

  ws.onopen = () => {
    console.log("Connected");
    clearTimeout(reconnectTimer);
    setStatus("connected");
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      render(data);
    } catch (e) {
      console.error("Parse error:", e);
    }
  };

  ws.onclose = () => {
    setStatus("disconnected");
    reconnectTimer = setTimeout(connect, 2000);
  };

  ws.onerror = () => {
    setStatus("error");
  };
}

function setStatus(state) {
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  dot.classList.remove("live", "idle", "error");
  if (state === "connected") {
    dot.classList.add("live");
    text.textContent = "LIVE";
  } else if (state === "disconnected") {
    text.textContent = "RECONNECTING...";
  } else {
    dot.classList.add("error");
    text.textContent = "ERROR";
  }
}

function fmtMoney(n) {
  if (n === null || n === undefined) return "$--";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtMoneySigned(n) {
  if (n === null || n === undefined) return "$--";
  const sign = n >= 0 ? "+" : "-";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(n) {
  if (n === null || n === undefined) return "--%";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

function colorClass(n) {
  if (n > 0) return "green";
  if (n < 0) return "red";
  return "";
}

function render(data) {
  if (data.error) {
    setStatus("error");
    document.getElementById("statusText").textContent = data.error;
    return;
  }

  // Timestamp
  const ts = new Date(data.timestamp);
  document.getElementById("timestamp").textContent = ts.toLocaleTimeString("en-US", { hour12: false });

  // Status
  const dot = document.getElementById("statusDot");
  if (data.status === "LIVE") {
    dot.className = "dot live";
    document.getElementById("statusText").textContent = "LIVE";
  } else {
    dot.className = "dot idle";
    document.getElementById("statusText").textContent = "IDLE";
  }

  // Summary
  const s = data.summary;
  document.getElementById("equity").textContent = fmtMoney(s.equity);
  document.getElementById("buyingPower").textContent = fmtMoney(s.buying_power);
  document.getElementById("deployed").textContent = fmtMoney(s.deployed);

  const todayEl = document.getElementById("todayPnl");
  todayEl.textContent = fmtMoneySigned(s.today_pnl);
  todayEl.className = "value " + colorClass(s.today_pnl);

  const openEl = document.getElementById("openPnl");
  openEl.textContent = fmtMoneySigned(s.open_pnl);
  openEl.className = "value " + colorClass(s.open_pnl);

  document.getElementById("tradeStats").textContent =
    `${s.trades} (${s.wins}W/${s.losses}L ${s.win_rate}%)`;

  // Open positions — stock format
  const posEl = document.getElementById("positions");
  document.getElementById("openCount").textContent = data.positions.length;

  if (data.positions.length === 0) {
    posEl.innerHTML = '<div class="empty">No open positions</div>';
  } else {
    posEl.innerHTML = data.positions.map(p => {
      const cls = p.pnl > 0 ? "win" : (p.pnl < 0 ? "loss" : "");
      const pnlCls = p.pnl > 0 ? "green" : (p.pnl < 0 ? "red" : "");
      const dir = p.direction || "LONG";
      const dirCls = dir === "LONG" ? "dir-long" : "dir-short";
      const vwapDist = p.vwap_distance_pct ? `VWAP: ${p.vwap_distance_pct > 0 ? "+" : ""}${p.vwap_distance_pct.toFixed(2)}%` : "";
      const stopDist = p.stop_price ? `Stop: $${p.stop_price.toFixed(2)}` : "";
      const tgt = p.target_1 ? `T1: $${p.target_1.toFixed(2)}` : "";
      return `
        <div class="position ${cls}">
          <div>
            <div class="pos-symbol">${p.symbol} <span class="${dirCls}">${dir}</span></div>
            <div class="pos-sub">${p.shares} shares @ $${(p.entry_price || 0).toFixed(2)} | ${(p.held_minutes || 0).toFixed(0)}min</div>
            <div class="pos-sub">${vwapDist} | ${stopDist} | ${tgt}</div>
          </div>
          <div>
            <div class="pos-sub">NOW</div>
            <div>$${(p.current_price || 0).toFixed(2)}</div>
          </div>
          <div>
            <div class="pos-sub">VALUE</div>
            <div>${fmtMoney(p.current_value)}</div>
          </div>
          <div class="pos-pnl ${pnlCls}">
            ${fmtMoneySigned(p.pnl)}
            <div class="pos-pnl-pct">${fmtPct(p.pnl_pct)}</div>
          </div>
        </div>
      `;
    }).join("");
  }

  // Closed today — stock format
  const closedEl = document.getElementById("closed");
  document.getElementById("closedCount").textContent = data.closed_today.length;

  if (data.closed_today.length === 0) {
    closedEl.innerHTML = '<div class="empty">No closed trades yet</div>';
  } else {
    closedEl.innerHTML = data.closed_today.map(t => {
      const isWin = (t.pnl || 0) > 0;
      const cls = isWin ? "win" : "loss";
      const pnlCls = isWin ? "green" : "red";
      const exitTime = t.exit_time ? new Date(t.exit_time).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" }) : "--";
      let duration = "--";
      try {
        if (t.entry_time && t.exit_time) {
          const mins = (new Date(t.exit_time) - new Date(t.entry_time)) / 60000;
          duration = `${mins.toFixed(1)}min`;
        }
      } catch (e) {}
      const dir = t.direction || "LONG";
      const reason = t.exit_reason || "";
      return `
        <div class="closed-item ${cls}">
          <div class="closed-time">${exitTime}</div>
          <div class="closed-result ${cls}">${isWin ? "WIN" : "LOSS"}</div>
          <div class="closed-symbol">${dir} ${t.symbol} ${t.shares || ""}sh ${reason}</div>
          <div class="closed-pnl ${pnlCls}">${fmtMoneySigned(t.pnl || 0)}</div>
          <div class="closed-duration">${duration}</div>
        </div>
      `;
    }).join("");
  }

  // Market
  if (data.market) {
    if (data.market.spy_price) {
      document.getElementById("spyPrice").textContent = `$${data.market.spy_price.toFixed(2)}`;
      const chgEl = document.getElementById("spyChange");
      const chg = data.market.spy_change_pct || 0;
      chgEl.textContent = fmtPct(chg);
      chgEl.className = "mkt-change " + colorClass(chg);
    }
    if (data.market.vix) {
      document.getElementById("vix").textContent = data.market.vix.toFixed(2);
    }
  }

  // Log
  const logEl = document.getElementById("log");
  logEl.innerHTML = data.log.map(line => {
    let cls = "log-line";
    if (line.includes("WARNING") || line.includes("WARN")) cls += " warning";
    else if (line.includes("ERROR")) cls += " error";
    else if (line.includes("Opened") || line.includes("EXIT") || line.includes("SIGNAL")) cls += " info";
    const stripped = line.replace(/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\|\s+\w+\s+\|\s+[^-]+-\s+/, "");
    return `<div class="${cls}">${escapeHtml(stripped)}</div>`;
  }).join("");
  logEl.scrollTop = logEl.scrollHeight;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// Start
connect();
