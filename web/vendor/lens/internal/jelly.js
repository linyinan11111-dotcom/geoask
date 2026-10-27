import { Spring, clamp, jellyMatrix, boundDrag, togglePose } from './jelly-physics.js';

/** Gesture-driven motion for semantic DOM and its matching glass shader. */
export class JellyController {
  constructor(stage, renderer, { intensity = .7 } = {}) {
    this.stage = stage; this.renderer = renderer; this.intensity = intensity;
    this.bodies = new Map(); this.frame = 0; this.last = 0; this.gesture = null;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.abort = new AbortController(); this.signal = this.abort.signal;
    this.blockClick = null;
    window.addEventListener('pointermove', e => this.move(e), { signal:this.signal, passive:false });
    window.addEventListener('pointerup', e => this.release(e), { signal:this.signal });
    window.addEventListener('pointercancel', e => this.release(e, true), { signal:this.signal });
    window.addEventListener('blur', () => this.cancel(), { signal:this.signal });
    window.addEventListener('resize', () => this.reflow(), { signal:this.signal });
    document.addEventListener('visibilitychange', () => { if(document.hidden)this.cancel(true); }, { signal:this.signal });
    document.addEventListener('click', e => {
      if(this.blockClick && performance.now() < this.blockClick.until && this.blockClick.source.contains(e.target)) {
        e.preventDefault(); e.stopImmediatePropagation(); this.blockClick = null;
      }
    }, { capture:true, signal:this.signal });
  }

  register(element, { source=element, mode='tether', maxTravel=Infinity, onCommit, segments, onPosition } = {}) {
    const body = { element,source,mode,maxTravel,onCommit,segments,onPosition,
      x:new Spring(), y:new Spring(), press:new Spring(), stretch:new Spring(), bendX:new Spring(), bendY:new Spring(),
      angle:0, held:false, freeX:0, freeY:0, optical:{bend:[0,0],pressure:0,energy:0} };
    this.bodies.set(element, body);
    element.dataset.jelly = mode; element.style.setProperty('--jelly-matrix','matrix(1,0,0,1,0,0)');
    source.addEventListener('pointerdown', e => this.grab(body,e), { signal:this.signal });
    source.addEventListener('lostpointercapture', e => {if(this.gesture?.id===e.pointerId)this.release(e,true);}, { signal:this.signal });
    return body;
  }

  grab(body, e) {
    if(e.button!==0 || !e.isPrimary || this.gesture || body.source.disabled || body.source.getAttribute('aria-disabled')==='true')return;
    // A panel never steals a slider, menu, button, or nested draggable's gesture.
    if(['tether','press'].includes(body.mode) && body.source===body.element && !body.element.matches('button')) {
      const interactive=e.target.closest('button,input,a,[role=slider],[data-jelly],[data-optical-drag]');
      if(interactive && interactive!==body.element)return;
    }
    const boundary=body.mode==='free'?body.element.parentElement:this.stage;
    const r=body.element.getBoundingClientRect(), sr=boundary.getBoundingClientRect();
    const capture=body.mode==='segment'?(e.target.closest('button')||body.source):body.source;
    this.gesture={body,boundary,capture,id:e.pointerId,startX:e.clientX,startY:e.clientY,baseX:body.mode==='toggle'?body.x.value:body.x.target,restX:body.x.target,baseY:body.y.target,
      rect:{left:r.left-sr.left-body.x.value,top:r.top-sr.top-body.y.value,right:r.right-sr.left-body.x.value,bottom:r.bottom-sr.top-body.y.value},moved:false};
    body.held=true;body.press.target=1;
    body.element.dataset.held='true';
    if(!['range','press'].includes(body.mode)) {e.preventDefault();capture.setPointerCapture(e.pointerId);}
    if(capture.matches('button,[tabindex]'))capture.focus({preventScroll:true});
    this.wake();
  }

