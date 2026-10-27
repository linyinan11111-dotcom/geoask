/** A shared budget includes ordinary glass, charts, records and same-origin frames. */
export class ContextBudget {
  constructor(limit=6){this.limit=limit;this.clients=new Set();}
  add(client){
    const entry={...client,active:false};this.clients.add(entry);
    entry.destroy=()=>{this.clients.delete(entry);if(entry.active){entry.active=false;entry.suspend();}};
    return entry;
  }
  update(){
    const ranked=[...this.clients].map(entry=>({entry,rank:entry.priority()}))
      .filter(({rank})=>Number.isFinite(rank)).sort((a,b)=>b.rank-a.rank);
    const wanted=new Set(ranked.slice(0,this.limit).map(({entry})=>entry));
    // Release every departing lease before acquiring any new one.
    for(const entry of this.clients)if(entry.active&&!wanted.has(entry)){entry.active=false;entry.suspend();}
    for(const entry of wanted)if(!entry.active){entry.active=true;entry.activate();}
  }
}

export function viewportPriority(host){
  const doc=host.ownerDocument,win=doc.defaultView;
  if(!host.isConnected||doc.hidden||!host.getClientRects().length)return -Infinity;
  const style=win.getComputedStyle(host);if(style.visibility==='hidden')return -Infinity;
  let rect=host.getBoundingClientRect(),left=Math.max(0,rect.left),top=Math.max(0,rect.top),right=Math.min(win.innerWidth,rect.right),bottom=Math.min(win.innerHeight,rect.bottom);
  if(right<=left||bottom<=top)return -Infinity;
  // A srcdoc lab must not retain GPU slots when its parent view is hidden.
  for(let current=win;current.frameElement;current=current.parent){
    const frame=current.frameElement,r=frame.getBoundingClientRect();
    if(!frame.getClientRects().length||current.parent.document.hidden||r.bottom<=0||r.top>=current.parent.innerHeight||r.right<=0||r.left>=current.parent.innerWidth)return -Infinity;
  }
  return (right-left)*(bottom-top)/(Math.max(1,rect.width*rect.height))-
    Math.abs((top+bottom)/2-win.innerHeight/2)/Math.max(1,win.innerHeight)*.01;
}

export function observeGPU(host,activate,suspend){
  const win=host.ownerDocument.defaultView;
  let owner=win;
  try{while(owner.parent!==owner&&owner.parent.document)owner=owner.parent;}catch{/* Cross-origin callers keep their own budget. */}
  const key=Symbol.for('lens.viewport-gpu-budget.v1');
  if(!owner[key]){
    const budget=new ContextBudget(6),documents=new Map();let frame=0;
    const schedule=()=>{if(!frame)frame=owner.requestAnimationFrame(()=>{frame=0;budget.update();});};
    owner[key]={budget,documents,schedule};
  }
  const shared=owner[key],doc=host.ownerDocument;
  if(!shared.documents.has(doc)){
    const abort=new win.AbortController(),signal=abort.signal;
    doc.addEventListener('scroll',shared.schedule,{capture:true,passive:true,signal});
    doc.addEventListener('visibilitychange',shared.schedule,{signal});
    win.addEventListener('resize',shared.schedule,{passive:true,signal});
    shared.documents.set(doc,{count:0,abort});
  }
  const connection=shared.documents.get(doc);connection.count++;
  const lease=shared.budget.add({priority:()=>viewportPriority(host),activate,suspend});
  const resize=new win.ResizeObserver(shared.schedule);resize.observe(host);
  const intersection=new win.IntersectionObserver(shared.schedule);intersection.observe(host);
  let destroyed=false;
  const destroy=()=>{
    if(destroyed)return;destroyed=true;resize.disconnect();intersection.disconnect();lease.destroy();
    win.removeEventListener('pagehide',pagehide);
    if(!--connection.count){connection.abort.abort();shared.documents.delete(doc);}
    shared.schedule();
  };
  const pagehide=event=>{if(!event.persisted)destroy();};win.addEventListener('pagehide',pagehide);
  shared.schedule();
  return {get active(){return lease.active;},destroy,update:shared.schedule};
}
