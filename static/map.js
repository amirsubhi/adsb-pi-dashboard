// Live map page. Polls /api/aircraft every second, /api/trails every 10 s,
// and moves each aircraft smoothly between polls by dead reckoning from its
// last position, track and ground speed. Text that came from the receiver is
// escaped with esc() before it reaches innerHTML.
"use strict";

const NM = 1852;
const POLL_MS = 1000, POLL_HIDDEN_MS = 5000, TRAILS_MS = 10000;
const MAX_EXTRAPOLATE = 15;   // seconds; don't keep "flying" an aircraft we've stopped hearing
const OLD_POSITION = 15;      // seconds; dim aircraft whose position is older than this
const EMERGENCY = { "7500": "unlawful interference (hijack)", "7600": "radio failure", "7700": "general emergency" };
const PLANE_PATH = "M24 3C26 3 27 6 27 9L27 18L44 27L44 31L27 26L27 37L32 41L32 44L24 42L16 44L16 41L21 37L21 26L4 31L4 27L21 18L21 9C21 6 22 3 24 3Z";
const PREFS_KEY = "adsb-map";

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let CONFIG = { receiver: null, transition_alt: 18000, tiles: null, trail_seconds: 300 };
let HOME = null;

// ---------- altitude colours, one set per theme ----------
const RAMPS = {
  dark:  [[0,"#ff7a45"],[4000,"#ffc53d"],[10000,"#8fd14f"],[20000,"#2ec8b5"],[30000,"#4c9bff"],[40000,"#b388ff"]],
  light: [[0,"#d4440c"],[4000,"#b77c00"],[10000,"#3f8f12"],[20000,"#0b8a80"],[30000,"#1f63c9"],[40000,"#7442cc"]]
};
let RAMP = RAMPS.light;
function hex2rgb(h){ return [1,3,5].map(i => parseInt(h.slice(i, i+2), 16)); }
function altColor(a){
  if (a == null) return "#7b8794";
  if (a <= RAMP[0][0]) return RAMP[0][1];
  for (let i = 1; i < RAMP.length; i++){
    if (a <= RAMP[i][0]){
      const [a0,c0] = RAMP[i-1], [a1,c1] = RAMP[i], t = (a-a0)/(a1-a0), x = hex2rgb(c0), y = hex2rgb(c1);
      return "rgb(" + x.map((v,k) => Math.round(v + (y[k]-v)*t)).join(",") + ")";
    }
  }
  return RAMP[RAMP.length-1][1];
}

// ---------- geometry ----------
const rad = d => d*Math.PI/180, deg = r => r*180/Math.PI;
function dest(lat, lon, brg, distM){
  const d = distM/6371000, b = rad(brg), p1 = rad(lat), l1 = rad(lon);
  const p2 = Math.asin(Math.sin(p1)*Math.cos(d) + Math.cos(p1)*Math.sin(d)*Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(p1), Math.cos(d) - Math.sin(p1)*Math.sin(p2));
  return [deg(p2), ((deg(l2)+540)%360)-180];
}
function distNm(lat1, lon1, lat2, lon2){
  const dp = rad(lat2-lat1), dl = rad(lon2-lon1);
  const a = Math.sin(dp/2)**2 + Math.cos(rad(lat1))*Math.cos(rad(lat2))*Math.sin(dl/2)**2;
  return 2*6371000*Math.asin(Math.sqrt(a))/NM;
}
function bearing(lat1, lon1, lat2, lon2){
  const y = Math.sin(rad(lon2-lon1))*Math.cos(rad(lat2));
  const x = Math.cos(rad(lat1))*Math.sin(rad(lat2)) - Math.sin(rad(lat1))*Math.cos(rad(lat2))*Math.cos(rad(lon2-lon1));
  return (deg(Math.atan2(y, x)) + 360) % 360;
}

// ---------- aircraft state ----------
// hex -> {..fields from /api/aircraft, t: time of the position (s), trail: [[lat, lon, alt]]}
const planes = new Map();
let selected = null, sortKey = "dist", query = "", trailsDirty = true, lastOk = 0, feedAge = 0;
const prefs = loadPrefs();

const nowS = () => Date.now()/1000;
const altNum = p => p.alt === "ground" ? 0 : p.alt;
const label = p => p.flight || p.hex.toUpperCase();
const hasPos = p => p.lat != null;

