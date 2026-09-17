import React, { useState } from 'react';
import {
  FileText,
  Image as ImageIcon,
  FileCode,
  Archive,
  CheckCircle2,
  AlertCircle,
  Clock,
  RotateCw,
  ExternalLink,
  Eye,
  Trash2,
  Copy,
  Hash,
  Filter,
  X,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { KnowledgeItem, IntakeStatus } from '../../types';

export const LibraryView: React.FC = () => {
  const {
    knowledgeItems,
    conversations,
    projects,
    retryKnowledgeIntake,
    revokeKnowledgeItem,
    jumpToMessage,
    showToast,
  } = useChatOS();

  const [statusFilter, setStatusFilter] = useState<'all' | IntakeStatus>('all');
  const [typeFilter, setTypeFilter] = useState<'all' | 'image' | 'pdf' | 'doc' | 'archive'>('all');
  const [projectFilter, setProjectFilter] = useState<string>('all');
  const [previewItem, setPreviewItem] = useState<KnowledgeItem | null>(null);

  const filteredItems = knowledgeItems.filter(item => {
    if (statusFilter !== 'all' && item.intakeStatus !== statusFilter) return false;
    if (typeFilter !== 'all' && item.fileType !== typeFilter) return false;
    if (projectFilter !== 'all' && item.linkedProjectId !== projectFilter) return false;
    return true;
  });

  const getStatusBadge = (status: IntakeStatus) => {
    switch (status) {
      case 'stored':
        return { text: '已入库', color: 'bg-emerald-950/70 text-emerald-300 border-emerald-700/60' };
      case 'pending_confirmation':
        return { text: '待确认', color: 'bg-amber-950/70 text-amber-300 border-amber-700/60' };
      case 'pending_fetch':
        return { text: '待获取原文件', color: 'bg-indigo-950/70 text-indigo-300 border-indigo-700/60' };
      case 'processing':
        return { text: '处理中', color: 'bg-blue-950/70 text-blue-300 border-blue-700/60' };
      case 'failed':
        return { text: '入库失败', color: 'bg-rose-950/70 text-rose-300 border-rose-700/60' };
    }
  };

  const copyHash = (hash: string) => {
    navigator.clipboard.writeText(hash);
    showToast('内容哈希已复制到剪贴板');
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto p-6 space-y-6">
      {/* Filters Row */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        {/* Status Filter */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            id="lib-status-all"
            onClick={() => setStatusFilter('all')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === 'all'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#151c2c] text-slate-300 hover:text-white border border-[#212c42]'
            }`}
          >
            全部资料 ({knowledgeItems.length})
          </button>
          <button
            id="lib-status-stored"
            onClick={() => setStatusFilter('stored')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === 'stored'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#151c2c] text-slate-300 hover:text-white border border-[#212c42]'
            }`}
          >
            已入库 ({knowledgeItems.filter(k => k.intakeStatus === 'stored').length})
          </button>
          <button
            id="lib-status-pending"
            onClick={() => setStatusFilter('pending_confirmation')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === 'pending_confirmation'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#151c2c] text-slate-300 hover:text-white border border-[#212c42]'
            }`}
          >
            待确认 ({knowledgeItems.filter(k => k.intakeStatus === 'pending_confirmation').length})
          </button>
          <button
            id="lib-status-missing"
            onClick={() => setStatusFilter('pending_fetch')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === 'pending_fetch'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#151c2c] text-slate-300 hover:text-white border border-[#212c42]'
            }`}
          >
            待获取原文件 ({knowledgeItems.filter(k => k.intakeStatus === 'pending_fetch').length})
          </button>
        </div>

        {/* Project and Type Selectors */}
        <div className="flex items-center gap-2 text-xs">
          <select
            id="lib-project-select"
            value={projectFilter}
            onChange={e => setProjectFilter(e.target.value)}
            className="bg-[#151c2c] text-slate-300 border border-[#212c42] rounded-lg px-2.5 py-1.5 outline-none cursor-pointer"
          >
            <option value="all">所有项目</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <select
            id="lib-type-select"
            value={typeFilter}
            onChange={e => setTypeFilter(e.target.value as any)}
            className="bg-[#151c2c] text-slate-300 border border-[#212c42] rounded-lg px-2.5 py-1.5 outline-none cursor-pointer"
          >
            <option value="all">所有类型</option>
            <option value="image">图片</option>
            <option value="pdf">PDF 文档</option>
            <option value="doc">Word / 文本</option>
            <option value="archive">压缩包</option>
          </select>
        </div>
      </div>

      {/* Library Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {filteredItems.map(item => {
          const statusBadge = getStatusBadge(item.intakeStatus);
          const linkedProj = projects.find(p => p.id === item.linkedProjectId);

          return (
            <div
              key={item.id}
              id={`library-card-${item.id}`}
              className="p-4 rounded-xl bg-[#131926] border border-[#212b3e] hover:border-indigo-500/50 transition-all flex flex-col justify-between space-y-3 group hover:bg-[#161d2d]"
            >
              <div>
                {/* Image Preview or File Type Banner */}
                {item.fileType === 'image' && item.thumbnailUrl ? (
                  <div
                    onClick={() => setPreviewItem(item)}
                    className="w-full h-36 rounded-lg bg-[#0c1017] overflow-hidden mb-3 border border-[#1f283a] cursor-pointer group-hover:opacity-90 relative"
                  >
                    <img src={item.thumbnailUrl} alt={item.fileName} className="w-full h-full object-cover" />
                    <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity text-white text-xs gap-1.5">
                      <Eye className="w-4 h-4" />
                      <span>点击预览</span>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 mb-3 p-3 rounded-lg bg-[#182033] border border-[#26334d]">
                    <div className="w-10 h-10 rounded-lg bg-[#202c44] flex items-center justify-center text-indigo-400 flex-shrink-0">
                      {item.fileType === 'pdf' ? (
                        <FileText className="w-5 h-5" />
                      ) : item.fileType === 'archive' ? (
                        <Archive className="w-5 h-5" />
                      ) : (
                        <FileCode className="w-5 h-5" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-slate-200 truncate">{item.fileName}</p>
                      <p className="text-[10px] text-slate-400">{item.fileSize}</p>
                    </div>
                  </div>
                )}

                {/* Status & Project Tag */}
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${statusBadge.color}`}>
                    {statusBadge.text}
                  </span>

                  {linkedProj && (
                    <span className="text-[10px] px-2 py-0.5 rounded bg-[#182133] text-indigo-300 border border-[#273754] truncate max-w-[140px]">
                      {linkedProj.name}
                    </span>
                  )}
                </div>

                {/* Title */}
                <h4 className="text-xs font-semibold text-slate-100 mt-2.5 line-clamp-1">
                  {item.title}
                </h4>

                {/* Content Hash Tag (Acceptance Criterion 5: Hash deduplication) */}
                <div className="mt-2 flex items-center justify-between text-[10px] font-mono text-slate-400 bg-[#0d121c] p-1.5 rounded border border-[#1b2334]">
                  <span className="truncate max-w-[190px] flex items-center gap-1">
                    <Hash className="w-3 h-3 text-indigo-400 flex-shrink-0" />
                    {item.contentHash.slice(0, 24)}...
                  </span>
                  <button
                    onClick={() => copyHash(item.contentHash)}
                    className="text-slate-500 hover:text-slate-300 p-0.5"
                    title="复制完整内容哈希"
                  >
                    <Copy className="w-3 h-3" />
                  </button>
                </div>

                {item.isDuplicateSample && (
                  <p className="text-[10px] text-indigo-400 mt-1">
                    * 镜像副本已去重复用，保留多来源记录
                  </p>
                )}

                {item.failureReason && (
                  <p className="text-[10px] text-rose-400 mt-1 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3 flex-shrink-0" />
                    <span>{item.failureReason}</span>
                  </p>
                )}
              </div>

              {/* Card Footer Actions */}
              <div className="pt-3 border-t border-[#1d273a] flex items-center justify-between text-xs">
                <button
                  id={`jump-lib-source-${item.id}`}
                  onClick={() => jumpToMessage(item.sourceConversationId, item.sourceMessageId)}
                  className="flex items-center gap-1 text-[11px] text-indigo-400 hover:text-indigo-300 transition-colors"
                  title={`定位原消息来源：${item.sourceConversationName}`}
                >
                  <span>来源会话</span>
                  <ExternalLink className="w-3 h-3" />
                </button>

                <div className="flex items-center gap-2">
                  {(item.intakeStatus === 'pending_fetch' || item.intakeStatus === 'failed') && (
                    <button
                      onClick={() => retryKnowledgeIntake(item.id)}
                      className="flex items-center gap-1 text-[10px] text-amber-400 hover:text-amber-300"
                    >
                      <RotateCw className="w-3 h-3" />
                      <span>重试获取</span>
                    </button>
                  )}

                  <button
                    onClick={() => revokeKnowledgeItem(item.id)}
                    className="text-[10px] text-slate-500 hover:text-rose-400 transition-colors"
                  >
                    撤回收录
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Preview Modal */}
      {previewItem && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="w-full max-w-2xl bg-[#131926] border border-[#28354c] rounded-xl overflow-hidden shadow-2xl space-y-4">
            <div className="p-4 border-b border-[#212c40] flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-white">{previewItem.fileName}</h3>
                <p className="text-xs text-slate-400 mt-0.5">来源：{previewItem.sourceConversationName}</p>
              </div>
              <button
                onClick={() => setPreviewItem(null)}
                className="p-1 text-slate-400 hover:text-white rounded"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-4 flex flex-col items-center justify-center">
              {previewItem.thumbnailUrl ? (
                <img
                  src={previewItem.thumbnailUrl}
                  alt={previewItem.fileName}
                  className="max-h-96 rounded-lg object-contain"
                />
              ) : (
                <div className="w-full h-48 rounded-lg bg-[#0d121c] border border-[#1e283b] flex flex-col items-center justify-center text-slate-400 gap-2">
                  <FileText className="w-12 h-12 text-indigo-400" />
                  <span className="text-xs font-mono">{previewItem.fileName}</span>
                  <span className="text-[11px] text-slate-500">文件大小：{previewItem.fileSize}</span>
                </div>
              )}

              <div className="w-full mt-4 p-3 bg-[#0d121c] rounded-lg border border-[#1e273a] text-xs space-y-1 font-mono">
                <div className="text-slate-400">内容哈希：{previewItem.contentHash}</div>
                <div className="text-slate-400">入库时间：{previewItem.createdAt}</div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
