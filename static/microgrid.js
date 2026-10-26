/* A live, procedural 3D visualization of the selected forecast hour. */
(() => {
  "use strict";
  const host = document.getElementById("microgrid-scene");
  if (!host || !window.THREE) return;
  const T = window.THREE;
  let renderer;
  try {
    renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch {
    host.innerHTML = '<p class="scene-fallback">3D preview is unavailable in this browser. The forecast and hourly controls still work.</p>';
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = .9;
  host.appendChild(renderer.domElement);
  const scene = new T.Scene();
  const camera = new T.OrthographicCamera(-5, 5, 4, -4, .1, 100);
  camera.position.set(9, 8, 10);
  camera.lookAt(0, .3, 0);
  const world = new T.Group();
  scene.add(world);
  const ambient = new T.HemisphereLight(0xffffff, 0x999999, 1.3);
  scene.add(ambient);
  const key = new T.DirectionalLight(0xffffff, 2.7);
  key.position.set(-4, 8, 5);key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left=-7;key.shadow.camera.right=7;key.shadow.camera.top=7;key.shadow.camera.bottom=-7;
  key.shadow.normalBias = .035;key.shadow.radius=5;
  scene.add(key);
  const fill = new T.DirectionalLight(0xffffff, .7);fill.position.set(5,3,-5);scene.add(fill);
  const material = (color, props={}) => new T.MeshStandardMaterial({color,roughness:.52,metalness:.12,...props});
  const white=material(0xfafafa), side=material(0xbcbcbc), purple=material(0x111111), dark=material(0x151515), steel=material(0x777777,{metalness:.5,roughness:.35});
  const pane=material(0x424242,{metalness:.25,roughness:.2});
  const green=material(0x222222), amber=material(0x555555);
  function box(w,h,d,mat,x,y,z,parent=world) {
    const m=new T.Mesh(new T.BoxGeometry(w,h,d),mat);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m;
  }
  function cylinder(rt,rb,h,mat,x,y,z,parent=world) {
    const m=new T.Mesh(new T.CylinderGeometry(rt,rb,h,32),mat);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m;
  }
  function rod(a,b,r,mat,parent=world) {
    const start=new T.Vector3(...a),end=new T.Vector3(...b),delta=end.clone().sub(start);
    const m=new T.Mesh(new T.CylinderGeometry(r,r,delta.length(),10),mat);m.position.copy(start.add(end).multiplyScalar(.5));m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),delta.normalize());m.castShadow=true;parent.add(m);return m;
  }
  function roundedDeck(w,d,r,depth,mat,y) {
    const s=new T.Shape(), x=-w/2,z=-d/2;
    s.moveTo(x+r,z);s.lineTo(x+w-r,z);s.quadraticCurveTo(x+w,z,x+w,z+r);s.lineTo(x+w,z+d-r);s.quadraticCurveTo(x+w,z+d,x+w-r,z+d);s.lineTo(x+r,z+d);s.quadraticCurveTo(x,z+d,x,z+d-r);s.lineTo(x,z+r);s.quadraticCurveTo(x,z,x+r,z);
    const g=new T.ExtrudeGeometry(s,{depth,bevelEnabled:true,bevelSegments:3,steps:1,bevelSize:.04,bevelThickness:.04,curveSegments:20});
    const m=new T.Mesh(g,mat);m.rotation.x=-Math.PI/2;m.position.y=y;m.castShadow=true;m.receiveShadow=true;world.add(m);return m;
  }
  roundedDeck(6.6,4.7,.6,.22,white,-.25);
  roundedDeck(6.35,4.45,.5,.035,material(0xeeeeee),-.01);
  // Main building, glazed facades, rooftop plant and entry canopy.
  box(1.8,1.55,1.35,white,-1.05,.8,-.85);
  box(1.94,.13,1.48,side,-1.05,1.61,-.85);
  box(1.82,.08,1.37,white,-1.05,1.71,-.85);
  for(let floor=0;floor<3;floor++) {
    box(1.56,.28,.018,pane,-1.05,.37+floor*.43,-.164);
    box(.018,.28,1.1,pane,-.142,.37+floor*.43,-.85);
    for(let i=0;i<5;i++) box(.025,.29,.035,white,-1.7+i*.32,.37+floor*.43,-.15);
  }
  box(.42,.52,.04,material(0x161616),-1.05,.29,-.14);
  box(.8,.065,.4,white,-1.05,.64,.02);
  box(.9,.08,.35,side,-1.05,.07,.1);
  for(let i=0;i<2;i++) {
    box(.42,.2,.48,side,-1.42+i*.72,1.85,-.83);
    cylinder(.13,.13,.025,steel,-1.42+i*.72,1.96,-.83);
  }
  const graphite=material(0x252525,{metalness:.35,roughness:.28});
  for(let floor=0;floor<3;floor++) {
    box(1.84,.045,1.39,white,-1.05,.19+floor*.43,-.85);
    for(let j=0;j<4;j++)box(.027,.31,.03,white,-.128,.37+floor*.43,-1.31+j*.3);
  }
  for(let j=0;j<7;j++)box(.22,.016,.21,side,-1.05,.065,.42+j*.24);
  for(let j=0;j<5;j++)box(.14,.02,.025,graphite,-1.61+j*.09,1.965,-.59);
  for(const z of [-1.49,-.22])rod([-1.9,1.83,z],[-.2,1.83,z],.012,steel);
  for(const x of [-1.9,-.2])rod([x,1.83,-1.49],[x,1.83,-.22],.012,steel);
  for(const x of [-1.9,-.2])for(const z of [-1.49,-.22])rod([x,1.72,z],[x,1.83,z],.012,steel);
  // A set of tilted photovoltaic panels, each subdivided into real cells. They track the sun.
  const panels=[];
  for(let row=0;row<2;row++)for(let col=0;col<3;col++) {
    const p=new T.Group();p.position.set(.2+col*.75,.41,.18+row*.94);p.rotation.x=-.25;world.add(p);panels.push(p);
    box(.68,.045,.78,steel,0,0,0,p);
    for(let x=0;x<3;x++)for(let z=0;z<4;z++)box(.204,.012,.172,dark,-.218+x*.218,.03,-.28+z*.185,p);
    rod([p.position.x,.03,p.position.z],[p.position.x,.35,p.position.z],.025,steel);
  }
  // Three battery modules with a changing charge bar.
  const chargeBars=[];
  for(let i=0;i<3;i++) {
    const x=-2.1+i*.52;
    box(.43,.68,.55,white,x,.38,1.15);
    box(.44,.045,.57,side,x,.74,1.15);
    box(.31,.47,.015,material(0xd5d5d5),x,.4,1.435);
    const bar=box(.23,.38,.02,green,x,.4,1.45);chargeBars.push(bar);
    box(.05,.05,.025,purple,x,.67,1.45);
  }
  // The main-grid tower and its cross-members.
  const tx=-2.45,tz=-.75;
  for(const x of [-.18,.18])for(const z of [-.18,.18]) rod([tx+x,.03,tz+z],[tx+x*.35,2.1,tz+z*.35],.024,steel);
  for(let y=.3;y<1.9;y+=.4) {
    rod([tx-.15,y,tz+.12],[tx+.15,y+.38,tz+.12],.014,steel);
    rod([tx+.15,y,tz-.12],[tx-.15,y+.38,tz-.12],.014,steel);
  }
  box(.82,.055,.1,steel,tx,1.65,tz);box(.64,.055,.1,steel,tx,2.02,tz);
  for(const x of [-.32,.32])cylinder(.05,.05,.16,white,tx+x,1.75,tz);
  for(const offset of [-.27,.27]) {
    const wire=new T.CatmullRomCurve3([new T.Vector3(tx+offset,1.68,tz),new T.Vector3(-2.13,1.3,-1.1),new T.Vector3(-1.75,1.74,-1.3+offset)]);
    world.add(new T.Mesh(new T.TubeGeometry(wire,28,.009,6,false),graphite));
  }
  // Landscaping supplies scale without distracting from the energy assets.
  for(const [x,z] of [[2.65,-1.5],[2.7,1.5],[-2.75,1.65]]) {
    cylinder(.04,.05,.42,material(0x777777),x,.22,z);
    const tree=new T.Mesh(new T.IcosahedronGeometry(.25,2),material(0xaaaaaa));tree.scale.y=1.4;tree.position.set(x,.61,z);tree.castShadow=true;world.add(tree);
    cylinder(.29,.32,.055,white,x,.04,z);
  }
  // A secondary solar canopy at the back of the plot.
  box(1.2,.12,.8,white,1.25,.65,-1.05);
  for(const dx of [-.48,.48])rod([1.25+dx,.03,-1.05],[1.25+dx,.6,-1.05],.03,steel);
  for(let x=0;x<4;x++)box(.26,.025,.67,dark,.8+x*.3,.73,-1.05);
  // A small wind turbine; its rotor speed follows a gusting wind.
  const turbine=new T.Group();turbine.position.set(.28,0,-1.62);world.add(turbine);
  rod([0,.03,0],[0,2.05,0],.035,white,turbine);
  box(.16,.13,.32,white,0,2.1,.02,turbine);
  const rotor=new T.Group();rotor.position.set(0,2.1,.2);turbine.add(rotor);
  cylinder(.055,.055,.09,side,0,0,0,rotor).rotation.x=Math.PI/2;
  for(let i=0;i<3;i++){const blade=new T.Group();blade.rotation.z=i*Math.PI*2/3;rotor.add(blade);box(.075,.86,.022,white,0,.46,0,blade);}
  // Everything built so far assembles on load: pieces drop in, sweeping from the grid tower to the solar field.
  const assembly=world.children.slice(2).map(o=>({o,y:o.position.y,s:o.scale.clone(),delay:(o.position.x+3)*190+Math.abs(o.position.z)*60}));
  // Energy routes and moving particles represent the selected dispatch signal.
  const routes=[];
  function route(points,color) {
    const curve=new T.CatmullRomCurve3(points.map(p=>new T.Vector3(...p)));
    const line=new T.Mesh(new T.TubeGeometry(curve,45,.018,8,false),material(color,{emissive:color,emissiveIntensity:.1}));world.add(line);
    const dots=[];for(let i=0;i<6;i++){const m=new T.Mesh(new T.SphereGeometry(i%2?.032:.052,14,14),material(0xf2b230,{emissive:0xf2b230,emissiveIntensity:1.4,roughness:.3}));world.add(m);dots.push(m);}
    const object={curve,line,dots,active:true};routes.push(object);return object;
  }
  const batteryRoute=route([[-1.58,.08,1.48],[-.5,.08,1.7],[-.45,.08,.55],[-1.05,.08,.15]],0x222222);
  const gridRoute=route([[-2.4,.08,-.25],[-2.35,.08,.3],[-1.6,.08,.42],[-1.05,.08,.15]],0x555555);
  const solarRoute=route([[1.4,.08,1.7],[.2,.08,1.88],[-.4,.08,1.9],[-1.58,.08,1.48]],0x333333);
  let dragging=false,lastX=0,targetYaw=-.18,visible=true,selected=null,paused=false;
  world.rotation.y=-.6;
  const reduceMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  host.addEventListener('pointerdown',e=>{dragging=true;lastX=e.clientX;host.setPointerCapture(e.pointerId);});
  host.addEventListener('pointermove',e=>{if(dragging){targetYaw+=(e.clientX-lastX)*.006;lastX=e.clientX;}});
  host.addEventListener('pointerup',()=>dragging=false);host.addEventListener('pointercancel',()=>dragging=false);
  document.getElementById('reset-camera').addEventListener('click',()=>targetYaw=-.18);
  document.getElementById('rotate-left').addEventListener('click',()=>targetYaw-=Math.PI/6);
  document.getElementById('rotate-right').addEventListener('click',()=>targetYaw+=Math.PI/6);
  const pauseButton=document.getElementById('pause-scene');
  function syncPause(){pauseButton.setAttribute('aria-pressed',String(paused));pauseButton.setAttribute('aria-label',paused?'Resume 3D animation':'Pause 3D animation');pauseButton.title=paused?'Resume animation':'Pause animation';pauseButton.innerHTML=paused?'<i class="ph ph-play" aria-hidden="true"></i>':'Ⅱ';}
  paused=reduceMotion;syncPause();
  pauseButton.addEventListener('click',()=>{paused=!paused;syncPause();});
  function resize(){const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;renderer.setSize(w,h);const span=5.8;camera.left=-span*w/h/2;camera.right=span*w/h/2;camera.top=span/2;camera.bottom=-span/2;camera.updateProjectionMatrix();const mobile=w<500;world.scale.setScalar(mobile?.66:.88);}
  new ResizeObserver(resize).observe(host);resize();
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;}).observe(host);
  // An on-scene clock for the simulated day.
  const clock=document.createElement('div');clock.className='scene-clock';clock.setAttribute('aria-hidden','true');host.appendChild(clock);
  let soc=80,socTarget=80,auto=true,lastHour=-1,pitch=0,targetPitch=0;
  const hero=host.closest('.hero-visual')||host;
  hero.addEventListener('pointermove',e=>{if(dragging)return;const r=hero.getBoundingClientRect();targetPitch=((e.clientY-r.top)/r.height-.5)*.14;});
  hero.addEventListener('pointerleave',()=>targetPitch=0);
  function apply(row){selected=row;const storage=row.grid_mode==='renewable_storage';batteryRoute.active=storage;gridRoute.active=!storage||row.storage_shortfall;solarRoute.active=row.solar_kw>0;
    socTarget=row.battery_soc_pct;
    routes.forEach(r=>{r.line.material.opacity=r.active?1:.2;r.line.material.transparent=true;r.dots.forEach(dot=>dot.visible=r.active);});
  }
  // The landing page's simulated day follows the same rules as the dashboard: evening hours draw on storage.
  function rowForHour(h,sun){
    if(h>=18&&h<=22)return {grid_mode:'renewable_storage',storage_shortfall:false,solar_kw:0,battery_soc_pct:Math.max(25,soc-11),label:'Storage'};
    if(sun>.08)return {grid_mode:'main_grid',storage_shortfall:false,solar_kw:150*sun,battery_soc_pct:Math.min(95,soc+6),label:'Solar charging'};
    return {grid_mode:'main_grid',storage_shortfall:false,solar_kw:0,battery_soc_pct:soc,label:'Main grid'};
  }
  window.GridScene={update(row){auto=false;clock.hidden=true;apply(row);},setAuto(on){auto=on;clock.hidden=!on;lastHour=-1;},resize,reset(){targetYaw=0;}};
  let previous=0,flowTime=reduceMotion?9000:3000,introStart=null;  // start the day at 08:00 (noon without motion)
  const ease=t=>{t=Math.min(1,Math.max(0,t));const c=1.70158;return 1+(c+1)*Math.pow(t-1,3)+c*Math.pow(t-1,2);};
  const night=new T.Color(0x0f1218),day=new T.Color(0xffffff),warm=new T.Color(0xffc46b);
  function animate(time){requestAnimationFrame(animate);if(!visible||document.hidden||time-previous<33)return;const elapsed=previous?Math.min(time-previous,100):0;previous=time;
    if(introStart===null)introStart=time;
    const intro=reduceMotion?1e9:time-introStart;
    if(intro<3200)assembly.forEach(a=>{const e=ease((intro-a.delay)/650);a.o.position.y=a.y+(1-Math.min(1,e))*2.4;a.o.scale.set(a.s.x*Math.max(.001,e),a.s.y*Math.max(.001,e),a.s.z*Math.max(.001,e));});
    else if(assembly.length){assembly.forEach(a=>{a.o.position.y=a.y;a.o.scale.copy(a.s);});assembly.length=0;}
    if(!paused&&!reduceMotion)flowTime+=elapsed;
    // One simulated day every 36 seconds, starting at 06:00.
    const hours=(6+flowTime/1500)%24, theta=(hours-6)/12*Math.PI, sun=Math.sin(theta), daylight=Math.max(0,sun);
    const dark=1-Math.min(1,Math.max(0,(sun+.12)/.35));
    key.position.set(-Math.cos(theta)*8,Math.max(.6,sun*9),4.5);key.intensity=.35+2.4*daylight;
    ambient.intensity=.5+.8*daylight;ambient.color.copy(day).lerp(night,dark*.55);
    pane.emissive.copy(warm);pane.emissiveIntensity=dark*1.1;
    host.style.setProperty('--night',dark.toFixed(3));
    panels.forEach(pn=>{pn.rotation.z+=((daylight>0?Math.cos(theta)*-.32:0)-pn.rotation.z)*.05;});
    rotor.rotation.z-=elapsed*(.004+.0025*Math.sin(flowTime*.0004)+.0015*dark);
    if(auto){const h=Math.floor(hours);if(h!==lastHour){lastHour=h;const row=rowForHour(h,daylight);soc=row.battery_soc_pct;apply(row);clock.textContent=`${String(h).padStart(2,'0')}:00 · ${row.label}`;}}
    const level=(soc=soc+(socTarget-soc)*.06);
    chargeBars.forEach(bar=>{bar.scale.y=Math.max(.03,level/100);bar.position.y=.21+.19*level/100;});
    const yaw=targetYaw+(!paused&&!reduceMotion&&!dragging?Math.sin(flowTime*.00018)*.06:0);
    world.rotation.y=reduceMotion?targetYaw:world.rotation.y+(yaw-world.rotation.y)*.12;
    pitch+=(targetPitch-pitch)*.08;world.rotation.x=reduceMotion?0:pitch;
    routes.forEach((r,j)=>r.dots.forEach((dot,i)=>{dot.position.copy(r.curve.getPointAt(((flowTime*.00016)+i/6+j*.11)%1));dot.scale.setScalar(1+.25*Math.sin(flowTime*.008+i));}));
    renderer.render(scene,camera);
  }
  requestAnimationFrame(animate);
})();
