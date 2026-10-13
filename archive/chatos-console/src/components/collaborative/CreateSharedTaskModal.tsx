import React, { useState } from 'react';
import { X, CheckSquare, Users, Calendar, AlertCircle, Check } from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { User } from '../../types';

interface CreateSharedTaskModalProps {
  listId: string;
  listTitle: string;
  onClose: () => void;
}

export const CreateSharedTaskModal: React.FC<CreateSharedTaskModalProps> = ({
  listId,
  listTitle,
  onClose,
}) => {
  const { teamMembers, addSharedTask } = useChatOS();

  const [title, setTitle] = useState<string>('');
  const [description, setDescription] = useState<string>('');
  const [selectedAssigneeIds, setSelectedAssigneeIds] = useState<string[]>([
    'user-joyboy',
    'user-alex',
  ]);
  const [dueDate, setDueDate] = useState<string>('2026-09-17 18:00');
  const [priority, setPriority] = useState<'high' | 'medium' | 'low'>('high');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const toggleAssignee = (id: string) => {
    setSelectedAssigneeIds(prev =>
      prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await addSharedTask(listId, {
        title: title.trim(),
        description: description.trim(),
        assigneeIds: selectedAssigneeIds.length > 0 ? selectedAssigneeIds : ['user-joyboy'],
        dueDate,
        priority,
      });
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fadeIn">
      <div
        id="create-shared-task-modal"
        className="w-full max-w-lg bg-[#121824] border border-[#232f44] rounded-2xl p-6 space-y-5 shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#1e293b] pb-3.5">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
              <CheckSquare className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">添加协同任务</h3>
              <p className="text-xs text-slate-400">
                所属清单：<span className="text-indigo-300 font-medium">{listTitle}</span>
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-white rounded-lg hover:bg-[#1a2336] transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Title */}
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              任务行动内容 <span className="text-rose-400">*</span>
            </label>
            <input
              id="shared-task-title-input"
              type="text"
              required
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="明确行动项，例如：多租户沙箱环境搭建与合规报告签署..."
              className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3.5 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              详细说明与验收要求
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              placeholder="补充上下文细节、相关文档连接或验收清单..."
              className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3.5 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none"
            />
          </div>

          {/* Multi-Assignee Selection */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs font-medium text-slate-300">
                指派负责人（支持多人联合攻坚）
              </label>
              <span className="text-[11px] text-indigo-400 font-medium">
                已选 {selectedAssigneeIds.length} 人
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto p-1 bg-[#0d131d] rounded-xl border border-[#212c40]">
              {teamMembers.map(member => {
                const isSelected = selectedAssigneeIds.includes(member.id);

                return (
                  <button
                    key={member.id}
                    type="button"
                    onClick={() => toggleAssignee(member.id)}
                    className={`p-2 rounded-lg flex items-center justify-between border text-left transition-all ${
                      isSelected
                        ? 'bg-indigo-950/70 border-indigo-600/60 text-white'
                        : 'bg-[#131a26] border-[#1e2838] text-slate-300 hover:bg-[#182130]'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <img
                        src={member.avatar}
                        alt={member.name}
                        className="w-6 h-6 rounded-full object-cover flex-shrink-0"
                        referrerPolicy="no-referrer"
                      />
                      <div className="min-w-0">
                        <div className="text-[11px] font-medium truncate">{member.name}</div>
                        <div className="text-[9px] text-slate-400 truncate">{member.role}</div>
                      </div>
                    </div>

                    <div
                      className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 ml-1.5 ${
                        isSelected ? 'bg-indigo-600 text-white' : 'border border-slate-600'
                      }`}
                    >
                      {isSelected && <Check className="w-3 h-3" />}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Priority & Due Date */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">优先级</label>
              <select
                value={priority}
                onChange={e => setPriority(e.target.value as any)}
                className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                <option value="high">🔴 高优先级 (High)</option>
                <option value="medium">🟡 中优先级 (Medium)</option>
                <option value="low">🟢 低优先级 (Low)</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1.5">截止期限</label>
              <input
                type="text"
                value={dueDate}
                onChange={e => setDueDate(e.target.value)}
                placeholder="2026-09-17 18:00"
                className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          {/* Actions */}
          <div className="pt-3 border-t border-[#1e293b] flex items-center justify-end gap-2.5">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-xs text-slate-400 hover:text-white hover:bg-[#1a2336] transition-colors"
            >
              取消
            </button>
            <button
              id="submit-create-shared-task-btn"
              type="submit"
              disabled={!title.trim() || isSubmitting}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl text-xs font-semibold transition-colors shadow-sm"
            >
              {isSubmitting ? '添加中...' : '确认添加协同任务'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
