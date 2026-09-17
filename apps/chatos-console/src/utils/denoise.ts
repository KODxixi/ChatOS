// Message preview denoising: strip emoji, filler words, repeated punctuation,
// and classify pure media/system placeholders.

export type DenoisedKind =
  | 'text'
  | 'image'
  | 'voice'
  | 'video'
  | 'file'
  | 'emoji'
  | 'call'
  | 'location'
  | 'card'

export interface DenoisedMessage {
  kind: DenoisedKind
  text: string
  /** true when the original was pure noise/media (no meaningful text left). */
  isMedia: boolean
}

const MEDIA_TAG_MAP: Record<string, DenoisedKind> = {
  '[图片]': 'image',
  '[表情]': 'emoji',
  '[动画表情]': 'emoji',
  '[语音]': 'voice',
  '[视频]': 'video',
  '[附件]': 'file',
  '[文件]': 'file',
  '[语音通话]': 'call',
  '[视频通话]': 'call',
  '[位置]': 'location',
  '[名片]': 'card',
  '[红包]': 'file',
  '[转账]': 'file',
}

// WeChat-style emoji shortcodes: [微笑] [破涕为笑] etc.
const WX_EMOJI_RE = /\[(?:微笑|撇嘴|色|发呆|得意|流泪|害羞|闭嘴|睡|大哭|尴尬|发怒|调皮|呲牙|惊讶|难过|酷|冷汗|抓狂|吐|偷笑|愉快|白眼|傲慢|饥饿|困|惊恐|流汗|憨笑|大兵|奋斗|咒骂|疑问|嘘|晕|折磨|衰|骷髅|敲打|再见|擦汗|抠鼻|鼓掌|糗大了|坏笑|左哼哼|右哼哼|哈欠|鄙视|委屈|快哭了|阴险|亲亲|吓|可怜|菜刀|西瓜|啤酒|篮球|乒乓|咖啡|饭|猪头|玫瑰|凋谢|爱心|心碎|蛋糕|闪电|炸弹|刀|足球|瓢虫|便便|月亮|太阳|礼物|拥抱|强|弱|握手|胜利|抱拳|勾引|拳头|差劲|爱你|NO|OK|爱情|飞吻|跳跳|发抖|怄火|转圈|磕头|回头|跳绳|挥手|激动|街舞|献吻|左太极|右太极|破涕为笑|让我看看|嘿哈|捂脸|奸笑|机智|皱眉|耶|喝茶|加油!|加油)]/g

// Unicode emoji ranges.
const EMOJI_RE =
  /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}\u{FE00}-\u{FE0F}\u{200D}\u{20E3}]/gu

// Pure filler / acknowledgement messages (when the whole message is only this).
const FILLER_RE =
  /^(啊+|哦+|噢+|嗯+|呃+|诶+|唉+|哈+|呵+|嘿+|嘻+|哎+|呀+|哟+|哇+|嗯哼+|好的|好滴|好嘞|好呀|好哒|好|行|嗯呢|收到|收到啦|妥了|了解|明白|知道了|OK|ok|Ok|可以|成|在|在的|嗯嗯|哦哦|哈哈|呵呵|嘿嘿|嘻嘻|嗯嗯嗯|噢噢|嗯嗯好|好的好的|收到收到|辛苦了|谢谢|感谢|多谢|晚安|早安|早|早啊|在吗|在么)$/u

export function classifyMessage(raw: string | null | undefined): DenoisedMessage {
  if (!raw) return { kind: 'text', text: '', isMedia: true }

  const trimmed = raw.trim()
  const mediaKind = MEDIA_TAG_MAP[trimmed]
  if (mediaKind) return { kind: mediaKind, text: '', isMedia: true }

  let text = trimmed
  // Strip media tags embedded in larger text.
  text = text.replace(/\[(图片|表情|动画表情|语音|视频|附件|文件|语音通话|视频通话|位置|名片|红包|转账)\]/g, '')
  // Strip WeChat emoji shortcodes.
  text = text.replace(WX_EMOJI_RE, '')
  // Strip unicode emoji.
  text = text.replace(EMOJI_RE, '')
  // Collapse repeated punctuation and whitespace.
  text = text.replace(/[.．。…]{2,}/g, '…').replace(/[!！]{2,}/g, '！').replace(/[?？]{2,}/g, '？')
  text = text.replace(/[ \t　]+/g, ' ').replace(/\s+\n/g, '\n').trim()

  if (!text) return { kind: 'text', text: '', isMedia: true }
  if (FILLER_RE.test(text)) return { kind: 'text', text: '', isMedia: true }

  if (text.length > 60) text = text.slice(0, 60) + '…'
  return { kind: 'text', text, isMedia: false }
}

