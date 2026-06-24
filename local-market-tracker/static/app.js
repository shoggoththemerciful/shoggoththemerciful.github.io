const state = {
  searchResults: [],
  watchlist: [],
  selectedKey: "",
  selectedSide: "yes",
  selectedRange: "7d",
  history: [],
  stream: null
};

const els = {
  statusLine: document.getElementById("statusLine"),
  liveBadge: document.getElementById("liveBadge"),
  venueSelect: document.getElementById("venueSelect"),
  searchForm: document.getElementById("searchForm"),
  searchInput: document.getElementById("searchInput"),
  resultList: document.getElementById("resultList"),
  resultCount: document.getElementById("resultCount"),
  watchList: document.getElementById("watchList"),
  watchCount: document.getElementById("watchCount"),
  detailVenue: document.getElementById("detailVenue"),
  detailTitle: document.getElementById("detailTitle"),
  detailMeta: document.getElementById("detailMeta"),
  sideGroup: document.getElementById("sideGroup"),
  rangeGroup: document.getElementById("rangeGroup"),
  chart: document.getElementById("priceChart"),
  chartEmpty: document.getElementById("chartEmpty"),
  metricsGrid: document.getElementById("metricsGrid")
};

els.searchForm.addEventListener("submit", handleSearch);
els.resultList.addEventListener("click", handleResultClick);
els.watchList.addEventListener("click", handleWatchClick);
els.sideGroup.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-side]");
  if (!button) return;
  state.selectedSide = button.dataset.side;
  setActive(els.sideGroup, button);
  loadHistory();
});
els.rangeGroup.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-range]");
  if (!button) return;
  state.selectedRange = button.dataset.range;
  setActive(els.rangeGroup, button);
  loadHistory();
});
window.addEventListener("resize", () => drawChart());

boot();

async function boot() {
  await loadWatchlist();
  connectStream();
}

async function handleSearch(event) {
  event.preventDefault();
  const q = els.searchInput.value.trim();
  if (!q) return;
  setStatus("Searching...");
  els.resultList.innerHTML = '<div class="empty">Searching markets...</div>';
  try {
    const data = await fetchJson("/api/search?" + new URLSearchParams({ venue: els.venueSelect.value, q }));
    state.searchResults = data.results || [];
    renderResults(data.errors || []);
    setStatus(state.searchResults.length + " search results");
  } catch (error) {
    state.searchResults = [];
    renderResults([{ venue: "search", error: error.message }]);
    setStatus("Search failed");
  }
}

async function loadWatchlist() {
  try {
    const data = await fetchJson("/api/watchlist");
    applyLivePayload(data);
  } catch (error) {
    setStatus("Could not load watchlist: " + error.message);
  }
}

function connectStream() {
  if (state.stream) state.stream.close();
  const stream = new EventSource("/api/stream");
  state.stream = stream;
  stream.addEventListener("open", () => {
    els.liveBadge.textContent = "live";
    els.liveBadge.className = "live-badge live";
  });
  stream.addEventListener("live", (event) => {
    applyLivePayload(JSON.parse(event.data));
  });
  stream.addEventListener("error", () => {
    els.liveBadge.textContent = "reconnecting";
    els.liveBadge.className = "live-badge error";
  });
}

function applyLivePayload(payload) {
  state.watchlist = payload.markets || [];
  renderWatchlist();
  const updated = payload.updated_at ? formatTime(payload.updated_at) : "now";
  setStatus("Live refresh " + updated);
  if (!state.selectedKey && state.watchlist.length) {
    state.selectedKey = keyFor(state.watchlist[0]);
    loadHistory();
  } else if (state.selectedKey) {
    renderDetail();
  }
}

function renderResults(errors) {
  els.resultCount.textContent = String(state.searchResults.length);
  const errorHtml = errors.length
    ? errors.map((item) => '<div class="empty">' + esc(item.venue) + ": " + esc(item.error) + "</div>").join("")
    : "";
  if (!state.searchResults.length) {
    els.resultList.innerHTML = errorHtml || '<div class="empty">No matching markets.</div>';
    return;
  }
  els.resultList.innerHTML = errorHtml + state.searchResults.map((item, index) => {
    const price = displayPrice(item, "yes");
    return '<div class="result-row">' +
      '<div class="row-top">' +
      '<div><div class="market-title">' + esc(item.title) + '</div><div class="row-meta">' + esc(metaLine(item)) + '</div></div>' +
      '<span class="venue-tag">' + esc(item.venue) + '</span>' +
      '</div>' +
      '<div class="price-line"><strong>' + price + '</strong><span class="row-meta">YES price</span></div>' +
      '<div class="row-actions"><button class="button secondary" data-add="' + index + '" type="button">Track</button></div>' +
      '</div>';
  }).join("");
}

