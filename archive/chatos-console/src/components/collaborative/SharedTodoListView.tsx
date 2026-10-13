import React, { useState } from 'react';
import {
  Users,
  Plus,
  CheckCircle2,
  Circle,
  Calendar,
  AlertCircle,
  FolderKanban,
  Trash2,
  MoreVertical,
  Search,
  Filter,
  Check,
  ShieldCheck,
  UserCheck,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { SharedTodoList, SharedTask, User } from '../../types';
import { CreateSharedListModal } from './CreateSharedListModal';
import { CreateSharedTaskModal } from './CreateSharedTaskModal';

export const SharedTodoListView: React.FC = () => {
  const {
    sharedLists,
    activeSharedListId,
    setActiveSharedListId,
    deleteSharedTodoList,
    toggleCompleteSharedTask,
    deleteSharedTask,
    teamMembers,
    projects,
    setSelectedProject,
    setActiveWorkspace,
  } = useChatOS();

  const [isCreateListModalOpen, setIsCreateListModalOpen] = useState<boolean>(false);
  const [isCreateTaskModalOpen, setIsCreateTaskModalOpen] = useState<boolean>(false);
  const [filterMode, setFilterMode] = useState<'all' | 'assigned_to_me' | 'pending' | 'completed'>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [completingAsUserId, setCompletingAsUserId] = useState<string>('user-joyboy');

  const activeList = sharedLists.find(l => l.id === activeSharedListId) || sharedLists[0];

  if (!activeList) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-12 text-center space-y-4">
        <div className="w-14 h-14 rounded-2xl bg-[#141b28] border border-[#212b3e] flex items-center justify-center text-indigo-400">
          <Users className="w-7 h-7" />
        </div>
        <div>
          <h3 className="text-base font-semibold text-white">暂无跨团队共享清单</h3>
          <p className="text-xs text-slate-400 mt-1 max-w-sm">
            创建共享待办清单，即可邀请 Alex、Sarah 等多位成员参与，并联合指派任务与标记交付进度。
          </p>
        </div>
        <button
          onClick={() => setIsCreateListModalOpen(true)}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm"
        >
          <Plus className="w-4 h-4" />
          <span>创建首个共享清单</span>
        </button>
        {isCreateListModalOpen && (
          <CreateSharedListModal onClose={() => setIsCreateListModalOpen(false)} />
        )}
      </div>
    );
  }

  const associatedProject = projects.find(p => p.id === activeList.associatedProjectId);
  const completedTasksCount = activeList.tasks.filter(t => t.isCompleted).length;
  const totalTasksCount = activeList.tasks.length;
  const progressPercentage =
    totalTasksCount > 0 ? Math.round((completedTasksCount / totalTasksCount) * 100) : 0;

  const filteredTasks = activeList.tasks.filter(task => {
    // Text search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchTitle = task.title.toLowerCase().includes(q);
      const matchDesc = task.description?.toLowerCase().includes(q);
      const matchAssignee = task.assignees.some(a => a.name.toLowerCase().includes(q));
      if (!matchTitle && !matchDesc && !matchAssignee) return false;
    }

    // Tab filter
    if (filterMode === 'assigned_to_me') {
      return task.assignees.some(a => a.id === 'user-joyboy' || a.isSelf);
    }
    if (filterMode === 'pending') {
      return !task.isCompleted;
    }
    if (filterMode === 'completed') {
      return task.isCompleted;
    }
    return true;
  });

  const completingUser =
    teamMembers.find(m => m.id === completingAsUserId) || teamMembers[0];

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto p-6 space-y-6">
      {/* Top Lists Switcher Bar */}
      <div className="flex items-center justify-between gap-4 flex-wrap pb-2 border-b border-[#1d273a]">
        {/* List Chips */}
        <div className="flex items-center gap-2 overflow-x-auto pb-1 max-w-full">
          {sharedLists.map(list => {
            const isSelected = list.id === activeList.id;
            const completedCount = list.tasks.filter(t => t.isCompleted).length;

            return (
              <button
                key={list.id}
                id={`select-shared-list-${list.id}`}
                onClick={() => setActiveSharedListId(list.id)}
                className={`px-3 py-1.5 rounded-xl text-xs font-medium flex items-center gap-2 transition-all flex-shrink-0 border ${
                  isSelected
                    ? 'bg-indigo-600 text-white border-indigo-500 shadow-md shadow-indigo-950/40'
                    : 'bg-[#131926] text-slate-300 hover:text-white border-[#212c40] hover:border-slate-600'
                }`}
              >
                <Users className="w-3.5 h-3.5" />
                <span className="truncate max-w-[180px]">{list.title}</span>
                <span
                  className={`text-[10px] px-1.5 py-0.2 rounded-full font-mono ${
                    isSelected ? 'bg-indigo-700 text-indigo-100' : 'bg-[#1e283b] text-slate-400'
                  }`}
                >
                  {completedCount}/{list.tasks.length}
                </span>
              </button>
            );
          })}
        </div>

        {/* New Shared List Button */}
        <button
          id="open-create-shared-list-btn"
          onClick={() => setIsCreateListModalOpen(true)}
          className="px-3 py-1.5 rounded-xl text-xs font-semibold bg-[#182133] hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 hover:border-indigo-400 flex items-center gap-1.5 transition-colors flex-shrink-0"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>新建共享待办清单</span>
        </button>
      </div>

      {/* Active List Overview Hero Card */}
      <div className="p-5 rounded-2xl bg-[#121824] border border-[#212b3e] shadow-lg space-y-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2.5">
              <h2 className="text-base font-bold text-white">{activeList.title}</h2>
              {associatedProject && (
                <button
                  onClick={() => {
                    setSelectedProject(associatedProject);
                    setActiveWorkspace('projects');
                  }}
                  className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-indigo-950/80 text-indigo-300 border border-indigo-700/50 hover:bg-indigo-900 transition-colors"
                >
                  <FolderKanban className="w-3 h-3" />
                  <span>关联项目：{associatedProject.name}</span>
                </button>
              )}
            </div>
            <p className="text-xs text-slate-300 max-w-2xl leading-relaxed">
              {activeList.description || '由团队多成员共同推进的协作清单。'}
            </p>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2">
            <button
              id="open-create-shared-task-btn"
              onClick={() => setIsCreateTaskModalOpen(true)}
              className="px-3.5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition-colors"
            >
              <Plus className="w-4 h-4" />
              <span>添加协同任务</span>
            </button>

            {sharedLists.length > 1 && (
              <button
                onClick={() => deleteSharedTodoList(activeList.id)}
                className="p-2 text-slate-500 hover:text-rose-400 rounded-xl hover:bg-[#1a2336] transition-colors"
                title="删除当前共享清单"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        {/* Collaborators Stack & Progress Bar */}
        <div className="pt-3 border-t border-[#1e293b] flex items-center justify-between gap-6 flex-wrap">
          {/* Collaborator Avatars */}
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400 flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-slate-400" />
              <span>协作成员 ({activeList.collaborators.length}人)：</span>
            </span>

            <div className="flex items-center -space-x-2 overflow-hidden">
              {activeList.collaborators.map(c => (
                <img
                  key={c.id}
                  src={c.avatar}
                  alt={c.name}
                  title={`${c.name} (${c.role})`}
                  className="w-7 h-7 rounded-full object-cover border-2 border-[#121824] hover:scale-110 hover:z-10 transition-transform"
                  referrerPolicy="no-referrer"
                />
              ))}
            </div>

            <div className="text-[11px] text-slate-400 hidden sm:block">
              {activeList.collaborators.map(c => c.name.split(' ')[0]).join(', ')}
            </div>
          </div>

          {/* Progress metric */}
          <div className="flex items-center gap-3 flex-1 max-w-xs">
            <div className="flex-1">
              <div className="flex items-center justify-between text-[11px] mb-1">
                <span className="text-slate-400">完成度</span>
                <span className="font-mono font-bold text-indigo-400">
                  {completedTasksCount}/{totalTasksCount} ({progressPercentage}%)
                </span>
              </div>
              <div className="w-full h-1.5 bg-[#1b2538] rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-500 rounded-full transition-all duration-300"
                  style={{ width: `${progressPercentage}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Control & Filter Toolbar */}
      <div className="flex items-center justify-between gap-4 flex-wrap bg-[#121824] p-3 rounded-xl border border-[#212b3e]">
        {/* Filter Pills */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setFilterMode('all')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              filterMode === 'all'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#182133] text-slate-400 hover:text-slate-200'
            }`}
          >
            全部任务 ({activeList.tasks.length})
          </button>
          <button
            onClick={() => setFilterMode('assigned_to_me')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              filterMode === 'assigned_to_me'
                ? 'bg-indigo-600 text-white'
                : 'bg-[#182133] text-slate-400 hover:text-slate-200'
            }`}
          >
            指派给我 ({activeList.tasks.filter(t => t.assignees.some(a => a.id === 'user-joyboy' || a.isSelf)).length})
          </button>
          <button
            onClick={() => setFilterMode('pending')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              filterMode === 'pending'
                ? 'bg-amber-600 text-white'
                : 'bg-[#182133] text-slate-400 hover:text-slate-200'
            }`}
          >
            未完成 ({activeList.tasks.filter(t => !t.isCompleted).length})
          </button>
          <button
            onClick={() => setFilterMode('completed')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              filterMode === 'completed'
                ? 'bg-emerald-600 text-white'
                : 'bg-[#182133] text-slate-400 hover:text-slate-200'
            }`}
          >
            已完成 ({completedTasksCount})
          </button>
        </div>

        {/* Search & Person-Completer-Simulator */}
        <div className="flex items-center gap-3">
          {/* Persona selector for marking completion */}
          <div className="flex items-center gap-1.5 text-xs">
            <span className="text-slate-400 text-[11px] hidden sm:inline">标记完成操作者：</span>
            <select
              value={completingAsUserId}
              onChange={e => setCompletingAsUserId(e.target.value)}
              className="bg-[#182133] border border-[#273650] rounded-lg px-2.5 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
              title="模拟不同成员勾选完成任务"
            >
              {teamMembers.map(m => (
                <option key={m.id} value={m.id}>
                  {m.name} {m.id === 'user-joyboy' ? '(本人)' : ''}
                </option>
              ))}
            </select>
          </div>

          {/* Search box */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="搜索协同任务..."
              className="bg-[#182133] border border-[#273650] rounded-lg pl-8 pr-3 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 w-36 sm:w-48"
            />
          </div>
        </div>
      </div>

      {/* Tasks List */}
      <div className="space-y-3">
        {filteredTasks.length === 0 ? (
          <div className="p-12 text-center border border-dashed border-[#232f44] rounded-2xl bg-[#121824]/50 space-y-2">
            <p className="text-sm font-medium text-slate-300">未找到符合条件的协同任务</p>
            <p className="text-xs text-slate-500">
              点击上方「添加协同任务」，为团队成员分派联合攻坚行动项。
            </p>
          </div>
        ) : (
          filteredTasks.map(task => {
            const isHighPriority = task.priority === 'high';
            const isMediumPriority = task.priority === 'medium';

            return (
              <div
                key={task.id}
                id={`shared-task-${task.id}`}
                className={`p-4 rounded-xl border transition-all duration-150 flex flex-col sm:flex-row sm:items-center justify-between gap-4 group ${
                  task.isCompleted
                    ? 'bg-[#111722]/80 border-[#1c2638] opacity-80'
                    : 'bg-[#131926] border-[#212b3e] hover:border-indigo-500/40 hover:bg-[#151d2d]'
                }`}
              >
                {/* Left: Checkbox + Title + Description + CompletedBy Badge */}
                <div className="flex items-start gap-3.5 min-w-0 flex-1">
                  {/* Complete Checkbox */}
                  <button
                    id={`toggle-task-${task.id}`}
                    onClick={() =>
                      toggleCompleteSharedTask(activeList.id, task.id, completingAsUserId)
                    }
                    className={`mt-0.5 w-5 h-5 rounded-md flex items-center justify-center transition-all flex-shrink-0 ${
                      task.isCompleted
                        ? 'bg-emerald-600 text-white hover:bg-emerald-500'
                        : 'border border-slate-500 hover:border-indigo-400 text-transparent hover:text-indigo-400/50'
                    }`}
                    title={task.isCompleted ? '取消完成' : `由 ${completingUser.name} 标记完成`}
                  >
                    <Check className="w-3.5 h-3.5 stroke-[2.5]" />
                  </button>

                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={`text-xs font-semibold ${
                          task.isCompleted
                            ? 'line-through text-slate-400'
                            : 'text-slate-100 group-hover:text-indigo-200'
                        }`}
                      >
                        {task.title}
                      </span>

                      {/* Priority Tag */}
                      <span
                        className={`text-[10px] px-1.5 py-0.2 rounded font-medium ${
                          isHighPriority
                            ? 'bg-rose-950/70 text-rose-300 border border-rose-800/60'
                            : isMediumPriority
                            ? 'bg-amber-950/70 text-amber-300 border border-amber-800/60'
                            : 'bg-emerald-950/70 text-emerald-300 border border-emerald-800/60'
                        }`}
                      >
                        {isHighPriority ? '高优先级' : isMediumPriority ? '中优先级' : '低优先级'}
                      </span>
                    </div>

                    {task.description && (
                      <p className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">
                        {task.description}
                      </p>
                    )}

                    {/* Completion Info Badge */}
                    {task.isCompleted && (
                      <div className="flex items-center gap-1.5 pt-1 text-[10px] text-emerald-400 font-medium">
                        <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
                        <span>
                          已由 {task.completedBy?.name || '协作成员'} 于 {task.completedAt || '刚刚'} 标记完成
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Right: Assignees Avatars + Due Date + Delete Button */}
                <div className="flex items-center justify-between sm:justify-end gap-4 flex-shrink-0 pt-2 sm:pt-0 border-t sm:border-t-0 border-[#1c2638]">
                  {/* Multi-Assignee Avatars Group */}
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-slate-400 hidden md:inline">指派：</span>
                    <div className="flex items-center -space-x-2">
                      {task.assignees.map(assignee => (
                        <img
                          key={assignee.id}
                          src={assignee.avatar}
                          alt={assignee.name}
                          title={`负责人: ${assignee.name} (${assignee.role})`}
                          className="w-6 h-6 rounded-full object-cover border-2 border-[#131926] hover:scale-110 hover:z-10 transition-transform"
                          referrerPolicy="no-referrer"
                        />
                      ))}
                    </div>
                    <div className="text-[11px] text-slate-300">
                      {task.assignees.length === 1
                        ? task.assignees[0].name.split(' ')[0]
                        : `${task.assignees[0].name.split(' ')[0]} 等${task.assignees.length}人`}
                    </div>
                  </div>

                  {/* Due Date */}
                  <div className="flex items-center gap-1 text-[11px] font-mono text-slate-400 px-2 py-1 rounded bg-[#182133] border border-[#26354e]">
                    <Calendar className="w-3 h-3 text-slate-400" />
                    <span>{task.dueDate}</span>
                  </div>

                  {/* Delete Task */}
                  <button
                    onClick={() => deleteSharedTask(activeList.id, task.id)}
                    className="p-1.5 text-slate-500 hover:text-rose-400 rounded-lg hover:bg-[#1c273a] transition-colors"
                    title="删除任务"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Modals */}
      {isCreateListModalOpen && (
        <CreateSharedListModal onClose={() => setIsCreateListModalOpen(false)} />
      )}
      {isCreateTaskModalOpen && (
        <CreateSharedTaskModal
          listId={activeList.id}
          listTitle={activeList.title}
          onClose={() => setIsCreateTaskModalOpen(false)}
        />
      )}
    </div>
  );
};
