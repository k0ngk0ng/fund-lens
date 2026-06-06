"use strict";

const $ = (s) => document.querySelector(s);
const api = (path, opts) => fetch(path, Object.assign({ credentials: "same-origin" }, opts));

let es = null;                 // EventSource
let expanded = new Set();      // 展开重仓的基金 code
let prevPct = {};              // code -> 上次估算涨跌幅（用于闪烁）
let watchSet = new Set();      // 当前关注

// ---------- 启动 ----------
init();

async function init() {
  try {
    const r = await api("/api/me");
    if (r.ok) return enterApp();
  } catch (e) {}
  showLogin();
}

function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-err").textContent = "";
  const username = $("#username").value.trim();
  const password = $("#password").value;
  try {
    const r = await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      $("#login-err").textContent = d.detail || "登录失败";
      return;
    }
    enterApp();
  } catch (err) {
    $("#login-err").textContent = "网络错误";
  }
});

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  if (es) es.close();
  location.reload();
});

$("#edit-btn").addEventListener("click", openCatalog);
$("#modal-close").addEventListener("click", closeCatalog);

$("#add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#add-err").textContent = "";
  const code = $("#add-code").value.trim();
  if (!/^\d{6}$/.test(code)) {
    $("#add-err").textContent = "请输入 6 位基金代码";
    return;
  }
  try {
    const r = await api("/api/watchlist/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      $("#add-err").textContent = d.detail || "添加失败";
      return;
    }
    $("#add-code").value = "";
    await openCatalog();   // 重新加载目录（含新基金，且已勾选）
  } catch (err) {
    $("#add-err").textContent = "网络错误";
  }
});

// ---------- 主流程 ----------
async function enterApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  await loadWatchlist();
  await refreshOnce();
  connectStream();
}

async function loadWatchlist() {
  try {
    const r = await api("/api/watchlist");
    const d = await r.json();
    watchSet = new Set(d.codes || []);
  } catch (e) {}
}

async function refreshOnce() {
  try {
    const r = await api("/api/snapshot");
    if (r.ok) render(await r.json());
  } catch (e) {}
}

function connectStream() {
  if (es) es.close();
  es = new EventSource("/api/stream");
  es.addEventListener("snapshot", (ev) => {
    setConn(true);
    try { render(JSON.parse(ev.data)); } catch (e) {}
  });
  es.onerror = () => setConn(false);
}

function setConn(ok) {
  const dot = $("#market-dot");
  if (!ok) {
    dot.className = "dot err";
    $("#market-text").textContent = "重连中…";
  }
}

// ---------- 渲染 ----------
function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  const s = v > 0 ? "+" : "";
  return s + v.toFixed(2) + "%";
}
function cls(v) {
  if (v === null || v === undefined || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}

function render(data) {
  // 顶栏
  const dot = $("#market-dot");
  dot.className = "dot " + (data.market_open ? "open" : "closed");
  $("#market-text").textContent = data.market_open ? "交易中" : "已收盘";
  $("#updated").textContent = data.server_time ? "更新于 " + data.server_time : "";

  const funds = data.funds || [];
  const empty = $("#empty");
  if (funds.length === 0) {
    empty.classList.remove("hidden");
    $("#cards").innerHTML = "";
    return;
  }
  empty.classList.add("hidden");

  const cards = $("#cards");
  cards.innerHTML = "";
  for (const f of funds) cards.appendChild(renderCard(f));
}

function renderCard(f) {
  const card = document.createElement("div");
  card.className = "card";

  const changed = prevPct[f.code] !== undefined && prevPct[f.code] !== f.est_pct;
  if (changed) card.classList.add("flash");
  prevPct[f.code] = f.est_pct;

  const top = document.createElement("div");
  top.className = "card-top";
  top.innerHTML =
    `<div class="fund-name">${esc(f.name || f.code)}<span class="fund-code">${f.code}</span></div>` +
    `<div class="est-pct ${cls(f.est_pct)}">${fmtPct(f.est_pct)}</div>`;
  card.appendChild(top);

  const sub = document.createElement("div");
  sub.className = "card-sub";
  const estNav = f.est_nav != null ? f.est_nav.toFixed(4) : "—";
  const prevNav = f.prev_nav != null ? f.prev_nav.toFixed(4) : "—";
  sub.innerHTML =
    `<span>估算净值 <b>${estNav}</b></span>` +
    `<span>昨净值 <b>${prevNav}</b></span>` +
    `<span>官方估 <b class="${cls(f.official_pct)}">${fmtPct(f.official_pct)}</b></span>` +
    `<span>覆盖度 <b>${(f.coverage || 0).toFixed(1)}%</b></span>` +
    (f.report_date ? `<span>报告期 ${f.report_date}</span>` : "");
  card.appendChild(sub);

  if (f.holdings && f.holdings.length) {
    const btn = document.createElement("button");
    btn.className = "expand-btn";
    const isOpen = expanded.has(f.code);
    btn.textContent = isOpen ? "收起持仓 ▲" : "查看全部持仓 ▼";
    btn.onclick = () => {
      if (expanded.has(f.code)) expanded.delete(f.code);
      else expanded.add(f.code);
      refreshOnce();
    };
    card.appendChild(btn);

    if (isOpen) {
      const box = document.createElement("div");
      box.className = "holdings";
      for (const h of f.holdings) {
        const row = document.createElement("div");
        row.className = "hold-row";
        row.innerHTML =
          `<span class="h-name">${esc(h.name)}</span>` +
          `<span class="h-w">${(h.weight || 0).toFixed(2)}%</span>` +
          `<span class="h-c ${cls(h.change_pct)}">${fmtPct(h.change_pct)}</span>`;
        box.appendChild(row);
      }
      card.appendChild(box);
    }
  }
  return card;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---------- 关注管理 ----------
async function openCatalog() {
  $("#modal").classList.remove("hidden");
  const box = $("#catalog");
  box.innerHTML = "加载中…";
  try {
    const [cr, wr] = await Promise.all([api("/api/catalog"), api("/api/watchlist")]);
    const cat = (await cr.json()).funds || [];
    watchSet = new Set((await wr.json()).codes || []);
    box.innerHTML = "";
    for (const f of cat) {
      const row = document.createElement("label");
      row.className = "cat-row";
      const checked = watchSet.has(f.code) ? "checked" : "";
      row.innerHTML =
        `<span class="cat-info"><span class="cat-name">${esc(f.name || f.code)}</span>` +
        `<span class="cat-code">${f.code}</span></span>` +
        `<input type="checkbox" value="${f.code}" ${checked}/>`;
      row.querySelector("input").addEventListener("change", onToggle);
      box.appendChild(row);
    }
  } catch (e) {
    box.innerHTML = "加载失败";
  }
}

async function onToggle(e) {
  const code = e.target.value;
  if (e.target.checked) watchSet.add(code);
  else watchSet.delete(code);
  try {
    await api("/api/watchlist", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes: Array.from(watchSet) }),
    });
  } catch (err) {}
}

function closeCatalog() {
  $("#modal").classList.add("hidden");
  refreshOnce();
}
