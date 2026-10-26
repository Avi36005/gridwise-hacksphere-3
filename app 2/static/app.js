"use strict";
const $ = (id) => document.getElementById(id);
const state = { allRows: [], rows: [], result: null, model: null, source: "Historical starter data · not live telemetry", busy: false, chartMetric: "demand", charts: {} };
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = (value, digits = 1) => Number(value).toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const hour = (stamp) => stamp.slice(11, 16);
const shortDate = (stamp) => new Date(stamp.replace(" ", "T") + "Z").toLocaleDateString("en-GB", {day:"numeric",month:"short",year:"numeric",timeZone:"UTC"});
function notice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, options) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch { throw new Error("The server returned an unreadable response."); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check the input values and try again.");
  return data;
}
function showView(view) {
  document.querySelectorAll(".view").forEach(el => el.hidden = el.id !== `view-${view}`);
  document.querySelectorAll(".nav-item").forEach(el => { el.classList.toggle("active", el.dataset.view === view); if(el.dataset.view === view) el.setAttribute("aria-current","page"); else el.removeAttribute("aria-current"); });
  $("breadcrumb").textContent = {forecast:"Overview",inputs:"Telemetry inputs",models:"Model performance"}[view];
  document.body.classList.remove("sidebar-open");$("sidebar-scrim").hidden=true;$("sidebar-toggle").setAttribute("aria-expanded","false");
  window.scrollTo({top:0,behavior:"instant"});
}
function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll("input, select, #run, #run-inputs, #reset-sample, #manual-form button").forEach(el => el.disabled = busy);
  $("run").innerHTML = busy ? "Forecasting…" : 'Run forecast <i class="ph ph-arrow-right" aria-hidden="true"></i>';
  $("run-inputs").innerHTML = busy ? "Forecasting…" : 'Run forecast <i class="ph ph-arrow-right" aria-hidden="true"></i>';
  $("export").disabled = busy || !state.result;
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
  }
  $("export").disabled=true;
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
    state.result=await api("/api/forecast",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({rows:state.rows,battery})});
    renderForecast(); showView("forecast");
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
  $("overload-banner").textContent=`Critical overload forecast: ${summary.overload_hours} hours at or above 200 kW. Review those hours in the table below.`;
  $("ending-soc").innerHTML=`${number(summary.ending_soc_pct,0)}<small>%</small>`;
  
  $("start-soc").textContent=`${number(battery.initial_soc_pct,0)}%`;
  $("reserve-label").textContent=`${number(battery.reserve_soc_pct,0)}%`;
  $("capacity-label").textContent=`${number(battery.capacity_kwh,0)} kWh`;
  $("battery-note").textContent=summary.backup_hours ? `Grid support needed in ${summary.backup_hours} storage hours because of reserve or power limits.` : "Storage can meet the requested dispatch under these assumptions.";
  $("model-caption").textContent=`${model.provider === "local"?"Local":"QuickML"} · ${model.name}`;
  renderChart(rows);
  renderSmallCharts(rows);
  $("timeline").innerHTML=rows.map((r,i)=>`<div class="timeline-hour ${r.grid_mode === 'renewable_storage'?'storage':''} ${r.critical_overload?'critical':''}" title="${escapeHtml(r.timestamp)} · ${escapeHtml(r.reason)}${r.critical_overload?' · Critical overload':''}"><div class="block"></div><span>${r.timestamp.slice(11,13)}</span></div>`).join("");
  renderForecastTable();
}
function renderForecastTable() {
  if(!state.result) return;
  const filter=$("row-filter").value;
  const rows=state.result.rows.filter(r=>filter==='all'||(filter==='overload'?r.critical_overload:r.grid_mode==='renewable_storage'));
  $("forecast-table").innerHTML=rows.length?rows.map(r=>`<tr><td><strong>${hour(r.timestamp)}</strong><span class="muted"> · ${shortDate(r.timestamp)}</span></td><td>${number(r.target_demand_kw)} kW</td><td><span class="mode-badge ${r.grid_mode==='renewable_storage'?'storage':''}" title="${escapeHtml(r.reason)}"><i class="dot ${r.grid_mode==='renewable_storage'?'green':'blue'}"></i>${r.grid_mode==='renewable_storage'?'Renewable storage':'Main grid'}</span></td><td><div class="soc-cell"><span class="soc-track"><i class="soc-fill" style="width:${r.battery_soc_pct}%"></i></span>${number(r.battery_soc_pct,0)}%</div></td><td>${r.critical_overload?'<span class="alert-badge">Critical overload</span>':r.storage_shortfall?'<span class="muted">Grid support needed</span>':'<span class="muted">—</span>'}</td></tr>`).join(""):'<tr><td colspan="5" class="empty">No hours match this filter.</td></tr>';
}
function renderModel(data) {
  state.model=data;
  $("provider-badge").textContent=data.provider==='quickml'?'QuickML provider':'Local model';
  const meta=data.local_training;
  if(!meta) { $("winner-name").textContent="No local model trained"; return; }
  $("winner-name").textContent=meta.model_name;
  $("training-period").textContent=`${number(meta.training_rows,0)} labeled rows · ${shortDate(meta.training_start)} – ${shortDate(meta.training_end)}`;
  $("holdout-score").textContent=number(meta.holdout.rmse_kw,2);
  $("baseline-score").textContent=number(meta.baseline_holdout.rmse_kw,2);
  const largest=Math.max(...meta.leaderboard.map(r=>r.cv_rmse_kw));
  $("model-table").innerHTML=meta.leaderboard.map((row,i)=>`<tr class="${i===0?'winner-row':''}"><td>${escapeHtml(row.name)}${i===0?'<span class="winner-label">SELECTED</span>':''}</td><td>${number(row.cv_rmse_kw,2)} kW<span class="model-comparison-bar" style="width:${row.cv_rmse_kw/largest*110}px"></span></td>${row.folds.map(f=>`<td>${number(f.rmse_kw,2)} kW</td>`).join("")}</tr>`).join("");
  if(data.provider==='quickml') $("quickml-state").textContent="The server is configured to use QuickML. Run a forecast to verify the published endpoint. Local scores shown here do not describe that endpoint.";
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
$("export").addEventListener("click",()=>{
  if(!state.result)return;
  const csv="timestamp,target_demand_kw\n"+state.result.rows.map(row=>`${row.timestamp},${row.target_demand_kw}`).join("\n")+"\n";
  const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
  const link=document.createElement("a");link.href=url;link.download="predictions.csv";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function init(){
  setBusy(true);
  try{
    const [model,sample]=await Promise.all([api("/api/model"),api("/api/sample")]);
    renderModel(model);loadRows(sample.rows,"Historical starter data · not live telemetry");notice("");
  }catch(err){error("Could not load the workspace. "+err.message);setBusy(false);return;}
  setBusy(false);await runForecast();
  if(location.hash==="#inputs")showView("inputs");
}
document.querySelectorAll('[data-chart]').forEach(button=>button.addEventListener('click',()=>{state.chartMetric=button.dataset.chart;document.querySelectorAll('[data-chart]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});if(state.result)renderChart(state.result.rows);}));
$('sidebar-toggle').addEventListener('click',()=>{const open=document.body.classList.toggle('sidebar-open');$('sidebar-scrim').hidden=!open;$('sidebar-toggle').setAttribute('aria-expanded',String(open));});
$('sidebar-scrim').addEventListener('click',()=>{document.body.classList.remove('sidebar-open');$('sidebar-scrim').hidden=true;$('sidebar-toggle').setAttribute('aria-expanded','false');});
init();
