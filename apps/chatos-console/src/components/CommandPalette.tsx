import React, { useState, useEffect, useMemo } from 'react';
import {
  Search,
  MessageSquare,
  CheckSquare,
  FolderKanban,
  FileText,
  X,
  ArrowRight,
  Sparkles,
  Command,
} from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';

export const CommandPalette: React.FC = () => {
  const {
    isCommandPaletteOpen,
    setIsCommandPaletteOpen,
    conversations,
    todos,
    projects,
    knowledgeItems,
    messages,
    setSelectedConversation,
    setSelectedProject,
    setSelectedTodo,
    setSelectedKnowledgeItem,
    jumpToMessage,
    setIsInspectorOpen,
    runIncrementalOrganize,
    setActiveWorkspace,
  } = useChatOS();

  const [query, setQuery] = useState<string>('');
  const [selectedIndex, setSelectedIndex] = useState<number>(0);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // Unified Search Results across all 5 entities
  const searchResults = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      // Default recommended actions when query is empty
      return [
        {
          type: 'action' as const,
          id: 'action-sync',
          title: '增量自动整理近90天关系与待办',
          subtitle: '系统维护操作 · 自动防重复与去重校验',
          execute: () => {
            runIncrementalOrganize();
            setIsCommandPaletteOpen(false);
          },
        },
        {
          type: 'action' as const,
          id: 'action-go-todos',
          title: '切换至：行动待办看板',
          subtitle: '查看我负责、我承诺与我需跟进事项',
          execute: () => {
            setActiveWorkspace('todos');
            setIsCommandPaletteOpen(false);
          },
        },
        {
          type: 'action' as const,
          id: 'action-go-projects',
          title: '切换至：跨会话项目工作区',
          subtitle: '浏览 ChatOS 2.0、Client-X 等项目进展',
          execute: () => {
            setActiveWorkspace('projects');
            setIsCommandPaletteOpen(false);
          },
        },
      ];
    }

    const items: Array<{
      type: 'conv' | 'message' | 'todo' | 'project' | 'file';
      id: string;
      title: string;
      subtitle: string;
      execute: () => void;
    }> = [];

    // 1. Match Conversations
    conversations.forEach(c => {
      if (
        c.name.toLowerCase().includes(q) ||
        c.twoLineSummary.toLowerCase().includes(q) ||
        c.priorityReason.toLowerCase().includes(q)
      ) {
        items.push({
          type: 'conv',
          id: `conv-${c.id}`,
          title: `会话：${c.name}`,
          subtitle: c.twoLineSummary,
          execute: () => {
            setSelectedConversation(c);
            setIsInspectorOpen(true);
            setActiveWorkspace('conversations');
            setIsCommandPaletteOpen(false);
          },
        });
      }
    });

    // 2. Match Messages
    Object.entries(messages).forEach(([convId, msgList]) => {
      const conv = conversations.find(c => c.id === convId);
      msgList.forEach(m => {
        if (m.content.toLowerCase().includes(q)) {
          items.push({
            type: 'message',
            id: `msg-${m.id}`,
            title: `消息：${m.senderName}`,
            subtitle: m.content,
            execute: () => {
              jumpToMessage(convId, m.id);
              setIsCommandPaletteOpen(false);
            },
          });
        }
      });
    });

    // 3. Match Todos
    todos.forEach(t => {
      if (
        t.title.toLowerCase().includes(q) ||
        (t.description && t.description.toLowerCase().includes(q)) ||
        t.sourceSnippet.toLowerCase().includes(q)
      ) {
        items.push({
          type: 'todo',
          id: `todo-${t.id}`,
          title: `待办：${t.title}`,
          subtitle: `截止: ${t.dueDate} · 来源: ${t.sourceConversationName}`,
          execute: () => {
            jumpToMessage(t.sourceConversationId, t.sourceMessageId);
            setIsCommandPaletteOpen(false);
          },
        });
      }
    });

    // 4. Match Projects
    projects.forEach(p => {
      if (
        p.name.toLowerCase().includes(q) ||
        p.oneLineProgress.toLowerCase().includes(q) ||
        p.currentAction.toLowerCase().includes(q)
      ) {
        items.push({
          type: 'project',
          id: `proj-${p.id}`,
          title: `项目：${p.name}`,
          subtitle: `进展: ${p.oneLineProgress}`,
          execute: () => {
            setSelectedProject(p);
            setActiveWorkspace('projects');
            setIsCommandPaletteOpen(false);
          },
        });
      }
    });

    // 5. Match Files
    knowledgeItems.forEach(k => {
      if (
        k.title.toLowerCase().includes(q) ||
        k.fileName.toLowerCase().includes(q) ||
        k.contentHash.toLowerCase().includes(q)
      ) {
        items.push({
          type: 'file',
          id: `file-${k.id}`,
          title: `资料：${k.fileName}`,
          subtitle: `Hash: ${k.contentHash.slice(0, 20)}... · 来源: ${k.sourceConversationName}`,
          execute: () => {
            jumpToMessage(k.sourceConversationId, k.sourceMessageId);
            setIsCommandPaletteOpen(false);
          },
        });
      }
    });

    return items;
  }, [query, conversations, messages, todos, projects, knowledgeItems, runIncrementalOrganize, setActiveWorkspace, setSelectedConversation, setSelectedProject, jumpToMessage, setIsInspectorOpen, setIsCommandPaletteOpen]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev + 1) % (searchResults.length || 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev - 1 + searchResults.length) % (searchResults.length || 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (searchResults[selectedIndex]) {
        searchResults[selectedIndex].execute();
      }
    }
  };

  if (!isCommandPaletteOpen) return null;

  return (
    <div
      className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-start justify-center pt-20 p-4"
      onClick={() => setIsCommandPaletteOpen(false)}
    >
      <div
        id="command-palette-modal"
        onClick={e => e.stopPropagation()}
        className="w-full max-w-2xl bg-[#111724] border border-[#27354d] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[75vh]"
      >
        {/* Search Input Bar */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-[#1e293b] bg-[#141b29]">
          <Search className="w-5 h-5 text-indigo-400 flex-shrink-0" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
            placeholder="搜索全部会话、消息、待办、项目、资料或快速指令..."
            className="flex-1 bg-transparent text-sm text-slate-100 placeholder:text-slate-500 outline-none"
          />
          {query && (
            <button onClick={() => setQuery('')} className="p-1 text-slate-400 hover:text-white">
              <X className="w-4 h-4" />
            </button>
          )}
          <kbd className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#1e283b] text-slate-400 border border-[#2d3a54]">
            ESC
          </kbd>
        </div>

        {/* Results List or Empty State */}
        <div className="flex-1 overflow-y-auto p-2 divide-y divide-[#182133]">
          {searchResults.length === 0 ? (
            <div className="text-center py-12 px-4 space-y-2">
              <p className="text-sm font-semibold text-slate-300">无匹配结果</p>
              <p className="text-xs text-slate-500">
                未搜索到包含「{query}」的会话、消息、任务或资料。尝试搜索 "Alex"、"待办"、"规范" 或 "Client"。
              </p>
            </div>
          ) : (
            searchResults.map((item, idx) => {
              const isSelected = selectedIndex === idx;

              return (
                <div
                  key={item.id}
                  onClick={item.execute}
                  onMouseEnter={() => setSelectedIndex(idx)}
                  className={`flex items-center justify-between p-3 rounded-xl cursor-pointer transition-colors ${
                    isSelected ? 'bg-indigo-600/20 border border-indigo-500/40 text-white' : 'hover:bg-[#161f30] text-slate-300'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="p-2 rounded-lg bg-[#182133] text-indigo-400 flex-shrink-0">
                      {item.type === 'conv' ? (
                        <MessageSquare className="w-4 h-4" />
                      ) : item.type === 'message' ? (
                        <Search className="w-4 h-4" />
                      ) : item.type === 'todo' ? (
                        <CheckSquare className="w-4 h-4" />
                      ) : item.type === 'project' ? (
                        <FolderKanban className="w-4 h-4" />
                      ) : item.type === 'file' ? (
                        <FileText className="w-4 h-4" />
                      ) : (
                        <Sparkles className="w-4 h-4" />
                      )}
                    </div>

                    <div className="min-w-0">
                      <div className="text-xs font-semibold truncate text-slate-100">{item.title}</div>
                      <div className="text-[11px] text-slate-400 truncate mt-0.5">{item.subtitle}</div>
                    </div>
                  </div>

                  <ArrowRight className={`w-4 h-4 flex-shrink-0 transition-opacity ${isSelected ? 'opacity-100 text-indigo-400' : 'opacity-0'}`} />
                </div>
              );
            })
          )}
        </div>

        {/* Footer shortcuts */}
        <div className="px-4 py-2.5 bg-[#0e1420] border-t border-[#1a2334] flex items-center justify-between text-[11px] text-slate-500">
          <div className="flex items-center gap-3">
            <span>↑ ↓ 选择</span>
            <span>回车 跳转并定位</span>
          </div>
          <span className="font-mono">ChatOS 智能索引</span>
        </div>
      </div>
    </div>
  );
};
