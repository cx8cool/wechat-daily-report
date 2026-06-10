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


# ── HTML builder ─────────────────────────────────────────────────────────────

def build_html(config, new_contacts, unanswered, all_private, analysis):
    now      = datetime.now()
    title    = config.get("report_title", "微信私信日报")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    weekday  = ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()]

    # ── Recent private chats (timeline, sorted by last activity) ─────────
    recent_private = sorted(
        [s for s in all_private if s.get("last_timestamp")],
        key=lambda x: x["last_timestamp"], reverse=True
    )[:18]

    tl_rows = ""
    unanswered_ids = {s.get("username") for s in unanswered}
    for s in recent_private:
        name    = esc(s.get("display_name") or s.get("username", "?"))
        msg     = esc(trunc(s.get("summary", ""), 42))
        t       = fmt_time(s.get("last_timestamp"))
        cnt     = s.get("unread_count", 0)
        waiting = s.get("username") in unanswered_ids
        dot     = "dot-red" if waiting else "dot-gray"
        cnt_html = f'<span class="badge">{cnt}</span>' if cnt else ""
        tl_rows += f"""<div class="tl-row">
          <div class="tl-meta">
            <span class="tl-time">{t}</span>
            <span class="{dot}"></span>
          </div>
          <div class="tl-body">
            <div class="tl-name">{name}{cnt_html}</div>
            <div class="tl-msg">{msg}</div>
          </div>
        </div>"""

    # ── Right col: new contacts ───────────────────────────────────────────
    nc_html = ""
    for c in new_contacts[:6]:
        name  = esc(c.get("nick_name") or c.get("alias") or c.get("username", "?"))
        alias = esc(c.get("alias") or "")
        nc_html += f"""<div class="r-row">
          <div class="av av-g">{name[:1]}</div>
          <div class="r-body">
            <div class="r-name">{name}</div>
            {"<div class='r-sub'>@"+alias+"</div>" if alias else ""}
          </div>
          <span class="tag tag-g">新</span>
        </div>"""
    if not nc_html:
        nc_html = '<div class="r-empty">今日暂无新联系人</div>'

    # ── Right col: unanswered ─────────────────────────────────────────────
    ua_html = ""
    for s in unanswered[:6]:
        name = esc(s.get("display_name") or "?")
        msg  = esc(trunc(s.get("summary", ""), 34))
        t    = fmt_time(s.get("last_timestamp"))
        cnt  = s.get("unread_count", 0)
        ua_html += f"""<div class="r-row">
          <div class="av av-r">{name[:1]}</div>
          <div class="r-body">
            <div class="r-name">{name} <span class="badge">{cnt}</span></div>
            <div class="r-sub">{msg}</div>
          </div>
          <span class="r-time">{t}</span>
        </div>"""
    if not ua_html:
        ua_html = '<div class="r-empty">✓ 全部已回复</div>'

    # ── Right col: AI important ───────────────────────────────────────────
    ai_rows = ""
    if analysis:
        items = []
        for x in analysis.get("must_followup", [])[:3]:
            items.append(("red", x.get("name",""), x.get("reason","") or x.get("action","")))
        for x in analysis.get("warming_up", [])[:3]:
            items.append(("blue", x.get("name",""), x.get("signal","")))
        for i, (color, name, desc) in enumerate(items):
            c = {"red":"#F03D3D","blue":"#1B6EF3"}.get(color,"#888")
            bg = {"red":"#FFF0F0","blue":"#EEF3FF"}.get(color,"#F5F5F7")
            ai_rows += f"""<div class="ai-row">
              <div class="ai-num" style="color:{c};background:{bg}">{i+1}</div>
              <div class="r-body">
                <div class="r-name">{esc(name)}</div>
                <div class="r-sub">{esc(trunc(desc,38))}</div>
              </div>
            </div>"""
        if analysis.get("summary"):
            ai_summary = f'<div class="ai-bar">{esc(analysis["summary"])}</div>'
        else:
            ai_summary = ""
    else:
        ai_rows = '<div class="r-empty">填写 Anthropic Key 启用 AI 分析</div>'
        ai_summary = ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"SF Pro Text","PingFang SC","Helvetica Neue",sans-serif;
  background:#FAFAFA;color:#111;font-size:14px;min-height:100vh}}

