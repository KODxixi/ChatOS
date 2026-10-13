import {
  Conversation,
  TodoItem,
  Project,
  KnowledgeItem,
  User,
  MindMapNode,
  MindMapBiLink,
  MindMapNodeType,
} from '../types';

export interface TimelineEvent {
  dateKey: string;
  displayDate: string;
  title: string;
  description: string;
  activeNodeIds: string[];
  activeLinkIds: string[];
  highlightNodeId?: string;
  category: 'conversation' | 'project' | 'todo' | 'milestone';
  stats: {
    conversationsCount: number;
    todosCount: number;
    activeLinksCount: number;
  };
}

export const TIMELINE_DATES = [
  '2026-09-08',
  '2026-09-10',
  '2026-09-11',
  '2026-09-13',
  '2026-09-14',
  '2026-09-15',
  '2026-09-16',
  '2026-09-17',
  '2026-09-20',
];

export const TIMELINE_EVENTS: Record<string, { title: string; desc: string; triggerConv: string }> = {
  '2026-09-08': {
    title: '学术前沿论证与模型构想',
    desc: '张教授推荐 Graph-RAG 与本地思考模型，确立本地/云端降级双通道架构。',
    triggerConv: '张教授 (AI 架构学术顾问)',
  },
  '2026-09-10': {
    title: 'SRE 云原生架构与网关启动',
    desc: 'Devin 就网关反代和容器化路由提供初步拓扑，准备支持 WebSocket 动态预览。',
    triggerConv: 'Devin (云原生运维)',
  },
  '2026-09-11': {
    title: 'ChatOS 2.0 重构正式立项',
    desc: 'Alex 与核心技术团队立项，决定彻底重写前端工作台，剔除繁杂侧栏。',
    triggerConv: 'Alex (Tech Lead)',
  },
  '2026-09-13': {
    title: 'Client-X 商业化合规对接介入',
    desc: '王总同步外部法务审阅意见，建立数据沙箱与第三方 OAuth 授权合规专栏。',
    triggerConv: '王总 (Client-X 商业伙伴)',
  },
  '2026-09-14': {
    title: 'Gary-UI 设计规范与暗色 Token 冻结',
    desc: 'Sarah 同步实体底与触控标准，完成首个已交付待办，归档色彩 Token 规范。',
    triggerConv: 'Sarah (产品设计总监)',
  },
  '2026-09-15': {
    title: '今日：重构攻坚高峰与增量引擎跑通',
    desc: '架构群高频对话，Alex 催收验收规范，JoyBoy 承诺今晚8点交付反代日志，双链网络全面织网。',
    triggerConv: 'ChatOS 架构攻坚群 & Alex',
  },
  '2026-09-16': {
    title: '关键里程碑：前端交互规范验收交付',
    desc: 'Alex 指定的验收规范到期交付，四工作区与右侧两段式长文流转正式验收。',
    triggerConv: '待办里程碑交付',
  },
  '2026-09-17': {
    title: 'Client-X 授权合规协议法务双签署',
    desc: '完成第三方数据留存法务对齐，技术侧交付数据沙箱安全隔离验证报告。',
    triggerConv: '商务交付推进',
  },
  '2026-09-20': {
    title: 'ChatOS 2.0 全面灰度发布',
    desc: '端到端闭环测试完成，双链图谱、协同待办与智能整理面向所有组织开放。',
    triggerConv: '里程碑发布',
  },
};

