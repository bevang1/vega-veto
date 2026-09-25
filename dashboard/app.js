/*
 * Signal Iris dashboard.
 *
 * Polls /api/state once a second and draws:
 *   - the iris: one bundle of strands per token, length = signal score,
 *     colour = who's winning (buyers white, sellers red), agent colour if held
 *   - the accordion: open positions + every buy/sell/block, explained
 *   - agents, readout, market flow, exit-reason pareto, P&L by agent
 *
 * Plain JavaScript and <canvas>, no libraries, so it works offline.
 */

const $ = (id) => document.getElementById(id);
const S = { state: null, agentFilter: "ALL", evFilter: "ALL", open: new Set(), seen: new Set(), firstLoad: true };
const DPR = () => window.devicePixelRatio || 1;

// ---------------------------------------------------------------- helpers
const fmt = {
  sol: (v) => (v == null ? "—" : v.toFixed(3)),
  pct: (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%"),
  usd: (v) => {
    if (v == null) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
    return "$" + v.toFixed(2);
  },
  price: (v) => (v == null ? "—" : "$" + (v < 0.01 ? v.toPrecision(3) : v.toFixed(4))),
  time: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
  dur: (min) => (min < 60 ? Math.round(min) + "m" : Math.floor(min / 60) + "h" + String(Math.round(min % 60)).padStart(2, "0")),
};
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Short text badge per agent: colour is never the only way to tell agents apart.
const code = (name) => { const w = (name || "").split(" "); return w.length > 1 ? w[0][0] + w[1][0] : w[0].slice(0, 2); };
const badge = (name, color) => `<span class="badge" style="background:${color}">${code(name)}</span>`;
const agentColor = (name) => (S.state?.agents.find((a) => a.name === name) || {}).color || "#fff";
const hash = (s) => { let h = 2166136261; for (const c of s) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967296; };

function sizeCanvas(c) {
  const r = c.getBoundingClientRect();
  const w = Math.max(1, Math.round(r.width * DPR())), h = Math.max(1, Math.round(r.height * DPR()));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  const ctx = c.getContext("2d");
  ctx.setTransform(DPR(), 0, 0, DPR(), 0, 0);
  return { ctx, w: r.width, h: r.height };
}

function showTip(el, html, x, y) {
  el.innerHTML = html;
  el.style.display = "block";
  const r = el.getBoundingClientRect();
  el.style.left = Math.min(x + 16, innerWidth - r.width - 8) + "px";
  el.style.top = Math.min(y + 16, innerHeight - r.height - 8) + "px";
}
const hideTip = (el) => (el.style.display = "none");

// ---------------------------------------------------------------- polling
async function poll() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    const st = await res.json();
    if (st && st.agents) { S.state = st; render(); }
  } catch (e) {
    $("meta").textContent = "DISCONNECTED · is run.py still running?";
  }
  setTimeout(poll, 1000);
}

async function control(action) {
  await fetch("/api/control", { method: "POST", body: JSON.stringify({ action }) });
}

// ---------------------------------------------------------------- render (data -> DOM)
function render() {
  const st = S.state;
  const tokens = st.tokens.length;
  $("meta").textContent =
    `FEED ${st.status.toUpperCase()} · TOKENS ${tokens} · TICK ${st.tick} · SOL ${fmt.usd(st.sol_usd)} · ` +
    `MARKET TIME ${fmt.dur(st.runtime_min)} · ${fmt.time(st.market_time)}`;
  const mb = $("modeBadge");
  mb.textContent = st.live ? "LIVE DATA · PAPER MONEY" : "SIMULATED MARKET · PAPER MONEY";
  mb.className = "mode " + (st.live ? "live" : "demo");
  $("pauseBtn").textContent = st.paused ? "RESUME" : "PAUSE";
  const T = st.total;
  $("strip").innerHTML =
    `DESK EQUITY <b>${fmt.sol(T.equity_sol)} SOL</b> <span class="${cls(T.pnl_pct)}">${fmt.pct(T.pnl_pct)}</span> · ` +
    `OPEN <b>${T.open}</b> · PENDING ORDERS <b>${T.pending}</b> · FEES PAID <b>${fmt.sol(T.fees_sol)} SOL</b> · ` +
    `COST MODEL DEX ${(st.costs.dex_fee * 100).toFixed(2)}% + ${st.costs.network_fee_sol} SOL/TX + AMM IMPACT + 1-TICK DELAY` +
    (st.paused ? ` · <b class="down">PAUSED: NO NEW ENTRIES</b>` : "");

  renderChips(st);
  renderAgents(st);
  renderPositions(st);
  renderEvents(st);
  renderReadout(st);
  spawnParticles(st);
  drawFlow(); drawPareto(); drawPnl();
  $("irisCount").textContent = tokens;
  S.firstLoad = false;
}

