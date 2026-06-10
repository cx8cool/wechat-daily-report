#!/usr/bin/env python3
"""
WeChat Daily Report — Real Estate Edition
Generates an AI-powered local HTML briefing from WeChat data.

Requirements:
  npm install -g @canghe_ai/wechat-cli && sudo wechat-cli init
  pip install anthropic          # for AI analysis
  wxkey bootstrap                # for full message history (optional but recommended)

Usage:
  python3 daily_report.py
  python3 daily_report.py --no-browser
  python3 daily_report.py --days 2   # look back 2 days
"""

import json, subprocess, sys, webbrowser, argparse, re
from datetime import datetime, timedelta
from pathlib import Path

ROOT        = Path(__file__).parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE  = ROOT / ".state.json"
OUTPUT_FILE = ROOT / "report.html"

SPECIAL_SESSIONS = {"brandsessionholder", "@placeholder_foldgroup",
                    "brandservicesessionholder"}
OFFICIAL_PREFIXES = ("gh_",)


# ── CLI helpers ───────────────────────────────────────────────────────────────

def run_cli(*args, timeout=25):
    try:
        r = subprocess.run(["wechat-cli"] + list(args),
                           capture_output=True, text=True, timeout=timeout)
        d = json.loads(r.stdout)
        if not d.get("ok"):
            msg = d.get("error", {}).get("message", "")
            if "wxkey" not in msg and "task_for_pid" not in msg:
                print(f"  [wechat-cli] {' '.join(str(a) for a in args[:2])}: {msg[:100]}")
            return None
        return d.get("data", {})
    except Exception:
        return None


def run_sql(query, subdir, file, timeout=15):
    return run_cli("sql", query, "--subdir", subdir, "--file", file, timeout=timeout)


# ── State (new-contact tracking) ──────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── Data fetchers ─────────────────────────────────────────────────────────────

def get_new_contacts(state):
    """Contacts added since last run, tracked by max contact id."""
    data = run_sql(
        "SELECT id, username, nick_name, alias, remark FROM contact "
        "WHERE local_type=1 AND delete_flag=0 ORDER BY id DESC LIMIT 100",
        "contact", "contact.db"
    )
    if not data:
        return [], state.get("last_max_contact_id", 0)

    rows = data.get("rows", [])
    if not rows:
        return [], state.get("last_max_contact_id", 0)

    max_id = max(r["id"] for r in rows)
    last_id = state.get("last_max_contact_id", 0)

    # Skip official accounts (username starts with gh_)
    new = [r for r in rows
           if r["id"] > last_id
           and not any(str(r.get("username","")).startswith(p) for p in OFFICIAL_PREFIXES)]

    return new, max_id


def get_private_sessions(limit=200):
    data = run_cli("sessions", "--limit", str(limit), "--type-filter", "private")
    if not data:
        return []
    return [s for s in data.get("sessions", [])
            if s.get("username") not in SPECIAL_SESSIONS
            and not any(str(s.get("username","")).startswith(p) for p in OFFICIAL_PREFIXES)]


def get_unread_private(all_private):
    """Private sessions with unread messages (they messaged, you haven't replied)."""
    return [s for s in all_private if s.get("unread_count", 0) > 0]


def get_unread_groups(limit=100):
    data = run_cli("unread", "--limit", str(limit))
    if not data:
        return []
    return [s for s in data.get("sessions", [])
            if s.get("chat_type") == "group"
            and s.get("username") not in SPECIAL_SESSIONS]


def get_message_history(chat_name, days_back=1):
    """Full message history — requires wxkey bootstrap. Returns [] on failure."""
    after = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    data = run_cli("history", chat_name, "--after", after,
                   "--limit", "80", "--view", "agent")
    if not data:
        return []
    return data.get("messages", [])


def get_group_messages(group_name, days_back=1):
    return get_message_history(group_name, days_back)


# ── AI analysis ───────────────────────────────────────────────────────────────

def claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=600, api_key=""):
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text
    except Exception as e:
        print(f"  [Claude] {e}")
        return None