function vis(p){
  // Dead-reckoned position for smooth motion between polls.
  if (!hasPos(p)) return null;
  const dt = Math.min(MAX_EXTRAPOLATE, Math.max(0, nowS() - p.t));
  if (!p.gs || p.track == null || p.alt === "ground") return { lat: p.lat, lon: p.lon, alt: altNum(p) };
  const [lat, lon] = dest(p.lat, p.lon, p.track, p.gs*NM/3600*dt);
  const alt = altNum(p) == null ? null : Math.max(0, altNum(p) + (p.vr || 0)*dt/60);
  return { lat, lon, alt };
}
function distanceOf(p){ const v = vis(p); return HOME && v ? distNm(HOME.lat, HOME.lon, v.lat, v.lon) : null; }

function applyFeed(data){
  const now = nowS(), seen = new Set();
  for (const a of data.aircraft){
    seen.add(a.hex);
    let p = planes.get(a.hex);
    if (!p){ p = { trail: [] }; planes.set(a.hex, p); }
    Object.assign(p, a);
    p.t = now - (a.seen_pos || 0);
    if (hasPos(p)){
      const last = p.trail[p.trail.length-1];
      if (!last || last[0] !== p.lat || last[1] !== p.lon) p.trail.push([p.lat, p.lon, altNum(p) || 0]);
    }
  }
  for (const hex of [...planes.keys()]) if (!seen.has(hex)) planes.delete(hex);
  feedAge = Math.max(0, now - data.now);
  trailsDirty = true;
}

function applyTrails(trails){
  for (const [hex, pts] of Object.entries(trails)){
    const p = planes.get(hex);
    if (p) p.trail = pts.map(x => [x[0], x[1], x[2]]);
  }
  trailsDirty = true;
}

// ---------- formatting ----------
// unit=true also spells out the altitude in feet alongside a flight level,
// since "FL350" means nothing if you don't already know what a flight level is.
function fmtAlt(a, unit){
  if (a == null) return "—";
  if (a === "ground" || a < 100) return "Ground";
  const r = Math.round(a/25)*25;
  return r > CONFIG.transition_alt ? "FL" + String(Math.round(a/100)).padStart(3, "0") + (unit ? ` (${r.toLocaleString()} ft)` : "") : r.toLocaleString() + (unit ? " ft" : "");
}
const vrArrow = v => v > 200 ? " ↑" : v < -200 ? " ↓" : "";
const vrLabel = v => v == null || Math.abs(v) <= 200 ? "Vertical rate" : v > 0 ? "Climbing" : "Descending";
const vrText = v => v == null ? "—" : Math.abs(v) <= 200 ? "Level" : Math.abs(v).toLocaleString() + " fpm";
const pad3 = n => String(Math.round(n) % 360).padStart(3, "0");
function glyph(p){
  const emg = EMERGENCY[p.squawk];
  return '<svg viewBox="0 0 48 48" width="22" height="22" style="transform:rotate(' + (p.track || 0).toFixed(0) + 'deg);fill:' +
    (emg ? "var(--danger)" : hasPos(p) ? altColor(altNum(p)) : "var(--ink-mute)") + '"><path d="' + PLANE_PATH + '"/></svg>';
}
function subline(p){
  const parts = [airlineOf(p.flight), p.type || p.desc, p.reg].filter(Boolean).map(esc);
  if (!hasPos(p)) parts.push("no position");
  return parts.length ? parts.join(" · ") : "Hex " + esc(p.hex.toUpperCase());
}

