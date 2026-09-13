import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { getHealth, getConversationPreviews, searchMessages, getProjects, classifyConversations, extractProjects } from './lib/api'
import type { ConversationPreview, HealthResponse, SearchItem, Project } from './types'

type ViewMode = 'conversations' | 'projects' | 'search' | 'audit' | 'kanban'
type DomainFilter = 'all' | 'project' | 'work' | 'personal' | 'service' | 'subscription' | 'archived'
type RightTab = 'detail' | 'webui'
type KanbanColumn = 'draft' | 'todo' | 'in_progress' | 'done'

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

const KANBAN_COLUMNS: { id: KanbanColumn; label: string; color: string }[] = [
  { id: 'draft', label: '草稿箱', color: '#8E8E93' },
  { id: 'todo', label: '待处理', color: '#FF9500' },
  { id: 'in_progress', label: '进行中', color: '#4A9EFF' },
  { id: 'done', label: '已完成', color: '#34C759' },
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

function extractProjectName(title: string): string {
  let name = title
  name = name.replace(/群$|项目群$|沟通群$|工作群$|讨论群$|交流群$/g, '')
  name = name.replace(/^[\d（(]+[^）)]*[）)]?\s*/g, '')
  name = name.replace(/HYP[-_]?/gi, '')
  return name.trim() || title
}

