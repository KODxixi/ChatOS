import React, { useState } from 'react';
import { X, CheckSquare, FolderKanban, Plus } from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';
import { TodoRole } from '../types';

interface CreateModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const CreateModal: React.FC<CreateModalProps> = ({ isOpen, onClose }) => {
  const { activeWorkspace, createTodo, createProject, conversations } = useChatOS();

  const [mode, setMode] = useState<'todo' | 'project'>(
    activeWorkspace === 'projects' ? 'project' : 'todo'
  );

  // Todo Form State
  const [todoTitle, setTodoTitle] = useState('');
  const [todoDesc, setTodoDesc] = useState('');
  const [todoDueDate, setTodoDueDate] = useState('2026-09-18 18:00');
  const [todoRole, setTodoRole] = useState<TodoRole>('i_am_responsible');

  // Project Form State
  const [projectName, setProjectName] = useState('');
  const [projectAction, setProjectAction] = useState('');
  const [selectedConvIds, setSelectedConvIds] = useState<string[]>([]);

  if (!isOpen) return null;

  const handleSubmitTodo = (e: React.FormEvent) => {
    e.preventDefault();
    if (!todoTitle.trim()) return;
    createTodo({
      title: todoTitle.trim(),
      description: todoDesc.trim(),
      dueDate: todoDueDate.trim(),
      assigneeRole: todoRole,
      status: 'pending',
    });
    onClose();
  };

  const handleSubmitProject = (e: React.FormEvent) => {
    e.preventDefault();
    if (!projectName.trim()) return;
    createProject(projectName.trim(), projectAction.trim(), selectedConvIds);
    onClose();
  };

  const toggleSelectConv = (id: string) => {
    setSelectedConvIds(prev =>
      prev.includes(id) ? prev.filter(c => c !== id) : [...prev, id]
    );
  };

  return (
    <div
      className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="w-full max-w-lg bg-[#111724] border border-[#27364f] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
      >
        {/* Header Tabs */}
        <div className="px-6 py-4 border-b border-[#1e293b] bg-[#141c2c] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMode('todo')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                mode === 'todo'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <CheckSquare className="w-4 h-4" />
              <span>新建待办</span>
            </button>

            <button
              onClick={() => setMode('project')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                mode === 'project'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <FolderKanban className="w-4 h-4" />
              <span>新建项目</span>
            </button>
          </div>

          <button onClick={onClose} className="p-1 text-slate-400 hover:text-white rounded">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6">
          {mode === 'todo' ? (
            <form onSubmit={handleSubmitTodo} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  待办名称 *
                </label>
                <input
                  type="text"
                  required
                  value={todoTitle}
                  onChange={e => setTodoTitle(e.target.value)}
                  placeholder="如：给 Alex 回复 API 灰度发布方案"
                  className="w-full bg-[#161f30] border border-[#26354f] rounded-lg px-3 py-2 text-xs text-white placeholder:text-slate-500 outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  补充说明或背景
                </label>
                <textarea
                  rows={2}
                  value={todoDesc}
                  onChange={e => setTodoDesc(e.target.value)}
                  placeholder="可简述关联背景..."
                  className="w-full bg-[#161f30] border border-[#26354f] rounded-lg px-3 py-2 text-xs text-white placeholder:text-slate-500 outline-none focus:border-indigo-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    责任归属
                  </label>
                  <select
                    value={todoRole}
                    onChange={e => setTodoRole(e.target.value as TodoRole)}
                    className="w-full bg-[#161f30] border border-[#26354f] rounded-lg px-3 py-2 text-xs text-slate-200 outline-none cursor-pointer"
                  >
                    <option value="i_am_responsible">我负责 (JoyBoy 牵头)</option>
                    <option value="i_promised">我承诺 (对外承诺交付)</option>
                    <option value="i_need_follow_up">我需跟进 (监督对方进展)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    截止期限
                  </label>
                  <input
                    type="text"
                    value={todoDueDate}
                    onChange={e => setTodoDueDate(e.target.value)}
                    placeholder="2026-09-18 18:00"
                    className="w-full bg-[#161f30] border border-[#26354f] rounded-lg px-3 py-2 text-xs text-white outline-none focus:border-indigo-500"
                  />
                </div>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 rounded-lg text-xs text-slate-400 hover:text-white hover:bg-[#161f30]"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-lg text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-medium"
                >
                  创建待办
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleSubmitProject} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  项目名称 *
                </label>
                <input
                  type="text"
                  required
                  value={projectName}
                  onChange={e => setProjectName(e.target.value)}
                  placeholder="如：Q4 商业化增长规划"
                  className="w-full bg-[#161f30] border border-[#26354f] rounded-lg px-3 py-2 text-xs text-white placeholder:text-slate-500 outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  首期关键行动
                </label>
                <input
                  type="text"
                  value={projectAction}
                  onChange={e => setProjectAction(e.target.value)}
                  placeholder="如：汇总核心伙伴商业诉求并确认排期"
                  className="w-full bg-[#161f30] border border-[#26354f] rounded-lg px-3 py-2 text-xs text-white placeholder:text-slate-500 outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  初始关联会话 (多选)
                </label>
                <div className="space-y-1.5 max-h-40 overflow-y-auto p-2 bg-[#0e1420] rounded-lg border border-[#1e283b]">
                  {conversations.map(c => {
                    const isSelected = selectedConvIds.includes(c.id);
                    return (
                      <div
                        key={c.id}
                        onClick={() => toggleSelectConv(c.id)}
                        className={`p-2 rounded-md text-xs flex items-center justify-between cursor-pointer transition-colors ${
                          isSelected
                            ? 'bg-indigo-600/30 text-white border border-indigo-500/50'
                            : 'text-slate-300 hover:bg-[#161f30]'
                        }`}
                      >
                        <span>{c.name}</span>
                        {isSelected && <span className="text-[10px] text-indigo-300 font-bold">已选</span>}
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 rounded-lg text-xs text-slate-400 hover:text-white hover:bg-[#161f30]"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-lg text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-medium"
                >
                  创建项目
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
