import {GlassRenderer} from './vendor/lens/internal/renderer.js';
import {JellyController} from './vendor/lens/internal/jelly.js';

// Lens handles the input's sampled material; panels refract the actual map
// with a separate SVG backdrop bevel, keeping every foreground label sharp.
const planner=document.querySelector('.query-box');
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
const reducedTransparency=matchMedia('(prefers-reduced-transparency: reduce)');
let renderer, jelly, canvas;

function paintScene(ctx,{width:w,height:h}){
  const base=ctx.createLinearGradient(0,0,w,h);
  base.addColorStop(0,'#ecf7ff');base.addColorStop(.42,'#dbe9f8');base.addColorStop(1,'#dbe5f5');
  ctx.fillStyle=base;ctx.fillRect(0,0,w,h);
  const ribbon=(offset,from,to)=>{
    ctx.beginPath();ctx.moveTo(-w*.2,h*(.55+offset));
    ctx.bezierCurveTo(w*.32,h*(.18+offset),w*.7,h*(.7+offset),w*1.3,h*(.15+offset));
    ctx.lineTo(w*1.3,h*1.3);ctx.lineTo(-w*.2,h*1.3);ctx.closePath();
    const fill=ctx.createLinearGradient(0,h*.25,w,h);
    fill.addColorStop(0,from);fill.addColorStop(1,to);
    ctx.save();ctx.shadowColor='#5b6d9230';ctx.shadowBlur=28;ctx.shadowOffsetY=-5;
    ctx.fillStyle=fill;ctx.fill();ctx.restore();
  };
  ribbon(.1,'#cbdff4','#c1d2eb');ribbon(.42,'#dae5f6','#e8eef9');
  const light=ctx.createRadialGradient(0,0,0,0,0,Math.max(w,h));
  light.addColorStop(0,'#ffffff95');light.addColorStop(1,'#ffffff08');
  ctx.fillStyle=light;ctx.fillRect(0,0,w,h);
}

function startMaterial(){
  if(renderer||reducedTransparency.matches)return;
  canvas=document.createElement('canvas');canvas.className='planner-glass-canvas';
  canvas.setAttribute('aria-hidden','true');planner.prepend(canvas);
  try{
    renderer=new GlassRenderer(canvas,planner,{artwork:paintScene,scene:'aurora',
      refraction:.72,blur:.22,dispersion:.25,motion:false});
    renderer.reducedMotion=reducedMotion.matches;
    renderer.setMotionSource(reducedMotion.matches?null:jelly);
  }catch{
    // A software renderer or unavailable WebGL2 keeps the readable CSS material.
    renderer?.destroy();renderer=undefined;canvas.remove();
    planner.classList.remove('webgl-ready');
  }
}

function stopMaterial(){
  renderer?.destroy();renderer=undefined;canvas?.remove();
  planner.classList.remove('webgl-ready');
}

const inputLens=document.createElement('span');inputLens.className='input-lens';
inputLens.dataset.glass='panel';inputLens.setAttribute('aria-hidden','true');planner.prepend(inputLens);

const springButtons=[...document.querySelectorAll('.primary,.suggestions button,.settings-button,.map-tip,.review-trip')];
function setSpringFeedback(){
  jelly?.destroy();jelly=undefined;
  for(const button of springButtons){
    delete button.dataset.jelly;delete button.dataset.held;
    button.classList.remove('jelly-moving');button.style.removeProperty('--jelly-matrix');
  }
  if(!reducedMotion.matches){
    jelly=new JellyController(document.body,null,{intensity:.42});
    for(const button of springButtons)jelly.register(button,{mode:'press'});
    // Forward spring deformation without repainting at idle.
    jelly.renderer={invalidate:(...args)=>renderer?.invalidate(...args)};
  }
  renderer?.setMotionSource(jelly);
}
setSpringFeedback();
startMaterial();

reducedMotion.addEventListener('change',event=>{
  setSpringFeedback();
  if(renderer){renderer.reducedMotion=event.matches;renderer.invalidate();}
});
reducedTransparency.addEventListener('change',()=>{
  if(reducedTransparency.matches)stopMaterial();else startMaterial();
});

