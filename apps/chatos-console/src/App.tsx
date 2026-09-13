import { useEffect, useState, useCallback, useRef } from 'react'
import { getHealth, getConversations, searchMessages } from './lib/api'
import type { Conversation, HealthResponse, SearchItem } from './types'

type ViewMode = 'conversations' | 'projects' | 'search' | 'audit'
type DomainFilter = 'all' | 'project' | 'work' | 'personal' | 'service' | 'subscription' | 'archived'
type RightTab = 'detail' | 'webui'

const DOMAIN_FILTERS: { id: DomainFilter; label: string; color: string }[] = [
  { id: 'all', label: '全部活跃', color: '#4A9EFF' },
  { id: 'project', label: '项目相关', color: '#7C6EF6' },
  { id: 'work', label: '工作协作', color: '#5AC8FA' },
  { id: 'personal', label: '个人社交', color: '#34C759' },
  { id: 'service', label: '服务消费', color: '#FF9500' },
  { id: 'subscription', label: '资讯订阅', color: '#AF52DE' },
  { id: 'archived', label: '已归档', color: '#8E8E93' },
]

const WEBUI_PRESETS = [
  { label: 'ArchOS Console', url: 'http://localhost:5173' },
  { label: 'ChatOS API', url: 'http://127.0.0.1:8912/docs' },
  { label: 'ChatOS Health', url: 'http://127.0.0.1:8912/api/health' },
  { label: 'Gary UI', url: 'http://localhost:5174' },
]

function formatTime(ts: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const diff = Date.now() - d.getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return d.toLocaleDateString('zh-CN')
}

