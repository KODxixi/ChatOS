import type { CSSProperties } from 'react'

// Reusable style fragments built on Gary-UI CSS variables.
// Never hardcode colors except tiny status accents.

export const c = {
  textPrimary: 'var(--gary-text-primary)',
  textSecondary: 'var(--gary-text-secondary)',
  textTertiary: 'var(--gary-text-tertiary)',
  focus: 'var(--gary-focus-ring)',
  positive: 'var(--gary-state-positive)',
  caution: 'var(--gary-state-caution)',
  critical: 'var(--gary-state-critical)',
  borderHi: 'var(--gary-border-highlight)',
  radiusControl: 'var(--gary-radius-control)',
  radiusCard: 'var(--gary-radius-card)',
  radiusPanel: 'var(--gary-radius-panel)',
}

export const glassCard: CSSProperties = {
  borderRadius: 'var(--gary-radius-card)',
  padding: 20,
}

export const sectionTitle: CSSProperties = {
  fontSize: 'clamp(1.1rem, 1.6vw, 1.45rem)',
  fontWeight: 650,
  color: 'var(--gary-text-primary)',
  marginBottom: 4,
}

export const sectionSub: CSSProperties = {
  fontSize: 13,
  color: 'var(--gary-text-tertiary)',
  marginBottom: 16,
}

export const badge: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  padding: '2px 8px',
  borderRadius: 999,
  letterSpacing: 0.2,
  whiteSpace: 'nowrap',
}

export const KIND_BADGE: Record<'group' | 'direct' | 'system', { label: string; bg: string; fg: string }> = {
  group: { label: '群', bg: 'rgba(124,110,246,0.16)', fg: '#a79bf7' },
  direct: { label: '私', bg: 'rgba(48,209,88,0.16)', fg: '#5fe386' },
  system: { label: '订', bg: 'rgba(255,214,10,0.16)', fg: '#ffd60a' },
}

export const DOMAIN_META: Record<string, { label: string; color: string }> = {
  project: { label: '项目', color: '#a79bf7' },
  work: { label: '工作', color: '#5ac8fa' },
  personal: { label: '个人', color: '#5fe386' },
  service: { label: '服务', color: '#ffb340' },
  subscription: { label: '订阅', color: '#d08bff' },
}

export const PRIORITY_META: Record<string, { label: string; color: string }> = {
  high: { label: '高', color: '#ff6b60' },
  medium: { label: '中', color: '#ffd60a' },
  low: { label: '低', color: '#8e8e93' },
}

export const GRAPH_COLORS: Record<string, string> = {
  contact: '#5ac8fa',
  group: '#a79bf7',
  topic: '#5fe386',
  project: '#ffb340',
}