def analyze_clients(private_sessions, api_key, days_back=1):
    """
    AI analysis of client private chats.
    Uses full message history if wxkey available, falls back to session summary.
    Returns dict with must_followup, warming_up, todos, summary.
    """
    if not api_key:
        return None

    # Build conversation snippets
    convs = []
    has_full_history = False

    for s in private_sessions[:30]:  # cap at 30 clients per run
        name = s.get("display_name") or s.get("username", "?")
        summary = s.get("summary", "")
        unread = s.get("unread_count", 0)
        ts = fmt_time(s.get("last_timestamp"))

        # Try full history first
        msgs = get_message_history(name, days_back)
        if msgs:
            has_full_history = True
            lines = []
            for m in msgs[-40:]:  # last 40 messages
                sender = (m.get("sender_display_name") or m.get("sender") or "?")
                content = m.get("content") or m.get("text") or m.get("summary") or ""
                if content:
                    lines.append(f"  {sender}: {content[:250]}")
            if lines:
                convs.append(f"【{name}】（{ts}）\n" + "\n".join(lines))
        elif summary:
            # Fallback: just last message
            convs.append(f"【{name}】（{ts}，未读{unread}条）\n  最后消息：{summary}")

    if not convs:
        return None

    mode_note = "（含完整对话记录）" if has_full_history else "（仅最后一条消息，完整分析需运行 wxkey bootstrap）"

    prompt = f"""你是北美房地产销售顾问助手。以下是客户的微信聊天记录{mode_note}。

背景：这是西雅图房产经纪团队，服务北美华人买卖房需求，主要区域在西雅图/东区/北区。

请分析并输出以下JSON（严格JSON格式，不要任何其他文字）：

{{
  "summary": "一段话概括今天的整体客户状态",
  "must_followup": [
    {{"name": "客户名", "reason": "必须今天跟进的原因", "action": "建议具体行动", "urgency": "high或medium"}}
  ],
  "warming_up": [
    {{"name": "客户名", "signal": "升温信号描述", "from_stage": "之前状态", "to_stage": "现在状态"}}
  ],
  "todos_today": ["具体任务1", "具体任务2"],
  "todos_tomorrow": ["明天需要准备的事1", "明天需要准备的事2"],
  "do_not_miss": ["绝对不能漏掉的客户名1", "客户名2"]
}}

判断标准：
- must_followup：问了具体问题没回复、说「再想想」需要推一把、有看房意向、说「发给我看看」
- warming_up：从随便问问→问具体价格/学区、从考虑中→主动要约看房
- do_not_miss：即将决策、等你回复、或者已经很久没联系快冷掉的

聊天记录：

{chr(10).join(convs)}"""

    raw = claude(prompt,
                 model="claude-sonnet-4-6",
                 max_tokens=1200,
                 api_key=api_key)
    if not raw:
        return None

    try:
        # Extract JSON from response
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


def summarize_group(messages, group_name, api_key):
    lines = []
    for m in messages[:100]:
        sender = (m.get("sender_display_name") or m.get("sender") or "?")
        content = m.get("content") or m.get("text") or m.get("summary") or ""
        if content:
            lines.append(f"{sender}: {content[:180]}")
    if not lines:
        return "", []

    raw = claude(
        f"微信群「{group_name}」聊天记录，请输出：\n"
        "摘要：（2-3句话）\n关键词：（最多5个，逗号分隔）\n\n"
        + "\n".join(lines),
        api_key=api_key
    )
    if not raw:
        return "", []

    summary, keywords = "", []
    for line in raw.strip().split("\n"):
        if line.startswith("摘要："):
            summary = line[3:].strip()
        elif line.startswith("关键词："):
            keywords = [k.strip() for k in line[4:].split(",") if k.strip()]
    return summary, keywords


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_time(ts):
    if not ts:
        return ""
    dt = datetime.fromtimestamp(int(ts))
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if dt.date() == (now - timedelta(days=1)).date():
        return f"昨天 {dt.strftime('%H:%M')}"
    return dt.strftime("%m/%d %H:%M")


def esc(s):
    return (str(s or "")
            .replace("&","&amp;").replace("<","&lt;")
            .replace(">","&gt;").replace('"',"&quot;"))


def trunc(s, n=60):
    s = str(s or "").replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


# ── HTML sections ─────────────────────────────────────────────────────────────