export function buildMindMapGraph(
  conversations: Conversation[],
  todos: TodoItem[],
  projects: Project[],
  knowledgeItems: KnowledgeItem[],
  members: User[],
  customLinks: MindMapBiLink[] = []
): { nodes: MindMapNode[]; links: MindMapBiLink[] } {
  const nodes: MindMapNode[] = [];
  const links: MindMapBiLink[] = [];

  // 0. Date Milestone Nodes (Chronological Evolution Spine & Temporal Anchors)
  TIMELINE_DATES.forEach((date, idx) => {
    const evt = TIMELINE_EVENTS[date];
    const isToday = date === '2026-09-15';
    nodes.push({
      id: `date-${date}`,
      label: `${date.slice(5)} ${evt ? evt.title.slice(0, 6) : '时序节点'}`,
      type: 'date',
      subType: isToday ? '今日核心' : idx < 5 ? '演化已成' : '未来目标',
      timestamp: date,
      dateKey: date,
      color: isToday ? '#f59e0b' : idx < 5 ? '#38bdf8' : '#a855f7',
      radius: isToday ? 21 : 16,
      entityId: date,
      details: evt ? `${evt.title}：${evt.desc}` : '时序演化里程碑',
      badge: isToday ? '今日' : `${date.slice(5)}`,
      clusterId: 'timeline-spine',
      isDateAnchor: true,
    });
  });

  // Connect Date Nodes into chronological backbone spine (时序演化骨干)
  for (let i = 0; i < TIMELINE_DATES.length - 1; i++) {
    const curDate = TIMELINE_DATES[i];
    const nextDate = TIMELINE_DATES[i + 1];
    links.push({
      id: `spine-${curDate}-${nextDate}`,
      source: `date-${curDate}`,
      target: `date-${nextDate}`,
      label: '时序演进',
      relationType: 'timeline',
      createdAt: curDate,
      strength: 2.2,
      direction: 'forward',
      evolutionFlow: '时序演进推进',
      snippet: `从 [[${curDate}]] 演化跃迁至 [[${nextDate}]] 关键里程碑`,
    });
  }

  // 1. Projects (Major Strategic Hubs)
  projects.forEach(p => {
    nodes.push({
      id: p.id,
      label: p.name,
      type: 'project',
      subType: p.code,
      timestamp: p.lastUpdated,
      dateKey: p.id === 'proj-agent-core' ? '2026-09-08' : p.id === 'proj-chatos-rebuild' ? '2026-09-11' : '2026-09-13',
      color: p.color || '#06b6d4',
      radius: 24,
      entityId: p.id,
      details: p.oneLineProgress,
      badge: `${p.completionPercentage}%`,
      clusterId: p.id,
    });
  });

  // Project <-> Date Bidirectional Evolution Links (战略基准与交付倒逼)
  const projectDateLinks: [string, string, string, number, 'forward' | 'backward' | 'bidirectional', string][] = [
    ['proj-agent-core', 'date-2026-09-08', '立项构想论证', 1.8, 'forward', '学术研讨催生项目立项'],
    ['proj-agent-core', 'date-2026-09-15', '本地双通道跑通', 2.0, 'bidirectional', '今日核心技术攻坚验证'],
    ['proj-agent-core', 'date-2026-09-20', '多模态灰度集成', 1.6, 'forward', '项目最终交付里程碑'],

    ['proj-chatos-rebuild', 'date-2026-09-11', '重构正式立项', 2.4, 'forward', '核心技术团队正式启动重构'],
    ['proj-chatos-rebuild', 'date-2026-09-14', 'UI规范冻结', 1.8, 'bidirectional', '设计规范反哺技术实现'],
    ['proj-chatos-rebuild', 'date-2026-09-15', '架构攻坚冲刺', 2.6, 'bidirectional', '今日全量跑通增量水位引擎'],
    ['proj-chatos-rebuild', 'date-2026-09-16', '交互验收交付', 2.2, 'backward', '里程碑期限倒逼项目产出'],
    ['proj-chatos-rebuild', 'date-2026-09-20', '全面灰度上线', 1.9, 'forward', '产品全量演化闭环'],

    ['proj-client-x', 'date-2026-09-13', '合规介入入场', 2.0, 'forward', '外部合作方正式介入'],
    ['proj-client-x', 'date-2026-09-17', '协议法务签署', 2.1, 'backward', '签署节点倒逼安全报告交付'],
  ];

  projectDateLinks.forEach(([projId, dateNodeId, label, strength, direction, note]) => {
    links.push({
      id: `link-${projId}-${dateNodeId}`,
      source: projId,
      target: dateNodeId,
      label,
      relationType: 'milestone',
      createdAt: dateNodeId.replace('date-', ''),
      strength,
      direction,
      evolutionFlow: note,
      snippet: `[[${projects.find(p => p.id === projId)?.name || projId}]] 与时序节点 [[${dateNodeId}]] 演化锚定 (${label})`,
    });
  });

  // 2. Conversations (Active Energy Centers & Driving Impulses)
  conversations.forEach(c => {
    let dateKey = '2026-09-15';
    if (c.id === 'conv-sarah') dateKey = '2026-09-14';
    if (c.id === 'conv-wang') dateKey = '2026-09-14';
    if (c.id === 'conv-prof-zhang') dateKey = '2026-09-08';
    if (c.id === 'conv-noise-group') dateKey = '2026-09-13';

    nodes.push({
      id: c.id,
      label: c.name,
      type: 'conversation',
      subType: c.isGroup ? '群聊协作' : '定向私聊',
      timestamp: c.lastActiveTime,
      dateKey,
      color: '#6366f1',
      radius: c.isGroup ? 18 : 15,
      entityId: c.id,
      details: c.twoLineSummary,
      badge: `${c.interactionDays90d}d 互动`,
      clusterId: c.linkedProjectIds[0] || 'conv-general',
    });

    // Bi-links to Linked Projects (会话驱动项目)
    c.linkedProjectIds.forEach(pId => {
      const p = projects.find(proj => proj.id === pId);
      const strength = 1.4 + (c.joyBoyParticipationScore / 100) * 1.2;
      links.push({
        id: `link-${c.id}-${pId}`,
        source: c.id,
        target: pId,
        label: c.isGroup ? '群聊协作驱动' : '高频双向对齐',
        relationType: 'evolution',
        createdAt: dateKey,
        strength,
        direction: 'bidirectional',
        evolutionFlow: `会话沟通转化沉淀为项目资产`,
        snippet: `[[${c.name}]] 深度对齐 [[项目:${p?.name || pId}]] 核心目标与技术选型`,
      });
    });
  });

  // Conversation <-> Date Bidirectional Evolution Links (会话生于时序，推动里程碑)
  const convDateLinks: [string, string, string, number, 'forward' | 'backward' | 'bidirectional', string][] = [
    ['conv-prof-zhang', 'date-2026-09-08', '学术顾问论证', 2.0, 'forward', '会话奠定理论基础'],
    ['conv-alex', 'date-2026-09-11', '立项决策对话', 2.3, 'forward', 'Alex 催生重构立项'],
    ['conv-alex', 'date-2026-09-15', '今日攻坚对齐', 2.5, 'bidirectional', '高频催收与交付承诺'],
    ['conv-alex', 'date-2026-09-16', '交付验收要求', 2.2, 'backward', '验收期限约束今日会话行为'],
    ['conv-wang', 'date-2026-09-13', '法务审核介入', 1.9, 'forward', '商业伙伴同步审核条款'],
    ['conv-wang', 'date-2026-09-17', '双签署推进', 2.0, 'backward', '签署节点倒逼会话输出沙箱报告'],
    ['conv-sarah', 'date-2026-09-14', '设计规范冻结', 2.2, 'forward', '产出暗色 Token 与触控标准'],
    ['conv-arch-group', 'date-2026-09-15', '全员在线攻坚', 2.7, 'bidirectional', '今日全组高频联调协同'],
  ];

  convDateLinks.forEach(([convId, dateNodeId, label, strength, direction, note]) => {
    if (nodes.some(n => n.id === convId) && nodes.some(n => n.id === dateNodeId)) {
      links.push({
        id: `link-${convId}-${dateNodeId}`,
        source: convId,
        target: dateNodeId,
        label,
        relationType: 'evolution',
        createdAt: dateNodeId.replace('date-', ''),
        strength,
        direction,
        evolutionFlow: note,
        snippet: `[[${conversations.find(c => c.id === convId)?.name || convId}]] 在时序节点 [[${dateNodeId}]] 持续演化 (${label})`,
      });
    }
  });

  // 3. Todos (Action Orbitals)
  todos.forEach(t => {
    let dateKey = '2026-09-15';
    if (t.createdAt) {
      dateKey = t.createdAt.split(' ')[0];
    }

    const isDone = t.status === 'completed';
    const color = isDone ? '#10b981' : t.isUrgent ? '#f43f5e' : '#f59e0b';

    nodes.push({
      id: t.id,
      label: t.title,
      type: 'todo',
      subType: t.status,
      timestamp: t.dueDate,
      dateKey,
      color,
      radius: 12,
      entityId: t.id,
      details: t.evidenceText || t.description,
      badge: isDone ? '已完成' : t.isUrgent ? '紧急待办' : '待处理',
      clusterId: t.projectId || 'todo-misc',
    });

    // Bi-link Todo <-> Conversation
    if (t.sourceConversationId) {
      links.push({
        id: `link-${t.sourceConversationId}-${t.id}`,
        source: t.sourceConversationId,
        target: t.id,
        label: '会话提炼待办',
        relationType: 'produces',
        createdAt: dateKey,
        strength: 1.2,
        snippet: `从 [[${t.sourceConversationName}]] 提取出行动待办: "${t.sourceSnippet || t.title}"`,
      });
    }

    // Bi-link Todo <-> Project
    if (t.projectId) {
      links.push({
        id: `link-${t.id}-${t.projectId}`,
        source: t.id,
        target: t.projectId,
        label: '行动支撑项目',
        relationType: 'belongs_to',
        createdAt: dateKey,
        strength: 1.3,
        snippet: `待办事项 [[${t.title}]] 作为交付节点归属于 [[${projects.find(p => p.id === t.projectId)?.name || t.projectId}]]`,
      });
    }
  });

  // 4. Knowledge Items (Crystalline Knowledge)
  knowledgeItems.forEach(k => {
    const dateKey = k.createdAt.split(' ')[0] || '2026-09-15';
    nodes.push({
      id: k.id,
      label: k.title,
      type: 'knowledge',
      subType: k.fileType,
      timestamp: k.createdAt,
      dateKey,
      color: '#10b981',
      radius: 13,
      entityId: k.id,
      details: `${k.fileName} · ${k.fileSize} (${k.intakeStatus})`,
      badge: k.fileType.toUpperCase(),
      clusterId: k.linkedProjectId || 'know-cluster',
    });

    // Bi-link Knowledge <-> Conversation
    if (k.sourceConversationId) {
      links.push({
        id: `link-${k.sourceConversationId}-${k.id}`,
        source: k.sourceConversationId,
        target: k.id,
        label: '传输沉淀资料',
        relationType: 'references',
        createdAt: dateKey,
        strength: 1.1,
        snippet: `在 [[${k.sourceConversationName}]] 中作为附件传输并提取至资料库`,
      });
    }

    // Bi-link Knowledge <-> Project
    if (k.linkedProjectId) {
      links.push({
        id: `link-${k.id}-${k.linkedProjectId}`,
        source: k.id,
        target: k.linkedProjectId,
        label: '项目支撑资产',
        relationType: 'belongs_to',
        createdAt: dateKey,
        strength: 1.3,
        snippet: `文档 [[${k.title}]] 关联支撑项目 [[${projects.find(p => p.id === k.linkedProjectId)?.name || k.linkedProjectId}]]`,
      });
    }
  });

  // 5. Key Team Members (Cosmic Collaborators)
  members.forEach(m => {
    nodes.push({
      id: m.id,
      label: m.name,
      type: 'person',
      subType: m.role,
      timestamp: '常驻协作',
      dateKey: '2026-09-08',
      color: m.isSelf ? '#6366f1' : '#ec4899',
      radius: m.isSelf ? 19 : 15,
      entityId: m.id,
      details: m.role,
      badge: m.isSelf ? '本人' : '协作者',
      clusterId: 'people-cluster',
    });
  });

  // Bi-links: Members <-> Conversations
  const personConvLinks: [string, string, string][] = [
    ['user-alex', 'conv-alex', '主导会话'],
    ['user-alex', 'conv-arch-group', '参与群攻坚'],
    ['user-sarah', 'conv-sarah', '主导设计会话'],
    ['user-wang', 'conv-wang', '商业沟通对接'],
    ['user-prof-zhang', 'conv-prof-zhang', '学术顾问咨询'],
    ['user-joyboy', 'conv-alex', '深度对齐'],
    ['user-joyboy', 'conv-arch-group', '承诺交付'],
    ['user-joyboy', 'conv-sarah', '规范落地'],
    ['user-joyboy', 'conv-wang', '法务审核跟进'],
  ];

  personConvLinks.forEach(([pId, cId, note]) => {
    if (nodes.some(n => n.id === pId) && nodes.some(n => n.id === cId)) {
      links.push({
        id: `link-${pId}-${cId}`,
        source: pId,
        target: cId,
        label: '核心参与人',
        relationType: 'assignee',
        createdAt: '2026-09-10',
        strength: 1.0,
        snippet: `[[${members.find(m => m.id === pId)?.name || pId}]] 作为核心发言人持续驱动 [[${conversations.find(c => c.id === cId)?.name || cId}]] (${note})`,
      });
    }
  });

  // 6. Topics & Conceptual Bridges (双链语义中心)
  const TOPICS = [
    { id: 'topic-arch', label: '#架构重构规范', dateKey: '2026-09-11', desc: '包含卡片实体底、两段式摘要与四工作区' },
    { id: 'topic-oauth', label: '#第三方OAuth合规', dateKey: '2026-09-13', desc: '涉及 Client-X 数据留存与沙箱隔离' },
    { id: 'topic-agent', label: '#本地思考推理引擎', dateKey: '2026-09-08', desc: '支持离线自动降级、异常回滚与重试' },
    { id: 'topic-tokens', label: '#Gary-UI暗色规范', dateKey: '2026-09-14', desc: '统一中性深色背景、无半透明重叠' },
  ];

  TOPICS.forEach(top => {
    nodes.push({
      id: top.id,
      label: top.label,
      type: 'topic',
      timestamp: '语义核心',
      dateKey: top.dateKey,
      color: '#a855f7',
      radius: 14,
      details: top.desc,
      badge: '双链议题',
      clusterId: 'topics-cluster',
    });
  });

  // Connect Topics to Conversations and Projects
  const topicLinks: [string, string, string][] = [
    ['topic-arch', 'conv-alex', '探讨重构交互规范'],
    ['topic-arch', 'proj-chatos-rebuild', '指导技术架构重构'],
    ['topic-oauth', 'conv-wang', '对齐法务审阅条款'],
    ['topic-oauth', 'proj-client-x', '作为交付合规基石'],
    ['topic-agent', 'conv-prof-zhang', '学术论文与算法选型'],
    ['topic-agent', 'proj-agent-core', '本地模型引擎研发'],
    ['topic-tokens', 'conv-sarah', 'UI暗色Token同步'],
    ['topic-tokens', 'know-2', '归档到设计系统规范表'],
  ];

  topicLinks.forEach(([tId, targetId, note]) => {
    if (nodes.some(n => n.id === tId) && nodes.some(n => n.id === targetId)) {
      links.push({
        id: `link-${tId}-${targetId}`,
        source: tId,
        target: targetId,
        label: '议题关联',
        relationType: 'references',
        createdAt: '2026-09-12',
        strength: 1.1,
        snippet: `[[${tId}]] 双链互联到 [[${targetId}]]: ${note}`,
      });
    }
  });

  // 7. Append Custom User Bi-Links
  customLinks.forEach(cl => {
    // Avoid duplicate link ID
    if (!links.some(l => l.id === cl.id)) {
      links.push(cl);
    }
  });

  // 8. Compute Bidirectional Affinity Metric (关联度) on all nodes
  const connectionMap = new Map<string, number>();
  const weightMap = new Map<string, number>();

  links.forEach(l => {
    const sId = typeof l.source === 'object' ? (l.source as any).id : l.source;
    const tId = typeof l.target === 'object' ? (l.target as any).id : l.target;
    const str = l.strength || 1;
    connectionMap.set(sId, (connectionMap.get(sId) || 0) + 1);
    connectionMap.set(tId, (connectionMap.get(tId) || 0) + 1);
    weightMap.set(sId, (weightMap.get(sId) || 0) + str);
    weightMap.set(tId, (weightMap.get(tId) || 0) + str);
  });

  nodes.forEach(n => {
    const count = connectionMap.get(n.id) || 0;
    const weight = weightMap.get(n.id) || 0;
    n.connectedCount = count;
    // Normalized affinity score (0 - 100) reflecting structural centrality and dynamic importance
    const baseBonus = n.type === 'project' ? 24 : n.type === 'date' ? 18 : n.type === 'conversation' ? 16 : 8;
    n.affinityScore = Math.min(99, Math.max(35, Math.round(baseBonus + Math.min(65, weight * 7.5))));
  });

  return { nodes, links };
}

