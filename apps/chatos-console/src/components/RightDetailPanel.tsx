import React, { useEffect, useRef, useState } from 'react';
import {
  X,
  Star,
  Clock,
  Send,
  Calendar,
  AlertTriangle,
  FileText,
  Users,
  CheckCircle2,
  ExternalLink,
  ShieldCheck,
  ChevronDown,
  Sparkles,
} from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';
import { CURRENT_USER } from '../mockData';

export const RightDetailPanel: React.FC = () => {
  const {
    isInspectorOpen,
    setIsInspectorOpen,
    selectedConversation,
    toggleFollowConversation,
    messages,
    highlightedMessageId,
    setHighlightedMessageId,
    projects,
    updateTodoStatus,
    showToast,
  } = useChatOS();

  const [replyText, setReplyText] = useState<string>('');
  const messageListRef = useRef<HTMLDivElement>(null);
  const highlightedMsgRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to highlighted message if present
  useEffect(() => {
    if (highlightedMessageId && highlightedMsgRef.current) {
      highlightedMsgRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightedMessageId]);

  if (!isInspectorOpen || !selectedConversation) {
    return null;
  }

  const convMessages = messages[selectedConversation.id] || [];
  const linkedProjectsList = projects.filter(p => selectedConversation.linkedProjectIds.includes(p.id));

  const handleSendReply = (e: React.FormEvent) => {
    e.preventDefault();
    if (!replyText.trim()) return;

    showToast('发送服务尚未接入，输入内容已保留');
  };

  return (
    <aside
      id="right-detail-panel"
      className="chatos-inspector gary-glass gary-ultrathin fixed md:static inset-0 md:inset-auto z-40 md:z-10 w-full md:w-[440px] lg:w-[480px] h-full bg-[#0d121c] border-l border-[#1e293b] flex flex-col justify-between shadow-2xl transition-all duration-200 select-none"
    >
      {/* Top Header */}
      <div className="p-4 border-b border-[#1e293b] flex items-center justify-between bg-[#111724]">
        <div className="flex items-center gap-3 min-w-0">
          <div className="relative flex-shrink-0">
            {selectedConversation.isGroup ? (
              <div className="flex items-center -space-x-2.5">
                {selectedConversation.groupParticipants && selectedConversation.groupParticipants.length > 0 ? (
                  selectedConversation.groupParticipants.slice(0, 3).map((gp, idx) => (
                    <img
                      key={gp.id || idx}
                      src={gp.avatar}
                      alt={gp.name}
                      className="w-8 h-8 rounded-full object-cover ring-2 ring-[#111724]"
                    />
                  ))
                ) : (
                  <div className="w-9 h-9 rounded-xl bg-indigo-950/80 border border-indigo-500/40 flex items-center justify-center text-indigo-400">
                    <Users className="w-4 h-4" />
                  </div>
                )}
                <div className="w-7 h-7 rounded-full bg-indigo-600 text-white font-bold text-[9px] flex items-center justify-center ring-2 ring-[#111724]">
                  +{Math.max(1, (selectedConversation.memberCount || 3) - 2)}
                </div>
              </div>
            ) : (
              <div className="relative">
                <img
                  src={selectedConversation.avatar || 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=60'}
                  alt={selectedConversation.name}
                  className="w-10 h-10 rounded-full object-cover ring-2 ring-emerald-500/50"
                />
                <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-500 ring-2 ring-[#111724]" />
              </div>
            )}
            {selectedConversation.isFollowed && (
              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-amber-500 flex items-center justify-center text-[9px] text-slate-950 font-bold shadow-sm ring-1 ring-[#0d121c]">
                ★
              </span>
            )}
          </div>
          <div className="min-w-0">
            <h3 className="chatos-detail-title text-sm font-semibold text-white whitespace-nowrap" title={selectedConversation.name}>
              {selectedConversation.name}
            </h3>
            <p className="chatos-detail-subtitle text-[11px] text-slate-400 truncate">
              {selectedConversation.isGroup
                ? `${selectedConversation.groupTopic || '群协作讨论'} · 本人参与率 ${selectedConversation.joyBoyParticipationScore}%`
                : `${selectedConversation.roleTitle || '核心联系人'} · 近90天互动 ${selectedConversation.interactionDays90d} 天`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            id="header-star-conversation-btn"
            onClick={() => {
              toggleFollowConversation(selectedConversation.id);
              showToast(
                selectedConversation.isFollowed
                  ? `已从左侧「星标会话」移除：${selectedConversation.name}`
                  : `⭐ 已固定到左侧「星标会话」：${selectedConversation.name}`
              );
            }}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
              selectedConversation.isFollowed
                ? 'text-amber-300 bg-amber-500/15 border border-amber-500/40 hover:bg-amber-500/25 shadow-xs'
                : 'text-slate-300 hover:text-amber-300 bg-[#161d2b] border border-[#243044] hover:border-amber-500/30'
            }`}
            title={selectedConversation.isFollowed ? '点击从左侧「星标会话」中取消固定' : '点击将此重要会话固定在左侧「星标会话」区域'}
          >
            <Star className={`w-4 h-4 ${selectedConversation.isFollowed ? 'text-amber-400 fill-amber-400' : ''}`} />
            <span className="text-[11px] hidden sm:inline">
              {selectedConversation.isFollowed ? '已固定' : '固定星标'}
            </span>
          </button>

          <button
            id="close-inspector-btn"
            onClick={() => {
              setIsInspectorOpen(false);
              setHighlightedMessageId(null);
            }}
            aria-label="关闭详情栏"
            className="p-2 text-slate-400 hover:text-white hover:bg-[#1a2336] rounded-lg transition-colors"
            title="关闭详情栏 (返回原位置)"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Main Content: Two Distinct Areas (Prompt Rule 2) */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* AREA 1: 一屏精选摘要 (Top Curated Digest) */}
        <div className="p-4 bg-[#111724]/90 border-b border-[#1e293b] space-y-3 flex-shrink-0">
          <div className="flex items-center justify-between text-xs">
            <span className="chatos-rich-label font-semibold text-slate-300 flex items-center gap-1">
              <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
              <span>精选行动摘要</span>
            </span>
            <span className="chatos-rich-meta text-[10px] text-slate-500 font-mono">
              整理依据：{selectedConversation.priorityReason}
            </span>
          </div>

          {/* 2-line summary */}
          <div className="chatos-rich-text p-3 rounded-lg bg-[#151c2c] border border-[#232f44] text-xs text-slate-200 leading-relaxed">
            {selectedConversation.twoLineSummary}
          </div>

          {/* Top Action Item in Summary */}
          {selectedConversation.topActionTodo ? (
            <div className="p-3 rounded-lg bg-amber-950/20 border border-amber-800/40 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="chatos-rich-label font-medium text-amber-400 flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                  <span>最需行动待办</span>
                </span>
                <span className="chatos-rich-meta text-[10px] font-mono text-slate-400">
                  截止: {selectedConversation.topActionTodo.dueDate}
                </span>
              </div>
              <p className="chatos-rich-emphasis text-xs font-semibold text-white">
                {selectedConversation.topActionTodo.title}
              </p>
              <div className="flex items-center justify-between pt-1">
                <span className="chatos-rich-meta text-[10px] text-slate-400">
                  依据：{selectedConversation.topActionTodo.evidenceText}
                </span>
                <button
                  onClick={() => updateTodoStatus(selectedConversation.topActionTodo!.id, 'completed')}
                  className="px-2 py-0.5 text-[10px] bg-emerald-600 hover:bg-emerald-500 rounded text-white font-medium flex items-center gap-1"
                >
                  <CheckCircle2 className="w-3 h-3" />
                  <span>标记完成</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="p-2.5 rounded-lg bg-[#141a27] border border-[#202a3d] text-xs text-slate-400 flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>本会话当前无紧急行动项</span>
            </div>
          )}

          {/* Linked Projects Tags */}
          {linkedProjectsList.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap pt-1">
              <span className="chatos-rich-meta text-[10px] text-slate-400">关联项目：</span>
              {linkedProjectsList.map(p => (
                <span
                  key={p.id}
                  className="text-[10px] font-medium px-2 py-0.5 rounded bg-indigo-950/70 text-indigo-300 border border-indigo-800/60"
                >
                  {p.name}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* AREA 2: 完整消息与资料深度可滚动阅读区 (Prompt Rule 2: 详情允许滚动并可定位) */}
        <div
          ref={messageListRef}
          className="flex-1 overflow-y-auto p-4 space-y-3 bg-[#0a0e16]"
        >
          <div className="text-center py-2">
            <span className="chatos-rich-meta text-[10px] font-mono px-2 py-0.5 rounded bg-[#131926] text-slate-500 border border-[#1e2738]">
              近 90 天完整上下文时间线
            </span>
          </div>

          {convMessages.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-xs">
              暂无历史消息记录
            </div>
          ) : (
            convMessages.map(msg => {
              const isSelf = msg.senderId === 'user-joyboy';
              const isHighlighted = highlightedMessageId === msg.id;

              return (
                <div
                  key={msg.id}
                  id={`chat-msg-${msg.id}`}
                  ref={isHighlighted ? highlightedMsgRef : null}
                  className={`flex flex-col space-y-1 transition-all duration-300 p-2 rounded-xl ${
                    isHighlighted
                      ? 'bg-amber-500/10 border-2 border-amber-500/60 shadow-lg shadow-amber-950/30 ring-2 ring-amber-500/20'
                      : 'hover:bg-[#121824]'
                  }`}
                >
                  <div className={`chatos-message-meta flex items-center gap-2 text-[11px] ${isSelf ? 'justify-end' : 'justify-start'}`}>
                    {!isSelf && (
                      <img src={msg.senderAvatar} alt={msg.senderName} className="w-5 h-5 rounded-full object-cover" />
                    )}
                    <span className={`font-medium ${isSelf ? 'text-indigo-300' : 'text-slate-300'}`}>
                      {msg.senderName}
                    </span>
                    <span className="text-[10px] font-mono text-slate-500">{msg.timestamp}</span>
                  </div>

                  <div className={`flex ${isSelf ? 'justify-end' : 'justify-start'}`}>
                    <div
                      className={`chatos-message-bubble chatos-rich-text ${
                        isSelf
                          ? 'chatos-message-bubble--self bg-indigo-600 text-white rounded-tr-none'
                          : 'chatos-message-bubble--other bg-[#151c2b] text-slate-200 border border-[#232f44] rounded-tl-none'
                      }`}
                    >
                      <p>{msg.content}</p>

                      {/* Attachment preview if any */}
                      {msg.hasAttachment && (
                        <div className="mt-2.5 p-2 rounded-lg bg-black/30 border border-white/10 flex items-center justify-between gap-2 text-[11px]">
                          <div className="flex items-center gap-2 min-w-0">
                            <FileText className="w-4 h-4 text-indigo-300 flex-shrink-0" />
                            <span className="truncate">{msg.attachmentName}</span>
                          </div>
                          {msg.attachmentSize && (
                            <span className="text-[10px] opacity-70 font-mono flex-shrink-0">
                              {msg.attachmentSize}
                            </span>
                          )}
                        </div>
                      )}

                      {/* Action trigger badge */}
                      {msg.isActionTrigger && (
                        <div className="mt-2 text-[10px] text-amber-300 font-medium flex items-center gap-1 pt-1 border-t border-white/10">
                          <AlertTriangle className="w-3 h-3" />
                          <span>已提取为 ChatOS 行动待办</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Bottom Message Input / Action Composer */}
      <form onSubmit={handleSendReply} className="p-3 border-t border-[#1e293b] bg-[#111724]">
        <div className="flex items-center gap-2 bg-[#161e2e] rounded-xl border border-[#243147] px-3 py-1.5 focus-within:border-indigo-500 transition-colors">
          <input
            type="text"
            value={replyText}
            onChange={e => setReplyText(e.target.value)}
            placeholder={`以 JoyBoy 身份回复或录入跟进备忘...`}
            className="flex-1 bg-transparent text-xs text-slate-100 placeholder:text-slate-500 outline-none py-1.5"
          />
          <button
            type="submit"
            disabled={!replyText.trim()}
            className="p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition-colors"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </form>
    </aside>
  );
};
