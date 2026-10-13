import React, { useState } from 'react';
import {
  CheckCircle2,
  Clock,
  Calendar,
  AlertCircle,
  ExternalLink,
  MoreVertical,
  RotateCcw,
  EyeOff,
  UserCheck,
  Flag,
  ChevronRight,
  ShieldCheck,
  Edit2,
  Trash2,
  Users,
  LayoutGrid,
} from 'lucide-react';
import { useChatOS } from '../../context/ChatOSContext';
import { TodoItem, TodoStatus, TodoRole } from '../../types';
import { SharedTodoListView } from '../collaborative/SharedTodoListView';

export const TodosView: React.FC = () => {
  const {
    todos,
    sharedLists,
    updateTodoStatus,
    updateTodoDueDate,
    toggleIgnoreTodo,
    deleteTodo,
    jumpToMessage,
    setSelectedTodo,
    setIsInspectorOpen,
    showToast,
  } = useChatOS();

  const [viewTab, setViewTab] = useState<'personal_kanban' | 'collaborative_lists'>('personal_kanban');
  const [roleFilter, setRoleFilter] = useState<'all' | 'i_am_responsible' | 'i_promised' | 'i_need_follow_up' | 'other'>('all');
  const [showIgnored, setShowIgnored] = useState<boolean>(false);
  const [editingDueDateId, setEditingDueDateId] = useState<string | null>(null);
  const [newDueDateValue, setNewDueDateValue] = useState<string>('');

  // Drag-and-drop state
  const [draggedTodoId, setDraggedTodoId] = useState<string | null>(null);

  const columns: { id: TodoStatus; label: string; description: string; badgeColor: string }[] = [
    {
      id: 'draft',
      label: '草稿箱',
      description: '意向存疑、归属或期限待核实',
      badgeColor: 'bg-slate-700/50 text-slate-300 border-slate-600/40',
    },
    {
      id: 'pending',
      label: '待处理',
      description: '行动明确，等待着手排期',
      badgeColor: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
    },
    {
      id: 'in_progress',
      label: '进行中',
      description: '当前正在攻坚或积极跟进中',
      badgeColor: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
    },
    {
      id: 'completed',
      label: '已完成',
      description: '已达成或交付，受保护绝不复活',
      badgeColor: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    },
  ];

  const filteredTodos = todos.filter(t => {
    if (!showIgnored && t.isIgnored) return false;
    if (showIgnored && !t.isIgnored) return false;
    if (roleFilter !== 'all' && t.assigneeRole !== roleFilter) return false;
    return true;
  });

  const getRoleBadge = (role: TodoRole) => {
    switch (role) {
      case 'i_am_responsible':
        return { text: '我负责', color: 'bg-indigo-950/70 text-indigo-300 border-indigo-700/60' };
      case 'i_promised':
        return { text: '我承诺', color: 'bg-amber-950/70 text-amber-300 border-amber-700/60' };
      case 'i_need_follow_up':
        return { text: '我需跟进', color: 'bg-cyan-950/70 text-cyan-300 border-cyan-700/60' };
      case 'other':
        return { text: '他人事项', color: 'bg-slate-800 text-slate-400 border-slate-700' };
    }
  };

  const handleDragStart = (e: React.DragEvent, id: string) => {
    e.dataTransfer.setData('text/plain', id);
    setDraggedTodoId(id);
  };

  const handleDrop = (e: React.DragEvent, targetStatus: TodoStatus) => {
    e.preventDefault();
    const id = e.dataTransfer.getData('text/plain') || draggedTodoId;
    if (id) {
      updateTodoStatus(id, targetStatus);
    }
    setDraggedTodoId(null);
  };

  const handleSaveDueDate = (id: string) => {
    if (newDueDateValue.trim()) {
      updateTodoDueDate(id, newDueDateValue.trim());
    }
    setEditingDueDateId(null);
  };

  const totalSharedTasks = sharedLists.reduce((acc, l) => acc + l.tasks.length, 0);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      {/* Top Workspace Mode Selector */}
      <div className="chatos-view-tabbar-wrap px-6 pt-4 pb-2 flex-shrink-0">
        <div className="chatos-view-tabbar" role="tablist" aria-label="待办视图">
          <button
            id="todo-view-tab-personal"
            role="tab"
            onClick={() => setViewTab('personal_kanban')}
            aria-selected={viewTab === 'personal_kanban'}
            className={`chatos-view-tab ${
              viewTab === 'personal_kanban'
                ? 'is-active'
                : ''
            }`}
          >
            <LayoutGrid className="w-3.5 h-3.5" />
            <span>我的</span>
            <span className="chatos-view-tab-count">{todos.filter(t => !t.isIgnored).length}</span>
          </button>

          <button
            id="todo-view-tab-shared"
            role="tab"
            onClick={() => setViewTab('collaborative_lists')}
            aria-selected={viewTab === 'collaborative_lists'}
            className={`chatos-view-tab ${
              viewTab === 'collaborative_lists'
                ? 'is-active'
                : ''
            }`}
          >
            <Users className="w-3.5 h-3.5" />
            <span>协作</span>
            <span className="chatos-view-tab-count">{totalSharedTasks}</span>
          </button>
        </div>
      </div>

      {/* Main View Content */}
      {viewTab === 'collaborative_lists' ? (
        <SharedTodoListView />
      ) : (
        <div className="flex-1 flex flex-col h-full overflow-hidden p-6 space-y-5">
          {/* Top Filter Bar */}
          <div className="chatos-filter-bar flex items-center justify-between gap-3">
        <label className="chatos-filter-field">
          <span>责任范围</span>
          <select
            id="todo-role-select"
            value={roleFilter}
            onChange={event => {
              setRoleFilter(event.target.value as typeof roleFilter);
              setShowIgnored(false);
            }}
          >
            <option value="all">全部事项 · {todos.filter(t => !t.isIgnored).length}</option>
            <option value="i_am_responsible">我负责 · {todos.filter(t => t.assigneeRole === 'i_am_responsible' && !t.isIgnored).length}</option>
            <option value="i_promised">我承诺 · {todos.filter(t => t.assigneeRole === 'i_promised' && !t.isIgnored).length}</option>
            <option value="i_need_follow_up">我需跟进 · {todos.filter(t => t.assigneeRole === 'i_need_follow_up' && !t.isIgnored).length}</option>
            <option value="other">他人事项</option>
          </select>
        </label>

        <button
          id="todo-toggle-ignored"
          onClick={() => setShowIgnored(!showIgnored)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
            showIgnored
              ? 'bg-amber-950/60 text-amber-300 border-amber-800/60'
              : 'bg-[#151c2c] text-slate-400 hover:text-slate-200 border-[#212c42]'
          }`}
        >
          <EyeOff className="w-3.5 h-3.5" />
          <span>{showIgnored ? '返回正常待办看板' : `已忽略箱 (${todos.filter(t => t.isIgnored).length})`}</span>
        </button>
      </div>

      {/* 4-Column Kanban Board */}
      <div className="flex-1 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 overflow-hidden">
        {columns.map(col => {
          const colTodos = filteredTodos.filter(t => t.status === col.id);

          return (
            <div
              key={col.id}
              id={`kanban-col-${col.id}`}
              onDragOver={e => e.preventDefault()}
              onDrop={e => handleDrop(e, col.id)}
              className="flex flex-col h-full rounded-xl bg-[#0f1420] border border-[#1e2738] overflow-hidden"
            >
              {/* Column Header */}
              <div className="p-3.5 border-b border-[#1c2536] bg-[#121824] flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-xs font-semibold text-slate-200">{col.label}</h3>
                    <span className={`text-[10px] font-mono px-1.5 py-0.2 rounded font-bold border ${col.badgeColor}`}>
                      {colTodos.length}
                    </span>
                  </div>
                  <p className="text-[10px] text-slate-400 mt-0.5">{col.description}</p>
                </div>
              </div>

              {/* Column Cards Stream (Scrollable) */}
              <div className="flex-1 overflow-y-auto p-2.5 space-y-3">
                {colTodos.length === 0 ? (
                  <div className="h-32 flex flex-col items-center justify-center text-slate-500 text-xs border border-dashed border-[#222d40] rounded-lg">
                    <span>暂无此类任务</span>
                  </div>
                ) : (
                  colTodos.map(todo => {
                    const roleBadge = getRoleBadge(todo.assigneeRole);

                    return (
                      <div
                        key={todo.id}
                        id={`todo-item-${todo.id}`}
                        style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 240px' }}
                        draggable
                        onDragStart={e => handleDragStart(e, todo.id)}
                        onClick={() => {
                          setSelectedTodo(todo);
                          setIsInspectorOpen(true);
                        }}
                        className="group relative p-3 rounded-lg bg-[#141a27] border border-[#232f44] hover:border-indigo-500/50 transition-all cursor-grab active:cursor-grabbing hover:bg-[#182133] space-y-2.5 shadow-sm"
                      >
                        {/* Tags & Action menu */}
                        <div className="flex items-center justify-between gap-1">
                          <span className={`text-[10px] font-mono font-medium px-1.5 py-0.5 rounded border ${roleBadge.color}`}>
                            {roleBadge.text}
                          </span>

                          <div className="flex items-center gap-1">
                            {todo.manualModified && (
                              <span
                                className="text-[9px] px-1 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800"
                                title="此待办已被本人人工核实修改，受防覆盖保护"
                              >
                                人工保护
                              </span>
                            )}

                            {/* Quick status cycle button */}
                            <select
                              id={`todo-status-select-${todo.id}`}
                              value={todo.status}
                              onClick={e => e.stopPropagation()}
                              onChange={e => updateTodoStatus(todo.id, e.target.value as TodoStatus)}
                              className="text-[10px] bg-[#1a2336] text-slate-300 rounded border border-[#2b3a54] px-1 py-0.5 outline-none cursor-pointer"
                            >
                              <option value="draft">草稿箱</option>
                              <option value="pending">待处理</option>
                              <option value="in_progress">进行中</option>
                              <option value="completed">已完成</option>
                            </select>
                          </div>
                        </div>

                        {/* Title & snippet */}
                        <div>
                          <h4 className="text-xs font-medium text-slate-100 line-clamp-2 leading-snug">
                            {todo.title}
                          </h4>
                          {todo.description && (
                            <p className="text-[11px] text-slate-400 mt-1 line-clamp-1">
                              {todo.description}
                            </p>
                          )}
                        </div>

                        {/* Due Date & Click to edit */}
                        <div className="flex items-center justify-between text-[11px] text-slate-400 pt-1 border-t border-[#1d2638]">
                          {editingDueDateId === todo.id ? (
                            <div className="flex items-center gap-1 w-full" onClick={e => e.stopPropagation()}>
                              <input
                                type="text"
                                value={newDueDateValue}
                                onChange={e => setNewDueDateValue(e.target.value)}
                                placeholder="如 2026-09-17 18:00"
                                className="flex-1 text-[11px] bg-[#0c1017] border border-indigo-500 rounded px-1.5 py-0.5 text-white outline-none"
                              />
                              <button
                                onClick={() => handleSaveDueDate(todo.id)}
                                className="px-1.5 py-0.5 text-[10px] bg-indigo-600 rounded text-white"
                              >
                                确定
                              </button>
                            </div>
                          ) : (
                            <button
                              id={`edit-due-${todo.id}`}
                              onClick={e => {
                                e.stopPropagation();
                                setEditingDueDateId(todo.id);
                                setNewDueDateValue(todo.dueDate);
                              }}
                              className="flex items-center gap-1 hover:text-indigo-400 transition-colors"
                              title="点击修改截止期限 (支持上海时区相对与绝对时间)"
                            >
                              <Calendar className="w-3 h-3 text-slate-500" />
                              <span className="font-mono text-[10px]">{todo.dueDate}</span>
                              <Edit2 className="w-2.5 h-2.5 opacity-0 group-hover:opacity-100 transition-opacity" />
                            </button>
                          )}

                          {/* Jump to source message button (Criterion 4) */}
                          <button
                            id={`jump-source-${todo.id}`}
                            onClick={e => {
                              e.stopPropagation();
                              jumpToMessage(todo.sourceConversationId, todo.sourceMessageId);
                            }}
                            className="flex items-center gap-1 text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors p-1 rounded hover:bg-[#1c273c]"
                            title={`定位原消息：${todo.sourceConversationName}`}
                          >
                            <span>来源</span>
                            <ExternalLink className="w-3 h-3" />
                          </button>
                        </div>

                        {/* Card bottom actions: Ignore / Delete */}
                        <div className="flex items-center justify-between pt-1 text-[10px] text-slate-500">
                          <button
                            id={`ignore-btn-${todo.id}`}
                            onClick={e => {
                              e.stopPropagation();
                              toggleIgnoreTodo(todo.id);
                            }}
                            className="hover:text-amber-400 transition-colors"
                          >
                            {todo.isIgnored ? '撤销忽略' : '忽略此项'}
                          </button>

                          <button
                            id={`del-btn-${todo.id}`}
                            onClick={e => {
                              e.stopPropagation();
                              deleteTodo(todo.id);
                            }}
                            className="hover:text-rose-400 transition-colors"
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
      )}
    </div>
  );
};