def section_new_contacts(new_contacts):
    if not new_contacts:
        return ""
    rows = ""
    for c in new_contacts[:20]:
        name = esc(c.get("nick_name") or c.get("alias") or c.get("username", "?"))
        remark = esc(c.get("remark") or "")
        rows += f"""
        <div class="person-row">
          <div class="avatar new">{name[:1]}</div>
          <div class="person-info">
            <div class="person-name">{name}
              {"<span class='remark'>"+remark+"</span>" if remark else ""}
            </div>
            <div class="person-msg">刚刚添加 · 还没打招呼</div>
          </div>
          <span class="tag tag-new">新</span>
        </div>"""
    return f"""
    <section>
      <div class="section-label">🆕 新添加的人 <span class="count-badge">{len(new_contacts)}</span></div>
      <div class="card">{rows}</div>
    </section>"""


def section_ai_briefing(analysis):
    if not analysis:
        return """
    <section>
      <div class="section-label">🤖 AI 客户日报</div>
      <div class="card">
        <div class="empty">需要填写 Anthropic Key 才能启用 AI 分析</div>
      </div>
    </section>"""

    # Summary
    summary_html = f'<div class="ai-summary">{esc(analysis.get("summary",""))}</div>' if analysis.get("summary") else ""

    # Must follow up
    must = analysis.get("must_followup", [])
    must_html = ""
    for item in must:
        urgency = item.get("urgency", "medium")
        badge = '<span class="tag tag-hot">🔥 今天</span>' if urgency == "high" else '<span class="tag tag-warm">⚡ 尽快</span>'
        must_html += f"""
        <div class="action-item">
          <div class="action-header">
            <span class="action-name">{esc(item.get('name',''))}</span>
            {badge}
          </div>
          <div class="action-reason">{esc(item.get('reason',''))}</div>
          <div class="action-do">→ {esc(item.get('action',''))}</div>
        </div>"""

    # Do not miss
    dont_miss = analysis.get("do_not_miss", [])
    dont_miss_html = "".join(
        f'<span class="kw kw-red">{esc(n)}</span>' for n in dont_miss
    )

    # Warming up
    warming = analysis.get("warming_up", [])
    warming_html = ""
    for item in warming:
        warming_html += f"""
        <div class="warming-row">
          <span class="warming-name">{esc(item.get('name',''))}</span>
          <span class="warming-arrow">{esc(item.get('from_stage',''))} → {esc(item.get('to_stage',''))}</span>
          <span class="warming-signal">{esc(item.get('signal',''))}</span>
        </div>"""

    # Todos
    def todo_list(items):
        if not items:
            return "<div class='empty'>暂无</div>"
        return "".join(f'<div class="todo-item">☐ {esc(t)}</div>' for t in items)

    today_todos = todo_list(analysis.get("todos_today", []))
    tmr_todos   = todo_list(analysis.get("todos_tomorrow", []))

    return f"""
    <section>
      <div class="section-label">🤖 AI 客户日报</div>

      {summary_html}

      {"<div class='subsection-label'>🔴 绝对不能漏</div><div class='kw-row'>" + dont_miss_html + "</div>" if dont_miss else ""}

      {"<div class='subsection-label'>必须今天跟进</div><div class='card'>" + must_html + "</div>" if must_html else ""}

      {"<div class='subsection-label'>客户升温信号</div><div class='card'>" + warming_html + "</div>" if warming_html else ""}

      <div class="todos-grid">
        <div>
          <div class="subsection-label">📋 今日待办</div>
          <div class="card todo-card">{today_todos}</div>
        </div>
        <div>
          <div class="subsection-label">🗓 明日准备</div>
          <div class="card todo-card">{tmr_todos}</div>
        </div>
      </div>
    </section>"""


