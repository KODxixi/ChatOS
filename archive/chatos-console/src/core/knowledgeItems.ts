import type { IntakeStatus, KnowledgeItem } from '../types';

export const KNOWLEDGE_KINDS = ['image', 'attachment', 'audio', 'video', 'link'] as const;

interface GatewayAsset {
  id: number;
  kind: string;
  status: string;
  fileName?: string | null;
  mimeType?: string | null;
  byteSize?: number | null;
  contentUrl?: string | null;
}

export interface GatewayKnowledgeItem {
  id: number;
  messageId: number;
  conversationId: string;
  conversationTitle: string;
  sentAt: number;
  kind: string;
  status: string;
  text: string;
  assets?: GatewayAsset[];
}

const formatBytes = (value?: number | null) => {
  if (!value) return '大小未知';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
};

const SENSITIVE_CONTENT = /(?:access[_-]?token|session[_-]?token|api[_-]?key|authorization\s*[:=]\s*bearer|client[_-]?secret|private[_-]?key|password|passwd|密码|do not share any part)/i;
const URL_PATTERN = /https?:\/\/[^\s<>"']+/gi;

const safeDisplayText = (kind: string, text: string) => {
  const value = text.trim();
  if (!value) return '';
  if (SENSITIVE_CONTENT.test(value)) return '疑似敏感内容，已隐藏';
  if (kind !== 'link') return value;
  return value.match(URL_PATTERN)?.slice(0, 5).join(' · ') || '[链接]';
};

const fileType = (item: GatewayKnowledgeItem, asset?: GatewayAsset): KnowledgeItem['fileType'] => {
  if (item.kind === 'image') return 'image';
  if (item.kind === 'audio') return 'audio';
  if (item.kind === 'video') return 'video';
  if (item.kind === 'link') return 'link';
  const name = (asset?.fileName || '').toLowerCase();
  if (asset?.mimeType === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
  if (/\.(zip|rar|7z|tar|gz)$/.test(name)) return 'archive';
  if (/\.(js|ts|tsx|jsx|py|json|yaml|yml|sql|html|css)$/.test(name)) return 'code';
  return 'doc';
};

const intakeStatus = (item: GatewayKnowledgeItem, asset?: GatewayAsset): IntakeStatus => {
  const status = asset?.status || item.status;
  if (status === 'resolved') return 'stored';
  if (status === 'processing') return 'processing';
  if (status === 'failed') return 'failed';
  return asset || item.kind !== 'link' ? 'pending_fetch' : 'pending_confirmation';
};

export const mapGatewayKnowledgeItem = (item: GatewayKnowledgeItem): KnowledgeItem => {
  const asset = item.assets?.[0];
  const displayText = safeDisplayText(item.kind, item.text);
  const mediaUrl = asset?.contentUrl
    ? asset.contentUrl.replace(/^\/api(?=\/)/, '/api/gateway')
    : undefined;
  const name = asset?.fileName || (item.kind === 'link' ? displayText : `${item.kind}-${item.messageId}`);
  return {
    id: String(item.id),
    title: displayText || name,
    fileName: name,
    fileType: fileType(item, asset),
    fileSize: formatBytes(asset?.byteSize),
    thumbnailUrl: item.kind === 'image' ? mediaUrl : undefined,
    mediaUrl,
    contentHash: `source:${item.conversationId}:${item.messageId}:${item.id}`,
    intakeStatus: intakeStatus(item, asset),
    sourceConversationId: item.conversationId,
    sourceConversationName: item.conversationTitle,
    sourceMessageId: String(item.messageId),
    createdAt: new Date(item.sentAt).toLocaleString('zh-CN', { hour12: false }),
    failureReason: (asset?.status || item.status) === 'unresolved' ? '原文件尚未解析到本地资源库' : undefined,
  };
};
