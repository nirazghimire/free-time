// Hyperliquid SMC Terminal Frontend Logic
// Manages real-time WebSocket state, DOM rendering, and API actions.

let ws = null;
let currentSnapshot = null;

// DOM Elements
const envBadge = document.getElementById("envBadge");
const envText = document.getElementById("envText");
const modeBadge = document.getElementById("modeBadge");
const modeText = document.getElementById("modeText");
const autoTradeBtn = document.getElementById("autoTradeBtn");
const autoTradeText = document.getElementById("autoTradeText");
const scanBtn = document.getElementById("scanBtn");
const closeAllBtn = document.getElementById("closeAllBtn");

const accountEquity = document.getElementById("accountEquity");
const accountAddress = document.getElementById("accountAddress");
const marginUsed = document.getElementById("marginUsed");
const openPositionsCount = document.getElementById("openPositionsCount");
const totalPnl = document.getElementById("totalPnl");
const lastScanTime = document.getElementById("lastScanTime");
const mtfMatrixContainer = document.getElementById("mtfMatrixContainer");
const positionsTableBody = document.getElementById("positionsTableBody");
const positionsCountBadge = document.getElementById("positionsCountBadge");
const terminalLogs = document.getElementById("terminalLogs");
const clearLogsBtn = document.getElementById("clearLogsBtn");

function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  addLog("system", `Connecting to real-time feed at ${wsUrl}...`);
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    addLog("system", "Connected to Hyperliquid SMC Bot WebSocket.");
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleSocketMessage(msg);
    } catch (e) {
      console.error("Failed to parse WebSocket message:", e);
    }
  };

  ws.onclose = () => {
    addLog("error", "WebSocket disconnected. Reconnecting in 3s...");
    setTimeout(initWebSocket, 3000);
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
  };
}

function handleSocketMessage(msg) {
  if (msg.type === "INITIAL_STATE" || msg.type === "SCAN_COMPLETED") {
    currentSnapshot = msg.data;
    renderSnapshot(currentSnapshot);
  } else if (msg.type === "TRADE_EXECUTION") {
    const data = msg.data;
    addLog("trade", `🚀 [EXECUTION] ${data.setup.direction} on ${data.symbol} | Size: ${data.setup.size} | Entry: $${formatPrice(data.setup.entry_price)} | SL: $${formatPrice(data.setup.stop_loss)} | TP: $${formatPrice(data.setup.take_profit)}`);
    fetchSnapshot();
  } else if (msg.type === "CONFIG_CHANGED") {
    if (msg.data.auto_trade_enabled !== undefined) {
      updateAutoTradeButton(msg.data.auto_trade_enabled);
    }
    if (msg.data.mode !== undefined) {
      modeText.textContent = msg.data.mode + " MODE";
    }
  } else if (msg.type === "POSITIONS_CLOSED") {
    addLog("system", "Emergency closed all active positions.");
    fetchSnapshot();
  }
}

async function fetchSnapshot() {
  try {
    const res = await fetch("/api/snapshot");
    const data = await res.json();
    currentSnapshot = data;
    renderSnapshot(data);
  } catch (e) {
    console.error("Failed to fetch snapshot:", e);
  }
}

function renderSnapshot(data) {
  if (!data) return;

  // 1. Env & Mode Badges
  envText.textContent = (data.env || "TESTNET").toUpperCase();
  modeText.textContent = data.mode || "PAPER MODE";
  updateAutoTradeButton(data.auto_trade_enabled);

  // 2. Account Details
  const acc = data.account || {};
  accountEquity.textContent = `$${(acc.equity || 0).toFixed(2)}`;
  marginUsed.textContent = `$${(acc.margin_used || 0).toFixed(2)}`;
  if (acc.address && acc.address !== "Not Connected") {
    const shortAddr = acc.address.slice(0, 6) + "..." + acc.address.slice(-4);
    accountAddress.textContent = `Wallet: ${shortAddr}`;
  } else {
    accountAddress.textContent = "Wallet: Not Connected";
  }

  // 3. Open Positions & PnL
  const positions = data.open_positions || [];
  openPositionsCount.textContent = positions.length;
  positionsCountBadge.textContent = positions.length;

  let sumPnl = 0;
  positions.forEach(p => sumPnl += (p.pnl || 0));
  totalPnl.textContent = `${sumPnl >= 0 ? '+' : ''}$${sumPnl.toFixed(2)}`;
  totalPnl.className = `stat-value ${sumPnl > 0 ? 'text-bullish' : sumPnl < 0 ? 'text-bearish' : 'pnl-neutral'}`;

  renderPositionsTable(positions);

  // 4. Time
  lastScanTime.textContent = `Updated: ${new Date().toLocaleTimeString()}`;

  // 5. Render MTF Matrix
  renderMtfMatrix(data.symbols || {});
}

