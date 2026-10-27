/**
 * GlassRenderer — dependency-free WebGL2 material compositor.
 * Semantic DOM supplies the geometry. The scene lives in a sampled framebuffer;
 * each rounded lens refracts that actual image, then contributes to later lenses.
 * It does not capture arbitrary DOM behind it (see README).
 */
import {tokens} from '../tokens.js';
import {readOpticalState} from './optics.js';
import {observeGPU} from './context-budget.js';
const VERTEX = `#version 300 es
in vec2 aPosition;
uniform vec2 uResolution;
uniform vec4 uQuad;
out vec2 vPixel;
void main(){
  vPixel = uQuad.xy + (aPosition * .5 + .5) * uQuad.zw;
  gl_Position = vec4(vPixel / uResolution * 2. - 1., 0., 1.);
}`;

const BACKGROUND = `#version 300 es
precision highp float;
in vec2 vPixel;
out vec4 outColor;
uniform sampler2D uArtwork;
uniform vec2 uResolution;
uniform float uTime;
uniform float uAnimate;
uniform float uBrightness;
uniform float uGrid;
void main(){
  vec2 uv = vPixel / uResolution;
  float movement = uAnimate * (1. - uGrid);
  uv.x += sin(uv.y * 5.8 + uTime * .21) * .012 * movement;
  uv.y += sin(uv.x * 5. + uTime * .16) * .010 * movement;
  vec3 c = texture(uArtwork, clamp(uv, .001, .999)).rgb;
  c *= uBrightness;
  outColor = vec4(c, 1.);
}`;

const COPY = `#version 300 es
precision highp float;
in vec2 vPixel;
out vec4 outColor;
uniform sampler2D uScene;
uniform vec2 uResolution;
void main(){ outColor = texture(uScene, vPixel / uResolution); }
`;

