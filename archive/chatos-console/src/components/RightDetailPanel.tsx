import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  FileText,
  Image,
  Link2,
  Mic,
  Sparkles,
  Star,
  Users,
  Video,
  X,
} from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';
import { getConversationRelationshipHalo } from '../core/conversationFeatureFilters';

interface ConversationInsight {
  summary?: string;
  importantMessages?: { messageId: string | number; senderName: string; sentAt: number; content: string }[];
  keyPeople?: { name: string; count: number }[];
  keyTopics?: { term: string; count: number }[];
  keyDates?: string[];
  files?: { count: number; recent?: { fileName: string; fileSize?: number | null }[] };
  links?: { count: number };
  mediaCount?: Record<string, number>;
}

interface MediaRecord {
  id: string | number;
  type: string;
  content?: string;
  fileName?: string | null;
  fileSize?: number | null;
}

const MEDIA_META: Record<string, { label: string; Icon: React.FC<{ className?: string }> }> = {
  image: { label: '图片', Icon: Image },
  voice: { label: '语音', Icon: Mic },
  video: { label: '视频', Icon: Video },
  attachment: { label: '文件', Icon: FileText },
};

const formatBytes = (value?: number | null) => {
  if (!value) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
};

const formatEvidenceTime = (timestamp: number) => new Date(timestamp).toLocaleString('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export const RightDetailPanel: React.FC = () => {
  const {
    isInspectorOpen,
    setIsInspectorOpen,
    selectedConversation,
    toggleFollowConversation,
    setHighlightedMessageId,
    projects,
    updateTodoStatus,
    acknowledgeImportantConversation,
    showToast,
  } = useChatOS();
  const [insight, setInsight] = useState<ConversationInsight | null>(null);
  const [mediaRecords, setMediaRecords] = useState<MediaRecord[]>([]);
  const [insightState, setInsightState] = useState<'loading' | 'ready' | 'unavailable'>('loading');

  useEffect(() => {
    if (!selectedConversation) return;
    const controller = new AbortController();
    const conversationId = encodeURIComponent(selectedConversation.id);
    setInsight(null);
    setMediaRecords([]);
    setInsightState('loading');

    Promise.all([
      fetch(`/api/gateway/conversations/${conversationId}/summary`, { cache: 'no-store', signal: controller.signal }),
      fetch(`/api/gateway/conversations/${conversationId}/media?kind=all&limit=20`, { cache: 'no-store', signal: controller.signal }),
    ])
      .then(async ([summaryResponse, mediaResponse]) => {
        const nextInsight = summaryResponse.ok ? await summaryResponse.json() : null;
        const nextMedia = mediaResponse.ok ? await mediaResponse.json() : { items: [] };
        if (!summaryResponse.ok && !mediaResponse.ok) throw new Error('insight unavailable');
        setInsight(nextInsight);
        setMediaRecords(Array.isArray(nextMedia.items) ? nextMedia.items : []);
        setInsightState('ready');
        acknowledgeImportantConversation(selectedConversation);
      })
      .catch(error => {
        if (error?.name === 'AbortError') return;
        setInsightState('unavailable');
        setInsight({ summary: selectedConversation.twoLineSummary, mediaCount: {} });
      });

    return () => controller.abort();
  }, [acknowledgeImportantConversation, selectedConversation?.id]);

  if (!isInspectorOpen || !selectedConversation) return null;

  const linkedProjectsList = projects.filter(p => selectedConversation.linkedProjectIds.includes(p.id));
  const mediaCount = insight?.mediaCount || {};
  const mediaTypes = Object.keys(MEDIA_META).filter(type => (mediaCount[type] || 0) > 0);
  const summary = insight?.summary || selectedConversation.twoLineSummary;
  const importantMessages = insight?.importantMessages || [];
  const relationship = getConversationRelationshipHalo(selectedConversation);
  const relationshipAttributes = {
    'data-relationship-kind': relationship.kind,
    'data-relationship-tone': relationship.tone,
    'data-relationship-score': relationship.score,
    title: relationship.label,
  };

  return (
    <aside
      id="right-detail-panel"
      className="chatos-inspector gary-glass gary-ultrathin fixed md:static inset-0 md:inset-auto z-40 md:z-10 w-full md:w-[440px] xl:w-[480px] h-full bg-[#0d121c] border-l border-[#1e293b] flex flex-col shadow-2xl transition-all duration-200 select-none"
    >
      <div className="p-4 border-b border-[#1e293b] flex items-center justify-between bg-[#111724]">
        <div className="flex items-center gap-3 min-w-0">
          <div className="relative flex-shrink-0">
            {selectedConversation.isGroup ? (
              <div className="flex items-center -space-x-2.5">
                {(selectedConversation.groupParticipants || []).slice(0, 3).map((gp, idx) => (
                  <img key={gp.id || idx} {...relationshipAttributes} src={gp.avatar} alt={gp.name} className="w-9 h-9 relationship-avatar-halo rounded-full object-cover" />
                ))}
                {(!selectedConversation.groupParticipants || selectedConversation.groupParticipants.length === 0) && (
                  <div {...relationshipAttributes} className="w-9 h-9 relationship-avatar-halo rounded-full bg-[#1b2330] flex items-center justify-center text-slate-300"><Users className="w-4 h-4" /></div>
                )}
                <div className="w-7 h-7 rounded-full bg-[#222d44] text-slate-100 font-bold text-[9px] flex items-center justify-center ring-2 ring-[#111724]">+{Math.max(1, (selectedConversation.memberCount || 3) - 2)}</div>
              </div>
            ) : (
              <div className="relative">
                {selectedConversation.avatar ? (
                  <img {...relationshipAttributes} src={selectedConversation.avatar} alt={selectedConversation.name} className="w-10 h-10 relationship-avatar-halo rounded-full object-cover" />
                ) : (
                  <div {...relationshipAttributes} className="w-10 h-10 relationship-avatar-halo rounded-full bg-[#1b2330] flex items-center justify-center text-sm font-semibold text-slate-200">{selectedConversation.name.slice(0, 1)}</div>
                )}
              </div>
            )}
            {selectedConversation.isFollowed && <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-amber-500 flex items-center justify-center text-[9px] text-slate-950 font-bold ring-1 ring-[#0d121c]">★</span>}
          </div>
          <div className="min-w-0">
            <h3 className="chatos-detail-title text-sm font-semibold text-white whitespace-nowrap" title={selectedConversation.name}>{selectedConversation.name}</h3>
            <p className="chatos-detail-subtitle text-[11px] text-slate-400 truncate">{selectedConversation.isGroup ? `${selectedConversation.groupTopic || '群协作讨论'} · 本人参与率 ${selectedConversation.joyBoyParticipationScore}%` : `${selectedConversation.roleTitle || '核心联系人'} · 近90天互动 ${selectedConversation.interactionDays90d} 天`}</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            id="header-star-conversation-btn"
            onClick={() => {
              toggleFollowConversation(selectedConversation.id);
              showToast(selectedConversation.isFollowed ? `已从左侧「星标会话」移除：${selectedConversation.name}` : `⭐ 已固定到左侧「星标会话」：${selectedConversation.name}`);
            }}
            className="chatos-icon-button text-slate-300 hover:text-amber-300 transition-colors"
            title={selectedConversation.isFollowed ? '取消固定' : '固定星标'}
          ><Star className={`w-4 h-4 ${selectedConversation.isFollowed ? 'text-amber-400 fill-amber-400' : ''}`} /></button>
          <button id="close-inspector-btn" onClick={() => { setIsInspectorOpen(false); setHighlightedMessageId(null); }} aria-label="关闭详情栏" className="chatos-icon-button text-slate-400 hover:text-white transition-colors" title="关闭详情栏"><X className="w-5 h-5" /></button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        <section className="chatos-detail-section" aria-labelledby="insight-summary-title">
          <div className="flex items-center justify-between gap-3 mb-3"><h4 id="insight-summary-title" className="chatos-rich-label font-semibold text-slate-200 flex items-center gap-2"><Sparkles className="w-4 h-4 text-indigo-300" />近期推理摘要</h4><span className="chatos-rich-meta text-[10px] text-slate-500">{insightState === 'loading' ? '正在整理' : insightState === 'ready' ? '本地派生信息' : '暂用会话摘要'}</span></div>
          <p className="chatos-rich-text text-sm text-slate-100 leading-relaxed">{summary}</p>
          <div className="mt-4 border-t border-white/10 pt-3">
            <div className="flex items-center justify-between gap-3 mb-1">
              <h5 className="chatos-rich-meta font-medium text-slate-300">重要消息摘录</h5>
              <span className="text-[10px] text-slate-500">{importantMessages.length} 条证据</span>
            </div>
            {importantMessages.length > 0 ? (
              <ol className="divide-y divide-white/10">
                {importantMessages.slice(0, 10).map((message, index) => (
                  <li key={message.messageId} className="py-2.5 first:pt-2">
                    <div className="flex items-center gap-2 text-[10px] text-slate-500">
                      <span className="tabular-nums text-slate-600">{String(index + 1).padStart(2, '0')}</span>
                      <span className="truncate text-slate-400">{message.senderName || '未知联系人'}</span>
                      <time className="ml-auto shrink-0 tabular-nums">{formatEvidenceTime(message.sentAt)}</time>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-slate-200 select-text">{message.content}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="py-3 text-xs text-slate-500">暂无足够的近期文本消息</p>
            )}
          </div>
          <p className="mt-2 text-[11px] text-slate-500">仅展示重要摘录；完整聊天记录仍作为证据保留，不在主详情面板展开。</p>
        </section>

        <section className="chatos-detail-section" aria-labelledby="important-info-title">
          <h4 id="important-info-title" className="chatos-rich-label font-semibold text-slate-200 mb-3">重要信息</h4>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div><span className="chatos-rich-meta inline-flex items-center gap-1.5"><Users className="w-3.5 h-3.5" />关键人物</span><div className="mt-2 flex flex-wrap gap-2">{(insight?.keyPeople || []).length ? insight!.keyPeople!.map(person => <span key={person.name} className="chatos-detail-chip">{person.name} · {person.count}</span>) : <span className="text-xs text-slate-500">暂无足够数据</span>}</div></div>
            <div><span className="chatos-rich-meta inline-flex items-center gap-1.5"><Calendar className="w-3.5 h-3.5" />时间锚点</span><div className="mt-2 flex flex-wrap gap-2">{(insight?.keyDates || []).length ? insight!.keyDates!.map(date => <span key={date} className="chatos-detail-chip">{date}</span>) : <span className="text-xs text-slate-500">暂无明确日期</span>}</div></div>
            <div><span className="chatos-rich-meta inline-flex items-center gap-1.5"><Link2 className="w-3.5 h-3.5" />主题</span><div className="mt-2 flex flex-wrap gap-2">{(insight?.keyTopics || []).length ? insight!.keyTopics!.map(topic => <span key={topic.term} className="chatos-detail-chip">{topic.term}</span>) : <span className="text-xs text-slate-500">暂无稳定主题</span>}</div></div>
            <div><span className="chatos-rich-meta inline-flex items-center gap-1.5"><FileText className="w-3.5 h-3.5" />关联资料</span><p className="mt-2 text-xs text-slate-300">{insight?.files?.count || 0} 个文件 · {insight?.links?.count || 0} 个链接</p></div>
          </div>
          {selectedConversation.topActionTodo && <div className="chatos-detail-action mt-4"><div className="flex items-center justify-between gap-3"><span className="text-xs font-medium text-amber-300 flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5" />最需行动待办</span><span className="text-[10px] text-slate-500">{selectedConversation.topActionTodo.dueDate}</span></div><p className="mt-2 text-sm text-slate-100">{selectedConversation.topActionTodo.title}</p><button onClick={() => updateTodoStatus(selectedConversation.topActionTodo!.id, 'completed')} className="mt-3 min-h-11 px-3 text-xs bg-emerald-600 hover:bg-emerald-500 rounded text-white font-medium inline-flex items-center gap-1.5"><CheckCircle2 className="w-3.5 h-3.5" />标记完成</button></div>}
        </section>

        <section className="chatos-detail-section" aria-labelledby="media-records-title">
          <div className="flex items-center justify-between gap-3 mb-3"><h4 id="media-records-title" className="chatos-rich-label font-semibold text-slate-200">媒体与文件记录</h4><span className="text-[10px] text-slate-500">仅记录类型与数据，不打开原始媒体</span></div>
          <div className="flex flex-wrap gap-2 mb-4">{mediaTypes.length ? mediaTypes.map(type => { const meta = MEDIA_META[type]; return <span key={type} className="chatos-detail-chip inline-flex items-center gap-1.5"><meta.Icon className="w-3.5 h-3.5" />{meta.label} {mediaCount[type]}</span>; }) : <span className="text-xs text-slate-500">暂无图片、语音、视频或文件记录</span>}</div>
          {mediaRecords.length > 0 && <div className="space-y-2">{mediaRecords.slice(0, 8).map(item => { const meta = MEDIA_META[item.type] || MEDIA_META.attachment; return <div key={`${item.type}-${item.id}`} className="chatos-media-record"><meta.Icon className="w-4 h-4 text-slate-400 shrink-0" /><span className="text-xs text-slate-200 truncate">{item.fileName || item.content || meta.label}</span><span className="text-[10px] text-slate-500 shrink-0">{meta.label}{item.fileSize ? ` · ${formatBytes(item.fileSize)}` : ''}</span></div>; })}</div>}
        </section>

        {linkedProjectsList.length > 0 && <section className="chatos-detail-section"><h4 className="chatos-rich-label font-semibold text-slate-200 mb-3">关联项目</h4><div className="flex flex-wrap gap-2">{linkedProjectsList.map(project => <span key={project.id} className="chatos-detail-chip">{project.name}</span>)}</div></section>}
      </div>
    </aside>
  );
};