function renderMtfMatrix(symbols) {
  const container = mtfMatrixContainer;
  const symKeys = Object.keys(symbols);

  if (symKeys.length === 0) {
    container.innerHTML = `
      <div class="loading-placeholder">
        <div class="spinner"></div>
        <p>Waiting for first multi-timeframe candle scan...</p>
      </div>
    `;
    return;
  }

  container.innerHTML = "";

  symKeys.forEach(sym => {
    const asset = symbols[sym];
    const row = document.createElement("div");
    row.className = "asset-row";

    // Macro bias color
    const isBull = asset.macro_bias === "BULLISH";
    const isBear = asset.macro_bias === "BEARISH";
    const biasClass = isBull ? "pill-bullish" : isBear ? "pill-bearish" : "badge-info";

    // Setup state
    let setupHtml = "";
    if (asset.trade_setup) {
      setupHtml = `
        <div class="setup-pill setup-ready">
          ⚡ <strong>${asset.trade_setup.direction} ORDER READY</strong><br>
          Size: ${asset.trade_setup.size} ${sym} (~$${asset.trade_setup.notional})<br>
          SL: $${formatPrice(asset.trade_setup.sl)} &bull; TP: $${formatPrice(asset.trade_setup.tp)}<br>
          Risk: $${asset.trade_setup.risk_usd} &bull; R:R 1:${asset.trade_setup.rr}
        </div>
      `;
    } else if (asset.has_active_sweep) {
      const sw = asset.sweep_details;
      setupHtml = `
        <div class="setup-pill pill-sweep">
          🎯 <strong>15M ${sw.direction} Sweep Active</strong><br>
          Extreme Wick: $${formatPrice(sw.extreme)}<br>
          Watching 5M for Break of Structure candle close...
        </div>
      `;
    } else {
      setupHtml = `
        <div class="setup-pill setup-waiting">
          Scanning for 15M Liquidity Sweeps aligned with ${asset.macro_bias} bias.
        </div>
      `;
    }

    // Timeframe cards
    const tfList = ["1w", "1d", "4h", "1h", "15m", "5m"];
    let tfCardsHtml = "";
    tfList.forEach(tf => {
      const tfData = (asset.timeframes && asset.timeframes[tf]) || { bias: "NEUTRAL" };
      const tfBull = tfData.bias === "BULLISH";
      const tfBear = tfData.bias === "BEARISH";
      const cardClass = tfBull ? "tf-bullish" : tfBear ? "tf-bearish" : "";

      let tagsHtml = "";
      if (tfData.has_sweep) tagsHtml += `<span class="mini-tag pill-sweep">SWEEP</span>`;
      if (tfData.has_bos) tagsHtml += `<span class="mini-tag pill-bos">BOS</span>`;
      if (tfData.has_choch) tagsHtml += `<span class="mini-tag pill-bos">CHOCH</span>`;

      tfCardsHtml += `
        <div class="tf-card ${cardClass}">
          <div class="tf-name">${tf.toUpperCase()}</div>
          <div class="tf-status">${tfData.bias}</div>
          <div class="tf-tags">${tagsHtml || `<span style="opacity:0.3">&mdash;</span>`}</div>
        </div>
      `;
    });

    row.innerHTML = `
      <div class="asset-identity">
        <div class="asset-title">
          <span class="asset-symbol">${sym}</span>
          <span class="asset-bias-badge ${biasClass}">${asset.macro_bias}</span>
        </div>
        <div class="asset-price">$${formatPrice(asset.current_price)}</div>
        <div style="font-size:0.75rem; color:var(--text-dim);">
          Macro: <strong>${asset.macro_bias}</strong> &bull; Int: <strong>${asset.intermediate_trend}</strong>
        </div>
      </div>

      <div class="timeframe-boxes">
        ${tfCardsHtml}
      </div>

      <div class="setup-summary">
        ${setupHtml}
      </div>
    `;

    container.appendChild(row);
  });
}

