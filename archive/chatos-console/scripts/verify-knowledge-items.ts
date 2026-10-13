import assert from 'node:assert/strict';
import { mapGatewayKnowledgeItem } from '../src/core/knowledgeItems.ts';

const image = mapGatewayKnowledgeItem({
  id: 11,
  messageId: 2,
  conversationId: 'team',
  conversationTitle: 'Team',
  sentAt: 1700000000000,
  kind: 'image',
  status: 'resolved',
  text: '方案图',
  assets: [{ id: 21, kind: 'image', status: 'resolved', fileName: 'board.jpg', mimeType: 'image/jpeg', byteSize: 2048, contentUrl: '/api/assets/21' }],
});
assert.equal(image.fileType, 'image');
assert.equal(image.intakeStatus, 'stored');
assert.equal(image.thumbnailUrl, '/api/gateway/assets/21');

const missingFile = mapGatewayKnowledgeItem({
  id: 12,
  messageId: 3,
  conversationId: 'team',
  conversationTitle: 'Team',
  sentAt: 1700000000000,
  kind: 'attachment',
  status: 'unresolved',
  text: '',
  assets: [{ id: 22, kind: 'attachment', status: 'unresolved', fileName: 'spec.pdf', mimeType: 'application/pdf' }],
});
assert.equal(missingFile.fileType, 'pdf');
assert.equal(missingFile.intakeStatus, 'pending_fetch');
assert.match(missingFile.failureReason || '', /尚未解析/);

const sensitiveLink = mapGatewayKnowledgeItem({
  id: 13,
  messageId: 4,
  conversationId: 'team',
  conversationTitle: 'Team',
  sentAt: 1700000000000,
  kind: 'link',
  status: 'resolved',
  text: 'accessToken=do-not-copy https://example.com/private?sessionToken=do-not-copy',
});
assert.equal(sensitiveLink.title, '疑似敏感内容，已隐藏');
assert.doesNotMatch(sensitiveLink.fileName, /do-not-copy/);

console.log('Gateway knowledge items map to honest local records.');
