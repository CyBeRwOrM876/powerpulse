"use strict";
const $ = id => document.getElementById(id);
const el = (tag, props = {}, ...kids) => { const n = document.createElement(tag); Object.assign(n, props); kids.forEach(k => n.append(k)); return n; };
let token = sessionStorage.getItem("pp_token") || "";
let template = {}, businessId = null, lastAnalysis = null;
const money = x => x == null ? "n/a" : Math.round(x).toLocaleString("en-US");
const num = (x, d = 1) => x == null ? "n/a" : Number(x).toFixed(d);

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) };
  const r = await fetch("/api/v1" + path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (r.status === 204) return null;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function msg(id, text, bad) { const n = $(id); n.textContent = text; n.style.fontWeight = bad ? "bold" : "normal"; }

// ---------- auth ----------
async function authenticate(kind) {
  try {
    const d = await api("/auth/" + kind, { method: "POST", body: { email: $("email").value, password: $("password").value } });
    token = d.token; sessionStorage.setItem("pp_token", token); start();
  } catch (e) { msg("auth-msg", e.message, true); }
}
$("login").onclick = () => authenticate("login");
$("register").onclick = () => authenticate("register");
$("logout").onclick = () => { sessionStorage.removeItem("pp_token"); token = ""; location.reload(); };

function show(tab) {
  ["setup", "analysis", "live", "reports"].forEach(t => $("tab-" + t).hidden = t !== tab);
  if (tab === "reports") loadReports();
  if (tab === "live") loadLive();
}
document.querySelectorAll("nav [data-tab]").forEach(b => b.onclick = () => show(b.dataset.tab));

async function start() {
  try { await api("/me"); } catch { sessionStorage.removeItem("pp_token"); token = ""; return; }
  $("auth").hidden = true; $("nav").hidden = false;
  await initSetup(); await loadBusinesses(); show("setup");
}

// ---------- setup ----------
function fill(sel, items, valueKey, labelFn) { sel.replaceChildren(...items.map(i => el("option", { value: valueKey ? i[valueKey] : i, textContent: labelFn ? labelFn(i) : i }))); }
async function initSetup() {
  template = await api("/templates");
  fill($("b-template"), [{ k: "custom", l: "Custom (blank)" }, ...Object.entries(template).map(([k, v]) => ({ k, l: v.label }))], "k", i => i.l);
  const sch = await api("/schedules");
  fill($("b-schedule"), sch, "schedule_id", s => s.schedule_id + (s.date ? " (" + s.date + ")" : " (undated)"));
  $("b-schedule").onchange = loadRegions; $("b-region").onchange = loadGroups; $("b-group").onchange = loadMask;
  $("b-template").onchange = () => { $("eq-table").tBodies[0].replaceChildren(); (template[$("b-template").value]?.equipment || []).forEach(addRow); };
  await loadRegions();
  const a = await Promise.resolve({ tariff: 1.8, diesel_price: 30, batt_capex_per_kwh: 7000, pv_capex_per_kwp: 10000, inv_capex_per_kw: 3000, gen_capex_per_kw: 3500, discount: 0.15 });
  $("assump").replaceChildren(...Object.entries(a).map(([k, v]) => el("label", {}, k, el("input", { id: "as-" + k, type: "number", step: "any", value: v }))));
}
async function loadRegions() { fill($("b-region"), await api(`/schedules/${$("b-schedule").value}/regions`)); await loadGroups(); }
async function loadGroups() {
  const g = await api(`/schedules/${$("b-schedule").value}/groups?region=${encodeURIComponent($("b-region").value)}`);
  fill($("b-group"), g, "group_id", x => x.group_id + (x.category ? " (" + x.category + ")" : "")); await loadMask();
}
async function loadMask() {
  try {
    const m = await api(`/schedules/${$("b-schedule").value}/mask?region=${encodeURIComponent($("b-region").value)}&group_id=${encodeURIComponent($("b-group").value)}`);
    $("mask").replaceChildren(...m.grid_on.map((on, h) => el("div", { className: on ? "on" : "", textContent: String(h), title: h + ":00 " + (on ? "grid on" : "grid off") })));
    $("sched-warn").textContent = m.warnings.join(" ");
  } catch (e) { $("sched-warn").textContent = e.message; }
}
const COLS = [["name", "text"], ["power_kw", "number"], ["hours_per_day", "number"], ["start_hour", "number"], ["critical", "checkbox"], ["flexible", "checkbox"], ["value_per_hour", "number"], ["spoil_per_hour", "number"], ["channel", "number"]];
function addRow(e = {}) {
  const tr = el("tr");
  COLS.forEach(([k, t]) => {
    const i = el("input", { type: t, step: "any" });
    if (t === "checkbox") i.checked = !!e[k]; else i.value = e[k] ?? (t === "number" ? (k === "channel" ? "" : 0) : "");
    i.dataset.k = k; tr.append(el("td", {}, i));
  });
  tr.append(el("td", {}, el("button", { className: "secondary", textContent: "Remove", onclick: () => tr.remove() })));
  $("eq-table").tBodies[0].append(tr);
}
$("add-eq").onclick = () => addRow({ hours_per_day: 8, start_hour: 8, power_kw: 1 });
function readEquipment() {
  return [...$("eq-table").tBodies[0].rows].map(tr => { const o = {}; tr.querySelectorAll("input").forEach(i => {
    const k = i.dataset.k; o[k] = i.type === "checkbox" ? i.checked : i.type === "number" ? (i.value === "" ? null : Number(i.value)) : i.value; }); return o; });
}
$("save-business").onclick = async () => {
  const body = { name: $("b-name").value || "My business", sector: $("b-template").value, region: $("b-region").value, group_id: $("b-group").value,
    schedule_id: $("b-schedule").value, days_per_week: +$("b-days").value, tariff: +$("b-tariff").value, diesel_price: +$("b-diesel").value,
    existing_gen_kw: +$("b-gen").value, equipment: readEquipment() };
  try {
    const d = await api(businessId ? "/businesses/" + businessId : "/businesses", { method: businessId ? "PUT" : "POST", body });
    businessId = d.id; msg("setup-msg", "Saved. Open the Analysis tab to run it."); loadBusinesses();
  } catch (e) { msg("setup-msg", e.message, true); }
};
async function loadBusinesses() {
  const list = await api("/businesses");
  $("biz-list").replaceChildren(...list.map(b => el("li", {}, `${b.name} (${b.region}, group ${b.group_id}) `, el("button", { className: "secondary", textContent: "Use", onclick: () => { businessId = b.id; msg("setup-msg", "Selected: " + b.name); } }))));
  if (!businessId && list.length) businessId = list[0].id;
}

// ---------- analysis ----------
function assumptions() { const o = {}; document.querySelectorAll("[id^=as-]").forEach(i => o[i.id.slice(3)] = Number(i.value)); return o; }
$("run").onclick = async () => {
  if (!businessId) return msg("an-msg", "Save or select a business first.", true);
  msg("an-msg", "Running…");
  try {
    const d = await api(`/businesses/${businessId}/analyse`, { method: "POST", body: { assumptions: assumptions(), use_measured: $("use-measured").checked } });
    lastAnalysis = d; renderAnalysis(d); msg("an-msg", "");
  } catch (e) { msg("an-msg", e.message, true); }
};
function renderAnalysis(d) {
  $("an-out").hidden = false;
  const best = d.strategies.find(s => s.id === d.recommended_id), g = d.supply_gap;
  $("an-summary").replaceChildren(el("b", { textContent: "Recommended: " + best.label }), el("br"),
    `Your group is off the grid for ${g.grid_off_hours_per_day} hours a day, ${num(g.off_hours_while_operating_per_week, 0)} of them while you are working each week. ` +
    `Unprotected, that costs about ZMW ${money(g.baseline_annual_loss)} a year. ` + (best.payback_years != null ? `Payback on the recommended option is about ${num(best.payback_years)} years.` : ""),
    ...(d.warnings.concat(d.schedule_warnings || []).map(w => el("div", { className: "warn", textContent: w }))));
  $("an-table").tBodies[0].replaceChildren(...d.strategies.map(s => el("tr", {}, ...[s.label + (s.id === d.recommended_id ? " (recommended)" : ""), money(s.capex), money(s.total_annual_cost), money(s.unserved_loss),
    s.payback_years == null ? "no saving" : num(s.payback_years), money(s.diesel_litres_per_year), num(s.co2_tonnes_per_year, 2), s.critical_ok ? "yes" : "no"].map(t => el("td", { textContent: t })))));
  $("prio").replaceChildren(...d.priority.map(p => el("li", { textContent: `${p.name}${p.critical ? " (critical)" : ""}: ZMW ${money(p.loss_per_kw_hour)} lost per kW-hour unpowered` })));
  $("shift").replaceChildren(...d.shift_plan.map(p => el("li", { textContent: `${p.name}: move start from ${p.current_start}:00 to ${p.suggested_start}:00 (${p.hours} h)` })));
  const label = id => (d.strategies.find(s => s.id === id) || {}).label || id;
  $("sens").tBodies[0].replaceChildren(...(d.sensitivity || []).map(s => el("tr", {}, ...[s.scenario, label(s.recommended_id), s.changed ? "yes" : "no"].map(t => el("td", { textContent: t })))));
}
$("quote").onclick = async () => {
  try {
    const r = await api(`/businesses/${businessId}/quote-check`, { method: "POST", body: { battery_kwh: +$("q-batt").value, pv_kwp: +$("q-pv").value, inverter_kw: +$("q-inv").value, generator_kw: +$("q-gen").value, price_zmw: +$("q-price").value } });
    $("q-out").replaceChildren(el("li", { textContent: `Modelled need: battery ${r.modelled_need.battery_kwh} kWh, solar ${r.modelled_need.pv_kwp} kWp, inverter ${r.modelled_need.inverter_kw} kW. Price implied by assumptions: ZMW ${money(r.implied_price)}.` }),
      ...(r.flags.length ? r.flags : ["No flags raised on these inputs."]).map(f => el("li", { textContent: f })));
  } catch (e) { $("q-out").replaceChildren(el("li", { textContent: e.message })); }
};
$("make-report").onclick = async () => {
  try {
    const r = await api(`/businesses/${businessId}/reports`, { method: "POST", body: { assumptions: assumptions(), use_measured: $("use-measured").checked } });
    msg("rep-msg", ""); $("rep-msg").replaceChildren("Report created. ", el("a", { href: r.verify_url, target: "_blank", rel: "noopener", textContent: "Open the verification page" }));
  } catch (e) { msg("rep-msg", e.message, true); }
};

// ---------- live ----------
$("refresh").onclick = loadLive;
$("new-device").onclick = async () => {
  try { const d = await api(`/businesses/${businessId}/devices`, { method: "POST" });
    $("device-secret").hidden = false; $("device-secret").textContent = `DEVICE_ID  ${d.device_key}\nDEVICE_SECRET  ${d.secret_hex}\n${d.note}`;
  } catch (e) { alert(e.message); }
};
const when = ts => ts ? new Date(ts * 1000).toLocaleString() : "open";
async function loadLive() {
  if (!businessId) return;
  const live = await api(`/businesses/${businessId}/live`);
  $("live-cards").replaceChildren(...live.flatMap(d => d.latest ? [
    el("div", { className: "card" }, "Node " + d.device_key, el("b", { textContent: d.online ? "online" : "offline" }), "Mains " + num(d.latest.v) + " V, PF " + num(d.latest.pf, 2) + (d.latest.grid ? "" : " (grid off)")),
    ...d.latest.watts.map((w, i) => el("div", { className: "card" }, "Circuit " + (i + 1), el("b", { textContent: w + " W" })))] : [el("div", { className: "card", textContent: "Node " + d.device_key + ": no data yet" })]));
  const h = await api(`/businesses/${businessId}/history?hours=24`), mx = Math.max(1, ...h.map(p => p.watts));
  $("chart").replaceChildren(...h.map(p => { const b = el("div"); b.style.height = Math.max(1, p.watts / mx * 100) + "%"; b.title = new Date(p.ts * 1000).toLocaleTimeString() + ": " + Math.round(p.watts) + " W"; return b; }));
  const r = await api(`/businesses/${businessId}/reliability`);
  $("reli").textContent = r.hours_of_data < 24 ? "Not enough measured data yet (need 24 hours)." :
    r.scheduled_on_but_measured_off.length ? "Hours when the schedule says power is on but it was mostly off: " + r.scheduled_on_but_measured_off.join(", ") : "Measured supply matched the published schedule.";
  const o = await api(`/businesses/${businessId}/outages`);
  $("outages").tBodies[0].replaceChildren(...o.map(x => el("tr", {}, ...[when(x.start_ts), when(x.end_ts), x.minutes ?? "", x.source].map(t => el("td", { textContent: t })))));
}

// ---------- reports ----------
async function loadReports() {
  if (!businessId) return;
  const r = await api(`/businesses/${businessId}/reports`);
  $("report-list").replaceChildren(...r.map(x => el("li", {}, new Date(x.created * 1000).toLocaleString() + " ", el("a", { href: "/verify.html?id=" + x.id, target: "_blank", rel: "noopener", textContent: "verify and print" }))));
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
if (token) start();
