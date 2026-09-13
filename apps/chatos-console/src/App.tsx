import { useEffect, useState, useCallback } from 'react'
import { getHealth, getConversations, searchMessages } from './lib/api'
import type { Conversation, HealthResponse, SearchItem } from './types'

type ViewMode = 'conversations' | 'projects' | 'search' | 'audit'
type FilterKind = 'all' | 'group' | 'direct' | 'system' | 'archived'

const KIND_FILTERS: { id: FilterKind; label: string; color: string }[] = [
  { id: 'all', label: '全部活跃', color: '#4A9EFF' },
  { id: 'group', label: '群聊', color: '#7C6EF6' },
  { id: 'direct', label: '私聊', color: '#34C759' },
  { id: 'system', label: '公众号', color: '#AF52DE' },
  { id: 'archived', label: '已归档', color: '#8E8E93' },
]

function formatTime(ts: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return d.toLocaleDateString('zh-CN')
}

export default function App() {
  const [activeMode, setActiveMode] = useState<ViewMode>('conversations')
  const [activeFilter, setActiveFilter] = useState<FilterKind>('all')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConv, setActiveConv] = useState<Conversation | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [stats, setStats] = useState({ total: 0, active: 0, archived: 0 })

  const loadConversations = useCallback(async (kind: FilterKind) => {
    setLoading(true)
    try {
      const all: Conversation[] = []
      let cursor: string | undefined
      // Fetch up to 200 conversations
      for (let i = 0; i < 4; i++) {
        const page = await getConversations(50, cursor, kind === 'archived' ? undefined : kind === 'all' ? undefined : kind)
        all.push(...page.items)
        if (!page.nextCursor) break
        cursor = page.nextCursor
      }
      // For archived filter, we need to fetch all and filter client-side
      let filtered = all
      if (kind === 'archived') {
        filtered = all.filter((c) => !c.isIndexingEnabled)
      } else if (kind === 'all') {
        filtered = all.filter((c) => c.isIndexingEnabled)
      }
      setConversations(filtered)
      if (filtered.length > 0 && !activeConv) {
        setActiveConv(filtered[0])
      }
      // Compute stats from all fetched
      const total = all.length
      const active = all.filter((c) => c.isIndexingEnabled).length
      setStats({ total, active, archived: total - active })
    } catch (e) {
      console.error('Failed to load conversations', e)
    } finally {
      setLoading(false)
    }
  }, [activeConv])

  useEffect(() => {
    getHealth().then(setHealth).catch(console.error)
  }, [])

  useEffect(() => {
    loadConversations(activeFilter)
  }, [activeFilter, loadConversations])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    try {
      const page = await searchMessages(searchQuery, 50)
      setSearchResults(page.items)
    } catch (e) {
      console.error('Search failed', e)
    } finally {
      setSearching(false)
    }
  }

  const online = health?.app === 'ready'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#000' }}>
      {/* WebBar */}
      <header style={styles.webbar}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={styles.logo}>C</div>
          <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: 0.3 }}>ChatOS</span>
          <span style={{ fontSize: 11, color: '#8E8E93', marginLeft: 4 }}>会话聊天与项目管理</span>
        </div>

        <nav style={{ display: 'flex', gap: 4 }}>
          {(['conversations', 'projects', 'search', 'audit'] as ViewMode[]).map((mode) => (
            <button key={mode} onClick={() => setActiveMode(mode)} style={{
              ...styles.navBtn,
              background: activeMode === mode ? 'rgba(255,255,255,0.12)' : 'transparent',
              color: activeMode === mode ? '#fff' : '#8E8E93',
            }}>
              {mode === 'conversations' ? '会话' : mode === 'projects' ? '项目' : mode === 'search' ? '搜索' : '审计'}
            </button>
          ))}
        </nav>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 8, height: 8, borderRadius: '50%',
            background: online ? '#34C759' : '#FF3B30',
            boxShadow: online ? '0 0 8px #34C759' : '0 0 8px #FF3B30',
          }} />
          <span style={{ fontSize: 12, color: '#8E8E93' }}>
            {online ? `网关在线 · ${stats.active}/${stats.total} 活跃` : '网关离线'}
          </span>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar */}
        <aside style={styles.sidebar}>
          <div style={styles.sidebarLabel}>分类目录</div>
          {KIND_FILTERS.map((f) => (
            <button key={f.id} onClick={() => setActiveFilter(f.id)} style={{
              ...styles.catBtn,
              background: activeFilter === f.id ? 'rgba(255,255,255,0.08)' : 'transparent',
            }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: f.color }} />
                <span style={{ fontSize: 13, color: activeFilter === f.id ? '#fff' : '#C7C7CC' }}>{f.label}</span>
              </span>
              <span style={{ fontSize: 11, color: '#636366' }}>
                {f.id === 'archived' ? stats.archived : f.id === 'all' ? stats.active : ''}
              </span>
            </button>
          ))}

          <div style={styles.statCard}>
            <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 6 }}>活跃度过滤</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#34C759' }}>
              {stats.active} <span style={{ fontSize: 12, fontWeight: 400, color: '#8E8E93' }}>/ {stats.total} 活跃</span>
            </div>
            <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>30天零发言自动归档</div>
          </div>

          <div style={styles.statCard}>
            <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 6 }}>库状态</div>
            <div style={{ fontSize: 12, color: health?.library === 'ready' ? '#34C759' : '#FF9500' }}>
              {health?.library || '检查中...'}
            </div>
            <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>D:\ChatOS-Vault\library</div>
          </div>
        </aside>

        {/* Center */}
        <main style={styles.main}>
          {activeMode === 'search' ? (
            <>
              <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                <input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="搜索消息内容..."
                  style={{
                    flex: 1, padding: '10px 14px', borderRadius: 10,
                    background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                    color: '#fff', fontSize: 14, outline: 'none',
                  }}
                />
                <button onClick={handleSearch} style={{
                  padding: '10px 20px', borderRadius: 10, border: 'none',
                  background: '#4A9EFF', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                }}>搜索</button>
              </div>
              {searching && <div style={{ color: '#8E8E93', fontSize: 13 }}>搜索中...</div>}
              {!searching && searchResults.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {searchResults.map((item) => (
                    <div key={item.id} style={styles.convItem}>
                      <div style={{ fontSize: 12, color: '#4A9EFF', marginBottom: 4 }}>{item.conversationTitle} · {item.senderName}</div>
                      <div style={{ fontSize: 13, color: '#C7C7CC', lineHeight: 1.5 }}>{item.content.slice(0, 200)}</div>
                      <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>{formatTime(item.sentAt)}</div>
                    </div>
                  ))}
                </div>
              )}
              {!searching && searchQuery && searchResults.length === 0 && (
                <div style={{ color: '#8E8E93', fontSize: 13 }}>无结果（已排除归档会话）</div>
              )}
            </>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>
                  {KIND_FILTERS.find((f) => f.id === activeFilter)?.label}
                </h2>
                <span style={{ fontSize: 12, color: '#8E8E93' }}>{conversations.length} 个会话</span>
              </div>

              {loading ? (
                <div style={{ color: '#8E8E93', fontSize: 13 }}>加载中...</div>
              ) : conversations.length === 0 ? (
                <div style={{ color: '#8E8E93', fontSize: 13 }}>暂无会话</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {conversations.map((conv) => (
                    <div key={conv.id} onClick={() => setActiveConv(conv)} style={{
                      ...styles.convItem,
                      background: activeConv?.id === conv.id ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)',
                      borderColor: activeConv?.id === conv.id ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.05)',
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div>
                          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>{conv.title || conv.id}</div>
                          <div style={{ fontSize: 12, color: '#8E8E93' }}>
                            {conv.kind === 'group' ? '群聊' : conv.kind === 'direct' ? '私聊' : '公众号'} · {conv.messageCount} 条消息 · {formatTime(conv.lastMessageAt)}
                          </div>
                        </div>
                        {conv.isFavorite && <span style={{ fontSize: 14 }}>⭐</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </main>

        {/* Right sidebar */}
        <aside style={styles.rightSidebar}>
          <div style={styles.sidebarLabel}>会话详情</div>
          {activeConv ? (
            <>
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>{activeConv.title || activeConv.id}</div>
                <div style={{ fontSize: 12, color: '#8E8E93' }}>
                  {activeConv.kind === 'group' ? '群聊' : activeConv.kind === 'direct' ? '私聊' : '公众号'} · {activeConv.isIndexingEnabled ? '已索引' : '已归档'}
                </div>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={styles.detailCard}>
                  <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>消息总数</div>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>{activeConv.messageCount.toLocaleString()}</div>
                </div>
                <div style={styles.detailCard}>
                  <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>最后活跃</div>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{formatTime(activeConv.lastMessageAt)}</div>
                </div>
                <div style={styles.detailCard}>
                  <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>索引状态</div>
                  <div style={{ fontSize: 14, fontWeight: 500, color: activeConv.isIndexingEnabled ? '#34C759' : '#8E8E93' }}>
                    {activeConv.isIndexingEnabled ? '✓ 参与检索' : '✕ 已排除检索'}
                  </div>
                </div>
              </div>

              {activeConv.note && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 8 }}>备注</div>
                  <div style={{ fontSize: 12, color: '#C7C7CC', lineHeight: 1.6 }}>{activeConv.note}</div>
                </div>
              )}
            </>
          ) : (
            <div style={{ color: '#636366', fontSize: 13 }}>选择一个会话查看详情</div>
          )}
        </aside>
      </div>

      {/* Bottom dock */}
      <footer style={styles.dock}>
        <span>ChatOS v0.1.0 · 与 ArchOS 并列</span>
        <span>会话库: D:\ChatOS-Vault · 分类体系 v1 · gary-ui Liquid Glass</span>
      </footer>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  webbar: {
    height: 52, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '0 20px', background: 'rgba(20,20,22,0.72)', backdropFilter: 'blur(20px)',
    WebkitBackdropFilter: 'blur(20px)', borderBottom: '1px solid rgba(255,255,255,0.08)', zIndex: 100,
  },
  logo: {
    width: 28, height: 28, borderRadius: 8,
    background: 'linear-gradient(135deg, #4A9EFF, #7C6EF6)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 14, fontWeight: 700,
  },
  navBtn: {
    padding: '6px 14px', borderRadius: 8, border: 'none',
    fontSize: 13, cursor: 'pointer', transition: 'all 150ms',
  },
  sidebar: {
    width: 220, background: 'rgba(15,15,17,0.6)', backdropFilter: 'blur(20px)',
    borderRight: '1px solid rgba(255,255,255,0.06)', padding: '16px 10px', overflowY: 'auto',
  },
  sidebarLabel: {
    fontSize: 11, color: '#8E8E93', padding: '0 10px 8px', letterSpacing: 0.5,
  },
  catBtn: {
    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '9px 10px', borderRadius: 10, border: 'none', cursor: 'pointer',
    marginBottom: 2, transition: 'all 150ms',
  },
  statCard: {
    marginTop: 14, padding: 12, borderRadius: 12,
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)',
  },
  main: {
    flex: 1, background: 'rgba(10,10,12,0.4)', overflowY: 'auto', padding: 20,
  },
  convItem: {
    padding: '14px 16px', borderRadius: 14, cursor: 'pointer',
    border: '1px solid rgba(255,255,255,0.05)', transition: 'all 150ms',
  },
  rightSidebar: {
    width: 280, background: 'rgba(15,15,17,0.6)', backdropFilter: 'blur(20px)',
    borderLeft: '1px solid rgba(255,255,255,0.06)', padding: 20, overflowY: 'auto',
  },
  detailCard: {
    padding: 12, borderRadius: 10, background: 'rgba(255,255,255,0.04)',
  },
  dock: {
    height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '0 20px', background: 'rgba(20,20,22,0.8)', backdropFilter: 'blur(20px)',
    borderTop: '1px solid rgba(255,255,255,0.08)', fontSize: 12, color: '#8E8E93',
  },
}