// Inspect Bidirectional Links (Both Outgoing & Incoming Backlinks)
export interface NodeBidirectionalInspect {
  node: MindMapNode;
  outgoingLinks: {
    link: MindMapBiLink;
    targetNode: MindMapNode;
  }[];
  incomingBacklinks: {
    link: MindMapBiLink;
    sourceNode: MindMapNode;
  }[];
  totalConnections: number;
}

export function inspectNodeBidirectional(
  nodeId: string,
  nodes: MindMapNode[],
  links: MindMapBiLink[]
): NodeBidirectionalInspect | null {
  const node = nodes.find(n => n.id === nodeId);
  if (!node) return null;

  const nodeMap = new Map<string, MindMapNode>();
  nodes.forEach(n => nodeMap.set(n.id, n));

  const outgoingLinks: { link: MindMapBiLink; targetNode: MindMapNode }[] = [];
  const incomingBacklinks: { link: MindMapBiLink; sourceNode: MindMapNode }[] = [];

  links.forEach(link => {
    const sId = typeof link.source === 'object' ? (link.source as any).id : link.source;
    const tId = typeof link.target === 'object' ? (link.target as any).id : link.target;

    if (sId === nodeId && nodeMap.has(tId)) {
      outgoingLinks.push({
        link,
        targetNode: nodeMap.get(tId)!,
      });
    } else if (tId === nodeId && nodeMap.has(sId)) {
      incomingBacklinks.push({
        link,
        sourceNode: nodeMap.get(sId)!,
      });
    }
  });

  return {
    node,
    outgoingLinks,
    incomingBacklinks,
    totalConnections: outgoingLinks.length + incomingBacklinks.length,
  };
}

