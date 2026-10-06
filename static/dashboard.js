// Dashboard page. Everything comes from app.py's JSON API; anything that
// originated from the receiver, a feeder or the journal is escaped with esc()
// before it reaches innerHTML.
"use strict";
const STALE_AFTER = 45;
let TRANSITION_ALT = 18000, RX = null, SHOW_EXACT = false;
const SVGNS = "http://www.w3.org/2000/svg";
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const num = (v, d=0) => v == null ? "—" : Number(v).toLocaleString(undefined, {minimumFractionDigits:d, maximumFractionDigits:d});
const fmtDate = s => new Date(s*1000).toLocaleString(undefined,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
const fmtClock = s => new Date(s*1000).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"});
const pad3 = n => String(Math.round(n) % 360).padStart(3, "0");
function fmtDur(sec){ sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60; return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`; }
function fmtAlt(a){ if (a == null) return "—"; if (a < 100) return "Ground"; return a > TRANSITION_ALT ? "FL" + String(Math.round(a/100)).padStart(3,"0") : (Math.round(a/25)*25).toLocaleString() + " ft"; }
function distNm(lat1, lon1, lat2, lon2){ const r = Math.PI/180, a = Math.sin((lat2-lat1)*r/2)**2 + Math.cos(lat1*r)*Math.cos(lat2*r)*Math.sin((lon2-lon1)*r/2)**2; return 2*6371000*Math.asin(Math.sqrt(a))/1852; }
function el(tag, attrs, parent){ const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
const kvRow = (k, v, extra="", flag=null) => `<div><dt>${k}</dt><dd>${flag ? `<span class="flag ${flag[0]}">${flag[1]}</span>` : ""}${v}</dd>${extra || "<span></span>"}</div>`;

// ---------- tiny inline sparkline for list rows ----------
function sparkSVG(points){
  if (!points || points.length < 2) return "<span></span>";
  const w = 84, h = 22, vals = points.map(p => p[1]), min = Math.min(...vals), max = Math.max(...vals), span = (max-min) || 1;
  const x = i => 2 + i/(points.length-1)*(w-6), y = v => h-3 - (v-min)/span*(h-6);
  const d = points.map((p,i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p[1]).toFixed(1)).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path class="s-line" d="${d}"/><circle class="s-end" cx="${x(points.length-1)}" cy="${y(vals[vals.length-1])}" r="3"/></svg>`;
}

// ---------- status ----------
let lastUpdated = 0, lastStatus = null, metrics = [], typical = null;
function renderUpdated(){
  if (!lastUpdated) return;
  const age = Date.now()/1000 - lastUpdated, stale = age > STALE_AFTER;
  $("updated").classList.toggle("stale", stale);
  $("updated").textContent = stale ? `No update for ${Math.round(age/60) || 1} min, last at ${fmtClock(lastUpdated)}` : `Updated ${new Date(lastUpdated*1000).toLocaleTimeString()}`;
}
function typicalAt(ts){ // usual min/max for this time of day, from the 7-day reference
  const r = typical && typical.ranges;
  if (!r || !r.length) return null;
  const d = new Date(ts*1000), n = r.length, pos = (d.getHours()*60 + d.getMinutes()) / (1440/n) - 0.5;
  const i = Math.floor(pos), f = pos - i, a = r[(i % n + n) % n], b = r[(i + 1) % n];
  if (!a || !b) return a || b || null;
  return [a[0] + (b[0]-a[0])*f, a[1] + (b[1]-a[1])*f];  // interpolate between slots so the band is smooth
}

async function refreshStatus(){
  let d;
  try { d = await (await fetch("/api/status")).json(); } catch(e){ renderUpdated(); return; }
  if (!d || !d.updated) return;
  lastStatus = d; lastUpdated = d.updated;
  if (d.station){ TRANSITION_ALT = d.station.transition_alt; SHOW_EXACT = !!d.station.show_exact_location; }
  renderUpdated();
  const rx = d.receiver || {}, t = d.throttled || {}, temp = d.temp_c, ac = d.aircraft || [];
  if (rx.lat != null){
    RX = {lat: rx.lat, lon: rx.lon};
    // Rounded to ~1 km unless show_exact_location is on, so screenshots don't give away an address.
    $("footer-location").textContent = SHOW_EXACT ? ` Receiver at ${rx.lat.toFixed(4)}°, ${rx.lon.toFixed(4)}°.` : ` Receiver near ${rx.lat.toFixed(2)}°, ${rx.lon.toFixed(2)}°.`;
  }

  // Annunciator: only what is abnormal, worst first. Nothing loud when all is well.
  // app.py decides what counts (and waits out short blips); this only draws it.
  const LABEL = {warning: ["critical", "WARNING"], caution: ["warning", "CAUTION"]};
  const issues = (d.alerts || []).map(a => [LABEL[a.level][0], LABEL[a.level][1], esc(a.text)]);
  if (d.error) issues.push(["warning", "CAUTION", "The collector hit an error, so these figures may be old: " + esc(d.error)]);
  $("annunciator").innerHTML = issues.length
    ? issues.map(i => `<div class="ann ${i[0]}"><b>${i[1]}</b><span>${i[2]}</span></div>`).join("")
    : `<p class="allclear">All checks normal: power, temperature, receiver and feeders.</p>`;

  // Performance band
  $("hero-fig").textContent = ac.length;
  const typ = typicalAt(d.updated);
  if (typ){
    const verdict = ac.length > typ[1] ? "Busier than usual" : ac.length < typ[0] ? "Quieter than usual" : "About usual";
    $("hero-ctx").innerHTML = `${verdict} for ${fmtClock(d.updated)} <small class="typ">(usually ${Math.round(typ[0])} to ${Math.round(typ[1])})</small>`;
  } else if (typical){
    $("hero-ctx").textContent = "The usual range for each time of day appears after two days of data.";
  }
  $("ro-msg").textContent = num(d.message_rate);
  const best = d.range_today;
  if (best){ $("ro-range").innerHTML = `${num(best.nm)} <small>nm</small>`; $("ro-range-sub").textContent = `${best.flight || String(best.hex).toUpperCase()}, bearing ${pad3(best.bearing)}°`; }
  else { $("ro-range").textContent = "—"; $("ro-range-sub").textContent = RX ? "No positions yet today" : "Needs the receiver position"; }
  $("ro-unique").textContent = num(d.unique_today);
  if (ac.length){
    const named = a => esc(a.flight || a.hex.toUpperCase());
    const highest = ac.filter(a => a.alt_baro != null).sort((a,b) => b.alt_baro - a.alt_baro)[0];
    const closest = RX ? ac.filter(a => a.lat != null).map(a => ({a, d: distNm(RX.lat, RX.lon, a.lat, a.lon)})).sort((x,y) => x.d - y.d)[0] : null;
    const parts = [];
    if (closest) parts.push(`Closest <b>${named(closest.a)}</b> at ${closest.d < 10 ? closest.d.toFixed(1) : closest.d.toFixed(0)} nm`);
    if (highest) parts.push(`highest <b>${named(highest)}</b> at ${fmtAlt(highest.alt_baro)}`);
    $("overhead-text").innerHTML = parts.join(" · ");
  } else $("overhead-text").textContent = "No aircraft in range right now.";

  // This Pi / Receiver
  const up = d.uptime_s || 0, load = d.load || {}, mem = d.mem || {}, disk = d.disk || {};
  const tempPts = metrics.filter(r => r.temp_c != null).slice(-72).map(r => [r.ts, r.temp_c]);
  const msgPts = metrics.filter(r => r.msg_rate != null).slice(-72).map(r => [r.ts, r.msg_rate]);
  const tempFlag = temp >= 75 ? ["critical","Hot"] : temp >= 70 ? ["warning","Warm"] : null;
  const power = t.undervoltage_now ? ["Under-voltage", ["critical","Now"]] : t.undervoltage_occurred ? ["Dipped this boot", ["warning","Check"]] : ["Stable", null];
  $("pi-list").innerHTML =
    kvRow("CPU temperature", temp != null ? `${temp.toFixed(0)} <small>°C</small>` : "—", sparkSVG(tempPts), tempFlag) +
    kvRow("Power", power[0], "", power[1]) +
    kvRow("Uptime", `${Math.floor(up/86400)} <small>d</small> ${Math.floor((up%86400)/3600)} <small>h</small>`) +
    kvRow("Load, 1 / 5 / 15 min", load.l1 != null ? `${num(load.l1,2)} <small>${num(load.l5,2)} · ${num(load.l15,2)}</small>` : "—") +
    kvRow("Memory available", mem.available_mb != null ? `${num(mem.available_mb/1024,1)} <small>of ${num(mem.total_mb/1024,1)} GB</small>` : "—") +
    kvRow("SD card used", disk.used_gb != null ? `${num(disk.used_gb,1)} <small>of ${num(disk.total_gb,1)} GB</small>` : "—");
  $("rx-list").innerHTML =
    kvRow("Message rate", d.message_rate != null ? `${num(d.message_rate)} <small>/ s</small>` : "—", sparkSVG(msgPts)) +
    kvRow("Positions, last minute", num(rx.positions_1min)) +
    kvRow("Signal / noise", rx.signal != null ? `${num(rx.signal,1)} <small>/ ${num(rx.noise,1)} dBFS</small>` : "—") +
    kvRow("Gain", rx.gain_db != null ? `${num(rx.gain_db,1)} <small>dB</small>` : "—") +
    kvRow("Clock drift", rx.estimated_ppm != null ? `${num(rx.estimated_ppm,1)} <small>ppm</small>` : "—") +
    kvRow("Total messages", num(d.messages_total));

  // Feeders: a quiet line when feeding, amber when MLAT isn't working, red when down
  const feeders = d.feeders || {}, fr = d.fr24 || {}, ml = d.mlat || {};
  const STATE = {ok: ["", "Feeding"], degraded: ["warn", "MLAT off"], down: ["down", "Down"], stopped: ["down", "Stopped"], absent: ["off", "Not installed"]};
  const showFeeder = (key, idText) => {
    const f = feeders[key] || {state: "absent", detail: ""}, [cls, label] = STATE[f.state] || ["off", f.state];
    $(key + "-card").classList.toggle("disabled", f.state === "absent");
    $(key + "-card").classList.toggle("down", cls === "down");
    $(key + "-state").className = "state " + cls; $(key + "-state").textContent = label;
    $(key + "-id").textContent = f.state === "ok" ? idText : f.detail;
    return f.state !== "absent";
  };
  if (showFeeder("fr24", `Radar ${fr.radar_id || "—"} · ${fr.link_type || "—"} link`)){
    $("fr24-ac").textContent = num(fr.tracked_ac); $("fr24-msgs").textContent = num(fr.msgs);
    $("fr24-mlat").innerHTML = fr.mlat_status == null ? "—" : fr.mlat_status === "ok" ? `${num(fr.mlat_ac)} <small>aircraft · sync ${num(fr.sync)}</small>` : esc(fr.mlat_status);
  }
  if (showFeeder("adsbx", "adsbexchange-feed · adsbexchange-mlat")){
    $("adsbx-ac").textContent = num(d.adsbx_status && d.adsbx_status.aircraft_with_pos);
    $("mlat-peers").textContent = num(ml.peer_count);
    $("mlat-rate").innerHTML = ml.msg_rate_received != null ? `${num(ml.msg_rate_received,1)} <small>/ s</small>` : "—";
    $("mlat-pos").innerHTML = ml.positions_per_min != null ? `${num(ml.positions_per_min)} <small>/ min</small>` : "—";
  }
}

// ---------- traffic chart: today's line over the usual band ----------
function drawTraffic(){
  const host = $("traffic"); host.innerHTML = "";
  const pts = metrics.filter(r => r.aircraft_count != null);
  if (pts.length < 2){ host.innerHTML = `<p class="cap">Collecting data. The chart appears after a few minutes.</p>`; return; }
  const W = Math.max(300, host.clientWidth), H = 210, L = 30, R = 10, T = 8, B = 24;
  const t0 = pts[0].ts, t1 = pts[pts.length-1].ts;
  const bandPts = pts.map(p => [p.ts, typicalAt(p.ts)]).filter(b => b[1]);
  $("band-key").hidden = bandPts.length < 2;
  const maxV = Math.max(...pts.map(p => p.aircraft_count), ...bandPts.map(b => b[1][1]), 5);
  const step = maxV > 60 ? 20 : maxV > 24 ? 10 : 5, top = Math.ceil(maxV/step)*step;
  const x = ts => L + (ts - t0)/(t1 - t0)*(W - L - R), y = v => T + (1 - v/top)*(H - T - B);
  const svg = el("svg", {viewBox:`0 0 ${W} ${H}`, role:"img", "aria-label":"Aircraft in range over the last 24 hours against the usual range"}, host);
  for (let v = 0; v <= top; v += step){
    el("line", {x1:L, x2:W-R, y1:y(v), y2:y(v), class: v ? "g-grid" : "g-base"}, svg);
    el("text", {x:L-6, y:y(v)+4, "text-anchor":"end", class:"g-tick"}, svg).textContent = v;
  }
  const first = new Date(t0*1000); first.setMinutes(0,0,0);
  for (let ts = first.getTime()/1000 + 3600; ts < t1; ts += 3600){
    const h = new Date(ts*1000).getHours();
    if (h % 3) continue;
    el("text", {x:x(ts), y:H-6, "text-anchor":"middle", class:"g-tick"}, svg).textContent = String(h).padStart(2,"0") + ":00";
  }
  if (bandPts.length > 1){
    const upper = bandPts.map(b => `${x(b[0]).toFixed(1)},${y(b[1][1]).toFixed(1)}`), lower = bandPts.slice().reverse().map(b => `${x(b[0]).toFixed(1)},${y(b[1][0]).toFixed(1)}`);
    el("polygon", {points: upper.concat(lower).join(" "), class:"g-band"}, svg);
  }
  const line = pts.map((p,i) => (i ? "L" : "M") + x(p.ts).toFixed(1) + " " + y(p.aircraft_count).toFixed(1)).join("");
  el("path", {d: line, class:"g-line"}, svg);
  const last = pts[pts.length-1];
  el("circle", {cx:x(last.ts), cy:y(last.aircraft_count), r:4.5, class:"g-dot"}, svg);

  // hover: crosshair + tooltip
  const cross = el("line", {y1:T, y2:H-B, class:"g-cross", visibility:"hidden"}, svg);
  const dot = el("circle", {r:4.5, class:"g-dot", visibility:"hidden"}, svg);
  const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; host.appendChild(tip);
  const hit = el("rect", {x:L, y:T, width:W-L-R, height:H-T-B, class:"g-hit"}, svg);
  const move = ev => {
    const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * W / r.width;
    const ts = t0 + (sx - L)/(W - L - R)*(t1 - t0);
    let best = pts[0]; for (const p of pts) if (Math.abs(p.ts - ts) < Math.abs(best.ts - ts)) best = p;
    const cx = x(best.ts), cy = y(best.aircraft_count), b = typicalAt(best.ts);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("visibility", "visible");
    tip.innerHTML = `<div class="t">${fmtClock(best.ts)}</div><b>${Math.round(best.aircraft_count)}</b> aircraft${b ? ` <small>· usually ${Math.round(b[0])} to ${Math.round(b[1])}</small>` : ""}`;
    tip.hidden = false;
    const px = cx / W * r.width, flip = px > r.width - 170;
    tip.style.left = (flip ? px - tip.offsetWidth - 12 : px + 12) + "px"; tip.style.top = (cy / H * r.height - 10) + "px";
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility","hidden"); dot.setAttribute("visibility","hidden"); tip.hidden = true; });
}

// ---------- coverage: furthest position per 10° sector ----------
let coverage = null;
function drawCoverage(){
  const host = $("coverage"); host.innerHTML = "";
  const today = coverage ? coverage.today.map(v => v || 0) : [], best = coverage ? coverage.best.map(v => v || 0) : [];
  if (!today.some(v => v > 0)){
    host.innerHTML = `<p class="cap">${RX ? "Collecting data. The plot fills in as aircraft are heard in each direction." : "Coverage needs the receiver position. Set receiver_lat and receiver_lon in settings.ini."}</p>`;
    return;
  }
  const S = 300, c = S/2, maxNm = Math.max(...today, ...best, 50);
  const ringStep = maxNm > 160 ? 50 : 25, top = Math.ceil(maxNm/ringStep)*ringStep, rMax = c - 22;
  const pt = (brg, nm) => { const a = (brg - 90) * Math.PI/180, r = nm/top*rMax; return [c + r*Math.cos(a), c + r*Math.sin(a)]; };
  const svg = el("svg", {viewBox:`0 0 ${S} ${S}`, role:"img", "aria-label":"Coverage: furthest range by bearing, today and best of the past 7 days"}, host);
  for (let b = 0; b < 360; b += 30){ const [x2,y2] = pt(b, top); el("line", {x1:c, y1:c, x2, y2, class:"g-spoke"}, svg); }
  for (let n = ringStep; n <= top; n += ringStep){
    el("circle", {cx:c, cy:c, r:n/top*rMax, class:"g-ring"}, svg);
    const [lx,ly] = pt(135, n); el("text", {x:lx+3, y:ly-3, class:"g-tick"}, svg).textContent = n;
  }
  [["N",0],["E",90],["S",180],["W",270]].forEach(([s,b]) => { const [lx,ly] = pt(b, top*1.1); el("text", {x:lx, y:ly+4, "text-anchor":"middle", class:"g-card"}, svg).textContent = s; });
  const n = today.length, w = 360/n;
  const poly = arr => arr.map((nm,i) => pt(i*w + w/2, nm).map(v => v.toFixed(1)).join(",")).join(" ");
  const sector = el("path", {class:"g-sector", visibility:"hidden"}, svg);
  el("polygon", {points: poly(today), class:"g-cov"}, svg);
  if (best.some(v => v > 0)) el("polygon", {points: poly(best), class:"g-out"}, svg);
  el("circle", {cx:c, cy:c, r:4, class:"g-home"}, svg);
  const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; host.appendChild(tip);
  const wedge = i => { const [ax,ay] = pt(i*w, top), [bx,by] = pt((i+1)*w, top); return `M${c} ${c}L${ax} ${ay}A${rMax} ${rMax} 0 0 1 ${bx} ${by}Z`; };
  for (let i = 0; i < n; i++){
    const hit = el("path", {d: wedge(i), class:"g-hit"}, svg);
    hit.addEventListener("pointerenter", () => {
      sector.setAttribute("d", wedge(i)); sector.setAttribute("visibility", "visible");
      tip.innerHTML = `<div class="t">${pad3(i*w)}° to ${pad3((i+1)*w)}°</div>` + (today[i] ? `<b>${num(today[i])}</b> nm today` : "Nothing heard today") + (best[i] ? ` <small>· best ${num(best[i])} nm</small>` : "");
      tip.hidden = false;
      const r = svg.getBoundingClientRect(), [px,py] = pt(i*w + w/2, top*.55);
      tip.style.left = Math.min(r.width - tip.offsetWidth, Math.max(0, px/S*r.width - tip.offsetWidth/2)) + "px"; tip.style.top = (py/S*r.height - 44) + "px";
    });
    hit.addEventListener("pointerleave", () => { sector.setAttribute("visibility","hidden"); tip.hidden = true; });
  }
}

async function refreshCharts(){
  try {
    const [m, typ, cov] = await Promise.all([fetch("/api/metrics?hours=24&step=300").then(r => r.json()), fetch("/api/typical").then(r => r.json()), fetch("/api/coverage").then(r => r.json())]);
    metrics = m; typical = typ; coverage = cov;
  } catch(e){ return; }
  drawTraffic(); drawCoverage();
  if (lastStatus) refreshStatus();
}
let resizeTimer; window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawTraffic, 150); });

