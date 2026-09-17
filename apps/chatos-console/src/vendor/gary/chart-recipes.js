/* Gary-UI native SVG reference recipes. Visual policy is supplied by the caller. */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const semanticRoles = ['primary', 'secondary', 'reference', 'negative', 'neutral'];
  const roles = [...semanticRoles, ...Array.from({ length: 8 }, (_, index) => `category${index + 1}`)];
  const kindLabels = { data: '原始数据', derived: '派生判断', assumed: '假设' };
  let sequence = 0;
  const fail = (path, message) => { throw new Error(`GaryChartRecipes: ${path} ${message}`); };
  const object = (value, path) => { if (!value || typeof value !== 'object' || Array.isArray(value)) fail(path, '必须是对象。'); };
  const string = (value, path) => { if (typeof value !== 'string' || !value.trim()) fail(path, '必须是非空字符串。'); };
  const finite = (value, path) => { if (!Number.isFinite(value)) fail(path, '必须是有限数值；缺测不能用零替代。'); };
  const list = (value, path, min = 1) => { if (!Array.isArray(value) || value.length < min) fail(path, `至少需要 ${min} 项。`); };
  const clean = value => Number(value.toPrecision(12));
  const display = value => typeof value === 'number' ? String(Number.isInteger(value) ? value : clean(value)) : String(value);
  const sum = values => values.reduce((total, value) => total + value, 0);
  const signed = value => `${value > 0 ? '+' : value < 0 ? '−' : ''}${display(Math.abs(value))}`;
  const percent = value => value > 0 && value < .1 ? '<0.1%' : `${Number(value.toFixed(1))}%`;
  function scale(value, path) {
    if (!Array.isArray(value) || value.length !== 2) fail(path, '必须是 [min,max]。');
    value.forEach((item, index) => finite(item, `${path}[${index}]`));
    if (value[0] >= value[1]) fail(path, '上界必须大于下界。');
    if (!Number.isFinite(value[1] - value[0]) || (value[1] - value[0]) / 5 === 0) fail(path, '跨度超出可安全表达的范围，请调整计量单位。');
  }
  function within(value, domain, path) { if (value < domain[0] || value > domain[1]) fail(path, '超出显式坐标范围；不得裁去真实值。'); }
  function role(value, path) { if (value !== undefined && !roles.includes(value)) fail(path, `必须是 ${roles.join('/')}。`); }
  function annotationMeta(value, path) {
    object(value, path); string(value.label, `${path}.label`); string(value.basis, `${path}.basis`);
    if (!Object.hasOwn(kindLabels, value.kind)) fail(`${path}.kind`, '必须标明 data/derived/assumed。');
  }
  function cumulative(items) {
    let total = 0;
    return items.map((item, index) => {
      const next = total + item.value;
      if (!Number.isFinite(next) || (item.value !== 0 && (next === total || display(next) === display(total)))) fail(`waterfall.items[${index}]`, '增量超出数值或展示精度；请改用最小整数计量单位，或核对原始输入。');
      total = next; return total;
    });
  }
  function validateData(type, config, allowEmptyDonut = false) {
    if (!['combo', 'waterfall', 'donut', 'radar', 'rose'].includes(type)) fail('type', '不支持；其他类型请使用既有 ECharts 适配器。');
    object(config, type); object(config.meta, `${type}.meta`);
    ['title', 'unit', 'source', 'status'].forEach(key => string(config.meta[key], `${type}.meta.${key}`));
    if (type === 'combo') {
      list(config.categories, 'combo.categories');
      config.categories.forEach((value, index) => string(value, `combo.categories[${index}]`));
      if (new Set(config.categories).size !== config.categories.length) fail('combo.categories', '类别名称必须唯一，标注才能稳定绑定对象。');
      object(config.scales, 'combo.scales'); scale(config.scales.left, 'combo.scales.left'); scale(config.scales.right, 'combo.scales.right');
      within(0, config.scales.left, 'combo.scales.left 零基线');
      ['bar', 'line'].forEach(key => {
        const series = config[key]; object(series, `combo.${key}`);
        string(series.label, `combo.${key}.label`); string(series.unit, `combo.${key}.unit`); role(series.role, `combo.${key}.role`);
        list(series.values, `combo.${key}.values`);
        if (series.values.length !== config.categories.length) fail(`combo.${key}.values`, '必须与 categories 等长。');
        series.values.forEach((value, index) => {
          if (key === 'line' && value === null) return;
          finite(value, `combo.${key}.values[${index}]`); within(value, config.scales[key === 'bar' ? 'left' : 'right'], `combo.${key}.values[${index}]`);
        });
        if (key === 'line' && series.values.every(value => value === null)) fail('combo.line.values', '不能全部缺测；可改用单系列图。');
      });
      if (config.annotation !== undefined) {
        const a = config.annotation; object(a, 'combo.annotation');
        string(a.start, 'combo.annotation.start'); string(a.end, 'combo.annotation.end');
        if (!config.categories.includes(a.start) || !config.categories.includes(a.end)) fail('combo.annotation', 'start/end 必须对应当前 categories 的类别名称。');
        annotationMeta(a, 'combo.annotation');
        if (a.detail !== undefined) string(a.detail, 'combo.annotation.detail');
      }
    }
    if (type === 'waterfall') {
      list(config.items, 'waterfall.items'); scale(config.scale, 'waterfall.scale'); string(config.totalLabel, 'waterfall.totalLabel');
      within(0, config.scale, 'waterfall.scale 零基线');
      config.items.forEach((item, index) => { object(item, `waterfall.items[${index}]`); string(item.label, `waterfall.items[${index}].label`); finite(item.value, `waterfall.items[${index}].value`); });
      cumulative(config.items).forEach((value, index) => within(value, config.scale, `waterfall.items[${index}] 累计值`));
      if (config.scenarios !== undefined) {
        list(config.scenarios, 'waterfall.scenarios');
        config.scenarios.forEach((item, index) => { annotationMeta(item, `waterfall.scenarios[${index}]`); finite(item.value, `waterfall.scenarios[${index}].value`); role(item.role, `waterfall.scenarios[${index}].role`); });
      }
    }
    if (type === 'donut' || type === 'rose') {
      list(config.items, `${type}.items`);
      config.items.forEach((item, index) => {
        object(item, `${type}.items[${index}]`); string(item.label, `${type}.items[${index}].label`); finite(item.value, `${type}.items[${index}].value`); role(item.role, `${type}.items[${index}].role`);
        if (item.value < 0) fail(`${type}.items[${index}].value`, '不能为负数。');
        if (type === 'donut') { string(item.key, `donut.items[${index}].key`); string(item.role, `donut.items[${index}].role`); }
      });
      if (type === 'donut') {
        const total = sum(config.items.map(item => item.value)); finite(total, 'donut 总量');
        if (total <= 0 && !allowEmptyDonut) fail('donut.items', '总量为零，不能生成有意义的比例；请展示原始表。');
        if (new Set(config.items.map(item => item.key)).size !== config.items.length) fail('donut.items.key', '必须唯一。');
        if (config.focusKey !== undefined && !config.items.some(item => item.key === config.focusKey)) fail('donut.focusKey', '必须对应已有项目。');
      } else {
        finite(config.max, 'rose.max'); if (config.max <= 0) fail('rose.max', '必须大于零。');
        config.items.forEach((item, index) => within(item.value, [0, config.max], `rose.items[${index}].value`));
      }
    }
    if (type === 'radar') {
      list(config.dimensions, 'radar.dimensions', 3);
      if (config.normalization !== 'linear-min-max') fail('radar.normalization', '本配方只支持明确声明的 linear-min-max。');
      if (config.reference !== undefined) annotationMeta(config.referenceMeta, 'radar.referenceMeta');
      config.dimensions.forEach((dimension, index) => { object(dimension, `radar.dimensions[${index}]`); string(dimension.label, `radar.dimensions[${index}].label`); string(dimension.unit, `radar.dimensions[${index}].unit`); scale([dimension.min, dimension.max], `radar.dimensions[${index}] 范围`); });
      ['values', ...(config.reference === undefined ? [] : ['reference'])].forEach(key => {
        list(config[key], `radar.${key}`);
        if (config[key].length !== config.dimensions.length) fail(`radar.${key}`, '必须与 dimensions 等长。');
        config[key].forEach((value, index) => { finite(value, `radar.${key}[${index}]`); within(value, [config.dimensions[index].min, config.dimensions[index].max], `radar.${key}[${index}]`); });
      });
    }
    return true;
  }
  const validate = (type, config) => validateData(type, config);

  function node(tag, attributes = {}, content) {
    const element = document.createElementNS(NS, tag);
    Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, value));
    if (content !== undefined) element.textContent = String(content);
    return element;
  }
  function add(parent, tag, attributes, content) { const element = node(tag, attributes, content); parent.append(element); return element; }
  function policyFor(policy, theme) {
    object(policy, 'options.policy'); object(policy.themes, 'policy.themes'); object(policy.themes[theme], `policy.themes.${theme}`);
    ['background', 'text', 'muted', 'grid', ...semanticRoles, 'point'].forEach(key => {
      const color = policy.themes[theme][key];
      if (typeof color !== 'string' || !CSS.supports('color', color)) fail(`policy.themes.${theme}.${key}`, '必须是有效颜色。');
    });
    for (const [group, keys] of Object.entries({ geometry: ['wideWidth', 'wideHeight', 'compactWidth', 'compactHeight', 'minWideWidth', 'minCompactWidth'], typography: ['label', 'small', 'metric'], strokes: ['data', 'reference', 'guide', 'marker'] })) {
      object(policy[group], `policy.${group}`);
      keys.forEach(key => { finite(policy[group][key], `policy.${group}.${key}`); if (policy[group][key] <= 0) fail(`policy.${group}.${key}`, '必须大于零。'); });
    }
    object(policy.fills, 'policy.fills');
    ['area', 'barTop', 'barBottom', 'band'].forEach(key => { finite(policy.fills[key], `policy.fills.${key}`); within(policy.fills[key], [0, 1], `policy.fills.${key}`); });
    string(policy.typography.family, 'policy.typography.family');
    return policy.themes[theme];
  }

  function frame(type, config, options, extraHeight = 0, notes = []) {
    const policy = options.policy, colors = policyFor(policy, options.theme || 'dark');
    const wide = type === 'combo' || type === 'waterfall', geometry = policy.geometry, fonts = policy.typography;
    const width = geometry[wide ? 'wideWidth' : 'compactWidth'], bodyHeight = geometry[wide ? 'wideHeight' : 'compactHeight'] + extraHeight;
    const context = document.createElement('canvas').getContext('2d');
    const measure = (value, size = fonts.label) => { context.font = `${size}px ${fonts.family}`; return context.measureText(display(value)).width; };
    const wrap = (value, available, size = fonts.label) => {
      if (available < size * 2) fail(type, '标签空间不足，请增大策略画布或改用 ECharts。');
      const lines = []; let current = '';
      for (const character of String(value)) {
        if (character === '\n' || measure(current + character, size) > available) { if (current) lines.push(current.trim()); current = character === '\n' ? '' : character; }
        else current += character;
      }
      if (current) lines.push(current.trim());
      return lines;
    };
    const titleLines = wrap(config.meta.title, width - 40);
    if (titleLines.length > 2) fail(`${type}.meta.title`, '标题超过两行，请提供准确短标题，完整说明留在 source/status。');
    const metadata = `${config.meta.status} · 来源：${config.meta.source} · 单位：${config.meta.unit}`;
    const footerParts = measure(metadata, fonts.small) <= width - 40 ? [metadata] : [`${config.meta.status} · 来源：${config.meta.source}`, `单位：${config.meta.unit}`];
    const footerLines = [...footerParts, ...notes].flatMap(value => wrap(value, width - 40, fonts.small));
    const lineHeight = Math.max(fonts.label, fonts.small) * 1.45;
    const height = bodyHeight + Math.max(0, footerLines.length - 1) * lineHeight;
    const id = `gary-chart-recipe-${++sequence}`;
    const svg = node('svg', { xmlns: NS, viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-labelledby': `${id}-title ${id}-desc`, 'data-chart-recipe': type, 'font-family': fonts.family, 'font-variant-numeric': 'tabular-nums' });
    svg.style.cssText = `display:block;width:100%;height:auto;min-width:${geometry[wide ? 'minWideWidth' : 'minCompactWidth']}px;overflow:visible`;
    add(svg, 'title', { id: `${id}-title` }, config.meta.title);
    add(svg, 'desc', { id: `${id}-desc` }, [config.meta.status, config.meta.source, ...notes].join('；'));
    add(svg, 'rect', { width, height, fill: colors.background });
    const textBoxes = [];
    const text = (x, y, value, attributes = {}) => {
      const size = attributes['font-size'] || fonts.label, anchor = attributes['text-anchor'];
      context.font = `${attributes['font-weight'] || 400} ${size}px ${fonts.family}`;
      const content = display(value), bounds = context.measureText(content), boxX = x - (anchor === 'end' ? bounds.width : anchor === 'middle' ? bounds.width / 2 : 0);
      textBoxes.push({ value: content, x: boxX, y: y - bounds.actualBoundingBoxAscent, width: bounds.width, height: bounds.actualBoundingBoxAscent + bounds.actualBoundingBoxDescent });
      return add(svg, 'text', { x, y, fill: colors.text, 'font-size': fonts.label, ...attributes }, content);
    };
    const line = (x1, y1, x2, y2, attributes = {}) => add(svg, 'line', { x1, y1, x2, y2, stroke: colors.grid, 'stroke-width': policy.strokes.guide, ...attributes });
    const multiline = (x, y, lines, attributes = {}) => lines.forEach((value, index) => text(x, y + index * lineHeight, value, attributes));
    multiline(20, fonts.label + 10, titleLines, { 'font-weight': 500 });
    multiline(20, bodyHeight - 14, footerLines, { fill: colors.muted, 'font-size': fonts.small });
    const top = 40 + titleLines.length * lineHeight;
    if (bodyHeight - 104 - top < fonts.label * 4) fail(type, '策略画布高度不足，无法同时容纳坐标与完整文字。');
    const finish = () => {
      textBoxes.forEach((a, index) => {
        if (a.x < -1 || a.y < -1 || a.x + a.width > width + 1 || a.y + a.height > height + 1) fail(`${type} 标签“${a.value}”`, '超出画布，请增大策略尺寸或改用 ECharts。');
        for (const b of textBoxes.slice(index + 1)) if (Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > 1 && Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 1) fail(`${type} 标签“${a.value}”/“${b.value}”`, '空间冲突，请调整标注、准确缩短标签或改用 ECharts；原始表仍可使用。');
      });
      return svg;
    };
    return { svg, colors, policy, fonts, width, bodyHeight, id, top, bottom: bodyHeight - 104, text, line, multiline, measure, wrap, lineHeight, finish };
  }
  function labelLines(f, label, width, path) {
    const lines = f.wrap(label, width);
    if (lines.length > 2) fail(path, '完整标签放不下；请使用准确短标签或既有 ECharts，不能截断。');
    return lines;
  }
  const position = (value, domain, low, high) => low + (value - domain[0]) / (domain[1] - domain[0]) * (high - low);
  const polar = (cx, cy, radius, angle) => [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
  function ticks(domain) {
    const rough = (domain[1] - domain[0]) / 5, power = 10 ** Math.floor(Math.log10(rough));
    const step = [1, 2, 2.5, 5, 10].find(value => value * power >= rough) * power, values = [];
    if (!step || !Number.isFinite(step)) fail('坐标范围', '无法生成稳定刻度，请调整计量单位。');
    const first = Math.ceil(domain[0] / step - 1e-9) * step;
    for (let index = 0; index < 12; index++) {
      const raw = first + index * step;
      if (raw > domain[1] + step * 1e-9) break;
      const tick = Number.isInteger(raw) ? raw : clean(raw);
      if (!values.includes(tick)) values.push(tick);
    }
    if (!values.length || values[0] - domain[0] >= step * .5) values.unshift(domain[0]);
    if (domain[1] - values.at(-1) >= step * .5) values.push(domain[1]);
    return values;
  }
  function axes(f, domain, left, right) {
    for (const value of ticks(domain)) {
      const y = position(value, domain, f.bottom, f.top);
      f.line(left, y, right, y, { 'stroke-dasharray': value === 0 ? 'none' : '4 6' });
      f.text(left - 12, y + f.fonts.label / 3, value, { fill: f.colors.muted, 'text-anchor': 'end' });
    }
  }
  function gradient(f, color) {
    const defs = add(f.svg, 'defs'), id = `${f.id}-bar`, fill = add(defs, 'linearGradient', { id, x1: 0, y1: 0, x2: 0, y2: 1 });
    add(fill, 'stop', { offset: 0, 'stop-color': color, 'stop-opacity': f.policy.fills.barTop });
    add(fill, 'stop', { offset: 1, 'stop-color': color, 'stop-opacity': f.policy.fills.barBottom });
    return `url(#${id})`;
  }
  function combo(config, options) {
    const notes = ['左右轴独立计量；图形并置不代表相关或因果。'];
    if (config.annotation) { const a = config.annotation; notes.push(`A · ${a.label}（${kindLabels[a.kind]}）· 依据：${a.basis}${a.detail ? ` · ${a.detail}` : ''}`); }
    const f = frame('combo', config, options, 0, notes), { colors: c, policy: p } = f;
    f.top += f.fonts.label * 2;
    const left = Math.max(66, f.measure(Math.max(...config.scales.left.map(Math.abs))) + 30), right = f.width - Math.max(66, f.measure(Math.max(...config.scales.right.map(Math.abs))) + 30);
    const step = (right - left) / config.categories.length, x = index => left + step * (index + .5);
    if (step < f.fonts.label * 3) fail('combo.categories', '类别密度超过画布容量；请增大策略宽度或改用 ECharts。');
    const yBar = value => position(value, config.scales.left, f.bottom, f.top), yLine = value => position(value, config.scales.right, f.bottom, f.top);
    const barColor = c[config.bar.role || 'primary'], lineColor = c[config.line.role || 'reference'];
    axes(f, config.scales.left, left, right);
    for (const value of ticks(config.scales.right)) f.text(right + 12, yLine(value) + f.fonts.label / 3, value, { fill: c.muted });
    f.text(left, f.top - f.fonts.label * 2.25, `${config.bar.label} · ${config.bar.unit}`, { fill: barColor });
    f.text(right, f.top - f.fonts.label * 2.25, `${config.line.label} · ${config.line.unit}`, { fill: lineColor, 'text-anchor': 'end' });
    if (config.annotation) {
      const a = config.annotation, indices = [a.start, a.end].map(label => config.categories.indexOf(label)), start = left + Math.min(...indices) * step + 3, end = left + (Math.max(...indices) + 1) * step - 3;
      add(f.svg, 'rect', { x: start, y: f.top, width: end - start, height: f.bottom - f.top, rx: 8, fill: c.primary, 'fill-opacity': p.fills.band, stroke: c.primary, 'stroke-width': p.strokes.reference, 'stroke-dasharray': '5 6' });
      f.text(start + 9, f.top + f.fonts.label + 4, 'A', { fill: c.primary });
    }
    const fill = gradient(f, barColor), barLabels = [];
    config.categories.forEach((label, index) => {
      const value = config.bar.values[index], y = yBar(value), zero = yBar(0), labelY = value >= 0 ? y - 9 : y + f.fonts.label + 7;
      if (f.measure(value) > step - 8) fail(`combo.bar.values[${index}]`, '数值标签过宽，不能省略有效数字。');
      add(f.svg, 'rect', { x: x(index) - step * .25, y: Math.min(y, zero), width: step * .5, height: Math.abs(y - zero), rx: 4, fill, stroke: barColor, 'stroke-width': p.strokes.guide });
      f.text(x(index), labelY, value, { fill: barColor, 'text-anchor': 'middle' }); barLabels.push(labelY);
      f.multiline(x(index), f.bottom + f.lineHeight, labelLines(f, label, step - 8, `combo.categories[${index}]`), { fill: c.muted, 'text-anchor': 'middle' });
    });
    let segment = [];
    const drawSegment = () => { if (segment.length) add(f.svg, 'polyline', { points: segment.join(' '), fill: 'none', stroke: lineColor, 'stroke-width': p.strokes.data, 'stroke-linejoin': 'round' }); segment = []; };
    config.line.values.forEach((value, index) => { if (value === null) drawSegment(); else segment.push(`${x(index)},${yLine(value)}`); }); drawSegment();
    config.line.values.forEach((value, index) => {
      if (value === null) return;
      if (f.measure(value) > step - 8) fail(`combo.line.values[${index}]`, '数值标签过宽，不能省略有效数字。');
      const y = yLine(value); let labelY = y + f.lineHeight;
      if (labelY > f.bottom) labelY = y - f.lineHeight;
      if (Math.abs(labelY - barLabels[index]) < f.lineHeight) labelY += labelY < y ? -f.lineHeight : f.lineHeight;
      add(f.svg, 'circle', { cx: x(index), cy: y, r: p.strokes.marker * 2.2, fill: c.point, stroke: lineColor, 'stroke-width': p.strokes.marker });
      f.text(x(index), labelY, value, { fill: lineColor, 'text-anchor': 'middle' });
    });
    return f.finish();
  }

  function waterfall(config, options) {
    const notes = (config.scenarios || []).map(item => `${item.label}（${kindLabels[item.kind]}）· 依据：${item.basis}`);
    const f = frame('waterfall', config, options, 0, notes), { colors: c, policy: p } = f;
    const hasScenarios = Boolean(config.scenarios), side = hasScenarios ? f.width * .27 : 0, left = Math.max(66, f.measure(Math.max(...config.scale.map(Math.abs))) + 30), right = f.width - side - 32;
    const step = (right - left) / (config.items.length + 1), barWidth = step * .52, y = value => position(value, config.scale, f.bottom, f.top);
    if (step < f.fonts.label * 3) fail('waterfall.items', '类别密度超过画布容量；请增大策略宽度或改用 ECharts。');
    axes(f, config.scale, left, right);
    const totals = cumulative(config.items);
    const items = [...config.items, { label: config.totalLabel, value: totals.at(-1), total: true }];
    items.forEach((item, index) => {
      const before = item.total ? 0 : (totals[index - 1] || 0), after = item.total ? item.value : totals[index], center = left + step * (index + .5), top = Math.min(y(before), y(after));
      const color = item.total ? c.neutral : item.value < 0 ? c.negative : c.primary;
      add(f.svg, 'rect', { x: center - barWidth / 2, y: top, width: barWidth, height: Math.abs(y(before) - y(after)), rx: 5, fill: color, 'fill-opacity': p.fills.barTop });
      const label = signed(item.value); if (f.measure(label) > step - 8) fail(`waterfall.items[${index}]`, '数值标签过宽。');
      f.text(center, top - 10, label, { 'text-anchor': 'middle', fill: item.value < 0 ? c.negative : c.text });
      f.multiline(center, f.bottom + f.lineHeight, labelLines(f, item.label, step - 8, `waterfall.items[${index}].label`), { 'text-anchor': 'middle', fill: c.muted });
      if (index < config.items.length) f.line(center + barWidth / 2, y(after), center + step - barWidth / 2, y(after), { stroke: c.muted, 'stroke-width': p.strokes.reference, 'stroke-dasharray': '3 5' });
    });
    if (hasScenarios) {
      const sx = right + 46, end = f.width - 24, values = config.scenarios.map(item => item.value), domain = [Math.min(0, ...values), Math.max(0, ...values)];
      if (domain[0] === domain[1]) domain[1] = 1;
      const zero = position(0, domain, sx, end), rowHeight = (f.bottom - f.top) / config.scenarios.length;
      if (rowHeight < f.lineHeight * 2.5) fail('waterfall.scenarios', '情景标签空间不足，请减少同图情景或改用 ECharts。');
      f.line(right + 22, f.top - 14, right + 22, f.bottom + 32);
      config.scenarios.forEach((item, index) => {
        const yy = f.top + rowHeight * index, color = c[item.role || 'secondary'], endX = position(item.value, domain, sx, end);
        if (f.measure(item.label) + f.measure(signed(item.value)) + 12 > end - sx) fail(`waterfall.scenarios[${index}].label`, '情景标签空间不足。');
        f.text(sx, yy, item.label, { fill: c.muted }); f.text(end, yy, signed(item.value), { fill: color, 'text-anchor': 'end' });
        f.line(zero, yy + 10, zero, yy + 31, { stroke: c.muted });
        add(f.svg, 'rect', { x: Math.min(zero, endX), y: yy + 13, width: Math.abs(endX - zero), height: 14, rx: 3, fill: color, 'fill-opacity': p.fills.barTop });
      });
    }
    return f.finish();
  }

  function donut(config, options) {
    const extra = Math.max(0, config.items.length * options.policy.typography.small * 2.2 + 100 - options.policy.geometry.compactHeight);
    const f = frame('donut', config, options, extra), { colors: c, policy: p } = f;
    const total = sum(config.items.map(item => item.value)), focus = config.focusKey === undefined ? config.items.reduce((a, b) => a.value >= b.value ? a : b) : config.items.find(item => item.key === config.focusKey);
    const cx = f.width * .255, cy = (f.top + f.bodyHeight - 70) / 2, radius = Math.min(f.width * .20, (f.bodyHeight - f.top - 100) / 2), thickness = radius * .24, circumference = 2 * Math.PI * radius;
    if (radius < f.fonts.label * 2) fail('donut', '环形与完整中心标签的空间不足，请增大策略画布。');
    add(f.svg, 'circle', { cx, cy, r: radius, fill: 'none', stroke: c.grid, 'stroke-width': thickness });
    let offset = 0;
    const legendX = f.width * .54, legendRight = f.width - 18, rowHeight = f.fonts.small * 2.2, legendTop = cy - (config.items.length - 1) * rowHeight / 2;
    const segments = [];
    config.items.forEach((item, index) => {
      const color = c[item.role], length = Math.max(0, Math.min(circumference, circumference * item.value / total));
      if (item.value > 0) add(f.svg, 'circle', { cx, cy, r: radius, fill: 'none', stroke: color, 'stroke-width': thickness, 'stroke-dasharray': `${length} ${circumference - length}`, 'stroke-dashoffset': -offset, transform: `rotate(-90 ${cx} ${cy})` });
      if (length > 0) segments.push({ offset, length });
      offset += length;
      const yy = legendTop + index * rowHeight, ratio = percent(item.value / total * 100);
      if (f.measure(item.label, f.fonts.small) + f.measure(ratio, f.fonts.small) + 29 > legendRight - legendX) fail(`donut.items[${index}].label`, '完整图例放不下；请扩大 compactWidth 或改用 ECharts，不截断标签。');
      add(f.svg, 'rect', { x: legendX, y: yy - 9, width: 8, height: 8, rx: 2, fill: color });
      f.text(legendX + 17, yy, item.label, { fill: c.muted, 'font-size': f.fonts.small });
      f.text(legendRight, yy, ratio, { 'text-anchor': 'end', 'font-size': f.fonts.small });
    });
    segments.forEach((segment, index) => {
      const previous = segments[(index + segments.length - 1) % segments.length];
      if (segments.length < 2 || Math.min(segment.length, previous.length) < p.strokes.guide * 4) return;
      const angle = -Math.PI / 2 + segment.offset / circumference * Math.PI * 2;
      f.line(...polar(cx, cy, radius - thickness / 2, angle), ...polar(cx, cy, radius + thickness / 2, angle), { stroke: c.background, 'stroke-width': p.strokes.guide });
    });
    const summary = percent(focus.value / total * 100), available = (radius - thickness) * 1.8;
    const metricSize = Math.max(f.fonts.label, Math.min(f.fonts.metric, f.fonts.metric * available / f.measure(summary, f.fonts.metric)));
    f.text(cx, cy + metricSize / 4, summary, { 'text-anchor': 'middle', 'font-size': metricSize });
    const focusLines = labelLines(f, focus.label, available, 'donut.focusKey 对应标签');
    f.multiline(cx, cy + metricSize / 4 + Math.max(f.lineHeight, metricSize * .25 + f.fonts.small * 1.25), focusLines, { 'text-anchor': 'middle', 'font-size': f.fonts.small, fill: c.muted });
    return f.finish();
  }

  function radialLabels(f, labels) {
    const boxes = [];
    labels.forEach((label, index) => {
      const width = Math.max(...label.lines.map(value => f.measure(value, f.fonts.small))), x = label.anchor === 'end' ? label.x - width : label.anchor === 'middle' ? label.x - width / 2 : label.x;
      const box = { x, y: label.y - f.fonts.small, width, height: (label.lines.length - 1) * f.lineHeight + f.fonts.small * 1.25 };
      if (box.x < 6 || box.x + box.width > f.width - 6 || box.y < f.top - 32 || box.y + box.height > f.bodyHeight - 31 || boxes.some(other => Math.min(box.x + box.width, other.x + other.width) > Math.max(box.x, other.x) && Math.min(box.y + box.height, other.y + other.height) > Math.max(box.y, other.y))) fail(`${f.svg.dataset.chartRecipe} 标签[${index}]`, '完整标签在当前策略尺寸下重叠或越界；请增大画布、使用准确短标签或改用 ECharts。');
      boxes.push(box); f.multiline(label.x, label.y, label.lines, { 'text-anchor': label.anchor, 'font-size': f.fonts.small, fill: f.colors.muted });
    });
  }
  function radar(config, options) {
    const notes = ['每维按已声明 min/max 线性归一至 0–100%；面积不用于推断综合分数。'];
    if (config.reference) notes.push(`${config.referenceMeta.label}（${kindLabels[config.referenceMeta.kind]}）· 依据：${config.referenceMeta.basis}`);
    const f = frame('radar', config, options, 0, notes), { colors: c, policy: p } = f;
    if (config.reference) {
      const legendY = f.top - 9, referenceX = 46 + f.measure('输入值') + 24;
      f.line(20, legendY - 5, 38, legendY - 5, { stroke: c.primary, 'stroke-width': p.strokes.data }); f.text(46, legendY, '输入值', { fill: c.muted });
      f.line(referenceX, legendY - 5, referenceX + 18, legendY - 5, { stroke: c.referenceNeutral, 'stroke-width': p.strokes.reference, 'stroke-dasharray': '4 5' }); f.text(referenceX + 26, legendY, config.referenceMeta.label, { fill: c.muted });
      f.top += f.lineHeight;
    }
    const cx = f.width / 2, cy = (f.top + f.bodyHeight - 72) / 2, radius = Math.min(f.width * .20, (f.bodyHeight - f.top - 118) / 2), labelRadius = radius + f.fonts.label * 2;
    if (radius < f.fonts.label * 2) fail('radar', '画布高度不足以容纳数据与完整标签。');
    const angles = config.dimensions.map((_, index) => -Math.PI / 2 + index * Math.PI * 2 / config.dimensions.length);
    const normalize = (value, index) => (value - config.dimensions[index].min) / (config.dimensions[index].max - config.dimensions[index].min);
    const points = values => values.map((value, index) => polar(cx, cy, radius * value, angles[index]).join(',')).join(' ');
    [.25, .5, .75, 1].forEach(value => add(f.svg, 'polygon', { points: points(config.dimensions.map(() => value)), fill: 'none', stroke: c.grid, 'stroke-width': p.strokes.guide }));
    angles.forEach(angle => f.line(cx, cy, ...polar(cx, cy, radius, angle)));
    if (config.reference) add(f.svg, 'polygon', { points: points(config.reference.map(normalize)), fill: 'none', stroke: c.referenceNeutral, 'stroke-width': p.strokes.reference, 'stroke-dasharray': '4 5' });
    add(f.svg, 'polygon', { points: points(config.values.map(normalize)), fill: c.primary, 'fill-opacity': p.fills.area, stroke: c.primary, 'stroke-width': p.strokes.data });
    config.values.forEach((value, index) => { const [x, y] = polar(cx, cy, radius * normalize(value, index), angles[index]); add(f.svg, 'circle', { cx: x, cy: y, r: p.strokes.marker * 1.8, fill: c.point, stroke: c.primary, 'stroke-width': p.strokes.marker }); });
    radialLabels(f, config.dimensions.map((dimension, index) => {
      const [x, y] = polar(cx, cy, labelRadius, angles[index]), cosine = Math.cos(angles[index]);
      return { x, y, anchor: cosine > .25 ? 'start' : cosine < -.25 ? 'end' : 'middle', lines: [dimension.label, `${config.values[index]}${dimension.unit} [${dimension.min}–${dimension.max}]`] };
    }));
    return f.finish();
  }
  function rose(config, options) {
    const f = frame('rose', config, options, 0, [`半径按原始值线性映射；外圈 ${config.max} ${config.meta.unit}。`]), { colors: c, policy: p } = f;
    const cx = f.width / 2, cy = (f.top + f.bodyHeight - 72) / 2, radius = Math.min(f.width * .20, (f.bodyHeight - f.top - 118) / 2), labelRadius = radius + f.fonts.label * 2;
    if (radius < f.fonts.label * 2) fail('rose', '画布高度不足以容纳数据与完整标签。');
    [.25, .5, .75, 1].forEach(value => add(f.svg, 'circle', { cx, cy, r: radius * value, fill: 'none', stroke: c.grid, 'stroke-width': p.strokes.guide, 'stroke-dasharray': '3 5' }));
    const labels = [];
    config.items.forEach((item, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / config.items.length, half = Math.PI * .36 / config.items.length, length = item.value / config.max * radius;
      f.line(cx, cy, ...polar(cx, cy, radius, angle));
      if (length > 0) {
        const a = polar(cx, cy, length, angle - half), b = polar(cx, cy, length, angle + half), color = c[item.role || 'primary'];
        add(f.svg, 'path', { d: `M${cx},${cy} L${a[0]},${a[1]} A${length},${length} 0 ${half * 2 > Math.PI ? 1 : 0} 1 ${b[0]},${b[1]} Z`, fill: color, 'fill-opacity': p.fills.barTop, stroke: color, 'stroke-width': p.strokes.guide });
      }
      const [x, y] = polar(cx, cy, labelRadius, angle), cosine = Math.cos(angle);
      labels.push({ x, y, anchor: cosine > .25 ? 'start' : cosine < -.25 ? 'end' : 'middle', lines: [item.label, `${item.value} ${config.meta.unit}`] });
    });
    radialLabels(f, labels); return f.finish();
  }

  function table(type, config) {
    validateData(type, config, true);
    let headers, rows;
    if (type === 'combo') { headers = ['类别', `${config.bar.label}（${config.bar.unit}）`, `${config.line.label}（${config.line.unit}）`]; rows = config.categories.map((label, index) => [label, config.bar.values[index], config.line.values[index] === null ? '缺测' : config.line.values[index]]); }
    if (type === 'waterfall') { headers = ['项目', `变动（${config.meta.unit}）`, `累计（${config.meta.unit}）`]; const totals = cumulative(config.items); rows = config.items.map((item, index) => [item.label, item.value, totals[index]]); rows.push([config.totalLabel, '总计', totals.at(-1)]); config.scenarios?.forEach(item => rows.push([item.label, '独立情景', item.value])); }
    if (type === 'donut') { const total = sum(config.items.map(item => item.value)); headers = ['类别', `原值（${config.meta.unit}）`, '占比（派生）']; rows = config.items.map(item => [item.label, item.value, total === 0 ? '无分母（总量为零）' : percent(item.value / total * 100)]); }
    if (type === 'radar') { headers = ['维度', '单位', '下界', '上界', '原始值', ...(config.reference ? [`${config.referenceMeta.label}原值`] : [])]; rows = config.dimensions.map((dimension, index) => [dimension.label, dimension.unit, dimension.min, dimension.max, config.values[index], ...(config.reference ? [config.reference[index]] : [])]); }
    if (type === 'rose') { headers = ['类别', `原值（${config.meta.unit}）`, `显式上限（${config.meta.unit}）`]; rows = config.items.map(item => [item.label, item.value, config.max]); }
    const result = document.createElement('table'); result.className = 'gary-chart-recipe-table';
    const disclosures = [config.annotation, config.referenceMeta, ...(config.scenarios || [])].filter(Boolean).map(item => `${item.label}（${kindLabels[item.kind]}）· 依据：${item.basis}`);
    result.createCaption().textContent = [`${config.meta.title} · ${config.meta.status} · 来源：${config.meta.source}`, ...disclosures].join('；');
    const head = result.createTHead().insertRow();
    headers.forEach(label => { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; head.append(cell); });
    const body = result.createTBody();
    rows.forEach(values => { const row = body.insertRow(); values.forEach((value, index) => { const cell = document.createElement(index === 0 ? 'th' : 'td'); cell.textContent = display(value); if (typeof value === 'number') cell.dataset.rawValue = String(value); cell.dataset.label = headers[index]; if (index === 0) cell.scope = 'row'; row.append(cell); }); });
    return result;
  }
  function render(type, config, options = {}) {
    validate(type, config); const colors = policyFor(options.policy, options.theme || 'dark');
    const declaredRoles = [config.bar?.role, config.line?.role, ...(config.items || []).map(item => item.role), ...(config.scenarios || []).map(item => item.role), ...(type === 'radar' && config.reference ? ['referenceNeutral'] : [])].filter(Boolean);
    declaredRoles.forEach(key => { if (typeof colors[key] !== 'string' || !CSS.supports('color', colors[key])) fail(`policy.themes.${options.theme || 'dark'}.${key}`, '缺少当前数据使用的有效颜色。'); });
    return { combo, waterfall, donut, radar, rose }[type](config, options);
  }
  window.GaryChartRecipes = { render, table, validate };
})();
