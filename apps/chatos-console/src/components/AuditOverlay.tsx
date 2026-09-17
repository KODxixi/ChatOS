import { useEffect, useState } from 'react'
import type { AuditStats } from '../types'
import { getAuditStats } from '../api'
import { formatNumber } from '../utils/format'

const DOMAIN_COLORS = ['#a79bf7', '#5ac8fa', '#5fe386', '#ffb340', '#d08bff']

function TrendChart({ data }: { data: Array<{ date: string; count: number }> }) {
  const w = 900, h = 300, pad = 28
  if (!data.length) return null
  const max = Math.max(...data.map((d) => d.count), 1)
  const step = (w - pad * 2) / Math.max(data.length - 1, 1)
  const pts = data.map((d, i) => [pad + i * step, h - pad - (d.count / max) * (h - pad * 2)] as const)
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${h - pad} L${pts[0][0].toFixed(1)},${h - pad} Z`
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 'auto' }}>
      <defs>
        <linearGradient id="ovTrend" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#74a7ff" stopOpacity="0.32" />
          <stop offset="100%" stopColor="#74a7ff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#ovTrend)" />
      <path d={line} fill="none" stroke="#74a7ff" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function HBar({ items, maxVal, color }: { items: Array<{ label: string; value: number }>; maxVal: number; color: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {items.map((it, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '160px 1fr 56px', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 12.5, color: 'var(--demo-ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label}</span>
          <div style={{ height: 6, borderRadius: 4, background: 'var(--line)', overflow: 'hidden' }}>
            <div style={{ width: `${maxVal ? (it.value / maxVal) * 100 : 0}%`, height: '100%', borderRadius: 4, background: color }} />
          </div>
          <span style={{ fontSize: 12, color: 'var(--quaternary)', fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>{formatNumber(it.value)}</span>
        </div>
      ))}
    </div>
  )
}

function Donut({ items }: { items: Array<{ label: string; value: number }> }) {
  const total = items.reduce((a, b) => a + b.value, 0) || 1
  const r = 52, cxF = 76, cy = 76, circ = 2 * Math.PI * r
  let offset = 0
  return (
    <svg viewBox="0 0 152 152" style={{ width: 152, height: 152, flexShrink: 0 }}>
      <circle cx={cxF} cy={cy} r={r} fill="none" stroke="var(--line)" strokeWidth="16" />
      {items.map((it, i) => {
        const dash = (it.value / total) * circ
        const el = <circle key={i} cx={cxF} cy={cy} r={r} fill="none" stroke={DOMAIN_COLORS[i % DOMAIN_COLORS.length]} strokeWidth="16" strokeDasharray={`${dash} ${circ - dash}`} strokeDashoffset={-offset} transform={`rotate(-90 ${cxF} ${cy})`} />
        offset += dash
        return el
      })}
      <text x={cxF} y={cy - 2} textAnchor="middle" fontSize="18" fontWeight="600" fill="var(--demo-ink)">{formatNumber(total)}</text>
      <text x={cxF} y={cy + 15} textAnchor="middle" fontSize="9" fill="var(--quaternary)">会话</text>
    </svg>
  )
}

export default function AuditOverlay({ onClose }: { onClose: () => void }) {
  const [stats, setStats] = useState<AuditStats | null>(null)
  const [err, setErr] = useState(false)
  const load = () => { setErr(false); getAuditStats().then(setStats).catch((e) => { console.error('[AuditOverlay] load failed', e); setErr(true) }) }
  useEffect(() => { load() }, [])

  const totalConversations = stats?.kindDistribution?.reduce((a, k) => a + k.conversationCount, 0) ?? 0
  const totalMessages = stats?.kindDistribution?.reduce((a, k) => a + k.messageCount, 0) ?? 0
  const todayMessages = stats?.messageTrend?.length ? stats.messageTrend[stats.messageTrend.length - 1].count : 0
  const kpis = [
    { label: '总会话', value: totalConversations },
    { label: '总消息', value: totalMessages },
    { label: '今日消息', value: todayMessages },
    { label: '高关注会话', value: stats?.attentionMetrics?.length ?? 0 },
  ]
  const topMax = Math.max(...(stats?.attentionMetrics || []).map((t) => t.attentionScore), 1)

  return (
    <div className="overlay-root" onClick={onClose}>
      <div className="overlay-head" onClick={(e) => e.stopPropagation()}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>审计分析</span>
        <div style={{ marginLeft: 'auto' }}>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 24 }} onClick={(e) => e.stopPropagation()}>
        {err && (
          <div style={{ padding: 40, textAlign: 'center' }}>
            <p style={{ color: 'var(--quaternary)', marginBottom: 12 }}>审计数据加载失败</p>
            <button className="button button--small button--primary" onClick={load}>重试</button>
          </div>
        )}
        {!err && (
        <>
        <section className="metric-strip" style={{ marginBottom: 24 }}>
          {kpis.map((k) => (
            <article key={k.label}>
              <p className="gary-role-label" style={{ margin: 0 }}>{k.label}</p>
              <p className="gary-role-metric" style={{ margin: '10px 0 0' }}>{formatNumber(k.value)}</p>
            </article>
          ))}
        </section>

        <div className="analysis-grid">
          <section className="surface chart-card">
            <h2 className="gary-role-section-title" style={{ margin: '0 0 16px', fontSize: 18 }}>消息量趋势 · 近 30 天</h2>
            <TrendChart data={stats?.messageTrend || []} />
          </section>
          <aside className="surface-frosted evidence-panel" style={{ borderRadius: 30, padding: 24 }}>
            <h2 className="gary-role-section-title" style={{ margin: '0 0 16px', fontSize: 18 }}>领域分布</h2>
            <div style={{ display: 'flex', gap: 18, alignItems: 'center' }}>
              <Donut items={(stats?.domainDistribution || []).map((d) => ({ label: d.domain, value: d.conversationCount }))} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                {(stats?.domainDistribution || []).map((d, i) => (
                  <div key={d.domain} style={{ display: 'flex', gap: 8, fontSize: 12.5 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 2, background: DOMAIN_COLORS[i % DOMAIN_COLORS.length] }} />
                    <span style={{ flex: 1 }}>{d.domain}</span>
                    <span style={{ color: 'var(--quaternary)' }}>{formatNumber(d.conversationCount)}</span>
                  </div>
                ))}
              </div>
            </div>
          </aside>
        </div>

        <section className="surface table-card" style={{ marginTop: 24 }}>
          <h2 className="gary-role-section-title" style={{ margin: '0 0 16px', fontSize: 18 }}>关注度 Top 10</h2>
          <HBar items={(stats?.attentionMetrics || []).slice(0, 10).map((t) => ({ label: t.title, value: t.attentionScore }))} maxVal={topMax} color="#74a7ff" />
        </section>
        </>
        )}
      </div>
    </div>
  )
}
