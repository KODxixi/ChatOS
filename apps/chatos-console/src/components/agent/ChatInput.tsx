import { useRef, useState, useEffect } from 'react'

interface Props {
  onSend: (text: string) => void
  onInterrupt: () => void
  isRunning: boolean
  disabled?: boolean
}

const MAX_CHARS = 32000

export default function ChatInput({ onSend, onInterrupt, isRunning, disabled }: Props) {
  const [text, setText] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
  }, [text])

  const submit = () => {
    const t = text.trim()
    if (!t || isRunning || disabled) return
    onSend(t)
    setText('')
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div style={{ padding: '12px 16px', borderTop: '1px solid var(--demo-border, rgba(255,255,255,0.08))', background: 'var(--demo-panel, rgba(20,20,24,0.6))' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, MAX_CHARS))}
          onKeyDown={onKey}
          placeholder={disabled ? 'Agent Runtime 未连接…' : '输入消息，Enter 发送，Shift+Enter 换行'}
          disabled={disabled}
          rows={1}
          style={{
            flex: 1,
            resize: 'none',
            background: 'var(--demo-bg, #0a0a0c)',
            border: '1px solid var(--demo-border, rgba(255,255,255,0.1))',
            borderRadius: 10,
            padding: '10px 14px',
            color: 'var(--demo-ink, #fff)',
            fontSize: 13,
            lineHeight: 1.5,
            outline: 'none',
            fontFamily: 'inherit',
            maxHeight: 160,
          }}
        />
        {isRunning ? (
          <button
            className="button button--small"
            onClick={onInterrupt}
            style={{ background: '#c0392b', color: '#fff', border: 'none', minWidth: 64 }}
          >
            停止
          </button>
        ) : (
          <button
            className="button button--small button--primary"
            onClick={submit}
            disabled={!text.trim() || disabled}
            style={{ minWidth: 64, opacity: !text.trim() || disabled ? 0.4 : 1 }}
          >
            发送
          </button>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 10, color: 'var(--quaternary, #666)' }}>
        <span>{isRunning ? '推理中…' : '本地模型 qwen3-vl-8b'}</span>
        <span>{text.length}/{MAX_CHARS}</span>
      </div>
    </div>
  )
}
