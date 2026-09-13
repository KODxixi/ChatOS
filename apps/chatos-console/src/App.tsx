import { useState } from 'react'

type ViewMode = 'conversations' | 'projects' | 'search' | 'audit'

const CATEGORIES = [
  { id: 'project', label: '项目相关', count: 12, color: '#4A9EFF' },
  { id: 'work', label: '工作协作', count: 23, color: '#7C6EF6' },
  { id: 'personal', label: '个人社交', count: 45, color: '#34C759' },
  { id: 'service', label: '服务消费', count: 89, color: '#FF9500' },
  { id: 'subscription', label: '资讯订阅', count: 15, color: '#AF52DE' },
  { id: 'archived', label: '已归档', count: 437, color: '#8E8E93' },
]

const RECENT_CONVERSATIONS = [
  { id: '1', title: 'HYP海棠湾电竞主题地产项目', last: '2分钟前', unread: 3, domain: 'project' },
  { id: '2', title: '灵犀AI小组', last: '15分钟前', unread: 0, domain: 'work' },
  { id: '3', title: 'A算力+通信', last: '1小时前', unread: 1, domain: 'work' },
  { id: '4', title: '你在午睡，我在举铁', last: '3小时前', unread: 0, domain: 'personal' },
  { id: '5', title: '中山圣湘生物园区', last: '昨天', unread: 0, domain: 'project' },
]

