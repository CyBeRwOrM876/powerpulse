"use strict";
const $ = id => document.getElementById(id);
const td = t => { const c = document.createElement("td"); c.textContent = t; return c; };
(async () => {
  const id = new URLSearchParams(location.search).get("id");
  try {
    const r = await (await fetch("/api/v1/public/reports/" + encodeURIComponent(id))).json();
    if (!r.report) throw new Error("Report not found");
    $("status").textContent = r.valid ? "Signature valid. The figures below are exactly as issued (key " + r.key_id + ")." : "SIGNATURE INVALID. Do not rely on this report.";
    $("status").style.fontWeight = "bold";
    const p = r.report, body = $("body");
    const h = (t, tag = "h2") => { const n = document.createElement(tag); n.textContent = t; body.append(n); };
    const para = t => { const n = document.createElement("p"); n.textContent = t; body.append(n); };
    h("Summary");
    para(`Generated ${p.generated_at}. Region ${p.business.region}, group ${p.business.group_id}, schedule ${p.business.schedule_id}. Engine ${p.engine_version}.`);
    para(`Hours without grid power while operating: ${p.supply_gap.off_hours_while_operating_per_week} per week. Annual loss if unprotected: ZMW ${Math.round(p.supply_gap.baseline_annual_loss).toLocaleString()}.`);
    h("Strategies");
    const t = document.createElement("table"); t.innerHTML = "";
    const head = t.insertRow(); ["Strategy", "Capital (ZMW)", "Annual cost (ZMW)", "Payback (years)", "Diesel (L/yr)", "CO2 (t/yr)"].forEach(x => { const c = document.createElement("th"); c.textContent = x; head.append(c); });
    p.strategies.forEach(s => { const row = t.insertRow(); [s.label + (s.id === p.recommended_id ? " (recommended)" : ""), Math.round(s.capex).toLocaleString(), Math.round(s.total_annual_cost).toLocaleString(), s.payback_years == null ? "no saving" : s.payback_years.toFixed(1), Math.round(s.diesel_litres_per_year).toLocaleString(), s.co2_tonnes_per_year.toFixed(2)].forEach(x => row.append(td(x))); });
    const w = document.createElement("div"); w.className = "tablewrap"; w.append(t); body.append(w);
    h("Provenance and assumptions");
    para("Assumptions changed by the owner: " + (p.provenance.assumptions_changed_by_owner.join(", ") || "none") + ". Measured data used: " + (p.provenance.measured_data_used ? "yes" : "no") + ". Price defaults are placeholders unless listed as changed.");
    if (p.schedule_warnings.length) para("Schedule notes: " + p.schedule_warnings.join(" "));
    para(p.statement);
    h("Signature", "h3"); para(r.signature);
  } catch (e) { $("status").textContent = "Could not verify: " + e.message; }
})();
