'use strict';
const flowExamples={solar:{grid_mode:'main_grid',storage_shortfall:false,solar_kw:100,battery_soc_pct:80},storage:{grid_mode:'renewable_storage',storage_shortfall:false,solar_kw:0,battery_soc_pct:60},grid:{grid_mode:'main_grid',storage_shortfall:false,solar_kw:0,battery_soc_pct:30}};
const descriptions={auto:'A simulated day: solar charges the battery, evening hours (18:00–22:00) draw on storage, nights use the main grid. Open the dashboard for model predictions.',solar:'Illustrative solar flow. Open the dashboard for model predictions.',storage:'Illustrative storage flow. Open the dashboard for model predictions.',grid:'Illustrative main grid flow. Open the dashboard for model predictions.'};
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('[data-mode]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  const mode=button.dataset.mode;
  if(mode==='auto')window.GridScene?.setAuto(true);else window.GridScene?.update(flowExamples[mode]);
  document.getElementById('scene-description').textContent=descriptions[mode];
}));
const reduceMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!reduceMotion&&'IntersectionObserver' in window){
  document.body.classList.add('motion-ready');
  const revealObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('visible');revealObserver.unobserve(entry.target);}}),{threshold:.08});
  document.querySelectorAll('.reveal').forEach(el=>revealObserver.observe(el));
  // Proof numbers count up once they scroll into view.
  const countObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(!entry.isIntersecting)return;countObserver.unobserve(entry.target);
    const node=entry.target.firstChild,end=parseInt(node.textContent,10),start=performance.now();
    const step=now=>{const t=Math.min(1,(now-start)/900);node.textContent=String(Math.round(end*(1-Math.pow(1-t,3))));if(t<1)requestAnimationFrame(step);};requestAnimationFrame(step);
  }),{threshold:.6});
  document.querySelectorAll('.proof-strip strong').forEach(el=>{if(el.firstChild&&/^\d+$/.test(el.firstChild.textContent))countObserver.observe(el);});
}
// Cards lean toward the pointer in 3D (mouse and pen only; touch scrolling is left alone).
if(!reduceMotion&&window.matchMedia('(hover: hover)').matches){
  document.querySelectorAll('.feature-card,.rule-card').forEach(card=>{
    card.addEventListener('pointermove',e=>{const r=card.getBoundingClientRect();card.style.setProperty('--ry',`${((e.clientX-r.left)/r.width-.5)*7}deg`);card.style.setProperty('--rx',`${(.5-(e.clientY-r.top)/r.height)*7}deg`);card.classList.add('tilting');});
    card.addEventListener('pointerleave',()=>{card.style.setProperty('--rx','0deg');card.style.setProperty('--ry','0deg');card.classList.remove('tilting');});
  });
}