def section_unanswered(unanswered):
    if not unanswered:
        return """
    <section>
      <div class="section-label">✅ 私聊全部已回复</div>
    </section>"""
    rows = ""
    for s in unanswered[:30]:
        name = esc(s.get("display_name") or s.get("username","?"))
        msg  = esc(trunc(s.get("summary","")))
        t    = fmt_time(s.get("last_timestamp"))
        cnt  = s.get("unread_count", 0)
        rows += f"""
        <div class="person-row">
          <div class="avatar">{name[:1]}</div>
          <div class="person-info">
            <div class="person-name">{name}
              <span class="unread-dot">{cnt}</span>
            </div>
            <div class="person-msg">{msg}</div>
          </div>
          <div class="person-time">{t}</div>
        </div>"""
    return f"""
    <section>
      <div class="section-label">🔴 私聊待回复 <span class="count-badge">{len(unanswered)}</span></div>
      <div class="card">{rows}</div>
    </section>"""


def section_groups(config, group_summaries, unread_groups):
    priority = config.get("priority_groups", [])
    if not priority:
        return ""

    unread_map = {s.get("display_name"): s.get("unread_count", 0) for s in unread_groups}
    cards = ""
    for g in priority:
        info = group_summaries.get(g, {})
        summary_text = esc(info.get("summary",""))
        keywords     = info.get("keywords", [])
        unread_count = unread_map.get(g, info.get("unread_count", 0))

        kw_html = "".join(f'<span class="kw">{esc(k)}</span>' for k in keywords)
        ai_html = (
            f'<div class="group-summary">{summary_text}</div>'
            f'<div class="kw-row">{kw_html}</div>'
            if summary_text else
            '<div class="empty">需要 wxkey bootstrap + Anthropic Key 启用 AI 摘要</div>'
        )
        cards += f"""
        <div class="card group-card">
          <div class="group-header">
            <span class="group-name">{esc(g)}</span>
            <span class="tag tag-warm">{unread_count} 未读</span>
          </div>
          {ai_html}
        </div>"""

    return f"""
    <section>
      <div class="section-label">📊 重点群摘要</div>
      {cards}
    </section>"""


def section_other_groups(unread_groups, priority_names):
    others = sorted(
        [g for g in unread_groups if g.get("display_name") not in priority_names],
        key=lambda x: x.get("unread_count", 0), reverse=True
    )
    if not others:
        return ""
    rows = ""
    for s in others[:50]:
        name   = esc(s.get("display_name","?"))
        cnt    = s.get("unread_count", 0)
        sender = esc(s.get("last_sender_display_name") or "")
        msg    = esc(trunc(s.get("summary",""), 50))
        t      = fmt_time(s.get("last_timestamp"))
        prefix = f"{sender}: " if sender else ""
        rows += f"""
        <div class="group-row">
          <div class="group-row-name">{name}</div>
          <div class="group-row-msg">{prefix}{msg}</div>
          <div class="group-row-meta">
            <span class="tag tag-cnt">{cnt}</span>
            <span class="group-row-time">{t}</span>
          </div>
        </div>"""
    return f"""
    <section>
      <div class="section-label">💬 其他未读群组</div>
      <div class="card">{rows}</div>
    </section>"""


# ── Full HTML ─────────────────────────────────────────────────────────────────

CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, "SF Pro Text", "PingFang SC", "Helvetica Neue", sans-serif;
  background: #f2f2f7; color: #1c1c1e; min-height: 100vh;
}

