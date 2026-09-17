import React, { useState } from 'react';
import { Sparkles, Brain, X, Loader2, CheckCircle2, Lightbulb } from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';

interface ThinkingCopilotModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ThinkingCopilotModal: React.FC<ThinkingCopilotModalProps> = ({ isOpen, onClose }) => {
  const { conversations, todos, projects } = useChatOS();
  const [query, setQuery] = useState<string>('综合近90天核心互动、待办与多会话证据，推演当前最高优先级风险并输出行动建议');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [result, setResult] = useState<{
    modelUsed: string;
    thinkingMode: string;
    thoughtSummary?: string;
    analysis: string;
  } | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleRunThinkingAnalysis = async (customQuery?: string) => {
    const activeQuery = customQuery || query;
    if (!activeQuery.trim()) return;

    setIsLoading(true);
    setResult(null);
    setErrorMessage(null);

    try {
      const response = await fetch('/api/local-model/thinking-analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: activeQuery,
          contextData: {
            currentUser: 'JoyBoy',
            conversationsCount: conversations.length,
            topConversations: conversations.slice(0, 5).map(c => ({
              name: c.name,
              score: c.priorityScore,
              reason: c.priorityReason,
              hasTodo: Boolean(c.topActionTodo),
            })),
            todosCount: todos.length,
            urgentTodos: todos.filter(t => t.isUrgent || t.status === 'in_progress').map(t => ({
              title: t.title,
              due: t.dueDate,
              role: t.assigneeRole,
            })),
            projects: projects.map(p => ({
              name: p.name,
              progress: p.oneLineProgress,
              currentAction: p.currentAction,
            })),
          },
        }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.message || '本地模型调用失败');
      if (!data.modelUsed || !data.analysis) throw new Error('本地模型返回格式不完整');
      setResult(data);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '本地模型暂不可用');
    } finally {
      setIsLoading(false);
    }
  };

  const samplePrompts = [
    '推演下周核心项目风险与待办阻塞点',
    '分析 Alex 与 Elena 的近90天协作强度与潜在遗漏',
    '核查是否有被误归类或存疑的待办项',
  ];

  return (
    <div
      className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="w-full max-w-2xl bg-[#111724] border border-[#27364f] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]"
      >
        {/* Modal Header */}
        <div className="px-6 py-4 border-b border-[#1e293b] bg-[#141c2c] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-indigo-600/30 text-indigo-300 border border-indigo-500/40">
              <Brain className="w-5 h-5 text-indigo-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                Thinking Mode 深度思考推演
                <span className="whitespace-nowrap text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800">
                  {result ? `${result.modelUsed} · ${result.thinkingMode}` : 'LM Studio · 本地'}
                </span>
              </h3>
              <p className="text-[11px] text-slate-400">
                仅调用本机 LM Studio，基于结构化会话证据推演复杂决策
              </p>
            </div>
          </div>

          <button onClick={onClose} className="p-1 text-slate-400 hover:text-white rounded">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {/* Preset Prompts */}
          <div>
            <span className="text-xs text-slate-400 mb-2 flex items-center gap-1">
              <Lightbulb className="w-3.5 h-3.5 text-amber-400" />
              <span>常用深度推演场景：</span>
            </span>
            <div className="flex flex-wrap gap-2 mt-1.5">
              {samplePrompts.map((sp, idx) => (
                <button
                  key={idx}
                  onClick={() => {
                    setQuery(sp);
                    handleRunThinkingAnalysis(sp);
                  }}
                  className="px-2.5 py-1 text-xs rounded-lg bg-[#162033] hover:bg-indigo-950/60 text-slate-300 hover:text-indigo-300 border border-[#24334f] transition-colors text-left"
                >
                  {sp}
                </button>
              ))}
            </div>
          </div>

          {/* Input Box */}
          <div className="space-y-2">
            <label className="block text-xs font-medium text-slate-300">
              输入您的复杂决策或跨关系推演诉求：
            </label>
            <div className="flex gap-2">
              <textarea
                rows={2}
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="输入推演指令..."
                className="flex-1 bg-[#151d2d] border border-[#26344d] rounded-xl px-3 py-2 text-xs text-white placeholder:text-slate-500 outline-none focus:border-indigo-500"
              />
              <button
                id="run-thinking-btn"
                onClick={() => handleRunThinkingAnalysis()}
                disabled={isLoading || !query.trim()}
                className="px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl text-xs font-medium flex items-center gap-2 transition-all flex-shrink-0"
              >
                {isLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
                <span>{isLoading ? '深度推演中...' : '开始思考'}</span>
              </button>
            </div>
          </div>

          {errorMessage && (
            <div role="alert" className="rounded-xl border border-red-900/60 bg-red-950/30 px-3.5 py-3 text-xs text-red-200">
              {errorMessage}
            </div>
          )}

          {/* Result Output */}
          {result && (
            <div className="space-y-4 pt-2">
              {/* Evidence scope, never hidden chain-of-thought. */}
              {result.thoughtSummary && (
                <div className="p-3.5 rounded-xl bg-[#0e1420] border border-[#223049] space-y-1.5">
                  <div className="text-[11px] font-mono text-indigo-400 flex items-center gap-1.5 font-semibold">
                    <Brain className="w-3.5 h-3.5" />
                    <span>本地推演依据范围</span>
                  </div>
                  <pre className="text-[11px] text-slate-400 font-mono whitespace-pre-wrap leading-relaxed">
                    {result.thoughtSummary}
                  </pre>
                </div>
              )}

              {/* Final Analysis */}
              <div className="p-4 rounded-xl bg-[#141b2a] border border-[#273752] space-y-2 text-xs leading-relaxed text-slate-200">
                <div className="flex items-center justify-between border-b border-[#212c40] pb-2 mb-2">
                  <span className="font-semibold text-white flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    <span>推理结论与行动矩阵</span>
                  </span>
                  <span className="text-[10px] font-mono text-slate-400">
                    模型: {result.modelUsed}
                  </span>
                </div>
                <div className="whitespace-pre-wrap space-y-2 text-slate-300">
                  {result.analysis}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