export default function App() {
  const [activeMode, setActiveMode] = useState<ViewMode>('conversations')
  const [activeCategory, setActiveCategory] = useState('project')
  const [activeConversation, setActiveConversation] = useState('1')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#000' }}>
      {/* WebBar */}
      <header
        style={{
          height: 52,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 20px',
          background: 'rgba(20,20,22,0.72)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          zIndex: 100,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: 'linear-gradient(135deg, #4A9EFF, #7C6EF6)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 14,
              fontWeight: 700,
            }}
          >
            C
          </div>
          <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: 0.3 }}>ChatOS</span>
          <span style={{ fontSize: 11, color: '#8E8E93', marginLeft: 4 }}>会话聊天与项目管理</span>
        </div>

        <nav style={{ display: 'flex', gap: 4 }}>
          {(['conversations', 'projects', 'search', 'audit'] as ViewMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => setActiveMode(mode)}
              style={{
                padding: '6px 14px',
                borderRadius: 8,
                border: 'none',
                background: activeMode === mode ? 'rgba(255,255,255,0.12)' : 'transparent',
                color: activeMode === mode ? '#fff' : '#8E8E93',
                fontSize: 13,
                cursor: 'pointer',
                transition: 'all 150ms',
              }}
            >
              {mode === 'conversations' ? '会话' : mode === 'projects' ? '项目' : mode === 'search' ? '搜索' : '审计'}
            </button>
          ))}
        </nav>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: '#34C759',
              boxShadow: '0 0 8px #34C759',
            }}
          />
          <span style={{ fontSize: 12, color: '#8E8E93' }}>网关在线</span>
        </div>
      </header>

      {/* Main area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar - categories */}
        <aside
          style={{
            width: 220,
            background: 'rgba(15,15,17,0.6)',
            backdropFilter: 'blur(20px)',
            borderRight: '1px solid rgba(255,255,255,0.06)',
            padding: '16px 10px',
            overflowY: 'auto',
          }}
        >
          <div style={{ fontSize: 11, color: '#8E8E93', padding: '0 10px 8px', letterSpacing: 0.5 }}>
            分类目录
          </div>
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '9px 10px',
                borderRadius: 10,
                border: 'none',
                background: activeCategory === cat.id ? 'rgba(255,255,255,0.08)' : 'transparent',
                cursor: 'pointer',
                marginBottom: 2,
                transition: 'all 150ms',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: cat.color,
                  }}
                />
                <span style={{ fontSize: 13, color: activeCategory === cat.id ? '#fff' : '#C7C7CC' }}>
                  {cat.label}
                </span>
              </span>
              <span style={{ fontSize: 11, color: '#636366' }}>{cat.count}</span>
            </button>
          ))}

          <div
            style={{
              marginTop: 20,
              padding: '12px',
              borderRadius: 12,
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid rgba(255,255,255,0.06)',
            }}
          >
            <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 6 }}>活跃度过滤</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#34C759' }}>85 <span style={{ fontSize: 12, fontWeight: 400, color: '#8E8E93' }}>/ 526 活跃</span></div>
            <div style={{ fontSize: 11, color: '#636366', marginTop: 4 }}>30天零发言自动归档</div>
          </div>
        </aside>

        {/* Center - conversation list */}
        <main
          style={{
            flex: 1,
            background: 'rgba(10,10,12,0.4)',
            overflowY: 'auto',
            padding: 20,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h2 style={{ fontSize: 20, fontWeight: 600 }}>
              {CATEGORIES.find((c) => c.id === activeCategory)?.label}
            </h2>
            <span style={{ fontSize: 12, color: '#8E8E93' }}>{RECENT_CONVERSATIONS.length} 个会话</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {RECENT_CONVERSATIONS.filter((c) => c.domain === activeCategory || activeCategory === 'archived').map((conv) => (
              <div
                key={conv.id}
                onClick={() => setActiveConversation(conv.id)}
                style={{
                  padding: '14px 16px',
                  borderRadius: 14,
                  background: activeConversation === conv.id ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)',
                  border: `1px solid ${activeConversation === conv.id ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.05)'}`,
                  cursor: 'pointer',
                  transition: 'all 150ms',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <div>
                  <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>{conv.title}</div>
                  <div style={{ fontSize: 12, color: '#8E8E93' }}>最后活跃: {conv.last}</div>
                </div>
                {conv.unread > 0 && (
                  <div
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: '50%',
                      background: '#4A9EFF',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 11,
                      fontWeight: 600,
                    }}
                  >
                    {conv.unread}
                  </div>
                )}
              </div>
            ))}
          </div>
        </main>

        {/* Right sidebar - details */}
        <aside
          style={{
            width: 280,
            background: 'rgba(15,15,17,0.6)',
            backdropFilter: 'blur(20px)',
            borderLeft: '1px solid rgba(255,255,255,0.06)',
            padding: 20,
            overflowY: 'auto',
          }}
        >
          <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 12, letterSpacing: 0.5 }}>会话详情</div>

          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>HYP海棠湾电竞主题地产项目</div>
            <div style={{ fontSize: 12, color: '#8E8E93' }}>群聊 · 项目相关 · 活跃</div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ padding: '12px', borderRadius: 10, background: 'rgba(255,255,255,0.04)' }}>
              <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>消息总数</div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>2,367</div>
            </div>
            <div style={{ padding: '12px', borderRadius: 10, background: 'rgba(255,255,255,0.04)' }}>
              <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>我的发言（30天）</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#34C759' }}>47</div>
            </div>
            <div style={{ padding: '12px', borderRadius: 10, background: 'rgba(255,255,255,0.04)' }}>
              <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 4 }}>关联项目</div>
              <div style={{ fontSize: 13, color: '#4A9EFF' }}>海棠湾电竞主题地产 → ArchOS</div>
            </div>
          </div>

          <div style={{ marginTop: 20 }}>
            <div style={{ fontSize: 11, color: '#8E8E93', marginBottom: 8 }}>项目摘要（Agent 提取）</div>
            <div
              style={{
                padding: 12,
                borderRadius: 10,
                background: 'rgba(74,158,255,0.08)',
                border: '1px solid rgba(74,158,255,0.15)',
                fontSize: 12,
                lineHeight: 1.6,
                color: '#C7C7CC',
              }}
            >
              电竞主题地产项目，位于三亚海棠湾。当前处于概念方案阶段，甲方关注电竞业态与商业动线结合。下次报审时间：9月20日。
            </div>
          </div>
        </aside>
      </div>

      {/* Bottom dock */}
      <footer
        style={{
          height: 44,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 20px',
          background: 'rgba(20,20,22,0.8)',
          backdropFilter: 'blur(20px)',
          borderTop: '1px solid rgba(255,255,255,0.08)',
          fontSize: 12,
          color: '#8E8E93',
        }}
      >
        <span>ChatOS v0.1.0 · 与 ArchOS 并列</span>
        <span>会话库: D:\ChatOS-Vault · 分类体系 v1</span>
      </footer>
    </div>
  )
}
