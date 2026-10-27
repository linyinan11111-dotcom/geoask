// A small XYZ map: no SDK key, third-party scripts, or CDN dependencies.
const PI = Math.PI, A = 6378245, EE = 0.00669342162296594323;
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
const outside = ([lon, lat]) => lon < 73.66 || lon > 135.05 || lat < 3.86 || lat > 53.55;

function delta([lon, lat]) {
  const x = lon - 105, y = lat - 35;
  let dy = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*Math.sqrt(Math.abs(x));
  dy += (20*Math.sin(6*x*PI) + 20*Math.sin(2*x*PI))*2/3;
  dy += (20*Math.sin(y*PI) + 40*Math.sin(y/3*PI))*2/3;
  dy += (160*Math.sin(y/12*PI) + 320*Math.sin(y*PI/30))*2/3;
  let dx = 300 + x + 2*y + .1*x*x + .1*x*y + .1*Math.sqrt(Math.abs(x));
  dx += (20*Math.sin(6*x*PI) + 20*Math.sin(2*x*PI))*2/3;
  dx += (20*Math.sin(x*PI) + 40*Math.sin(x/3*PI))*2/3;
  dx += (150*Math.sin(x/12*PI) + 300*Math.sin(x/30*PI))*2/3;
  const rad = lat/180*PI, magic = 1-EE*Math.sin(rad)**2, root = Math.sqrt(magic);
  return [dx*180/(A/root*Math.cos(rad)*PI), dy*180/((A*(1-EE))/(magic*root)*PI)];
}
export function toGCJ(point) {
  if (outside(point)) return [...point];
  const d = delta(point);
  return [point[0]+d[0], point[1]+d[1]];
}
export function toWGS(point) {
  if (outside(point)) return [...point];
  let result = [...point];
  for (let i=0; i<5; i++) {
    const gcj = toGCJ(result);
    result = [result[0]+point[0]-gcj[0], result[1]+point[1]-gcj[1]];
  }
  return result;
}
export function project([lon, lat], zoom) {
  const size = 256*2**zoom, sin = Math.sin(clamp(lat,-85.05112878,85.05112878)*PI/180);
  return [(lon+180)/360*size, (.5-Math.log((1+sin)/(1-sin))/(4*PI))*size];
}
export function unproject([x,y], zoom) {
  const size = 256*2**zoom;
  return [((x/size*360)%360+360)%360-180, Math.atan(Math.sinh(PI*(1-2*clamp(y,0,size)/size)))*180/PI];
}