function renderWatchlist() {
  els.watchCount.textContent = String(state.watchlist.length);
  if (!state.watchlist.length) {
    els.watchList.innerHTML = '<div class="empty">No tracked markets yet. Search and add one.</div>';
    renderDetail();
    return;
  }
  els.watchList.innerHTML = state.watchlist.map((item) => {
    const key = keyFor(item);
    const latest = sidePrice(item, state.selectedSide);
    const active = key === state.selectedKey ? " active" : "";
    const status = item.ok === false ? '<div class="row-meta negative">' + esc(item.error || "Live update failed") + '</div>' : "";
    return '<div class="market-row' + active + '">' +
      '<div class="row-top">' +
      '<button class="market-title" data-select="' + esc(key) + '" type="button">' + esc(item.title) + '</button>' +
      '<span class="venue-tag">' + esc(item.venue) + '</span>' +
      '</div>' +
      '<div class="price-line"><strong>' + formatCents(latest) + '</strong><span class="row-meta">' + state.selectedSide.toUpperCase() + '</span></div>' +
      '<div class="row-meta">' + esc(metaLine(item)) + '</div>' +
      status +
      '<div class="row-actions"><button class="button secondary danger" data-remove="' + esc(key) + '" type="button">Remove</button></div>' +
      '</div>';
  }).join("");
  renderDetail();
}

function renderDetail() {
  const item = selectedItem();
  if (!item) {
    els.detailVenue.textContent = "Select a market";
    els.detailTitle.textContent = "No market selected";
    els.detailMeta.textContent = "Add a market to your watchlist, then select it.";
    state.history = [];
    drawChart();
    renderMetrics();
    return;
  }
  els.detailVenue.textContent = item.venue.toUpperCase() + " / " + state.selectedSide.toUpperCase();
  els.detailTitle.textContent = item.title;
  els.detailMeta.textContent = metaLine(item) + " / live " + (item.updated_at ? formatTime(item.updated_at) : "-");
  drawChart();
  renderMetrics(item);
}

async function loadHistory() {
  const item = selectedItem();
  renderDetail();
  if (!item) return;
  state.history = [];
  drawChart();
  try {
    const params = new URLSearchParams({
      venue: item.venue,
      id: item.id,
      side: state.selectedSide,
      range: state.selectedRange
    });
    const data = await fetchJson("/api/history?" + params.toString());
    state.history = data.points || [];
    drawChart();
    renderMetrics(item);
  } catch (error) {
    els.chartEmpty.textContent = "History failed: " + error.message;
    els.chartEmpty.classList.remove("hidden");
  }
}

function renderMetrics(item = selectedItem()) {
  const points = state.history.filter((point) => Number.isFinite(point.p));
  const latest = item ? sidePrice(item, state.selectedSide) : null;
  const first = points.length ? points[0].p : null;
  const last = points.length ? points[points.length - 1].p : latest;
  const change = first != null && last != null ? last - first : null;
  const prices = points.map((point) => point.p);
  const high = prices.length ? Math.max(...prices) : latest;
  const low = prices.length ? Math.min(...prices) : latest;
  const volatility = prices.length > 2 ? stdDev(prices.map((price, index) => index ? price - prices[index - 1] : 0).slice(1)) : null;
  const spread = item ? spreadFor(item, state.selectedSide) : null;
  const volume = item ? item.volume : null;
  const metrics = [
    [formatCents(latest), "Latest"],
    [formatSigned(change), "Change"],
    [formatCents(high) + " / " + formatCents(low), "High / Low"],
    [spread == null ? "-" : formatCents(spread), "Spread"],
    [volatility == null ? "-" : volatility.toFixed(2) + "c", "Volatility"],
    [formatCompact(volume), "Volume"]
  ];
  els.metricsGrid.innerHTML = metrics.map(([value, label]) => {
    const cls = label === "Change" && change ? (change > 0 ? "positive" : "negative") : "";
    return '<div class="metric"><strong class="' + cls + '">' + value + '</strong><span>' + label + '</span></div>';
  }).join("");
}

