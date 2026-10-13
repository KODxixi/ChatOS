import React from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  SkipForward,
  SkipBack,
  Calendar,
  Sparkles,
  Zap,
  Activity,
  Layers,
  Clock,
  Radio,
} from 'lucide-react';
import { TIMELINE_DATES, TIMELINE_EVENTS } from '../../../utils/mindmapData';

interface MindmapTimelineControlProps {
  currentDateIndex: number;
  onSelectDateIndex: (index: number) => void;
  isPlaying: boolean;
  onTogglePlay: () => void;
  playbackSpeed: number;
  onChangeSpeed: (speed: number) => void;
  onGeneratePulse: () => void;
  isContinuousStream: boolean;
  onToggleContinuousStream: () => void;
  activeNodesCount: number;
  totalNodesCount: number;
  activeLinksCount: number;
}

export const MindmapTimelineControl: React.FC<MindmapTimelineControlProps> = ({
  currentDateIndex,
  onSelectDateIndex,
  isPlaying,
  onTogglePlay,
  playbackSpeed,
  onChangeSpeed,
  onGeneratePulse,
  isContinuousStream,
  onToggleContinuousStream,
  activeNodesCount,
  totalNodesCount,
  activeLinksCount,
}) => {
  const currentDateKey = TIMELINE_DATES[currentDateIndex] || '2026-09-15';
  const currentEvent = TIMELINE_EVENTS[currentDateKey] || {
    title: '演化节点',
    desc: '时序仿真中...',
    triggerConv: '系统演算',
  };

  const isToday = currentDateKey === '2026-09-15';

  const handlePrev = () => {
    if (currentDateIndex > 0) {
      onSelectDateIndex(currentDateIndex - 1);
    }
  };

  const handleNext = () => {
    if (currentDateIndex < TIMELINE_DATES.length - 1) {
      onSelectDateIndex(currentDateIndex + 1);
    }
  };

  const handleReset = () => {
    onSelectDateIndex(0);
  };

  return (
    <div
      id="mindmap-timeline-control"
      className="absolute bottom-4 left-4 right-4 md:left-6 md:right-6 bg-[#0d131f]/92 backdrop-blur-xl border border-[#22314a] rounded-2xl p-4 shadow-2xl z-20 flex flex-col gap-3"
    >
      {/* Top Row: Event Narrative & Live Simulation HUD */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs border-b border-[#1c283d] pb-3">
        {/* Left: Current Date & Active Event */}
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-[#141d2f] border border-[#253652]">
            <Calendar className="w-4 h-4 text-indigo-400 flex-shrink-0" />
            <span className="font-mono font-bold text-white text-sm">
              {currentDateKey}
            </span>
            {isToday && (
              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-indigo-600 text-white animate-pulse">
                今日 (TODAY)
              </span>
            )}
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-bold text-slate-100 truncate">{currentEvent.title}</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-[#162033] text-indigo-300 border border-[#263753] truncate hidden sm:inline-block">
                触发源: {currentEvent.triggerConv}
              </span>
            </div>
            <p className="text-[11px] text-slate-400 truncate max-w-xl hidden md:block">
              {currentEvent.desc}
            </p>
          </div>
        </div>

        {/* Right: Cosmograph Stream Generator & Stats Bar */}
        <div className="flex items-center gap-2">
          {/* Dynamic Data Stream Generator (Cosmograph showcase style) */}
          <button
            id="cosmo-pulse-stream-btn"
            onClick={onGeneratePulse}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white font-medium shadow-md shadow-indigo-950/60 transition-all active:scale-95"
            title="生成一次模拟会话动态脉冲与双链（参考 Cosmograph 动态数据仿真）"
          >
            <Sparkles className="w-3.5 h-3.5 animate-spin" />
            <span className="hidden sm:inline">动态脉冲生成</span>
          </button>

          <button
            id="cosmo-continuous-stream-btn"
            onClick={onToggleContinuousStream}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-xs font-medium transition-all ${
              isContinuousStream
                ? 'bg-emerald-950/80 text-emerald-300 border-emerald-700 ring-1 ring-emerald-500/40'
                : 'bg-[#141d2f] text-slate-400 border-[#253652] hover:text-slate-200'
            }`}
            title="切换连续动态数据流仿真"
          >
            <Radio className={`w-3.5 h-3.5 ${isContinuousStream ? 'text-emerald-400 animate-pulse' : ''}`} />
            <span className="hidden md:inline">{isContinuousStream ? '连续仿真中' : '自动数据流'}</span>
          </button>

          {/* Node & Link Count Stats */}
          <div className="hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-xl bg-[#111726] border border-[#1d273a] text-slate-400 font-mono text-[11px]">
            <span className="flex items-center gap-1 text-slate-300">
              <Layers className="w-3 h-3 text-indigo-400" />
              <span>{activeNodesCount}/{totalNodesCount} 活跃节点</span>
            </span>
            <span className="text-slate-600">|</span>
            <span className="flex items-center gap-1 text-slate-300">
              <Zap className="w-3 h-3 text-cyan-400" />
              <span>{activeLinksCount} 条双链</span>
            </span>
          </div>
        </div>
      </div>

      {/* Middle Row: Date Progression Scrubber with Milestone Anchors */}
      <div className="relative flex flex-col gap-1.5">
        {/* Date Milestones Grid */}
        <div className="relative flex items-center justify-between px-2">
          {TIMELINE_DATES.map((date, idx) => {
            const isSelected = idx === currentDateIndex;
            const isPassed = idx <= currentDateIndex;
            const isCurrentToday = date === '2026-09-15';

            return (
              <button
                key={date}
                id={`timeline-step-${idx}`}
                onClick={() => onSelectDateIndex(idx)}
                className={`flex flex-col items-center group relative z-10 transition-transform ${
                  isSelected ? 'scale-110' : 'hover:scale-105'
                }`}
                title={TIMELINE_EVENTS[date]?.title || date}
              >
                {/* Visual Dot */}
                <div
                  className={`w-3.5 h-3.5 rounded-full border-2 transition-all flex items-center justify-center ${
                    isSelected
                      ? 'bg-indigo-500 border-white ring-4 ring-indigo-500/40 shadow-lg shadow-indigo-500/50'
                      : isPassed
                      ? 'bg-indigo-900 border-indigo-400'
                      : 'bg-[#182235] border-[#2e3e5c]'
                  }`}
                >
                  {isCurrentToday && (
                    <div className="w-1.5 h-1.5 rounded-full bg-white animate-ping" />
                  )}
                </div>

                {/* Date Label */}
                <span
                  className={`text-[10px] font-mono mt-1 transition-colors ${
                    isSelected
                      ? 'text-indigo-300 font-bold'
                      : isPassed
                      ? 'text-slate-300'
                      : 'text-slate-400'
                  }`}
                >
                  {date.substring(5)}
                </span>
              </button>
            );
          })}

          {/* Background Connecting Line */}
          <div className="absolute left-4 right-4 top-[7px] h-0.5 bg-[#1f2b40] z-0">
            <div
              className="h-full bg-gradient-to-r from-cyan-500 via-indigo-500 to-purple-500 transition-all duration-300"
              style={{
                width: `${(currentDateIndex / (TIMELINE_DATES.length - 1)) * 100}%`,
              }}
            />
          </div>
        </div>
      </div>

      {/* Bottom Row: Playback Controls & Speed Selectors */}
      <div className="flex items-center justify-between text-xs pt-1">
        {/* Player Transport Controls */}
        <div className="flex items-center gap-1.5">
          <button
            id="timeline-reset-btn"
            onClick={handleReset}
            className="p-1.5 rounded-lg bg-[#141d2f] hover:bg-[#1a253b] text-slate-400 hover:text-white border border-[#23324c] transition-colors"
            title="重置到初始日期 (2026-09-08)"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>

          <button
            id="timeline-prev-btn"
            onClick={handlePrev}
            disabled={currentDateIndex === 0}
            className="p-1.5 rounded-lg bg-[#141d2f] hover:bg-[#1a253b] text-slate-400 hover:text-white border border-[#23324c] disabled:opacity-40 transition-colors"
            title="前一演化阶段"
          >
            <SkipBack className="w-3.5 h-3.5" />
          </button>

          <button
            id="timeline-play-toggle-btn"
            onClick={onTogglePlay}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl font-medium transition-all ${
              isPlaying
                ? 'bg-rose-600 hover:bg-rose-500 text-white shadow-md shadow-rose-950/50 ring-2 ring-rose-400/30'
                : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-950/50'
            }`}
          >
            {isPlaying ? (
              <>
                <Pause className="w-3.5 h-3.5" />
                <span>暂停演算</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>时序推演播放</span>
              </>
            )}
          </button>

          <button
            id="timeline-next-btn"
            onClick={handleNext}
            disabled={currentDateIndex === TIMELINE_DATES.length - 1}
            className="p-1.5 rounded-lg bg-[#141d2f] hover:bg-[#1a253b] text-slate-400 hover:text-white border border-[#23324c] disabled:opacity-40 transition-colors"
            title="后一演化阶段"
          >
            <SkipForward className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Speed Controls */}
        <div className="flex items-center gap-1 bg-[#121929] p-1 rounded-xl border border-[#212f46]">
          <span className="text-[10px] text-slate-400 px-1.5 font-mono">速率:</span>
          {[0.5, 1, 2, 5].map(speed => (
            <button
              key={speed}
              onClick={() => onChangeSpeed(speed)}
              className={`px-2 py-0.5 rounded-lg text-[10px] font-mono transition-colors ${
                playbackSpeed === speed
                  ? 'bg-indigo-600 text-white font-bold'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {speed}x
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
