// Settings page: read-only view of /api/settings.
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SRC = {"settings.ini": ["file", "settings.ini"], "environment": ["env", "environment"], "default": ["", "default"]};

function fmtValue(row){
  const v = row.value;
  if (v === null || v === "") return '<span class="unset">not set</span>';
  if (row.type === "bool") return v ? "yes" : "no";
  return esc(v);
}

async function load(){
  let d;
  try { d = await (await fetch("/api/settings")).json(); }
  catch(e){ $("checks").innerHTML = '<li class="empty">Could not reach the dashboard service.</li>'; return; }

  $("problems").innerHTML = d.errors.map(e => `<div class="problem"><b>CAUTION</b>${esc(e)}</div>`).join("");

  $("checks").innerHTML = d.checks.map(c =>
    `<li class="${esc(c.state)}"><span class="dot" aria-hidden="true"></span><span class="name">${esc(c.name)}</span><span class="detail">${esc(c.detail)}</span></li>`
  ).join("");

  $("settings-path").textContent = d.settings_file;
  $("cmd-edit").textContent = "nano " + d.settings_file;
  $("settings-rows").innerHTML = d.settings.map(r => {
    const [cls, label] = SRC[r.source] || ["", r.source];
    return `<tr><td class="opt"><span class="key">${esc(r.key)}</span><span class="env">${esc(r.env)}</span>` +
      `<div class="desc">${esc(r.description)}</div></td>` +
      `<td class="val">${fmtValue(r)}<div class="src-row"><span class="src ${cls}">${label}</span></div></td></tr>`;
  }).join("");

  $("about").innerHTML = [
    ["Version", d.version], ["Python", d.python], ["Data folder", d.data_dir],
    ["Settings file", d.settings_file + (d.settings_file_exists ? "" : " (not created yet, so defaults apply)")],
  ].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
}

document.querySelectorAll("[data-copy]").forEach(btn => btn.addEventListener("click", async () => {
  const text = $(btn.dataset.copy).textContent;
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
  catch(e){ const r = document.createRange(); r.selectNodeContents($(btn.dataset.copy)); getSelection().removeAllRanges(); getSelection().addRange(r); btn.textContent = "Selected"; }
  setTimeout(() => btn.textContent = "Copy", 1500);
}));

load();
