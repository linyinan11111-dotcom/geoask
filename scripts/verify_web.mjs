// Run against scripts/serve_web_fixture.py; all trip data is explicitly synthetic.
// npm install --prefix .cache/browser-tools --no-audit --no-fund @playwright/test
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(path.join(root,'.cache/browser-tools/package.json'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:process.env.GEOASK_BROWSER||'msedge'});
const context=await browser.newContext({viewport:{width:1440,height:960},deviceScaleFactor:1});
const page=await context.newPage();
const errors=[];page.on('pageerror',error=>errors.push(error.message));
page.on('console',message=>{if(message.type()==='error'&&message.text().includes('Content Security Policy'))errors.push(message.text());});
const base=process.env.GEOASK_WEB_URL||'http://127.0.0.1:8766';
const waitReady=async()=>{await page.waitForFunction(()=>!document.body.classList.contains('is-busy'));};
const query=async(text,{keepPicker=false}={})=>{
  await page.locator('#query').fill(text);await page.locator('#query-submit').click();await waitReady();
  if(!keepPicker&&await page.locator('#candidates-dialog').isVisible())await page.locator('#candidate-dismiss').click();
};
const screenshot=async name=>page.screenshot({path:path.join(root,name),fullPage:true,animations:'disabled'});
const waitMap=async()=>{
  await page.waitForFunction(()=>{
    const tiles=[...document.querySelectorAll('.tile-layer img')];
    return tiles.length&&tiles.every(img=>img.complete);
  },{},{timeout:20000}).catch(()=>{});
};
const markersVisible=async()=>{
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert(await page.evaluate(()=>{
  const map=document.querySelector('#map').getBoundingClientRect();
  const intersects=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
  const panels=[...document.querySelectorAll('.sidebar-scroll,.sidebar-status,.route-card,.brand,.top-actions,.map-controls,.map-legend,.map-title,.map-tip')]
    .filter(p=>p.getClientRects().length).map(p=>p.getBoundingClientRect()).filter(r=>intersects(map,r));
  return [...document.querySelectorAll('.origin-marker,.stop-marker')].every(m=>{
    const bounds=[m.getBoundingClientRect(),...Array.from(m.querySelectorAll('.marker-label'),l=>l.getBoundingClientRect())];
    return bounds.every(r=>r.left>=map.left&&r.right<=map.right&&r.top>=map.top&&r.bottom<=map.bottom&&!panels.some(p=>intersects(r,p)));
  });
  }),'selected markers and labels must stay inside the map and clear of floating controls');
};
let steps=0;
try{
  await mkdir(path.join(root,'.cache/web-verification'),{recursive:true});
  await page.goto(base);await waitReady();
  await page.evaluate(async()=>{
    const token=document.querySelector('meta[name="geoask-token"]').content;
    const headers={'X-Geoask-Token':token,'Content-Type':'application/json'};
    const response=await fetch('/api/reset',{method:'POST',headers,body:'{}'});
    const {job}=await response.json();
    if(!job)throw new Error('Cannot reset fixture state');
    for(let i=0;i<100;i++){
      const state=await (await fetch('/api/jobs/'+job,{headers})).json();
      if(state.status==='done')return;
      await new Promise(r=>setTimeout(r,50));
    }
    throw new Error('Fixture reset timed out');
  });
  await page.reload();await waitReady();
  assert.equal(await page.title(),'geoask · 地点查询与路线规划');
  const coordinates=await page.evaluate(async()=>{
    const {project,unproject,toGCJ,toWGS}=await import('/map.js');
    return [[116.46,39.92],[-73.9857,40.7484],[179.9,-20]].map(p=>({original:p,mercator:unproject(project(p,15),15),gcj:toWGS(toGCJ(p))}));
  });
  for(const c of coordinates)for(let i=0;i<2;i++){assert(Math.abs(c.original[i]-c.mercator[i])<1e-7);assert(Math.abs(c.original[i]-c.gcj[i])<1e-6);}
  await waitMap();
  await screenshot('docs/web-ui.png');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);steps++;

  await page.locator('#help-button').click();assert(await page.locator('#help-dialog').isVisible());await page.keyboard.press('Escape');
  await page.locator('[data-query]').first().click();assert((await page.locator('#query').inputValue()).includes('咖啡'));steps++;

  await query('先喝咖啡，再去健身，最后吃晚饭',{keepPicker:true});
  assert(await page.locator('#candidates-dialog').isVisible());
  assert.equal(await page.locator('.candidate-tab').count(),3);
  assert((await page.locator('#candidates-heading').innerText()).includes('第 1 站'));
  assert(await page.locator('#candidates-list .candidate').first().isEnabled());
  await screenshot('.cache/web-verification/place-picker-desktop.png');
  await page.locator('#candidates-list .candidate').nth(1).click();await waitReady();
  assert((await page.locator('#candidates-heading').innerText()).includes('第 2 站'));
  await page.locator('#candidates-list .candidate').first().click();await waitReady();
  assert((await page.locator('#candidates-heading').innerText()).includes('第 3 站'));
  await page.locator('#candidates-list .candidate').first().click();await waitReady();
  assert(await page.locator('#candidates-dialog').isHidden());
  await page.locator('#review-trip').click();assert(await page.locator('#candidates-dialog').isVisible());
  await page.locator('.candidate-tab').nth(2).click();assert((await page.locator('#candidates-heading').innerText()).includes('第 3 站'));
  await page.locator('#candidate-dismiss').click();steps++;

  const beforeCandidateClick=await page.locator('.stop-place').first().innerText();
  await page.locator('.candidate-marker').first().click();
  assert(await page.locator('#candidates-dialog').isVisible());
  assert.equal(await page.locator('.stop-place').first().innerText(),beforeCandidateClick);
  assert(await page.locator('.candidate.highlighted').isVisible());
  await page.keyboard.press('Escape');

  await query('先喝咖啡，再去健身，最后吃晚饭');
  assert.equal(await page.locator('.stop').count(),3);assert(await page.locator('#plan-route').isEnabled());
  await page.locator('#plan-route').click();await waitReady();
  assert(await page.locator('#route-metrics').isVisible());
  assert((await page.locator('.route-line').getAttribute('d')).startsWith('M'));
  await markersVisible();
  const markerAppearance=await page.evaluate(()=>{
    const classes=['origin-marker','stop-marker','candidate-marker'];
    return classes.map(cls=>{
      const marker=document.querySelector('.'+cls);const style=getComputedStyle(marker,':before');
      return {color:style.backgroundColor,border:style.borderRadius,width:marker.getBoundingClientRect().width};
    });
  });
  assert.equal(new Set(markerAppearance.map(m=>m.color)).size,3);
  assert(markerAppearance[1].width>=40);assert(await page.locator('.map-legend').isVisible());
  await page.locator('#navigation-button').click();assert.equal(await page.locator('.navigation-leg').count(),3);await page.keyboard.press('Escape');
  const downloadEvent=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadEvent;
  assert(download.suggestedFilename().endsWith('.csv'));await download.saveAs(path.join(root,'.cache/web-verification/trip.csv'));steps++;

  await page.locator('.stop-place').first().click();
  assert.equal(await page.locator('#candidates-list .candidate').count(),3);
  assert.equal(await page.locator('#candidates-list script').count(),0);
  await page.locator('#candidates-list .candidate').nth(1).click();await waitReady();
  assert((await page.locator('.stop-place').first().innerText()).includes('咖啡馆 B'));
  assert.equal(await page.locator('.route-line').getAttribute('d'),'');
  assert(await page.locator('#route-metrics').isHidden());steps++;

  await page.locator('[data-mode="cycling"]').click();await page.locator('#plan-route').click();await waitReady();
  assert((await page.locator('#status-text').innerText()).includes('路线已规划'));
  await waitMap();await markersVisible();await screenshot('.cache/web-verification/trip-desktop.png');steps++;
  await page.setViewportSize({width:960,height:680});
  await page.waitForTimeout(200);await markersVisible();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),false);
  await screenshot('.cache/web-verification/trip-compact.png');
  await page.setViewportSize({width:1440,height:960});
  await page.waitForTimeout(200);

  await page.locator('#parking-enabled').check();await page.locator('#find-parking').click();await waitReady();
  await page.locator('#parking-select').selectOption('1');await page.locator('#plan-route').click();await waitReady();
  assert(await page.locator('.parking-marker').isVisible());
  assert((await page.locator('#route-note').innerText()).includes('停车场'));
  await page.locator('#parking-enabled').uncheck();assert(await page.locator('#route-metrics').isHidden());steps++;

  await query('先去鸟巢，再找餐厅',{keepPicker:true});assert(await page.locator('#plan-route').isDisabled());
  assert(await page.locator('#candidates-dialog').isVisible());
  assert(await page.locator('.stop-place').nth(1).isDisabled());
  await page.locator('#candidates-list .candidate').first().click();await waitReady();
  assert((await page.locator('#candidates-heading').innerText()).includes('第 2 站'));
  await page.locator('#candidates-list .candidate').first().click();await waitReady();
  assert(await page.locator('#plan-route').isEnabled());steps++;

  await page.locator('#preview').check();await query('先咖啡，再健身');
  assert.equal(await page.locator('.stop').count(),2);assert(await page.locator('#route-card').isHidden());assert(await page.locator('#export').isDisabled());
  assert(await page.locator('#candidates-dialog').isHidden());
  assert(await page.locator('#review-trip').isHidden());
  await page.locator('#preview').uncheck();await query('测试错误');
  assert.equal(await page.locator('.stop').count(),0);assert((await page.locator('#status-text').innerText()).includes('失败'));
  await query('先咖啡，再健身');assert.equal(await page.locator('.stop').count(),2);steps++;

  await page.locator('#origin-button').click();await page.locator('#longitude').fill('116.47');await page.locator('#origin-form button[type="submit"]').click();await waitReady();
  assert.equal(await page.locator('.stop').count(),0);assert((await page.locator('#origin-coordinates').innerText()).includes('116.470000'));steps++;

  await page.locator('#settings-button').click();await page.locator('#llm-url').fill('https://changed.example/v1');await page.locator('#settings-form button[type="submit"]').click();await waitReady();
  const token=await page.locator('meta[name="geoask-token"]').getAttribute('content');
  let response=await page.request.get(base+'/api/config',{headers:{'X-Geoask-Token':token}});let config=await response.json();
  assert.equal(config.has_llm_key,false);assert.equal(config.has_amap_key,true);assert(!JSON.stringify(config).includes('web-test-'));
  await query('咖啡');assert(await page.locator('#settings-dialog').isVisible());
  await page.locator('#llm-url').fill('https://api.deepseek.com');await page.locator('#llm-key').fill('web-test-llm');
  await page.locator('#settings-form button[type="submit"]').click();await waitReady();steps++;

  await page.setViewportSize({width:390,height:844});await page.goto(base);await waitReady();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await waitMap();await screenshot('.cache/web-verification/mobile-initial.png');
  await query('骑车先喝咖啡，再健身',{keepPicker:true});assert.equal(await page.locator('.stop').count(),2);
  assert(await page.locator('#candidates-dialog').isVisible());
  assert.equal(await page.evaluate(()=>document.querySelector('#candidates-dialog').scrollWidth>document.querySelector('#candidates-dialog').clientWidth),false);
  await screenshot('.cache/web-verification/place-picker-mobile.png');
  await page.locator('#candidates-list .candidate').first().click();await waitReady();
  await page.locator('#candidates-list .candidate').first().click();await waitReady();
  await page.locator('#plan-route').click();await waitReady();assert(await page.locator('#route-metrics').isVisible());
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await waitMap();await markersVisible();await screenshot('.cache/web-verification/trip-mobile.png');steps++;
  assert.deepEqual(errors,[]);
  await writeFile(path.join(root,'.cache/web-verification/result.json'),JSON.stringify({passed:true,steps,browser:'Chromium / Edge',desktop:[1440,960],mobile:[390,844],provider_data:'synthetic browser test fixtures',errors},null,2));
  console.log(`Web verification passed: ${steps} flows; desktop and mobile; no page or CSP errors.`);
}finally{await browser.close();}
