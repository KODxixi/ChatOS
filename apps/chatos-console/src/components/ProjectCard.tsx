import type { Project } from '../types'
import { formatTime } from '../utils/format'

interface Props {
  project: Project
  selected: boolean
  onSelect: () => void
}

export default function ProjectCard({ project, selected, onSelect }: Props) {
  return (
    <button className={`ch-card ch-card--optical ${selected ? 'is-selected' : ''}`} onClick={onSelect}>
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {project.projectName}
      </div>
      <div style={{ fontSize: 13, color: 'var(--secondary)', marginTop: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {project.conversationCount} 个会话 · {project.messageCount.toLocaleString()} 条消息
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 14, fontSize: 12, color: 'var(--tertiary)' }}>
        <span>决策 {project.decisions}</span>
        <span>里程碑 {project.milestones}</span>
        <span>更新 {formatTime(project.lastUpdated)}</span>
      </div>
    </button>
  )
}