// Generate dynamic streaming conversational simulation nodes (Cosmograph showcase style)
export function generateSimulatedThoughtPulse(
  currentStep: number,
  existingNodes: MindMapNode[],
  existingProjects: Project[]
): { newNode: MindMapNode; newLinks: MindMapBiLink[] } {
  const PULSE_CONCEPTS = [
    { label: 'WebSocket 实时事件分发', type: 'topic' as const, color: '#38bdf8', targetProject: 'proj-chatos-rebuild', note: '实时推送会话新消息与图谱位置同步' },
    { label: '增量哈希幂等判定机', type: 'todo' as const, color: '#f59e0b', targetProject: 'proj-chatos-rebuild', note: '连续整理时严格杜绝任务重复提取' },
    { label: 'OAuth 数据沙箱隔离报告', type: 'knowledge' as const, color: '#10b981', targetProject: 'proj-client-x', note: '完成法务审核归档与沙箱加密' },
    { label: '本地多模态向量粗排', type: 'topic' as const, color: '#a855f7', targetProject: 'proj-agent-core', note: '张教授建议的向量检索加速算法' },
    { label: 'Gary-UI 触控面积合规检查', type: 'todo' as const, color: '#ec4899', targetProject: 'proj-chatos-rebuild', note: '确保移动端所有点击按钮均 ≥ 44px' },
    { label: 'Thinking 决策推演历史回放', type: 'topic' as const, color: '#6366f1', targetProject: 'proj-agent-core', note: '自动留存本地模型的树形思考轨迹' },
  ];

  const concept = PULSE_CONCEPTS[currentStep % PULSE_CONCEPTS.length];
  const pulseId = `dyn-pulse-${Date.now()}-${currentStep}`;

  const newNode: MindMapNode = {
    id: pulseId,
    label: `${concept.label} #${currentStep + 1}`,
    type: concept.type,
    timestamp: '刚刚 (动态生成)',
    dateKey: '2026-09-15',
    color: concept.color,
    radius: 13,
    details: concept.note,
    badge: 'Cosmo 仿真流',
    clusterId: concept.targetProject,
  };

  const newLinks: MindMapBiLink[] = [];

  // Link to target project
  newLinks.push({
    id: `dyn-link-${pulseId}-${concept.targetProject}`,
    source: pulseId,
    target: concept.targetProject,
    label: '动态产生双链',
    relationType: 'produces',
    createdAt: '2026-09-15',
    strength: 1.4,
    snippet: `动态仿真引擎基于会话脉冲生成: [[${newNode.label}]] 绑定到 [[${existingProjects.find(p => p.id === concept.targetProject)?.name || concept.targetProject}]]`,
  });

  // Link to a random conversation
  const convCandidates = existingNodes.filter(n => n.type === 'conversation');
  if (convCandidates.length > 0) {
    const randomConv = convCandidates[Math.floor(Math.random() * convCandidates.length)];
    newLinks.push({
      id: `dyn-link-${pulseId}-${randomConv.id}`,
      source: randomConv.id,
      target: pulseId,
      label: '会话涌现',
      relationType: 'references',
      createdAt: '2026-09-15',
      strength: 1.1,
      snippet: `会话 [[${randomConv.label}]] 激发涌现出思考节点: [[${newNode.label}]]`,
    });
  }

  return { newNode, newLinks };
}
