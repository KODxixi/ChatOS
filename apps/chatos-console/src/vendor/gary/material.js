(() => {
  'use strict';
  const params=new URLSearchParams(location.search);
  if(document.body.classList.contains('gary-ui')){
    if(['dark','light'].includes(params.get('theme')))document.documentElement.dataset.garyTheme=params.get('theme');
    if(['ultrathin','regular','thick','solid-plate'].includes(params.get('material')))document.documentElement.dataset.garyMaterial=params.get('material');
  }
  // Cards use the shared optical material; the page owns the only dot field.
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const fine=matchMedia('(hover: hover) and (pointer: fine)');
  const print=matchMedia('print');
  const lightScopes=new Map();
  let lightFrame=0,lastPointer=null,printing=false;
  const mounted=new WeakSet();
  const pointerEnabled=()=>document.documentElement.dataset.garyPointerEffects==='on'&&!reduced.matches&&fine.matches&&!document.hidden&&!printing&&!print.matches;
  const clearPointer=card=>{card.style.removeProperty('--gary-card-x');card.style.removeProperty('--gary-card-y');};
  const resetPointers=()=>{document.querySelectorAll('[data-gary-card="action"]').forEach(clearPointer);clearLight();};
  const systemCards='.starter-shell-context.gary-glass,.material-sample.gary-glass,.portal-surface:not(.gallery-toolbar),.review-form,.review-summary,.review-coverage,.review-surface,.pd-live-preview,.mode-card.gary-glass,.pattern-card.gary-glass,.toc-nav.gary-glass,.starter-controls,.starter-preview,.starter-conclusion.gary-glass,.starter-toc-nav.gary-glass,.gary-glass-card:not([data-card-style="custom"]):not(.gary-solid-plate),.gary-metric-card:not(.gary-solid-plate),.gary-media-card:not(.gary-solid-plate),.gary-profile-card:not(.gary-solid-plate)';
  const mount=card=>{
    const surface=card.dataset.garySurface;
    if(['solid','frosted','optical'].includes(surface)){
      card.dataset.garyMaterial=surface==='solid'?'solid-plate':surface==='frosted'?'regular':'glass';
      if(surface==='optical')card.setAttribute('data-gary-refraction','');
      else {card.removeAttribute('data-gary-refraction');window.GaryGlassSurface?.destroy(card);clearSurfaceLight(card);}
    }else if(!mounted.has(card)){
      card.dataset.garyMaterial='glass';
      if(card.matches(systemCards))card.setAttribute('data-gary-refraction','');
    }
    if(mounted.has(card))return; mounted.add(card);
    if(card.matches('a[href],button')&&!card.hasAttribute('aria-disabled')){
      card.dataset.garyCard='action';
      card.addEventListener('pointermove',event=>{
        if(!pointerEnabled()||card.hasAttribute('data-gary-surface')){clearPointer(card);return;}
        const r=card.getBoundingClientRect();
        card.style.setProperty('--gary-card-x',`${event.clientX-r.left}px`);
        card.style.setProperty('--gary-card-y',`${event.clientY-r.top}px`);
      },{passive:true});
      card.addEventListener('pointerleave',()=>clearPointer(card),{passive:true});
    }

  };
  function clearSurfaceLight(card){
    card.style.removeProperty('--gary-light-x');card.style.removeProperty('--gary-light-y');
    card.style.removeProperty('--gary-light-active');
  }
  function clearScope(scope){
    scope.style.removeProperty('--gary-scene-light-x');scope.style.removeProperty('--gary-scene-light-y');
    scope.style.removeProperty('--gary-scene-light-active');
    scope.querySelectorAll('[data-gary-surface]').forEach(card=>{if(!scope.hasAttribute('data-gary-light-scope')||card.closest('[data-gary-light-scope]')===scope)clearSurfaceLight(card);});
  }
  function clearLight(){
    cancelAnimationFrame(lightFrame);lightFrame=0;lastPointer=null;
    lightScopes.forEach((_,scope)=>clearScope(scope));
  }
  function mountScope(scope){
    if(lightScopes.has(scope))return;
    const light=document.createElement('div');light.className='gary-surface-light';light.setAttribute('aria-hidden','true');
    lightScopes.set(scope,light);scope.prepend(light);
  }
  function paintLight(){
    lightFrame=0;
    if(!lastPointer||!pointerEnabled()){clearLight();return;}
    const {x,y}=lastPointer;
    lightScopes.forEach((_,scope)=>{
      const box=scope.getBoundingClientRect();
      if(x<box.left||x>box.right||y<box.top||y>box.bottom){clearScope(scope);return;}
      scope.style.setProperty('--gary-scene-light-x',`${x-box.left}px`);scope.style.setProperty('--gary-scene-light-y',`${y-box.top}px`);scope.style.setProperty('--gary-scene-light-active','1');
      scope.querySelectorAll('[data-gary-surface="optical"]').forEach(card=>{
        if(card.closest('[data-gary-light-scope]')!==scope)return;
        const rect=card.getBoundingClientRect();
        card.style.setProperty('--gary-light-x',`${x-rect.left}px`);card.style.setProperty('--gary-light-y',`${y-rect.top}px`);
        const distance=Math.hypot(Math.max(rect.left-x,0,x-rect.right),Math.max(rect.top-y,0,y-rect.bottom));
        card.style.setProperty('--gary-light-active',String(Math.max(0,1-distance/240)));
      });
    });
  }
  const scan=(scope=document)=>{
    const selectors='[data-gary-surface],[data-gary-material="glass"],.demo-preview,.glass,.region-tile,'+systemCards;
    if(scope.matches?.(selectors))mount(scope);scope.querySelectorAll?.(selectors).forEach(mount);
    if(scope.matches?.('[data-gary-light-scope]'))mountScope(scope);scope.querySelectorAll?.('[data-gary-light-scope]').forEach(mountScope);
  };
  scan();new MutationObserver(records=>{
    records.forEach(record=>{
      if(record.type==='attributes')scan(record.target);
      else record.addedNodes.forEach(node=>{if(node.nodeType===1)scan(node);});
    });
    lightScopes.forEach((light,scope)=>{if(!scope.isConnected||!scope.hasAttribute('data-gary-light-scope')){clearScope(scope);light.remove();lightScopes.delete(scope);}});
  }).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['data-gary-surface','data-gary-light-scope']});
  addEventListener('pointermove',event=>{
    if(event.pointerType!=='mouse'||!lightScopes.size||!pointerEnabled())return;
    lastPointer={x:event.clientX,y:event.clientY};if(!lightFrame)lightFrame=requestAnimationFrame(paintLight);
  },{passive:true});
  addEventListener('pointerout',event=>{if(!event.relatedTarget)resetPointers();});
  addEventListener('scroll',resetPointers,{passive:true});addEventListener('resize',resetPointers);addEventListener('pagehide',resetPointers);
  document.addEventListener('visibilitychange',resetPointers);
  addEventListener('beforeprint',()=>{printing=true;resetPointers();});addEventListener('afterprint',()=>{printing=false;resetPointers();});
  addEventListener('gary:pointer-effects-change',resetPointers);
  reduced.addEventListener('change',resetPointers);fine.addEventListener('change',resetPointers);print.addEventListener('change',resetPointers);
  new MutationObserver(resetPointers).observe(document.documentElement,{attributes:true,attributeFilter:['data-gary-pointer-effects']});
})();