// ---------- list rail ----------
const listEl = $("list");
function renderList(){
  const q = query.trim().toUpperCase();
  let rows = [...planes.values()].map(p => ({ p, d: distanceOf(p) }));
  const positioned = rows.filter(r => hasPos(r.p)).length;
  const furthest = Math.max(0, ...rows.map(r => r.d || 0));
  $("statline").innerHTML = `<span><b>${planes.size}</b> aircraft</span> · <span><b>${positioned}</b> on the map</span>` +
    (HOME && positioned ? ` · <span>furthest <b>${furthest.toFixed(0)}</b> nm</span>` : "");
  if (q) rows = rows.filter(({p}) => [p.flight, airlineOf(p.flight), p.hex.toUpperCase(), p.type, p.reg, p.squawk].some(s => s && String(s).toUpperCase().includes(q)));
  const far = 1e9;
  rows.sort((a, b) => (!!EMERGENCY[b.p.squawk]) - (!!EMERGENCY[a.p.squawk]) ||
    (sortKey === "dist" ? (a.d ?? far) - (b.d ?? far)
     : sortKey === "alt" ? (altNum(b.p) ?? -1) - (altNum(a.p) ?? -1)
     : label(a.p).localeCompare(label(b.p))));
  if (!rows.length){
    listEl.innerHTML = `<div class="empty">${q ? `No aircraft match “${esc(query)}”.` : "No aircraft in range right now."}</div>`;
    return;
  }
  listEl.innerHTML = rows.map(({p, d}) => {
    const emg = EMERGENCY[p.squawk];
    return `<div class="row${p.hex === selected ? " sel" : ""}${emg ? " emg" : ""}" role="listitem" tabindex="0" data-hex="${esc(p.hex)}">` +
      `<span class="glyph">${glyph(p)}</span>` +
      `<span class="cs">${esc(label(p))}${emg ? `<span class="sq">${esc(p.squawk)}</span>` : ""}</span>` +
      `<span class="alt" title="${esc(fmtAlt(p.alt, true))}">${fmtAlt(p.alt)}${vrArrow(p.vr)}</span>` +
      `<span class="sub">${subline(p)}</span>` +
      `<span class="dist">${d == null ? "" : d.toFixed(0) + " nm"}</span></div>`;
  }).join("");
}
listEl.addEventListener("click", e => { const r = e.target.closest(".row"); if (r) select(r.dataset.hex, true); });
listEl.addEventListener("keydown", e => { if (e.key === "Enter"){ const r = e.target.closest(".row"); if (r) select(r.dataset.hex, true); } });
$("q").addEventListener("input", e => { query = e.target.value; renderList(); });
document.querySelectorAll("[data-sort]").forEach(b => b.addEventListener("click", () => {
  sortKey = b.dataset.sort;
  document.querySelectorAll("[data-sort]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  renderList();
}));

// ---------- detail card ----------
const cardEl = $("card");
function renderCard(){
  const p = selected && planes.get(selected);
  if (!p){ cardEl.hidden = true; return; }
  const v = vis(p), emg = EMERGENCY[p.squawk];
  const d = HOME && v ? distNm(HOME.lat, HOME.lon, v.lat, v.lon) : null, b = HOME && v ? bearing(HOME.lat, HOME.lon, v.lat, v.lon) : null;
  const meta = [airlineOf(p.flight), p.desc || p.type, p.reg].filter(Boolean).map(esc).concat(`<span class="mono">${esc(p.hex.toUpperCase())}</span>`).join(" · ");
  const age = p.seen_pos != null ? Math.round(nowS() - p.t) : null;
  cardEl.hidden = false;
  cardEl.innerHTML =
    `<div class="card-top"><div><h2>${esc(label(p))}</h2><div class="meta">${meta}</div></div>` +
    `<button class="card-close" aria-label="Close details">×</button></div>` +
    (emg ? `<div class="alert">Squawk <b>${esc(p.squawk)}</b>, ${emg}</div>` : "") +
    `<dl>` +
    `<div><dt>Altitude</dt><dd>${fmtAlt(v ? (p.alt === "ground" ? "ground" : v.alt) : p.alt, true)}</dd></div>` +
    `<div><dt>${vrLabel(p.vr)}</dt><dd>${vrText(p.vr)}</dd></div>` +
    `<div><dt>Ground speed</dt><dd>${p.gs != null ? Math.round(p.gs) + " kt" : "—"}</dd></div>` +
    `<div><dt>Track</dt><dd>${p.track != null ? pad3(p.track) + "°" : "—"}</dd></div>` +
    `<div><dt>Distance</dt><dd>${d != null ? d.toFixed(1) + " nm" : "—"}</dd></div>` +
    `<div><dt>Bearing from you</dt><dd>${b != null ? pad3(b) + "°" : "—"}</dd></div>` +
    `<div><dt>Squawk</dt><dd>${p.squawk ? esc(p.squawk) : "—"}</dd></div>` +
    `<div><dt>Signal</dt><dd>${p.rssi != null ? p.rssi.toFixed(1) + " dBFS" : "—"}</dd></div>` +
    `</dl>` + (age != null && age > OLD_POSITION ? `<p class="meta" style="margin:8px 0 0">Position ${age} s old</p>` : "");
}
cardEl.addEventListener("click", e => { if (e.target.closest(".card-close")) select(null); });

// ---------- map ----------
let map = null, ready = false, zooming = false, tiles = null, tilesCredited = false;
const markers = new Map(), rings = [];
let trailLayer, ringLayer, homeDot, outlineLand, outlineLines, outlineGrid;

function select(hex, fly){
  selected = hex; trailsDirty = true; renderList(); renderCard();
  for (const [h, m] of markers) m.el.classList.toggle("sel", h === hex);
  const p = hex && planes.get(hex), v = p && vis(p);
  if (v && fly && map) map.setView([v.lat, v.lon], Math.max(map.getZoom(), 7.25), { animate: true });
}

function makeMarker(p){
  const html = `<div class="ac${EMERGENCY[p.squawk] ? " emg" : ""}${p.hex === selected ? " sel" : ""}">` +
    `<svg viewBox="0 0 48 48" width="24" height="24"><path d="${PLANE_PATH}"/></svg><span class="lbl">${esc(label(p))}</span></div>`;
  const mk = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "ac-wrap", html, iconSize: [24, 24], iconAnchor: [12, 12] }), keyboard: false }).addTo(map);
  mk.on("click", () => select(p.hex, false));
  const el = mk.getElement().firstChild;
  return { mk, el, svg: el.firstChild, lbl: el.lastChild, squawk: p.squawk, name: label(p) };
}
function syncMarkers(){
  for (const [hex, m] of markers){
    const p = planes.get(hex);
    // Rebuild when the aircraft vanished, lost its position, or its callsign/squawk changed.
    if (!p || !hasPos(p) || m.squawk !== p.squawk || m.name !== label(p)){ m.mk.remove(); markers.delete(hex); }
  }
  for (const p of planes.values()) if (hasPos(p) && !markers.has(p.hex)) markers.set(p.hex, makeMarker(p));
}

