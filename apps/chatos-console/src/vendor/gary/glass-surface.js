/*
 * Adapted from React Bits GlassSurface, David Haz, 2026.
 * Source commit c39f1f69222288986e4336c74f7e1a9b6a7060fb.
 * MIT + Commons Clause; see glass-surface-LICENSE.md.
 * Same displacement-map / RGB-filter approach, native DOM lifecycle.
 */
(() => {
  'use strict';
  const mounted=new Map();
  let nextId=0;
  let strength=180;
  const mode=new URLSearchParams(location.search).get('glass');
  const compatible=()=>{
    const ua=navigator.userAgent;
    return !(/Safari/.test(ua)&&!/Chrome/.test(ua))&&!/Firefox/.test(ua)&&CSS.supports('backdrop-filter','url("#gary-optics-probe")');
  };
  function mount(card){
    if(mounted.has(card)||mode==='frosted')return;
    if(mode==='fallback'||!compatible()){card.dataset.garyRefractionState='fallback';mounted.set(card,{destroy(){delete card.dataset.garyRefractionState;mounted.delete(card);}});return;}
    const id='gary-optics-'+(++nextId);
    const template=document.createElement('template');
    template.innerHTML=`<svg class="gary-refraction-filter" aria-hidden="true" focusable="false"><defs><filter id="${id}" color-interpolation-filters="sRGB" x="0%" y="0%" width="100%" height="100%">
      <feImage x="0" y="0" width="100%" height="100%" preserveAspectRatio="none" result="map"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${-strength}" xChannelSelector="R" yChannelSelector="G" result="dispRed"/>
      <feColorMatrix in="dispRed" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="red"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${-Math.max(0,strength-1)}" xChannelSelector="R" yChannelSelector="G" result="dispGreen"/>
      <feColorMatrix in="dispGreen" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="green"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${-Math.max(0,strength-2)}" xChannelSelector="R" yChannelSelector="G" result="dispBlue"/>
      <feColorMatrix in="dispBlue" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="blue"/>
      <feBlend in="red" in2="green" mode="screen" result="rg"/><feBlend in="rg" in2="blue" mode="screen" result="output"/>
      <feGaussianBlur in="output" stdDeviation="0.35"/>
    </filter></defs></svg>`;
    const svg=template.content.firstElementChild,map=svg.querySelector('feImage');
    card.prepend(svg);
    let frame=0,lastSize='';
    function update(){
      frame=0;const rect=card.getBoundingClientRect(),w=Math.max(1,Math.round(rect.width)),h=Math.max(1,Math.round(rect.height));
      const radius=Math.max(0,parseFloat(getComputedStyle(card).borderRadius)||30),key=[w,h,radius].join('/');
      if(key===lastSize)return;lastSize=key;
      const edge=Math.min(w,h)*.035;
      // XML namespace is taken from the local SVG DOM; the map is a local data URI.
      const source=`<svg viewBox="0 0 ${w} ${h}" xmlns="${svg.namespaceURI}"><defs>
        <linearGradient id="red" x1="100%" y1="0%" x2="0%" y2="0%"><stop offset="0%" stop-color="#0000"/><stop offset="100%" stop-color="red"/></linearGradient>
        <linearGradient id="blue" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#0000"/><stop offset="100%" stop-color="blue"/></linearGradient>
        </defs><rect width="${w}" height="${h}" fill="black"/>
        <rect width="${w}" height="${h}" rx="${radius}" fill="url(#red)"/>
        <rect width="${w}" height="${h}" rx="${radius}" fill="url(#blue)" style="mix-blend-mode:difference"/>
        <rect x="${edge}" y="${edge}" width="${Math.max(1,w-edge*2)}" height="${Math.max(1,h-edge*2)}" rx="${radius}" fill="hsl(0 0% 50% / .93)" style="filter:blur(11px)"/></svg>`;
      map.setAttribute('href','data:image/svg+xml,'+encodeURIComponent(source));
      card.style.setProperty('--gary-refraction-filter',`url("#${id}")`);
      card.dataset.garyRefractionState='svg';
    }
    const resize=new ResizeObserver(()=>{if(!frame)frame=requestAnimationFrame(update);});resize.observe(card);update();
    const handle={destroy(){resize.disconnect();cancelAnimationFrame(frame);svg.remove();card.style.removeProperty('--gary-refraction-filter');delete card.dataset.garyRefractionState;mounted.delete(card);}};
    mounted.set(card,handle);return handle;
  }
  const scan=()=>{document.querySelectorAll('[data-gary-refraction]').forEach(mount);for(const [card,handle] of mounted)if(!card.isConnected)handle.destroy();};
  scan();new MutationObserver(scan).observe(document.body,{childList:true,subtree:true});
  window.GaryGlassSurface={mount,get strength(){return strength;},setStrength(value){
    if(!Number.isFinite(Number(value)))return;strength=Math.max(0,Math.min(300,Number(value)));
    for(const card of mounted.keys())card.querySelectorAll('feDisplacementMap').forEach((node,i)=>node.setAttribute('scale',-Math.max(0,strength-i)));
  },destroy(card){mounted.get(card)?.destroy();}};
})();
