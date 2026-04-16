// Small Cap Dashboard — WebSocket client
let ws = null;
let reconnectTimer = null;

function connect() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${window.location.host}/ws`);
  ws.onopen    = () => { clearTimeout(reconnectTimer); setStatus("connected", null); };
  ws.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch(_) {} };
  ws.onclose   = () => { setStatus("disconnected", null); reconnectTimer = setTimeout(connect, 2000); };
  ws.onerror   = () => { setStatus("error", null); };
}

// ── Status dot ─────────────────────────────────────────────────────────────
function setStatus(conn, sessionStatus) {
  const dot  = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  dot.className = "dot";

  if (conn === "disconnected") {
    text.textContent = "● RECONNECTING...";
    return;
  }
  if (conn === "error") {
    dot.classList.add("error");
    text.textContent = "● ERROR";
    return;
  }
  // conn === "connected" — use session status
  const labels = {
    "LIVE":       ["live",       "● LIVE"],
    "PRE-MARKET": ["premarket",  "● PRE-MARKET"],
    "WATCHING":   ["watching",   "● WATCHING"],
    "HALTED":     ["halted",     "● HALTED"],
    "CLOSED":     ["idle",       "● CLOSED"],
    "OFFLINE":    ["idle",       "● OFFLINE"],
    "IDLE":       ["idle",       "● IDLE"],
  };
  const [cls, label] = labels[sessionStatus] || ["idle", `● ${sessionStatus || "IDLE"}`];
  dot.classList.add(cls);
  text.textContent = label;
}

// ── Formatters ──────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function fmt$(n)  { if (n == null) return "$--"; const s = n<0?"-":""; return `${s}$${Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}`; }
function fmtS$(n) { if (n == null) return "$--"; const s = n>=0?"+":"-"; return `${s}$${Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}`; }
function fmtPct(n){ if (n == null) return "--%"; return `${n>=0?"+":""}${n.toFixed(1)}%`; }
function cc(n)    { return n>0?"green":(n<0?"red":""); }
function esc(s)   { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }

function fmtTime(iso) {
  if (!iso) return "--:--";
  try {
    return new Date(iso).toLocaleTimeString("en-US",{hour12:false,hour:"2-digit",minute:"2-digit"});
  } catch(_) { return "--:--"; }
}

function stars(score) {
  if (score >= 50) return "★★★";
  if (score >= 25) return "★★";
  if (score >= 15) return "★";
  return "";
}

function fmtFloat(f) {
  if (!f) return "?";
  if (f >= 1e6) return (f/1e6).toFixed(1)+"M";
  if (f >= 1e3) return (f/1e3).toFixed(0)+"K";
  return String(f);
}

// ── Main render ──────────────────────────────────────────────────────────────
function render(data) {
  if (!data) return;

  // Timestamp + status
  const ts = new Date(data.timestamp);
  $("timestamp").textContent = ts.toLocaleTimeString("en-US",{hour12:false});
  setStatus("connected", data.status);

  // ── Summary cards ──────────────────────────────────────────────────────────
  const s = data.summary || {};

  const todayEl = $("todayPnl");
  todayEl.textContent = fmtS$(s.daily_pnl);
  todayEl.className   = "value " + cc(s.daily_pnl);

  const openEl = $("openPnl");
  openEl.textContent = fmtS$(s.open_pnl);
  openEl.className   = "value " + cc(s.open_pnl);

  const totalEl = $("totalPnl");
  totalEl.textContent = fmtS$(s.total_pnl);
  totalEl.className   = "value " + cc(s.total_pnl);

  $("tradeStats").textContent = s.trades_today != null
    ? `${s.trades_today} (${s.wins||0}W/${s.losses||0}L)` : "--";

  // Daily limit progress bar
  const risk = data.risk || {};
  const pct  = Math.min(risk.limit_used_pct || 0, 100);
  $("limitBar").textContent = `${fmt$(Math.abs(risk.daily_pnl||0))} / ${fmt$(risk.daily_limit||500)}`;
  $("limitBar").className   = "value" + (risk.halted?" red":"");
  const bar = $("limitProg");
  bar.style.width = pct + "%";
  bar.className   = "progress-bar" + (pct>=80?" red":(pct>=50?" amber":""));

  // Strike counter
  const strikes   = risk.consecutive_loss || 0;
  const maxStr    = risk.max_consecutive || 3;
  const strikeEl  = $("strikeDisplay");
  const dots      = "●".repeat(strikes) + "○".repeat(Math.max(0, maxStr - strikes));
  strikeEl.textContent = `${dots} ${strikes}/${maxStr}`;
  strikeEl.className   = "value " + (strikes === 0 ? "green" : strikes >= maxStr ? "red" : "amber");

  // ── Risk side panel ────────────────────────────────────────────────────────
  const rdp = $("rDailyPnl");
  rdp.textContent = fmtS$(risk.daily_pnl);
  rdp.className   = "risk-val " + cc(risk.daily_pnl);
  $("rLimitPct").textContent = `${(risk.limit_used_pct||0).toFixed(1)}%`;
  $("rStreak").textContent   = `${strikes} consecutive loss${strikes===1?"":"es"}`;
  const rstat = $("rStatus");
  if (risk.halted) {
    rstat.textContent = "HALTED";
    rstat.className   = "risk-val red";
  } else {
    rstat.textContent = data.status || "OK";
    rstat.className   = "risk-val green";
  }

  // ── Market context ─────────────────────────────────────────────────────────
  const mkt = data.market || {};
  if (mkt.spy_price) {
    $("spyPrice").textContent = `$${mkt.spy_price.toFixed(2)}`;
    const chgEl = $("spyChg");
    chgEl.textContent = fmtPct(mkt.spy_change_pct || 0);
    chgEl.className   = "mkt-chg " + cc(mkt.spy_change_pct || 0);
  }
  if (mkt.vix) $("vix").textContent = mkt.vix.toFixed(2);

  // ── Open positions ─────────────────────────────────────────────────────────
  const posEl = $("positions");
  $("openCount").textContent = (data.positions || []).length;
  if (!data.positions || data.positions.length === 0) {
    posEl.innerHTML = '<div class="empty">No open positions</div>';
  } else {
    posEl.innerHTML = data.positions.map(p => {
      const cls    = p.pnl > 0 ? "win" : (p.pnl < 0 ? "loss" : "");
      const pnlCls = cc(p.pnl);
      return `
        <div class="position ${cls}">
          <div>
            <div class="pos-sym">${esc(p.symbol)}</div>
            <div class="pos-sub">${p.shares} shares · entered ${fmt$(p.entry_price)}</div>
          </div>
          <div class="pos-field">
            <div class="lbl">CURRENT</div>
            <div class="val">${fmt$(p.current)}</div>
          </div>
          <div class="pos-field">
            <div class="lbl">MOVE</div>
            <div class="val ${pnlCls}">${fmtPct(p.pnl_pct)}</div>
          </div>
          <div class="pos-pnl">
            <div class="amt ${pnlCls}">${fmtS$(p.pnl)}</div>
            <div class="pct">${p.shares} × ${fmt$(p.current)}</div>
          </div>
        </div>`;
    }).join("");
  }

  // ── Gap candidates ─────────────────────────────────────────────────────────
  const candEl = $("candidates");
  $("candCount").textContent = (data.candidates || []).length;
  if (!data.candidates || data.candidates.length === 0) {
    candEl.innerHTML = '<div class="empty">Waiting for pre-market scan...</div>';
  } else {
    candEl.innerHTML = data.candidates.map((c, i) => {
      const catScore = c.catalyst_score || 0;
      return `
        <div class="candidate">
          <div>
            <div class="cand-sym">${esc(c.symbol)}</div>
            <div class="cand-rank">#${i+1}</div>
          </div>
          <div class="cand-field">
            <div class="lbl">GAP</div>
            <div class="val green">${fmtPct(c.gap_pct)}</div>
          </div>
          <div class="cand-field">
            <div class="lbl">PRICE</div>
            <div class="val">${fmt$(c.price)}</div>
          </div>
          <div class="cand-field">
            <div class="lbl">REL VOL</div>
            <div class="val">${c.rel_volume != null ? c.rel_volume+"x" : "n/a"}</div>
          </div>
          <div class="cand-field">
            <div class="lbl">FLOAT</div>
            <div class="val">${fmtFloat(c.float)}</div>
          </div>
          <div class="cand-field">
            <div class="lbl">VOL</div>
            <div class="val">${(c.volume||0).toLocaleString()}</div>
          </div>
          <div class="cand-score">
            <div class="${catScore>=25?"green":(catScore>=15?"amber":"")}">${catScore>0?"+":""}${catScore}</div>
            <div class="cand-stars">${stars(catScore)}</div>
          </div>
        </div>`;
    }).join("");
  }

  // ── Closed trades ──────────────────────────────────────────────────────────
  const closedEl = $("closed");
  $("closedCount").textContent = (data.closed_today || []).length;
  if (!data.closed_today || data.closed_today.length === 0) {
    closedEl.innerHTML = '<div class="empty">No closed trades yet</div>';
  } else {
    closedEl.innerHTML = data.closed_today.map(t => {
      const isWin  = t.pnl > 0;
      const cls    = isWin ? "win" : "loss";
      const pnlCls = cc(t.pnl);
      const reasonLabel = {
        "partial1":    "partial ①",
        "partial2":    "partial ②",
        "stop_hit":    "stop hit",
        "EOD_flatten": "EOD",
        "time_stop":   "time stop",
        "halt":        "halt",
        "EOD":         "EOD",
      }[t.reason] || (t.reason || "close");
      return `
        <div class="closed-item ${cls}">
          <span class="cl-time">${fmtTime(t.time)}</span>
          <span class="cl-sym">${esc(t.symbol)}</span>
          <span class="cl-reason">${esc(reasonLabel)}</span>
          <span class="cl-detail">${t.shares} @ ${fmt$(t.exit_price)}</span>
          <span class="cl-pnl ${pnlCls}">${fmtS$(t.pnl)}</span>
          <span class="cl-result ${cls}">${isWin?"WIN":"LOSS"}</span>
        </div>`;
    }).join("");
  }

  // ── Log ───────────────────────────────────────────────────────────────────
  const logEl = $("log");
  logEl.innerHTML = (data.log || []).map(line => {
    let cls = "log-line";
    const u = line.toUpperCase();
    if (u.includes("SELL") || u.includes("EXIT"))          cls += " trade";
    else if (u.includes("ENTRY"))                          cls += " entry";
    else if (u.includes("PATTERN") || u.includes("BULL")
          || u.includes("ABCD") || u.includes("ORB"))      cls += " pat";
    else if (u.includes("WARNING") || u.includes("WARN"))  cls += " warn";
    else if (u.includes("ERROR"))                          cls += " error";
    const stripped = line.replace(/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\|\s+\w+\s+\|\s+[^|]+-\s+/, "");
    return `<div class="${cls}">${esc(stripped)}</div>`;
  }).join("");
  logEl.scrollTop = logEl.scrollHeight;
}

connect();
