// Render the actual optical material, then verify idle, accessibility and fallback.
// Use scripts/serve_web_fixture.py: no real credentials or paid provider calls.
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(path.join(root,'.cache/browser-tools/package.json'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:process.env.GEOASK_BROWSER||'msedge'});
const base=process.env.GEOASK_WEB_URL||'http://127.0.0.1:8766';
const errors=[];
async function open(context){
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('.origin-marker'));
  return page;
}
try{
  await mkdir(path.join(root,'.cache/web-verification'),{recursive:true});
  const context=await browser.newContext({viewport:{width:1440,height:960}});
  await context.addInitScript(()=>{
    window.glassDraws=0;
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...args){
      const ctx=original.call(this,type,...args);
      if(type==='webgl2'&&ctx){
        const draw=ctx.drawArrays;
        ctx.drawArrays=function(...a){window.glassDraws++;return draw.apply(this,a);};
      }
      return ctx;
    };
  });
  const page=await open(context);
  await page.waitForFunction(()=>document.querySelector('.planner-glass-canvas')?.dataset.gpuState==='active');
  await page.waitForFunction(()=>window.glassDraws>0);
  const pixels=await page.evaluate(()=>{
    const canvas=document.querySelector('.planner-glass-canvas'),gl=canvas.getContext('webgl2');
    const colors=[];
    for(let y=5;y<canvas.height;y+=20)for(let x=5;x<canvas.width;x+=30){
      const p=new Uint8Array(4);gl.readPixels(x,y,1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);colors.push([...p].join(','));
    }
    return new Set(colors).size;
  });
  assert(pixels>5,'The real WebGL canvas must render a nonempty varied scene');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight),960,'desktop must fit vertically');
  const suggestion=page.locator('[data-query]').first();
  await suggestion.hover();await page.mouse.down();await page.waitForTimeout(100);
  assert.notEqual(await suggestion.evaluate(el=>getComputedStyle(el).transform),'matrix(1, 0, 0, 1, 0, 0)');
  await page.mouse.up();assert((await page.locator('#query').inputValue()).includes('咖啡'));
  await page.mouse.move(900,90);await page.waitForTimeout(1500);
  const before=await page.evaluate(()=>window.glassDraws);await page.waitForTimeout(400);
  assert.equal(await page.evaluate(()=>window.glassDraws),before,'glass must stop repainting when idle');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await suggestion.evaluate(el=>getComputedStyle(el).transform),'none');
  await page.locator('#help-button').click();
  assert.equal(await page.locator('#help-dialog').evaluate(el=>getComputedStyle(el).animationName),'none');
  await page.keyboard.press('Escape');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>document.querySelector('[data-query]').hasAttribute('data-jelly'));
  assert(await suggestion.getAttribute('data-jelly'),'spring feedback should return with the preference');
  const cdp=await context.newCDPSession(page);
  await cdp.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-transparency',value:'reduce'}]});
  await page.waitForFunction(()=>!document.querySelector('.planner-glass-canvas'));
  const material=await page.locator('#query-form').evaluate(el=>getComputedStyle(el,'::before').backgroundColor);
  assert.equal(material,'rgb(237, 243, 251)','reduced transparency uses an opaque panel');
  await cdp.send('Emulation.setEmulatedMedia',{features:[]});
  await page.waitForFunction(()=>document.querySelector('.planner-glass-canvas')?.dataset.gpuState==='active');
  await page.setViewportSize({width:960,height:680});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),false,'minimum native window height must fit');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await context.close();

  const fallback=await browser.newContext({viewport:{width:390,height:844}});
  await fallback.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...args){return type==='webgl2'?null:original.call(this,type,...args);};
  });
  const noGPU=await open(fallback);
  assert.equal(await noGPU.locator('.planner-glass-canvas').evaluate(el=>el.hidden),true);
  await noGPU.locator('[data-query]').first().click();
  await noGPU.locator('#query-submit').click();
  await noGPU.waitForFunction(()=>document.querySelector('#candidates-dialog').open);
  assert(await noGPU.locator('#candidates-list button').first().isEnabled());
  await noGPU.keyboard.press('Escape');
  assert.equal(await noGPU.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await noGPU.screenshot({path:path.join(root,'.cache/web-verification/glass-fallback-mobile.png'),fullPage:true,animations:'disabled'});
  assert.deepEqual(errors,[]);
  await writeFile(path.join(root,'.cache/web-verification/glass-result.json'),JSON.stringify({passed:true,
    checks:['real WebGL pixels','spring feedback','idle rendering stops','reduced motion','reduced transparency and recovery','no WebGL query and picker'],errors},null,2));
  console.log('Glass verification passed: GPU, spring, idle, reduced motion/transparency, and no-WebGL query.');
}finally{await browser.close();}