export default function App() {
  const [activeMode, setActiveMode] = useState<ViewMode>('conversations')
  const [activeFilter, setActiveFilter] = useState<DomainFilter>('all')
  const [allConversations, setAllConversations] = useState<Conversation[]>([])
  const [activeConv, setActiveConv] = useState<Conversation | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchItem[]>([])
  const [searching, setSearching] = useState(false)

  // WebUI state
  const [rightTab, setRightTab] = useState<RightTab>('detail')
  const [webuiUrl, setWebuiUrl] = useState('http://127.0.0.1:8912/docs')
  const [webuiInput, setWebuiInput] = useState('http://127.0.0.1:8912/docs')
  const [webuiLoading, setWebuiLoading] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  const loadConversations = useCallback(async () => {
    setLoading(true)
    try {
      const all: Conversation[] = []
      let cursor: string | undefined
      for (let i = 0; i < 10; i++) {
        const page = await getConversations(50, cursor)
        all.push(...page.items)
        if (!page.nextCursor) break
        cursor = page.nextCursor
      }
      setAllConversations(all)
      if (all.length > 0 && !activeConv) {
        const firstActive = all.find((c) => c.isIndexingEnabled)
        setActiveConv(firstActive || all[0])
      }
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
    loadConversations()
  }, [loadConversations])

  const filteredConversations = allConversations.filter((c) => {
    if (activeFilter === 'all') return c.isIndexingEnabled
    if (activeFilter === 'archived') return !c.isIndexingEnabled
    return c.isIndexingEnabled && c.domain === activeFilter
  })

  const domainCounts = DOMAIN_FILTERS.reduce((acc, f) => {
    if (f.id === 'all') acc[f.id] = allConversations.filter((c) => c.isIndexingEnabled).length
    else if (f.id === 'archived') acc[f.id] = allConversations.filter((c) => !c.isIndexingEnabled).length
    else acc[f.id] = allConversations.filter((c) => c.isIndexingEnabled && c.domain === f.id).length
    return acc
  }, {} as Record<string, number>)

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

  const loadWebui = (url: string) => {
    setWebuiUrl(url)
    setWebuiInput(url)
    setWebuiLoading(true)
  }

  const online = health?.app === 'ready'
  const activeCount = allConversations.filter((c) => c.isIndexingEnabled).length

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
            {online ? `${activeCount}/${allConversations.length} 活跃` : '网关离线'}
          </span>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar - domain categories */}
        <aside style={styles.sidebar}>
          <div style={styles.sidebarLabel}>分类目录</div>
          {DOMAIN_FILTERS.map((f) => (
            <button key={f.id} onClick={() => setActiveFilter(f.id)} style={{
              ...styles.catBtn,
              background: activeFilter === f.id ? 'rgba(255,255,255,0.08)' : 'transparent',
            }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: f.color }} />
                <span style={{ fontSize: 13, color: activeFilter === f.id ? '#fff' : '#C7C7CC' }}>{f.label}</span>
              </span>
              <span style={{ fontSize: 11, color: '#636366' }}>{domainCounts[f.id] || 0}</span>
            </button>
          ))}

          <div style={styles.statCard}>
            <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 6 }}>活跃度过滤</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#34C759' }}>
              {activeCount} <span style={{ fontSize: 12, fontWeight: 400, color: '#8E8E93' }}>/ {allConversations.length} 活跃</span>
            </div>
            <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>30天零发言自动归档</div>
          </div>

          <div style={styles.statCard}>
            <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 6 }}>分类状态</div>
            <div style={{ fontSize: 12, color: '#C7C7CC' }}>
              自动: {allConversations.filter((c) => c.classificationStatus === 'auto').length}
            </div>
            <div style={{ fontSize: 12, color: '#FF9500' }}>
              待复核: {allConversations.filter((c) => c.classificationStatus === 'needs_review').length}
            </div>
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
                  placeholder="搜索消息内容（已排除归档会话）..."
                  style={styles.searchInput}
                />
                <button onClick={handleSearch} style={styles.primaryBtn}>搜索</button>
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
                <div style={{ color: '#8E8E93', fontSize: 13 }}>无结果</div>
              )}
            </>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>
                  {DOMAIN_FILTERS.find((f) => f.id === activeFilter)?.label}
                </h2>
                <span style={{ fontSize: 12, color: '#8E8E93' }}>{filteredConversations.length} 个会话</span>
              </div>

              {loading ? (
                <div style={{ color: '#8E8E93', fontSize: 13 }}>加载中...</div>
              ) : filteredConversations.length === 0 ? (
                <div style={{ color: '#8E8E93', fontSize: 13 }}>暂无会话</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {filteredConversations.map((conv) => (
                    <div key={conv.id} onClick={() => { setActiveConv(conv); setRightTab('detail') }} style={{
                      ...styles.convItem,
                      background: activeConv?.id === conv.id ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)',
                      borderColor: activeConv?.id === conv.id ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.05)',
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div>
                          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>{conv.title || conv.id}</div>
                          <div style={{ fontSize: 12, color: '#8E8E93' }}>
                            {conv.kind === 'group' ? '群聊' : conv.kind === 'direct' ? '私聊' : '公众号'} · {conv.messageCount} 条 · {formatTime(conv.lastMessageAt)}
                            {conv.classificationStatus === 'needs_review' && <span style={{ color: '#FF9500', marginLeft: 6 }}>· 待分类</span>}
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

        {/* Right sidebar - tabbed */}
        <aside style={styles.rightSidebar}>
          {/* Tab bar */}
          <div style={styles.tabBar}>
            <button onClick={() => setRightTab('detail')} style={{
              ...styles.tabBtn,
              color: rightTab === 'detail' ? '#fff' : '#8E8E93',
              borderBottom: rightTab === 'detail' ? '2px solid #4A9EFF' : '2px solid transparent',
            }}>详情</button>
            <button onClick={() => setRightTab('webui')} style={{
              ...styles.tabBtn,
              color: rightTab === 'webui' ? '#fff' : '#8E8E93',
              borderBottom: rightTab === 'webui' ? '2px solid #4A9EFF' : '2px solid transparent',
            }}>WebUI</button>
          </div>

          {rightTab === 'detail' ? (
            <div style={{ padding: '0 4px', overflowY: 'auto', flex: 1 }}>
              {activeConv ? (
                <>
                  <div style={{ marginBottom: 16 }}>
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
                      <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>领域分类</div>
                      <div style={{ fontSize: 14, fontWeight: 500, color: '#4A9EFF' }}>
                        {activeConv.domain || '未分类'}
                        {activeConv.projectRole && <span style={{ fontSize: 11, color: '#8E8E93', marginLeft: 6 }}>({activeConv.projectRole})</span>}
                      </div>
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

                  {activeConv.projectId && (
                    <div style={{ marginTop: 14, padding: 12, borderRadius: 10, background: 'rgba(124,110,246,0.1)', border: '1px solid rgba(124,110,246,0.2)' }}>
                      <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>关联项目</div>
                      <div style={{ fontSize: 13, color: '#7C6EF6' }}>{activeConv.projectId} → ArchOS</div>
                    </div>
                  )}
                </>
              ) : (
                <div style={{ color: '#636366', fontSize: 13 }}>选择一个会话查看详情</div>
              )}
            </div>
          ) : (
            /* WebUI tab */
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
              {/* URL bar */}
              <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                <input
                  value={webuiInput}
                  onChange={(e) => setWebuiInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && loadWebui(webuiInput)}
                  placeholder="输入 URL..."
                  style={styles.urlInput}
                />
                <button onClick={() => loadWebui(webuiInput)} style={styles.goBtn}>Go</button>
                <button onClick={() => iframeRef.current?.contentWindow?.location.reload()} style={styles.goBtn}>↻</button>
              </div>

              {/* Presets */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
                {WEBUI_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => loadWebui(p.url)}
                    style={{
                      ...styles.presetBtn,
                      background: webuiUrl === p.url ? 'rgba(74,158,255,0.15)' : 'rgba(255,255,255,0.05)',
                      borderColor: webuiUrl === p.url ? 'rgba(74,158,255,0.3)' : 'rgba(255,255,255,0.08)',
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* Iframe */}
              <div style={{ flex: 1, borderRadius: 10, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)', background: '#fff', position: 'relative' }}>
                {webuiLoading && (
                  <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 10, fontSize: 11, color: '#4A9EFF', background: 'rgba(0,0,0,0.6)', padding: '2px 8px', borderRadius: 4 }}>
                    加载中...
                  </div>
                )}
                <iframe
                  ref={iframeRef}
                  src={webuiUrl}
                  title="ChatOS WebUI"
                  style={{ width: '100%', height: '100%', border: 'none' }}
                  onLoad={() => setWebuiLoading(false)}
                  sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
                />
              </div>
              <div style={{ fontSize: 10, color: '#636366', marginTop: 6, textAlign: 'center' }}>
                {webuiUrl}
              </div>
            </div>
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
  navBtn: { padding: '6px 14px', borderRadius: 8, border: 'none', fontSize: 13, cursor: 'pointer', transition: 'all 150ms' },
  sidebar: {
    width: 220, background: 'rgba(15,15,17,0.6)', backdropFilter: 'blur(20px)',
    borderRight: '1px solid rgba(255,255,255,0.06)', padding: '16px 10px', overflowY: 'auto',
  },
  sidebarLabel: { fontSize: 11, color: '#8E8E93', padding: '0 10px 8px', letterSpacing: 0.5 },
  catBtn: {
    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '9px 10px', borderRadius: 10, border: 'none', cursor: 'pointer',
    marginBottom: 2, transition: 'all 150ms',
  },
  statCard: {
    marginTop: 14, padding: 12, borderRadius: 12,
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)',
  },
  main: { flex: 1, background: 'rgba(10,10,12,0.4)', overflowY: 'auto', padding: 20 },
  convItem: {
    padding: '14px 16px', borderRadius: 14, cursor: 'pointer',
    border: '1px solid rgba(255,255,255,0.05)', transition: 'all 150ms',
  },
  searchInput: {
    flex: 1, padding: '10px 14px', borderRadius: 10,
    background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
    color: '#fff', fontSize: 14, outline: 'none',
  },
  primaryBtn: {
    padding: '10px 20px', borderRadius: 10, border: 'none',
    background: '#4A9EFF', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
  },
  rightSidebar: {
    width: 340, background: 'rgba(15,15,17,0.6)', backdropFilter: 'blur(20px)',
    borderLeft: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column',
  },
  tabBar: {
    display: 'flex', borderBottom: '1px solid rgba(255,255,255,0.08)',
    padding: '0 12px', flexShrink: 0,
  },
  tabBtn: {
    padding: '12px 16px', background: 'none', border: 'none',
    fontSize: 13, cursor: 'pointer', transition: 'all 150ms',
  },
  detailCard: { padding: 12, borderRadius: 10, background: 'rgba(255,255,255,0.04)' },
  urlInput: {
    flex: 1, padding: '7px 10px', borderRadius: 8,
    background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
    color: '#fff', fontSize: 12, outline: 'none',
  },
  goBtn: {
    padding: '7px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.1)',
    background: 'rgba(255,255,255,0.06)', color: '#C7C7CC', fontSize: 12, cursor: 'pointer',
  },
  presetBtn: {
    padding: '4px 10px', borderRadius: 6, border: '1px solid',
    fontSize: 11, cursor: 'pointer', color: '#C7C7CC', transition: 'all 150ms',
  },
  dock: {
    height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '0 20px', background: 'rgba(20,20,22,0.8)', backdropFilter: 'blur(20px)',
    borderTop: '1px solid rgba(255,255,255,0.08)', fontSize: 12, color: '#8E8E93',
  },
}
