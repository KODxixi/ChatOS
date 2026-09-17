import React, { useState } from 'react';
import {
  FolderKanban,
  CheckCircle2,
  Clock,
  MessageSquare,
  FileText,
  CheckSquare,
  Plus,
  Edit2,
  Archive,
  ArrowLeft,
  Users,
  ExternalLink,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { Project, Conversation } from '../../types';
import { ProjectDiscussionDrawer } from '../collaborative/ProjectDiscussionDrawer';

export const ProjectsView: React.FC = () => {
  const {
    projects,
    conversations,
    todos,
    knowledgeItems,
    messages,
    projectComments,
    selectedProject,
    setSelectedProject,
    renameProject,
    archiveProject,
    linkConversationToProject,
    unlinkConversationFromProject,
    jumpToMessage,
    setIsInspectorOpen,
    setSelectedConversation,
    showToast,
  } = useChatOS();

  const [activeTab, setActiveTab] = useState<'all' | 'active' | 'archived'>('active');
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [editingProjectName, setEditingProjectName] = useState<string>('');
  const [isAddConvModalOpen, setIsAddConvModalOpen] = useState<boolean>(false);
  const [discussingProject, setDiscussingProject] = useState<Project | null>(null);

  const filteredProjects = projects.filter(p => {
    if (activeTab === 'active') return p.status === 'active';
    if (activeTab === 'archived') return p.status === 'archived';
    return true;
  });

  const handleStartRename = (proj: Project) => {
    setEditingProjectId(proj.id);
    setEditingProjectName(proj.name);
  };

  const handleSaveRename = (id: string) => {
    if (editingProjectName.trim()) {
      renameProject(id, editingProjectName.trim());
    }
    setEditingProjectId(null);
  };

  // If a project is selected, render the deep project workspace
  if (selectedProject) {
    const linkedConvs = conversations.filter(c => selectedProject.linkedConversationIds.includes(c.id));
    const projectTodos = todos.filter(t => t.projectId === selectedProject.id);
    const projectFiles = knowledgeItems.filter(k => k.linkedProjectId === selectedProject.id);

    // Aggregate filtered project-specific message evidence (Acceptance Criterion 3)
    const projectEvidenceMessages: { convName: string; convId: string; msg: any }[] = [];
    Object.entries(messages).forEach(([convId, msgList]) => {
      const conv = conversations.find(c => c.id === convId);
      msgList.forEach(msg => {
        if (msg.associatedProjectId === selectedProject.id) {
          projectEvidenceMessages.push({
            convName: conv?.name || '未知会话',
            convId,
            msg,
          });
        }
      });
    });

    const unlinkedConversations = conversations.filter(
      c => !selectedProject.linkedConversationIds.includes(c.id)
    );

    return (
      <div className="flex-1 flex flex-col h-full overflow-y-auto p-6 space-y-6">
        {/* Project Detail Header */}
        <div className="flex flex-col gap-4 pb-4 border-b border-[#1e293b]">
          <div className="flex items-center justify-between">
            <button
              id="back-to-projects-btn"
              onClick={() => setSelectedProject(null)}
              className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors font-medium"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>返回全部项目卡片</span>
            </button>

            <div className="flex items-center gap-2">
              <button
                id="open-project-discussion-btn"
                onClick={() => setDiscussingProject(selectedProject)}
                className="flex items-center gap-1.5 px-3 py-1 text-xs rounded-lg bg-indigo-600/20 border border-indigo-500/40 text-indigo-300 hover:bg-indigo-600 hover:text-white transition-colors font-medium"
              >
                <MessageSquare className="w-3.5 h-3.5" />
                <span>协作讨论 ({projectComments[selectedProject.id]?.length || 0})</span>
              </button>
              <button
                onClick={() => handleStartRename(selectedProject)}
                className="flex items-center gap-1 px-2.5 py-1 text-xs rounded bg-[#141b29] border border-[#232f44] text-slate-300 hover:text-white transition-colors"
              >
                <Edit2 className="w-3 h-3" />
                <span>重命名</span>
              </button>
              <button
                onClick={() => {
                  archiveProject(selectedProject.id);
                  setSelectedProject(null);
                }}
                className="flex items-center gap-1 px-2.5 py-1 text-xs rounded bg-[#141b29] border border-[#232f44] text-amber-400 hover:text-amber-300 transition-colors"
              >
                <Archive className="w-3 h-3" />
                <span>归档项目</span>
              </button>
            </div>
          </div>

          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2.5">
                <span className="w-3 h-3 rounded-full" style={{ backgroundColor: selectedProject.color }} />
                <h2 className="text-lg font-bold text-white tracking-wide">{selectedProject.name}</h2>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-950/80 text-indigo-300 border border-indigo-800">
                  {selectedProject.code}
                </span>
                {selectedProject.isAutoGenerated && (
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded bg-blue-950/60 text-blue-300 border border-blue-800/60 flex items-center gap-1">
                    <Sparkles className="w-3 h-3" />
                    智能证据聚合
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-300 mt-2 font-medium">
                进展：{selectedProject.oneLineProgress}
              </p>
              <p className="text-xs text-amber-400 mt-0.5">
                当前行动：{selectedProject.currentAction}
              </p>
            </div>

            {/* Progress Gauge */}
            <div className="w-48 bg-[#121824] p-3 rounded-xl border border-[#212c40]">
              <div className="flex items-center justify-between text-xs mb-1.5">
                <span className="text-slate-400">完成度</span>
                <span className="font-mono font-bold text-indigo-400">{selectedProject.completionPercentage}%</span>
              </div>
              <div className="w-full h-2 bg-[#1b2538] rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-indigo-500 to-emerald-400 rounded-full transition-all"
                  style={{ width: `${selectedProject.completionPercentage}%` }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* 3 Columns in Project: 1. Linked Chats, 2. Filtered Message Evidence, 3. Project Tasks & Files */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Linked Chats Column */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                <MessageSquare className="w-4 h-4 text-indigo-400" />
                <span>关联会话 ({linkedConvs.length})</span>
              </h3>
              <button
                id="add-linked-conv-btn"
                onClick={() => setIsAddConvModalOpen(true)}
                className="flex items-center gap-1 text-[11px] text-indigo-400 hover:text-indigo-300 p-1 rounded hover:bg-[#161d2d]"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>关联会话</span>
              </button>
            </div>

            <div className="space-y-2.5">
              {linkedConvs.map(conv => (
                <div
                  key={conv.id}
                  className="p-3 rounded-lg bg-[#131926] border border-[#202b3e] flex items-center justify-between gap-3 group hover:border-[#2f3d59]"
                >
                  <div
                    onClick={() => {
                      setSelectedConversation(conv);
                      setIsInspectorOpen(true);
                    }}
                    className="flex items-center gap-2.5 min-w-0 cursor-pointer"
                  >
                    <img src={conv.avatar || 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=60'} alt={conv.name} className="w-8 h-8 rounded-lg object-cover" />
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-slate-200 truncate group-hover:text-indigo-300">
                        {conv.name}
                      </p>
                      <p className="text-[10px] text-slate-400 truncate">{conv.isGroup ? '群聊协同' : '私聊对接'}</p>
                    </div>
                  </div>

                  {/* Explicit Unlink Menu to satisfy "移除关联使用明确菜单，避免意外移走其他项目中的同一会话" */}
                  <button
                    onClick={() => unlinkConversationFromProject(selectedProject.id, conv.id)}
                    className="p-1 text-slate-500 hover:text-rose-400 rounded transition-colors text-[10px]"
                    title="仅从本项目解除关联（保留该会话与其他项目的关联）"
                  >
                    解除关联
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Filtered Message Evidence Column */}
          <div className="space-y-4">
            <h3 className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              <span>项目专属消息证据 ({projectEvidenceMessages.length})</span>
            </h3>

            <div className="p-3 bg-[#0f1420] border border-[#1d273a] rounded-xl space-y-3 max-h-[500px] overflow-y-auto">
              {projectEvidenceMessages.length === 0 ? (
                <div className="text-center py-8 text-slate-500 text-xs">
                  暂无匹配到本项目的专属证据消息
                </div>
              ) : (
                projectEvidenceMessages.map((item, idx) => (
                  <div
                    key={idx}
                    onClick={() => jumpToMessage(item.convId, item.msg.id)}
                    className="p-2.5 rounded-lg bg-[#141a27] border border-[#232f44] hover:border-indigo-500/50 cursor-pointer transition-all text-xs"
                  >
                    <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1">
                      <span className="font-semibold text-indigo-300">{item.convName} · {item.msg.senderName}</span>
                      <span className="font-mono">{item.msg.timestamp}</span>
                    </div>
                    <p className="text-slate-200 leading-relaxed">{item.msg.content}</p>
                    {item.msg.hasAttachment && (
                      <div className="mt-2 text-[10px] text-indigo-400 flex items-center gap-1">
                        <FileText className="w-3 h-3" />
                        <span>附件：{item.msg.attachmentName}</span>
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Project Tasks & Files Column */}
          <div className="space-y-4">
            <h3 className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
              <CheckSquare className="w-4 h-4 text-amber-400" />
              <span>项目任务与资料 ({projectTodos.length + projectFiles.length})</span>
            </h3>

            <div className="space-y-3">
              {/* Tasks */}
              <div className="space-y-1.5">
                <div className="text-[11px] font-medium text-slate-400">专属行动任务</div>
                {projectTodos.map(todo => (
                  <div
                    key={todo.id}
                    onClick={() => jumpToMessage(todo.sourceConversationId, todo.sourceMessageId)}
                    className="p-2.5 rounded-lg bg-[#131926] border border-[#212b3e] text-xs flex items-center justify-between gap-2 hover:border-indigo-500/40 cursor-pointer"
                  >
                    <span className="text-slate-200 truncate">{todo.title}</span>
                    <span className="text-[10px] font-mono text-slate-400 flex-shrink-0">{todo.dueDate}</span>
                  </div>
                ))}
              </div>

              {/* Files */}
              <div className="space-y-1.5 pt-2">
                <div className="text-[11px] font-medium text-slate-400">项目收录资料</div>
                {projectFiles.map(file => (
                  <div
                    key={file.id}
                    className="p-2.5 rounded-lg bg-[#131926] border border-[#212b3e] text-xs flex items-center justify-between gap-2"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <FileText className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
                      <span className="text-slate-200 truncate">{file.fileName}</span>
                    </div>
                    <span className="text-[10px] font-mono text-slate-400 flex-shrink-0">{file.fileSize}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Modal: Link Existing Conversation to Project */}
        {isAddConvModalOpen && (
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md bg-[#131926] border border-[#28354c] rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-white">添加关联会话至本项目</h3>
                <button
                  onClick={() => setIsAddConvModalOpen(false)}
                  className="p-1 text-slate-400 hover:text-white"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-2 max-h-60 overflow-y-auto">
                {unlinkedConversations.length === 0 ? (
                  <div className="text-xs text-slate-500 text-center py-4">所有会话已全部关联</div>
                ) : (
                  unlinkedConversations.map(c => (
                    <div
                      key={c.id}
                      className="p-2.5 rounded-lg bg-[#182133] border border-[#273652] flex items-center justify-between"
                    >
                      <span className="text-xs text-slate-200">{c.name}</span>
                      <button
                        onClick={() => {
                          linkConversationToProject(selectedProject.id, c.id);
                          setIsAddConvModalOpen(false);
                        }}
                        className="px-2 py-1 text-[10px] rounded bg-indigo-600 hover:bg-indigo-500 text-white"
                      >
                        添加关联
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* Project Discussion Drawer */}
        {discussingProject && (
          <ProjectDiscussionDrawer
            project={discussingProject}
            onClose={() => setDiscussingProject(null)}
          />
        )}
      </div>
    );
  }

  // Projects Grid Overview
  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto p-6 space-y-6">
      {/* Filter Row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            id="proj-tab-active"
            onClick={() => setActiveTab('active')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'active'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#151c2c] text-slate-300 hover:text-white border border-[#212c42]'
            }`}
          >
            进行中项目 ({projects.filter(p => p.status === 'active').length})
          </button>
          <button
            id="proj-tab-archived"
            onClick={() => setActiveTab('archived')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'archived'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#151c2c] text-slate-300 hover:text-white border border-[#212c42]'
            }`}
          >
            已归档 ({projects.filter(p => p.status === 'archived').length})
          </button>
        </div>

        <span className="text-xs text-slate-400">
          点击项目卡片进入专属多会话协同与证据视图
        </span>
      </div>

      {/* Projects Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {filteredProjects.map(proj => {
          const linkedConvs = conversations.filter(c => proj.linkedConversationIds.includes(c.id));
          const comments = projectComments[proj.id] || [];

          return (
            <div
              key={proj.id}
              id={`proj-card-${proj.id}`}
              onClick={() => setSelectedProject(proj)}
              className="p-5 rounded-xl bg-[#131926] border border-[#212b3e] hover:border-indigo-500/50 cursor-pointer transition-all duration-150 hover:bg-[#161d2d] flex flex-col justify-between space-y-3.5 group"
            >
              {/* Header */}
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: proj.color }} />
                    <h3 className="text-sm font-semibold text-slate-100 truncate group-hover:text-indigo-300">
                      {proj.name}
                    </h3>
                  </div>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#1c2638] text-slate-400 border border-[#2b3a54] flex-shrink-0">
                    {proj.code}
                  </span>
                </div>

                {/* 1-sentence progress */}
                <div className="mt-3 text-xs text-slate-300 line-clamp-2 leading-relaxed">
                  <span className="text-indigo-400 font-medium">进展：</span>
                  {proj.oneLineProgress}
                </div>

                {/* Current Action */}
                <div className="mt-2.5 p-2 rounded-lg bg-[#182033] border border-[#26344f] text-xs">
                  <span className="text-amber-400 font-medium">当前行动：</span>
                  <span className="text-slate-200 line-clamp-1">{proj.currentAction}</span>
                </div>
              </div>

              {/* Collaborative Discussion Bar on Card */}
              <div className="pt-2 border-t border-[#1d273a] flex items-center justify-between gap-2">
                <button
                  id={`proj-discuss-btn-${proj.id}`}
                  type="button"
                  onClick={e => {
                    e.stopPropagation();
                    setDiscussingProject(proj);
                  }}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#182133] hover:bg-indigo-950/70 text-slate-300 hover:text-indigo-200 border border-[#27354e] hover:border-indigo-500/50 transition-colors text-xs"
                >
                  <MessageSquare className="w-3.5 h-3.5 text-indigo-400" />
                  <span>协同讨论</span>
                  <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-indigo-950 text-indigo-300 border border-indigo-800 font-mono">
                    {comments.length}
                  </span>
                </button>

                {comments.length > 0 && (
                  <div className="flex items-center -space-x-1.5 overflow-hidden" title="讨论参与成员">
                    {Array.from(new Set(comments.map(c => c.author.avatar))).slice(0, 3).map((avatar, idx) => (
                      <img
                        key={idx}
                        src={avatar}
                        alt="commenter"
                        className="w-5 h-5 rounded-full object-cover border border-[#131926]"
                        referrerPolicy="no-referrer"
                      />
                    ))}
                  </div>
                )}
              </div>

              {/* Latest Comment Snippet Preview on Card */}
              {comments.length > 0 && (
                <div
                  onClick={e => {
                    e.stopPropagation();
                    setDiscussingProject(proj);
                  }}
                  className="p-2 rounded-lg bg-[#0f1521] border border-[#1d273b] hover:border-indigo-500/40 text-[11px] text-slate-300 flex items-start gap-2 group/comment cursor-pointer transition-colors"
                  title="点击查看讨论批注"
                >
                  <img
                    src={comments[comments.length - 1].author.avatar}
                    alt={comments[comments.length - 1].author.name}
                    className="w-4 h-4 rounded-full object-cover flex-shrink-0 mt-0.5"
                    referrerPolicy="no-referrer"
                  />
                  <div className="min-w-0 flex-1 leading-tight">
                    <span className="font-semibold text-slate-200 mr-1">
                      {comments[comments.length - 1].author.name.split(' ')[0]}:
                    </span>
                    <span className="text-slate-400 truncate inline-block max-w-[170px] align-bottom">
                      {comments[comments.length - 1].content}
                    </span>
                  </div>
                  <span className="text-[10px] text-indigo-400 opacity-0 group-hover/comment:opacity-100 flex-shrink-0 transition-opacity">
                    批注
                  </span>
                </div>
              )}

              {/* Footer: Linked Chats & Progress */}
              <div className="pt-2.5 border-t border-[#1e283b] space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-400 flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5 text-slate-400" />
                    <span>关联会话 ({linkedConvs.length})</span>
                  </span>
                  <span className="font-mono text-indigo-400 font-bold">{proj.completionPercentage}%</span>
                </div>

                <div className="w-full h-1.5 bg-[#1a2336] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-indigo-500 rounded-full"
                    style={{ width: `${proj.completionPercentage}%` }}
                  />
                </div>

                <div className="flex items-center gap-1 overflow-hidden">
                  {linkedConvs.map(c => (
                    <span
                      key={c.id}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-[#192233] text-slate-300 truncate max-w-[100px]"
                    >
                      {c.name}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Project Discussion Drawer for Project Grid */}
      {discussingProject && (
        <ProjectDiscussionDrawer
          project={discussingProject}
          onClose={() => setDiscussingProject(null)}
        />
      )}
    </div>
  );
};
