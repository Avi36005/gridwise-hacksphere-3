'use strict';
const flowExamples={solar:{grid_mode:'main_grid',storage_shortfall:false,solar_kw:100,battery_soc_pct:80},storage:{grid_mode:'renewable_storage',storage_shortfall:false,solar_kw:0,battery_soc_pct:60},grid:{grid_mode:'main_grid',storage_shortfall:false,solar_kw:0,battery_soc_pct:30}};
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('[data-mode]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  window.GridScene?.update(flowExamples[button.dataset.mode]);
  document.getElementById('scene-description').textContent=`Illustrative ${button.dataset.mode==='grid'?'main grid':button.dataset.mode} flow. Open the dashboard for model predictions.`;
}));
window.GridScene?.update(flowExamples.solar);
if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches&&'IntersectionObserver' in window){
  document.body.classList.add('motion-ready');
  const revealObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('visible');revealObserver.unobserve(entry.target);}}),{threshold:.08});
  document.querySelectorAll('.reveal').forEach(el=>revealObserver.observe(el));
}