  move(e) {
    const g=this.gesture;if(!g||g.id!==e.pointerId)return;
    const b=g.body,dx=e.clientX-g.startX,dy=e.clientY-g.startY;
    const wasMoved=g.moved;g.moved ||= Math.hypot(dx,dy)>5;
    if(g.moved&&!wasMoved)b.source.dispatchEvent(new CustomEvent('lg:gesture',{bubbles:true,detail:{phase:'drag',mode:b.mode,moved:true}}));
    if(b.mode==='press') {
      // A lens in a native scroller responds elastically without capturing its pan.
      b.x.target=clamp(dx*.12,-5,5);b.y.target=clamp(dy*.12,-5,5);
    } else if(b.mode==='range') {
      b.y.target=clamp(dy*.35,-18,18);
    } else if(b.mode==='toggle') {
      b.x.target=clamp(g.baseX+dx,0,this.toggleTravel(b.source));b.y.target=0;
    } else if(b.mode==='segment') {
      const box=b.source.getBoundingClientRect();
      b.x.target=clamp(e.clientX-box.left-b.element.offsetWidth/2,3,b.source.clientWidth-b.element.offsetWidth-3);
      b.y.target=clamp(dy*.25,-10,10);
    } else {
      const bound=boundDrag(g.baseX+dx,g.baseY+dy,g.rect,{width:g.boundary.clientWidth,height:g.boundary.clientHeight},22);
      b.x.target=clamp(bound.x,-b.maxTravel,b.maxTravel);b.y.target=clamp(bound.y,-b.maxTravel,b.maxTravel);
    }
    if(!['range','press'].includes(b.mode))e.preventDefault();
    this.wake();
  }

  release(e, cancelled=false) {
    const g=this.gesture;if(!g||(e&&g.id!==e.pointerId))return;
    this.gesture=null;const b=g.body;b.held=false;b.press.target=0;delete b.element.dataset.held;
    if(g.moved&&!['range','press'].includes(b.mode))this.blockClick={source:b.source,until:performance.now()+350};
    if(g.capture.hasPointerCapture?.(g.id))g.capture.releasePointerCapture(g.id);
    if(['tether','press'].includes(b.mode)) {b.x.target=0;b.y.target=0;}
    if(b.mode==='free') {
      if(cancelled){b.x.target=b.freeX;b.y.target=b.freeY;}
      else{b.freeX=b.x.target;b.freeY=b.y.target;b.onPosition?.(b.freeX,b.freeY);}
    }
    if(b.mode==='range')b.y.target=0;
    if(b.mode==='toggle'){
      if(g.moved&&!cancelled)b.onCommit?.(b.x.target>=this.toggleTravel(b.source)/2);
      else b.x.target=g.restX;
      b.y.target=0;
    }
    if(b.mode==='segment'){
      if(g.moved&&!cancelled){
        const center=b.x.target+b.element.offsetWidth/2;
        const selected=b.segments().reduce((a,c)=>Math.abs(c.offsetLeft+c.offsetWidth/2-center)<Math.abs(a.offsetLeft+a.offsetWidth/2-center)?c:a);
        b.onCommit?.(selected);
      }else b.x.target=g.baseX;
      b.y.target=0;
    }
    // Native click behavior remains intact for a tap; only a true drag suppresses it.
    b.source.dispatchEvent(new CustomEvent('lg:gesture',{bubbles:true,detail:{phase:cancelled?'cancel':'end',mode:b.mode,moved:g.moved}}));
    this.wake();
  }