export default function App() {
  const [activeMode, setActiveMode] = useState<ViewMode>('conversations')
  const [activeFilter, setActiveFilter] = useState<DomainFilter>('all')
  const [previews, setPreviews] = useState<ConversationPreview[]>([])
  const [activeConv, setActiveConv] = useState<ConversationPreview | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchItem[]>([])
  const [searching, setSearching] = useState(false)

  const [projects, setProjects] = useState<Project[]>([])
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [auditAction, setAuditAction] = useState('')

  const [rightTab, setRightTab] = useState<RightTab>('detail')
  const [webuiUrl, setWebuiUrl] = useState('http://127.0.0.1:8912/docs')
  const [webuiInput, setWebuiInput] = useState('http://127.0.0.1:8912/docs')
  const [webuiLoading, setWebuiLoading] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  const loadPreviews = useCallback(async () => {
    setLoading(true)
    try {
      const page = await getConversationPreviews(150, true)
      setPreviews(page.items)
      if (page.items.length > 0 && !activeConv) {
        setActiveConv(page.items[0])
      }
    } catch (e) {
      console.error('Failed to load previews', e)
    } finally {
      setLoading(false)
    }
  }, [activeConv])

  useEffect(() => {
    getHealth().then(setHealth).catch(console.error)
  }, [])

  useEffect(() => {
    loadPreviews()
  }, [loadPreviews])

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true)
    try {
      const page = await getProjects(20)
      setProjects(page.items)
    } catch (e) {
      console.error('Failed to load projects', e)
    } finally {
      setProjectsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (activeMode === 'projects' && projects.length === 0) {
      loadProjects()
    }
  }, [activeMode, projects.length, loadProjects])

  const filteredPreviews = useMemo(() => {
    if (activeFilter === 'all') return previews
    if (activeFilter === 'archived') return previews.filter((c) => !c.isIndexingEnabled)
    return previews.filter((c) => c.isIndexingEnabled && c.domain === activeFilter)
  }, [previews, activeFilter])

  // Group project conversations by project name
  const groupedConversations = useMemo(() => {
    if (activeFilter !== 'project') return null
    const groups: Record<string, ConversationPreview[]> = {}
    filteredPreviews.forEach((c) => {
      const pname = extractProjectName(c.title)
      if (!groups[pname]) groups[pname] = []
      groups[pname].push(c)
    })
    return Object.entries(groups).sort((a, b) => b[1].length - a[1].length)
  }, [filteredPreviews, activeFilter])

  const domainCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    DOMAIN_FILTERS.forEach((f) => {
      if (f.id === 'all') counts[f.id] = previews.filter((c) => c.isIndexingEnabled).length
      else if (f.id === 'archived') counts[f.id] = previews.filter((c) => !c.isIndexingEnabled).length
      else counts[f.id] = previews.filter((c) => c.isIndexingEnabled && c.domain === f.id).length
    })
    return counts
  }, [previews])

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

  const runClassify = async () => {
    setAuditAction('分类中...')
    try {
      await classifyConversations(false)
      await loadPreviews()
      setAuditAction('分类完成')
    } catch { setAuditAction('分类失败') }
    setTimeout(() => setAuditAction(''), 3000)
  }

  const runExtract = async () => {
    setAuditAction('提取项目中...')
    try {
      await extractProjects(20)
      await loadProjects()
      setAuditAction('项目提取完成')
    } catch { setAuditAction('提取失败') }
    setTimeout(() => setAuditAction(''), 3000)
  }

  const loadWebui = (url: string) => {
    setWebuiUrl(url)
    setWebuiInput(url)
    setWebuiLoading(true)
  }

  const online = health?.app === 'ready'
  const activeCount = previews.filter((c) => c.isIndexingEnabled).length

  const renderCard = (conv: ConversationPreview) => (
    <div
      key={conv.id}
      onClick={() => { setActiveConv(conv); setRightTab('detail') }}
      style={{
        ...styles.convCard,
        borderColor: activeConv?.id === conv.id ? 'rgba(74,158,255,0.4)' : 'rgba(255,255,255,0.06)',
        background: activeConv?.id === conv.id ? 'rgba(74,158,255,0.08)' : 'rgba(255,255,255,0.03)',
      }}
    >
      {/* Top row: title + kind badge + time */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            fontSize: 10, padding: '2px 6px', borderRadius: 4, fontWeight: 600,
            background: conv.kind === 'group' ? 'rgba(124,110,246,0.15)' : 'rgba(52,199,89,0.15)',
            color: conv.kind === 'group' ? '#7C6EF6' : '#34C759',
          }}>
            {conv.kind === 'group' ? '群' : conv.kind === 'direct' ? '私' : '订'}
          </span>
          <span style={{ fontSize: 12, color: '#8E8E93', fontWeight: 500 }}>{conv.title}</span>
        </div>
        <span style={{ fontSize: 10, color: '#636366' }}>{formatTime(conv.latestSentAt || conv.lastMessageAt)}</span>
      </div>

      {/* Message preview - prominent */}
      <div style={{
        fontSize: 14, color: '#E5E5EA', lineHeight: 1.5, marginBottom: 8,
        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
        minHeight: 42,
      }}>
        {conv.latestMessage || '（无文字消息）'}
      </div>

      {/* Bottom row: sender + speakers + count */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: '#8E8E93' }}>
          {conv.latestSender && <span style={{ color: '#5AC8FA' }}>{conv.latestSender}</span>}
          {conv.kind === 'group' && conv.topSpeakers.length > 0 && (
            <span style={{ color: '#636366' }}>
              · 主: {conv.topSpeakers.slice(0, 2).map((s) => s.name).join('、')}
            </span>
          )}
        </div>
        <span style={{ fontSize: 10, color: '#636366' }}>{conv.messageCount.toLocaleString()} 条</span>
      </div>
    </div>
  )

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
          {(['conversations', 'kanban', 'projects', 'search', 'audit'] as ViewMode[]).map((mode) => (
            <button key={mode} onClick={() => setActiveMode(mode)} style={{
              ...styles.navBtn,
              background: activeMode === mode ? 'rgba(255,255,255,0.12)' : 'transparent',
              color: activeMode === mode ? '#fff' : '#8E8E93',
            }}>
              {mode === 'conversations' ? '会话' : mode === 'kanban' ? '任务' : mode === 'projects' ? '项目' : mode === 'search' ? '搜索' : '审计'}
            </button>
          ))}
        </nav>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: online ? '#34C759' : '#FF3B30', boxShadow: online ? '0 0 8px #34C759' : '0 0 8px #FF3B30' }} />
          <span style={{ fontSize: 12, color: '#8E8E93' }}>{online ? `${activeCount}/${previews.length} 活跃` : '网关离线'}</span>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar */}
        <aside style={styles.sidebar}>
          <div style={styles.sidebarLabel}>分类目录</div>
          {DOMAIN_FILTERS.map((f) => (
            <button key={f.id} onClick={() => { setActiveFilter(f.id); setActiveMode('conversations') }} style={{
              ...styles.catBtn,
              background: activeFilter === f.id && activeMode === 'conversations' ? 'rgba(255,255,255,0.08)' : 'transparent',
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
              {activeCount} <span style={{ fontSize: 12, fontWeight: 400, color: '#8E8E93' }}>/ {previews.length}</span>
            </div>
            <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>30天零发言自动归档</div>
          </div>
        </aside>

        {/* Center */}
        <main style={styles.main}>
          {activeMode === 'search' ? (
            <>
              <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleSearch()} placeholder="搜索消息内容..." style={styles.searchInput} />
                <button onClick={handleSearch} style={styles.primaryBtn}>搜索</button>
              </div>
              {searching && <div style={{ color: '#8E8E93', fontSize: 13 }}>搜索中...</div>}
              {!searching && searchResults.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {searchResults.map((item) => (
                    <div key={item.id} style={styles.convCard}>
                      <div style={{ fontSize: 12, color: '#4A9EFF', marginBottom: 4 }}>{item.conversationTitle} · {item.senderName}</div>
                      <div style={{ fontSize: 13, color: '#C7C7CC', lineHeight: 1.5 }}>{item.content.slice(0, 200)}</div>
                      <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>{formatTime(item.sentAt)}</div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : activeMode === 'projects' ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>项目总览</h2>
                <div style={{ display: 'flex', gap: 8 }}>
                  <span style={{ fontSize: 12, color: '#8E8E93', alignSelf: 'center' }}>{projects.length} 个项目</span>
                  <button onClick={loadProjects} style={styles.smallBtn}>刷新</button>
                  <button onClick={runExtract} style={styles.smallBtn}>重新提取</button>
                </div>
              </div>
              {projectsLoading ? <div style={{ color: '#8E8E93', fontSize: 13 }}>加载中...</div> : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
                  {projects.map((proj) => (
                    <div key={proj.projectId} style={styles.projectCard}>
                      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>{proj.projectName}</div>
                      <div style={{ display: 'flex', gap: 12, marginBottom: 10 }}>
                        <div><div style={{ fontSize: 18, fontWeight: 700, color: '#4A9EFF' }}>{proj.messageCount.toLocaleString()}</div><div style={{ fontSize: 10, color: '#8E8E93' }}>消息</div></div>
                        <div><div style={{ fontSize: 18, fontWeight: 700, color: '#7C6EF6' }}>{proj.decisions}</div><div style={{ fontSize: 10, color: '#8E8E93' }}>决策</div></div>
                        <div><div style={{ fontSize: 18, fontWeight: 700, color: '#FF9500' }}>{proj.milestones}</div><div style={{ fontSize: 10, color: '#8E8E93' }}>节点</div></div>
                        <div><div style={{ fontSize: 18, fontWeight: 700, color: '#FF3B30' }}>{proj.clientFeedback}</div><div style={{ fontSize: 10, color: '#8E8E93' }}>甲方</div></div>
                      </div>
                      <div style={{ fontSize: 10, color: '#7C6EF6' }}>brief: D:\ChatOS-Vault\conversations\project\{proj.projectId}\</div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : activeMode === 'audit' ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>分类审计</h2>
                <div style={{ display: 'flex', gap: 8 }}>
                  {auditAction && <span style={{ fontSize: 12, color: '#4A9EFF' }}>{auditAction}</span>}
                  <button onClick={runClassify} style={styles.smallBtn}>重新分类</button>
                  <button onClick={runExtract} style={styles.smallBtn}>提取项目</button>
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 20 }}>
                {['project', 'work', 'personal', 'service'].map((d) => {
                  const count = previews.filter((c) => c.domain === d).length
                  const colors: Record<string, string> = { project: '#7C6EF6', work: '#5AC8FA', personal: '#34C759', service: '#FF9500' }
                  const labels: Record<string, string> = { project: '项目', work: '工作', personal: '个人', service: '服务' }
                  return (
                    <div key={d} style={{ ...styles.statCard, textAlign: 'center' }}>
                      <div style={{ fontSize: 24, fontWeight: 700, color: colors[d] }}>{count}</div>
                      <div style={{ fontSize: 11, color: '#8E8E93' }}>{labels[d]}</div>
                    </div>
                  )
                })}
              </div>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: '#FF9500' }}>待复核 ({previews.filter((c) => c.classificationStatus === 'needs_review').length})</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
                {previews.filter((c) => c.classificationStatus === 'needs_review').slice(0, 20).map(renderCard)}
              </div>
            </>
          ) : activeMode === 'kanban' ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>任务看板</h2>
                <span style={{ fontSize: 12, color: '#8E8E93' }}>从会话中提取的待办事项</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, height: 'calc(100% - 60px)' }}>
                {KANBAN_COLUMNS.map((col) => (
                  <div key={col.id} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 4px' }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: col.color }} />
                      <span style={{ fontSize: 13, fontWeight: 600 }}>{col.label}</span>
                      <span style={{ fontSize: 11, color: '#636366' }}>0</span>
                    </div>
                    <div style={{ flex: 1, borderRadius: 12, background: 'rgba(255,255,255,0.02)', border: '1px dashed rgba(255,255,255,0.08)', padding: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <span style={{ fontSize: 12, color: '#48484A' }}>拖拽任务到此处</span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>{DOMAIN_FILTERS.find((f) => f.id === activeFilter)?.label}</h2>
                <span style={{ fontSize: 12, color: '#8E8E93' }}>{filteredPreviews.length} 个会话</span>
              </div>
              {loading ? <div style={{ color: '#8E8E93', fontSize: 13 }}>加载中...</div> : filteredPreviews.length === 0 ? (
                <div style={{ color: '#8E8E93', fontSize: 13 }}>暂无会话</div>
              ) : groupedConversations ? (
                // Project domain: grouped by project name
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {groupedConversations.map(([pname, convs]) => (
                    <div key={pname}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, paddingLeft: 4 }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: '#7C6EF6' }}>{pname}</span>
                        <span style={{ fontSize: 11, color: '#636366' }}>{convs.length} 个会话</span>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 8 }}>
                        {convs.map(renderCard)}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 8 }}>
                  {filteredPreviews.map(renderCard)}
                </div>
              )}
            </>
          )}
        </main>

        {/* Right sidebar */}
        <aside style={styles.rightSidebar}>
          <div style={styles.tabBar}>
            <button onClick={() => setRightTab('detail')} style={{ ...styles.tabBtn, color: rightTab === 'detail' ? '#fff' : '#8E8E93', borderBottom: rightTab === 'detail' ? '2px solid #4A9EFF' : '2px solid transparent' }}>详情</button>
            <button onClick={() => setRightTab('webui')} style={{ ...styles.tabBtn, color: rightTab === 'webui' ? '#fff' : '#8E8E93', borderBottom: rightTab === 'webui' ? '2px solid #4A9EFF' : '2px solid transparent' }}>WebUI</button>
          </div>
          {rightTab === 'detail' ? (
            <div style={{ padding: '0 4px', overflowY: 'auto', flex: 1 }}>
              {activeConv ? (
                <>
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>{activeConv.title}</div>
                    <div style={{ fontSize: 12, color: '#8E8E93' }}>
                      {activeConv.kind === 'group' ? '群聊' : activeConv.kind === 'direct' ? '私聊' : '公众号'} · {activeConv.isIndexingEnabled ? '已索引' : '已归档'}
                    </div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div style={styles.detailCard}><div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>消息总数</div><div style={{ fontSize: 22, fontWeight: 700 }}>{activeConv.messageCount.toLocaleString()}</div></div>
                    <div style={styles.detailCard}><div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>领域分类</div><div style={{ fontSize: 14, fontWeight: 500, color: '#4A9EFF' }}>{activeConv.domain || '未分类'}</div></div>
                    <div style={styles.detailCard}><div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>最后活跃</div><div style={{ fontSize: 14, fontWeight: 500 }}>{formatTime(activeConv.lastMessageAt)}</div></div>
                    {activeConv.topSpeakers.length > 0 && (
                      <div style={styles.detailCard}>
                        <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 6 }}>主要讲话人</div>
                        {activeConv.topSpeakers.map((s, i) => (
                          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '2px 0' }}>
                            <span style={{ color: '#C7C7CC' }}>{s.name}</span>
                            <span style={{ color: '#636366' }}>{s.count}条</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              ) : <div style={{ color: '#636366', fontSize: 13 }}>选择一个会话查看详情</div>}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
              <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                <input value={webuiInput} onChange={(e) => setWebuiInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && loadWebui(webuiInput)} placeholder="输入 URL..." style={styles.urlInput} />
                <button onClick={() => loadWebui(webuiInput)} style={styles.goBtn}>Go</button>
                <button onClick={() => iframeRef.current?.contentWindow?.location.reload()} style={styles.goBtn}>↻</button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
                {WEBUI_PRESETS.map((p) => (
                  <button key={p.label} onClick={() => loadWebui(p.url)} style={{ ...styles.presetBtn, background: webuiUrl === p.url ? 'rgba(74,158,255,0.15)' : 'rgba(255,255,255,0.05)', borderColor: webuiUrl === p.url ? 'rgba(74,158,255,0.3)' : 'rgba(255,255,255,0.08)' }}>{p.label}</button>
                ))}
              </div>
              <div style={{ flex: 1, borderRadius: 10, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)', background: '#fff', position: 'relative' }}>
                {webuiLoading && <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 10, fontSize: 11, color: '#4A9EFF', background: 'rgba(0,0,0,0.6)', padding: '2px 8px', borderRadius: 4 }}>加载中...</div>}
                <iframe ref={iframeRef} src={webuiUrl} title="ChatOS WebUI" style={{ width: '100%', height: '100%', border: 'none' }} onLoad={() => setWebuiLoading(false)} sandbox="allow-same-origin allow-scripts allow-forms allow-popups" />
              </div>
              <div style={{ fontSize: 10, color: '#636366', marginTop: 6, textAlign: 'center' }}>{webuiUrl}</div>
            </div>
          )}
        </aside>
      </div>

      <footer style={styles.dock}>
        <span>ChatOS v0.2.0 · 卡片式会话 · 任务看板 · 与 ArchOS 并列</span>
        <span>会话库: D:\ChatOS-Vault · gary-ui Liquid Glass</span>
      </footer>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  webbar: { height: 52, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px', background: 'rgba(20,20,22,0.72)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)', borderBottom: '1px solid rgba(255,255,255,0.08)', zIndex: 100 },
  logo: { width: 28, height: 28, borderRadius: 8, background: 'linear-gradient(135deg, #4A9EFF, #7C6EF6)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700 },
  navBtn: { padding: '6px 14px', borderRadius: 8, border: 'none', fontSize: 13, cursor: 'pointer', transition: 'all 150ms' },
  sidebar: { width: 200, background: 'rgba(15,15,17,0.6)', backdropFilter: 'blur(20px)', borderRight: '1px solid rgba(255,255,255,0.06)', padding: '16px 10px', overflowY: 'auto' },
  sidebarLabel: { fontSize: 11, color: '#8E8E93', padding: '0 10px 8px', letterSpacing: 0.5 },
  catBtn: { width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 10px', borderRadius: 10, border: 'none', cursor: 'pointer', marginBottom: 2, transition: 'all 150ms' },
  statCard: { marginTop: 14, padding: 12, borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' },
  main: { flex: 1, background: 'rgba(10,10,12,0.4)', overflowY: 'auto', padding: 20 },
  convCard: { padding: '14px 16px', borderRadius: 14, cursor: 'pointer', border: '1px solid', transition: 'all 150ms', background: 'rgba(255,255,255,0.03)' },
  projectCard: { padding: 16, borderRadius: 14, cursor: 'pointer', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', transition: 'all 150ms' },
  searchInput: { flex: 1, padding: '10px 14px', borderRadius: 10, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', fontSize: 14, outline: 'none' },
  primaryBtn: { padding: '10px 20px', borderRadius: 10, border: 'none', background: '#4A9EFF', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  smallBtn: { padding: '5px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.06)', color: '#C7C7CC', fontSize: 12, cursor: 'pointer' },
  rightSidebar: { width: 320, background: 'rgba(15,15,17,0.6)', backdropFilter: 'blur(20px)', borderLeft: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column' },
  tabBar: { display: 'flex', borderBottom: '1px solid rgba(255,255,255,0.08)', padding: '0 12px', flexShrink: 0 },
  tabBtn: { padding: '12px 16px', background: 'none', border: 'none', fontSize: 13, cursor: 'pointer', transition: 'all 150ms' },
  detailCard: { padding: 12, borderRadius: 10, background: 'rgba(255,255,255,0.04)' },
  urlInput: { flex: 1, padding: '7px 10px', borderRadius: 8, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', fontSize: 12, outline: 'none' },
  goBtn: { padding: '7px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.06)', color: '#C7C7CC', fontSize: 12, cursor: 'pointer' },
  presetBtn: { padding: '4px 10px', borderRadius: 6, border: '1px solid', fontSize: 11, cursor: 'pointer', color: '#C7C7CC', transition: 'all 150ms' },
  dock: { height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px', background: 'rgba(20,20,22,0.8)', backdropFilter: 'blur(20px)', borderTop: '1px solid rgba(255,255,255,0.08)', fontSize: 12, color: '#8E8E93' },
}