function rebuildTrails(){
  trailLayer.clearLayers();
  for (const p of planes.values()){
    if ((!prefs.trails && p.hex !== selected) || !hasPos(p) || !p.trail.length) continue;
    const v = vis(p), t = p.trail.concat([[v.lat, v.lon, v.alt || 0]]), sel = p.hex === selected;
    // One polyline per run of points in the same 2,000 ft band keeps the layer count low.
    let run = [[t[0][0], t[0][1]]], band = Math.floor(t[0][2]/2000);
    const flush = () => { if (run.length > 1) L.polyline(run, { color: altColor(band*2000 + 1000), weight: sel ? 2.6 : 1.4, opacity: sel ? 1 : 0.55, interactive: false }).addTo(trailLayer); };
    for (let i = 1; i < t.length; i++){
      const b = Math.floor(t[i][2]/2000), pt = [t[i][0], t[i][1]];
      run.push(pt);
      if (b !== band){ flush(); run = [pt]; band = b; }
    }
    flush();
  }
}

function frame(){
  if (ready && !zooming){
    const now = nowS();
    for (const p of planes.values()){
      const m = markers.get(p.hex); if (!m) continue;
      const v = vis(p);
      m.mk.setLatLng([v.lat, v.lon]);
      m.svg.style.transform = "rotate(" + (p.track || 0).toFixed(1) + "deg)";
      m.el.style.setProperty("--c", EMERGENCY[p.squawk] ? "var(--danger)" : altColor(v.alt));
      m.el.classList.toggle("old", now - p.t > OLD_POSITION);
    }
    if (trailsDirty){ rebuildTrails(); trailsDirty = false; }
  }
  requestAnimationFrame(frame);
}