function renderPositionsTable(positions) {
  const tbody = positionsTableBody;
  if (!positions || positions.length === 0) {
    tbody.innerHTML = `
      <tr class="empty-row">
        <td colspan="9">No active open positions. Watching for confirmed 15M sweep + 5M BOS setups.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = "";
  positions.forEach(pos => {
    const tr = document.createElement("tr");
    const isBull = pos.direction === "BULLISH";
    const sideClass = isBull ? "pill-bullish" : "pill-bearish";
    const pnl = pos.pnl || 0;
    const pnlColor = pnl > 0 ? "style='color: var(--bullish);'" : pnl < 0 ? "style='color: var(--bearish);'" : "";

    tr.innerHTML = `
      <td><strong>${pos.symbol}</strong></td>
      <td><span class="legend-pill ${sideClass}">${isBull ? 'LONG' : 'SHORT'}</span></td>
      <td>${pos.size}</td>
      <td>$${formatPrice(pos.entry_price)}</td>
      <td>$${formatPrice(currentSnapshot?.symbols[pos.symbol]?.current_price || pos.entry_price)}</td>
      <td style="color:#f43f5e;">$${formatPrice(pos.stop_loss)}</td>
      <td style="color:#10b981;">$${formatPrice(pos.take_profit)}</td>
      <td ${pnlColor}><strong>${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</strong></td>
      <td><span class="badge-live">${pos.status}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function updateAutoTradeButton(enabled) {
  if (enabled) {
    autoTradeBtn.className = "toggle-btn";
    autoTradeText.textContent = "AUTO-TRADE: ON";
  } else {
    autoTradeBtn.className = "toggle-btn off";
    autoTradeText.textContent = "AUTO-TRADE: OFF";
  }
}

function addLog(type, msg) {
  const time = new Date().toLocaleTimeString();
  const entry = document.createElement("div");
  entry.className = `log-entry log-${type}`;
  entry.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-msg">${msg}</span>`;
  terminalLogs.appendChild(entry);
  terminalLogs.scrollTop = terminalLogs.scrollHeight;
}

function formatPrice(val) {
  if (val === undefined || val === null) return "0.00";
  const num = Number(val);
  if (num > 100) return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (num > 1) return num.toFixed(3);
  return num.toFixed(5);
}

// Event Listeners
autoTradeBtn.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/toggle_autotrade", { method: "POST" });
    const data = await res.json();
    updateAutoTradeButton(data.auto_trade_enabled);
    addLog("system", `Auto execution toggled to: ${data.auto_trade_enabled ? 'ENABLED' : 'PAUSED'}`);
  } catch (e) {
    console.error(e);
  }
});

modeBadge.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/toggle_dryrun", { method: "POST" });
    const data = await res.json();
    modeText.textContent = data.mode + " MODE";
    addLog("system", `Trading mode toggled to: ${data.mode}`);
  } catch (e) {
    console.error(e);
  }
});

scanBtn.addEventListener("click", async () => {
  scanBtn.disabled = true;
  scanBtn.style.opacity = "0.6";
  addLog("system", "Triggering manual multi-timeframe scan across BTC, ETH, SOL...");
  try {
    const res = await fetch("/api/scan", { method: "POST" });
    const data = await res.json();
    if (data.snapshot) {
      renderSnapshot(data.snapshot);
      addLog("system", "Manual scan completed.");
    }
  } catch (e) {
    addLog("error", `Scan failed: ${e.message}`);
  } finally {
    scanBtn.disabled = false;
    scanBtn.style.opacity = "1";
  }
});

closeAllBtn.addEventListener("click", async () => {
  if (!confirm("Are you sure you want to emergency close all open positions?")) return;
  try {
    const res = await fetch("/api/close_all", { method: "POST" });
    const data = await res.json();
    addLog("system", `Emergency close executed: ${JSON.stringify(data.results)}`);
    fetchSnapshot();
  } catch (e) {
    addLog("error", `Emergency close failed: ${e.message}`);
  }
});

clearLogsBtn.addEventListener("click", () => {
  terminalLogs.innerHTML = "";
});

// Boot
window.addEventListener("DOMContentLoaded", () => {
  initWebSocket();
  fetchSnapshot();
});