export const MEDIA_LABEL: Record<DenoisedKind, string> = {
  text: '消息',
  image: '图片',
  voice: '语音',
  video: '视频',
  file: '文件',
  emoji: '表情',
  call: '通话',
  location: '位置',
  card: '名片',
}

const MEDIA_ICON: Record<DenoisedKind, string> = {
  text: '💬', image: '🖼', voice: '🎤', video: '🎬', file: '📎', emoji: '😀', call: '📞', location: '📍', card: '🪪',
}

/** Build a compact preview line for a list item. */
export function previewLine(raw: string | null | undefined, sender?: string | null, max = 40): string {
  const d = classifyMessage(raw)
  if (d.isMedia) {
    return d.kind === 'text' ? '暂无文字消息' : `${MEDIA_ICON[d.kind]} ${MEDIA_LABEL[d.kind]}`
  }
  let text = d.text
  // strip leading filler words (哦/嗯/好的/收到/哈哈…) when they open a longer sentence
  text = text.replace(/^(哦+|嗯+|好的|收到|哈哈|呵呵|嗯嗯|哦哦|啊|噢|呃)[，,。. ]*/u, '').trim()
  if (!text) return '暂无文字消息'
  // Backend may already prefix "sender: " for groups — don't double it.
  const s = (sender || '').trim()
  const alreadyPrefixed = s && (text.startsWith(s + ':') || text.startsWith(s + '：'))
  if (!alreadyPrefixed && s) text = `${s}: ${text}`
  if (text.length > max) text = text.slice(0, max) + '…'
  return text
}

/** Strip emoji shortcodes / unicode emoji from any text (defensive, for detail panels). */
export function cleanText(raw: string | null | undefined, max = 0): string {
  if (!raw) return ''
  let t = raw.trim()
  t = t.replace(WX_EMOJI_RE, '')
  t = t.replace(EMOJI_RE, '')
  // Strip leading @mentions, e.g. @张三 @Bob
  t = t.replace(/^(@[\w一-龥·_-]{1,20}(\s|$))+/g, '').trim()
  // Strip a leading "Name:" / "Name：" attribution prefix.
  t = t.replace(/^[^\s:：]{1,20}[:：]\s*/, (m) => (/[一-龥a-zA-Z]/.test(m) && !/^\d/.test(m) ? '' : m)).trim()
  // Strip up to two leading bare name tokens (e.g. "缪旭东 ZQi 今天..."):
  // pure CJK 2-4 chars, pure latin 2-15 chars, or wxid_.
  t = t.replace(/^(?:(?:[一-龥]{2,4}|[A-Za-z][A-Za-z0-9_]{1,14}|wxid_\w+)\s+){1,2}/, '').trim()
  t = t.replace(/\s{2,}/g, ' ').trim()
  if (max > 0 && t.length > max) t = t.slice(0, max) + '…'
  return t
}

/** Heuristic: is this a real person name (not wxid / url / pure digits)? */
export function isRealPerson(name: string): boolean {
  if (!name) return false
  if (/^(wxid_|gh_)/i.test(name)) return true
  if (/^(https?:|www\.)/i.test(name)) return false
  if (/^\d+$/.test(name)) return false
  if (name.length < 2) return false
  return true
}

/** Shorten raw IDs for display. */
export function displayName(name: string): string {
  if (/^wxid_/i.test(name)) return name.slice(0, 8) + '…'
  return name
}
