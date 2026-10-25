"use strict";
const $ = (id) => document.getElementById(id);
const state = { allRows: [], rows: [], result: null, model: null, source: "Historical starter data · not live telemetry", busy: false, chartMetric: "demand", charts: {} };
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = (value, digits = 1) => Number(value).toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const hour = (stamp) => stamp.slice(11, 16);
const modeLabel = (mode) => mode === "renewable_storage" ? "Renewable Storage Mode" : "Main Grid Mode";
const shortDate = (stamp) => new Date(stamp.replace(" ", "T") + "Z").toLocaleDateString("en-GB", {day:"numeric",month:"short",year:"numeric",timeZone:"UTC"});
function notice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, options = {}) {
  // Guardrails: 60 s timeout, and up to two retries for network errors and gateway/cold-start responses.
  for (let attempt = 0; ; attempt++) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 60000);
    let response;
    try { response = await fetch(path, {...options, signal: controller.signal}); }
    catch (err) {
      clearTimeout(timer);
      if (attempt < 2) { await new Promise(r => setTimeout(r, 1500 * (attempt + 1))); continue; }
      throw new Error(err.name === "AbortError" ? "The server took too long to respond. Please try again." : "Cannot reach the server. Check your connection and try again.");
    }
    clearTimeout(timer);
    if ([502, 503, 504].includes(response.status) && attempt < 2) { await new Promise(r => setTimeout(r, 1500 * (attempt + 1))); continue; }
    let data;
    try { data = await response.json(); } catch { throw new Error("The server returned an unreadable response. Please try again."); }
    if (response.status === 429) throw new Error(data.detail || "Too many requests. Please wait a minute and try again.");
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check the input values and try again.");
    return data;
  }
}
function showView(view) {
  document.querySelectorAll(".view").forEach(el => el.hidden = el.id !== `view-${view}`);
  document.querySelectorAll(".nav-item").forEach(el => { el.classList.toggle("active", el.dataset.view === view); if(el.dataset.view === view) el.setAttribute("aria-current","page"); else el.removeAttribute("aria-current"); });
  $("breadcrumb").textContent = {forecast:"Overview",inputs:"Telemetry inputs",scenarios:"Scenario lab",models:"Model performance"}[view];
  document.body.classList.remove("sidebar-open");$("sidebar-scrim").hidden=true;$("sidebar-toggle").setAttribute("aria-expanded","false");
  window.scrollTo({top:0,behavior:"instant"});
}
function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll("input, select, #run, #run-inputs, #reset-sample, #manual-form button").forEach(el => el.disabled = busy);
  $("run").innerHTML = busy ? "Forecasting…" : 'Run forecast <i class="ph ph-arrow-right" aria-hidden="true"></i>';
  $("run-inputs").innerHTML = busy ? "Forecasting…" : 'Run forecast <i class="ph ph-arrow-right" aria-hidden="true"></i>';
  setExportable(!busy && !!state.result);
}
function setExportable(on) {
  $("export").setAttribute("aria-disabled", String(!on));
  if(!on) $("export-menu").open = false;
}
function dirty() {
  if(state.result) {
    state.result = null;
    ["peak","energy","storage","overloads"].forEach(id => $(id).textContent="—");
    $("peak-note").textContent="Across the forecast window";
    $("ending-soc").innerHTML="—<small>%</small>";
    Object.values(state.charts).forEach(chart=>chart.destroy()); state.charts={};
    $("demand-chart").innerHTML='<p class="chart-empty">Run a forecast for the updated inputs.</p>';
    $("timeline").innerHTML='<p class="muted">Run a forecast to view the updated schedule.</p>';
    $("forecast-table").innerHTML='<tr><td colspan="5" class="empty">Inputs changed. Run a new forecast.</td></tr>';
    $("battery-note").textContent="Waiting for an updated forecast.";
    $("overload-banner").hidden=true;
    resetLab("Run a forecast for the updated inputs to size the battery.");
  }
  setExportable(false);
  notice("Inputs changed. Run the forecast to update the results.");
}
function updateWindowLabel() {
  if(!state.rows.length) return;
  const first=state.rows[0].timestamp, last=state.rows[23].timestamp;
  $("window-label").textContent=`${shortDate(first)} ${hour(first)} → ${shortDate(last)} ${hour(last)}`;
  $("source-label").textContent=state.source;
}
function renderInputs() {
  $("input-table").innerHTML=state.rows.map((row,i)=>`<tr><td>${escapeHtml(row.timestamp)}</td>${["ambient_temp_c","humidity_pct","solar_irradiance_wm2"].map((key)=>`<td><input type="number" step="any" data-row="${i}" data-key="${key}" aria-label="${key} at ${escapeHtml(row.timestamp)}" value="${row[key] == null ? "" : escapeHtml(row[key])}" ${key === "humidity_pct" ? 'min="0" max="100"' : key === "solar_irradiance_wm2" ? 'min="0"' : ""}></td>`).join("")}</tr>`).join("");
  updateWindowLabel();
}
function selectWindow(index) {
  state.rows=state.allRows.slice(index,index+24).map(row=>({...row}));
  renderInputs(); dirty();
}
function loadRows(rows, source) {
  state.allRows=rows; state.source=source;
  $("window-start").innerHTML=rows.slice(0,rows.length-23).map((row,i)=>`<option value="${i}">${escapeHtml(row.timestamp)}</option>`).join("");
  selectWindow(0);
  $("manual-start").value=rows[0].timestamp.replace(" ","T").slice(0,16);
}
function batteryConfig() {
  const mapping={capacity_kwh:"capacity",initial_soc_pct:"initial",reserve_soc_pct:"reserve",solar_capacity_kw:"solar",max_charge_kw:"charge",max_discharge_kw:"discharge"};
  const config={efficiency:0.95};
  for(const [key,id] of Object.entries(mapping)) {
    const value=$("battery-"+id).value;
    if(value === "" || !Number.isFinite(Number(value))) throw new Error("Fill in every battery setting with a number.");
    config[key]=Number(value);
  }
  return config;
}
async function runForecast() {
  if(state.busy) return;
  error(""); notice("");
  try {
    const battery=batteryConfig();
    setBusy(true);
    state.result=await api("/api/forecast",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({rows:state.rows,battery,source:state.source.slice(0,120)})});
    renderForecast(); showView("forecast"); loadScenarios();
    if(state.result.persisted) setTimeout(loadRuns,2500);
    if(state.result.warnings.length) notice(state.result.warnings.join(" "));
  } catch(err) { error(err.message || "Forecast failed. Please try again."); }
  finally { setBusy(false); }
}
function renderChart(rows) {
  if(state.charts.demand) state.charts.demand.destroy();
  const battery=state.chartMetric==='battery';
  $("chart-title").innerHTML=`<i class="ph ph-chart-line-up" aria-hidden="true"></i> ${battery?'Battery charge':'Demand forecast'}`;
  $("chart-subtitle").textContent=battery?'Simulated state of charge across the day':'Hourly demand and switching thresholds';
  $("chart-legend").innerHTML=battery?'<span><i class="line-swatch"></i> Battery charge</span><span><i class="dash-swatch"></i> Reserve floor</span>':'<span><i class="line-swatch"></i> Predicted demand</span><span><i class="dash-swatch"></i> Storage · 160 kW</span><span><i class="dash-swatch red"></i> Overload · 200 kW</span>';
  $("demand-chart").innerHTML='<canvas id="forecast-canvas" aria-label="Interactive hourly forecast chart"></canvas>';
  Chart.defaults.font.family='Geist, sans-serif';
  const datasets=[{label:battery?'Battery charge':'Demand',data:rows.map(r=>battery?r.battery_soc_pct:r.target_demand_kw),borderColor:'#111111',backgroundColor:'#11111108',borderWidth:2.5,fill:true,tension:.28,pointRadius:2,pointHoverRadius:5}];
  const threshold=(label,value,color)=>({label,data:rows.map(()=>value),borderColor:color,borderDash:[4,5],borderWidth:1,pointRadius:0,fill:false});
  if(battery) datasets.push(threshold('Reserve',state.result.battery.reserve_soc_pct,'#777777'));
  else datasets.push(threshold('Storage threshold',160,'#888888'),threshold('Overload threshold',200,'#444444'));
  state.charts.demand=new Chart($("forecast-canvas"),{type:'line',data:{labels:rows.map(r=>hour(r.timestamp)),datasets},options:{responsive:true,maintainAspectRatio:false,animation:window.matchMedia('(prefers-reduced-motion: reduce)').matches?false:{duration:280,easing:'easeOutQuart'},interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{backgroundColor:'#111111',titleFont:{size:13},bodyFont:{size:13},padding:12,cornerRadius:9,callbacks:{label:ctx=>`${ctx.dataset.label}: ${number(ctx.parsed.y)} ${battery?'%':'kW'}`}}},scales:{x:{grid:{display:false},border:{display:false},ticks:{maxTicksLimit:8,maxRotation:0,color:'#555555',font:{size:12}}},y:{beginAtZero:true,max:battery?100:undefined,grid:{color:'#eeeeee'},border:{display:false},ticks:{maxTicksLimit:5,color:'#555555',font:{size:12}}}}}});
}
function renderSmallCharts(rows) {
  const colors=['#222222','#444444','#666666','#333333'];
  const data=[rows.map(r=>r.target_demand_kw),rows.map((_,i)=>rows.slice(0,i+1).reduce((s,r)=>s+r.target_demand_kw,0)),rows.map(r=>r.battery_soc_pct),rows.map(r=>r.critical_overload?1:0)];
  data.forEach((values,i)=>{if(state.charts['spark'+i])state.charts['spark'+i].destroy();state.charts['spark'+i]=new Chart($('spark-'+i),{type:'line',data:{labels:values.map((_,j)=>j),datasets:[{data:values,borderColor:colors[i],backgroundColor:colors[i]+'15',borderWidth:1.7,pointRadius:0,fill:true,tension:.35}]},options:{responsive:true,maintainAspectRatio:false,animation:false,events:[],plugins:{legend:{display:false},tooltip:{enabled:false}},scales:{x:{display:false},y:{display:false}}}});});
  if(state.charts.ring)state.charts.ring.destroy();
  const soc=state.result.summary.ending_soc_pct;
  state.charts.ring=new Chart($('battery-ring'),{type:'doughnut',data:{datasets:[{data:[soc,100-soc],backgroundColor:['#222222','#eeeeee'],borderWidth:0,borderRadius:6}]},options:{responsive:true,maintainAspectRatio:false,animation:false,cutout:'86%',rotation:-100,circumference:280,events:[],plugins:{legend:{display:false},tooltip:{enabled:false}}}});
}
function renderForecast() {
  const {rows,summary,battery,model}=state.result;
  $("peak").textContent=number(summary.peak_demand_kw);
  $("peak-note").textContent=`Peak at ${hour(summary.peak_timestamp)} · ${shortDate(summary.peak_timestamp)}`;
  $("energy").textContent=number(summary.energy_kwh,0);
  $("storage").textContent=summary.storage_hours;
  $("overloads").textContent=summary.overload_hours;
  $("overload-banner").hidden=!summary.overload_hours;
  const firstAlert=rows.find(r=>r.critical_overload);
  $("overload-banner").textContent=firstAlert?`Critical Overload Alert: ${summary.overload_hours} forecast hours at or above 200 kW, starting ${hour(firstAlert.timestamp)} on ${shortDate(firstAlert.timestamp)}. Review them in the table below.`:"";
  const first=rows[0];
  $("active-mode").textContent=modeLabel(first.grid_mode);
  $("active-mode").className=`mode-badge ${first.grid_mode==='renewable_storage'?'storage':''}`;
  $("active-hour").textContent=`${hour(first.timestamp)} · ${shortDate(first.timestamp)}`;
  $("active-demand").textContent=`${number(first.target_demand_kw)} kW`;
  $("active-alert").innerHTML=first.critical_overload?'<span class="alert-badge">Critical Overload Alert</span>':'<span class="muted">No alert</span>';
  renderLog(rows);
  $("ending-soc").innerHTML=`${number(summary.ending_soc_pct,0)}<small>%</small>`;
  
  $("start-soc").textContent=`${number(battery.initial_soc_pct,0)}%`;
  $("reserve-label").textContent=`${number(battery.reserve_soc_pct,0)}%`;
  $("capacity-label").textContent=`${number(battery.capacity_kwh,0)} kWh`;
  $("battery-note").textContent=summary.backup_hours ? `Grid support needed in ${summary.backup_hours} storage hours because of reserve or power limits.` : "Storage can meet the requested dispatch under these assumptions.";
  $("model-caption").textContent=`${({quickml:"QuickML",local:"Local","local-backup":"Backup",datastore:"Data Store replay"})[model.provider]||model.provider} · ${model.name}`;
  if(model.provider==="local-backup") $("provider-badge").textContent="Backup model · QuickML unavailable";
  renderChart(rows);
  renderSmallCharts(rows);
  $("timeline").innerHTML=rows.map((r,i)=>`<div class="timeline-hour ${r.grid_mode === 'renewable_storage'?'storage':''} ${r.critical_overload?'critical':''}" title="${escapeHtml(r.timestamp)} · ${escapeHtml(r.reason)}${r.critical_overload?' · Critical Overload Alert':''}"><div class="block"></div><span>${r.timestamp.slice(11,13)}</span></div>`).join("");
  renderForecastTable();
}
function renderForecastTable() {
  if(!state.result) return;
  const filter=$("row-filter").value;
  const rows=state.result.rows.filter(r=>filter==='all'||(filter==='overload'?r.critical_overload:r.grid_mode==='renewable_storage'));
  $("forecast-table").innerHTML=rows.length?rows.map(r=>`<tr><td><strong>${hour(r.timestamp)}</strong><span class="muted"> · ${shortDate(r.timestamp)}</span></td><td>${number(r.target_demand_kw)} kW</td><td><span class="mode-badge ${r.grid_mode==='renewable_storage'?'storage':''}" title="${escapeHtml(r.reason)}"><i class="dot ${r.grid_mode==='renewable_storage'?'green':'blue'}"></i>${modeLabel(r.grid_mode)}</span></td><td><div class="soc-cell"><span class="soc-track"><i class="soc-fill" style="width:${r.battery_soc_pct}%"></i></span>${number(r.battery_soc_pct,0)}%</div></td><td>${r.critical_overload?'<span class="alert-badge">Critical Overload Alert</span>':r.storage_shortfall?'<span class="muted">Grid support needed</span>':'<span class="muted">—</span>'}</td></tr>`).join(""):'<tr><td colspan="5" class="empty">No hours match this filter.</td></tr>';
}
function renderLog(rows) {
  const events=[];
  rows.forEach((r,i)=>{
    const prev=rows[i-1];
    if(!prev||prev.grid_mode!==r.grid_mode) events.push({stamp:r.timestamp,kind:'switch',text:`${prev?'Switch to':'Start in'} ${modeLabel(r.grid_mode)}`,why:r.reason});
    if(r.critical_overload&&!(prev&&prev.critical_overload)) events.push({stamp:r.timestamp,kind:'alert',text:'Critical Overload Alert triggered',why:`${number(r.target_demand_kw)} kW ≥ 200 kW`});
    if(!r.critical_overload&&prev&&prev.critical_overload) events.push({stamp:r.timestamp,kind:'clear',text:'Critical Overload Alert cleared',why:`${number(r.target_demand_kw)} kW < 200 kW`});
  });
  $("transition-count").textContent=String(events.filter(e=>e.kind==='switch').length-1);
  $("transition-log").innerHTML=events.map(e=>`<li class="log-${e.kind}"><time>${hour(e.stamp)} · ${shortDate(e.stamp)}</time><strong>${escapeHtml(e.text)}</strong><span>${escapeHtml(e.why)}</span></li>`).join("");
}
function resetLab(message) {
  state.lab=null;
  $("lab-headline").textContent=message; $("lab-detail").textContent=""; $("lab-apply").hidden=true;
  $("lab-power").textContent="—"; $("lab-shortfall").textContent="—";
  $("lab-table").innerHTML='<tr><td colspan="6" class="empty">No forecast yet.</td></tr>';
}
async function loadScenarios() {
  if(!state.result) return;
  try {
    const rows=state.result.rows.map(r=>({timestamp:r.timestamp,ambient_temp_c:r.ambient_temp_c,humidity_pct:r.humidity_pct,solar_irradiance_wm2:r.solar_irradiance_wm2,target_demand_kw:r.target_demand_kw}));
    state.lab=await api("/api/scenarios",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({rows,battery:state.result.battery})});
    renderLab();
  } catch(err) { resetLab(`Battery sizing is unavailable: ${err.message}`); }
}
function renderLab() {
  const {scenarios,recommendation:rec,battery}=state.lab;
  $("lab-power").textContent=number(rec.required_discharge_kw);
  $("lab-shortfall").textContent=rec.current_shortfall_hours;
  $("lab-current").textContent=`storage hours needing grid support with ${number(battery.capacity_kwh,0)} kWh / ${number(battery.max_discharge_kw,0)} kW`;
  const covered=rec.current_shortfall_hours===0;
  if(rec.capacity_kwh===null) {
    $("lab-headline").textContent="No battery size covers every storage hour under these assumptions.";
    $("lab-detail").textContent="Charging from the configured solar capacity cannot refill storage fast enough. Raise PV capacity or the starting charge on Telemetry inputs.";
  } else if(covered && !rec.raise_discharge) {
    $("lab-headline").textContent=`Your ${number(battery.capacity_kwh,0)} kWh battery already covers every storage hour.`;
    $("lab-detail").textContent=`The smallest battery that would still cover them is ${number(rec.capacity_kwh,0)} kWh at ${number(rec.discharge_kw,0)} kW.`;
  } else {
    $("lab-headline").textContent=`${number(rec.capacity_kwh,0)} kWh at ${number(rec.discharge_kw,0)} kW covers every storage hour.`;
    $("lab-detail").textContent=rec.raise_discharge?`The current ${number(battery.max_discharge_kw,0)} kW discharge rating is below the largest deficit, so the rating is raised first; then capacity is sized to the nearest kWh.`:"Sized to the nearest kWh with every other assumption unchanged.";
  }
  $("lab-apply").hidden=rec.capacity_kwh===null || (covered && !rec.raise_discharge);
  const best=rec.capacity_kwh;
  $("lab-table").innerHTML=scenarios.map(r=>`<tr class="${r.capacity_kwh===battery.capacity_kwh?'lab-current':''} ${r.shortfall_hours===0?'lab-best':''}"><td>${number(r.capacity_kwh,0)} kWh${r.capacity_kwh===battery.capacity_kwh?'<span class="winner-label">CURRENT</span>':''}</td><td>${r.shortfall_hours}</td><td>${number(r.grid_backup_kwh)} kWh</td><td>${number(r.battery_delivered_kwh)} kWh</td><td>${number(r.min_soc_pct,0)}%</td><td>${number(r.ending_soc_pct,0)}%</td></tr>`).join("");
}
function download(name, text, type) {
  const url=URL.createObjectURL(new Blob([text],{type}));
  const link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function exportResult(kind) {
  if(!state.result) return;
  const rows=state.result.rows, day=rows[0].timestamp.slice(0,10);
  const csvCell=v=>/[",\n]/.test(String(v))?`"${String(v).replace(/"/g,'""')}"`:String(v);
  if(kind==="predictions") download("predictions.csv","timestamp,target_demand_kw\n"+rows.map(r=>`${r.timestamp},${r.target_demand_kw}`).join("\n")+"\n","text/csv;charset=utf-8");
  if(kind==="schedule") download(`gridwise-schedule-${day}.csv`,["timestamp,target_demand_kw,grid_mode,critical_overload_alert,battery_soc_pct,grid_supply_kw,battery_discharge_kw,reason",...rows.map(r=>[r.timestamp,r.target_demand_kw,modeLabel(r.grid_mode),r.critical_overload,r.battery_soc_pct,r.grid_supply_kw,r.battery_discharge_kw,r.reason].map(csvCell).join(","))].join("\n")+"\n","text/csv;charset=utf-8");
  if(kind==="commands") download(`gridwise-commands-${day}.json`,JSON.stringify({generated_by:"Gridwise",model:state.result.model,thresholds:{storage_kw_above:160,evening_window:"18:00-22:00 inclusive",overload_kw_at_or_above:200},commands:rows.map(r=>({at:r.timestamp,set_mode:r.grid_mode==="renewable_storage"?"RENEWABLE_STORAGE":"MAIN_GRID",critical_overload_alert:r.critical_overload,predicted_demand_kw:r.target_demand_kw,reason:r.reason}))},null,2)+"\n","application/json");
  $("export-menu").open=false;
}
async function replayRun(id) {
  if(state.busy) return;
  error(""); notice("");
  try {
    const battery=batteryConfig();
    setBusy(true);
    state.result=await api(`/api/runs/${encodeURIComponent(id)}/replay`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({battery})});
    state.allRows=state.rows=state.result.rows.map(r=>({timestamp:r.timestamp,ambient_temp_c:r.ambient_temp_c,humidity_pct:r.humidity_pct,solar_irradiance_wm2:r.solar_irradiance_wm2}));
    state.source=`Saved run …${String(id).slice(-4)} · Catalyst Data Store`;
    $("window-start").innerHTML=`<option value="0">${escapeHtml(state.rows[0].timestamp)}</option>`;
    renderInputs(); renderForecast(); loadScenarios(); window.scrollTo({top:0,behavior:"smooth"});
    notice(state.result.warnings.join(" "));
  } catch(err) { error(err.message || "Could not reopen that run."); }
  finally { setBusy(false); }
}
async function loadRuns(){
  try{
    const data=await api("/api/runs");
    if(!data.enabled){$("runs-subtitle").textContent="Run history is saved to Catalyst Data Store on the deployed app; this environment has no Catalyst credentials.";$("runs-table").innerHTML='<tr><td colspan="8" class="empty">Persistence is off here.</td></tr>';$("events-log").innerHTML="";return;}
    if(data.error){$("runs-table").innerHTML=`<tr><td colspan="8" class="empty">${escapeHtml(data.error)}</td></tr>`;return;}
    $("runs-table").innerHTML=data.runs.length?data.runs.map(r=>`<tr><td>${escapeHtml(String(r.CREATEDTIME).slice(0,16))}</td><td>${escapeHtml(r.source)}</td><td>${escapeHtml(hour(r.window_start))} · ${shortDate(r.window_start)} → ${escapeHtml(hour(r.window_end))}</td><td>${escapeHtml(r.model)}</td><td>${number(r.peak_kw)} kW</td><td>${escapeHtml(r.storage_hours)}</td><td>${escapeHtml(r.overload_hours)}</td><td><button class="run-open" data-run="${escapeHtml(r.ROWID)}" aria-label="Reopen run saved ${escapeHtml(String(r.CREATEDTIME).slice(0,16))}"><i class="ph ph-arrows-clockwise" aria-hidden="true"></i> Open</button></td></tr>`).join(""):'<tr><td colspan="8" class="empty">No runs saved yet.</td></tr>';
    $("events-log").innerHTML=data.events.map(e=>{const [title,...rest]=String(e.message).split(": ");return `<li class="log-${escapeHtml(e.event_type)}"><time>${escapeHtml(hour(e.event_hour))} · ${shortDate(e.event_hour)}</time><strong>${escapeHtml(title)}</strong><span>${escapeHtml(rest.join(": "))} · run …${escapeHtml(String(e.run_id).slice(-4))}</span></li>`;}).join("");
  }catch(err){$("runs-table").innerHTML=`<tr><td colspan="8" class="empty">${escapeHtml(err.message)}</td></tr>`;}
}
function renderModel(data) {
  state.model=data;
  const q=data.quickml, live=data.provider==='quickml';
  const both=live&&(data.members||[]).length>1;
  $("provider-badge").textContent=!live?'Local model':both?'QuickML · CatBoost + Linear':`QuickML · ${q?q.deployed.algorithm.replace(' Regression',''):'endpoint'}`;
  $("quickml-state").textContent=live?"This server calls the published endpoint for every forecast on the Overview and for predict.py.":"This server is currently using the bundled local model (MODEL_PROVIDER=local); set MODEL_PROVIDER=quickml to call the published endpoint.";
  if(!q){$("winner-name").textContent="Model evidence unavailable";return;}
  const ens=q.ensemble;
  if(ens&&live&&(data.members||[]).length>1){
    $("deployed-pill").textContent="QuickML · CatBoost + Linear";
    $("models-intro").textContent="Every forecast is the average of two models trained and published in Zoho Catalyst QuickML: CatBoost regression and a linear regression on hourly cooling features.";
    $("ensemble-panel").hidden=false;
    $("ensemble-method").textContent=ens.method;
    $("ensemble-table").innerHTML=ens.models.map((r,i)=>`<tr class="${r.served?'winner-row':''}"><td>${escapeHtml(r.name)}${r.served?'<span class="winner-label">SERVED</span>':''}</td>${r.rmse_kw.map(v=>`<td>${number(v,2)} kW</td>`).join("")}<td><strong>${number(r.rmse_kw.reduce((a,b)=>a+b,0)/r.rmse_kw.length,2)} kW</strong></td></tr>`).join("");
  } else { $("deployed-pill").textContent=`QuickML · ${q.deployed.algorithm.replace(' Regression','')}`; }
  const cv=q.quickml_cv.models, deployed=cv[0], bar=(v,max)=>`<span class="model-comparison-bar" style="width:${v/max*110}px"></span>`;
  $("winner-name").textContent=q.deployed.algorithm;
  $("training-period").textContent=`Pipeline ${q.deployed.pipeline} · endpoint ${q.deployed.endpoint} · ${number(q.deployed.training_rows,0)} training hours · ${q.deployed.settings}`;
  $("holdout-score").textContent=number(deployed.cv_rmse_kw,2);
  $("latest-block").textContent=`kW mean · latest date block ${number(deployed.folds_kw[deployed.folds_kw.length-1],2)} kW`;
  $("baseline-score").textContent=number(q.baseline_holdout_rmse_kw,2);
  $("quickml-cv-method").textContent=q.quickml_cv.method;
  const maxCv=Math.max(...cv.map(r=>r.cv_rmse_kw));
  $("quickml-table").innerHTML=cv.map((r,i)=>`<tr class="${i===0?'winner-row':''}"><td>${escapeHtml(r.name)}${i===0?'<span class="winner-label">DEPLOYED</span>':''}</td><td>${number(r.cv_rmse_kw,2)} kW${bar(r.cv_rmse_kw,maxCv)}</td>${r.folds_kw.map(f=>`<td>${number(f,2)}</td>`).join("")}</tr>`).join("");
  const sel=q.local_selection.models, maxSel=Math.max(...sel.map(r=>r.cv_rmse_kw));
  $("selection-method").textContent=q.local_selection.method;
  $("model-table").innerHTML=sel.map((r,i)=>`<tr class="${i===0?'winner-row':''}"><td>${escapeHtml(r.name)}${i===0?'<span class="winner-label">SELECTED</span>':''}<br><span class="muted">${escapeHtml(r.params)}</span></td><td>${number(r.cv_rmse_kw,2)} kW${bar(r.cv_rmse_kw,maxSel)}</td>${r.folds_kw.map(f=>`<td>${number(f,2)}</td>`).join("")}<td>${number(r.holdout_rmse_kw,2)} kW</td></tr>`).join("");
}
document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>showView(button.dataset.view)));
$("run").addEventListener("click",runForecast);
$("run-inputs").addEventListener("click",runForecast);
$("row-filter").addEventListener("change",renderForecastTable);
$("window-start").addEventListener("change",event=>selectWindow(Number(event.target.value)));
$("battery-edit").addEventListener("click",()=>{showView("inputs");$("battery-capacity").focus();});
$("input-table").addEventListener("input",event=>{
  const input=event.target;if(!input.dataset.key)return;
  state.rows[Number(input.dataset.row)][input.dataset.key]=input.value===""?null:Number(input.value);
  state.source="Edited hourly inputs";updateWindowLabel();dirty();
});
document.querySelectorAll('[id^="battery-"] input, input[id^="battery-"]').forEach(input=>input.addEventListener("input",dirty));
$("csv-file").addEventListener("change",async(event)=>{
  const file=event.target.files[0];if(!file)return;
  error("");
  try {
    if(file.size>1024*1024)throw new Error("CSV is too large. Maximum size is 1 MB.");
    setBusy(true);
    const data=await api("/api/inputs/csv",{method:"POST",headers:{"Content-Type":"text/csv"},body:file});
    loadRows(data.rows,`Uploaded: ${file.name}`);
    $("upload-status").textContent=`${file.name} · ${number(data.rows.length,0)} rows${data.missing_values?` · ${data.missing_values} blank values`:''}`;
  }catch(err){error(err.message);}finally{setBusy(false);event.target.value="";}
});
$("reset-sample").addEventListener("click",async()=>{
  error("");setBusy(true);
  try{const data=await api("/api/sample");loadRows(data.rows,"Historical starter data · not live telemetry");$("upload-status").textContent="Using the supplied historical dataset.";}catch(err){error(err.message);}finally{setBusy(false);}
});
$("manual-form").addEventListener("submit",event=>{
  event.preventDefault();error("");
  const date=new Date($("manual-start").value+":00Z");
  if(Number.isNaN(date.valueOf()) || date.getUTCMinutes()!==0){error("Choose a valid starting time on the hour.");return;}
  const rows=Array.from({length:24},(_,i)=>({timestamp:new Date(date.getTime()+i*3600000).toISOString().slice(0,19).replace("T"," "),ambient_temp_c:Number($("manual-temp").value),humidity_pct:Number($("manual-humidity").value),solar_irradiance_wm2:Number($("manual-solar").value)}));
  loadRows(rows,"Manual scenario · repeated weather assumptions");$("upload-status").textContent="Manual 24-hour scenario. Edit hourly values below.";
});
document.querySelectorAll("[data-export]").forEach(button=>button.addEventListener("click",()=>exportResult(button.dataset.export)));
document.addEventListener("click",event=>{if(!$("export-menu").contains(event.target))$("export-menu").open=false;});
$("runs-table").addEventListener("click",event=>{const button=event.target.closest("[data-run]");if(button)replayRun(button.dataset.run);});
$("lab-apply").addEventListener("click",()=>{
  if(!state.lab) return;
  const rec=state.lab.recommendation;
  $("battery-capacity").value=rec.capacity_kwh; $("battery-discharge").value=Math.max(Number($("battery-discharge").value),rec.discharge_kw);
  runForecast();
});
async function init(){
  setBusy(true);
  try{
    const [model,sample]=await Promise.all([api("/api/model"),api("/api/sample")]);
    renderModel(model);loadRows(sample.rows,"Historical starter data · not live telemetry");notice("");
  }catch(err){error("Could not load the workspace. "+err.message);setBusy(false);return;}
  setBusy(false);await runForecast();
  loadRuns();
  if(location.hash==="#inputs")showView("inputs");
}
document.querySelectorAll('[data-chart]').forEach(button=>button.addEventListener('click',()=>{state.chartMetric=button.dataset.chart;document.querySelectorAll('[data-chart]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});if(state.result)renderChart(state.result.rows);}));
$('sidebar-toggle').addEventListener('click',()=>{const open=document.body.classList.toggle('sidebar-open');$('sidebar-scrim').hidden=!open;$('sidebar-toggle').setAttribute('aria-expanded',String(open));});
$('sidebar-scrim').addEventListener('click',()=>{document.body.classList.remove('sidebar-open');$('sidebar-scrim').hidden=true;$('sidebar-toggle').setAttribute('aria-expanded','false');});
init();
