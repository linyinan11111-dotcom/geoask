const freezeTokens=value=>{for(const item of Object.values(value))if(item&&typeof item==='object')freezeTokens(item);return Object.freeze(value);};
export const tokens=freezeTokens({
  version:'1.1.0-showcase.1',
  color:{text:'#22252b',muted:'#6b7079',accent:'#2875ed',focus:'#2576e8',page:'#f7f8fa',success:'#236442'},
  type:{family:'-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif',body:16,control:14,caption:12},
  space:{xs:4,sm:8,md:16,lg:24,xl:32},
  radius:{chrome:14,panel:34,lens:60,pill:999},
  surface:{fallback:'rgba(245,250,255,.30)',solid:'#eef2f8',shadow:'inset 0 1px 0 #fff9,inset 1px 0 0 #fff3,0 12px 24px -12px #2135524d'},
  motion:{elasticity:.70,spring:'cubic-bezier(.2,.9,.25,1.22)',fast:160,normal:240,feedback:520},
  optics:{gold:'#e2b54e',green:'#52bb83',light:'#90e8fa',flowDuration:1100,settleDuration:280,magnification:1.8},
  audio:{enabled:true,volume:.28,maxVoices:6,cooldown:70,storageKey:'lens.sound.v1'},
  materials:{clear:{refraction:.65,blur:.12,dispersion:.35},soft:{refraction:.38,blur:.64,dispersion:.12},thick:{refraction:.90,blur:.24,dispersion:.65}},
});
export const materialPresets=tokens.materials;
export function resolveMaterial(value='thick'){
  const source=typeof value==='string'?materialPresets[value]:value;
  if(!source||typeof source!=='object')throw new TypeError('Unknown glass material');
  const result={...materialPresets.thick};
  for(const key of ['refraction','blur','dispersion'])if(key in source){
    if(!Number.isFinite(source[key]))throw new TypeError(`${key} must be finite`);
    result[key]=Math.max(0,Math.min(1,source[key]));
  }
  return result;
}