const LENS = `#version 300 es
precision highp float;
in vec2 vPixel;
out vec4 outColor;
uniform sampler2D uScene;
uniform vec2 uResolution;
uniform vec4 uBox;
uniform vec2 uLocalSize;
uniform mat2 uInverse;
uniform vec2 uBend;
uniform float uPressure;
uniform float uEnergy;
uniform vec2 uPointer;
uniform float uRadius;
uniform float uScale;
uniform float uRefraction;
uniform float uBlur;
uniform float uDispersion;
uniform float uTint;
uniform float uDark;
uniform float uCompare;
uniform float uHover;
uniform vec4 uAccent;
uniform float uOpacity;
uniform float uOpticKind;
uniform vec4 uOptic;
uniform vec3 uEmission;
uniform float uFlow;
uniform float uZoom;
uniform float uWaveTime;

float roundedBox(vec2 p, vec2 b, float r){
  vec2 q = abs(p) - b + r;
  return min(max(q.x,q.y),0.) + length(max(q,0.)) - r;
}
float surfaceDistance(vec2 world){
  vec2 p=uInverse*world;
  vec2 halfSize=uLocalSize*.5;
  vec2 q=clamp(p/max(halfSize,vec2(1.)),vec2(-1.3),vec2(1.3));
  // A dragged surface bows between its corners, then settles with the spring.
  // This same shape drives silhouette, refraction normals and specular light.
  p -= uBend * vec2(1.-q.y*q.y,1.-q.x*q.x)*.7;
  float r=min(uRadius*(1.+uPressure*.15),min(halfSize.x,halfSize.y));
  return roundedBox(p,halfSize,r);
}
vec3 sampleScene(vec2 px){
  return texture(uScene,clamp(px/uResolution,vec2(.001),vec2(.999))).rgb;
}
vec3 blurred(vec2 px, float blur){
  vec3 c = sampleScene(px) * .24;
  c += sampleScene(px + vec2(blur,0.)) * .12;
  c += sampleScene(px - vec2(blur,0.)) * .12;
  c += sampleScene(px + vec2(0.,blur)) * .12;
  c += sampleScene(px - vec2(0.,blur)) * .12;
  c += sampleScene(px + vec2(blur,blur)*.707) * .07;
  c += sampleScene(px - vec2(blur,blur)*.707) * .07;
  c += sampleScene(px + vec2(blur,-blur)*.707) * .07;
  c += sampleScene(px + vec2(-blur,blur)*.707) * .07;
  return c;
}
void main(){
  vec2 center = uBox.xy + uBox.zw * .5;
  vec2 p = vPixel - center;
  vec2 b = uLocalSize * .5;
  vec2 local = uInverse*p;
  vec2 opticalUV = clamp(local/max(uLocalSize,vec2(1.))+.5,0.,1.);
  float d = surfaceDistance(p);
  vec3 base = sampleScene(vPixel);
  bool progressGlass = uOpticKind > .5 && uOpticKind < 1.5;
  bool wellGlass = uOpticKind > 1.5 && uOpticKind < 2.5;
  bool fieldGlass = uOpticKind > 2.5 && uOpticKind < 3.5;
  bool magnifier = uOpticKind > 3.5;
  float filled = progressGlass ? (1.-smoothstep(uOptic.x-.007,uOptic.x+.007,opticalUV.x))*step(.0001,uOptic.x) : 0.;
  vec3 sourceColor = mix(vec3(.42,.86,1.),uEmission,uOptic.y);
  vec2 emitter = vec2(-b.x+uLocalSize.x*uOptic.x,0.);
  float sourceGlow = exp(-length((local-emitter)/vec2(23.,15.)/uScale)*1.6)*step(.0001,uOptic.x);
  float halo = exp(-max(d,0.)/(7.*uScale));
  if(progressGlass) base += sourceColor*(sourceGlow*.23+halo*uOptic.y*.075)*uOpacity;
  if(wellGlass) base += vec3(.37,.8,1.)*halo*uOptic.w*.14*uOpacity;
  if(uOptic.z>0.) base += uEmission*halo*uOptic.z*.055*uOpacity;
  float shadowD = surfaceDistance(p+vec2(0.,(5.+uPressure*3.)*uScale));
  float shadow = exp(-max(shadowD,0.)/((7.+uPressure*2.)*uScale))*.078;
  base *= 1. - shadow * uOpacity * smoothstep(-.5*uScale,1.5*uScale,d);
  if(d > 1.5*uScale){outColor=vec4(base,1.);return;}

  float e = .7 * uScale;
  vec2 n2 = normalize(vec2(
    surfaceDistance(p+vec2(e,0.))-surfaceDistance(p-vec2(e,0.)),
    surfaceDistance(p+vec2(0.,e))-surfaceDistance(p-vec2(0.,e))
  )+vec2(.00001));
  float bevel = min(min(b.x,b.y)*.48,19.*uScale);
  float edge = clamp(1.+d/bevel,0.,1.);
  float slope = pow(edge,1.65)*.94;
  vec2 faceNormal=clamp(p/max(b,vec2(1.)),vec2(-1.),vec2(1.))*.045*(1.-edge);
  vec2 normalXY=n2*slope+faceNormal;
  if(wellGlass) normalXY *= mix(-1.,1.,uOptic.z);
  vec3 normal = vec3(normalXY,sqrt(max(1.-dot(normalXY,normalXY),.001)));
  // Snell's law: air (1.0) -> a glass-like medium (1.45).
  vec3 ray = refract(vec3(0.,0.,-1.),normal,1./1.45);
  float thickness = (18.+uRefraction*57.)*uScale*(1.+uPressure*.13+uEnergy*.12);
  vec2 offset = ray.xy/max(-ray.z,.05)*thickness*uRefraction;
  offset -= p * .016 * uRefraction;
  offset *= 1.+filled*.85;
  if(progressGlass) offset.y += sin(opticalUV.x*24.-uWaveTime*2.2)*filled*2.4*uScale;
  offset *= 1.-uCompare;
  vec2 at = vPixel + offset;
  if(magnifier) at = center+p/max(uZoom,1.)+offset*.18;
  float blur = uBlur * 10. * uScale + uCompare * 5. * uScale;
  blur *= magnifier ? .12 : 1.-filled*.65;
  vec3 c = blurred(at,blur);
  vec2 split = n2*pow(edge,2.8)*uDispersion*2.6*uScale*(1.-uCompare);
  c.r += (sampleScene(at+split).r-sampleScene(at).r)*.8;
  c.b += (sampleScene(at-split).b-sampleScene(at).b)*.8;
  // Very slight saturation lift plus an adaptive, readable material tint.
  float luma = dot(c,vec3(.2126,.7152,.0722));
  c = mix(vec3(luma),c,1.035);
  vec3 tint = mix(vec3(.97,.985,1.),vec3(.16,.24,.34),uDark);
  c = mix(c,tint,uTint+uBlur*.12+uCompare*.11+uDark*.10);
  c = mix(c,uAccent.rgb,uAccent.a);
  if(fieldGlass){
    vec2 q=clamp((local+b-vec2(40.*uScale))/max(uLocalSize-vec2(80.*uScale),vec2(1.)),0.,1.);
    vec3 low=mix(vec3(.50,.66,.82),vec3(.89,.69,.47),q.x);
    vec3 high=mix(vec3(.60,.55,.69),vec3(.83,.58,.59),q.x);
    c=mix(low,high,q.y);
    vec2 lines=abs(fract(q*8.+.5)-.5)/max(fwidth(q*8.),vec2(.001));
    float grid=1.-smoothstep(.2,.9,min(lines.x,lines.y));
    float axes=1.-smoothstep(.002,.006,min(abs(q.x-.5),abs(q.y-.5)));
    c=mix(c,vec3(.97),grid*.18+axes*.26);
  }

  vec2 light = normalize(uPointer-center+vec2(-80.,150.)*uScale);
  float directional = pow(max(dot(n2,light),0.),7.);
  float opposite = pow(max(dot(n2,-light),0.),7.)*.52;
  float fresnel = .04+.96*pow(1.-normal.z,5.);
  float outline = 1.-smoothstep(.12*uScale,1.1*uScale,abs(d+.65*uScale));
  float edgeLight = (directional+opposite)*pow(edge,4.);
  float spec = edgeLight*.065 + outline*(.018+directional*.35+opposite*.15);
  spec += fresnel*.035;
  spec *= 1.-uCompare;
  vec3 environment=sampleScene(vPixel+n2*34.*uScale);
  c=mix(c,environment,fresnel*.12*(1.-uCompare));
  c += mix(vec3(1.),environment,.18)*spec;
  float caustic=exp(-pow((d+3.*uScale)/(1.65*uScale),2.));
  c += caustic*opposite*.038*(1.-uCompare);
  // A broad, quiet reflection follows the pointer inside the material.
  float glint = exp(-length((vPixel-uPointer)/vec2(180.,150.)/uScale)*2.);
  c += glint*.024*(.4+uHover)*(1.-uCompare);
  vec2 face=p/max(b,vec2(1.));
  float broadReflection=exp(-pow((face.x*.55+face.y*.8-light.x*.4)*1.6,2.));
  vec3 ambient=sampleScene(center+vec2(-b.x*.6,b.y*.55));
  c=mix(c,clamp(ambient+vec3(.15),0.,1.),broadReflection*(.045+uPressure*.035)*(1.-uCompare));
  c -= pow(edge,8.)*max(dot(n2,vec2(.45,-.89)),0.)*.085*(1.-uCompare);
  if(wellGlass){
    // Reverse the bevel and occlusion: the receiver sits below the glass plane.
    c -= pow(edge,2.)*max(dot(n2,normalize(vec2(-.4,.9))),0.)*.24*(1.-uOptic.z);
    c += pow(edge,4.)*max(dot(n2,normalize(vec2(.4,-.9))),0.)*.19*(1.-uOptic.z);
    c += vec3(.3,.76,1.)*uOptic.w*(pow(edge,3.)*.35+.04);
    c=mix(c,c*vec3(.89,1.04,.95)+uEmission*.045,uOptic.z*.55);
  }
  float phase=fract(atan(local.y/max(b.y,1.),local.x/max(b.x,1.))/6.2831853+.5);
  float travel=fract(phase-uFlow*1.15+1.);
  float flow=uFlow>=0. ? exp(-pow(travel/.105,2.))*sin(3.14159265*clamp(uFlow,0.,1.)) : 0.;
  float lightEdge=exp(-pow((d+1.4*uScale)/(1.25*uScale),2.));
  float innerEdge=exp(-pow((d+4.*uScale)/(3.*uScale),2.));
  if(progressGlass){
    float fiberY=local.y/uScale-sin(opticalUV.x*16.-uWaveTime*2.8)*1.5-sin(opticalUV.x*31.-uWaveTime*4.1)*.35;
    float fiber=exp(-pow(fiberY/1.0,2.))*.24+exp(-pow((fiberY-5.)/.65,2.))*.11;
    float spark=exp(-pow((fract(opticalUV.x-uWaveTime*.12)-.08)/.065,2.));
    c += sourceColor*(filled*(fiber*(1.+spark*.42)+innerEdge*.10)+sourceGlow*.42+lightEdge*uOptic.y*.26);
    c += mix(vec3(.8,.98,1.),uEmission,uOptic.y)*(lightEdge*.75+innerEdge*.15)*flow;
  }
  c += uEmission*uOptic.z*(lightEdge*.28+innerEdge*.045);
  if(!progressGlass) c += mix(uEmission,vec3(1.),.45)*uOptic.z*(lightEdge*.8+innerEdge*.18)*flow;
  float aa=max(fwidth(d)*.7,.5*uScale);
  float mask = 1.-smoothstep(-aa,aa,d);
  outColor=vec4(mix(base,c,mask*uOpacity),1.);
}`;

