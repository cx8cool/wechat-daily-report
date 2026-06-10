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
    """Contacts added since last run (today's new), tracked by max contact id."""
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

    new = [r for r in rows
           if r["id"] > last_id
           and not any(str(r.get("username","")).startswith(p) for p in OFFICIAL_PREFIXES)]

    return new, max_id


def get_recent_contacts(limit=20):
    """Most recently added contacts by id (regardless of last-run state)."""
    data = run_sql(
        f"SELECT id, username, nick_name, alias, remark FROM contact "
        f"WHERE local_type=1 AND delete_flag=0 ORDER BY id DESC LIMIT {limit}",
        "contact", "contact.db"
    )
    if not data:
        return []
    return [r for r in data.get("rows", [])
            if not any(str(r.get("username","")).startswith(p) for p in OFFICIAL_PREFIXES)]


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


def get_memo_messages(days_back=30):
    """Fetch messages from 文件传输助手 (filehelper), used as personal memo."""
    # filehelper is the real username for 文件传输助手 / 美女的备忘录
    for name in ("filehelper", "美女的备忘录"):
        data = run_cli("history", name, "--limit", "60")
        if data and data.get("messages"):
            msgs = data["messages"]
            cutoff = (datetime.now() - timedelta(days=days_back)).timestamp()
            return [m for m in msgs if (m.get("timestamp") or 0) >= cutoff or not m.get("timestamp")]
    return []


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


def summarize_memo(messages, api_key):
    """Extract structured reminders from personal notes chat."""
    if not messages or not api_key:
        return None
    lines = []
    for m in messages:
        content = m.get("content") or m.get("text") or m.get("summary") or ""
        if content and content.strip():
            lines.append(content.strip()[:300])
    if not lines:
        return None

    raw = claude(
        f"""以下是我的微信备忘录（我给自己发的消息，记录日程、提醒、重要事项）：

{chr(10).join(f'- {l}' for l in lines)}

请提取并输出以下JSON（严格JSON，不要其他文字）：
{{
  "dates": [{{"text": "描述", "date": "日期或时间"}}],
  "people": [{{"name": "人名", "note": "关于此人的备忘"}}],
  "todos": ["待办事项1", "待办事项2"],
  "notes": ["其他重要备忘1", "其他重要备忘2"]
}}

dates：含具体日期/时间的事项；people：提到具体人名的备忘；todos：需要做的事；notes：其他重要信息。
如某类为空则返回空数组。""",
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        api_key=api_key
    )
    if not raw:
        return None
    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


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


# ── HTML builder ─────────────────────────────────────────────────────────────

