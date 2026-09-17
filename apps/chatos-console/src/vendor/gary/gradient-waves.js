/*
 * GradientWaves shader: Copyright (c) 2026 David Haz / React Bits
 * Pinned source: 88501f94882ef73be1ec79639ae7d3ad7ff7f23a
 * MIT + Commons Clause; full terms in gradient-waves-LICENSE.md.
 * Gary adaptation: native WebGL2 host and bounded accessible lifecycle.
 */
(() => {
  'use strict';
  const vertex = "#version 300 es\nin vec2 position;\nvoid main() {\n  gl_Position = vec4(position, 0.0, 1.0);\n}\n";
  const fragment = "#version 300 es\nprecision highp float;\nuniform vec2 iResolution;\nuniform float iTime;\nuniform float uSpeed;\nuniform float uAmplitude;\nuniform float uWaveScale;\nuniform float uWaveRatio;\nuniform float uSwell;\nuniform float uTurbulence;\nuniform float uTilt;\nuniform float uZoom;\nuniform float uHeight;\nuniform float uFogDepth;\nuniform float uSteps;\nuniform float uBrightness;\nuniform float uOpacity;\nuniform float uGrain;\nuniform float uGrainIntensity;\nuniform vec2 uMouse;\nuniform float uParallax;\nuniform bool uEnableMouse;\nuniform vec3 uHorizonColor;\nuniform vec3 uWaveColor;\nuniform vec3 uCrestColor;\nout vec4 fragColor;\n\nconst float MAX_DIST = 20000.0;\n\nfloat hash21(vec2 p) {\n  vec3 p3 = fract(vec3(p.xyx) * 0.1031);\n  p3 += dot(p3, p3.yzx + 33.33);\n  return fract((p3.x + p3.y) * p3.z);\n}\n\nfloat plasma(vec3 r, vec2 freq, vec4 tc) {\n  float mx = r.x + tc.x;\n  mx += uSwell * sin((r.y + mx) / 20.0 + tc.y);\n  float my = r.y - tc.z;\n  my += uTurbulence * cos(r.x / 23.0 + tc.w);\n  return r.z - (sin(mx * freq.x) * uAmplitude + sin(my * freq.y) * uAmplitude + uHeight);\n}\n\nfloat raymarch(vec3 pos, vec3 dir, vec2 freq, vec4 tc) {\n  float dist = 0.0;\n  for (int i = 0; i < 128; i++) {\n    if (float(i) >= uSteps) break;\n    float dscene = plasma(pos + dist * dir, freq, tc);\n    if (abs(dscene) < 0.1) break;\n    dist += 0.9 * dscene;\n    if (!(abs(dist) < MAX_DIST)) return MAX_DIST;\n  }\n  return dist;\n}\n\nvoid main() {\n  float T = iTime * uSpeed;\n  vec2 freq = vec2(uWaveScale / 7.0, (uWaveScale * uWaveRatio) / 3.0);\n  vec4 tc = vec4(T / 0.130, T / 0.810, T / 0.200, T / 0.710);\n  float c, s;\n  float vfov = (3.14159 / 2.3) / max(uZoom, 0.05);\n  vec3 cam = vec3(0.0, 0.0, 30.0);\n  vec2 uv = (gl_FragCoord.xy / iResolution.xy) - 0.5;\n  uv.x *= iResolution.x / iResolution.y;\n  uv.y *= -1.0;\n\n  vec3 dir = vec3(0.0, 0.0, -1.0);\n  float ulen = length(uv);\n  float xrot = vfov * ulen;\n  c = cos(xrot); s = sin(xrot);\n  dir = mat3(1.0, 0.0, 0.0, 0.0, c, -s, 0.0, s, c) * dir;\n  vec2 nuv = ulen > 1e-5 ? uv / ulen : vec2(1.0, 0.0);\n  c = nuv.x; s = nuv.y;\n  dir = mat3(c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0) * dir;\n  c = cos(uTilt); s = sin(uTilt);\n  dir = mat3(c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c) * dir;\n\n  if (uEnableMouse) {\n    float yaw = (uMouse.x - 0.5) * uParallax * 0.4;\n    float pitch = (uMouse.y - 0.5) * uParallax * 0.4;\n    c = cos(yaw); s = sin(yaw);\n    dir = mat3(c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c) * dir;\n    c = cos(pitch); s = sin(pitch);\n    dir = mat3(1.0, 0.0, 0.0, 0.0, c, -s, 0.0, s, c) * dir;\n  }\n\n  float dist = raymarch(cam, dir, freq, tc);\n  vec3 pos = cam + dist * dir;\n\n  float t = clamp(uFogDepth / max(dist, 0.001), 0.0, 1.0);\n  vec3 body = mix(uWaveColor, uCrestColor, clamp(pos.z * 0.08 + 0.5, 0.0, 1.0));\n  vec3 col = mix(uHorizonColor, body, t);\n  col *= uBrightness;\n  col = clamp(col, 0.0, 1.0);\n\n  float alpha = clamp(t, 0.0, 1.0) * uOpacity;\n  if (uGrain > 0.5) {\n    float g = hash21(gl_FragCoord.xy + mod(iTime, 64.0) * 11.0);\n    alpha += (g - 0.5) * uGrainIntensity;\n  }\n  alpha = clamp(alpha, 0.0, 1.0);\n  fragColor = vec4(col * alpha, alpha);\n}\n";
  const defaults = Object.freeze({horizonColor:'#5227ff',waveColor:'#ff9ffc',crestColor:'#ffffff',speed:.4,amplitude:2.5,waveScale:.6,waveRatio:.9,swell:35,turbulence:20,tilt:1.11,zoom:1,height:5.5,fogDepth:15,detail:'medium',brightness:1,opacity:1,mouseInteraction:true,parallaxStrength:.5,grain:true,grainIntensity:.05});
  const ranges = Object.freeze({speed:[0,1.5,.05],amplitude:[0,10,.1],waveScale:[.1,2,.05],waveRatio:[.1,2,.05],swell:[0,60,1],turbulence:[0,50,1],tilt:[0,1.57,.01],zoom:[.3,2,.05],height:[0,15,.5],fogDepth:[1,40,1],brightness:[.1,1.5,.05],opacity:[0,1,.01],parallaxStrength:[0,1,.05],grainIntensity:[0,.2,.01]});
  function normalize(input={},base=defaults) {
    const next={...base};
    for(const [key,[min,max]] of Object.entries(ranges)) if(Object.hasOwn(input,key)){
      const value=Number(input[key]);if(Number.isFinite(value))next[key]=Math.min(max,Math.max(min,value));
    }
    for(const key of ['horizonColor','waveColor','crestColor']) if(/^#[a-f0-9]{6}$/i.test(input[key]||''))next[key]=input[key];
    for(const key of ['grain','mouseInteraction'])if(typeof input[key]==='boolean')next[key]=input[key];
    if(['low','medium','high'].includes(input.detail))next.detail=input.detail;
    return next;
  }
  function mount(host,initial={},playback={}) {
    const duration=playback.durationSeconds===null?Infinity:Number.isFinite(playback.durationSeconds)&&playback.durationSeconds>0?playback.durationSeconds:20;
    let options=normalize(initial),time=0,remaining=duration,frames=0,raf=0,last=0,visible=true,paused=false,destroyed=false,failed=false,printing=false;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)'),canvas=document.createElement('canvas');
    canvas.className='gary-gradient-waves-canvas';canvas.setAttribute('aria-hidden','true');
    const gl=canvas.getContext('webgl2',{alpha:true,premultipliedAlpha:true,antialias:false,powerPreference:'low-power'});
    let program,buffer;const shaders=[],uniforms={},mouse=[.5,.5],target=[.5,.5];
    const status=()=>failed?'fallback':reduced.matches?'reduced-motion':printing?'print':paused?'paused':!visible||document.hidden?'suspended':remaining<=0?'complete':options.speed===0?'static':'playing';
    const notify=()=>{host.dataset.wavesStatus=status();host.dispatchEvent(new CustomEvent('gary-waves-status',{detail:{status:status()}}));};
    const stop=()=>{cancelAnimationFrame(raf);raf=0;last=0;};
    function fail(){failed=true;stop();canvas.hidden=true;notify();}
    const canRun=()=>!destroyed&&!failed&&!reduced.matches&&!paused&&!printing&&visible&&!document.hidden&&remaining>0&&options.speed>0;
    function draw(){
      if(!gl||failed||destroyed)return;
      gl.useProgram(program);
      gl.uniform2f(uniforms.iResolution,canvas.width,canvas.height);gl.uniform1f(uniforms.iTime,time);
      for(const key of Object.keys(ranges)){
        const name=key==='parallaxStrength'?'uParallax':'u'+key[0].toUpperCase()+key.slice(1);
        gl.uniform1f(uniforms[name],options[key]);
      }
      gl.uniform1f(uniforms.uGrain,Number(options.grain));gl.uniform1f(uniforms.uSteps,{low:40,medium:70,high:110}[options.detail]);
      gl.uniform1i(uniforms.uEnableMouse,Number(options.mouseInteraction&&!reduced.matches));
      gl.uniform2fv(uniforms.uMouse,mouse);
      for(const key of ['horizonColor','waveColor','crestColor'])gl.uniform3fv(uniforms['u'+key[0].toUpperCase()+key.slice(1)],options[key].slice(1).match(/../g).map(x=>parseInt(x,16)/255));
      gl.drawArrays(gl.TRIANGLES,0,3);frames++;
    }
    function loop(stamp){
      raf=0;if(!canRun()){last=0;notify();return;}
      if(!last)last=stamp;
      const delta=(stamp-last)/1000;
      if(delta>=1/30){
        const step=Math.min(delta,.1);time+=step;remaining=Math.max(0,remaining-step);last=stamp;
        for(let i=0;i<2;i++)mouse[i]+=.1*(target[i]-mouse[i]);draw();
      }
      if(canRun())raf=requestAnimationFrame(loop);else notify();
    }
    const start=()=>{if(canRun()&&!raf)raf=requestAnimationFrame(loop);notify();};
    function resize(){
      if(failed||destroyed)return;const rect=host.getBoundingClientRect();
      const scale=Math.min(devicePixelRatio||1,1.5,Math.sqrt(1200000/Math.max(1,rect.width*rect.height)));
      canvas.width=Math.max(1,Math.round(rect.width*scale));canvas.height=Math.max(1,Math.round(rect.height*scale));
      gl.viewport(0,0,canvas.width,canvas.height);draw();
    }
    const onPointer=e=>{
      if(!canRun()||!options.mouseInteraction||e.pointerType==='touch')return;
      const r=host.getBoundingClientRect();target[0]=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));target[1]=Math.max(0,Math.min(1,1-(e.clientY-r.top)/r.height));
    };
    const onLeave=()=>target.fill(.5);
    const onVisibility=()=>{stop();start();};
    const onReduced=()=>{stop();mouse.fill(.5);target.fill(.5);draw();start();};
    const onPrint=()=>{printing=true;stop();notify();};
    const afterPrint=()=>{printing=false;start();};
    const onLost=e=>{e.preventDefault();fail();};
    let ro,io;
    try {
      if(!gl)throw Error('WebGL2 unavailable');
      program=gl.createProgram();
      for(const [type,source] of [[gl.VERTEX_SHADER,vertex],[gl.FRAGMENT_SHADER,fragment]]){
        const shader=gl.createShader(type);shaders.push(shader);gl.shaderSource(shader,source);gl.compileShader(shader);
        if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));gl.attachShader(program,shader);
      }
      gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
      gl.useProgram(program);buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
      gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
      const position=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
      for(let i=0;i<gl.getProgramParameter(program,gl.ACTIVE_UNIFORMS);i++){const name=gl.getActiveUniform(program,i).name;uniforms[name]=gl.getUniformLocation(program,name);}
      host.append(canvas);ro=new ResizeObserver(resize);ro.observe(host);
      io=new IntersectionObserver(([entry])=>{visible=entry.isIntersecting;stop();start();});io.observe(host);
      addEventListener('pointermove',onPointer,{passive:true});document.documentElement.addEventListener('pointerleave',onLeave);
      document.addEventListener('visibilitychange',onVisibility);reduced.addEventListener('change',onReduced);
      addEventListener('beforeprint',onPrint);addEventListener('afterprint',afterPrint);canvas.addEventListener('webglcontextlost',onLost);
      resize();start();
    }catch(error){host.dataset.wavesError=error.message;fail();}
    return {
      get state(){return {status:status(),time,remaining:Number.isFinite(remaining)?remaining:null,frames,options:{...options},width:canvas.width,height:canvas.height,mouse:[...mouse]};},
      update(patch){options=normalize(patch,options);draw();stop();start();},
      pause(){paused=true;stop();notify();},
      play(){paused=false;if(remaining<=0)remaining=duration;start();},
      replay(){time=0;remaining=duration;paused=false;mouse.fill(.5);target.fill(.5);draw();stop();start();},
      snapshot(){if(failed||destroyed)throw Error('波浪不可用 无法导出');draw();return canvas.toDataURL('image/png');},
      destroy(){
        if(destroyed)return;destroyed=true;stop();ro?.disconnect();io?.disconnect();
        removeEventListener('pointermove',onPointer);document.documentElement.removeEventListener('pointerleave',onLeave);
        document.removeEventListener('visibilitychange',onVisibility);reduced.removeEventListener('change',onReduced);
        removeEventListener('beforeprint',onPrint);removeEventListener('afterprint',afterPrint);canvas.removeEventListener('webglcontextlost',onLost);
        if(gl){gl.deleteBuffer(buffer);gl.deleteProgram(program);shaders.forEach(s=>gl.deleteShader(s));gl.getExtension('WEBGL_lose_context')?.loseContext();}
        canvas.remove();delete host.dataset.wavesStatus;
      }
    };
  }
  window.GaryGradientWaves=Object.freeze({mount,defaults,ranges,normalize});
})();
