import { useEffect, useRef, useState, useCallback } from 'react'
import type { GraphData, GraphNode } from '../types'
import { getGraph } from '../api'
import { GRAPH_COLORS } from '../utils/ui'

interface SimNode extends GraphNode {
  x: number; y: number; vx: number; vy: number; r: number
}

const KIND_LABEL: Record<string, string> = { contact: '联系人', group: '群组', topic: '话题', project: '项目' }

export default function GraphOverlay({ onClose }: { onClose: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const nodesRef = useRef<SimNode[]>([])
  const edgesRef = useRef<Array<{ source: SimNode; target: SimNode; weight: number }>>([])
  const viewRef = useRef({ x: 0, y: 0, scale: 1 })
  const dragRef = useRef<{ type: 'node' | 'pan' | null; node?: SimNode; sx: number; sy: number }>({ type: null, sx: 0, sy: 0 })
  const selectedRef = useRef<string | null>(null)
  const hoveredRef = useRef<string | null>(null)
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [meta, setMeta] = useState({ nodes: 0, edges: 0 })
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const data: GraphData = await getGraph(80, true)
      const W = wrapRef.current?.clientWidth || 800
      const H = wrapRef.current?.clientHeight || 600
      const nodes: SimNode[] = data.nodes.map((n, i) => ({
        ...n,
        x: W / 2 + (Math.random() - 0.5) * 300,
        y: H / 2 + (Math.random() - 0.5) * 300,
        vx: 0, vy: 0,
        r: Math.max(6, Math.min(18, (n.size || 1) * 4)),
        _i: i,
      } as SimNode))
      const byId = new Map(nodes.map((n) => [n.id, n]))
      const edges = data.edges
        .map((e) => ({ source: byId.get(e.sourceNodeId), target: byId.get(e.targetNodeId), weight: e.weight || 1 }))
        .filter((e) => e.source && e.target) as Array<{ source: SimNode; target: SimNode; weight: number }>
      nodesRef.current = nodes
      edgesRef.current = edges
      setMeta({ nodes: nodes.length, edges: edges.length })
    } catch (e) { console.error(e) } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    let ctx: CanvasRenderingContext2D | null = null
    let intervalId: ReturnType<typeof setInterval> | null = null
    let disposed = false

    const resize = () => {
      const w = wrap.clientWidth, h = wrap.clientHeight
      if (!w || !h) return
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      canvas.style.width = w + 'px'
      canvas.style.height = h + 'px'
      ctx = canvas.getContext('2d')
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(wrap)

    const tick = () => {
      if (disposed) return
      try {
        if (!ctx) ctx = canvas.getContext('2d')
        if (!ctx) return
        const nodes = nodesRef.current
        const edges = edgesRef.current
        const W = wrap.clientWidth, H = wrap.clientHeight
        if (!W || !H || nodes.length === 0) return
        for (let i = 0; i < nodes.length; i++) {
          const a = nodes[i]
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j]
            const dx = a.x - b.x, dy = a.y - b.y
            const d = Math.hypot(dx, dy) || 1
            const force = 2600 / (d * d)
            const fx = (dx / d) * force, fy = (dy / d) * force
            if (dragRef.current.node !== a) { a.vx += fx; a.vy += fy }
            if (dragRef.current.node !== b) { b.vx -= fx; b.vy -= fy }
          }
          a.vx += (W / 2 - a.x) * 0.002
          a.vy += (H / 2 - a.y) * 0.002
        }
        for (const e of edges) {
          const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y
          const d = Math.hypot(dx, dy) || 1
          const spring = (d - 120) * 0.02 * Math.min(e.weight, 3)
          const fx = (dx / d) * spring, fy = (dy / d) * spring
          if (dragRef.current.node !== e.source) { e.source.vx += fx; e.source.vy += fy }
          if (dragRef.current.node !== e.target) { e.target.vx -= fx; e.target.vy -= fy }
        }
        for (const n of nodes) {
          if (dragRef.current.node === n) continue
          n.vx *= 0.85; n.vy *= 0.85
          n.x += n.vx; n.y += n.vy
        }
        const v = viewRef.current
        ctx.clearRect(0, 0, W, H)
        ctx.save()
        ctx.translate(v.x, v.y)
        ctx.scale(v.scale, v.scale)
        const sel = selectedRef.current
        const hovered = hoveredRef.current
        const neighbor = new Set<string>()
        if (sel) {
          neighbor.add(sel)
          for (const e of edges) {
            if (e.source.id === sel) neighbor.add(e.target.id)
            if (e.target.id === sel) neighbor.add(e.source.id)
          }
        }
        for (const e of edges) {
          const hot = !!sel && (e.source.id === sel || e.target.id === sel)
          ctx.strokeStyle = hot ? 'rgba(116,167,255,0.8)' : 'rgba(255,255,255,0.08)'
          ctx.lineWidth = Math.min(3, 0.5 + e.weight * 0.4)
          ctx.beginPath()
          ctx.moveTo(e.source.x, e.source.y)
          ctx.lineTo(e.target.x, e.target.y)
          ctx.stroke()
        }
        for (const n of nodes) {
          const dim = !!sel && !neighbor.has(n.id)
          const color = GRAPH_COLORS[n.kind] || '#5ac8fa'
          ctx.beginPath()
          ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
          ctx.fillStyle = dim ? color + '33' : color
          ctx.globalAlpha = dim ? 0.35 : 1
          ctx.fill()
          ctx.globalAlpha = 1
          if (sel === n.id || hovered === n.id) {
            ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke()
          }
          if (!dim) {
            ctx.fillStyle = 'rgba(255,255,255,0.75)'
            ctx.font = '11px sans-serif'; ctx.textAlign = 'center'
            ctx.fillText(n.label.length > 10 ? n.label.slice(0, 10) + '…' : n.label, n.x, n.y + n.r + 12)
          }
        }
        ctx.restore()
      } catch (err) { console.error('[GraphOverlay] render:', err) }
    }
    intervalId = setInterval(tick, 1000 / 30)
    return () => { disposed = true; if (intervalId) clearInterval(intervalId); ro.disconnect() }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const toLocal = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const v = viewRef.current
      return { x: (e.clientX - rect.left - v.x) / v.scale, y: (e.clientY - rect.top - v.y) / v.scale }
    }
    const onDown = (e: MouseEvent) => {
      const p = toLocal(e)
      const hit = nodesRef.current.find((n) => Math.hypot(n.x - p.x, n.y - p.y) <= n.r + 4)
      if (hit) dragRef.current = { type: 'node', node: hit, sx: p.x - hit.x, sy: p.y - hit.y }
      else dragRef.current = { type: 'pan', sx: e.clientX - viewRef.current.x, sy: e.clientY - viewRef.current.y }
    }
    const onMove = (e: MouseEvent) => {
      const p = toLocal(e)
      const drag = dragRef.current
      if (drag?.type === 'node' && drag.node) {
        drag.node.x = p.x - drag.sx; drag.node.y = p.y - drag.sy
        drag.node.vx = 0; drag.node.vy = 0
      } else if (drag?.type === 'pan') {
        viewRef.current.x = e.clientX - drag.sx
        viewRef.current.y = e.clientY - drag.sy
      } else {
        const hit = nodesRef.current.find((n) => Math.hypot(n.x - p.x, n.y - p.y) <= n.r + 4)
        hoveredRef.current = hit?.id || null
        canvas.style.cursor = hit ? 'grab' : 'default'
      }
    }
    const onUp = () => {
      const drag = dragRef.current
      if (drag?.type === 'node' && drag.node) {
        selectedRef.current = drag.node.id
        setSelected(drag.node)
      }
      dragRef.current = { type: null, sx: 0, sy: 0 }
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const v = viewRef.current
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left, my = e.clientY - rect.top
      const factor = e.deltaY < 0 ? 1.1 : 0.9
      const newScale = Math.min(3, Math.max(0.3, v.scale * factor))
      v.x = mx - (mx - v.x) * (newScale / v.scale)
      v.y = my - (my - v.y) * (newScale / v.scale)
      v.scale = newScale
    }
    canvas.addEventListener('mousedown', onDown)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      canvas.removeEventListener('mousedown', onDown)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      canvas.removeEventListener('wheel', onWheel)
    }
  }, [])

  return (
    <div className="overlay-root" onClick={onClose}>
      <div className="overlay-head" onClick={(e) => e.stopPropagation()}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>知识图谱</span>
        <div style={{ display: 'flex', gap: 14, fontSize: 12, color: 'var(--secondary)' }}>
          {Object.entries(GRAPH_COLORS).map(([k, color]) => (
            <span key={k} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: color }} />
              {KIND_LABEL[k] || k}
            </span>
          ))}
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      </div>
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }} onClick={(e) => e.stopPropagation()}>
        <div ref={wrapRef} style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          {loading && <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--quaternary)', fontSize: 13 }}>加载图谱…</div>}
          <canvas ref={canvasRef} style={{ display: 'block' }} />
        </div>
        <aside style={{ width: 240, flexShrink: 0, padding: 20, borderLeft: '1px solid var(--line)' }}>
          <div className="detail-eyebrow" style={{ marginBottom: 10 }}>统计</div>
          <div style={{ fontSize: 13, color: 'var(--secondary)', marginBottom: 4 }}>节点 <b style={{ float: 'right', color: '#fff' }}>{meta.nodes}</b></div>
          <div style={{ fontSize: 13, color: 'var(--secondary)', marginBottom: 18 }}>边 <b style={{ float: 'right', color: '#fff' }}>{meta.edges}</b></div>
          <div className="detail-eyebrow" style={{ marginBottom: 10 }}>选中节点</div>
          {selected ? (
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>{selected.label}</div>
              <div style={{ fontSize: 12, color: 'var(--quaternary)' }}>类型：{KIND_LABEL[selected.kind] || selected.kind}</div>
            </div>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--quaternary)' }}>点击节点查看详情</div>
          )}
        </aside>
      </div>
    </div>
  )
}