export class TripMap {
  constructor(element, {onOrigin, onCandidate, onError}) {
    this.el = element;
    this.tileLayer = element.querySelector('.tile-layer');
    this.markerLayer = element.querySelector('.marker-layer');
    this.paths = [...element.querySelectorAll('.route-layer path')];
    this.options = {coordinate_system:'gcj02',tile_url:''};
    this.center = [116.46,39.92]; this.zoom = 14;
    this.tiles = new Map(); this.markers = []; this.line = [];
    this.onOrigin = onOrigin; this.onCandidate = onCandidate; this.onError = onError;
    this.pending = false;
    new ResizeObserver(() => this.schedule()).observe(element);
    element.addEventListener('pointerdown', e => {
      if (e.button !== 0 || e.target.closest('button')) return;
      this.drag = {start:[e.clientX,e.clientY], center:project(this.mapPoint(this.center),this.zoom)};
      element.setPointerCapture(e.pointerId); element.classList.add('dragging');
    });
    element.addEventListener('pointermove', e => {
      if (!this.drag) return;
      const dx = e.clientX-this.drag.start[0], dy = e.clientY-this.drag.start[1];
      const p = unproject([this.drag.center[0]-dx,this.drag.center[1]-dy], this.zoom);
      this.center = this.fromMap(p); this.schedule();
    });
    const stop = () => {this.drag=null;element.classList.remove('dragging');};
    element.addEventListener('pointerup',stop); element.addEventListener('pointercancel',stop);
    element.addEventListener('wheel', e => {
      e.preventDefault();
      if (this.lastWheel && performance.now()-this.lastWheel<120) return;
      this.lastWheel=performance.now();
      const rect = element.getBoundingClientRect();
      this.changeZoom(e.deltaY<0?1:-1,[e.clientX-rect.left,e.clientY-rect.top]);
    },{passive:false});
    element.addEventListener('contextmenu',e => {
      e.preventDefault(); const rect=element.getBoundingClientRect();
      this.onOrigin(this.location([e.clientX-rect.left,e.clientY-rect.top]));
    });
    element.addEventListener('dblclick', e => {
      if (e.target.closest('button')) return;
      const rect=element.getBoundingClientRect();
      this.changeZoom(1,[e.clientX-rect.left,e.clientY-rect.top]);
    });
    element.addEventListener('keydown',e => {
      const moves = {ArrowLeft:[-100,0],ArrowRight:[100,0],ArrowUp:[0,-100],ArrowDown:[0,100]};
      if (e.target !== element) return;
      if (['+','=','-'].includes(e.key)){e.preventDefault();this.changeZoom(e.key==='-'?-1:1);}
      else if(moves[e.key]){
        e.preventDefault();const p=project(this.mapPoint(this.center),this.zoom), d=moves[e.key];
        this.center=this.fromMap(unproject([p[0]+d[0],p[1]+d[1]],this.zoom));this.schedule();
      }
    });
  }
  mapPoint(p){return this.options.coordinate_system==='gcj02'?toGCJ(p):p;}
  fromMap(p){return this.options.coordinate_system==='gcj02'?toWGS(p):p;}
  configure(options, center){this.options=options;this.center=[...center];this.tiles.clear();this.tileLayer.replaceChildren();this.schedule();}
  screen(point){
    const p=project(this.mapPoint(point),this.zoom), c=project(this.mapPoint(this.center),this.zoom), size=256*2**this.zoom;
    let dx=p[0]-c[0]; if(dx>size/2)dx-=size; if(dx< -size/2)dx+=size;
    return [dx+this.el.clientWidth/2,p[1]-c[1]+this.el.clientHeight/2];
  }
  location([x,y]){
    const c=project(this.mapPoint(this.center),this.zoom);
    return this.fromMap(unproject([c[0]+x-this.el.clientWidth/2,c[1]+y-this.el.clientHeight/2],this.zoom));
  }
  changeZoom(d, anchor=[this.el.clientWidth/2,this.el.clientHeight/2]){
    const next=clamp(this.zoom+d,3,18); if(next===this.zoom)return;
    const point=this.location(anchor); this.zoom=next;
    const p=project(this.mapPoint(point),this.zoom);
    this.center=this.fromMap(unproject([p[0]-anchor[0]+this.el.clientWidth/2,p[1]-anchor[1]+this.el.clientHeight/2],this.zoom));
    this.schedule();
  }
  goTo(point, zoom=this.zoom){this.center=[...point];this.zoom=zoom;this.schedule();}
  fit(points, reserveCard=false){
    if(!points.length)return;
    if(points.length===1){this.goTo(points[0],15);return;}
    const pixels=points.map(p=>project(this.mapPoint(p),0));
    const reference=pixels[0][0];
    for(const p of pixels){if(p[0]-reference>128)p[0]-=256;if(p[0]-reference< -128)p[0]+=256;}
    const xs=pixels.map(p=>p[0]),ys=pixels.map(p=>p[1]);
    const min=[Math.min(...xs),Math.min(...ys)],max=[Math.max(...xs),Math.max(...ys)];
    const w=this.el.clientWidth,h=this.el.clientHeight;
    const right=reserveCard&&w>680?350:40,bottom=reserveCard&&w<=680?310:60;
    const usableW=Math.max(130,w-right-70),usableH=Math.max(150,h-bottom-90);
    const scale=Math.min(usableW/Math.max(max[0]-min[0],.00001),usableH/Math.max(max[1]-min[1],.00001));
    this.zoom=clamp(Math.floor(Math.log2(scale)),3,15);
    const center=[(min[0]+max[0])/2,(min[1]+max[1])/2];
    center[0]+=(right-40)/2/2**this.zoom;center[1]+=(bottom-60)/2/2**this.zoom;
    this.center=this.fromMap(unproject(center,0));this.schedule();
  }
  setData(markers, line=[]){
    this.markers=markers;this.line=line;this.markerLayer.replaceChildren();
    for(const marker of markers){
      const button=document.createElement('button');button.type='button';
      button.className=marker.kind==='origin'?'origin-marker':marker.kind==='candidate'?'candidate-marker':marker.kind==='parking'?'parking-marker':'stop-marker';
      button.textContent=marker.kind==='origin'?'':marker.label;
      button.title=marker.name;button.setAttribute('aria-label',marker.name);
      if(marker.kind!=='candidate'){
        const label=document.createElement('span');label.className='marker-label';label.textContent=marker.name;button.append(label);
      }
      button.addEventListener('click',()=>{
        if(marker.kind==='candidate')this.onCandidate(marker.stop,marker.index);
        else {this.goTo(marker.point);if(marker.stop!=null)this.onCandidate(marker.stop,null);}
      });
      marker.element=button;this.markerLayer.append(button);
    }
    this.schedule();
  }
  schedule(){if(this.pending)return;this.pending=true;requestAnimationFrame(()=>{this.pending=false;this.render();});}
  render(){
    const w=this.el.clientWidth,h=this.el.clientHeight;if(!w||!h)return;
    const center=project(this.mapPoint(this.center),this.zoom),ox=center[0]-w/2,oy=center[1]-h/2,n=2**this.zoom;
    const keep=new Set();
    if(this.options.tile_url){
      for(let y=Math.floor(oy/256);y<=Math.floor((oy+h)/256);y++){
        if(y<0||y>=n)continue;
        for(let x=Math.floor(ox/256);x<=Math.floor((ox+w)/256);x++){
          const key=`${this.zoom}/${x}/${y}`;keep.add(key);
          let tile=this.tiles.get(key);
          if(!tile){
            tile=document.createElement('img');tile.alt='';tile.draggable=false;tile.referrerPolicy='no-referrer';tile.decoding='async';
            tile.addEventListener('load',()=>{tile.dataset.loaded='true';this.tileMessage();});
            tile.addEventListener('error',()=>{tile.dataset.failed='true';tile.style.visibility='hidden';this.tileMessage();});
            tile.src=this.options.tile_url.replaceAll('{z}',this.zoom).replaceAll('{x}',((x%n)+n)%n).replaceAll('{y}',y);
            this.tiles.set(key,tile);this.tileLayer.append(tile);
          }
          tile.style.left=`${Math.round(x*256-ox)}px`;tile.style.top=`${Math.round(y*256-oy)}px`;
        }
      }
    }
    for(const [key,tile] of this.tiles)if(!keep.has(key)){tile.remove();this.tiles.delete(key);}
    for(const marker of this.markers){const p=this.screen(marker.point);marker.element.style.left=`${p[0]}px`;marker.element.style.top=`${p[1]}px`;}
    const d=this.line.map((p,i)=>{const [x,y]=this.screen(p);return `${i?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`;}).join(' ');
    this.paths.forEach(path=>path.setAttribute('d',d));
  }
  tileMessage(){
    const tiles=[...this.tiles.values()];
    if(tiles.some(t=>t.dataset.loaded))this.onError('');
    else if(tiles.length&&tiles.every(t=>t.dataset.failed))this.onError('底图暂时无法加载。请检查网络；地点与路线仍保留。');
  }
}