function drawChart() {
  const canvas = els.chart;
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(600, Math.floor(rect.width * dpr));
  canvas.height = Math.max(300, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);

  const points = state.history.filter((point) => Number.isFinite(point.t) && Number.isFinite(point.p));
  if (!points.length) {
    els.chartEmpty.textContent = selectedItem() ? "Loading chart data" : "No chart data loaded";
    els.chartEmpty.classList.remove("hidden");
    return;
  }
  els.chartEmpty.classList.add("hidden");

  const pad = { left: 52, right: 18, top: 18, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const minT = points[0].t;
  const maxT = points[points.length - 1].t || minT + 1;
  const minP = Math.max(0, Math.min(...points.map((p) => p.p)) - 2);
  const maxP = Math.min(100, Math.max(...points.map((p) => p.p)) + 2);
  const spanP = Math.max(1, maxP - minP);

  ctx.strokeStyle = "rgba(23, 32, 38, 0.12)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#66716f";
  ctx.font = "12px system-ui, sans-serif";
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    const price = maxP - (spanP * i) / 4;
    ctx.fillText(price.toFixed(1) + "c", 8, y + 4);
  }

  ctx.strokeStyle = "#177c76";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = pad.left + ((point.t - minT) / Math.max(1, maxT - minT)) * plotW;
    const y = pad.top + ((maxP - point.p) / spanP) * plotH;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  const last = points[points.length - 1];
  const x = pad.left + ((last.t - minT) / Math.max(1, maxT - minT)) * plotW;
  const y = pad.top + ((maxP - last.p) / spanP) * plotH;
  ctx.fillStyle = "#df5f4e";
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "#66716f";
  ctx.fillText(formatDate(minT), pad.left, height - 10);
  const endLabel = formatDate(maxT);
  ctx.fillText(endLabel, width - pad.right - ctx.measureText(endLabel).width, height - 10);
}

function handleResultClick(event) {
  const button = event.target.closest("button[data-add]");
  if (!button) return;
  const item = state.searchResults[Number(button.dataset.add)];
  if (!item) return;
  addWatch(item);
}

async function addWatch(item) {
  await fetchJson("/api/watchlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item)
  });
  setStatus("Tracking " + item.title);
  await loadWatchlist();
}

async function handleWatchClick(event) {
  const select = event.target.closest("button[data-select]");
  if (select) {
    state.selectedKey = select.dataset.select;
    await loadHistory();
    renderWatchlist();
    return;
  }
  const remove = event.target.closest("button[data-remove]");
  if (!remove) return;
  await fetchJson("/api/watchlist?" + new URLSearchParams({ key: remove.dataset.remove }), { method: "DELETE" });
  if (state.selectedKey === remove.dataset.remove) state.selectedKey = "";
  await loadWatchlist();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function selectedItem() {
  return state.watchlist.find((item) => keyFor(item) === state.selectedKey) || null;
}

function keyFor(item) {
  return item.venue + ":" + item.id;
}

function sidePrice(item, side) {
  return side === "no" ? item.no_price : item.yes_price;
}

function spreadFor(item, side) {
  const bid = side === "no" ? item.no_bid : item.yes_bid;
  const ask = side === "no" ? item.no_ask : item.yes_ask;
  if (!Number.isFinite(bid) || !Number.isFinite(ask)) return null;
  return Math.max(0, ask - bid);
}

function displayPrice(item, side) {
  return formatCents(sidePrice(item, side));
}

function metaLine(item) {
  const id = item.ticker || item.slug || item.id;
  const event = item.event ? item.event + " / " : "";
  return event + id;
}

function setActive(group, button) {
  [...group.querySelectorAll("button")].forEach((child) => child.classList.remove("active"));
  button.classList.add("active");
}

function setStatus(text) {
  els.statusLine.textContent = text;
}

function formatCents(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(1) + "c" : "-";
}

function formatSigned(value) {
  if (!Number.isFinite(Number(value))) return "-";
  const number = Number(value);
  return (number > 0 ? "+" : "") + number.toFixed(1) + "c";
}

function formatCompact(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  if (Math.abs(number) >= 1000000) return (number / 1000000).toFixed(1) + "m";
  if (Math.abs(number) >= 1000) return (number / 1000).toFixed(1) + "k";
  return number.toFixed(number >= 100 ? 0 : 1);
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

function stdDev(values) {
  if (!values.length) return null;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
