import React, { useState } from 'react';
import {
  X,
  Link2,
  ArrowUpRight,
  ArrowDownLeft,
  ExternalLink,
  Plus,
  Trash2,
  Sparkles,
  Focus,
  Clock,
  Tag,
  CheckCircle2,
  FolderKanban,
  MessageSquare,
  FileText,
  UserCheck,
} from 'lucide-react';
import { MindMapNode, MindMapBiLink } from '../../../types';
import { NodeBidirectionalInspect } from '../../../utils/mindmapData';
import { useChatOS } from '../../../context/ChatOSContext';

interface BidirectionalInspectorProps {
  inspectData: NodeBidirectionalInspect;
  allNodes: MindMapNode[];
  onClose: () => void;
  onFocusNode: (nodeId: string) => void;
}

export const BidirectionalInspector: React.FC<BidirectionalInspectorProps> = ({
  inspectData,
  allNodes,
  onClose,
  onFocusNode,
}) => {
  const {
    setActiveWorkspace,
    setSelectedConversation,
    setSelectedProject,
    setSelectedTodo,
    setSelectedKnowledgeItem,
    conversations,
    projects,
    todos,
    knowledgeItems,
    addCustomBiLink,
    removeCustomBiLink,
  } = useChatOS();

  const [isCreatingLink, setIsCreatingLink] = useState<boolean>(false);
  const [targetNodeId, setTargetNodeId] = useState<string>('');
  const [relationLabel, setRelationLabel] = useState<string>('双链关联');
  const [relationType, setRelationType] = useState<
    'references' | 'produces' | 'belongs_to' | 'assignee' | 'timeline' | 'custom'
  >('references');
  const [customSnippet, setCustomSnippet] = useState<string>('');

  const { node, outgoingLinks, incomingBacklinks, totalConnections } = inspectData;

  const handleJumpToSourceWorkspace = () => {
    if (node.type === 'conversation') {
      const conv = conversations.find(c => c.id === node.entityId || c.id === node.id);
      if (conv) {
        setSelectedConversation(conv);
        setActiveWorkspace('conversations');
      }
    } else if (node.type === 'project') {
      const proj = projects.find(p => p.id === node.entityId || p.id === node.id);
      if (proj) {
        setSelectedProject(proj);
        setActiveWorkspace('projects');
      }
    } else if (node.type === 'todo') {
      const todo = todos.find(t => t.id === node.entityId || t.id === node.id);
      if (todo) {
        setSelectedTodo(todo);
        setActiveWorkspace('todos');
      }
    } else if (node.type === 'knowledge') {
      const item = knowledgeItems.find(k => k.id === node.entityId || k.id === node.id);
      if (item) {
        setSelectedKnowledgeItem(item);
        setActiveWorkspace('library');
      }
    }
  };

  const handleCreateBiLinkSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetNodeId) return;

    const targetNode = allNodes.find(n => n.id === targetNodeId);
    const snippet =
      customSnippet.trim() ||
      `双链映射: [[${node.label}]] <==> [[${targetNode?.label || targetNodeId}]] (${relationLabel})`;

    addCustomBiLink(node.id, targetNodeId, relationType, relationLabel, snippet);
    setIsCreatingLink(false);
    setTargetNodeId('');
    setCustomSnippet('');
  };

  const getNodeIcon = (type: string) => {
    switch (type) {
      case 'conversation':
        return <MessageSquare className="w-4 h-4 text-indigo-400" />;
      case 'project':
        return <FolderKanban className="w-4 h-4 text-cyan-400" />;
      case 'todo':
        return <CheckCircle2 className="w-4 h-4 text-amber-400" />;
      case 'knowledge':
        return <FileText className="w-4 h-4 text-emerald-400" />;
      case 'person':
        return <UserCheck className="w-4 h-4 text-pink-400" />;
      default:
        return <Tag className="w-4 h-4 text-purple-400" />;
    }
  };

  const getNodeTypeBadge = (type: string) => {
    const map: Record<string, { label: string; bg: string }> = {
      conversation: { label: '会话实体', bg: 'bg-indigo-950 text-indigo-300 border-indigo-800' },
      project: { label: '主导项目', bg: 'bg-cyan-950 text-cyan-300 border-cyan-800' },
      todo: { label: '行动待办', bg: 'bg-amber-950 text-amber-300 border-amber-800' },
      knowledge: { label: '资产资料', bg: 'bg-emerald-950 text-emerald-300 border-emerald-800' },
      person: { label: '协作成员', bg: 'bg-pink-950 text-pink-300 border-pink-800' },
      topic: { label: '议题网络', bg: 'bg-purple-950 text-purple-300 border-purple-800' },
      date: { label: '时序锚点', bg: 'bg-slate-900 text-slate-300 border-slate-700' },
    };
    const item = map[type] || { label: '实体', bg: 'bg-slate-800 text-slate-300 border-slate-700' };
    return (
      <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${item.bg}`}>
        {item.label}
      </span>
    );
  };

  // Available nodes for linking (exclude current node)
  const linkCandidates = allNodes.filter(n => n.id !== node.id);

  return (
    <div
      id="bidirectional-inspector-drawer"
      className="absolute top-3 right-3 bottom-3 w-96 max-w-[calc(100vw-2rem)] bg-[#0d131f]/95 backdrop-blur-xl border border-[#22314a] rounded-2xl shadow-2xl z-30 flex flex-col overflow-hidden animate-in slide-in-from-right-4 duration-200"
    >
      {/* Drawer Header */}
      <div className="p-4 border-b border-[#1c283d] flex items-center justify-between bg-[#101726]/60">
        <div className="flex items-center gap-2 min-w-0">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center border"
            style={{
              backgroundColor: `${node.color}15`,
              borderColor: `${node.color}50`,
            }}
          >
            {getNodeIcon(node.type)}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <h3 className="text-sm font-bold text-white truncate max-w-[200px]">{node.label}</h3>
              {getNodeTypeBadge(node.type)}
            </div>
            <p className="text-[11px] text-slate-400 flex items-center gap-2 mt-0.5 font-mono">
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3 text-slate-400" />
                {node.timestamp || node.dateKey}
              </span>
              <span className="text-indigo-400">· {totalConnections} 条双向链接</span>
              {node.affinityScore && (
                <span className="text-cyan-400 font-semibold">· 关联度 {node.affinityScore}%</span>
              )}
            </p>
          </div>
        </div>

        <button
          onClick={onClose}
          className="p-1.5 text-slate-400 hover:text-white hover:bg-[#1a2436] rounded-lg transition-colors"
          title="关闭双链检查器"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Action Bar */}
      <div className="px-4 py-2.5 bg-[#0f1624] border-b border-[#1c283d] flex items-center justify-between gap-2 text-xs">
        <button
          id="focus-ego-network-btn"
          onClick={() => onFocusNode(node.id)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600 text-indigo-300 hover:text-white border border-indigo-500/40 transition-all font-medium"
          title="在图谱中聚焦该节点的邻居子网络"
        >
          <Focus className="w-3.5 h-3.5" />
          <span>聚焦子图</span>
        </button>

        <button
          id="jump-workspace-source-btn"
          onClick={handleJumpToSourceWorkspace}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#162032] hover:bg-[#1f2d47] text-slate-300 hover:text-white border border-[#273752] transition-colors"
          title="跳转到对应的原始工作区"
        >
          <ExternalLink className="w-3.5 h-3.5" />
          <span>定位工作区</span>
        </button>

        <button
          id="toggle-create-bilink-btn"
          onClick={() => setIsCreatingLink(!isCreatingLink)}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 shadow-sm transition-colors font-medium ml-auto"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>建双链</span>
        </button>
      </div>

      {/* Drawer Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
        {/* Affinity Metric Meter */}
        {node.affinityScore && (
          <div className="p-3 rounded-xl bg-[#121927] border border-[#1e2a3f] space-y-1.5">
            <div className="flex items-center justify-between text-slate-300 font-mono text-[11px]">
              <span className="flex items-center gap-1 text-slate-400">
                <Sparkles className="w-3 h-3 text-cyan-400" />
                <span>动态关联度指数 (Affinity Index)</span>
              </span>
              <span className="text-cyan-400 font-bold">{node.affinityScore}%</span>
            </div>
            <div className="w-full h-2 bg-[#0a0f18] rounded-full overflow-hidden p-0.5 border border-[#1b273b]">
              <div
                className="h-full bg-gradient-to-r from-indigo-500 via-cyan-400 to-amber-400 rounded-full transition-all duration-500"
                style={{ width: `${node.affinityScore}%` }}
              />
            </div>
            <p className="text-[10px] text-slate-400">
              {node.affinityScore >= 80
                ? '⭐ 核心引力枢纽：主导会话、项目与日期的演化流动'
                : node.affinityScore >= 60
                ? '⚡ 关键互联节点：具有高密度双向信息对齐'
                : '🔹 常规协同节点：受时间与项目引力约束自动排列'}
            </p>
          </div>
        )}

        {/* Node Overview Card */}
        {node.details && (
          <div className="p-3 rounded-xl bg-[#121927] border border-[#1e2a3f] text-slate-300 leading-relaxed">
            <span className="text-[10px] uppercase font-mono text-slate-400 block mb-1">
              实体摘要 / 演化状态
            </span>
            <p className="text-slate-200">{node.details}</p>
            {node.badge && (
              <span className="inline-block mt-2 text-[10px] px-2 py-0.5 rounded bg-indigo-950/70 border border-indigo-800/60 text-indigo-300 font-mono">
                {node.badge}
              </span>
            )}
          </div>
        )}

        {/* Quick Bi-Link Creator Form */}
        {isCreatingLink && (
          <form
            onSubmit={handleCreateBiLinkSubmit}
            className="p-3.5 rounded-xl bg-[#151e30] border border-indigo-500/50 space-y-3 animate-in fade-in duration-150"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-indigo-300 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                <span>新建双向链接</span>
              </span>
              <button
                type="button"
                onClick={() => setIsCreatingLink(false)}
                className="text-slate-400 hover:text-slate-200"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">连接目标节点</label>
              <select
                value={targetNodeId}
                onChange={e => setTargetNodeId(e.target.value)}
                required
                className="w-full px-2.5 py-1.5 rounded-lg bg-[#0d1422] border border-[#24334f] text-slate-200 text-xs focus:outline-none focus:border-indigo-500"
              >
                <option value="">-- 选择目标节点 --</option>
                {linkCandidates.map(c => (
                  <option key={c.id} value={c.id}>
                    [{c.type}] {c.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[11px] text-slate-400 block mb-1">关系标签</label>
                <input
                  type="text"
                  value={relationLabel}
                  onChange={e => setRelationLabel(e.target.value)}
                  placeholder="如: 探讨归属, 提取待办"
                  className="w-full px-2.5 py-1.5 rounded-lg bg-[#0d1422] border border-[#24334f] text-slate-200 text-xs focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="text-[11px] text-slate-400 block mb-1">关系类型</label>
                <select
                  value={relationType}
                  onChange={e => setRelationType(e.target.value as any)}
                  className="w-full px-2.5 py-1.5 rounded-lg bg-[#0d1422] border border-[#24334f] text-slate-200 text-xs focus:outline-none focus:border-indigo-500"
                >
                  <option value="references">双向引用</option>
                  <option value="produces">生成/产出</option>
                  <option value="belongs_to">归属/隶属</option>
                  <option value="assignee">人员负责</option>
                  <option value="timeline">时序支撑</option>
                </select>
              </div>
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">双链推演依据 / 上下文</label>
              <input
                type="text"
                value={customSnippet}
                onChange={e => setCustomSnippet(e.target.value)}
                placeholder="说明两个节点为何发生双向关联..."
                className="w-full px-2.5 py-1.5 rounded-lg bg-[#0d1422] border border-[#24334f] text-slate-200 text-xs focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setIsCreatingLink(false)}
                className="px-3 py-1 rounded-lg bg-[#182338] text-slate-300 hover:text-white"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={!targetNodeId}
                className="px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium disabled:opacity-50"
              >
                保存双链
              </button>
            </div>
          </form>
        )}

        {/* 1. Incoming Backlinks (反向链接 - 被谁引用) */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold text-slate-200 flex items-center gap-1.5">
              <ArrowDownLeft className="w-3.5 h-3.5 text-emerald-400" />
              <span>反向链接 (Backlinks · 被引用)</span>
            </h4>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-300 border border-emerald-800">
              {incomingBacklinks.length} 条
            </span>
          </div>

          {incomingBacklinks.length === 0 ? (
            <div className="p-3 rounded-xl bg-[#111724] border border-[#1e2a3f] text-slate-400 text-center text-[11px]">
              暂无其它节点反向引用该实体
            </div>
          ) : (
            <div className="space-y-2">
              {incomingBacklinks.map(({ link, sourceNode }) => {
                const isCustom = link.id.startsWith('custom-bilink');

                return (
                  <div
                    key={link.id}
                    onClick={() => onFocusNode(sourceNode.id)}
                    className="p-3 rounded-xl bg-[#111724] border border-[#1e2a3f] hover:border-emerald-500/50 cursor-pointer transition-all space-y-1.5 group"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 min-w-0">
                        {getNodeIcon(sourceNode.type)}
                        <span className="font-semibold text-slate-200 truncate group-hover:text-emerald-300 transition-colors">
                          {sourceNode.label}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#192233] text-slate-300 border border-[#273650]">
                          {link.label || '反向引用'}
                        </span>
                        {isCustom && (
                          <button
                            type="button"
                            onClick={e => {
                              e.stopPropagation();
                              removeCustomBiLink(link.id);
                            }}
                            className="p-1 text-slate-400 hover:text-rose-400 transition-colors"
                            title="解除此双链"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        )}
                      </div>
                    </div>

                    {link.snippet && (
                      <p className="text-[11px] text-slate-400 group-hover:text-slate-300 line-clamp-2 leading-relaxed bg-[#0b1019] p-1.5 rounded-lg border border-[#1b263b]">
                        {link.snippet}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 2. Outgoing Links (正向引用 - 引用了谁) */}
        <div className="space-y-2 pt-2 border-t border-[#1c283d]">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold text-slate-200 flex items-center gap-1.5">
              <ArrowUpRight className="w-3.5 h-3.5 text-indigo-400" />
              <span>正向引用 (Outgoing Links)</span>
            </h4>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-800">
              {outgoingLinks.length} 条
            </span>
          </div>

          {outgoingLinks.length === 0 ? (
            <div className="p-3 rounded-xl bg-[#111724] border border-[#1e2a3f] text-slate-400 text-center text-[11px]">
              该节点尚未主动引用其它实体
            </div>
          ) : (
            <div className="space-y-2">
              {outgoingLinks.map(({ link, targetNode }) => {
                const isCustom = link.id.startsWith('custom-bilink');

                return (
                  <div
                    key={link.id}
                    onClick={() => onFocusNode(targetNode.id)}
                    className="p-3 rounded-xl bg-[#111724] border border-[#1e2a3f] hover:border-indigo-500/50 cursor-pointer transition-all space-y-1.5 group"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 min-w-0">
                        {getNodeIcon(targetNode.type)}
                        <span className="font-semibold text-slate-200 truncate group-hover:text-indigo-300 transition-colors">
                          {targetNode.label}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#192233] text-indigo-300 border border-indigo-900/50">
                          {link.label || '正向关联'}
                        </span>
                        {isCustom && (
                          <button
                            type="button"
                            onClick={e => {
                              e.stopPropagation();
                              removeCustomBiLink(link.id);
                            }}
                            className="p-1 text-slate-400 hover:text-rose-400 transition-colors"
                            title="解除此双链"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        )}
                      </div>
                    </div>

                    {link.snippet && (
                      <p className="text-[11px] text-slate-400 group-hover:text-slate-300 line-clamp-2 leading-relaxed bg-[#0b1019] p-1.5 rounded-lg border border-[#1b263b]">
                        {link.snippet}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
