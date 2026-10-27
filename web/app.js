import {TripMap} from './map.js';

const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name="geoask-token"]').content;
const state={config:null,center:[116.46,39.92],originName:'自定义起点',result:null,route:null,parking:null,route_parking:null,mode:'walking',busy:false,activeStop:0,choosingTrip:false,toastTimer:null};
const modeNames={walking:'步行',cycling:'骑行',driving:'驾车'};
const distance=n=>n>=1000?`${(n/1000).toFixed(2)} 公里`:`${Math.round(n)} 米`;
const duration=n=>n>=3600?`${Math.floor(n/3600)} 小时 ${Math.round(n%3600/60)} 分钟`:`${Math.max(1,Math.round(n/60))} 分钟`;
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('icon');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href',`#i-${name}`);svg.append(use);return svg;}
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;}
function status(text,error=false){$('status-text').textContent=text;$('status').classList.toggle('error',error);}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
function dialog(id){if(!$(id).open)$(id).showModal();}
function closeDialogs(){document.querySelectorAll('dialog[open]').forEach(d=>d.close());}

async function api(path,body){
  let response;
  try{response=await fetch(`/api/${path}`,{method:body===undefined?'GET':'POST',headers:{'X-Geoask-Token':token,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});}
  catch{throw new Error('无法连接本地服务，请重新启动 geoask。');}
  let data;try{data=await response.json();}catch{throw new Error('本地服务响应无效，请刷新页面。');}
  if(!response.ok)throw new Error(data.error||'操作失败，请稍后重试。');
  return data;
}
function busy(value){
  state.busy=value;document.body.classList.toggle('is-busy',value);$('status').classList.toggle('busy',value);
  for(const node of document.querySelectorAll('#query-form button,#query-form input,#query-form textarea,#query-form select,#settings-form button,#settings-form input,#settings-form select,#origin-button,#locate,#settings-button,#origin-form button,#origin-form input,#candidates-list button,.stop-place,.mode-picker button,#parking-enabled,#find-parking,#parking-radius,#parking-select'))node.disabled=value;
  $('query-submit').firstElementChild.textContent=value?'正在安排这段旅程…':'理解需求，发现地点';
  document.querySelectorAll('.stop-place').forEach((button,i)=>{button.disabled=value||!state.result?.stops[i]?.candidates.length;});
  document.querySelectorAll('.candidate-tab').forEach((button,i)=>{button.disabled=value||!state.result?.stops[i]?.candidates.length;});
  $('review-trip').disabled=value||!state.result?.stops.some(s=>s.candidates.length);
  updateActions();
}
async function poll(job){
  for(;;){
    const data=await api(`jobs/${job}`);
    if(data.status==='running'){status(data.message);await new Promise(r=>setTimeout(r,350));continue;}
    if(data.state)acceptState(data.state);
    if(data.config)applyConfig(data.config);
    if(data.status==='error')throw new Error(data.error);
    return data;
  }
}
async function action(name,payload={},success){
  if(state.busy)return;
  busy(true);status('正在处理…');
  try{const {job}=await api(name,payload);const result=await poll(job);if(success)await success(result);return result;}
  catch(error){status(error.message,true);toast(error.message);if($('settings-dialog').open){$('settings-error').textContent=error.message;$('settings-error').hidden=false;}return null;}
  finally{busy(false);}
}
function applyConfig(config){
  state.config=config;
  $('service-badge').textContent=config.desktop?'桌面应用已就绪':'本地服务已连接';
  shutdown.textContent=config.desktop?'退出应用':'结束本地服务';
  const ready=config.has_llm_key&&(!config.online||config.has_amap_key);
  $('settings-dot').hidden=ready;
  $('map-source').textContent=config.online?'高德地图':'区域数据';
  $('attribution').textContent=config.map.attribution;
  if(config.warning)toast(config.warning);
}
function acceptState(data){
  const was=state.result?.id;
  Object.assign(state,data);
  if(state.result&&was!==state.result.id){
    if(!was||state.activeStop>=state.result.stops.length)state.activeStop=0;
    if(!was)state.mode=state.result.travel_mode;
  }
  if(!state.parking){$('parking-select').hidden=true;$('parking-select').replaceChildren();$('parking-note').textContent='';}
  render();
}
function updateOrigin(){
  $('origin-title').textContent=state.originName;
  $('origin-coordinates').textContent=`${state.center[0].toFixed(6)}, ${state.center[1].toFixed(6)}`;
}
async function setOrigin(point,name='地图选定起点'){
  if(state.busy){toast('请等待当前操作完成后设置起点。');return;}
  state.center=[...point];state.originName=name;updateOrigin();
  if(state.result)await action('reset',{},()=>status('起点已更新，请重新查询行程。'));
  renderMap();map.goTo(point);
}
const map=new TripMap($('map'),{
  onOrigin:p=>setOrigin(p),
  onCandidate:(stop,index)=>{if(state.busy)return;showCandidates(stop,{highlight:index});},
  onError:text=>{$('map-message').hidden=!text;$('map-message').textContent=text;}
});
function markers(){
  const data=[{kind:'origin',point:state.center,name:state.originName}];
  if(!state.result)return data;
  state.result.stops.forEach((s,i)=>{
    const p=s.candidates[s.selected_index];
    if(p)data.push({kind:'stop',point:[p.longitude,p.latitude],label:String(i+1),name:p.name||'未命名地点',stop:i,active:i===state.activeStop});
  });
  const active=state.result.stops[state.activeStop];
  active?.candidates.forEach((p,i)=>{if(i!==active.selected_index)data.push({kind:'candidate',point:[p.longitude,p.latitude],label:String.fromCharCode(65+i),name:p.name||'未命名地点',stop:state.activeStop,index:i});});
  if(state.route_parking){const p=state.route_parking;data.push({kind:'parking',label:'P',point:[p.longitude,p.latitude],name:p.name||'未命名停车场'});}
  return data;
}
function renderMap(){map.setData(markers(),visibleRoute()?.coordinates||[]);}
function fitMap(){const points=markers().map(m=>m.point);if(visibleRoute())points.push(...state.route.coordinates);map.fit(points,!!state.result);}
function visibleRoute(){
  if(!state.route)return null;
  if($('parking-enabled').checked)return state.route_parking?state.route:null;
  return !state.route_parking&&state.route.travel_mode===state.mode?state.route:null;
}
function render(){
  const r=state.result;$('stop-count').textContent=String(r?.stops.length||0).padStart(2,'0');
  $('welcome-card').hidden=!!r;$('route-card').hidden=!r||r.dry_run;
  $('route-stop-count').textContent=`${r?.stops.length||0} 个目的地`;
  if(r){
    $('stops').replaceChildren();
    r.stops.forEach((s,i)=>{
      const place=s.candidates[s.selected_index],block=el('article',`stop ${i===state.activeStop?'active':''} ${!place?'warning':''}`);
      block.append(el('span','stop-number',i+1));
      const label=el('div','stop-label');label.append(el('span','',s.intent.place_name||s.intent.intent),el('small','stop-selected',place?'已选择':r.dry_run?'仅预览':'待选择'));block.append(label);
      const button=el('button','stop-place');button.type='button';button.disabled=!s.candidates.length||state.busy;button.setAttribute('aria-haspopup','dialog');
      const copy=el('span');copy.append(el('strong','',place?.name|| (r.dry_run?'预览：'+(s.intent.place_name||s.intent.intent):s.candidates.length?'选择具体地点':'等待前站 / 暂无地点')));
      copy.append(el('small','',place?`距${i?'前一站':'起点'} ${distance(place.dist_m)} · ${s.candidates.length} 个候选`:s.candidates.length?`${s.candidates.length} 个候选地点可选`:'确认前一站后继续查询'));
      const cta=el('span','stop-select-action',place?'更换地点':'选择地点');cta.append(icon('chevron'));
      button.append(copy,cta);button.addEventListener('click',()=>showCandidates(i));block.append(button);
      if(s.blocked_reason)block.append(el('p','stop-reason',s.blocked_reason));
      if(s.intent.modifiers.length)block.append(el('p','stop-modifiers',`未用于筛选：${s.intent.modifiers.join('、')}`));
      $('stops').append(block);
    });
  }else{
    const empty=el('div','empty-trip'),route=el('div','empty-route');
    ['span','i','span','i','span'].forEach(t=>route.append(el(t)));
    empty.append(route,el('p','','下一段小旅程，从这里开始'),el('small','','输入需求后，地点会按顺序出现在这里。'));$('stops').replaceChildren(empty);
  }
  renderModes();renderRoute();renderParking();renderMap();updateActions();
}
function updateActions(){
  const r=state.result,ready=r&&!r.dry_run&&r.stops.every(s=>s.selected_index!=null);
  const parkingReady=!$('parking-enabled').checked||($('parking-select').value!==''&&state.parking?.candidates.length);
  $('plan-route').disabled=state.busy||!ready||!parkingReady;
  $('find-parking').disabled=state.busy||!ready;
  $('export').disabled=state.busy||!r||r.dry_run||!r.stops.some(s=>s.selected_index!=null);
  $('plan-route').firstElementChild.textContent=visibleRoute()?'重新规划路线':'规划这段旅程';
}
function renderModes(){document.querySelectorAll('[data-mode]').forEach(b=>{const active=b.dataset.mode===state.mode;b.classList.toggle('active',active);b.setAttribute('aria-pressed',active);});}
function renderRoute(){
  const r=visibleRoute();$('route-metrics').hidden=!r;$('navigation-button').hidden=!r;
  if(r){
    const metrics=$('route-metrics');metrics.replaceChildren();
    const parts=[[r.distance_m>=1000?(r.distance_m/1000).toFixed(2):Math.round(r.distance_m),r.distance_m>=1000?'公里':'米','沿道路距离'],[Math.max(1,Math.round(r.duration_s/60)),'分钟','预计出行时间']];
    parts.forEach(([n,u,l])=>{const group=el('div');group.append(el('strong','',n),el('span','',u),el('small','',l));metrics.append(group);});
    $('route-heading').textContent='好路线，准备出发';
    $('route-note').textContent=state.route_parking?'驾车终点为所选停车场；目的地仍保留。':'预计用时不包含停留时间。';
  }else{
    $('route-heading').textContent='选择你的出发方式';
    const ready=state.result?.stops.every(s=>s.selected_index!=null);
    $('route-note').textContent=ready?'保留站点顺序，沿真实道路规划。':'请先为每一站选择具体地点。';
  }
}
function renderParking(){
  $('parking-fields').hidden=!$('parking-enabled').checked;
  if(!state.parking)return;
  const select=$('parking-select'),previous=select.value;select.replaceChildren(new Option('请选择停车场',''));
  state.parking.candidates.forEach((p,i)=>select.append(new Option(`${p.name||'未命名停车场'} · ${distance(p.dist_m)}`,String(i))));
  select.value=previous;select.hidden=!state.parking.candidates.length;
  $('parking-note').textContent=state.parking.summary;
}
function showCandidates(index,{flow=false,highlight=null}={}){
  if(state.busy||!state.result)return;
  state.activeStop=index;state.choosingTrip=flow;const stop=state.result.stops[index];if(!stop?.candidates.length)return;
  $('candidates-heading').textContent=`为第 ${index+1} 站选择地点`;
  $('candidates-description').textContent=`${stop.intent.place_name||stop.intent.intent} · 按距${index?'前一站':'起点'}的直线距离排序。${index<state.result.stops.length-1?'选择后重新查询后续站点。':'点击下方地点确认选择。'}`;
  $('candidate-tabs').replaceChildren();
  state.result.stops.forEach((s,i)=>{
    const tab=el('button',`candidate-tab ${i===index?'active':''}`);tab.type='button';tab.disabled=!s.candidates.length;
    tab.append(el('span','',i+1),el('strong','',s.intent.place_name||s.intent.intent));
    tab.setAttribute('aria-current',i===index?'step':'false');
    tab.addEventListener('click',()=>showCandidates(i,{flow:state.choosingTrip}));$('candidate-tabs').append(tab);
  });
  $('candidate-progress').textContent=flow?`第 ${index+1} / ${state.result.stops.length} 站 · 选定后继续下一站`:'点击地点即可确认，也可切换上方站点。';
  $('candidate-dismiss').textContent=flow?(state.result.stops.every(s=>s.selected_index!=null)?'先用当前地点，返回行程':'稍后选择，返回行程'):'返回行程';
  $('candidates-list').replaceChildren();
  stop.candidates.forEach((p,i)=>{
    const button=el('button',`candidate ${i===stop.selected_index?'current':''} ${i===highlight?'highlighted':''}`);button.type='button';
    const text=el('span','candidate-text');text.append(el('strong','',p.name||'未命名地点'),el('small','',`${p.category||'地点'} · ${distance(p.dist_m)} · ${p.longitude.toFixed(5)}, ${p.latitude.toFixed(5)}`));
    const callout=el('span','candidate-action',i===stop.selected_index?'确认此地点':'选择此地点');callout.append(icon(i===stop.selected_index?'check':'chevron'));
    button.append(el('span','candidate-number',String.fromCharCode(65+i)),text,callout);
    button.addEventListener('click',()=>choose(index,i));$('candidates-list').append(button);
  });
  renderMap();dialog('candidates-dialog');
  if(highlight!=null)$('candidates-list').children[highlight]?.focus();
}
async function choose(stop,index){
  if(state.busy)return;const flow=state.choosingTrip;closeDialogs();
  const result=await action('select',{result_id:state.result.id,stop,index},()=>{status('地点已确认，后续站点已重新查询。');fitMap();});
  if(!result){showCandidates(stop,{flow});return;}
  const next=state.result.stops.findIndex((s,i)=>i>stop&&s.candidates.length&&(flow||s.selected_index==null));
  if(next>=0)showCandidates(next,{flow:true});
  else if(flow)status(state.result.stops.every(s=>s.selected_index!=null)?'地点选择完成，可以规划路线；需要时可点击“选择行程地点”调整。':'部分站点暂无可选地点，请调整搜索范围或更换前一站。');
}
function settingsForm(){
  const c=state.config;if(!c)return;
  $('llm-url').value=c.llm.base_url;$('llm-model').value=c.llm.model;$('llm-format').value=c.llm.response_format;
  for(const id of ['llm-key','amap-key','amap-code'])$(id).value='';
  $('llm-key').placeholder=c.has_llm_key?'已配置 · 留空保留':'输入你的 API Key';
  $('amap-key').placeholder=c.has_amap_key?'已配置 · 留空保留':'请使用 Web 服务 Key';
  $('amap-code').placeholder=c.has_security_code?'已配置 · 留空保留':'仅在控制台启用时填写';
  $('llm-key-status').textContent=c.has_llm_key?'当前已配置，密钥不会返回到浏览器。':'密钥只在本机使用';
  $('amap-key-status').textContent=c.has_amap_key?'当前已配置，可查询地点和规划路线。':'用于地点查询和真实道路规划';
  $('remember').checked=c.remember;$('settings-error').hidden=true;dialog('settings-dialog');
}

document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{const r=d.getBoundingClientRect();if(e.target===d&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom))d.close();}));
$('settings-button').addEventListener('click',settingsForm);
$('settings-dialog').addEventListener('close',()=>{for(const id of ['llm-key','amap-key','amap-code'])$(id).value='';});
$('help-button').addEventListener('click',()=>dialog('help-dialog'));
$('review-trip').addEventListener('click',()=>{
  const pending=state.result?.stops.findIndex(s=>s.candidates.length&&s.selected_index==null);
  const first=state.result?.stops.findIndex(s=>s.candidates.length);
  if(first>=0)showCandidates(pending>=0?pending:first,{flow:true});
});
$('candidate-dismiss').addEventListener('click',()=>{$('candidates-dialog').close();state.choosingTrip=false;});
$('query').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();$('query-form').requestSubmit();}});
document.querySelectorAll('[data-query]').forEach(b=>b.addEventListener('click',()=>{$('query').value=b.dataset.query;$('query').focus();}));
$('query-form').addEventListener('submit',async e=>{
  e.preventDefault();if(state.busy)return;
  if(!state.config.has_llm_key||(state.config.online&&!state.config.has_amap_key)){settingsForm();toast('先配置语言理解和地图密钥，就可以开始查询。');return;}
  state.activeStop=0;$('parking-enabled').checked=false;
  const result=await action('query',{query:$('query').value.trim(),center:state.center,radius:Number($('radius').value),limit:10,dry_run:$('preview').checked},()=>{
    state.mode=state.result.travel_mode;renderModes();renderRoute();
    fitMap();status(state.result.dry_run?'已理解需求；预览模式未执行地点搜索。':state.result.stops.every(s=>s.selected_index!=null)?'地点已就绪，选择出行方式后规划路线。':'请确认具体地点，继续安排你的行程。');
    $('trip-heading').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  });
  if(result&&!state.result.dry_run){
    const first=state.result.stops.findIndex(s=>s.candidates.length);
    if(first>=0)showCandidates(first,{flow:true});
  }
});
$('origin-button').addEventListener('click',()=>{$('longitude').value=state.center[0].toFixed(6);$('latitude').value=state.center[1].toFixed(6);dialog('origin-dialog');});
$('origin-form').addEventListener('submit',e=>{e.preventDefault();closeDialogs();setOrigin([Number($('longitude').value),Number($('latitude').value)],'自定义起点');});
$('locate').addEventListener('click',()=>{
  const origin=[...state.center];
  action('locate',{},data=>{
    if(origin.some((p,i)=>p!==state.center[i]))return;
    const p=data.location;state.center=[p.longitude,p.latitude];state.originName='设备当前位置';updateOrigin();
    // Location changes invalidate the previous itinerary in both layers.
    state.result=state.route=state.parking=state.route_parking=null;render();map.goTo(state.center,15);
    status(`已获取设备位置${p.accuracy_m!=null?'，精度约 '+Math.round(p.accuracy_m)+' 米':''}。请重新查询行程。`);
  });
});
$('zoom-in').addEventListener('click',()=>map.changeZoom(1));$('zoom-out').addEventListener('click',()=>map.changeZoom(-1));
$('fit-map').addEventListener('click',fitMap);$('center-map').addEventListener('click',()=>map.goTo(state.center,15));
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{
  if(state.busy)return;state.mode=b.dataset.mode;
  if($('parking-enabled').checked){$('parking-enabled').checked=false;renderParking();}
  renderModes();renderRoute();renderMap();updateActions();
}));
$('parking-enabled').addEventListener('change',()=>{if($('parking-enabled').checked)state.mode='driving';renderModes();renderParking();renderRoute();renderMap();updateActions();});
$('parking-radius').addEventListener('change',()=>{state.parking=null;state.route=state.route_parking=null;$('parking-select').hidden=true;$('parking-select').replaceChildren();$('parking-note').textContent='搜索范围已变化，请重新查找停车场。';renderRoute();renderMap();updateActions();});
$('find-parking').addEventListener('click',()=>action('parking',{result_id:state.result.id,radius:Number($('parking-radius').value)},()=>{status('停车场查询完成，请选择具体停车场。');}));
$('parking-select').addEventListener('change',()=>{state.route=state.route_parking=null;renderRoute();renderMap();updateActions();});
$('plan-route').addEventListener('click',()=>{
  const payload={result_id:state.result.id,mode:state.mode};
  if($('parking-enabled').checked)payload.parking_index=Number($('parking-select').value);
  action('route',payload,()=>{renderRoute();renderMap();fitMap();status(`路线已规划 · ${distance(state.route.distance_m)} · 约 ${duration(state.route.duration_s)}`);});
});
$('navigation-button').addEventListener('click',()=>{
  const r=visibleRoute();if(!r)return;$('navigation-list').replaceChildren();
  r.legs.forEach((leg,i)=>{
    const section=el('section','navigation-leg');const stop=state.result.stops[i];
    const name=state.route_parking&&i===r.legs.length-1?(state.route_parking.name||'停车场'):(stop.candidates[stop.selected_index]?.name||stop.intent.intent);
    section.append(el('h3','',`${i+1}. ${i?'前一站':'起点'} → ${name}`),el('small','',`${modeNames[r.travel_mode]} · ${distance(leg.distance_m)} · 约 ${duration(leg.duration_s)}`));
    const list=el('ol');leg.instructions.forEach(text=>list.append(el('li','',text)));if(!leg.instructions.length)list.append(el('li','','请沿地图道路路线行进。'));section.append(list);$('navigation-list').append(section);
  });dialog('navigation-dialog');
});
$('settings-form').addEventListener('submit',e=>{
  e.preventDefault();$('settings-error').hidden=true;
  const llm={...state.config.llm,base_url:$('llm-url').value.trim(),model:$('llm-model').value.trim(),response_format:$('llm-format').value};
  const payload={llm,remember:$('remember').checked};
  for(const [id,key] of [['llm-key','api_key'],['amap-key','amap_key'],['amap-code','security_code']])if($(id).value.trim())payload[key]=$(id).value.trim();
  action('settings',payload,()=>{closeDialogs();for(const id of ['llm-key','amap-key','amap-code'])$(id).value='';status('接口设置已应用，可以开始规划行程。');toast('设置已保存。');});
});
$('clear-settings').addEventListener('click',()=>action('clear-settings',{},()=>{settingsFormRefresh();status('已清除本地保存的接口和密钥。');}));
const shutdown=el('button','shutdown-button','结束本地服务');shutdown.type='button';
$('settings-form').append(shutdown);
shutdown.addEventListener('click',async()=>{
  if(state.busy)return;
  try{await api('shutdown',{});closeDialogs();busy(true);$('service-badge').textContent='本地服务已结束';status('服务已结束，可以关闭此页面。');toast('本地服务已结束。重新运行启动程序即可继续使用。');}
  catch(error){toast(error.message);}
});
function settingsFormRefresh(){for(const id of ['llm-key','amap-key','amap-code']){$(id).value='';$(id).placeholder='请输入新的密钥';}$('remember').checked=false;$('llm-key-status').textContent='尚未配置';$('amap-key-status').textContent='尚未配置';}
$('export').addEventListener('click',async()=>{
  try{
    const response=await fetch(`/api/export?result_id=${encodeURIComponent(state.result.id)}`,{headers:{'X-Geoask-Token':token}});
    if(!response.ok)throw new Error((await response.json()).error);
    const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='geoask-行程.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status('行程已导出为 CSV。');
  }catch(error){toast(error.message);}
});

async function start(){
  busy(true);
  try{
    const config=await api('config');applyConfig(config);state.center=[...config.center];updateOrigin();
    if(![...$('radius').options].some(o=>Number(o.value)===config.radius))$('radius').append(new Option(distance(config.radius),String(config.radius)));
    $('radius').value=String(config.radius);map.configure(config.map,state.center);
    const snapshot=await api('state');
    if(snapshot.busy){await poll(snapshot.job);}else if(snapshot.state)acceptState(snapshot.state);
    if(state.result){state.center=[...state.result.center];updateOrigin();fitMap();}
    status(config.has_llm_key&&config.has_amap_key?'准备好就出发。':'先在接口设置中连接语言理解与地图服务。');
  }catch(error){status(error.message,true);toast(error.message);$('service-badge').textContent='本地服务未连接';}
  finally{busy(false);}
}
start();
