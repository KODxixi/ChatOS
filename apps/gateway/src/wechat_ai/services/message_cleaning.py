"""Shared message-text cleaning helpers for ChatOS derived-data APIs.

These utilities are used by the conversation summary, keyPeople, todo
extraction and conversation-preview endpoints.  The goal is to strip the
raw WeChat/GChat import noise (``wxid:\\n`` line headers, bracket emoticons,
unicode emoji, @mention punctuation, filler words) and to decide whether a
sender identifier represents a real person vs. an internal/system/URL entity.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# WeChat bracket emoticon shortcodes, e.g. [旺柴], [微笑], [破涕为笑]
_BRACKET_EMOJI_RE = re.compile(r"\[[\u4e00-\u9fa5A-Za-z]{1,8}\]")

# Unicode emoji + pictographs
_UNICODE_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "️‍"
    "]"
)

# @mention: capture the name after @ so we can keep it and drop the @ sign.
_MENTION_RE = re.compile(r"@([^\s@，。！、:：；]{1,20})")

# Leading sender header line: "wxid_xxx:" or "昵称:" followed by newline.
_SENDER_HEADER_RE = re.compile(r"^[^\n:：]{1,40}[:：]\s*\n")

# Leading filler words at the start of a sentence (repetitions collapsed).
_LEADING_FILLER_RE = re.compile(
    r"^(好的|好滴|好呀|好嘞|收到|明白|了解|嗯嗯|嗯呢|可以|哦|噢|嗯|呃|啊|哎|"
    r"那|这个|那个|就是|然后|对|是|行|好)+[，,。.！!、]?\s*"
)

# Trailing action-verb noise that must never leak into a summary/todo.
_ACTION_NOISE_RE = re.compile(r"(激动地喊|激动地说|开心地说|生气地说|回复|发送了|说|喊|叫道|笑道|怒道)")

# URLs
_URL_RE = re.compile(r"https?://\S+|www\.\S+")

# Pure media / placeholder types (not real text content).
MEDIA_TYPES = {
    "image", "voice", "video", "sticker", "location", "call",
    "attachment", "unsupported_10000", "card",
}

_MEDIA_LABELS = {
    "image": "[图片]",
    "voice": "[语音]",
    "call": "[语音通话]",
    "video": "[视频]",
    "location": "[位置]",
    "attachment": "[文件]",
    "sticker": "[表情]",
}


# ---------------------------------------------------------------------------
# Filler / confirmation sets
# ---------------------------------------------------------------------------

FILLER_EXACT = {
    "哦", "噢", "喔", "嗯", "嗯嗯", "嗯嗯嗯", "嗯呢", "好", "好的", "好滴",
    "好呀", "好嘞", "好喔", "收到", "哈哈", "哈哈哈", "哈哈哈哈", "呵呵",
    "嘿嘿", "马上", "对", "是", "行", "行吧", "ok", "OK", "Ok", "是的",
    "可以", "在", "在的", "了解", "明白", "赞", "1", "2", "好的好的",
    "收到收到", "是的是的", "okok", "OKOK", "no", "NO",
}

# Action verbs a real todo must contain.
ACTION_VERBS_RE = re.compile(
    r"准备|提交|发送|发一下|发我|确认|修改|完成|下载|整理|回复|联系|"
    r"预约|购买|支付|填写|审核|审批|安排|跟进|落实|处理|更新|同步|"
    r"合并|部署|测试|修复|设计|开发|写|做|弄|搞|冲|报|申请|报销|"
    r"对齐|订|签|改|补|对接|盯|核对|梳理|推进|发"
)

# Time commitments that imply a todo even without an explicit verb.
# "前/之前" only counts as a deadline when preceded by a time expression,
# otherwise phrases like "保持在之前的流中" false-positive.
_TIME_COMMIT_RE = re.compile(
    r"明天|今天|后天|大后天|周一|周二|周三|周四|周五|周六|周日|"
    r"星期[一二三四五六日天]|下周|本周|本周内|几点前|尽快|月底|"
    r"下班前|今晚|今早|周[一二三四五六日天]?前"
    r"|(\d{1,2}\s*[点时]|\d{1,2}[:：]\d{2}|下班|周[一二三四五六日天])[之]?前"
)

# Imperative: @someone followed (within a few chars) by an action verb.
_IMPERATIVE_RE = re.compile(
    r"@[^\s@，。！、:：；]{1,20}\s*[，,]?\s*\S{0,10}"
    r"(准备|提交|发送|确认|修改|完成|回复|联系|安排|跟进|处理|更新|同步|写|做|弄|搞|报|申请|发一下|发我|核对)"
)

# Exclusion patterns.
_SYSTEM_RE = re.compile(
    r"AGENTS\.md|system prompt|你是一个|你是一位|你是一|system|prompt|"
    r"技能|skill|SkilL", re.IGNORECASE
)
_AD_RE = re.compile(
    r"app[:：]|下载链接|推广|优惠|返利|一键报销|扫码|点击链接|优惠券|福利",
    re.IGNORECASE,
)
_EMOTION_RE = re.compile(
    r"来不及|怎么办|好累|好烦|郁闷|开心|哈哈|笑死|难过|焦虑|崩溃|无语|"
    r"服了|摆烂|别想|不要想|不想|算了|无所谓|"
    r"蠢一比|傻逼|他妈|草了|垃圾|完蛋|牛逼|卧槽|麻了|离谱"
)
_PURE_QUESTION_RE = re.compile(r"^[^\n]{0,40}[?？]\s*$")
# Ends with a Chinese question particle (吗/么/呢/吧) — rhetorical, not a task.
_QUESTION_TAIL_RE = re.compile(r"[吗么呢吧嘛?？]\s*$")
# Starts with a clear imperative cue (so a question can still be a task).
_IMPERATIVE_START_RE = re.compile(
    r"^(帮我|请|麻烦|@|我们|大家|记得|提醒|需要|赶紧|赶快|尽快|你们)"
)
# Calendar / meeting / card auto-messages that aren't real chat content.
_SYSTEM_INVITE_RE = re.compile(
    r"邀请您参加|会议主题|会议时间|点击链接|腾讯会议|企业微信邀请|"
    r"收到一条|正在输入|以上是|本条内容|链接：|链接:",
    re.IGNORECASE,
)

# --- Round-2 todo exclusion patterns -----------------------------------
# Past-tense / already-done states (only exclude when no future marker present).
_PAST_TENSE_RE = re.compile(
    r"搞定了|已经|完成了|做完了|弄好了|处理完了|收好了|接起来了|发出去了|"
    r"弄完了|定下来了|商量好了|已经完成|已经做完|"
    r"上次就|上次发|之前发|已经发|发过了|上次就发过"
)
# Number/unit-heavy distance or data descriptions (not a task).
_NUMERIC_DESC_RE = re.compile(r"(\d+(\.\d+)?\s*(公里|km|分钟|小时|米|%|个|条|张))")

# --- Round-3 todo exclusion patterns ----------------------------------
# Status reports: 业主/领导/客户/甲方 + 明确了/决定了/确认了/说了 (no future action).
_STATUS_REPORT_RE = re.compile(
    r"^(业主|领导|客户|甲方|老板|同事).{0,8}(明确了|决定了|确认了|说了|要求的是|定了|拍板了)"
)
# Stock / investment discussion.
_STOCK_RE = re.compile(
    r"长线|短线|选股|持仓|割肉|涨停|跌停|加仓|减仓|被套|解套|牛市|熊市|"
    r"基金|股票|炒股|A股|港股|美股|大盘|回调|加仓|清仓|建仓"
)
# Work / architecture action verbs that override the stock filter.
_WORK_VERB_RE = re.compile(
    r"发我|提交|准备|修改|设计|画图|画|做|写|整理|改|补|对接|安排|跟进|发一下"
)
# Existential / descriptive openers (not a commitment).
_OBSERVE_OPEN_RE = re.compile(
    r"^(钉钉|飞书|企业微信|微信).{0,6}有个|^有个|^呈现|^示范区|^这个宽度|^就是"
)
# Leading name tokens to strip from a todo text (e.g. "缪旭东 ZQi 今天...").
_LEADING_NAME_RE = re.compile(
    r"^([\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z0-9_·]{1,15})\s+"
)
# Concrete YYYY-MM-DD / M月D日 dates inside text.
_INLINE_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})月(\d{1,2})[日号]?")

# --- Round-4 todo exclusion patterns -----------------------------------
# Weather / small-talk words (no work action => not a todo).
_WEATHER_RE = re.compile(
    r"天气|太阳|下雨|下雪|刮风|温度|气温|舒服|不热|挺舒服|凉快|闷热"
)
# Descriptive / observation openers.
_DESCRIBE_RE = re.compile(r"^(跟.*一样|看起来|好像|感觉|蹲着|挺.{0,4}的|真.{0,3}的)")
# Chatty laughter without any action verb.
_CHATTY_RE = re.compile(r"^(哈哈+|呵呵+|嘿嘿+|嘻嘻+|嗯嗯+|哦哦+|啊啊+|哎呀)")
# Trailing pure name token (Chinese name / English name / wxid).
_TRAILING_NAME_RE = re.compile(
    r"[\s，。、;；]+([\u4e00-\u9fa5]{2,4}|[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9_·]{1,15}|wxid_\w+)$"
)

# --- Round-5 todo exclusion patterns ----------------------------------
# Polite request words that KEEP a question as a todo.
_REQUEST_RE = re.compile(r"帮我|给我|发我|麻烦|请|能不能|可以.*吗|帮忙")
# Prediction / estimate words.
_PREDICT_RE = re.compile(r"估计|大概|可能|也许|说不定|预计|应该.{0,4}会")
# Concrete action verbs.
_CONCRETE_VERB_RE = re.compile(
    r"发|做|写|画|提交|准备|修改|整理|安排|跟进|对接|改|补|画|写|联系|买|订"
)
# Offer / help-me-out phrases (not a committed todo).
_OFFER_RE = re.compile(r"可以跟我说|随时找我|有需要|我帮你|找我就行|跟我说一下|找我")
# Joke / sarcasm: "能不能...呢" without a real object.
_JOKE_Q_RE = re.compile(r"能不能.{0,20}呢\??$")
_JOKE_OBJECT_RE = re.compile(r"文件|资料|方案|数据|代码|帮我|给我|发我|文档|图纸")
# Mid-sentence yes/no question (能做吗 / 可以吗 / 行不行 吗).
_MID_QUESTION_RE = re.compile(r"(能|可以|行|好|要不要|有没有|会不会|能不能|是不是|对不对).{0,10}吗")
# Explanation / supplementary / conditional openers.
_EXPLAIN_OPEN_RE = re.compile(
    r"^(不过|因为|其实|反正|总之|另外|而且|但是|只是|就是说|也就是说|"
    r"如果|假如|要是|万一|由于|毕竟|虽然|当然|确实|感觉|我觉得|我认为|"
    r"你觉得|你看|你说|没事儿|没事|没关系|无所谓|问题是|关键是|其实呢|"
    r"类似|我搞|我们做|那个的|这种的)"
)
# File paths (Windows / UNC / Unix absolute).
_FILEPATH_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\|^/|^~/")
_PATH_SEG_RE = re.compile(r"\\\\[^\s\\]{1,}\\")
# Profanity / emotional outbursts at the start.
_PROFANITY_RE = re.compile(r"^(尼玛|卧槽|妈的|我去|醉了|他妈|娘的|靠了|艹)")
# Pure state / impossibility.
_PURE_STATE_RE = re.compile(r"现在做不了|做不了|没办法|不可以|做不到|不行啊|没法搞|搞不定")
# Leading punctuation after trim.
_PUNCT_START_RE = re.compile(r"^[，。、；：？！,.;:!?\-—~～\*#\s]+")

# Noun-keyword extraction (for keyTopics). CJK runs + stop-char filtering.
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}")
_TOPIC_STOP_CHARS = set(
    "的了吗呢吧啊哦嗯在是有要能会去来说看想知道觉得认为发现需要应该可以"
    "可能不你我他她它们这个那个就都也就还很被把对从到和与及或之而且但并"
)


def extract_noun_grams(text: str) -> dict[str, int]:
    """Crude noun-ish bigram/trigram frequency extractor.

    Splits CJK runs into 2/3-char grams and drops any gram containing a
    verb/particle/pronoun character.  Returns {gram: count}.
    """
    out: dict[str, int] = {}
    for run in _CJK_RUN_RE.findall(text or ""):
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                if any(ch in _TOPIC_STOP_CHARS for ch in g):
                    continue
                out[g] = out.get(g, 0) + 1
    return out

_SYSTEM_SENDERS = {
    "newsapp", "weixin", "tmessage", "filehelper", "fmessage", "qmessage",
    "mmnews", "wechat", "message", "newsapp_qq", "medianote", "floatbottle",
}


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def strip_sender_header(content: str) -> str:
    """Drop a leading ``wxid:\\n`` / ``昵称:\\n`` group prefix line."""
    s = (content or "").strip()
    m = _SENDER_HEADER_RE.match(s)
    if m:
        s = s[m.end():].strip()
    return s


def strip_emoji(text: str) -> str:
    """Remove unicode emoji and WeChat bracket emoticon shortcodes."""
    s = _BRACKET_EMOJI_RE.sub("", text)
    s = _UNICODE_EMOJI_RE.sub("", s)
    return s.strip()


def clean_mentions(text: str) -> str:
    """Keep the mentioned person's name, drop the leading ``@``."""
    return _MENTION_RE.sub(r"\1", text)