// Offline outline: Natural Earth coastlines and borders, bundled with the
// dashboard, drawn beneath the street map so it shows whenever tiles can't load.
async function loadOutline(){
  try {
    const world = await (await fetch("/geo/countries-50m.json")).json();
    const center = map.getCenter(), lat = HOME ? HOME.lat : center.lat, lon = HOME ? HOME.lon : center.lng;
    const box = [lon - 25, lat - 20, lon + 25, lat + 20];
    const inBox = ([x, y]) => x >= box[0] && x <= box[2] && y >= box[1] && y <= box[3];
    const near = f => JSON.stringify(f.geometry.coordinates).match(/-?\d+\.?\d*,-?\d+\.?\d*/g).some(s => inBox(s.split(",").map(Number)));
    const countries = topojson.feature(world, world.objects.countries).features.filter(f => f.geometry && near(f));
    const mesh = topojson.mesh(world, world.objects.countries);
    const lines = [];
    for (const line of mesh.coordinates){
      let cur = [];
      for (const pt of line){ if (inBox(pt)) cur.push([pt[1], pt[0]]); else { if (cur.length > 1) lines.push(cur); cur = []; } }
      if (cur.length > 1) lines.push(cur);
    }
    const grid = [];
    for (let x = Math.floor(box[0]); x <= box[2]; x++) grid.push([[box[1], x], [box[3], x]]);
    for (let y = Math.floor(box[1]); y <= box[3]; y++) grid.push([[y, box[0]], [y, box[2]]]);
    outlineLand = L.geoJSON({ type: "FeatureCollection", features: countries }, { pane: "outline", style: { stroke: false, fillOpacity: 1 }, interactive: false }).addTo(map);
    outlineGrid = L.polyline(grid, { pane: "outline", weight: 1, interactive: false }).addTo(map);
    outlineLines = L.polyline(lines, { pane: "outline", weight: 1, interactive: false }).addTo(map);
    styleMap();
  } catch (e) {
    console.warn("outline map not available", e);
  }
}

function tileUrl(){
  return CONFIG.tiles.url.replace("{theme}", effectiveTheme());
}
function setupTiles(){
  if (!CONFIG.tiles){
    $("map-note").textContent = "Outline map only (map_tiles = off). Nothing is loaded from the internet.";
    return;
  }
  tiles = L.tileLayer(tileUrl(), {
    subdomains: CONFIG.tiles.subdomains || "abc", maxZoom: CONFIG.tiles.max_zoom,
    // The page sends no referrer, but tile servers ask for one to identify the app.
    referrerPolicy: "strict-origin-when-cross-origin", crossOrigin: false,
  }).addTo(map);
  // Credit the street map only once it actually shows; offline, the outline (Natural Earth) is all you see.
  tiles.on("tileload", () => {
    if (tilesCredited) return;
    tilesCredited = true;
    map.attributionControl.addAttribution(CONFIG.tiles.attribution);
    $("map-note").textContent = "Street map loads when the Pi's internet is up; otherwise the bundled outline shows.";
  });
  $("map-note").textContent = "Street map loads when the Pi's internet is up; otherwise the bundled outline shows.";
}

function initMap(){
  if (!window.L) throw new Error("Leaflet did not load");
  const center = HOME ? [HOME.lat, HOME.lon] : [20, 0];
  map = L.map("map", { zoomControl: false, preferCanvas: true, zoomSnap: 0.25, zoomDelta: 0.5 }).setView(center, HOME ? 6.25 : 2);
  map.createPane("outline").style.zIndex = 150;   // below tiles (200), so street maps cover it when they load
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  map.attributionControl.addAttribution('<a href="https://www.naturalearthdata.com">Natural Earth</a>');
  L.control.zoom({ position: "topright" }).addTo(map);
  setupTiles();
  ringLayer = L.layerGroup();
  if (HOME){
    [50, 100, 150, 200].forEach(n => {
      rings.push(L.circle([HOME.lat, HOME.lon], { radius: n*NM, fill: false, weight: 1, interactive: false }).addTo(ringLayer));
      L.marker(dest(HOME.lat, HOME.lon, 0, n*NM), { icon: L.divIcon({ className: "ring-lbl", html: n + " nm", iconSize: [48, 14], iconAnchor: [24, 16] }), interactive: false }).addTo(ringLayer);
    });
    if (prefs.rings) ringLayer.addTo(map);
    homeDot = L.circleMarker([HOME.lat, HOME.lon], { radius: 5, weight: 2, fillOpacity: 1, interactive: false }).addTo(map);
  }
  trailLayer = L.layerGroup().addTo(map);
  map.on("click", () => select(null));
  map.on("zoomstart", () => zooming = true);
  map.on("zoomend", () => zooming = false);
  loadOutline();
  styleMap();
  ready = true;
}

