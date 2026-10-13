import React, { useState } from 'react';
import {
  MessageSquare,
  Send,
  Pin,
  Smile,
  Trash2,
  X,
  Users,
  Sparkles,
  ChevronDown,
  Check,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { Project, ProjectComment, User } from '../../types';

interface ProjectDiscussionDrawerProps {
  project: Project;
  onClose: () => void;
}

const COMMON_REACTION_EMOJIS = ['👍', '🚀', '❤️', '🔥', '👀', '💡'];

export const ProjectDiscussionDrawer: React.FC<ProjectDiscussionDrawerProps> = ({
  project,
  onClose,
}) => {
  const {
    projectComments,
    addProjectComment,
    deleteProjectComment,
    toggleCommentReaction,
    teamMembers,
  } = useChatOS();

  const comments: ProjectComment[] = projectComments[project.id] || [];

  const [inputContent, setInputContent] = useState<string>('');
  const [selectedAuthorId, setSelectedAuthorId] = useState<string>(teamMembers[0]?.id || 'user-joyboy');
  const [isPinned, setIsPinned] = useState<boolean>(false);
  const [showAuthorPicker, setShowAuthorPicker] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const currentAuthor = teamMembers.find(m => m.id === selectedAuthorId) || teamMembers[0];

  const handleSendComment = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputContent.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await addProjectComment(project.id, inputContent.trim(), selectedAuthorId);
      setInputContent('');
      setIsPinned(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleInsertMention = (member: User) => {
    setInputContent(prev => `${prev}@${member.name} `);
  };

  const sortedComments = [...comments].sort((a, b) => {
    if (a.isPinned && !b.isPinned) return -1;
    if (!a.isPinned && b.isPinned) return 1;
    return 0;
  });

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-end animate-fadeIn">
      {/* Drawer Container */}
      <div
        id="project-discussion-drawer"
        className="w-full max-w-xl h-full bg-[#111722] border-l border-[#1f293d] flex flex-col shadow-2xl"
      >
        {/* Header */}
        <div className="p-4 border-b border-[#1f293d] bg-[#141b27] flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <span
              className="w-3 h-3 rounded-full flex-shrink-0"
              style={{ backgroundColor: project.color }}
            />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white truncate">{project.name}</h3>
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#1e283b] text-slate-300 border border-[#2c3952]">
                  {project.code}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 truncate mt-0.5">
                协作讨论批注 · 共 {comments.length} 条评论
              </p>
            </div>
          </div>

          <button
            id="close-discussion-drawer-btn"
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-[#1f2a3e] transition-colors"
            title="关闭讨论"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Project Context Snippet Banner */}
        <div className="px-4 py-2.5 bg-[#172030] border-b border-[#212c40] flex items-center justify-between text-xs">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-amber-400 font-medium flex-shrink-0">当前行动：</span>
            <span className="text-slate-300 truncate">{project.currentAction}</span>
          </div>
          <span className="text-indigo-400 font-mono font-bold flex-shrink-0 ml-2">
            {project.completionPercentage}%
          </span>
        </div>

        {/* Comments Feed */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {sortedComments.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-8 space-y-3">
              <div className="w-12 h-12 rounded-full bg-[#182133] border border-[#273652] flex items-center justify-center text-indigo-400">
                <MessageSquare className="w-6 h-6" />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-300">暂无项目讨论</p>
                <p className="text-xs text-slate-500 mt-1 max-w-xs">
                  针对当前交付节点、法务边界或技术架构发表第一条批注，支持跨角色讨论与表情互动。
                </p>
              </div>
            </div>
          ) : (
            sortedComments.map(comment => {
              const hasUserReacted = (emoji: string) =>
                comment.reactions?.some(r => r.emoji === emoji && r.users.includes(selectedAuthorId));

              return (
                <div
                  key={comment.id}
                  id={`comment-${comment.id}`}
                  className={`p-3.5 rounded-xl border transition-all ${
                    comment.isPinned
                      ? 'bg-[#182236] border-indigo-500/40 shadow-sm'
                      : 'bg-[#141b27] border-[#222d42]'
                  }`}
                >
                  {/* Top: Author, Role, Time, Pin badge, Delete */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2.5">
                      <img
                        src={comment.author.avatar}
                        alt={comment.author.name}
                        className="w-7 h-7 rounded-full object-cover border border-[#2b3952]"
                        referrerPolicy="no-referrer"
                      />
                      <div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-semibold text-slate-200">
                            {comment.author.name}
                          </span>
                          {comment.author.isSelf && (
                            <span className="text-[9px] px-1 py-0.2 rounded bg-indigo-950 text-indigo-300 border border-indigo-800">
                              本人
                            </span>
                          )}
                          <span className="text-[10px] text-slate-400">
                            ({comment.author.role})
                          </span>
                        </div>
                        <span className="text-[10px] text-slate-500">{comment.timestamp}</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-1">
                      {comment.isPinned && (
                        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                          <Pin className="w-2.5 h-2.5 fill-indigo-300" />
                          置顶
                        </span>
                      )}
                      <button
                        onClick={() => deleteProjectComment(project.id, comment.id)}
                        className="p-1 text-slate-500 hover:text-rose-400 rounded transition-colors"
                        title="删除评论"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Body Content with @mention highlights */}
                  <div className="mt-2 text-xs text-slate-200 leading-relaxed whitespace-pre-wrap">
                    {comment.content.split(' ').map((word, idx) => {
                      if (word.startsWith('@')) {
                        return (
                          <span key={idx} className="font-semibold text-indigo-400 bg-indigo-950/60 px-1 py-0.5 rounded mr-1">
                            {word}
                          </span>
                        );
                      }
                      return word + ' ';
                    })}
                  </div>

                  {/* Reactions Bar */}
                  <div className="mt-3 pt-2.5 border-t border-[#1f293d] flex flex-wrap items-center gap-1.5">
                    {comment.reactions?.map(reaction => (
                      <button
                        key={reaction.emoji}
                        onClick={() =>
                          toggleCommentReaction(
                            project.id,
                            comment.id,
                            reaction.emoji,
                            selectedAuthorId
                          )
                        }
                        className={`px-2 py-0.5 rounded-full text-[11px] flex items-center gap-1 border transition-colors ${
                          reaction.users.includes(selectedAuthorId)
                            ? 'bg-indigo-950/80 border-indigo-500/60 text-indigo-300'
                            : 'bg-[#182236] border-[#25334c] text-slate-400 hover:text-slate-200'
                        }`}
                        title={`参与者: ${reaction.users.join(', ')}`}
                      >
                        <span>{reaction.emoji}</span>
                        <span className="font-mono text-[10px]">{reaction.count}</span>
                      </button>
                    ))}

                    {/* Quick Reaction Adder */}
                    <div className="flex items-center gap-0.5 ml-1">
                      {COMMON_REACTION_EMOJIS.filter(
                        emoji => !comment.reactions?.some(r => r.emoji === emoji)
                      )
                        .slice(0, 3)
                        .map(emoji => (
                          <button
                            key={emoji}
                            onClick={() =>
                              toggleCommentReaction(
                                project.id,
                                comment.id,
                                emoji,
                                selectedAuthorId
                              )
                            }
                            className="p-1 rounded text-slate-500 hover:text-white hover:bg-[#1e2a3f] text-xs transition-colors"
                            title={`添加 ${emoji}`}
                          >
                            {emoji}
                          </button>
                        ))}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Quick Mention Toolbar */}
        <div className="px-4 py-2 bg-[#141b27] border-t border-[#1f293d] flex items-center gap-1.5 overflow-x-auto text-[11px]">
          <span className="text-slate-400 text-[10px] flex-shrink-0">快捷提醒：</span>
          {teamMembers.map(m => (
            <button
              key={m.id}
              onClick={() => handleInsertMention(m)}
              className="px-2 py-0.5 rounded-full bg-[#1b2436] hover:bg-indigo-900/40 text-slate-300 hover:text-indigo-200 border border-[#27344c] flex-shrink-0 flex items-center gap-1 transition-colors"
            >
              <span className="text-indigo-400">@</span>
              <span>{m.name.split(' ')[0]}</span>
            </button>
          ))}
        </div>

        {/* Bottom Input Area */}
        <div className="p-4 bg-[#141b27] border-t border-[#1f293d] space-y-2.5">
          {/* Persona / Switch Author Selector (Enables testing multi-person discussion) */}
          <div className="flex items-center justify-between text-xs">
            <div className="relative">
              <button
                type="button"
                onClick={() => setShowAuthorPicker(prev => !prev)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#1a2336] hover:bg-[#202c44] border border-[#2b3952] text-slate-300 transition-colors"
              >
                <img
                  src={currentAuthor.avatar}
                  alt={currentAuthor.name}
                  className="w-4 h-4 rounded-full object-cover"
                  referrerPolicy="no-referrer"
                />
                <span className="text-[11px]">当前发言身份：</span>
                <span className="font-semibold text-white">{currentAuthor.name}</span>
                <ChevronDown className="w-3 h-3 text-slate-400" />
              </button>

              {/* Author Dropdown */}
              {showAuthorPicker && (
                <div className="absolute bottom-full left-0 mb-1 w-64 bg-[#161f30] border border-[#2a3852] rounded-xl shadow-xl p-1.5 z-50 space-y-1">
                  <div className="text-[10px] text-slate-400 px-2 py-1 font-medium">
                    选择模拟发言成员（多人协同演练）：
                  </div>
                  {teamMembers.map(member => (
                    <button
                      key={member.id}
                      type="button"
                      onClick={() => {
                        setSelectedAuthorId(member.id);
                        setShowAuthorPicker(false);
                      }}
                      className={`w-full flex items-center justify-between p-2 rounded-lg text-xs transition-colors ${
                        selectedAuthorId === member.id
                          ? 'bg-indigo-600 text-white'
                          : 'hover:bg-[#1e2a40] text-slate-200'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <img
                          src={member.avatar}
                          alt={member.name}
                          className="w-5 h-5 rounded-full object-cover"
                          referrerPolicy="no-referrer"
                        />
                        <div className="text-left">
                          <div className="font-medium leading-none">{member.name}</div>
                          <div className="text-[10px] opacity-70 mt-0.5">{member.role}</div>
                        </div>
                      </div>
                      {selectedAuthorId === member.id && <Check className="w-3.5 h-3.5" />}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Pin comment checkbox */}
            <label className="flex items-center gap-1.5 text-slate-400 hover:text-slate-300 cursor-pointer text-xs select-none">
              <input
                type="checkbox"
                checked={isPinned}
                onChange={e => setIsPinned(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-slate-600 text-indigo-600 focus:ring-indigo-500 bg-[#161f30]"
              />
              <Pin className={`w-3 h-3 ${isPinned ? 'text-indigo-400' : 'text-slate-400'}`} />
              <span>置顶此批注</span>
            </label>
          </div>

          {/* Text Area & Submit */}
          <form onSubmit={handleSendComment} className="flex gap-2">
            <textarea
              id="project-comment-input"
              value={inputContent}
              onChange={e => setInputContent(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  handleSendComment();
                }
              }}
              placeholder="发表针对该项目的协作批注与讨论... 支持 @成员（Ctrl+Enter 发送）"
              rows={2}
              className="flex-1 bg-[#0f1520] border border-[#243048] rounded-xl px-3.5 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none"
            />

            <button
              id="send-project-comment-btn"
              type="submit"
              disabled={!inputContent.trim() || isSubmitting}
              className="px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl flex items-center justify-center transition-colors shadow-sm"
              title="发送批注 (Ctrl+Enter)"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