def strip_mentions(text: str) -> str:
    """Remove the @mention and the name that follows it entirely."""
    return re.sub(r"@[^\s@，。！、:：；]{1,20}\s*", "", text)


def strip_leading_filler(text: str) -> str:
    return _LEADING_FILLER_RE.sub("", text).strip()


def is_filler(text: str) -> bool:
    """Return True for pure confirmations / emoticon-only / punctuation."""
    s = strip_emoji(text).strip().rstrip("。，！!?~～.、 ")
    if s in FILLER_EXACT:
        return True
    if not re.search(r"[\u4e00-\u9fa5A-Za-z]", s):
        return True
    return False


def clean_summary_body(content: str) -> str:
    """Full clean for summary/todo text: header, emoji, mentions, lead filler."""
    s = strip_sender_header(content)
    s = strip_emoji(s)
    s = clean_mentions(s)
    s = s.replace("\u2005", " ").replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = strip_leading_filler(s)
    return s


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。、 ") + "…"


# ---------------------------------------------------------------------------
# Person / sender classification
# ---------------------------------------------------------------------------

def looks_like_wxid(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if n.startswith("wxid_"):
        return True
    if "@openim" in n or "@chatroom" in n or "@weclaw" in n:
        return True
    # bare wxid / wechat id: all lowercase letters+digits+underscore, 10+ chars.
    # Mixed-case or dotted strings like "JoyBoy"/"h.sd"/"Jason@yang" are nicknames.
    if re.fullmatch(r"[a-z0-9_]{10,30}", n):
        return True
    return False


def is_non_person(name: str) -> bool:
    """True if this sender label is not a real human being."""
    n = (name or "").strip()
    if not n:
        return True
    low = n.lower()
    if low in _SYSTEM_SENDERS:
        return True
    if "://" in n or n.startswith("http") or n.startswith("www."):
        return True
    # internal GChat / agent senders
    if ":sender:" in n or "@weclaw" in n or n.endswith("@chatroom"):
        return True
    # pure long number (QQ id) — ambiguous, keep readable nicknames but drop bare ids
    if re.fullmatch(r"\d{6,}", n):
        return True
    # IPv4
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", n):
        return True
    return False


def truncate_wxid(name: str) -> str:
    n = (name or "").strip()
    if len(n) <= 8:
        return n + "…"
    return n[:8] + "…"


# ---------------------------------------------------------------------------
# Derived wxid -> display-name map (no contact table in this DB)
# ---------------------------------------------------------------------------

_person_map_cache: dict[str, str] | None = None


def get_person_map(connection: sqlite3.Connection) -> dict[str, str]:
    """Build a sender_id -> readable display-name map from messages.

    There is no contact table in the derived DB, so the most frequent
    non-wxid ``sender_name`` per ``sender_id`` is used as the nickname.
    """
    global _person_map_cache
    if _person_map_cache is not None:
        return _person_map_cache

    rows = connection.execute(
        """
        SELECT sender_id, sender_name, COUNT(*) AS c
        FROM messages
        WHERE sender_id IS NOT NULL AND sender_id != ''
          AND sender_name IS NOT NULL AND sender_name != ''
        GROUP BY sender_id, sender_name
        """
    ).fetchall()

    best: dict[str, tuple[str, int]] = {}
    for r in rows:
        sid, sname, cnt = r[0], r[1], r[2]
        if is_non_person(sname) or looks_like_wxid(sname):
            continue
        cur = best.get(sid)
        if cur is None or cnt > cur[1]:
            best[sid] = (sname, cnt)
    _person_map_cache = {sid: name for sid, (name, _c) in best.items()}
    return _person_map_cache


def resolve_person_name(
    sender_id: str | None,
    fallback_name: str | None,
    person_map: dict[str, str],
) -> str | None:
    """Return a displayable person name, or None if it is not a person."""
    sid = (sender_id or "").strip()
    name = (fallback_name or "").strip()

    if sid and sid in person_map:
        name = person_map[sid]
    elif name and not looks_like_wxid(name) and not is_non_person(name):
        pass  # use the readable fallback
    elif sid and sid in person_map:
        name = person_map[sid]
    else:
        # no readable name available
        if sid and not is_non_person(sid):
            return truncate_wxid(sid)
        return None

    if is_non_person(name):
        # try the map one more time
        if sid and sid in person_map and not is_non_person(person_map[sid]):
            name = person_map[sid]
        else:
            return None
    return name


def invalidate_person_map_cache() -> None:
    global _person_map_cache
    _person_map_cache = None


# --- Public aliases for constants used by API layer ---------------------
MEDIA_LABELS = _MEDIA_LABELS
ACTION_NOISE_RE = _ACTION_NOISE_RE
URL_RE = _URL_RE
SYSTEM_INVITE_RE = _SYSTEM_INVITE_RE
EXPLAIN_OPEN_RE = _EXPLAIN_OPEN_RE
TIME_COMMIT_RE = _TIME_COMMIT_RE
IMPERATIVE_RE = _IMPERATIVE_RE
QUESTION_TAIL_RE = _QUESTION_TAIL_RE
MID_QUESTION_RE = _MID_QUESTION_RE