def build_html(config, new_contacts, unanswered, all_private, analysis, recent_contacts=None):
    now      = datetime.now()
    title    = config.get("report_title", "微信私信日报")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    weekday  = ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()]

    # ── Unanswered section ────────────────────────────────────────────────
    ua_rows = ""
    for s in unanswered:
        name = esc(s.get("display_name") or "?")
        msg  = esc(trunc(s.get("summary", ""), 40))
        t    = fmt_time(s.get("last_timestamp"))
        cnt  = s.get("unread_count", 0)
        ua_rows += f"""<div class="ua-row">
          <div class="av av-r">{name[:1]}</div>
          <div class="ua-body">
            <div class="ua-name">{name}<span class="badge">{cnt}</span></div>
            <div class="ua-msg">{msg or "等待你回复"}</div>
          </div>
          <span class="ua-time">{t}</span>
        </div>"""

    # ── New contacts section ──────────────────────────────────────────────
    nc_rows = ""
    for c in new_contacts:
        name   = esc(c.get("nick_name") or c.get("alias") or c.get("username","?"))
        remark = esc(c.get("remark") or c.get("alias") or "")
        nc_rows += f"""<div class="nc-row">
          <div class="av av-g">{name[:1]}</div>
          <div class="ua-body">
            <div class="ua-name">{name}</div>
            {"<div class='ua-msg'>"+remark+"</div>" if remark else ""}
          </div>
          <span class="tag-new">新</span>
        </div>"""

    # ── Card: AI 核心判断 ─────────────────────────────────────────────────
    ai_items = ""
    ai_footer = "跟进建议"
    if analysis:
        has_urgent = False
        for x in analysis.get("must_followup", [])[:4]:
            urg = x.get("urgency","")
            dc = "#E8393A" if urg == "high" else "#F59F00"
            bc = "#FFF1F1" if urg == "high" else "#FFFBEC"
            if urg == "high": has_urgent = True
            ai_items += f"""<div class="ci">
              <div class="ci-dot" style="background:{dc}"></div>
              <div class="ci-body">
                <div class="ci-name">{esc(x.get('name',''))}</div>
                <div class="ci-desc" style="background:{bc}">{esc(trunc(x.get('reason','') or x.get('action',''), 46))}</div>
              </div>
            </div>"""
        for x in analysis.get("warming_up", [])[:3]:
            ai_items += f"""<div class="ci">
              <div class="ci-dot" style="background:#1B6EF3"></div>
              <div class="ci-body">
                <div class="ci-name">{esc(x.get('name',''))}</div>
                <div class="ci-desc" style="background:#EEF3FF">{esc(trunc(x.get('signal',''), 46))}</div>
              </div>
            </div>"""
        if has_urgent: ai_footer = "风险"
        if not ai_items and analysis.get("summary"):
            ai_items = f'<div class="ci-summary">{esc(analysis["summary"])}</div>'
    if not ai_items:
        ai_items = '<div class="c-empty">配置 Anthropic Key 后启用 AI 分析</div>'

    # ── Card: 时间故事线 ──────────────────────────────────────────────────
    recent_private = sorted(
        [s for s in all_private if s.get("last_timestamp")],
        key=lambda x: x["last_timestamp"], reverse=True
    )[:15]
    unanswered_ids = {s.get("username") for s in unanswered}

    tl_items = ""
    for s in recent_private:
        name    = esc(s.get("display_name") or s.get("username", "?"))
        msg     = esc(trunc(s.get("summary", ""), 34))
        t       = fmt_time(s.get("last_timestamp"))
        waiting = s.get("username") in unanswered_ids
        cnt     = s.get("unread_count", 0)
        dot_cls = "tl-dot-red" if waiting else "tl-dot-gray"
        badge   = f'<span class="badge">{cnt}</span>' if cnt else ""
        tl_items += f"""<div class="tl-item">
          <div class="tl-left">
            <span class="tl-t">{t}</span>
            <span class="{dot_cls}"></span>
            <span class="tl-line"></span>
          </div>
          <div class="tl-body">
            <div class="tl-name">{name}{badge}</div>
            <div class="tl-msg">{msg}</div>
          </div>
        </div>"""
    if not tl_items:
        tl_items = '<div class="c-empty">暂无私聊数据</div>'

    # ── Card: 最近新联系人 ────────────────────────────────────────────────
    rc_items = ""
    for c in (recent_contacts or []):
        name   = esc(c.get("nick_name") or c.get("alias") or c.get("username","?"))
        remark = esc(c.get("remark") or "")
        alias  = esc(c.get("alias") or "")
        sub    = remark or alias
        # mark today's new contacts with a badge
        is_new = c in new_contacts
        badge  = '<span class="tag-new">今日新增</span>' if is_new else ""
        rc_items += f"""<div class="nc-item">
          <div class="av av-g">{name[:1]}</div>
          <div class="ua-body">
            <div class="ua-name">{name}{badge}</div>
            {"<div class='ua-msg'>"+sub+"</div>" if sub else ""}
          </div>
        </div>"""
    if not rc_items:
        rc_items = '<div class="c-empty">暂无联系人数据</div>'

    # ── Render ────────────────────────────────────────────────────────────
    ua_count   = len(unanswered)
    nc_count   = len(new_contacts)
    ua_section = f"""
    <div class="alert-card">
      <div class="alert-hdr">
        <div class="alert-left">
          <span class="alert-num">{ua_count}</span>
          <span class="alert-label">条未回复</span>
        </div>
        <div class="card-icon icon-red">🔔</div>
      </div>
      <div class="alert-body">{"".join(f'<div class="ua-row"><div class="av av-r">{esc((s.get("display_name") or "?")[:1])}</div><div class="ua-body"><div class="ua-name">{esc(s.get("display_name") or "?")}<span class="badge">{s.get("unread_count",0)}</span></div><div class="ua-msg">{esc(trunc(s.get("summary",""),38))}</div></div><span class="ua-time">{fmt_time(s.get("last_timestamp"))}</span></div>' for s in unanswered)}</div>
      <div class="card-ftr"><div class="ftr-dot" style="background:#E8393A"></div><span class="ftr-label" style="color:#E8393A">待回复</span></div>
    </div>""" if ua_count else ""

    nc_section = f"""
    <div class="nc-card">
      <div class="card-hdr">
        <span class="card-title">昨日新添加</span>
        <div class="card-icon icon-green">👋</div>
      </div>
      <div class="card-body">{nc_rows}</div>
      <div class="card-ftr"><div class="ftr-dot" style="background:#1CB47A"></div><span class="ftr-label" style="color:#1CB47A">新联系人</span></div>
    </div>""" if nc_count else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"SF Pro Text","PingFang SC","Helvetica Neue",sans-serif;
  background:#F2F2F7;color:#111;font-size:14px}}

