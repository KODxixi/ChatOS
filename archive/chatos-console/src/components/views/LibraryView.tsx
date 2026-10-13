import React, { useMemo, useState } from 'react';
import {
  Database,
  Search,
  RefreshCw,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import type { KnowledgeItem } from '../../types';

type TypeFilter = 'all' | 'image' | 'file' | 'audio' | 'video' | 'link';

const FILE_TYPES = new Set<KnowledgeItem['fileType']>(['pdf', 'doc', 'code', 'archive']);

const TYPE_GROUPS: Array<{ key: TypeFilter; label: string }> = [
  { key: 'image', label: '图片' },
  { key: 'file', label: '文件' },
  { key: 'audio', label: '语音' },
  { key: 'video', label: '视频' },
  { key: 'link', label: '链接' },
];

const matchesType = (item: KnowledgeItem, filter: TypeFilter) =>
  filter === 'all'
  || item.fileType === filter
  || (filter === 'file' && FILE_TYPES.has(item.fileType));

export const LibraryView: React.FC = () => {
  const {
    knowledgeItems,
    projects,
    knowledgeDataStatus,
    knowledgeIndexStatus,
    runKnowledgeIndex,
  } = useChatOS();
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending'>('all');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [query, setQuery] = useState('');

  const filteredItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return knowledgeItems.filter(item => {
      if (statusFilter === 'pending' && item.intakeStatus === 'stored') return false;
      if (!matchesType(item, typeFilter)) return false;
      if (!normalized) return true;
      return [item.title, item.fileName, item.sourceConversationName]
        .some(value => value.toLocaleLowerCase().includes(normalized));
    });
  }, [knowledgeItems, query, statusFilter, typeFilter]);

  const pendingCount = knowledgeItems.filter(item => item.intakeStatus !== 'stored').length;
  const typeStats = useMemo(
    () => TYPE_GROUPS.map(group => ({
      ...group,
      count: filteredItems.filter(item => matchesType(item, group.key)).length,
    })),
    [filteredItems],
  );
  const conversationStats = useMemo(() => {
    const counts = new Map<string, { name: string; count: number }>();
    filteredItems.forEach(item => {
      const current = counts.get(item.sourceConversationId);
      counts.set(item.sourceConversationId, {
        name: item.sourceConversationName,
        count: (current?.count || 0) + 1,
      });
    });
    return [...counts.entries()]
      .map(([id, value]) => ({ id, ...value }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN'))
      .slice(0, 8);
  }, [filteredItems]);
  const projectStats = useMemo(() => {
    const assignedConversationIds = new Set(projects.flatMap(project => project.linkedConversationIds));
    const rows = projects
      .map(project => ({
        id: project.id,
        name: project.name,
        count: filteredItems.filter(item => project.linkedConversationIds.includes(item.sourceConversationId)).length,
      }))
      .filter(row => row.count > 0)
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN'));
    const unassignedCount = filteredItems.filter(item => !assignedConversationIds.has(item.sourceConversationId)).length;
    if (unassignedCount > 0) rows.push({ id: 'unassigned', name: '未关联项目', count: unassignedCount });
    return rows.slice(0, 8);
  }, [filteredItems, projects]);
  const stats = knowledgeIndexStatus.stats || {};
  const indexSummary = knowledgeIndexStatus.status === 'running'
    ? (knowledgeIndexStatus.stage === 'archive' ? '正在整理媒体与证据清单' : 'local-model 正在生成本地向量')
    : knowledgeIndexStatus.status === 'completed'
      ? `已索引 ${stats.files_discovered ?? stats.files_indexed ?? stats.documentsWritten ?? 0} 个文档 · ${stats.total_chunks_in_store ?? stats.chunks_indexed ?? 0} 个片段`
      : knowledgeIndexStatus.status === 'failed'
        ? `索引失败：${knowledgeIndexStatus.error || '未知错误'}`
        : knowledgeIndexStatus.status === 'interrupted'
          ? '上次索引被中断，可安全重新运行'
          : '尚未建立 ChatOS 本地向量索引';

  return (
    <div className="flex-1 h-full overflow-y-auto p-6 lg:p-8">
      <section className="gary-glass gary-regular rounded-[var(--gary-radius-control)] p-5" aria-labelledby="knowledge-title">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-slate-100">
              <Database className="w-4 h-4 text-indigo-300" />
              <h2 id="knowledge-title" className="text-base font-semibold">ChatOS 本地资料库</h2>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-slate-400">微信原始数据保持只读；此处仅展示可追溯、可重建的文件与媒体投影。</p>
            <p className={`mt-2 text-[11px] ${knowledgeIndexStatus.status === 'failed' ? 'text-rose-300' : 'text-slate-500'}`}>{indexSummary}</p>
          </div>
          <button
            type="button"
            onClick={() => void runKnowledgeIndex()}
            disabled={knowledgeIndexStatus.status === 'running'}
            className="min-h-11 px-3 inline-flex items-center justify-center gap-2 border border-white/15 rounded-[var(--gary-radius-control)] text-xs text-slate-200 hover:text-white hover:border-white/30 disabled:opacity-45 disabled:cursor-not-allowed"
          >
            <RefreshCw className={`w-4 h-4 ${knowledgeIndexStatus.status === 'running' ? 'animate-spin' : ''}`} />
            {knowledgeIndexStatus.status === 'running' ? '整理中' : '整理并索引'}
          </button>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
          <span><strong className="text-slate-100 font-semibold">{knowledgeItems.length}</strong> 条资料记录</span>
          <span><strong className="text-amber-300 font-semibold">{pendingCount}</strong> 条待解析</span>
          <span><strong className="text-slate-100 font-semibold">{stats.assetsArchived ?? 0}</strong> 个归档对象</span>
        </div>

        <div className="mt-5 grid grid-cols-1 lg:grid-cols-[minmax(260px,1fr)_auto_auto] gap-3">
          <label className="relative min-w-0">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-3.5 pointer-events-none" />
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="搜索文件、会话或摘要"
              className="w-full min-h-11 pl-9 pr-3 bg-transparent border border-white/10 rounded-[var(--gary-radius-control)] text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-400/60"
            />
          </label>
          <select value={typeFilter} onChange={event => setTypeFilter(event.target.value as TypeFilter)} className="min-h-11 px-3 bg-transparent border border-white/10 rounded-[var(--gary-radius-control)] text-xs text-slate-300">
            <option value="all">全部类型</option>
            <option value="image">图片</option>
            <option value="file">文件</option>
            <option value="audio">语音</option>
            <option value="video">视频</option>
            <option value="link">链接</option>
          </select>
          <div className="chatos-view-tabbar" role="tablist" aria-label="资料视图">
            <button id="lib-status-all" type="button" role="tab" aria-selected={statusFilter === 'all'} onClick={() => setStatusFilter('all')} className={`chatos-view-tab${statusFilter === 'all' ? ' is-active' : ''}`}>全部</button>
            <button id="lib-status-pending" type="button" role="tab" aria-selected={statusFilter === 'pending'} onClick={() => setStatusFilter('pending')} className={`chatos-view-tab${statusFilter === 'pending' ? ' is-active' : ''}`}>待处理</button>
          </div>
        </div>
      </section>

      <section className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3" aria-label="本地资料库统计">
        <div className="min-w-0 border-b border-white/10 pb-4 xl:border-b-0 xl:border-r xl:pr-6">
          <div className="flex items-center justify-between border-b border-white/10 pb-3">
            <h3 className="text-sm font-medium text-slate-200">按类型</h3>
            <span className="text-[11px] text-slate-500">{filteredItems.length} 条</span>
          </div>
          <div className="mt-2" role="list" aria-label="按类型统计">
            {typeStats.map(row => (
              <div key={row.key} role="listitem" className="flex min-h-10 items-center justify-between border-b border-white/5 text-xs">
                <span className="text-slate-400">{row.label}</span>
                <span className="font-medium text-slate-200">{row.count}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="min-w-0 border-b border-white/10 pb-4 xl:border-b-0 xl:border-r xl:pr-6">
          <div className="flex items-center justify-between border-b border-white/10 pb-3">
            <h3 className="text-sm font-medium text-slate-200">按会话</h3>
            <span className="text-[11px] text-slate-500">共 {new Set(filteredItems.map(item => item.sourceConversationId)).size} 个</span>
          </div>
          <div className="mt-2" role="list" aria-label="按会话统计">
            {conversationStats.length > 0 ? conversationStats.map(row => (
              <div key={row.id} role="listitem" className="flex min-h-10 items-center justify-between gap-4 border-b border-white/5 text-xs">
                <span className="truncate text-slate-400" title={row.name}>{row.name}</span>
                <span className="shrink-0 font-medium text-slate-200">{row.count}</span>
              </div>
            )) : <p className="py-5 text-xs text-slate-500">暂无匹配会话</p>}
          </div>
        </div>

        <div className="min-w-0 pb-4">
          <div className="flex items-center justify-between border-b border-white/10 pb-3">
            <h3 className="text-sm font-medium text-slate-200">按项目</h3>
            <span className="text-[11px] text-slate-500">统计关联资料</span>
          </div>
          <div className="mt-2" role="list" aria-label="按项目统计">
            {projectStats.length > 0 ? projectStats.map(row => (
              <div key={row.id} role="listitem" className="flex min-h-10 items-center justify-between gap-4 border-b border-white/5 text-xs">
                <span className="truncate text-slate-400" title={row.name}>{row.name}</span>
                <span className="shrink-0 font-medium text-slate-200">{row.count}</span>
              </div>
            )) : <p className="py-5 text-xs text-slate-500">暂无项目关联</p>}
          </div>
        </div>
      </section>

      {filteredItems.length === 0 && (
        <div className="py-10 text-center text-sm text-slate-500">{
          knowledgeDataStatus === 'loading'
            ? '正在读取本地资料记录…'
            : knowledgeDataStatus === 'unavailable'
              ? '本地资料接口暂不可用，没有使用演示数据替代'
              : knowledgeItems.length
                ? '没有符合当前筛选的资料'
                : '尚无可展示的真实资料记录'
        }</div>
      )}
    </div>
  );
};