function renderChips(st) {
  const names = ["ALL", ...st.agents.map((a) => a.name)];
  $("agentChips").innerHTML = names.map((n) => {
    const a = st.agents.find((x) => x.name === n);
    return `<button class="chip ${S.agentFilter === n ? "on" : ""}" data-agent="${esc(n)}">` +
      (a ? `<i style="background:${a.color}"></i>${code(n)} · ` : "") + esc(n) + `</button>`;
  }).join("");
}

function renderAgents(st) {
  const T = st.total;
  $("heroEq").innerHTML = `${fmt.sol(T.equity_sol)} <span class="dim" style="font-size:13px">SOL</span>`;
  $("heroSub").innerHTML = `<span class="${cls(T.pnl_pct)}">${fmt.pct(T.pnl_pct)}</span> from ${fmt.sol(T.start_sol)} SOL · ≈ ${fmt.usd(T.equity_sol * st.sol_usd)}`;
  const box = $("agents");
  if (box.children.length !== st.agents.length) {
    box.innerHTML = st.agents.map((a, i) => `
      <div class="agent" data-agent="${esc(a.name)}">
        <div class="a-head"><span>${badge(a.name, a.color)} ${esc(a.name)}</span><span class="state" id="ag-state-${i}"></span></div>
        <div class="a-nums"><b id="ag-eq-${i}"></b><span id="ag-pnl-${i}"></span></div>
        <canvas id="ag-spark-${i}"></canvas>
        <div class="a-stats" id="ag-stats-${i}"></div>
        <div class="thesis">${esc(a.thesis)}</div>
      </div>`).join("");
  }
  st.agents.forEach((a, i) => {
    const el = box.children[i];
    el.classList.toggle("dimmed", S.agentFilter !== "ALL" && S.agentFilter !== a.name);
    $("ag-state-" + i).className = "state " + (a.killed ? "killed" : "armed");
    $("ag-state-" + i).textContent = a.killed ? "■ KILLED" : "● ARMED";
    $("ag-eq-" + i).textContent = fmt.sol(a.equity_sol) + " SOL";
    $("ag-pnl-" + i).innerHTML = `<span class="${cls(a.pnl_pct)}">${fmt.pct(a.pnl_pct)}</span>`;
    $("ag-stats-" + i).innerHTML =
      `<span>TRADES ${a.trades}</span><span>WIN ${a.win_rate == null ? "—" : Math.round(a.win_rate) + "%"}</span>` +
      `<span>OPEN ${a.open}</span><span>FEES ${a.fees_sol.toFixed(3)}</span>`;
    drawSpark($("ag-spark-" + i), a.equity_hist, a.color, 10);
  });
}

function drawSpark(c, data, color, base) {
  const { ctx, w, h } = sizeCanvas(c);
  ctx.clearRect(0, 0, w, h);
  if (!data || data.length < 2) return;
  const lo = Math.min(base, ...data), hi = Math.max(base, ...data), pad = 3;
  const y = (v) => h - pad - ((v - lo) / (hi - lo || 1)) * (h - 2 * pad);
  const x = (i) => (i / (data.length - 1)) * w;
  // Starting balance as a faint reference line: above it = profit.
  ctx.strokeStyle = "#243041"; ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, y(base)); ctx.lineTo(w, y(base)); ctx.stroke(); ctx.setLineDash([]);
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round";
  ctx.beginPath(); data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)))); ctx.stroke();
  ctx.fillStyle = color; ctx.beginPath(); ctx.arc(w - 2, y(data[data.length - 1]), 2.5, 0, 7); ctx.fill();
}

function agentOk(name) { return S.agentFilter === "ALL" || S.agentFilter === name; }

