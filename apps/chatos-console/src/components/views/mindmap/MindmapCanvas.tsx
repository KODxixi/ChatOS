import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as d3 from 'd3';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  Sliders,
  RotateCcw,
  Sparkles,
  Zap,
  Calendar,
  Layers,
  ArrowRightLeft,
  Compass,
  Clock,
  Radio,
} from 'lucide-react';
import { MindMapNode, MindMapBiLink } from '../../../types';
import { TIMELINE_DATES } from '../../../utils/mindmapData';

export type MindmapLayoutMode = 'affinity' | 'timeline' | 'triad';

interface MindmapCanvasProps {
  nodes: MindMapNode[];
  links: MindMapBiLink[];
  selectedNodeId: string | null;
  onSelectNode: (node: MindMapNode | null) => void;
  filterType: string;
  focusedEgoNodeId: string | null;
  onClearEgoFocus: () => void;
  newPulseNodeId: string | null;
  layoutMode: MindmapLayoutMode;
  onChangeLayoutMode: (mode: MindmapLayoutMode) => void;
  affinityStrength: number;
  onChangeAffinityStrength: (val: number) => void;
}

export const MindmapCanvas: React.FC<MindmapCanvasProps> = ({
  nodes,
  links,
  selectedNodeId,
  onSelectNode,
  filterType,
  focusedEgoNodeId,
  onClearEgoFocus,
  newPulseNodeId,
  layoutMode,
  onChangeLayoutMode,
  affinityStrength,
  onChangeAffinityStrength,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Hover states for node and edge
  const [hoveredNode, setHoveredNode] = useState<MindMapNode | null>(null);
  const [hoveredLink, setHoveredLink] = useState<{ link: any; source: MindMapNode; target: MindMapNode } | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null);

  // Physics simulation tuning states
  const [linkDistance, setLinkDistance] = useState<number>(105);
  const [showParticles, setShowParticles] = useState<boolean>(true);
  const [isPhysicsPanelOpen, setIsPhysicsPanelOpen] = useState<boolean>(false);

  // Transform state: pan & zoom
  const transformRef = useRef<{ x: number; y: number; k: number }>({ x: 0, y: 0, k: 1 });
  const isDraggingCanvasRef = useRef<boolean>(false);
  const dragStartRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });

  // Dragging node state
  const draggedNodeRef = useRef<MindMapNode | null>(null);

  // Simulation ref
  const simulationRef = useRef<d3.Simulation<MindMapNode, undefined> | null>(null);

  // Filtered nodes and links based on filterType and layoutMode
  const filteredNodes = useMemo(() => {
    let result = nodes;
    if (layoutMode === 'triad') {
      // Focus on the core triad: Conversations, Projects, Dates
      result = nodes.filter(n => n.type === 'conversation' || n.type === 'project' || n.type === 'date');
    } else if (filterType === 'convs_projects') {
      result = nodes.filter(n => n.type === 'conversation' || n.type === 'project' || n.type === 'date');
    } else if (filterType === 'todos') {
      result = nodes.filter(n => n.type === 'todo' || n.type === 'project' || n.type === 'conversation');
    } else if (filterType === 'knowledge') {
      result = nodes.filter(n => n.type === 'knowledge' || n.type === 'project' || n.type === 'conversation');
    } else if (filterType !== 'all') {
      result = nodes.filter(n => n.type === filterType);
    }
    return result;
  }, [nodes, filterType, layoutMode]);

  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map(n => n.id)), [filteredNodes]);

  const filteredLinks = useMemo(() => {
    return links.filter(l => {
      const sId = typeof l.source === 'object' ? (l.source as any).id : l.source;
      const tId = typeof l.target === 'object' ? (l.target as any).id : l.target;
      return filteredNodeIds.has(sId) && filteredNodeIds.has(tId);
    });
  }, [links, filteredNodeIds]);

  // Connected nodes map for ego focus
  const egoConnectedIds = useMemo(() => {
    const focusId = focusedEgoNodeId || selectedNodeId;
    if (!focusId) return null;

    const set = new Set<string>();
    set.add(focusId);

    filteredLinks.forEach(l => {
      const sId = typeof l.source === 'object' ? (l.source as any).id : l.source;
      const tId = typeof l.target === 'object' ? (l.target as any).id : l.target;
      if (sId === focusId) set.add(tId);
      if (tId === focusId) set.add(sId);
    });

    return set;
  }, [focusedEgoNodeId, selectedNodeId, filteredLinks]);

  // Photon pulses traveling along links (animation tick)
  const photonsRef = useRef<
    {
      linkIndex: number;
      progress: number;
      speed: number;
      reverse: boolean;
      color: string;
    }[]
  >([]);

  // Setup / update D3 force simulation based on layoutMode & association degree
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Reset center on initial transform if zero
    if (transformRef.current.x === 0 && transformRef.current.y === 0) {
      transformRef.current.x = width / 2;
      transformRef.current.y = height / 2;
    }

    const simNodes: MindMapNode[] = filteredNodes.map(d => ({ ...d }));
    const simLinks: any[] = filteredLinks.map(d => ({ ...d }));

    // Base force simulation
    const simulation = d3.forceSimulation<MindMapNode>(simNodes);

    if (layoutMode === 'affinity') {
      // 1. AFFINITY CLUSTERING MODE:
      // Nodes automatically arrange based on bidirectional link association strength
      simulation
        .force(
          'link',
          d3
            .forceLink(simLinks)
            .id((d: any) => d.id)
            .distance((l: any) => {
              const str = l.strength || 1;
              // Stronger association brings nodes closer together
              return Math.max(45, (130 / Math.sqrt(str)) * (linkDistance / 100));
            })
            .strength((l: any) => {
              const str = l.strength || 1;
              return Math.min(0.95, 0.22 * str * affinityStrength);
            })
        )
        .force(
          'charge',
          d3.forceManyBody().strength((d: any) => {
            if (d.type === 'project') return -500;
            if (d.type === 'date') return -340;
            return -220;
          })
        )
        .force('center', d3.forceCenter(0, 0))
        .force(
          'collide',
          d3.forceCollide().radius((d: any) => (d.radius || 15) + 14)
        )
        .alphaDecay(0.02)
        .velocityDecay(0.28);
    } else if (layoutMode === 'timeline') {
      // 2. TIMELINE EVOLUTION RIVER MODE:
      // Uses D3 forceX mapped to chronological dates (past -> today -> future)
      // and forceY mapped to entity categories/project affinities
      simulation
        .force(
          'link',
          d3
            .forceLink(simLinks)
            .id((d: any) => d.id)
            .distance(75)
            .strength((l: any) => {
              const str = l.strength || 1;
              return Math.min(0.8, 0.2 * str * affinityStrength);
            })
        )
        .force(
          'x',
          d3
            .forceX<MindMapNode>()
            .x(d => {
              const idx = TIMELINE_DATES.indexOf(d.dateKey);
              if (idx >= 0) {
                // Distribute horizontally across time axis
                return (idx - 4) * 160;
              }
              return 0;
            })
            .strength(0.65 * affinityStrength)
        )
        .force(
          'y',
          d3
            .forceY<MindMapNode>()
            .y(d => {
              if (d.type === 'project') return -130;
              if (d.type === 'conversation') return -15;
              if (d.type === 'date') return 140;
              if (d.type === 'todo') return -65;
              return 70;
            })
            .strength(0.48)
        )
        .force(
          'charge',
          d3.forceManyBody().strength((d: any) => (d.type === 'date' ? -250 : -160))
        )
        .force(
          'collide',
          d3.forceCollide().radius((d: any) => (d.radius || 15) + 12)
        )
        .alphaDecay(0.02)
        .velocityDecay(0.32);
    } else {
      // 3. TRIAD NET (CONVERSATION - PROJECT - DATE) MODE:
      simulation
        .force(
          'link',
          d3
            .forceLink(simLinks)
            .id((d: any) => d.id)
            .distance((l: any) => {
              const str = l.strength || 1;
              return Math.max(55, (120 / Math.sqrt(str)) * (linkDistance / 100));
            })
            .strength((l: any) => {
              const str = l.strength || 1;
              return Math.min(1.0, 0.3 * str * affinityStrength);
            })
        )
        .force(
          'charge',
          d3.forceManyBody().strength((d: any) => {
            if (d.type === 'project') return -580;
            if (d.type === 'date') return -420;
            return -280;
          })
        )
        .force('center', d3.forceCenter(0, 0))
        .force(
          'collide',
          d3.forceCollide().radius((d: any) => (d.radius || 16) + 16)
        )
        .alphaDecay(0.02)
        .velocityDecay(0.28);
    }

    simulationRef.current = simulation;

    // Initialize photon particles with evolutionary directional colors
    photonsRef.current = simLinks.map(l => {
      const isBackward = l.direction === 'backward';
      return {
        linkIndex: simLinks.indexOf(l),
        progress: Math.random(),
        speed: 0.006 + Math.random() * 0.007,
        reverse: isBackward,
        color: isBackward ? '#f59e0b' : l.relationType === 'timeline' ? '#38bdf8' : '#67e8f9',
      };
    });

    return () => {
      simulation.stop();
    };
  }, [filteredNodes, filteredLinks, layoutMode, affinityStrength, linkDistance]);

  // Restart / Reheat Simulation helper
  const handleReheatSimulation = () => {
    if (simulationRef.current) {
      simulationRef.current.alpha(0.8).restart();
    }
  };

  // Main Render Animation Loop with High-DPI Canvas
  useEffect(() => {
    let animationFrameId: number;
    let pulseAngle = 0;

    const render = () => {
      const canvas = canvasRef.current;
      const container = containerRef.current;
      if (!canvas || !container) return;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const dpr = window.devicePixelRatio || 1;
      const width = container.clientWidth;
      const height = container.clientHeight;

      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr;
        canvas.height = height * dpr;
      }

      ctx.save();
      ctx.scale(dpr, dpr);

      // Deep space background
      ctx.fillStyle = '#080c14';
      ctx.fillRect(0, 0, width, height);

      // Subtle Cosmic Grid / Starfield Pattern
      ctx.strokeStyle = 'rgba(30, 41, 59, 0.22)';
      ctx.lineWidth = 1;
      const gridSize = 42 * transformRef.current.k;
      const offsetX = transformRef.current.x % gridSize;
      const offsetY = transformRef.current.y % gridSize;

      for (let x = offsetX; x < width; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = offsetY; y < height; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }

      // Apply pan and zoom transform
      ctx.translate(transformRef.current.x, transformRef.current.y);
      ctx.scale(transformRef.current.k, transformRef.current.k);

      const simNodes = simulationRef.current?.nodes() || [];
      const linkForce = simulationRef.current?.force('link') as d3.ForceLink<MindMapNode, any>;
      const simLinks = linkForce ? linkForce.links() : [];

      pulseAngle += 0.04;

      // Draw Chronological Axis Guide in timeline mode
      if (layoutMode === 'timeline') {
        ctx.save();
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.15)';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 6]);
        ctx.beginPath();
        ctx.moveTo(-700, 140);
        ctx.lineTo(700, 140);
        ctx.stroke();
        ctx.restore();
      }

      // 1. Draw Links (Edges) with Bidirectional Association Visuals
      simLinks.forEach((link: any, idx: number) => {
        const source = link.source;
        const target = link.target;
        if (!source || !target || source.x == null || target.x == null) return;

        const isConnectedToEgo =
          !egoConnectedIds || (egoConnectedIds.has(source.id) && egoConnectedIds.has(target.id));

        const isDimmed = egoConnectedIds && !isConnectedToEgo;
        const alpha = isDimmed ? 0.07 : 0.48;

        const isHighlighted =
          !isDimmed &&
          (source.id === selectedNodeId ||
            target.id === selectedNodeId ||
            hoveredLink?.link.id === link.id);

        // Draw Edge line
        ctx.beginPath();
        ctx.moveTo(source.x, source.y);
        ctx.lineTo(target.x, target.y);

        if (isHighlighted) {
          ctx.strokeStyle =
            link.direction === 'backward'
              ? 'rgba(245, 158, 11, 0.9)'
              : 'rgba(129, 140, 248, 0.95)';
          ctx.lineWidth = 2.4;
          ctx.shadowColor = link.direction === 'backward' ? '#f59e0b' : '#6366f1';
          ctx.shadowBlur = 9;
        } else if (link.relationType === 'timeline') {
          ctx.strokeStyle = `rgba(56, 189, 248, ${Math.max(0.2, alpha)})`;
          ctx.lineWidth = 1.6;
          ctx.shadowBlur = 0;
        } else if (link.relationType === 'milestone') {
          ctx.strokeStyle = `rgba(245, 158, 11, ${Math.max(0.25, alpha)})`;
          ctx.lineWidth = 1.8;
          ctx.shadowBlur = 0;
        } else {
          ctx.strokeStyle = `rgba(71, 85, 105, ${alpha})`;
          ctx.lineWidth = isDimmed ? 1 : 1.3;
          ctx.shadowBlur = 0;
        }
        ctx.stroke();

        // Draw Edge relation label & association score if zoom level > 0.82
        if (transformRef.current.k > 0.82 && link.label && !isDimmed) {
          const midX = (source.x + target.x) / 2;
          const midY = (source.y + target.y) / 2;
          ctx.font = '9px "JetBrains Mono", monospace';
          ctx.fillStyle = isHighlighted ? '#ffffff' : 'rgba(148, 163, 184, 0.75)';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(link.label, midX, midY - 4);
        }

        // 2. Draw Moving Photon Pulse (Bidirectional Evolution Particle Flow)
        if (showParticles && !isDimmed) {
          const photon = photonsRef.current[idx];
          if (photon) {
            photon.progress += photon.speed;
            if (photon.progress > 1) photon.progress = 0;

            const t = photon.reverse ? 1 - photon.progress : photon.progress;
            const px = source.x + (target.x - source.x) * t;
            const py = source.y + (target.y - source.y) * t;

            ctx.beginPath();
            ctx.arc(px, py, isHighlighted ? 3.0 : 2.2, 0, Math.PI * 2);
            ctx.fillStyle = photon.color || '#67e8f9';
            ctx.shadowColor = photon.color || '#06b6d4';
            ctx.shadowBlur = 6;
            ctx.fill();
          }
        }
      });

      // 3. Draw Nodes with Specialized Visual Archetypes (Date, Project, Conversation)
      simNodes.forEach(node => {
        if (node.x == null || node.y == null) return;

        const isSelected = node.id === selectedNodeId;
        const isHovered = hoveredNode?.id === node.id;
        const isInEgo = !egoConnectedIds || egoConnectedIds.has(node.id);
        const isNewPulse = node.id === newPulseNodeId;

        const baseAlpha = isInEgo ? 1 : 0.15;
        const r = node.radius || 15;

        ctx.save();
        ctx.globalAlpha = baseAlpha;

        // Dynamic pulsating birth ring for newly spawned or active node
        if (isNewPulse || isSelected) {
          const haloRadius = r + 7 + Math.sin(pulseAngle) * 4;
          ctx.beginPath();
          ctx.arc(node.x, node.y, haloRadius, 0, Math.PI * 2);
          ctx.strokeStyle = isSelected ? 'rgba(129, 140, 248, 0.85)' : 'rgba(34, 211, 238, 0.9)';
          ctx.lineWidth = 2;
          ctx.shadowColor = isSelected ? '#6366f1' : '#06b6d4';
          ctx.shadowBlur = 14;
          ctx.stroke();
        }

        // ARCHETYPE 1: DATE / TIMELINE MILESTONE NODE
        if (node.type === 'date') {
          const isToday = node.dateKey === '2026-09-15';

          // Outer milestone orbital dashed ring
          ctx.save();
          ctx.strokeStyle = isToday ? 'rgba(245, 158, 11, 0.7)' : 'rgba(56, 189, 248, 0.5)';
          ctx.lineWidth = 1.5;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 5, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();

          // Golden/Sky radial core
          const grad = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, r);
          grad.addColorStop(0, isToday ? '#ffffff' : '#e0f2fe');
          grad.addColorStop(0.4, isToday ? '#f59e0b' : '#0284c7');
          grad.addColorStop(1, '#090d16');

          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fillStyle = grad;
          ctx.shadowColor = isToday ? '#f59e0b' : '#38bdf8';
          ctx.shadowBlur = isSelected || isHovered ? 20 : 10;
          ctx.fill();

          // Border ring
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.strokeStyle = isToday ? '#fbbf24' : '#7dd3fc';
          ctx.lineWidth = isSelected ? 2.5 : 1.4;
          ctx.stroke();

          // Inner Calendar Glyph Mark
          ctx.font = 'bold 9px monospace';
          ctx.fillStyle = '#0f172a';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(node.dateKey ? node.dateKey.slice(5) : 'DATE', node.x, node.y);
        }
        // ARCHETYPE 2: PROJECT STRATEGIC HUB
        else if (node.type === 'project') {
          // Double outer aura
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 4, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(6, 182, 212, 0.4)';
          ctx.lineWidth = 1.5;
          ctx.stroke();

          const grad = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, r);
          grad.addColorStop(0, '#ffffff');
          grad.addColorStop(0.35, node.color || '#06b6d4');
          grad.addColorStop(1, '#081426');

          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fillStyle = grad;
          ctx.shadowColor = node.color || '#06b6d4';
          ctx.shadowBlur = isSelected || isHovered ? 22 : 12;
          ctx.fill();

          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(255, 255, 255, 0.6)';
          ctx.lineWidth = isSelected ? 2.8 : 1.5;
          ctx.stroke();

          // Draw progress badge inside
          if (node.badge) {
            ctx.font = 'bold 9px monospace';
            ctx.fillStyle = '#021827';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(node.badge, node.x, node.y);
          }
        }
        // ARCHETYPE 3: CONVERSATION ACTIVE ENERGY CENTER
        else if (node.type === 'conversation') {
          const isGroup = node.subType?.includes('群聊');

          const grad = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, r);
          grad.addColorStop(0, '#ffffff');
          grad.addColorStop(0.4, isGroup ? '#818cf8' : '#10b981');
          grad.addColorStop(1, '#0b1120');

          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fillStyle = grad;
          ctx.shadowColor = isGroup ? '#6366f1' : '#10b981';
          ctx.shadowBlur = isSelected || isHovered ? 18 : 9;
          ctx.fill();

          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.strokeStyle = isSelected ? '#ffffff' : isGroup ? 'rgba(165, 180, 252, 0.6)' : 'rgba(110, 231, 183, 0.6)';
          ctx.lineWidth = isSelected ? 2.5 : 1.2;
          ctx.stroke();
        }
        // ARCHETYPE 4: TODOS & OTHER ENTITIES
        else {
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.fillStyle = node.color || '#6366f1';
          ctx.shadowColor = node.color || '#6366f1';
          ctx.shadowBlur = isSelected || isHovered ? 16 : 8;
          ctx.fill();

          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
          ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(255, 255, 255, 0.5)';
          ctx.lineWidth = isSelected ? 2.2 : 1.2;
          ctx.stroke();
        }

        // Draw Node Label & Affinity Badge
        ctx.font = isSelected ? 'bold 12px sans-serif' : '11px sans-serif';
        ctx.fillStyle = isSelected ? '#ffffff' : '#e2e8f0';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
        ctx.shadowBlur = 4;

        let displayLabel = node.label;
        if (displayLabel.length > 15) {
          displayLabel = displayLabel.substring(0, 14) + '…';
        }
        ctx.fillText(displayLabel, node.x, node.y + r + 5);

        // Association Affinity Score Tag (if affinityScore present and zoomed in)
        if (transformRef.current.k > 0.85 && node.affinityScore) {
          ctx.font = '9px monospace';
          ctx.fillStyle = node.affinityScore > 75 ? '#38bdf8' : '#94a3b8';
          ctx.fillText(`关联度 ${node.affinityScore}%`, node.x, node.y + r + 18);
        }

        ctx.restore();
      });

      ctx.restore();
      animationFrameId = requestAnimationFrame(render);
    };

    animationFrameId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animationFrameId);
  }, [selectedNodeId, hoveredNode, hoveredLink, egoConnectedIds, newPulseNodeId, layoutMode, showParticles]);

  // Hit test helper: find node at canvas screen coordinates
  const getNodeAtCoords = useCallback(
    (screenX: number, screenY: number): MindMapNode | null => {
      const { x: tx, y: ty, k } = transformRef.current;
      const simNodes = simulationRef.current?.nodes() || [];

      const worldX = (screenX - tx) / k;
      const worldY = (screenY - ty) / k;

      for (let i = simNodes.length - 1; i >= 0; i--) {
        const node = simNodes[i];
        if (node.x == null || node.y == null) continue;
        const dist = Math.hypot(node.x - worldX, node.y - worldY);
        if (dist <= (node.radius || 15) + 5) {
          return node;
        }
      }
      return null;
    },
    []
  );

  // Mouse Handlers: Pan, Zoom, Drag Node, Click
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const screenX = e.clientX - rect.left;
    const screenY = e.clientY - rect.top;

    const hitNode = getNodeAtCoords(screenX, screenY);
    if (hitNode) {
      draggedNodeRef.current = hitNode;
      hitNode.fx = hitNode.x;
      hitNode.fy = hitNode.y;
      if (simulationRef.current) {
        simulationRef.current.alphaTarget(0.3).restart();
      }
    } else {
      isDraggingCanvasRef.current = true;
      dragStartRef.current = { x: e.clientX, y: e.clientY };
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const screenX = e.clientX - rect.left;
    const screenY = e.clientY - rect.top;

    if (draggedNodeRef.current) {
      const { x: tx, y: ty, k } = transformRef.current;
      draggedNodeRef.current.fx = (screenX - tx) / k;
      draggedNodeRef.current.fy = (screenY - ty) / k;
    } else if (isDraggingCanvasRef.current) {
      const dx = e.clientX - dragStartRef.current.x;
      const dy = e.clientY - dragStartRef.current.y;
      transformRef.current.x += dx;
      transformRef.current.y += dy;
      dragStartRef.current = { x: e.clientX, y: e.clientY };
    } else {
      const hitNode = getNodeAtCoords(screenX, screenY);
      if (hitNode) {
        setHoveredNode(hitNode);
        setTooltipPos({ x: e.clientX, y: e.clientY });
      } else {
        setHoveredNode(null);
        setTooltipPos(null);
      }
    }
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (draggedNodeRef.current) {
      draggedNodeRef.current.fx = null;
      draggedNodeRef.current.fy = null;
      draggedNodeRef.current = null;
      if (simulationRef.current) {
        simulationRef.current.alphaTarget(0);
      }
    }

    if (isDraggingCanvasRef.current) {
      isDraggingCanvasRef.current = false;
    }
  };

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const screenX = e.clientX - rect.left;
    const screenY = e.clientY - rect.top;

    const hitNode = getNodeAtCoords(screenX, screenY);
    if (hitNode) {
      onSelectNode(hitNode);
    } else {
      onSelectNode(null);
      onClearEgoFocus();
    }
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    const newK = Math.max(0.2, Math.min(4.0, transformRef.current.k * zoomFactor));

    transformRef.current.x = mouseX - (mouseX - transformRef.current.x) * (newK / transformRef.current.k);
    transformRef.current.y = mouseY - (mouseY - transformRef.current.y) * (newK / transformRef.current.k);
    transformRef.current.k = newK;
  };

  const handleZoomIn = () => {
    transformRef.current.k = Math.min(4.0, transformRef.current.k * 1.25);
  };
  const handleZoomOut = () => {
    transformRef.current.k = Math.max(0.2, transformRef.current.k * 0.8);
  };
  const handleResetView = () => {
    const container = containerRef.current;
    if (container) {
      transformRef.current = {
        x: container.clientWidth / 2,
        y: container.clientHeight / 2,
        k: 1,
      };
    }
  };

  return (
    <div ref={containerRef} className="relative w-full h-full overflow-hidden select-none bg-[#080c14]">
      <canvas
        id="d3-evolution-simulation-canvas"
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
        onClick={handleClick}
        className={`w-full h-full block ${hoveredNode ? 'cursor-pointer' : 'cursor-grab active:cursor-grabbing'}`}
      />

      {/* Top Floating Simulation & Layout Switcher Dock */}
      <div className="absolute top-4 left-4 flex flex-wrap items-center gap-2 z-10">
        {/* Layout Modes */}
        <div className="flex items-center p-1 rounded-xl bg-[#0d131f]/92 backdrop-blur-md border border-[#22314a] shadow-xl text-xs">
          <button
            id="layout-mode-affinity-btn"
            onClick={() => onChangeLayoutMode('affinity')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all ${
              layoutMode === 'affinity'
                ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-950'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="关联度自动排列：根据会话与项目的关联强度引力聚合"
          >
            <Compass className="w-3.5 h-3.5" />
            <span>关联度自动排列</span>
          </button>

          <button
            id="layout-mode-timeline-btn"
            onClick={() => onChangeLayoutMode('timeline')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all ${
              layoutMode === 'timeline'
                ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-950'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="时空演化时间流：沿时间轴横向展示会话、项目与日期的演化推演"
          >
            <Clock className="w-3.5 h-3.5" />
            <span>时空演化时间流</span>
          </button>

          <button
            id="layout-mode-triad-btn"
            onClick={() => onChangeLayoutMode('triad')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all ${
              layoutMode === 'triad'
                ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-950'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="三元核心演化：聚焦会话、项目与日期之间的三向驱动与约束"
          >
            <ArrowRightLeft className="w-3.5 h-3.5" />
            <span>三元核心网络</span>
          </button>
        </div>

        {/* Reheat Simulation Button */}
        <button
          id="reheat-sim-btn"
          onClick={handleReheatSimulation}
          className="flex items-center gap-1 px-2.5 py-2 rounded-xl bg-[#0d131f]/90 backdrop-blur-md border border-[#22314a] text-slate-300 hover:text-white hover:border-indigo-500/50 shadow-xl text-xs transition-colors"
          title="重新激活力导向动态物理演算"
        >
          <RotateCcw className="w-3.5 h-3.5 text-indigo-400" />
          <span className="hidden sm:inline">重新排列</span>
        </button>

        {/* Physics Controls Toggle */}
        <button
          id="toggle-physics-panel-btn"
          onClick={() => setIsPhysicsPanelOpen(!isPhysicsPanelOpen)}
          className={`flex items-center gap-1 px-2.5 py-2 rounded-xl backdrop-blur-md border shadow-xl text-xs transition-colors ${
            isPhysicsPanelOpen
              ? 'bg-indigo-600/30 text-indigo-200 border-indigo-500'
              : 'bg-[#0d131f]/90 text-slate-300 border-[#22314a] hover:text-white'
          }`}
          title="展开动态力导向参数调节面板"
        >
          <Sliders className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">动力学参数</span>
        </button>
      </div>

      {/* Floating Physics Sliders Popover */}
      {isPhysicsPanelOpen && (
        <div className="absolute top-16 left-4 w-72 p-3.5 rounded-2xl bg-[#0d131f]/95 backdrop-blur-xl border border-[#22314a] shadow-2xl text-xs z-20 space-y-3 animate-in fade-in zoom-in-95 duration-150">
          <div className="flex items-center justify-between border-b border-[#1f2b3f] pb-2">
            <span className="font-semibold text-slate-200 flex items-center gap-1.5">
              <Zap className="w-3.5 h-3.5 text-indigo-400" />
              <span>D3 演化物理引擎参数</span>
            </span>
            <span className="text-[10px] font-mono text-slate-400">实时计算</span>
          </div>

          <div>
            <div className="flex items-center justify-between text-[11px] text-slate-300 mb-1">
              <span>关联吸附力 (Affinity Attraction)</span>
              <span className="font-mono text-indigo-400 font-bold">{affinityStrength.toFixed(1)}x</span>
            </div>
            <input
              type="range"
              min="0.5"
              max="2.5"
              step="0.1"
              value={affinityStrength}
              onChange={e => onChangeAffinityStrength(parseFloat(e.target.value))}
              className="w-full h-1.5 bg-[#1b2537] rounded-lg appearance-none cursor-pointer accent-indigo-500"
            />
          </div>

          <div>
            <div className="flex items-center justify-between text-[11px] text-slate-300 mb-1">
              <span>双链弹性间距 (Link Elasticity)</span>
              <span className="font-mono text-cyan-400 font-bold">{linkDistance}px</span>
            </div>
            <input
              type="range"
              min="60"
              max="180"
              step="5"
              value={linkDistance}
              onChange={e => setLinkDistance(parseInt(e.target.value))}
              className="w-full h-1.5 bg-[#1b2537] rounded-lg appearance-none cursor-pointer accent-cyan-500"
            />
          </div>

          <div className="flex items-center justify-between pt-1 border-t border-[#1f2b3f]">
            <span className="text-[11px] text-slate-400">双向演化能量流微粒</span>
            <button
              onClick={() => setShowParticles(!showParticles)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-colors ${
                showParticles
                  ? 'bg-cyan-950 text-cyan-300 border border-cyan-800'
                  : 'bg-[#151e2f] text-slate-400 border border-[#233148]'
              }`}
            >
              {showParticles ? '流向高亮开启' : '微粒已关闭'}
            </button>
          </div>
        </div>
      )}

      {/* Floating Camera / Zoom Controls */}
      <div className="absolute top-4 right-4 flex items-center gap-1 p-1 rounded-xl bg-[#0d131f]/90 backdrop-blur-md border border-[#22314a] shadow-xl text-xs z-10">
        <button
          onClick={handleZoomIn}
          className="w-7 h-7 rounded-lg bg-[#141d2f] hover:bg-[#1f2d47] text-slate-300 hover:text-white flex items-center justify-center font-bold text-sm transition-colors"
          title="放大"
        >
          <ZoomIn className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleZoomOut}
          className="w-7 h-7 rounded-lg bg-[#141d2f] hover:bg-[#1f2d47] text-slate-300 hover:text-white flex items-center justify-center font-bold text-sm transition-colors"
          title="缩小"
        >
          <ZoomOut className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleResetView}
          className="px-2.5 h-7 rounded-lg bg-[#141d2f] hover:bg-[#1f2d47] text-slate-300 hover:text-white flex items-center justify-center text-[11px] font-mono transition-colors"
          title="重置居中视角"
        >
          <Maximize2 className="w-3 h-3 mr-1" />
          视角居中
        </button>
      </div>

      {/* Hover Node Tooltip */}
      {hoveredNode && tooltipPos && (
        <div
          className="fixed pointer-events-none z-40 px-3.5 py-2.5 rounded-xl bg-[#0f1626]/95 backdrop-blur-md border border-[#253754] text-white shadow-2xl text-xs transform -translate-x-1/2 -translate-y-full -mt-3 animate-in fade-in zoom-in-95 duration-100 min-w-[210px]"
          style={{ left: tooltipPos.x, top: tooltipPos.y }}
        >
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <div className="flex items-center gap-2 min-w-0">
              <span
                className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: hoveredNode.color }}
              />
              <span className="font-bold truncate">{hoveredNode.label}</span>
            </div>
            {hoveredNode.affinityScore && (
              <span className="text-[10px] font-mono font-bold px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">
                关联度 {hoveredNode.affinityScore}%
              </span>
            )}
          </div>

          <div className="text-[11px] text-slate-400 space-y-1 font-mono">
            <p className="flex items-center justify-between">
              <span>类型:</span>
              <span className="text-slate-200">
                {hoveredNode.type === 'date'
                  ? '📅 时序演化节点'
                  : hoveredNode.type === 'project'
                  ? '📂 主导项目枢纽'
                  : hoveredNode.type === 'conversation'
                  ? '💬 核心驱动会话'
                  : hoveredNode.type}
              </span>
            </p>
            <p className="flex items-center justify-between">
              <span>时序归属:</span>
              <span className="text-slate-200">{hoveredNode.dateKey || hoveredNode.timestamp}</span>
            </p>
            {hoveredNode.connectedCount !== undefined && (
              <p className="flex items-center justify-between">
                <span>双向互链数:</span>
                <span className="text-indigo-400 font-bold">{hoveredNode.connectedCount} 条</span>
              </p>
            )}
            {hoveredNode.details && (
              <p className="text-slate-300 mt-1 pt-1 border-t border-[#1c283d] line-clamp-2 leading-relaxed font-sans">
                {hoveredNode.details}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