// ---------- toolbar and preferences (remembered in this browser) ----------
function loadPrefs(){
  const d = { trails: true, labels: true, rings: true };
  try { return Object.assign(d, JSON.parse(localStorage.getItem(PREFS_KEY) || "{}")); } catch (e) { return d; }
}
function savePrefs(){ try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) {} }
function toggle(id, key, fn){
  const b = $(id);
  b.setAttribute("aria-pressed", String(prefs[key]));
  fn(prefs[key]);
  b.addEventListener("click", () => { prefs[key] = !prefs[key]; savePrefs(); b.setAttribute("aria-pressed", String(prefs[key])); fn(prefs[key]); });
}

// ---------- theme ----------
const mq = matchMedia("(prefers-color-scheme: dark)");
const tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function effectiveTheme(){ const a = document.documentElement.dataset.theme; return a === "light" || a === "dark" ? a : (mq.matches ? "dark" : "light"); }
function styleMap(){
  if (!map) return;
  if (outlineLand){
    outlineLand.setStyle({ fillColor: tok("--map-land") });
    outlineLines.setStyle({ color: tok("--map-border") });
    outlineGrid.setStyle({ color: tok("--map-grat") });
  }
  rings.forEach(r => r.setStyle({ color: tok("--map-ring") }));
  if (homeDot) homeDot.setStyle({ color: tok("--accent"), fillColor: tok("--map-sea") });
  if (tiles){
    tiles.setUrl(tileUrl());
    $("map").classList.toggle("tiles-dark", effectiveTheme() === "dark" && CONFIG.tiles.dark_filter);
  }
}
function applyTheme(){
  RAMP = RAMPS[effectiveTheme()];
  $("ramp").style.background = "linear-gradient(90deg," + RAMP.map(([a, c]) => c + " " + (a/400) + "%").join(",") + ")";
  styleMap(); trailsDirty = true; renderList();
}
document.addEventListener("themechange", applyTheme);
mq.addEventListener("change", applyTheme);

// ---------- feed ----------
function renderFeed(){
  const el = $("feed"), age = Math.round(Math.max(nowS() - lastOk, feedAge));
  const live = lastOk && age < 10;
  el.className = "pill " + (live ? "live" : lastOk ? "stale" : "");
  el.innerHTML = `<span class="dot"></span>${live ? "Live" : lastOk ? `No data for ${age} s` : "Connecting"}`;
}
async function poll(){
  try {
    const r = await fetch("/api/aircraft");
    if (!r.ok) throw new Error(r.status);
    applyFeed(await r.json());
    lastOk = nowS();
    if (ready) syncMarkers();
    renderList(); renderCard();
  } catch (e) { /* keep the last picture; renderFeed shows the gap */ }
  renderFeed();
  setTimeout(poll, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
}
async function pollTrails(){
  try { applyTrails(await (await fetch("/api/trails")).json()); } catch (e) {}
  setTimeout(pollTrails, TRAILS_MS);
}

// ---------- boot ----------
async function boot(){
  try { CONFIG = Object.assign(CONFIG, await (await fetch("/api/map-config")).json()); } catch (e) {}
  HOME = CONFIG.receiver ? { lat: CONFIG.receiver.lat, lon: CONFIG.receiver.lon } : null;
  applyTheme();
  try { initMap(); }
  catch (err) { $("map").innerHTML = '<div class="map-err">The map could not load. The aircraft list still updates live.</div>'; }
  if (!HOME) $("map-note").textContent = "Receiver position unknown, so there are no distances or range rings. Set receiver_lat and receiver_lon in settings.ini.";
  toggle("t-trails", "trails", () => { trailsDirty = true; });
  toggle("t-labels", "labels", on => $("map").classList.toggle("no-labels", !on));
  toggle("t-rings", "rings", on => { if (ready && HOME){ on ? ringLayer.addTo(map) : ringLayer.remove(); } });
  $("t-center").addEventListener("click", () => { if (map && HOME) map.setView([HOME.lat, HOME.lon], 6.25, { animate: true }); });
  if (!HOME) $("t-rings").disabled = $("t-center").disabled = true;
  await poll();
  pollTrails();
  setInterval(renderCard, 500);
  setInterval(renderFeed, 1000);
  requestAnimationFrame(frame);
}
boot();
