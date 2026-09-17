import React, { useState } from 'react';
import { X, Users, Check, FolderKanban } from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';

interface CreateSharedListModalProps {
  onClose: () => void;
}

export const CreateSharedListModal: React.FC<CreateSharedListModalProps> = ({ onClose }) => {
  const { teamMembers, projects, createSharedTodoList } = useChatOS();

  const [title, setTitle] = useState<string>('');
  const [description, setDescription] = useState<string>('');
  const [selectedCollaboratorIds, setSelectedCollaboratorIds] = useState<string[]>([
    'user-joyboy',
    'user-alex',
    'user-sarah',
  ]);
  const [associatedProjectId, setAssociatedProjectId] = useState<string>('proj-chatos-rebuild');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const toggleCollaborator = (id: string) => {
    if (id === 'user-joyboy') return; // Always keep JoyBoy (self)
    setSelectedCollaboratorIds(prev =>
      prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await createSharedTodoList(
        title.trim(),
        description.trim(),
        selectedCollaboratorIds,
        associatedProjectId || undefined
      );
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fadeIn">
      <div
        id="create-shared-list-modal"
        className="w-full max-w-lg bg-[#121824] border border-[#232f44] rounded-2xl p-6 space-y-5 shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#1e293b] pb-3.5">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
              <Users className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">新建跨团队共享待办清单</h3>
              <p className="text-xs text-slate-400">邀请多位团队成员协同攻坚与指派任务</p>
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
              清单名称 <span className="text-rose-400">*</span>
            </label>
            <input
              id="shared-list-title-input"
              type="text"
              required
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="例如：双周跨团队发布联调清单、Client-X 专属待办..."
              className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3.5 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              清单描述与协同目标
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              placeholder="简述协同背景、排期节奏或各角色关注重点..."
              className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3.5 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none"
            />
          </div>

          {/* Associated Project */}
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              关联项目（可选）
            </label>
            <div className="relative">
              <select
                value={associatedProjectId}
                onChange={e => setAssociatedProjectId(e.target.value)}
                className="w-full bg-[#0d131d] border border-[#232f44] rounded-xl px-3.5 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              >
                <option value="">不关联特定项目</option>
                {projects.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.code})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Collaborators Picker */}
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">
              协同成员（多选）· 已选 {selectedCollaboratorIds.length} 人
            </label>
            <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto p-1 bg-[#0d131d] rounded-xl border border-[#212c40]">
              {teamMembers.map(member => {
                const isSelected = selectedCollaboratorIds.includes(member.id);
                const isSelf = member.id === 'user-joyboy';

                return (
                  <button
                    key={member.id}
                    type="button"
                    onClick={() => toggleCollaborator(member.id)}
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
                        <div className="text-[11px] font-medium truncate flex items-center gap-1">
                          <span>{member.name}</span>
                          {isSelf && (
                            <span className="text-[9px] px-1 py-0.2 rounded bg-indigo-900 text-indigo-200">
                              本人
                            </span>
                          )}
                        </div>
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
              id="submit-create-shared-list-btn"
              type="submit"
              disabled={!title.trim() || isSubmitting}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl text-xs font-semibold transition-colors shadow-sm"
            >
              {isSubmitting ? '创建中...' : '立即创建清单'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
