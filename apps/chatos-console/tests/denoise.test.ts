// Lightweight node test runner (no vitest needed). Run: node --experimental-strip-types tests/denoise.test.ts
import { previewLine, cleanText, isRealPerson, classifyMessage } from '../src/utils/denoise.ts'

let pass = 0, fail = 0
function eq(name: string, got: unknown, want: unknown) {
  const ok = got === want
  if (ok) { pass++; console.log(`  ok  ${name}`) }
  else { fail++; console.error(`  FAIL ${name}\n     got:  ${JSON.stringify(got)}\n     want: ${JSON.stringify(want)}`) }
}

console.log('previewLine')
eq('image media label', previewLine('[图片]'), '🖼 图片')
eq('voice media label', previewLine('[语音]'), '🎤 语音')
eq('call media label', previewLine('[语音通话]'), '📞 通话')
eq('group sender prefix', previewLine('今天合并', '张晗').startsWith('张晗: '), true)
eq('no double prefix', previewLine('张晗: 今天合并', '张晗'), '张晗: 今天合并')
eq('filler stripped', previewLine('嗯嗯'), '暂无文字消息')
eq('truncate ~40 chars', previewLine('A'.repeat(80)).length <= 42, true)
eq('emoji shortcode removed', !previewLine('[微笑]你好').includes('微笑'), true)

console.log('cleanText')
eq('strip shortcode', cleanText('[微笑]你好'), '你好')
eq('strip mention', cleanText('@张三 今天PPT').includes('张三'), false)
eq('strip leading name', cleanText('缪旭东 ZQi 今天PPT得拿出来'), '今天PPT得拿出来')
eq('strip unicode emoji', cleanText('好😀的'), '好的')

console.log('isRealPerson')
eq('wxid true', isRealPerson('wxid_abc123'), true)
eq('url false', isRealPerson('https://x.com'), false)
eq('digits false', isRealPerson('12345'), false)
eq('name true', isRealPerson('张晗'), true)

console.log('classifyMessage')
eq('filler -> media', classifyMessage('好的').isMedia, true)
eq('plain text', classifyMessage('明天合并到一起').isMedia, false)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