function shader(gl, type, source) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(s); gl.deleteShader(s); throw new Error(message);
  }
  return s;
}

function program(gl, fragment) {
  const p = gl.createProgram();
  const vs = shader(gl, gl.VERTEX_SHADER, VERTEX), fs = shader(gl, gl.FRAGMENT_SHADER, fragment);
  gl.attachShader(p,vs); gl.attachShader(p,fs); gl.linkProgram(p);
  gl.deleteShader(vs); gl.deleteShader(fs);
  if (!gl.getProgramParameter(p,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const uniforms = {};
  for (let i=0;i<gl.getProgramParameter(p,gl.ACTIVE_UNIFORMS);i++) {
    const u=gl.getActiveUniform(p,i); uniforms[u.name]=gl.getUniformLocation(p,u.name);
  }
  return { p, uniforms };
}

export class GlassRenderer {
  constructor(canvas, stage, options={}) {
    this.canvas=canvas; this.stage=stage;
    this.artwork=typeof options.artwork==='function'?options.artwork:null;
    this.settings={...tokens.materials.thick,brightness:.95,motion:true,opaque:false,compare:false,scene:'aurora',...options};
    this.pointer={x:0,y:0}; this.smoothPointer={x:0,y:0};
    this.time=0; this.lastTime=0; this.frame=0; this.dirty=true; this.rectDirty=true;
    this.geometry=[]; this.visible=true; this.destroyed=false; this.animatingUntil=0;
    this.reducedMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.gl=null;this.suspended=true;this.width=0;this.height=0;this.dpr=1;this.onState=options.onState;
    canvas.hidden=true;canvas.dataset.gpuState='idle';stage.classList.add('fallback');
    this.resizeObserver=new ResizeObserver(()=>{this.resize();this.invalidate(150);});
    this.resizeObserver.observe(stage);
    this.mutationObserver=new MutationObserver(()=>this.invalidate(600));
    this.mutationObserver.observe(stage,{subtree:true,childList:true,attributes:true,attributeFilter:['class','style','hidden','aria-selected','aria-pressed','aria-checked']});
    this.intersectionObserver=new IntersectionObserver(entries=>{this.visible=entries[0].isIntersecting;if(this.visible)this.invalidate();});
    this.intersectionObserver.observe(stage);
    this.abort=new AbortController(); const signal=this.abort.signal;
    stage.addEventListener('pointermove',e=>{
      const r=stage.getBoundingClientRect();
      this.pointer.x=(e.clientX-r.left)*this.dpr;
      this.pointer.y=(r.height-e.clientY+r.top)*this.dpr;
      this.invalidate(this.reducedMotion?0:220,false);
    },{signal,passive:true});
    stage.addEventListener('pointerdown',()=>this.invalidate(650),{signal,passive:true});
    stage.addEventListener('pointerup',()=>this.invalidate(650),{signal,passive:true});
    stage.addEventListener('lg:optics',event=>this.invalidate(event.detail.duration,false),{signal});
    // Scroll does not bubble, so capture it at the stage. Descendant overflow
    // viewports otherwise leave cached lens rectangles at their old positions.
    stage.addEventListener('scroll',()=>this.invalidate(),{capture:true,signal,passive:true});
    window.addEventListener('resize',()=>this.invalidate(300),{signal,passive:true});
    document.addEventListener('visibilitychange',()=>{this.lastTime=0;if(!document.hidden)this.invalidate();},{signal});
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();this.lossObserved=true;this.lost=true;cancelAnimationFrame(this.frame);this.frame=0;canvas.hidden=true;stage.classList.remove('webgl-ready');stage.classList.add('fallback');if(!this.suspended)options.onState?.('lost');if(this.gpuLease?.active)this.resumeGPU();},{signal});
    canvas.addEventListener('webglcontextrestored',()=>{this.lossObserved=false;if(this.destroyed||!this.gpuLease?.active){this.contextExtension?.loseContext();return;}this.startGPU();},{signal});
    this.gpuLease=observeGPU(stage,()=>this.resumeGPU(),()=>this.suspendGPU());
  }

  resumeGPU(){
    if(this.destroyed)return;
    if(this.gl?.isContextLost()){
      if(this.lossObserved){this.canvas.dataset.gpuState='restoring';this.contextExtension?.restoreContext();}
      return;
    }
    this.startGPU();
  }

  startGPU(){
    try{
      this.gl ||= this.canvas.getContext('webgl2',{alpha:false,antialias:false,preserveDrawingBuffer:true,powerPreference:'low-power'});
      if(!this.gl)throw new Error('WebGL2 unavailable');
      this.contextExtension=this.gl.getExtension('WEBGL_lose_context');
      this.suspended=false;this.lost=false;this.initGL();this.resize();
      if(!this.hasPointer){this.pointer={x:this.width*.25,y:this.height*.85};this.smoothPointer={...this.pointer};this.hasPointer=true;}
      this.canvas.hidden=false;this.canvas.dataset.gpuState='active';this.stage.classList.add('webgl-ready');this.stage.classList.remove('fallback');
      this.onState?.('ready');this.lastTime=0;this.invalidate(150);
    }catch(error){this.suspendGPU();this.onState?.('lost');console.warn('Glass initialization failed',error);}
  }

  suspendGPU(){
    this.suspended=true;cancelAnimationFrame(this.frame);this.frame=0;
    this.canvas.hidden=true;this.canvas.dataset.gpuState='suspended';this.stage.classList.remove('webgl-ready');this.stage.classList.add('fallback');
    if(this.gl&&!this.gl.isContextLost())this.contextExtension?.loseContext();
  }

  initGL() {
    const gl=this.gl;
    this.programs={background:program(gl,BACKGROUND),copy:program(gl,COPY),lens:program(gl,LENS)};
    this.buffer=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
    this.vao=gl.createVertexArray();gl.bindVertexArray(this.vao);
    this.textures={art:this.makeTexture(),scene:this.makeTexture(),composite:this.makeTexture()};
    this.targets=['scene','composite'].map(name=>{
      const framebuffer=gl.createFramebuffer();gl.bindFramebuffer(gl.FRAMEBUFFER,framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,this.textures[name],0);
      return {framebuffer,texture:this.textures[name]};
    });
    gl.bindFramebuffer(gl.FRAMEBUFFER,null);
    gl.disable(gl.DEPTH_TEST);gl.disable(gl.BLEND);
    this.width=0;this.height=0;
  }

  makeTexture() {
    const gl=this.gl, t=gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D,t);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    return t;
  }

  resize() {
    // ResizeObserver can deliver after context loss or teardown.
    if(this.destroyed||this.suspended||this.lost||!this.gl||this.gl.isContextLost?.())return;
    const rect=this.stage.getBoundingClientRect();
    this.dpr=Math.min(devicePixelRatio||1,1.75);
    const width=Math.max(1,Math.round(rect.width*this.dpr)),height=Math.max(1,Math.round(rect.height*this.dpr));
    if (width===this.width&&height===this.height)return;
    this.width=width;this.height=height;this.canvas.width=width;this.canvas.height=height;
    const gl=this.gl;
    for(const target of this.targets){
      gl.bindTexture(gl.TEXTURE_2D,target.texture);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,width,height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
      gl.bindFramebuffer(gl.FRAMEBUFFER,target.framebuffer);
      if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw new Error('Incomplete scene framebuffer');
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER,null);
    this.setScene(this.settings.scene); this.rectDirty=true;
  }

  setScene(name) {
    this.settings.scene=name;
    if(this.suspended||!this.gl||this.gl.isContextLost?.())return;
    const art=document.createElement('canvas');art.width=this.width;art.height=this.height;
    const ctx=art.getContext('2d'),w=art.width,h=art.height;
    if(this.artwork){
      this.artwork(ctx,{width:w,height:h,dpr:this.dpr,scene:name});
    }else{
      const palettes={aurora:['#d3e8e3','#badde8','#9db7dc','#929ee0','#d3b4d2','#eccbc1'],sunset:['#f2dfbd','#f5cdaf','#e7a59c','#c387a2','#9f89b2','#dcb9c3'],ocean:['#326975','#286d85','#225879','#293e68','#4a5486','#7184a3']};
      if(name==='grid'){
        ctx.fillStyle='#e8ebef';ctx.fillRect(0,0,w,h);ctx.lineWidth=this.dpr;
        const step=34*this.dpr;
        for(let x=0;x<w;x+=step){ctx.strokeStyle=Math.round(x/step)%4===0?'#a1acbb':'#c4cbd5';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();}
        for(let y=0;y<h;y+=step){ctx.strokeStyle=Math.round(y/step)%4===0?'#a1acbb':'#c4cbd5';ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
      }else{
        const p=palettes[name]||palettes.aurora;
        const gradient=ctx.createLinearGradient(0,0,w,h);gradient.addColorStop(0,p[0]);gradient.addColorStop(.5,p[1]);gradient.addColorStop(1,p[5]);ctx.fillStyle=gradient;ctx.fillRect(0,0,w,h);
        // Raised, curved color sheets: directional face gradients, soft inter-layer
        // occlusion and a broad grazing highlight, baked once when a scene changes.
        const ribbon=(offset,color1,color2)=>{
          const contour=new Path2D();contour.moveTo(-w*.1,h*(.98+offset));
          contour.bezierCurveTo(w*.13,h*(.39+offset),w*.48,h*(.86+offset),w*1.1,h*(-.32+offset));
          const shape=new Path2D(contour);shape.lineTo(w*1.15,h*1.4);shape.lineTo(-w*.1,h*1.4);shape.closePath();
          const g=ctx.createLinearGradient(w*.05,h*.1,w*.87,h*.92);g.addColorStop(0,color1);g.addColorStop(.48,color2);g.addColorStop(1,p[5]);
          ctx.save();ctx.shadowColor=name==='ocean'?'#0b23484a':'#38486830';ctx.shadowBlur=27*this.dpr;ctx.shadowOffsetY=-7*this.dpr;ctx.fillStyle=g;ctx.fill(shape);ctx.restore();
          ctx.save();ctx.clip(shape);
          ctx.filter=`blur(${15*this.dpr}px)`;ctx.strokeStyle=name==='ocean'?'#bce9ff24':'#ffffff45';ctx.lineWidth=37*this.dpr;ctx.stroke(contour);
          ctx.filter='none';ctx.strokeStyle='#ffffff25';ctx.lineWidth=1.2*this.dpr;ctx.stroke(contour);
          const roll=ctx.createLinearGradient(0,h*(.25+offset),w*.7,h*(1.08+offset));roll.addColorStop(0,'#ffffff00');roll.addColorStop(.45,'#ffffff00');roll.addColorStop(.72,'#324e7e13');roll.addColorStop(1,'#ffffff1c');ctx.fillStyle=roll;ctx.fill(shape);
          ctx.restore();
        };
        ribbon(-.31,p[1],p[2]);ribbon(-.10,p[2],p[3]);ribbon(.18,p[3],p[4]);ribbon(.44,p[4],p[5]);
        const glow=ctx.createRadialGradient(w*.12,h*.08,0,w*.12,h*.08,w*.85);glow.addColorStop(0,'#ffffff55');glow.addColorStop(1,'#ffffff00');ctx.fillStyle=glow;ctx.fillRect(0,0,w,h);
      }
      const size=Math.min(w*.265,h*.30);
      ctx.textAlign='center';ctx.textBaseline='middle';ctx.font=`300 ${size}px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`;
      ctx.fillStyle=name==='grid'?'#596578':'#ffffff75';
      ctx.fillText('hello.',w*.47,h*.49);
    }
    const gl=this.gl;gl.bindTexture(gl.TEXTURE_2D,this.textures.art);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,art);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
    this.invalidate();
  }

  set(next) {
    const previousScene=this.settings.scene;Object.assign(this.settings,next);
    if (next.scene&&(next.scene!==previousScene||this.artwork))this.setScene(next.scene);
    this.invalidate(400);
  }

  setMotionSource(source){this.motionSource=source;this.invalidate();}

  invalidate(duration=0,geometry=true) {
    if(this.destroyed||this.suspended||this.lost)return;
    this.dirty=true;if(geometry)this.rectDirty=true;
    this.animatingUntil=Math.max(this.animatingUntil,performance.now()+duration);
    if(!this.frame)this.frame=requestAnimationFrame(t=>this.render(t));
  }

  readGeometry() {
    const stage=this.stage.getBoundingClientRect(),s=this.dpr;
    const matrices=new Map();
    const transformFor=el=>{
      if(!el||el===this.stage)return new DOMMatrix();
      if(matrices.has(el))return matrices.get(el);
      const transform=getComputedStyle(el).transform;
      const own=transform==='none'?new DOMMatrix():new DOMMatrix(transform);
      own.e=0;own.f=0;
      const total=transformFor(el.parentElement).multiply(own);
      matrices.set(el,total);return total;
    };
    const rank={'xy-field':-1,'optical-well':0,tab:0,note:1,panel:2,dock:3,small:4,'switch-track':4,thumb:5,'slider-thumb':5,selected:6,free:7,'optical-lens':7,menu:8};
    const depth=el=>{let value=0;for(let parent=el.parentElement;parent&&parent!==this.stage;parent=parent.parentElement)if(parent.dataset.glass)value++;return value;};
    const clipFor=el=>{
      let left=stage.left,top=stage.top,right=stage.right,bottom=stage.bottom;
      for(let parent=el.parentElement;parent&&parent!==this.stage;parent=parent.parentElement){
        const cs=getComputedStyle(parent),explicit=parent.dataset.lgClip==='true';
        const clipX=explicit||/^(auto|scroll|hidden|clip)$/.test(cs.overflowX);
        const clipY=explicit||/^(auto|scroll|hidden|clip)$/.test(cs.overflowY);
        if(!clipX&&!clipY)continue;
        const r=parent.getBoundingClientRect(),clientLeft=r.left+parent.clientLeft,clientTop=r.top+parent.clientTop;
        if(clipX){left=Math.max(left,clientLeft);right=Math.min(right,clientLeft+parent.clientWidth);}
        if(clipY){top=Math.max(top,clientTop);bottom=Math.min(bottom,clientTop+parent.clientHeight);}
      }
      return {left:(left-stage.left)*s,right:(right-stage.left)*s,bottom:(stage.bottom-bottom)*s,top:(stage.bottom-top)*s};
    };
    this.geometry=[...this.stage.querySelectorAll('[data-glass]')]
      .filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden')
      .sort((a,b)=>depth(a)-depth(b)||(rank[a.dataset.glass]??0)-(rank[b.dataset.glass]??0))
      .map(el=>{
        const r=el.getBoundingClientRect(),cs=getComputedStyle(el),kind=el.dataset.glass;
        const localW=el.offsetWidth,localH=el.offsetHeight;
        const radius=Math.min(parseFloat(cs.borderTopLeftRadius)||24,localW/2,localH/2)*s;
        const m=transformFor(el),det=m.a*m.d-m.b*m.c;
        const inverse=Math.abs(det)>.001?[m.d/det,m.b/det,m.c/det,m.a/det]:[1,0,0,1];
        let opacity=1;for(let parent=el;parent&&parent!==this.stage;parent=parent.parentElement)opacity*=Number(getComputedStyle(parent).opacity);
        const selected=Number(cs.getPropertyValue('--glass-selection'))||0;
        return {el,kind,opacity,selected,x:(r.left-stage.left)*s,y:(stage.bottom-r.bottom)*s,w:r.width*s,h:r.height*s,r:radius,localW:localW*s,localH:localH*s,inverse,clip:clipFor(el)};
      });
    this.rectDirty=false;
  }

  use(p,quad=[0,0,this.width,this.height]) {
    const gl=this.gl;gl.useProgram(p.p);gl.bindVertexArray(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
    const a=gl.getAttribLocation(p.p,'aPosition');gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,2,gl.FLOAT,false,0,0);
    gl.uniform2f(p.uniforms.uResolution,this.width,this.height);gl.uniform4fv(p.uniforms.uQuad,quad);
  }

  render(now) {
    this.frame=0;
    if(this.destroyed||this.suspended||this.lost||!this.gl||document.hidden||!this.visible)return;
    const dt=this.lastTime?Math.min((now-this.lastTime)/1000,.05):.016;this.lastTime=now;
    if(this.settings.motion&&!this.reducedMotion)this.time+=dt;
    const lerp=this.reducedMotion?1:1-Math.exp(-dt*13);
    this.smoothPointer.x+=(this.pointer.x-this.smoothPointer.x)*lerp;this.smoothPointer.y+=(this.pointer.y-this.smoothPointer.y)*lerp;
    if(this.rectDirty||now<this.animatingUntil)this.readGeometry();
    const gl=this.gl,p=this.programs;
    let opticalMoving=false;
    gl.viewport(0,0,this.width,this.height);gl.activeTexture(gl.TEXTURE0);
    let source=this.targets[0],destination=this.targets[1];
    gl.bindFramebuffer(gl.FRAMEBUFFER,source.framebuffer);gl.bindTexture(gl.TEXTURE_2D,this.textures.art);
    this.use(p.background);
    gl.uniform1i(p.background.uniforms.uArtwork,0);gl.uniform1f(p.background.uniforms.uTime,this.time);
    // Pause freezes time, so the background never jumps back to an initial pose.
    gl.uniform1f(p.background.uniforms.uAnimate,this.reducedMotion?0:1);
    gl.uniform1f(p.background.uniforms.uGrid,this.settings.scene==='grid'?1:0);
    gl.uniform1f(p.background.uniforms.uBrightness,this.settings.brightness);
    gl.drawArrays(gl.TRIANGLES,0,6);
    if(!this.settings.opaque){
      for(const g of this.geometry){
        const pad=36*this.dpr;
        const x=Math.max(0,g.clip.left,Math.floor(g.x-pad)),y=Math.max(0,g.clip.bottom,Math.floor(g.y-pad));
        const right=Math.min(this.width,g.clip.right,Math.ceil(g.x+g.w+pad)),top=Math.min(this.height,g.clip.top,Math.ceil(g.y+g.h+pad));
        const width=right-x,height=top-y;
        if(width<=0||height<=0)continue;
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER,source.framebuffer);
        gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,destination.framebuffer);
        gl.blitFramebuffer(0,0,this.width,this.height,0,0,this.width,this.height,gl.COLOR_BUFFER_BIT,gl.NEAREST);
        gl.bindFramebuffer(gl.FRAMEBUFFER,destination.framebuffer);
        gl.bindTexture(gl.TEXTURE_2D,source.texture);
        this.use(p.lens,[x,y,width,height]);const u=p.lens.uniforms;
        gl.uniform1i(u.uScene,0);gl.uniform4f(u.uBox,g.x,g.y,g.w,g.h);gl.uniform1f(u.uRadius,g.r);gl.uniform1f(u.uScale,this.dpr);
        gl.uniform2f(u.uLocalSize,g.localW,g.localH);gl.uniformMatrix2fv(u.uInverse,false,g.inverse);
        const effect=readOpticalState(g.el,now,this.reducedMotion);
        const optical=effect?.deformation||this.motionSource?.optics(g.el)||{bend:[0,0],pressure:0,energy:0};
        gl.uniform2f(u.uBend,optical.bend[0]*this.dpr,-optical.bend[1]*this.dpr);
        gl.uniform1f(u.uPressure,optical.pressure);gl.uniform1f(u.uEnergy,optical.energy);
        gl.uniform2f(u.uPointer,this.smoothPointer.x,this.smoothPointer.y);
        const rail=g.kind==='switch-track',thumb=g.kind==='thumb';
        gl.uniform1f(u.uRefraction,this.settings.refraction*(rail?.12:thumb?.45:1));gl.uniform1f(u.uBlur,this.settings.blur);gl.uniform1f(u.uDispersion,this.settings.dispersion*((rail||thumb)?.2:1));
        const tint={panel:.14,dock:.095,small:.12,thumb:.83,'switch-track':.06,'slider-thumb':.18,selected:.19,tab:.18,note:.09,free:.012,menu:.60,'optical-lens':.012,'optical-well':.055}[g.kind]??.13;
        gl.uniform1f(u.uTint,tint);gl.uniform1f(u.uDark,this.settings.scene==='ocean'&&!thumb?1:0);
        const progress=rail?(this.motionSource?.toggleProgress(g.el.parentElement)||0):0;
        gl.uniform4f(u.uAccent,rail?.38-progress*.22:.25,rail?.45+progress*.17:.51,rail?.54-progress*.17:.87,rail?.48+progress*.34:g.selected*.17);
        gl.uniform1f(u.uOpacity,g.opacity*(effect?.opacity??1));
        const waving=effect?.kind===1 && effect.wave && effect.fill>0 && (!this.reducedMotion || effect.forceMotion);
        opticalMoving ||= Boolean(waving);
        gl.uniform1f(u.uOpticKind,effect?.kind||0);
        gl.uniform4f(u.uOptic,effect?.fill||0,effect?.complete||0,effect?.selected||0,effect?.hover||0);
        gl.uniform3fv(u.uEmission,effect?.color||[.32,.73,.51]);
        gl.uniform1f(u.uFlow,effect?.flow??-1);gl.uniform1f(u.uZoom,effect?.zoom||1);
        gl.uniform1f(u.uWaveTime,waving?now*.001:0);
        gl.uniform1f(u.uCompare,this.settings.compare?1:0);gl.uniform1f(u.uHover,g.el.matches(':hover')?1:0);
        gl.drawArrays(gl.TRIANGLES,0,6);
        // Ping-pong linear RGBA targets. Never copy from the browser's managed
        // display framebuffer: its color conversion can create rectangular seams.
        [source,destination]=[destination,source];
      }
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.bindTexture(gl.TEXTURE_2D,source.texture);
    this.use(p.copy);gl.uniform1i(p.copy.uniforms.uScene,0);gl.drawArrays(gl.TRIANGLES,0,6);
    this.dirty=false;
    const moving=this.settings.motion&&!this.reducedMotion&&this.settings.scene!=='grid';
    if(moving||opticalMoving||now<this.animatingUntil)this.frame=requestAnimationFrame(t=>this.render(t));
  }

  destroy() {
    this.destroyed=true;cancelAnimationFrame(this.frame);this.abort.abort();
    this.resizeObserver.disconnect();this.mutationObserver.disconnect();this.intersectionObserver.disconnect();
    this.gpuLease?.destroy();
    const gl=this.gl;if(!gl)return;
    Object.values(this.programs||{}).forEach(p=>gl.deleteProgram(p.p));
    Object.values(this.textures||{}).forEach(t=>gl.deleteTexture(t));(this.targets||[]).forEach(t=>gl.deleteFramebuffer(t.framebuffer));gl.deleteBuffer(this.buffer);gl.deleteVertexArray(this.vao);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  }
}
