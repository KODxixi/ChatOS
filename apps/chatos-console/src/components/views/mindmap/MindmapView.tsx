import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  Network,
  Share2,
  Calendar,
  Sparkles,
  Layers,
  Filter,
  ArrowRightLeft,
  Info,
  SlidersHorizontal,
} from 'lucide-react';
import { useChatOS } from '../../../context/ChatOSContext';
import {
  buildMindMapGraph,
  generateSimulatedThoughtPulse,
  inspectNodeBidirectional,
  TIMELINE_DATES,
  TIMELINE_EVENTS,
} from '../../../utils/mindmapData';
import { MindMapNode, MindMapBiLink } from '../../../types';
import { MindmapCanvas, MindmapLayoutMode } from './MindmapCanvas';
import { MindmapTimelineControl } from './MindmapTimelineControl';
import { BidirectionalInspector } from './BidirectionalInspector';

export const MindmapView: React.FC = () => {
  const {
    conversations,
    todos,
    projects,
    knowledgeItems,
    teamMembers,
    customBiLinks,
    showToast,
  } = useChatOS();

  // Dynamic simulated nodes & links injected via Cosmograph showcase stream
  const [streamedNodes, setStreamedNodes] = useState<MindMapNode[]>([]);
  const [streamedLinks, setStreamedLinks] = useState<MindMapBiLink[]>([]);
  const [newPulseNodeId, setNewPulseNodeId] = useState<string | null>(null);
  const pulseStepRef = useRef<number>(0);

  // Layout mode & dynamic physics parameters
  const [layoutMode, setLayoutMode] = useState<MindmapLayoutMode>('affinity');
  const [affinityStrength, setAffinityStrength] = useState<number>(1.2);

  // Timeline playback state
  const initialDateIdx = TIMELINE_DATES.indexOf('2026-09-15');
  const [currentDateIndex, setCurrentDateIndex] = useState<number>(
    initialDateIdx >= 0 ? initialDateIdx : TIMELINE_DATES.length - 1
  );
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(1);
  const [isContinuousStream, setIsContinuousStream] = useState<boolean>(false);

  // Filter & Focus state
  const [filterType, setFilterType] = useState<string>('all');
  const [selectedNode, setSelectedNode] = useState<MindMapNode | null>(null);
  const [focusedEgoNodeId, setFocusedEgoNodeId] = useState<string | null>(null);

  // Build base graph from application state
  const baseGraph = useMemo(() => {
    return buildMindMapGraph(conversations, todos, projects, knowledgeItems, teamMembers, customBiLinks);
  }, [conversations, todos, projects, knowledgeItems, teamMembers, customBiLinks]);

  // Combine base graph + custom user links + streamed dynamic pulse nodes
  const allNodes = useMemo(() => {
    return [...baseGraph.nodes, ...streamedNodes];
  }, [baseGraph.nodes, streamedNodes]);

  const allLinks = useMemo(() => {
    return [...baseGraph.links, ...customBiLinks, ...streamedLinks];
  }, [baseGraph.links, customBiLinks, streamedLinks]);

  // Filter nodes according to simulated timeline progression (nodes emerged on or before selected date)
  const currentDateKey = TIMELINE_DATES[currentDateIndex] || '2026-09-15';

  const timeFilteredNodes = useMemo(() => {
    return allNodes.filter(node => {
      // If node has dateKey, compare with currentDateKey
      if (node.dateKey) {
        return node.dateKey <= currentDateKey;
      }
      return true;
    });
  }, [allNodes, currentDateKey]);

  const activeNodeIds = useMemo(
    () => new Set(timeFilteredNodes.map(n => n.id)),
    [timeFilteredNodes]
  );

  const timeFilteredLinks = useMemo(() => {
    return allLinks.filter(l => {
      const sId = typeof l.source === 'object' ? (l.source as any).id : l.source;
      const tId = typeof l.target === 'object' ? (l.target as any).id : l.target;
      return activeNodeIds.has(sId) && activeNodeIds.has(tId);
    });
  }, [allLinks, activeNodeIds]);

  // Handle timeline autoplay
  useEffect(() => {
    if (!isPlaying) return;

    const intervalTime = Math.max(200, 1400 / playbackSpeed);
    const timer = setInterval(() => {
      setCurrentDateIndex(prev => {
        if (prev >= TIMELINE_DATES.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, intervalTime);

    return () => clearInterval(timer);
  }, [isPlaying, playbackSpeed]);

  // Handle continuous stream generator (Cosmograph showcase style)
  useEffect(() => {
    if (!isContinuousStream) return;

    const streamInterval = setInterval(() => {
      handleGeneratePulse();
    }, 4500);

    return () => clearInterval(streamInterval);
  }, [isContinuousStream, allNodes]);

  // Cosmograph stream dynamic pulse generator
  const handleGeneratePulse = useCallback(() => {
    const { newNode, newLinks } = generateSimulatedThoughtPulse(pulseStepRef.current++, allNodes, projects);

    setStreamedNodes(prev => [...prev, newNode]);
    setStreamedLinks(prev => [...prev, ...newLinks]);
    setNewPulseNodeId(newNode.id);

    showToast(`✨ 动态思维脉冲涌现: [[${newNode.label}]] 已注入双链拓扑`);

    // Reset pulse highlight after 3.5 seconds
    setTimeout(() => {
      setNewPulseNodeId(prev => (prev === newNode.id ? null : prev));
    }, 3500);
  }, [allNodes, projects, showToast]);

  // Inspect data for selected node
  const inspectData = useMemo(() => {
    if (!selectedNode) return null;
    return inspectNodeBidirectional(selectedNode.id, allNodes, allLinks);
  }, [selectedNode, allNodes, allLinks]);

  // Select node and trigger inspector
  const handleSelectNode = (node: MindMapNode | null) => {
    setSelectedNode(node);
  };

  // Focus ego network
  const handleFocusEgo = (nodeId: string) => {
    setFocusedEgoNodeId(nodeId);
    const target = allNodes.find(n => n.id === nodeId);
    if (target) {
      setSelectedNode(target);
    }
  };

  const handleClearEgoFocus = () => {
    setFocusedEgoNodeId(null);
  };

  return (
    <div
      id="mindmap-workspace-view"
      className="relative w-full h-full bg-[#080c14] text-slate-100 flex flex-col overflow-hidden select-none"
    >
      {/* Top Header / View Controller */}
      <header className="h-14 border-b border-[#1c283d] px-4 md:px-6 bg-[#0c121e]/90 backdrop-blur-md flex items-center justify-between z-20">
        {/* Left: Title & Concept Tag */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center text-indigo-400 shadow-sm">
            <Network className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-bold text-white tracking-tight">思维图谱</h1>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-gradient-to-r from-indigo-950 to-purple-950 text-indigo-300 border border-indigo-800">
                双链时序演化网络
              </span>
            </div>
            <p className="text-[11px] text-slate-400 font-mono hidden sm:block">
              基于会话上下文与多维实体的双向链接拓扑仿真
            </p>
          </div>
        </div>

        {/* Center: Filter Tabs */}
        <div className="hidden md:flex items-center gap-1 bg-[#121929] p-1 rounded-xl border border-[#223048] text-xs">
          {[
            { id: 'all', label: '全景拓扑' },
            { id: 'convs_projects', label: '会话·项目·日期' },
            { id: 'todos', label: '行动与待办' },
            { id: 'knowledge', label: '知识与资产' },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setFilterType(tab.id)}
              className={`px-3 py-1 rounded-lg font-medium transition-all ${
                filterType === tab.id
                  ? 'bg-indigo-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Right: Network Metrics & Quick Badges */}
        <div className="flex items-center gap-2 text-xs">
          <div className="hidden lg:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#141d2f] border border-[#23334d] text-slate-300 font-mono text-[11px]">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-slate-400">D3 演化物理模拟中</span>
          </div>

          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#141d2f] border border-[#23334d] text-slate-300 font-mono text-[11px]">
            <ArrowRightLeft className="w-3.5 h-3.5 text-indigo-400" />
            <span className="text-indigo-300 font-bold">{timeFilteredLinks.length}</span>
            <span className="text-slate-500">双链</span>
          </div>

          {focusedEgoNodeId && (
            <button
              onClick={handleClearEgoFocus}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-indigo-950 text-indigo-300 border border-indigo-700 text-xs hover:bg-indigo-900 transition-colors"
              title="退出局部子网络聚焦"
            >
              <span>退出聚焦</span>
            </button>
          )}
        </div>
      </header>

      {/* Main Interactive Graph Stage */}
      <main className="relative flex-1 w-full h-full overflow-hidden">
        <MindmapCanvas
          nodes={timeFilteredNodes}
          links={timeFilteredLinks}
          selectedNodeId={selectedNode?.id || null}
          onSelectNode={handleSelectNode}
          filterType={filterType}
          focusedEgoNodeId={focusedEgoNodeId}
          onClearEgoFocus={handleClearEgoFocus}
          newPulseNodeId={newPulseNodeId}
          layoutMode={layoutMode}
          onChangeLayoutMode={setLayoutMode}
          affinityStrength={affinityStrength}
          onChangeAffinityStrength={setAffinityStrength}
        />

        {/* Floating Bidirectional Links Inspector Drawer */}
        {inspectData && (
          <BidirectionalInspector
            inspectData={inspectData}
            allNodes={allNodes}
            onClose={() => setSelectedNode(null)}
            onFocusNode={handleFocusEgo}
          />
        )}

        {/* Cosmograph Time Progression & Dynamic Simulation Scrubber */}
        <MindmapTimelineControl
          currentDateIndex={currentDateIndex}
          onSelectDateIndex={setCurrentDateIndex}
          isPlaying={isPlaying}
          onTogglePlay={() => setIsPlaying(!isPlaying)}
          playbackSpeed={playbackSpeed}
          onChangeSpeed={setPlaybackSpeed}
          onGeneratePulse={handleGeneratePulse}
          isContinuousStream={isContinuousStream}
          onToggleContinuousStream={() => setIsContinuousStream(!isContinuousStream)}
          activeNodesCount={timeFilteredNodes.length}
          totalNodesCount={allNodes.length}
          activeLinksCount={timeFilteredLinks.length}
        />
      </main>
    </div>
  );
};
