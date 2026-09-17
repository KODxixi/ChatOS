// Run: node --experimental-strip-types tests/format.test.ts
import { formatNumber, formatSize, formatDuration, truncateUrl, extractProjectName, formatTime, formatDateTime } from '../src/utils/format.ts'

let pass = 0, fail = 0
function eq(name: string, got: unknown, want: unknown) {
  const ok = got === want
  if (ok) { pass++; console.log(`  ok  ${name}`) }
  else { fail++; console.error(`  FAIL ${name}\n     got:  ${JSON.stringify(got)}\n     want: ${JSON.stringify(want)}`) }
}

console.log('formatNumber')
eq('null -> 0', formatNumber(null), '0')
eq('thousands', formatNumber(12345), '12,345')

console.log('formatSize')
eq('bytes', formatSize(512), '512 B')
eq('kb', formatSize(2048), '2.0 KB')
eq('mb', formatSize(5 * 1024 * 1024), '5.0 MB')
eq('null', formatSize(null), '—')

console.log('formatDuration')
eq('seconds', formatDuration(45), '45″')
eq('m:ss', formatDuration(95), '1:35')
eq('null', formatDuration(null), '—')

console.log('truncateUrl')
eq('host only', truncateUrl('https://www.example.com/x'), 'example.com/x')
eq('truncate long', truncateUrl('https://example.com/' + 'a'.repeat(60), 20).endsWith('…'), true)
eq('invalid', truncateUrl('notaurl'), 'notaurl')

console.log('extractProjectName')
eq('strip 项目群', extractProjectName('观潮府项目群'), '观潮府')
eq('strip HYP', extractProjectName('HYP-壹号院'), '壹号院')
eq('fallback', extractProjectName('你好'), '你好')

console.log('formatTime / formatDateTime')
eq('null time', formatTime(null), '—')
eq('datetime format', /^\d{2}-\d{2} \d{2}:\d{2}$/.test(formatDateTime(new Date(2026, 0, 2, 9, 5).getTime())), true)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