// Specular light follows a pointer; it never moves text or interferes with map gestures.
let lightFrame=0,lightTarget,point;
document.addEventListener('pointermove',event=>{
  if(reducedMotion.matches||event.pointerType==='touch')return;
  const target=event.target.closest('#query-form,.itinerary,.brand,.top-actions,.route-card,.modal');
  if(!target)return;
  lightTarget=target;point=[event.clientX,event.clientY];
  if(lightFrame)return;
  lightFrame=requestAnimationFrame(()=>{
    lightFrame=0;
    const box=lightTarget.getBoundingClientRect();
    lightTarget.style.setProperty('--light-x',`${point[0]-box.left}px`);
    lightTarget.style.setProperty('--light-y',`${point[1]-box.top}px`);
  });
},{passive:true});

// A normal map bends only the rim of each rounded panel. Generated locally,
// once per size; neither map pixels nor cross-origin tiles are read or copied.
const ns='http://www.w3.org/2000/svg';
const defsRoot=document.createElementNS(ns,'svg');defsRoot.classList.add('optical-defs');
defsRoot.setAttribute('aria-hidden','true');
const defs=document.createElementNS(ns,'defs');defsRoot.append(defs);document.body.append(defsRoot);
const bevels=new Map();
function buildBevel(element){
  const entry=bevels.get(element),w=element.clientWidth,h=element.clientHeight;
  if(!w||!h||entry.size===`${w}:${h}`)return;
  entry.size=`${w}:${h}`;
  const ratio=Math.min(1,480/Math.max(w,h));
  const width=Math.max(1,Math.round(w*ratio)),height=Math.max(1,Math.round(h*ratio));
  const radius=Math.min(parseFloat(getComputedStyle(element).borderTopLeftRadius)||20,w/2,h/2)*ratio;
  const surface=document.createElement('canvas');surface.width=width;surface.height=height;
  const ctx=surface.getContext('2d'),pixels=ctx.createImageData(width,height),band=22*ratio;
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const px=x+.5-width/2,py=y+.5-height/2;
    const qx=Math.abs(px)-width/2+radius,qy=Math.abs(py)-height/2+radius;
    const ox=Math.max(qx,0),oy=Math.max(qy,0),length=Math.hypot(ox,oy);
    const distance=length+Math.min(Math.max(qx,qy),0)-radius;
    const strength=distance<=0&&distance>-band?Math.sin(-distance/band*Math.PI)*.9:0;
    const nx=length?ox/length:qx>qy?1:0,ny=length?oy/length:qy>=qx?1:0;
    const i=(y*width+x)*4;
    pixels.data[i]=128+Math.sign(px)*nx*strength*127;
    pixels.data[i+1]=128+Math.sign(py)*ny*strength*127;
    pixels.data[i+2]=128;pixels.data[i+3]=255;
  }
  ctx.putImageData(pixels,0,0);
  entry.filter.setAttribute('width',w);entry.filter.setAttribute('height',h);
  entry.image.setAttribute('width',w);entry.image.setAttribute('height',h);
  entry.image.setAttribute('href',surface.toDataURL());
}
const bevelObserver=new ResizeObserver(entries=>{for(const {target} of entries)buildBevel(target);});
for(const element of document.querySelectorAll('.brand,.top-actions,#query-form,.itinerary,.route-card,.modal,.map-controls')){
  const filter=document.createElementNS(ns,'filter');filter.id=`glass-rim-${bevels.size}`;
  for(const [name,value] of Object.entries({x:0,y:0,filterUnits:'userSpaceOnUse','color-interpolation-filters':'sRGB'}))filter.setAttribute(name,value);
  const normal=document.createElementNS(ns,'feImage');normal.setAttribute('result','normal');normal.setAttribute('preserveAspectRatio','none');
  const displacement=document.createElementNS(ns,'feDisplacementMap');
  for(const [name,value] of Object.entries({in:'SourceGraphic',in2:'normal',scale:16,xChannelSelector:'R',yChannelSelector:'G'}))displacement.setAttribute(name,value);
  filter.append(normal,displacement);defs.append(filter);
  const rim=document.createElement('span');rim.className='glass-bevel';rim.setAttribute('aria-hidden','true');element.append(rim);
  element.style.setProperty('--refraction-filter',`url("#${filter.id}")`);
  bevels.set(element,{filter,image:normal,size:null});bevelObserver.observe(element);
}

window.addEventListener('pagehide',event=>{
  if(event.persisted)return;
  cancelAnimationFrame(lightFrame);bevelObserver.disconnect();jelly?.destroy();stopMaterial();
});
