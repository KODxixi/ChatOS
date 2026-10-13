import React from 'react';
import {
  X,
  FileCheck2,
  GitFork,
  Cpu,
  AlertCircle,
  CheckCircle2,
  ShieldCheck,
  RotateCcw,
  RefreshCw,
  Users,
  Activity,
  Layers,
} from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';

export const AuxModals: React.FC = () => {
  const {
    activeAuxModal,
    setActiveAuxModal,
    auditLogs,
    engineState,
    conversations,
    modelDiagnosticStatus,
    setModelDiagnosticStatus,
    runIncrementalOrganize,
    isSyncing,
    showToast,
  } = useChatOS();

  if (!activeAuxModal) return null;

  return (
    <div
      className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={() => setActiveAuxModal(null)}
    >
      <div
        id="aux-modal-container"
        onClick={e => e.stopPropagation()}
        className="w-full max-w-3xl bg-[#111724] border border-[#27364f] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]"
        role="dialog"
        aria-modal="true"
        aria-label="辅助工具"
      >
        {/* Modal Header */}
        <div className="px-6 py-4 border-b border-[#1e293b] bg-[#141c2c] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            {activeAuxModal === 'audit' && (
              <>
                <FileCheck2 className="w-5 h-5 text-indigo-400" />
                <h3 className="text-sm font-bold text-white">整理审计与判定追溯</h3>
              </>
            )}
            {activeAuxModal === 'graph' && (
              <>
                <GitFork className="w-5 h-5 text-indigo-400" />
                <h3 className="text-sm font-bold text-white">近90天关系强度与互动图谱</h3>
              </>
            )}
            {activeAuxModal === 'engine' && (
              <>
                <Cpu className="w-5 h-5 text-indigo-400" />
                <h3 className="text-sm font-bold text-white">ChatOS 引擎与模型运行状态</h3>
              </>
            )}
          </div>

          <button
            onClick={() => setActiveAuxModal(null)}
            className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-[#1f2a3e] transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* MODAL 1: AUDIT LOG */}
          {activeAuxModal === 'audit' && (
            <div className="space-y-4">
              <div className="p-3 rounded-xl bg-[#162033] border border-[#263757] text-xs text-slate-300 leading-relaxed flex items-start gap-2.5">
                <ShieldCheck className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
                <div>
                  <span className="font-semibold text-white">审计原则：</span>
                  下列内容来自演示数据。真实增量扫描、哈希比对与数据库持久化尚未接入，不能据此判定生产能力。
                </div>
              </div>

              <div className="space-y-3">
                {auditLogs.map(log => (
                  <div
                    key={log.id}
                    className="p-3.5 rounded-xl bg-[#0e1420] border border-[#1e283b] space-y-1.5 text-xs"
                  >
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-mono text-slate-400">{log.timestamp}</span>
                      <span
                        className={`font-semibold px-2 py-0.5 rounded text-[10px] ${
                          log.type === 'manual_override'
                            ? 'bg-amber-950/80 text-amber-300 border border-amber-800'
                            : log.type === 'sync'
                            ? 'bg-indigo-950/80 text-indigo-300 border border-indigo-800'
                            : 'bg-slate-800 text-slate-300'
                        }`}
                      >
                        {log.actor} · {log.action}
                      </span>
                    </div>
                    <p className="text-slate-200 leading-relaxed">{log.details}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* MODAL 2: RELATIONSHIP GRAPH */}
          {activeAuxModal === 'graph' && (
            <div className="space-y-5">
              <p className="text-xs text-slate-400 leading-relaxed">
                依据近 90 天双向互动天数、本人参与频次及近期持续联系综合计算，避免被群聊水群刷屏所操纵。常联系人与家庭核心受稳固保护。
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {conversations.map(conv => (
                  <div
                    key={conv.id}
                    className="p-4 rounded-xl bg-[#0f1422] border border-[#212d42] space-y-2.5"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2.5">
                        <img
                          src={conv.avatar || 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=60'}
                          alt={conv.name}
                          className="w-8 h-8 rounded-lg object-cover"
                        />
                        <div>
                          <h4 className="text-xs font-semibold text-slate-100">{conv.name}</h4>
                          <span className="text-[10px] text-slate-400">{conv.priorityReason}</span>
                        </div>
                      </div>

                      <span className="text-xs font-mono font-bold text-indigo-400">
                        {conv.priorityScore} 分
                      </span>
                    </div>

                    {/* Metrics Bars */}
                    <div className="space-y-1.5 pt-1 text-[10px]">
                      <div>
                        <div className="flex justify-between text-slate-400 mb-1">
                          <span>90天双向互动天数</span>
                          <span className="font-mono text-slate-200">{conv.interactionDays90d} 天</span>
                        </div>
                        <div className="w-full h-1 bg-[#1a2336] rounded-full overflow-hidden">
                          <div
                            className="h-full bg-indigo-500 rounded-full"
                            style={{ width: `${Math.min(100, (conv.interactionDays90d / 90) * 100)}%` }}
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-slate-400 mb-1">
                          <span>本人参与率 (JoyBoy)</span>
                          <span className="font-mono text-slate-200">{conv.joyBoyParticipationScore}%</span>
                        </div>
                        <div className="w-full h-1 bg-[#1a2336] rounded-full overflow-hidden">
                          <div
                            className="h-full bg-emerald-500 rounded-full"
                            style={{ width: `${conv.joyBoyParticipationScore}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* MODAL 3: ENGINE & MODEL DIAGNOSTIC */}
          {activeAuxModal === 'engine' && (
            <div className="space-y-5">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                <div className="p-3 rounded-xl bg-[#0e1420] border border-[#1e273a]">
                  <div className="text-[11px] text-slate-400">管理会话总量</div>
                  <div className="text-lg font-bold text-white font-mono mt-1">
                    {engineState.stats.totalConversations}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-[#0e1420] border border-[#1e273a]">
                  <div className="text-[11px] text-slate-400">活跃行动待办</div>
                  <div className="text-lg font-bold text-amber-400 font-mono mt-1">
                    {engineState.stats.activeTodos}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-[#0e1420] border border-[#1e273a]">
                  <div className="text-[11px] text-slate-400">已入库实体资料</div>
                  <div className="text-lg font-bold text-emerald-400 font-mono mt-1">
                    {engineState.stats.storedFiles}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-[#0e1420] border border-[#1e273a]">
                  <div className="text-[11px] text-slate-400">哈希去重命中</div>
                  <div className="text-lg font-bold text-indigo-400 font-mono mt-1">
                    {engineState.stats.dedupedCount}
                  </div>
                </div>
              </div>

              {/* Status and Recovery Controls */}
              <div className="p-4 rounded-xl bg-[#0e1420] border border-[#1e273a] space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                    <Activity className="w-4 h-4 text-indigo-400" />
                    <span>模型与抽取服务健康状态</span>
                  </span>

                  <span
                    className={`text-[10px] font-mono font-semibold px-2 py-0.5 rounded ${
                      modelDiagnosticStatus === 'simulate_offline'
                        ? 'bg-rose-950 text-rose-300 border border-rose-800'
                        : 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                    }`}
                  >
                    {modelDiagnosticStatus === 'simulate_offline' ? '故障演示' : '本地接入'}
                  </span>
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">
                  {modelDiagnosticStatus === 'simulate_offline'
                    ? '当前只演示离线状态标签，不代表已执行真实回滚、缓存或恢复测试。'
                    : 'Thinking Mode 已接入本机 LM Studio；每次推演实时检查指定模型。缓存和后台抽取任务仍未接入。'}
                </p>

                <div className="flex items-center gap-3 pt-2">
                  <button
                    onClick={() => {
                      setModelDiagnosticStatus(
                        modelDiagnosticStatus === 'normal' ? 'simulate_offline' : 'normal'
                      );
                      showToast(
                        modelDiagnosticStatus === 'normal'
                          ? '已进入故障演示状态'
                          : '已退出故障演示；本地模型将在推演时实时检查'
                      );
                    }}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                      modelDiagnosticStatus === 'simulate_offline'
                        ? 'bg-emerald-600 text-white border-transparent'
                        : 'bg-rose-950/60 text-rose-300 border-rose-800/60 hover:bg-rose-900/50'
                    }`}
                  >
                    {modelDiagnosticStatus === 'simulate_offline' ? '退出故障演示' : '查看故障演示'}
                  </button>

                  <button
                    onClick={() => runIncrementalOrganize()}
                    disabled={isSyncing}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-500 transition-colors disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
                    <span>检查整理接入状态</span>
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-3 bg-[#0d121c] border-t border-[#1e293b] flex items-center justify-between text-[11px] text-slate-500">
          <span>ChatOS 正式界面 2.0.4</span>
          <span>会话只读接入 · 待办/项目/资料/协作仍为演示</span>
        </div>
      </div>
    </div>
  );
};