/* Header */
.header { background: #1c1c1e; color: #fff; padding: 28px 32px 24px; }
.header h1 { font-size: 22px; font-weight: 700; letter-spacing: -.3px; }
.header-meta { font-size: 13px; color: #8e8e93; margin-top: 5px; }
.header-stats { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.stat-chip {
  font-size: 12px; padding: 4px 12px; border-radius: 100px;
  background: rgba(255,255,255,.1); color: #ebebf5;
}
.stat-chip.red { background: rgba(255,59,48,.25); color: #ff6961; }
.stat-chip.green { background: rgba(52,199,89,.2); color: #4cd964; }

/* Layout */
.container { max-width: 880px; margin: 0 auto; padding: 24px 16px 56px; }
section { margin-bottom: 28px; }

.section-label {
  font-size: 11.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .7px; color: #8e8e93; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}
.subsection-label {
  font-size: 12px; font-weight: 600; color: #636366;
  margin: 16px 0 8px; letter-spacing: .3px;
}

.count-badge {
  background: #ff3b30; color: #fff; font-size: 11px;
  font-weight: 700; padding: 1px 7px; border-radius: 100px;
}

/* Cards */
.card {
  background: #fff; border-radius: 14px; overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
}
.group-card { padding: 16px 18px; margin-bottom: 10px; }

/* Tags */
.tag {
  font-size: 11px; font-weight: 600; padding: 2px 9px;
  border-radius: 100px; white-space: nowrap;
}
.tag-new   { background: #e3f9e5; color: #30a14e; }
.tag-hot   { background: #fff0ee; color: #ff3b30; }
.tag-warm  { background: #fff8ec; color: #ff9500; }
.tag-cnt   { background: #ff9500; color: #fff; }
.kw-red    { background: #fff0ee; color: #ff3b30; }

/* Person rows */
.person-row {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px; border-bottom: 1px solid #f2f2f7;
}
.person-row:last-child { border-bottom: none; }

.avatar {
  width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
  background: linear-gradient(135deg,#007aff,#5ac8fa);
  color: #fff; font-size: 16px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.avatar.new { background: linear-gradient(135deg,#34c759,#30d158); }

.person-info { flex: 1; min-width: 0; }
.person-name {
  font-size: 15px; font-weight: 600;
  display: flex; align-items: center; gap: 6px;
}
.remark { font-size: 11px; color: #8e8e93; font-weight: 400; }
.unread-dot {
  font-size: 11px; background: #ff3b30; color: #fff;
  padding: 1px 6px; border-radius: 100px; font-weight: 700;
}
.person-msg { font-size: 13px; color: #8e8e93; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.person-time { font-size: 12px; color: #aeaeb2; flex-shrink: 0; }

/* AI briefing */
.ai-summary {
  background: #f9f9fb; border-radius: 10px; padding: 14px 16px;
  font-size: 14px; line-height: 1.7; color: #3a3a3c;
  margin-bottom: 16px; border-left: 3px solid #007aff;
}

.action-item {
  padding: 12px 16px; border-bottom: 1px solid #f2f2f7;
}
.action-item:last-child { border-bottom: none; }
.action-header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
}
.action-name { font-size: 15px; font-weight: 600; }
.action-reason { font-size: 13px; color: #6c6c70; margin-bottom: 4px; }
.action-do { font-size: 13px; color: #007aff; }

.warming-row {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 16px; border-bottom: 1px solid #f2f2f7; font-size: 14px;
}
.warming-row:last-child { border-bottom: none; }
.warming-name { font-weight: 600; width: 90px; flex-shrink: 0; }
.warming-arrow { color: #34c759; font-size: 12px; width: 140px; flex-shrink: 0; }
.warming-signal { color: #636366; font-size: 13px; }

.todos-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
@media (max-width: 600px) { .todos-grid { grid-template-columns: 1fr; } }
.todo-card { padding: 4px 0; }
.todo-item { padding: 9px 16px; border-bottom: 1px solid #f2f2f7; font-size: 14px; }
.todo-item:last-child { border-bottom: none; }

/* Keywords */
.kw-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.kw {
  font-size: 12px; padding: 3px 10px; background: #f2f2f7;
  border-radius: 100px; color: #636366;
}

/* Group rows */
.group-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.group-name { font-size: 15px; font-weight: 600; }
.group-summary { font-size: 14px; color: #3a3a3c; line-height: 1.65; margin-bottom: 8px; }

.group-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; border-bottom: 1px solid #f2f2f7;
}
.group-row:last-child { border-bottom: none; }
.group-row-name { font-size: 14px; font-weight: 600; width: 150px; flex-shrink: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.group-row-msg { flex: 1; font-size: 13px; color: #8e8e93;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.group-row-meta { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.group-row-time { font-size: 12px; color: #aeaeb2; }

.empty { font-size: 13px; color: #aeaeb2; font-style: italic; padding: 14px 16px; }

.footer { text-align: center; font-size: 12px; color: #c7c7cc; padding: 32px 0 8px; }
.footer a { color: #c7c7cc; }
"""


def build_html(config, new_contacts, unanswered, unread_groups,
               group_summaries, analysis):
    now      = datetime.now()
    date_str = now.strftime("%Y年%m月%d日 %A")
    time_str = now.strftime("%H:%M")
    title    = config.get("report_title", "微信日报")

    stats = []
    if new_contacts:
        stats.append(f'<span class="stat-chip green">🆕 {len(new_contacts)} 个新联系人</span>')
    if unanswered:
        stats.append(f'<span class="stat-chip red">🔴 {len(unanswered)} 人等回复</span>')
    stats.append(f'<span class="stat-chip">💬 {len(unread_groups)} 个群有未读</span>')
    stats_html = "\n".join(stats)

    priority_names = set(config.get("priority_groups", []))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>

<div class="header">
  <h1>🏠 {esc(title)}</h1>
  <div class="header-meta">{esc(date_str)} &nbsp;·&nbsp; 生成于 {esc(time_str)}</div>
  <div class="header-stats">{stats_html}</div>
</div>

<div class="container">
  {section_new_contacts(new_contacts)}
  {section_ai_briefing(analysis)}
  {section_unanswered(unanswered)}
  {section_groups(config, group_summaries, unread_groups)}
  {section_other_groups(unread_groups, priority_names)}

  <div class="footer">
    由 <a href="https://github.com/your-username/wechat-daily-report">wechat-daily-report</a> 生成
  </div>
</div>

</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--days", type=int, default=1,
                        help="How many days back to analyze (default: 1)")
    args = parser.parse_args()

    # Config
    if not CONFIG_FILE.exists():
        example = ROOT / "config.example.json"
        if example.exists():
            import shutil; shutil.copy(example, CONFIG_FILE)
        print(f"⚠️  请先编辑 config.json，填入你的微信 ID 和重点群名称\n   {CONFIG_FILE}")
        sys.exit(1)

    config   = json.loads(CONFIG_FILE.read_text())
    self_wxid = config.get("self_wxid", "")
    api_key   = config.get("anthropic_api_key", "")
    days_back = args.days

    if not self_wxid:
        print("⚠️  config.json 中 self_wxid 未填写"); sys.exit(1)

    state = load_state()

    print(f"\n📱 微信日报生成中... (最近 {days_back} 天)")

    # 1. New contacts
    print("   查询新添加联系人...")
    new_contacts, max_contact_id = get_new_contacts(state)

    # 2. Private sessions
    print("   拉取私聊会话...")
    all_private = get_private_sessions()
    unanswered  = get_unread_private(all_private)

    # 3. Unread groups
    print("   拉取未读群组...")
    unread_groups = get_unread_groups()

    # 4. AI client analysis
    analysis = None
    if api_key:
        print(f"   AI 分析 {len(all_private)} 个私聊客户...")
        # Only analyze chats with recent activity
        active = [s for s in all_private
                  if s.get("last_timestamp", 0) > (datetime.now() - timedelta(days=days_back+1)).timestamp()]
        analysis = analyze_clients(active, api_key, days_back)
        if analysis:
            print("   ✓ AI 分析完成")
        else:
            print("   ⚠ AI 分析失败（可能是 wxkey 未设置或 API Key 问题）")

    # 5. Priority group summaries
    group_summaries = {}
    for g in config.get("priority_groups", []):
        print(f"   重点群：{g}...")
        unread_map = {s.get("display_name"): s.get("unread_count",0) for s in unread_groups}
        group_summaries[g] = {"unread_count": unread_map.get(g, 0), "summary": "", "keywords": []}
        if api_key:
            msgs = get_group_messages(g, days_back)
            if msgs:
                s, k = summarize_group(msgs, g, api_key)
                group_summaries[g].update({"summary": s, "keywords": k})

    # 6. Save state
    state["last_max_contact_id"] = max_contact_id
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    # 7. Build & write HTML
    html = build_html(config, new_contacts, unanswered,
                      unread_groups, group_summaries, analysis)
    OUTPUT_FILE.write_text(html, encoding="utf-8")

    print(f"\n✅ 报告已生成：{OUTPUT_FILE}")
    print(f"   新联系人：{len(new_contacts)} 人")
    print(f"   私聊待回复：{len(unanswered)} 人")
    print(f"   未读群组：{len(unread_groups)} 个")
    print(f"   AI 分析：{'已完成' if analysis else '未启用（填写 API Key）'}")

    if not args.no_browser:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