  target(element,x,y=0,{snap=false}={}) {
    const b=this.bodies.get(element);if(!b)return;
    b.x.target=x;b.y.target=y;
    if(b.mode==='free'){b.freeX=x;b.freeY=y;}
    if(snap){b.x.snap();b.y.snap();}this.wake();
  }
  pulse(element,amount=.45) {
    const b=this.bodies.get(element);if(!b)return;
    b.press.velocity=clamp(b.press.velocity+amount*8,-4,4);b.stretch.velocity=clamp(b.stretch.velocity+amount*1.2,-1.5,1.5);this.wake();
  }
  setIntensity(value,explicit=false){this.intensity=clamp(value,0,1);if(explicit)this.reduced=false;this.wake();}
  toggleTravel(source){const thumb=source.querySelector('[data-glass="thumb"]');if(!thumb)return 0;const inset=parseFloat(getComputedStyle(thumb).left)||3;return Math.max(0,source.clientWidth-thumb.offsetWidth-inset*2);}
  toggleProgress(source){const body=this.bodies.get(source.querySelector('[data-glass="thumb"]'));return clamp((body?.x.value||0)/Math.max(1,this.toggleTravel(source)),0,1);}
  optics(element){return this.bodies.get(element)?.optical||{bend:[0,0],pressure:0,energy:0};}
  cancel(snap=false){this.release(null,true);if(snap){for(const b of this.bodies.values())for(const k of ['x','y','press','stretch','bendX','bendY'])b[k].snap();}this.wake();}
  reflow(){
    this.cancel();
    for(const b of this.bodies.values())if(b.mode==='free'){
      const parent=b.element.parentElement,w=parent.clientWidth,h=parent.clientHeight;
      if(!w||!h)continue;
      const maxX=Math.max(0,(w-b.element.offsetWidth)/2-22),maxY=Math.max(0,(h-b.element.offsetHeight)/2-22);
      b.x.target=b.freeX=clamp(b.freeX,-maxX,maxX);b.y.target=b.freeY=clamp(b.freeY,-maxY,maxY);b.onPosition?.(b.freeX,b.freeY);
    }
    this.wake();
  }
  reset(){this.cancel();for(const b of this.bodies.values()){if(['tether','free','press'].includes(b.mode)){b.x.target=b.y.target=b.freeX=b.freeY=0;}b.press.target=0;}this.wake();}
  wake(){if(!this.frame&&!this.destroyed)this.frame=requestAnimationFrame(now=>this.tick(now));}

  tick(now) {
    this.frame=0;const dt=this.last?Math.min((now-this.last)/1000,.05):1/60;this.last=now;
    const amount=this.intensity*(this.reduced?.65:1);let moving=false;
    for(const b of this.bodies.values()){
      if(!b.element.getClientRects().length)continue;
      const damping=this.reduced?34:b.mode==='toggle'?26:b.held?22:17;
      b.x.step(dt,b.mode==='range'?380:260,damping);b.y.step(dt,260,damping);
      b.press.step(dt,310,this.reduced?32:18);
      const speed=Math.hypot(b.x.velocity,b.y.velocity);
      if(speed>16)b.angle=Math.atan2(b.y.velocity,b.x.velocity);
      const normalized=Math.min(speed/(b.mode==='range'?1700:2400),.25);
      b.stretch.target=normalized*amount;
      b.stretch.step(dt,230,this.reduced?30:15);
      // Opposite edges trail at different speeds; the shader deforms its SDF.
      b.bendX.target=clamp((b.x.target-b.x.value)*.12,-11,11)*amount;
      b.bendY.target=clamp((b.y.target-b.y.value)*.12,-11,11)*amount;
      b.bendX.step(dt,190,this.reduced?28:14);b.bendY.step(dt,190,this.reduced?28:14);
      const pressure=clamp(b.press.value,0,1.16);
      const lift=1+pressure*amount*(b.mode==='range'?.20:.035);
      let m=jellyMatrix(b.stretch.value,b.angle,lift),x=b.x.value,y=b.y.value;
      if(b.mode==='toggle'){
        const pose=togglePose(x,this.toggleTravel(b.source),b.element.offsetWidth,pressure*amount,b.stretch.value);
        m=[pose.scaleX,0,0,pose.scaleY];x=pose.x;y=0;
      }
      b.element.style.setProperty('--jelly-matrix',`matrix(${m.map(v=>v.toFixed(5)).join(',')},${x.toFixed(3)},${y.toFixed(3)})`);
      b.optical={bend:b.mode==='toggle'?[0,0]:[b.bendX.value,b.bendY.value],pressure,energy:Math.abs(b.stretch.value)+Math.hypot(b.bendX.value,b.bendY.value)*.015};
      const active=['x','y','press','stretch','bendX','bendY'].some(k=>b[k].moving);
      b.element.classList.toggle('jelly-moving',active||b.held);
      moving ||= active;
    }
    this.renderer?.invalidate();
    if(moving)this.wake();else this.last=0;
  }
  destroy(){this.destroyed=true;cancelAnimationFrame(this.frame);this.abort.abort();}
}