// ---------- history ----------
async function refreshHistory(){
  let rows;
  try { rows = await (await fetch("/api/history?hours=" + $("history-range").value)).json(); } catch(e){ return; }
  const wrap = $("history-wrap");
  if (!rows.length){ wrap.innerHTML = `<div class="empty">No sightings in this window yet.</div>`; return; }
  const now = Date.now()/1000;
  wrap.innerHTML = `<table><thead><tr><th>Flight</th><th>First seen</th><th>Last seen</th><th class="num">Duration</th><th class="num">Highest</th><th class="num">Fastest</th></tr></thead><tbody>${
    rows.map(r => `<tr>
      <td class="flight">${r.flight ? esc(r.flight) : ""}<span class="hex">${esc(r.hex.toUpperCase())}</span></td>
      <td>${fmtDate(r.first_seen)}</td>
      <td>${now - r.last_seen < 60 ? `<span class="inrange">In range</span>` : fmtDate(r.last_seen)}</td>
      <td class="num">${fmtDur(r.last_seen - r.first_seen)}</td>
      <td class="num"><span class="altbar" style="width:${r.max_alt ? Math.round(Math.min(r.max_alt, 45000)/45000*48) : 0}px"></span>${fmtAlt(r.max_alt)}</td>
      <td class="num">${r.max_gs != null ? Math.round(r.max_gs) + " kt" : "—"}</td></tr>`).join("")
  }</tbody></table>`;
}
$("history-range").addEventListener("change", refreshHistory);

refreshStatus(); refreshCharts(); refreshHistory();
setInterval(refreshStatus, 12000);
setInterval(renderUpdated, 5000);
setInterval(refreshCharts, 60000);
setInterval(refreshHistory, 60000);