function renderPositions(st) {
  const list = st.positions.filter((p) => agentOk(p.agent)).sort((a, b) => b.pnl_pct - a.pnl_pct);
  $("posCount").textContent = list.length;
  $("positions").innerHTML = list.length ? list.map((p) => {
    const id = "p:" + p.agent + p.address;
    return `<div class="item ${S.open.has(id) ? "open" : ""}" data-id="${esc(id)}">
      <div class="item-head"><span class="t">${fmt.dur(p.minutes)}</span><span class="tag BUY">HOLD</span>
        <span class="sym">${esc(p.symbol)}${badge(p.agent, p.color)}</span>
        <span class="pos ${cls(p.pnl_pct)}">${fmt.pct(p.pnl_pct)}</span></div>
      <div class="item-body"><ul>
        <li>${esc(p.agent)} paid <b>${fmt.sol(p.cost_sol)} SOL</b>; selling right now would return <b>${fmt.sol(p.value_sol)} SOL</b> after fees and price impact.</li>
        <li>Entry ${fmt.price(p.entry_price)} → now ${fmt.price(p.price)}. Held ${fmt.dur(p.minutes)}.</li>
        <li>Exit rules are checked every tick; the first one to trigger sells it.</li>
      </ul>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">OPEN ON DEXSCREENER ↗</a>` : ""}</div></div>`;
  }).join("") : `<div class="empty">No open positions${S.agentFilter !== "ALL" ? " for this agent" : ""}. Agents buy when every item on their checklist passes and risk approves.</div>`;
}

function renderEvents(st) {
  const f = S.evFilter;
  const list = st.events.filter((e) => (f === "ALL" || e.kind === f || (f === "KILL" && (e.kind === "KILL" || e.kind === "INFO"))) && (!e.agent || agentOk(e.agent)));
  $("evCount").textContent = st.events.length;
  const html = list.map((e) => {
    const id = "e:" + e.id;
    const fresh = !S.firstLoad && !S.seen.has(e.id);
    const pnl = e.pnl_pct != null ? `<span class="pos ${cls(e.pnl_pct)}">${fmt.pct(e.pnl_pct)}</span>` :
      e.sol != null ? `<span class="dim">${fmt.sol(e.sol)} SOL</span>` : "<span></span>";
    const checks = e.checks && e.checks.length ? `<table class="checks">${e.checks.map((c) =>
      `<tr><td>${esc(c.label)}</td><td>${Number(c.value).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td><td>${esc(c.need)}</td><td class="${c.passed ? "ok" : "no"}">${c.passed ? "✓" : "✗"}</td></tr>`).join("")}</table>` : "";
    return `<div class="item ${S.open.has(id) ? "open" : ""} ${fresh ? "flash" : ""}" data-id="${esc(id)}">
      <div class="item-head"><span class="t">${fmt.time(e.t)}</span><span class="tag ${e.kind}">${e.kind}</span>
        <span class="sym">${esc(e.symbol || e.headline)}${e.agent ? badge(e.agent, e.color) : ""}</span>${pnl}</div>
      <div class="item-body"><div style="margin-top:6px"><b>${esc(e.headline)}</b></div>
        <ul>${(e.explain || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>${checks}
        ${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">OPEN ON DEXSCREENER ↗</a>` : ""}</div></div>`;
  }).join("");
  const box = $("events");
  if (box.dataset.html !== html) { box.innerHTML = html || `<div class="empty">Nothing yet. Agents are scanning; the first trades usually appear within a few minutes.</div>`; box.dataset.html = html; }
  st.events.forEach((e) => S.seen.add(e.id));
}

function renderReadout(st) {
  const hot = st.tokens.filter((t) => t.score >= 60).length;
  const rows = [
    ["FEED", st.live ? "LIVE · DEXSCREENER" : "SIMULATED"], ["SOL PRICE", fmt.usd(st.sol_usd)],
    ["TOKENS TRACKED", st.tokens.length], ["HOT (SCORE ≥ 60)", hot],
    ["OPEN POSITIONS", st.total.open], ["PENDING ORDERS", st.total.pending],
    ["TOTAL FEES", fmt.sol(st.total.fees_sol) + " SOL"], ["TICK", st.tick],
    ["MARKET TIME RUN", fmt.dur(st.runtime_min)], ["UPTIME", fmt.dur(st.uptime_s / 60)],
  ];
  $("readout").innerHTML = rows.map(([k, v]) => `<div>${k}</div><div>${esc(v)}</div>`).join("");
}

// ---------------------------------------------------------------- the iris
const iris = { c: $("iris"), spin: 0, strands: new Map(), particles: [], hover: null, geom: null };

function tokenColor(t) {
  // Diverging: sellers red <- neutral grey -> buyers cool white.
  const r = Math.log(Math.max(t.buy_ratio, 0.05)) / Math.log(2.5);   // -1..1 around a 2.5x ratio
  const k = Math.max(-1, Math.min(1, r));
  const mix = (a, b, u) => a.map((v, i) => Math.round(v + (b[i] - v) * u));
  const neutral = [125, 135, 148], buy = [230, 243, 255], sell = [208, 59, 59];
  return k >= 0 ? mix(neutral, buy, k) : mix(neutral, sell, -k);
}

function syncStrands() {
  const st = S.state; if (!st) return;
  const live = new Set();
  // Spread tokens evenly around the ring (ordered by a stable hash of the
  // address), so the iris is full like a real one. Each token's bundle
  // eases toward its slot, so tokens arriving or leaving don't make it jump.
  const order = [...st.tokens].sort((a, b) => hash(a.address) - hash(b.address));
  const slot = new Map(order.map((t, i) => [t.address, (i / Math.max(order.length, 1)) * Math.PI * 2]));
  const width = (Math.PI * 2) / Math.max(order.length, 1);
  for (const t of st.tokens) {
    live.add(t.address);
    let s = iris.strands.get(t.address);
    if (!s) {
      const seed = hash(t.address);
      s = { angle: slot.get(t.address), len: 0, seed, fibres: [] };
      for (let i = 0; i < 34; i++) s.fibres.push({ u: hash(t.address + i) - 0.5, lf: 0.45 + hash(i + t.address) * 0.55, ph: hash("p" + i + t.address) * 6.28 });
      iris.strands.set(t.address, s);
    }
    s.t = t;
    s.slot = slot.get(t.address);
    s.width = width;
    // A floor keeps the iris full; the score decides how far each bundle reaches.
    s.target = 0.22 + 0.78 * (t.score / 100);
    s.dim = S.agentFilter !== "ALL" && !t.held.includes(agentColor(S.agentFilter));
  }
  for (const [a, s] of iris.strands) if (!live.has(a)) { s.target = 0; s.dying = true; if (s.len < 0.01) iris.strands.delete(a); }
}

function spawnParticles(st) {
  if (S.firstLoad) return;
  for (const e of st.events) {
    if (e._spawned || (e.kind !== "BUY" && e.kind !== "SELL")) continue;
    if (S.spawned?.has(e.id)) continue;
    (S.spawned ||= new Set()).add(e.id);
    const s = iris.strands.get(e.address);
    const angle = s ? s.angle : Math.random() * 6.28;
    const color = e.kind === "BUY" ? e.color : (e.pnl_pct >= 0 ? "#3fd13f" : "#ff6b6b");
    for (let i = 0; i < 70; i++) {
      iris.particles.push({ a: angle + (Math.random() - 0.5) * 0.25, r: e.kind === "BUY" ? 0 : 1, v: (0.004 + Math.random() * 0.012) * (e.kind === "BUY" ? 1 : -1), life: 1, color });
    }
  }
}

function drawIris(ts) {
  const { ctx, w, h } = sizeCanvas(iris.c);
  ctx.clearRect(0, 0, w, h);
  const cx = w * 0.5, cy = h * 0.52, R = Math.min(w * 0.5, h * 0.4), r0 = R * 0.24;
  iris.geom = { cx, cy, R, r0 };
  iris.spin += 0.0006;

  // Axis ticks on the left edge, like an instrument.
  ctx.fillStyle = "#3b4757"; ctx.font = "10px ui-monospace, Menlo, monospace";
  for (let d = 0; d <= 360; d += 45) {
    const y = 40 + (d / 360) * (h - 80);
    ctx.fillText(`${d}°`, 10, y);
    ctx.fillRect(38, y - 3, 6, 1);
  }
  ctx.fillRect(44, 40 - 3, 1, h - 80);

  // Faint guide rings: 25/50/75/100 signal score.
  ctx.strokeStyle = "rgba(127,212,255,0.06)"; ctx.lineWidth = 1;
  for (const q of [0.25, 0.5, 0.75, 1]) { ctx.beginPath(); ctx.arc(cx, cy, r0 + (R - r0) * q, 0, 6.29); ctx.stroke(); }

  syncStrands();
  const hov = iris.hover;
  const labels = [];
  // Sort so held/hovered tokens draw on top.
  const list = [...iris.strands.values()].sort((a, b) => (a.t?.held.length || 0) - (b.t?.held.length || 0));
  for (const s of list) {
    s.len += ((s.target || 0) - s.len) * 0.06;
    if (s.slot != null) {
      // Ease the angle toward its slot the short way round the circle.
      const d = ((s.slot - s.angle + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
      s.angle += d * 0.05;
    }
    const t = s.t; if (!t) continue;
    const held = t.held.length > 0;
    const isHover = hov === t.address;
    const base = held ? t.held[0] : `rgb(${tokenColor(t).join(",")})`;
    const alpha = s.dim ? 0.05 : isHover ? 1 : held ? 0.85 : 0.12 + 0.5 * s.len * s.len;
    ctx.strokeStyle = base; ctx.fillStyle = base; ctx.globalAlpha = alpha;
    ctx.lineWidth = held || isHover ? 1 : 0.55;
    const a0 = s.angle + iris.spin;
    for (const f of s.fibres) {
      const breathe = 1 + 0.035 * Math.sin(ts / 900 + f.ph);
      const len = (R - r0) * s.len * f.lf * breathe;
      const a = a0 + f.u * (s.width || 0.1) * 1.15;
      const x1 = cx + Math.cos(a) * r0, y1 = cy + Math.sin(a) * r0 * 1.18;
      const x2 = cx + Math.cos(a) * (r0 + len), y2 = cy + Math.sin(a) * (r0 + len) * 1.18;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.beginPath(); ctx.arc(x2, y2, held ? 1.5 : 0.9, 0, 6.29); ctx.fill();
    }
    if (held || isHover || t.score >= 75) {
      const tipR = r0 + (R - r0) * s.len + 14;
      labels.push({ x: cx + Math.cos(a0) * tipR, y: cy + Math.sin(a0) * tipR * 1.18, t, held, a: a0 });
    }
  }
  ctx.globalAlpha = 1;

  // Particles: bursts outward on buys, inward on sells.
  iris.particles = iris.particles.filter((p) => p.life > 0);
  for (const p of iris.particles) {
    p.r += p.v; p.life -= 0.012;
    if (p.r < 0 || p.r > 1.15) p.life = 0;
    const rr = r0 + (R - r0) * p.r;
    ctx.globalAlpha = Math.max(0, p.life); ctx.fillStyle = p.color;
    ctx.beginPath(); ctx.arc(cx + Math.cos(p.a + iris.spin) * rr, cy + Math.sin(p.a + iris.spin) * rr * 1.18, 1.8, 0, 6.29); ctx.fill();
  }
  ctx.globalAlpha = 1;

  // The pupil: a soft black core with a faint rim.
  const g = ctx.createRadialGradient(cx, cy, r0 * 0.2, cx, cy, r0 * 1.05);
  g.addColorStop(0, "#000"); g.addColorStop(0.85, "#020305"); g.addColorStop(1, "rgba(2,3,5,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.ellipse(cx, cy, r0 * 1.05, r0 * 1.24, 0, 0, 6.29); ctx.fill();
  const st = S.state;
  if (st) {
    ctx.textAlign = "center"; ctx.fillStyle = "#5f6f82"; ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.fillText("DESK P&L", cx, cy - 12);
    ctx.font = "bold 20px ui-monospace, Menlo, monospace";
    ctx.fillStyle = st.total.pnl_pct >= 0 ? "#3fd13f" : "#ff6b6b";
    ctx.fillText(fmt.pct(st.total.pnl_pct), cx, cy + 10);
    ctx.font = "10px ui-monospace, Menlo, monospace"; ctx.fillStyle = "#5f6f82";
    ctx.fillText(`${st.total.open} OPEN · ${st.paused ? "PAUSED" : "SCANNING"}`, cx, cy + 26);
    ctx.textAlign = "left";
  }

  // Direct labels for held / very hot / hovered tokens only (selective, not every strand).
  ctx.font = "10px ui-monospace, Menlo, monospace";
  // Greedy placement: held tokens first, then hottest; skip any label that
  // would overlap one already drawn.
  labels.sort((a, b) => (b.held - a.held) || (b.t.score - a.t.score));
  const placed = [];
  for (const L of labels) {
    const tw = ctx.measureText(L.t.symbol + "  ◆ 9").width, right = Math.cos(L.a) >= 0;
    const box = { x0: right ? L.x : L.x - tw, x1: right ? L.x + tw : L.x, y0: L.y - 10, y1: L.y + 3 };
    if (placed.some((p) => box.x0 < p.x1 && box.x1 > p.x0 && box.y0 < p.y1 && box.y1 > p.y0)) continue;
    placed.push(box);
    ctx.textAlign = right ? "left" : "right";
    ctx.fillStyle = L.held ? "#dbe6f2" : "#9fb0c3";
    ctx.fillText(L.t.symbol + (L.held ? "  ◆ " + L.t.held.length : ""), L.x, L.y);
  }
  ctx.textAlign = "left";
  requestAnimationFrame(drawIris);
}

iris.c.addEventListener("mousemove", (ev) => {
  const g = iris.geom; if (!g || !S.state) return;
  const rect = iris.c.getBoundingClientRect();
  const x = ev.clientX - rect.left - g.cx, y = (ev.clientY - rect.top - g.cy) / 1.18;
  const dist = Math.hypot(x, y);
  if (dist < g.r0 * 0.8 || dist > g.R * 1.1) { iris.hover = null; hideTip($("tip")); return; }
  const ang = Math.atan2(y, x);
  let best = null, bestD = 0.08;
  for (const s of iris.strands.values()) {
    if (!s.t || s.dying) continue;
    let d = Math.abs(((ang - (s.angle + iris.spin)) % 6.283 + 9.42) % 6.283 - 3.14);
    if (d < bestD) { bestD = d; best = s.t; }
  }
  iris.hover = best?.address || null;
  if (!best) { hideTip($("tip")); return; }
  const heldBy = S.state.positions.filter((p) => p.address === best.address).map((p) => badge(p.agent, p.color) + " " + esc(p.agent)).join("<br>");
  showTip($("tip"), `<div class="tt">${esc(best.symbol)}</div><div class="kv">
    <span>SIGNAL SCORE</span><span>${best.score}</span>
    <span>5M / 1H</span><span><span class="${cls(best.chg_m5)}">${fmt.pct(best.chg_m5)}</span> / <span class="${cls(best.chg_h1)}">${fmt.pct(best.chg_h1)}</span></span>
    <span>BUY/SELL 5M</span><span>${best.buy_ratio}x</span>
    <span>VOL ACCEL</span><span>${best.vol_accel}x</span>
    <span>LIQUIDITY</span><span>${fmt.usd(best.liq)}</span>
    <span>VOLUME 5M</span><span>${fmt.usd(best.vol_m5)}</span>
    <span>TXNS 5M</span><span>${best.txns_m5}</span>
    <span>PAIR AGE</span><span>${best.age_min == null ? "—" : fmt.dur(best.age_min)}</span></div>
    ${heldBy ? `<div style="margin-top:6px" class="dim">HELD BY</div>${heldBy}` : ""}`, ev.clientX, ev.clientY);
});
iris.c.addEventListener("mouseleave", () => { iris.hover = null; hideTip($("tip")); });
iris.c.addEventListener("click", () => { const t = S.state?.tokens.find((x) => x.address === iris.hover); if (t?.url) window.open(t.url, "_blank", "noopener"); });

// ---------------------------------------------------------------- bottom charts
const hovers = {};  // chart id -> index under the mouse

function axisText(ctx, text, x, y, align = "left") {
  ctx.fillStyle = "#5f6f82"; ctx.font = "10px ui-monospace, Menlo, monospace"; ctx.textAlign = align; ctx.fillText(text, x, y); ctx.textAlign = "left";
}

function drawFlow() {
  const c = $("flow"), { ctx, w, h } = sizeCanvas(c);
  ctx.clearRect(0, 0, w, h);
  const data = S.state?.flow || [];
  if (data.length < 2) { axisText(ctx, "collecting market flow…", 8, h / 2); return; }
  // Net flow = buy volume minus sell volume. Total buys and sells are always
  // close, so plotting them separately gives two flat lines; the DIFFERENCE is
  // what shows whether the market is being bought or sold right now.
  const net = data.map(([b, s]) => b - s);
  const mid = h / 2, max = Math.max(1, ...net.map(Math.abs));
  const L = 64, x = (i) => L + (i / (data.length - 1)) * (w - L - 8);
  const y = (v) => mid - (v / max) * (mid - 10);
  // Fill above zero in cool white (net buying), below zero in red (net selling).
  for (const [sign, fill] of [[1, "rgba(230,243,255,0.16)"], [-1, "rgba(208,59,59,0.22)"]]) {
    ctx.beginPath(); ctx.moveTo(x(0), mid);
    net.forEach((v, i) => ctx.lineTo(x(i), sign * v > 0 ? y(v) : mid));
    ctx.lineTo(x(net.length - 1), mid); ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  }
  ctx.beginPath(); net.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = "#dbe6f2"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  ctx.fillStyle = "#243041"; ctx.fillRect(L, mid, w - L - 8, 1);
  axisText(ctx, "NET BUY", 4, 14); axisText(ctx, "+" + fmt.usd(max), 4, 26);
  axisText(ctx, "0", 4, mid + 3);
  axisText(ctx, "NET SELL", 4, h - 18); axisText(ctx, "-" + fmt.usd(max), 4, h - 6);
  const [b, s] = data[data.length - 1];
  $("flowNow").innerHTML = `NOW <span class="${cls(b - s)}">${b >= s ? "NET BUYING" : "NET SELLING"} ${fmt.usd(Math.abs(b - s))}</span> · ${((b / (b + s || 1)) * 100).toFixed(0)}% BUYS`;
  const hi = hovers.flow;
  if (hi != null && data[hi]) {
    ctx.strokeStyle = "#7fd4ff"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x(hi), 4); ctx.lineTo(x(hi), h - 4); ctx.stroke();
    ctx.fillStyle = net[hi] >= 0 ? "#e6f3ff" : "#d03b3b"; ctx.beginPath(); ctx.arc(x(hi), y(net[hi]), 4, 0, 6.29); ctx.fill();
    ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 2; ctx.stroke();
  }
}

function drawPareto() {
  const c = $("pareto"), { ctx, w, h } = sizeCanvas(c);
  ctx.clearRect(0, 0, w, h);
  const data = S.state?.exit_reasons || [];
  const total = data.reduce((a, [, n]) => a + n, 0);
  $("exitTotal").textContent = total ? `${total} EXITS` : "";
  if (!total) { axisText(ctx, "no exits yet", 8, h / 2); return; }
  const n = data.length, gap = 2, left = 8, bottom = 22, top = 18;
  const SHORT = { "STOP LOSS": "STOP", "TAKE PROFIT": "TARGET", "TRAILING STOP": "TRAIL", "TIME STOP": "TIME",
                  "RUG ALERT": "RUG", "RUG / DELISTED": "DELISTED", "KILL SWITCH": "KILL" };
  const bw = (w - left * 2) / n - gap, max = data[0][1];
  let cum = 0; const pts = [];
  data.forEach(([label, v], i) => {
    const bx = left + i * (bw + gap), bh = (v / max) * (h - bottom - top);
    const good = label.startsWith("TAKE") || label.startsWith("TRAIL");
    ctx.fillStyle = hovers.pareto === i ? "#7fd4ff" : good ? "#2a5a78" : "#3a4658";
    roundTop(ctx, bx, h - bottom - bh, bw, bh, 4);
    axisText(ctx, SHORT[label] || label.slice(0, 8), bx + bw / 2, h - bottom + 13, "center");
    axisText(ctx, v, bx + bw / 2, h - bottom - bh - 4, "center");
    cum += v; pts.push([bx + bw / 2, top + (1 - cum / total) * (h - bottom - top)]);
  });
  ctx.strokeStyle = "#dbe6f2"; ctx.lineWidth = 2; ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke();
  for (const [x, y] of pts) { ctx.fillStyle = "#dbe6f2"; ctx.beginPath(); ctx.arc(x, y, 4, 0, 6.29); ctx.fill(); ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 2; ctx.stroke(); }
  c._bars = data.map((d, i) => ({ x0: left + i * (bw + gap), x1: left + i * (bw + gap) + bw, d, cum: pts[i] }));
  c._total = total;
}

function roundTop(ctx, x, y, w, h, r) {
  r = Math.min(r, h, w / 2);
  ctx.beginPath(); ctx.moveTo(x, y + h); ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r); ctx.lineTo(x + w, y + h); ctx.closePath(); ctx.fill();
}

function drawPnl() {
  const c = $("pnl"), { ctx, w, h } = sizeCanvas(c);
  ctx.clearRect(0, 0, w, h);
  const agents = S.state?.agents || [];
  if (!agents.length) return;
  const max = Math.max(0.05, ...agents.map((a) => Math.abs(a.pnl_sol)));
  // Label column on the left; bars grow either way from a zero line in the
  // middle of the remaining space, with room left for the value text.
  const labelW = 132, half = (w - labelW - 16) / 2 - 52, mid = labelW + 8 + (w - labelW - 16) / 2;
  const row = (h - 10) / agents.length;
  ctx.fillStyle = "#243041"; ctx.fillRect(mid, 4, 1, h - 8);
  agents.forEach((a, i) => {
    const y = 6 + i * row, bh = Math.min(16, row - 8), len = (Math.abs(a.pnl_sol) / max) * half;
    const x0 = a.pnl_sol >= 0 ? mid + 1 : mid - len;
    ctx.fillStyle = a.color; ctx.globalAlpha = hovers.pnl === i ? 1 : 0.85;
    ctx.fillRect(x0, y + (row - bh) / 2, Math.max(len, 1), bh);
    ctx.globalAlpha = 1;
    axisText(ctx, code(a.name) + " · " + a.name, 4, y + row / 2 + 4);
    ctx.fillStyle = a.pnl_sol >= 0 ? "#3fd13f" : "#ff6b6b"; ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.textAlign = a.pnl_sol >= 0 ? "left" : "right";
    ctx.fillText((a.pnl_sol >= 0 ? "+" : "") + a.pnl_sol.toFixed(3), a.pnl_sol >= 0 ? mid + len + 6 : mid - len - 6, y + row / 2 + 4);
    ctx.textAlign = "left";
  });
  c._rows = agents.map((a, i) => ({ y0: 6 + i * row, y1: 6 + (i + 1) * row, a }));
}

// Chart hover layer: crosshair on the flow chart, per-bar tooltips elsewhere.
function chartHover(id, fn) {
  const c = $(id);
  c.addEventListener("mousemove", (ev) => {
    const r = c.getBoundingClientRect(); const res = fn(c, ev.clientX - r.left, ev.clientY - r.top, r);
    if (res) { hovers[id] = res.i; showTip($("chartTip"), res.html, ev.clientX, ev.clientY); } else { hovers[id] = null; hideTip($("chartTip")); }
  });
  c.addEventListener("mouseleave", () => { hovers[id] = null; hideTip($("chartTip")); });
}
chartHover("flow", (c, x, y, r) => {
  const data = S.state?.flow || []; if (data.length < 2) return null;
  const i = Math.round(((x - 64) / (r.width - 72)) * (data.length - 1));
  if (i < 0 || i >= data.length) return null;
  const [b, s] = data[i];
  return { i, html: `<div class="kv"><span>NET</span><span class="${cls(b - s)}">${b >= s ? "+" : "-"}${fmt.usd(Math.abs(b - s))}</span><span>BUY VOL 5M</span><span class="up">${fmt.usd(b)}</span><span>SELL VOL 5M</span><span class="down">${fmt.usd(s)}</span><span>BUY SHARE</span><span>${((b / (b + s || 1)) * 100).toFixed(0)}%</span><span>TICKS AGO</span><span>${data.length - 1 - i}</span></div>` };
});
chartHover("pareto", (c, x) => {
  const bar = (c._bars || []).find((b) => x >= b.x0 - 1 && x <= b.x1 + 1); if (!bar) return null;
  return { i: c._bars.indexOf(bar), html: `<div class="tt">${esc(bar.d[0])}</div><div class="kv"><span>EXITS</span><span>${bar.d[1]}</span><span>SHARE</span><span>${((bar.d[1] / c._total) * 100).toFixed(0)}%</span></div>` };
});
chartHover("pnl", (c, x, y) => {
  const row = (c._rows || []).find((r) => y >= r.y0 && y < r.y1); if (!row) return null;
  const a = row.a;
  return { i: c._rows.indexOf(row), html: `<div class="tt">${badge(a.name, a.color)} ${esc(a.name)}</div><div class="kv"><span>TOTAL P&amp;L</span><span class="${cls(a.pnl_sol)}">${a.pnl_sol.toFixed(4)} SOL</span><span>REALISED</span><span>${a.realized_sol.toFixed(4)} SOL</span><span>FEES PAID</span><span>${a.fees_sol.toFixed(4)} SOL</span><span>WIN RATE</span><span>${a.win_rate == null ? "—" : Math.round(a.win_rate) + "%"}</span></div>` };
});

// ---------------------------------------------------------------- interactions
document.addEventListener("click", (ev) => {
  const head = ev.target.closest(".acc-head");
  if (head) { head.parentElement.classList.toggle("open"); return; }
  const itemHead = ev.target.closest(".item-head");
  if (itemHead) {
    const item = itemHead.parentElement, id = item.dataset.id;
    item.classList.toggle("open");
    item.classList.contains("open") ? S.open.add(id) : S.open.delete(id);
    $("events").dataset.html = "";   // force the next render to keep this state
    return;
  }
  const chip = ev.target.closest("[data-agent]");
  if (chip && (chip.classList.contains("chip") || chip.classList.contains("agent"))) {
    const n = chip.dataset.agent;
    S.agentFilter = S.agentFilter === n && n !== "ALL" ? "ALL" : n;
    $("events").dataset.html = ""; render(); return;
  }
  const pill = ev.target.closest("#evFilter button");
  if (pill) {
    S.evFilter = pill.dataset.f;
    document.querySelectorAll("#evFilter button").forEach((b) => b.classList.toggle("on", b === pill));
    render();
  }
});
$("pauseBtn").addEventListener("click", () => control(S.state?.paused ? "resume" : "pause"));
$("killBtn").addEventListener("click", () => {
  if (confirm("KILL ALL: sell every position and stop all agents until you restart the desk?")) control("kill_all");
});
window.addEventListener("resize", () => S.state && render());

poll();
requestAnimationFrame(drawIris);