/* ── Header ── */
.hdr{{background:#fff;padding:24px 28px 20px;border-bottom:1px solid #E5E5EA}}
.hdr-top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}}
.hdr-date{{font-size:22px;font-weight:800;color:#111}}
.hdr-day{{font-size:15px;color:#999;margin-left:10px;font-weight:400}}
.hdr-time{{font-size:12px;color:#C0C0C6}}
.hdr-stats{{display:flex;gap:32px}}
.hs{{display:flex;flex-direction:column;gap:2px}}
.hs-n{{font-size:42px;font-weight:900;line-height:1;letter-spacing:-1px}}
.hs-n.clr-red{{color:#E8393A}}
.hs-n.clr-green{{color:#1CB47A}}
.hs-n.clr-gray{{color:#3C3C43}}
.hs-l{{font-size:12px;color:#8E8E93;font-weight:500}}
.hs-divider{{width:1px;background:#E5E5EA;align-self:stretch;margin:4px 0}}

/* ── Page wrapper ── */
.page{{max-width:980px;margin:0 auto;padding:12px}}

/* ── Alert card (待回复) ── */
.alert-card{{background:#fff;border-radius:16px;overflow:hidden;margin-bottom:12px;
  border:1.5px solid #FFDCDC;
  box-shadow:0 2px 8px rgba(232,57,58,.10)}}
.alert-hdr{{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 8px}}
.alert-left{{display:flex;align-items:baseline;gap:8px}}
.alert-num{{font-size:36px;font-weight:900;color:#E8393A;line-height:1}}
.alert-label{{font-size:15px;font-weight:700;color:#E8393A}}
.alert-body{{padding:0 16px 4px}}

/* ── New contacts card ── */
.nc-card{{background:#fff;border-radius:16px;overflow:hidden;margin-bottom:12px;
  border:1.5px solid #D4F5E9;
  box-shadow:0 2px 8px rgba(28,180,122,.08)}}

/* ── 2×2 grid ── */
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:620px){{.grid{{grid-template-columns:1fr}}}}

/* ── Card ── */
.card{{background:#fff;border-radius:16px;overflow:hidden;
  box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 12px rgba(0,0,0,.04)}}
.card-hdr{{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 8px}}
.card-title{{font-size:15px;font-weight:700;color:#111}}
.card-icon{{width:34px;height:34px;border-radius:10px;display:flex;
  align-items:center;justify-content:center;font-size:17px}}
.icon-green{{background:#E8FAF2}} .icon-blue{{background:#E8F0FF}}
.icon-orange{{background:#FFF4E8}} .icon-purple{{background:#F3EEFF}}
.icon-red{{background:#FFECEC}}
.card-body{{padding:0 16px 4px;min-height:70px}}
.card-ftr{{display:flex;align-items:center;gap:6px;
  padding:10px 16px;border-top:1px solid #F2F2F7;margin-top:6px}}
.ftr-dot{{width:6px;height:6px;border-radius:50%}}
.ftr-label{{font-size:12px;font-weight:600}}
.c-empty{{font-size:12px;color:#bbb;padding:14px 0;font-style:italic}}

/* ── Rows shared ── */
.ua-row,.nc-row{{display:flex;align-items:center;gap:10px;padding:9px 0;
  border-bottom:1px solid #F5F5F5}}
.ua-row:last-child,.nc-row:last-child{{border-bottom:none}}
.av{{width:32px;height:32px;border-radius:50%;flex-shrink:0;font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center;color:#fff}}
.av-r{{background:linear-gradient(135deg,#E8393A,#FF6B6B)}}
.av-g{{background:linear-gradient(135deg,#1CB47A,#34D399)}}
.ua-body{{flex:1;min-width:0}}
.ua-name{{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px}}
.ua-msg{{font-size:11px;color:#999;margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ua-time{{font-size:11px;color:#C0C0C6;flex-shrink:0}}
.tag-new{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:100px;
  background:#EDFBF4;color:#1CB47A;flex-shrink:0}}

/* ── AI items ── */
.ci{{display:flex;gap:10px;align-items:flex-start;padding:8px 0;
  border-bottom:1px solid #F5F5F5}}
.ci:last-child{{border-bottom:none}}
.ci-dot{{width:8px;height:8px;border-radius:50%;margin-top:4px;flex-shrink:0}}
.ci-body{{flex:1;min-width:0}}
.ci-name{{font-size:13px;font-weight:600}}
.ci-desc{{font-size:11px;color:#555;margin-top:3px;padding:3px 8px;border-radius:5px;
  display:inline-block;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ci-summary{{font-size:12px;color:#444;line-height:1.65;padding:8px 0}}

/* ── Timeline ── */
.tl-item{{display:flex;gap:10px;padding:7px 0}}
.tl-left{{display:flex;flex-direction:column;align-items:center;
  gap:3px;width:44px;flex-shrink:0}}
.tl-t{{font-size:10px;color:#C0C0C6;white-space:nowrap}}
.tl-dot-red{{width:7px;height:7px;border-radius:50%;background:#E8393A;flex-shrink:0}}
.tl-dot-gray{{width:7px;height:7px;border-radius:50%;background:#C7C7CC;flex-shrink:0}}
.tl-line{{width:1px;background:#E5E5EA;flex:1;min-height:6px}}
.tl-body{{flex:1;min-width:0}}
.tl-name{{font-size:13px;font-weight:600;display:flex;align-items:center;gap:5px}}
.tl-msg{{font-size:11px;color:#999;margin-top:1px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}

/* ── Recent contacts grid ── */
.nc-item{{display:flex;align-items:center;gap:10px;padding:8px 0;
  border-bottom:1px solid #F5F5F5}}
.nc-item:last-child{{border-bottom:none}}

/* ── Badge ── */
.badge{{background:#E8393A;color:#fff;font-size:10px;font-weight:700;
  padding:1px 5px;border-radius:100px;margin-left:2px}}
.footer{{text-align:center;font-size:11px;color:#bbb;padding:14px}}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr">
  <div class="hdr-top">
    <div><span class="hdr-date">{date_str}</span><span class="hdr-day">{weekday}</span></div>
    <div class="hdr-time">生成于 {time_str}</div>
  </div>
  <div class="hdr-stats">
    <div class="hs">
      <span class="hs-n {'clr-red' if ua_count else 'clr-gray'}">{ua_count}</span>
      <span class="hs-l">待回复</span>
    </div>
    <div class="hs-divider"></div>
    <div class="hs">
      <span class="hs-n {'clr-green' if nc_count else 'clr-gray'}">{nc_count}</span>
      <span class="hs-l">新联系人</span>
    </div>
    <div class="hs-divider"></div>
    <div class="hs">
      <span class="hs-n clr-gray">{len(all_private)}</span>
      <span class="hs-l">个私聊</span>
    </div>
  </div>
</div>

<div class="page">

  {ua_section}
  {nc_section}

  <!-- 2×2 grid -->
  <div class="grid">

    <div class="card">
      <div class="card-hdr">
        <span class="card-title">AI 核心判断</span>
        <div class="card-icon icon-green">📊</div>
      </div>
      <div class="card-body">{ai_items}</div>
      <div class="card-ftr">
        <div class="ftr-dot" style="background:{'#E8393A' if ai_footer=='风险' else '#1CB47A'}"></div>
        <span class="ftr-label" style="color:{'#E8393A' if ai_footer=='风险' else '#1CB47A'}">{ai_footer}</span>
      </div>
    </div>

    <div class="card">
      <div class="card-hdr">
        <span class="card-title">时间故事线</span>
        <div class="card-icon icon-blue">🕐</div>
      </div>
      <div class="card-body">{tl_items}</div>
      <div class="card-ftr">
        <div class="ftr-dot" style="background:#1B6EF3"></div>
        <span class="ftr-label" style="color:#1B6EF3">动态</span>
      </div>
    </div>

    <div class="card" style="grid-column:1/-1">
      <div class="card-hdr">
        <span class="card-title">最近新联系人</span>
        <div class="card-icon icon-green">👥</div>
      </div>
      <div class="card-body" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:0 24px">{rc_items}</div>
      <div class="card-ftr">
        <div class="ftr-dot" style="background:#1CB47A"></div>
        <span class="ftr-label" style="color:#1CB47A">联系人</span>
      </div>
    </div>

  </div>
</div>

<div class="footer">微信日报 · {date_str} {time_str}</div>
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
    recent_contacts = get_recent_contacts(20)

    # 2. Private sessions
    print("   拉取私聊会话...")
    all_private = get_private_sessions()
    unanswered  = get_unread_private(all_private)

    # 3. AI client analysis
    analysis = None
    if api_key:
        print(f"   AI 分析 {len(all_private)} 个私聊客户...")
        active = [s for s in all_private
                  if s.get("last_timestamp", 0) > (datetime.now() - timedelta(days=days_back+1)).timestamp()]
        analysis = analyze_clients(active, api_key, days_back)
        if analysis:
            print("   ✓ AI 分析完成")
        else:
            print("   ⚠ AI 分析失败（可能是 wxkey 未设置或 API Key 问题）")

    # 4. Save state
    state["last_max_contact_id"] = max_contact_id
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    # 7. Build & write HTML
    html = build_html(config, new_contacts, unanswered,
                      all_private, analysis, recent_contacts)
    OUTPUT_FILE.write_text(html, encoding="utf-8")

    print(f"\n✅ 报告已生成：{OUTPUT_FILE}")
    print(f"   新联系人：{len(new_contacts)} 人")
    print(f"   私聊待回复：{len(unanswered)} 人")
    print(f"   AI 分析：{'已完成' if analysis else '未启用（填写 API Key）'}")

    if not args.no_browser:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