/* ── Header ── */
.hdr{{background:#fff;border-bottom:1px solid #EBEBEB;padding:20px 28px 16px}}
.hdr-brand{{font-size:11px;font-weight:700;color:#999;letter-spacing:.8px;text-transform:uppercase}}
.hdr-date{{font-size:24px;font-weight:800;color:#111;line-height:1.1;margin:4px 0}}
.hdr-stats{{display:flex;gap:20px;margin-top:10px}}
.hs{{display:flex;align-items:baseline;gap:4px}}
.hs-n{{font-size:17px;font-weight:800;color:#111}}
.hs-n.red{{color:#E02020}}
.hs-n.green{{color:#1CB47A}}
.hs-l{{font-size:11px;color:#999}}
.hdr-time{{font-size:11px;color:#bbb;margin-top:8px}}

/* ── Layout ── */
.page{{display:grid;grid-template-columns:1fr 340px;min-height:calc(100vh - 110px)}}
@media(max-width:680px){{.page{{grid-template-columns:1fr}}}}

/* ── Left: timeline ── */
.tl{{padding:20px 24px;border-right:1px solid #EBEBEB;background:#fff}}
.col-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
  color:#bbb;margin-bottom:16px}}

.tl-row{{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #F5F5F5}}
.tl-row:last-child{{border-bottom:none}}
.tl-meta{{display:flex;flex-direction:column;align-items:center;gap:5px;
  width:46px;flex-shrink:0;padding-top:2px}}
.tl-time{{font-size:10px;color:#bbb;white-space:nowrap}}
.dot-red{{width:7px;height:7px;border-radius:50%;background:#E02020;flex-shrink:0}}
.dot-gray{{width:7px;height:7px;border-radius:50%;background:#D1D1D6;flex-shrink:0}}
.tl-body{{flex:1;min-width:0}}
.tl-name{{font-size:13px;font-weight:600;display:flex;align-items:center;gap:5px}}
.tl-msg{{font-size:12px;color:#999;margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.badge{{background:#E02020;color:#fff;font-size:10px;font-weight:700;
  padding:1px 5px;border-radius:100px}}

/* ── Right panel ── */
.panel{{background:#FAFAFA;padding:20px 20px}}
.panel-section{{margin-bottom:20px}}
.panel-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
  color:#bbb;margin-bottom:10px}}
.panel-card{{background:#fff;border:1px solid #EBEBEB;border-radius:10px;overflow:hidden}}

.r-row{{display:flex;align-items:center;gap:10px;padding:9px 12px;
  border-bottom:1px solid #F5F5F5}}
.r-row:last-child{{border-bottom:none}}
.av{{width:30px;height:30px;border-radius:50%;flex-shrink:0;font-size:12px;font-weight:700;
  display:flex;align-items:center;justify-content:center;color:#fff}}
.av-g{{background:linear-gradient(135deg,#1CB47A,#30D158)}}
.av-r{{background:linear-gradient(135deg,#E02020,#FF453A)}}
.r-body{{flex:1;min-width:0}}
.r-name{{font-size:13px;font-weight:600;display:flex;align-items:center;gap:5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.r-sub{{font-size:11px;color:#999;margin-top:1px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.r-time{{font-size:11px;color:#bbb;flex-shrink:0}}
.r-empty{{padding:12px;font-size:12px;color:#bbb;font-style:italic}}

.tag{{font-size:10px;font-weight:700;padding:2px 7px;border-radius:100px;flex-shrink:0}}
.tag-g{{background:#EDFBF4;color:#1CB47A}}

.ai-bar{{background:#EEF3FF;border-left:3px solid #1B6EF3;border-radius:6px;
  padding:9px 12px;font-size:12px;line-height:1.6;color:#1A1A2E;margin-bottom:10px}}
.ai-row{{display:flex;align-items:flex-start;gap:8px;padding:8px 12px;
  border-bottom:1px solid #F5F5F5}}
.ai-row:last-child{{border-bottom:none}}
.ai-num{{width:20px;height:20px;border-radius:5px;font-size:11px;font-weight:800;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}}

/* ── Footer ── */
.footer{{text-align:center;font-size:11px;color:#ccc;padding:12px;
  border-top:1px solid #EBEBEB;background:#fff}}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-brand">{esc(title)}</div>
  <div class="hdr-date">{date_str} &nbsp;<span style="font-size:16px;color:#999;font-weight:500">{weekday}</span></div>
  <div class="hdr-stats">
    <div class="hs"><span class="hs-n {'red' if unanswered else ''}">{len(unanswered)}</span><span class="hs-l">待回复</span></div>
    <div class="hs"><span class="hs-n {'green' if new_contacts else ''}">{len(new_contacts)}</span><span class="hs-l">新联系人</span></div>
    <div class="hs"><span class="hs-n">{len(all_private)}</span><span class="hs-l">个私聊</span></div>
  </div>
  <div class="hdr-time">生成于 {time_str}</div>
</div>

<div class="page">

  <!-- Left: recent private chat timeline -->
  <div class="tl">
    <div class="col-label">最近私聊动态</div>
    {tl_rows or '<div style="color:#bbb;font-size:13px;padding:20px 0">暂无私聊数据</div>'}
  </div>

  <!-- Right panel -->
  <div class="panel">

    <div class="panel-section">
      <div class="panel-label">新添加的人</div>
      <div class="panel-card">{nc_html}</div>
    </div>

    <div class="panel-section">
      <div class="panel-label">还没回复</div>
      <div class="panel-card">{ua_html}</div>
    </div>

    <div class="panel-section">
      <div class="panel-label">AI 重要消息</div>
      {ai_summary}
      <div class="panel-card">{ai_rows}</div>
    </div>

  </div>
</div>

<div class="footer">wechat-daily-report · {date_str} {time_str}</div>
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
                      all_private, analysis)
    OUTPUT_FILE.write_text(html, encoding="utf-8")

    print(f"\n✅ 报告已生成：{OUTPUT_FILE}")
    print(f"   新联系人：{len(new_contacts)} 人")
    print(f"   私聊待回复：{len(unanswered)} 人")
    print(f"   AI 分析：{'已完成' if analysis else '未启用（填写 API Key）'}")

    if not args.no_browser:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
