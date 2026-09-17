/* SPDX-License-Identifier: MIT
 * Gary-UI optical glass. Independently authored using a rounded-box signed
 * distance field and the SVG filter primitives implemented by the browser.
 * One RG vector map drives one displacement operation; there is no color split.
 */
(() => {
  'use strict';
  if (window.GaryGlassSurface) return;
  const ns = 'http://www.w3.org/2000/svg';
  const selector = '[data-gary-refraction]';
  const property = '--gary-refraction-filter';
  const cards = new Map();
  const pending = new Set();
  const mode = new URLSearchParams(location.search).get('glass');
  // SVG filters in backdrop-filter are not interoperable yet. Keep other
  // engines on their existing CSS frost instead of falsely claiming refraction.
  const supported = !['fallback', 'frosted'].includes(mode)
    && /(?:Chrome|Chromium|Edg)\//.test(navigator.userAgent)
    && typeof SVGFEImageElement !== 'undefined'
    && window.CSS?.supports('backdrop-filter', 'url(#gary-optical-probe)');
  let strength = 180;
  let sequence = 0;
  let frame = 0;

  function svgNode(name, attributes) {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }

  function cornerRadius(element, width, height) {
    const values = getComputedStyle(element).borderTopLeftRadius.split(/\s+/);
    const value = values[0];
    const radius = value.endsWith('%') ? parseFloat(value) * Math.min(width, height) / 100 : parseFloat(value);
    return Math.min(width / 2, height / 2, Math.max(0, radius || 0));
  }

  function normalField(width, height, radius) {
    // Cap the texture cost, but express all distances in CSS pixels so the
    // physical bevel thickness is stable on large and high-DPI cards.
    const ratio = Math.min(1, 900 / width, 900 / height);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(2, Math.ceil(width * ratio));
    canvas.height = Math.max(2, Math.ceil(height * ratio));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas is unavailable');
    const pixels = context.createImageData(canvas.width, canvas.height);
    const halfW = width / 2, halfH = height / 2;
    const bevel = Math.min(34, Math.max(10, Math.min(width, height) * .13));
    for (let y = 0; y < canvas.height; y++) {
      for (let x = 0; x < canvas.width; x++) {
        const px = (x + .5) / canvas.width * width - halfW;
        const py = (y + .5) / canvas.height * height - halfH;
        const qx = Math.abs(px) - halfW + radius;
        const qy = Math.abs(py) - halfH + radius;
        const ax = Math.max(qx, 0), ay = Math.max(qy, 0);
        const outside = Math.hypot(ax, ay);
        const distance = outside + Math.min(Math.max(qx, qy), 0) - radius;
        let nx = 0, ny = 0;
        if (outside > 0) { nx = ax / outside; ny = ay / outside; }
        else if (qx > qy) nx = 1;
        else ny = 1;
        nx *= Math.sign(px); ny *= Math.sign(py);
        const depth = Math.max(0, -distance);
        const profile = distance <= 0 && depth < bevel ? Math.sin(Math.PI * depth / bevel) ** 1.3 : 0;
        const i = (y * canvas.width + x) * 4;
        pixels.data[i] = Math.round(127.5 + 127.5 * nx * profile);
        pixels.data[i + 1] = Math.round(127.5 + 127.5 * ny * profile);
        pixels.data[i + 2] = 128;
        pixels.data[i + 3] = 255;
      }
    }
    context.putImageData(pixels, 0, 0);
    return canvas.toDataURL('image/png');
  }

  function useFallback(element, record) {
    record.svg?.remove();
    record.svg = null;
    record.displacement = null;
    element.style.removeProperty(property);
    element.dataset.garyRefractionState = 'fallback';
  }

  function rebuild(element) {
    const record = cards.get(element);
    if (!record) return;
    if (!element.isConnected) { destroy(element); return; }
    if (!supported) { useFallback(element, record); return; }
    // Layout coordinates, not transformed screen bounds, match filter space.
    const width = element.offsetWidth, height = element.offsetHeight;
    if (width < 2 || height < 2) { record.signature = ''; useFallback(element, record); return; }
    const radius = cornerRadius(element, width, height);
    const signature = `${width}:${height}:${radius}`;
    if (record.signature === signature && record.svg) return;
    try {
      const texture = normalField(width, height, radius);
      const id = `gary-optical-${++sequence}`;
      const svg = svgNode('svg', { class: 'gary-refraction-filter', 'aria-hidden': 'true', focusable: 'false', width: 0, height: 0 });
      // Inline positioning keeps this utility layout-neutral even without Gary CSS.
      Object.assign(svg.style, { position: 'absolute', width: '0', height: '0', overflow: 'hidden', pointerEvents: 'none' });
      const defs = svgNode('defs', {});
      const filter = svgNode('filter', { id, x: 0, y: 0, width, height, filterUnits: 'userSpaceOnUse', primitiveUnits: 'userSpaceOnUse', 'color-interpolation-filters': 'sRGB' });
      const image = svgNode('feImage', { href: texture, x: 0, y: 0, width, height, preserveAspectRatio: 'none', result: 'surfaceNormals' });
      const frost = svgNode('feGaussianBlur', { in: 'SourceGraphic', stdDeviation: 2.4, edgeMode: 'duplicate', result: 'frostedBackdrop' });
      const displacement = svgNode('feDisplacementMap', { in: 'frostedBackdrop', in2: 'surfaceNormals', scale: strength * .14, xChannelSelector: 'R', yChannelSelector: 'G' });
      filter.append(image, frost, displacement); defs.append(filter); svg.append(defs);
      record.svg?.remove(); element.append(svg);
      record.svg = svg; record.displacement = displacement; record.signature = signature;
      element.style.setProperty(property, `url(#${id})`);
      element.dataset.garyRefractionState = 'svg';
    } catch (_) { record.signature = ''; useFallback(element, record); }
  }

  function enqueue(element) {
    if (!cards.has(element)) return;
    pending.add(element);
    if (!frame) frame = requestAnimationFrame(() => {
      frame = 0;
      const queue = [...pending]; pending.clear();
      queue.forEach(rebuild);
    });
  }

  const resizeObserver = typeof ResizeObserver === 'function'
    ? new ResizeObserver(entries => entries.forEach(entry => enqueue(entry.target))) : null;

  function mount(element) {
    if (!(element instanceof Element)) throw new TypeError('GaryGlassSurface.mount requires an element');
    if (cards.has(element)) { enqueue(element); return element; }
    cards.set(element, {
      svg: null, displacement: null, signature: '',
      previousState: element.getAttribute('data-gary-refraction-state'),
      previousProperty: element.style.getPropertyValue(property),
      previousPriority: element.style.getPropertyPriority(property)
    });
    resizeObserver?.observe(element);
    rebuild(element);
    return element;
  }

  function destroy(element) {
    const record = cards.get(element);
    if (!record) return;
    resizeObserver?.unobserve(element); pending.delete(element);
    record.svg?.remove(); cards.delete(element);
    if (record.previousState === null) element.removeAttribute('data-gary-refraction-state');
    else element.setAttribute('data-gary-refraction-state', record.previousState);
    if (record.previousProperty) element.style.setProperty(property, record.previousProperty, record.previousPriority);
    else element.style.removeProperty(property);
    if (!pending.size && frame) { cancelAnimationFrame(frame); frame = 0; }
  }

  function scan(root) {
    if (!(root instanceof Element) && root !== document) return;
    if (root.matches?.(selector)) mount(root);
    root.querySelectorAll(selector).forEach(mount);
  }

  function start() {
    scan(document);
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'attributes') {
          if (record.target.matches(selector)) {
            if (cards.has(record.target)) enqueue(record.target); else mount(record.target);
          } else if (record.attributeName === 'data-gary-refraction') destroy(record.target);
        } else record.addedNodes.forEach(scan);
      }
      for (const element of cards.keys()) if (!element.isConnected) destroy(element);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-gary-refraction', 'class'] });
    addEventListener('resize', () => cards.forEach((_, element) => enqueue(element)), { passive: true });
  }

  window.GaryGlassSurface = Object.freeze({
    mount, destroy,
    setStrength(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return strength;
      strength = Math.max(0, Math.min(300, numeric));
      cards.forEach(record => record.displacement?.setAttribute('scale', String(strength * .14)));
      return strength;
    },
    get strength() { return strength; }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
