/*
 * App-level Light Rays scene adapted from the local ShiBao AI / Gary-UI Portal implementation.
 * Kept as an application background, not exported as a reusable component package.
 */
(() => {
  'use strict';

  const options = Object.freeze({
    raysColor: [1, 1, 1],
    raysSpeed: 1.5,
    lightSpread: 0.8,
    rayLength: 1.2,
    fadeDistance: 1,
    saturation: 1,
    followMouse: true,
    mouseInfluence: 0.1,
    noiseAmount: 0.1,
    distortion: 0.05,
  });

  const vertexSource = `#version 300 es
in vec2 position;
void main(){gl_Position=vec4(position,0.0,1.0);}`;

  const fragmentSource = `#version 300 es
precision highp float;
uniform float iTime;
uniform vec2 iResolution;
uniform vec2 rayPos;
uniform vec2 rayDir;
uniform vec3 raysColor;
uniform float raysSpeed;
uniform float lightSpread;
uniform float rayLength;
uniform float fadeDistance;
uniform float saturation;
uniform vec2 mousePos;
uniform float mouseInfluence;
uniform float noiseAmount;
uniform float distortion;
out vec4 fragColor;

float noise(vec2 st){return fract(sin(dot(st.xy,vec2(12.9898,78.233)))*43758.5453123);}
float rayStrength(vec2 source,vec2 referenceDir,vec2 coord,float seedA,float seedB,float speed){
  vec2 sourceToCoord=coord-source;
  vec2 dirNorm=normalize(sourceToCoord);
  float cosAngle=dot(dirNorm,referenceDir);
  float distortedAngle=cosAngle+distortion*sin(iTime*2.0+length(sourceToCoord)*0.01)*0.2;
  float spreadFactor=pow(max(distortedAngle,0.0),1.0/max(lightSpread,0.001));
  float distance=length(sourceToCoord);
  float maxDistance=iResolution.x*rayLength;
  float lengthFalloff=clamp((maxDistance-distance)/maxDistance,0.0,1.0);
  float fadeFalloff=clamp((iResolution.x*fadeDistance-distance)/(iResolution.x*fadeDistance),0.5,1.0);
  float baseStrength=clamp((0.45+0.15*sin(distortedAngle*seedA+iTime*speed))+(0.3+0.2*cos(-distortedAngle*seedB+iTime*speed)),0.0,1.0);
  return baseStrength*lengthFalloff*fadeFalloff*spreadFactor;
}
void main(){
  vec2 coord=vec2(gl_FragCoord.x,iResolution.y-gl_FragCoord.y);
  vec2 finalDir=rayDir;
  if(mouseInfluence>0.0){
    vec2 mouseDirection=normalize(mousePos*iResolution.xy-rayPos);
    finalDir=normalize(mix(rayDir,mouseDirection,mouseInfluence));
  }
  vec4 color=vec4(1.0)*rayStrength(rayPos,finalDir,coord,36.2214,21.11349,1.5*raysSpeed)*0.5;
  color+=vec4(1.0)*rayStrength(rayPos,finalDir,coord,22.3991,18.0234,1.1*raysSpeed)*0.4;
  float n=noise(coord*0.01+iTime*0.1);
  color.rgb*=1.0-noiseAmount+noiseAmount*n;
  float brightness=1.0-(coord.y/iResolution.y);
  color.r*=0.1+brightness*0.8;
  color.g*=0.3+brightness*0.6;
  color.b*=0.5+brightness*0.5;
  float gray=dot(color.rgb,vec3(0.299,0.587,0.114));
  color.rgb=mix(vec3(gray),color.rgb,saturation)*raysColor;
  fragColor=color;
}`;

  const mount = () => {
    const scene = document.querySelector('[data-gary-scene-engine="light-rays"]');
    if (!scene) return false;
    if (scene.querySelector('canvas')) return true;

    const canvas = document.createElement('canvas');
    canvas.className = 'portal-scene-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    const gl = canvas.getContext('webgl2', { alpha: true, antialias: false, powerPreference: 'low-power' });
    if (!gl) {
      scene.dataset.sceneStatus = 'fallback';
      return true;
    }

    const compile = (type, source) => {
      const shader = gl.createShader(type);
      if (!shader) throw new Error('Unable to create WebGL shader');
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || 'Shader compile failed');
      return shader;
    };

    let program;
    try {
      program = gl.createProgram();
      if (!program) throw new Error('Unable to create WebGL program');
      gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexSource));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSource));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || 'Program link failed');
    } catch (error) {
      console.warn('Light Rays scene unavailable', error);
      scene.dataset.sceneStatus = 'fallback';
      return true;
    }

    scene.append(canvas);
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

    const uniformNames = ['iTime', 'iResolution', 'rayPos', 'rayDir', 'raysColor', 'raysSpeed', 'lightSpread', 'rayLength', 'fadeDistance', 'saturation', 'mousePos', 'mouseInfluence', 'noiseAmount', 'distortion'];
    const uniforms = Object.fromEntries(uniformNames.map(name => [name, gl.getUniformLocation(program, name)]));
    gl.uniform3fv(uniforms.raysColor, options.raysColor);
    gl.uniform1f(uniforms.raysSpeed, options.raysSpeed);
    gl.uniform1f(uniforms.lightSpread, options.lightSpread);
    gl.uniform1f(uniforms.rayLength, options.rayLength);
    gl.uniform1f(uniforms.fadeDistance, options.fadeDistance);
    gl.uniform1f(uniforms.saturation, options.saturation);
    gl.uniform1f(uniforms.mouseInfluence, options.mouseInfluence);
    gl.uniform1f(uniforms.noiseAmount, options.noiseAmount);
    gl.uniform1f(uniforms.distortion, options.distortion);

    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let visible = true;
    const mouse = { x: 0.5, y: 0.5, smoothX: 0.5, smoothY: 0.5 };

    const resize = () => {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(scene.clientWidth * dpr));
      const height = Math.max(1, Math.round(scene.clientHeight * dpr));
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
      gl.uniform2f(uniforms.iResolution, width, height);
      gl.uniform2f(uniforms.rayPos, width * 0.5, -height * 0.2);
      gl.uniform2f(uniforms.rayDir, 0, 1);
    };

    const render = time => {
      frame = 0;
      if (!visible || document.hidden) return;
      mouse.smoothX = mouse.smoothX * 0.92 + mouse.x * 0.08;
      mouse.smoothY = mouse.smoothY * 0.92 + mouse.y * 0.08;
      gl.uniform1f(uniforms.iTime, time * 0.001);
      gl.uniform2f(uniforms.mousePos, mouse.smoothX, mouse.smoothY);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      scene.dataset.sceneStatus = reducedMotion.matches ? 'reduced-motion' : 'ready';
      if (!reducedMotion.matches) frame = requestAnimationFrame(render);
    };
    const start = () => {
      if (!frame && visible && !document.hidden) frame = requestAnimationFrame(render);
    };
    const stop = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    };

    new ResizeObserver(() => {
      resize();
      if (reducedMotion.matches) start();
    }).observe(scene);
    new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      visible ? start() : stop();
    }, { threshold: 0.01 }).observe(scene);
    reducedMotion.addEventListener?.('change', () => {
      stop();
      start();
    });
    addEventListener('mousemove', event => {
      if (!options.followMouse) return;
      const rect = scene.getBoundingClientRect();
      mouse.x = (event.clientX - rect.left) / rect.width;
      mouse.y = (event.clientY - rect.top) / rect.height;
    }, { passive: true });
    document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      stop();
      scene.dataset.sceneStatus = 'fallback';
    });

    resize();
    start();
    return true;
  };

  const boot = () => {
    if (mount()) return;
    const observer = new MutationObserver(() => {
      if (mount()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  };

  if (document.readyState === 'loading') {
    addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
