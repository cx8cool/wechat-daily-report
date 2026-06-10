# WeChat Daily Report

An AI-powered local HTML briefing tool for teams with high-volume WeChat usage. Reads your local WeChat data, analyzes client conversations with Claude AI, and generates a daily action-oriented report.

**Built for:** Sales teams, real estate agents, consultants — anyone drowning in WeChat messages and needing to know *what to act on today*.

---

## What It Does

Every morning, run one command and get a local HTML report with:

- **🆕 New contacts** — people who added you since your last report, flagged for follow-up
- **🤖 AI client briefing** — which clients can't be missed, who's warming up, today's priorities, tomorrow's prep
- **🔴 Unanswered private chats** — private conversations waiting for your reply
- **📊 Priority group summaries** — AI-distilled digest of your key groups
- **💬 All unread groups** — full unread overview sorted by volume

All data stays on your machine. Nothing is sent to external servers except the AI summary request to Anthropic (optional).

---

## Requirements

| Requirement | Notes |
|---|---|
| macOS | Windows/Linux have limited support |
| WeChat for Mac | Version ≤ 4.1.9 tested |
| Python 3.9+ | `python3 --version` to check |
| [wechat-cli](https://www.npmjs.com/package/@canghe_ai/wechat-cli) | Local WeChat data reader |
| Anthropic API Key | Optional — for AI summaries |

---

## Setup

### 1. Install wechat-cli

```bash
npm install -g @canghe_ai/wechat-cli
```

Open WeChat on your Mac, then run:

```bash
sudo wechat-cli init
```

Verify it works:

```bash
wechat-cli sessions --limit 3
```

### 2. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Configure

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "self_wxid": "your_wechat_id",
  "priority_groups": ["销售群", "客户群"],
  "days_back": 1,
  "anthropic_api_key": "sk-ant-...",
  "report_title": "微信日报"
}
```

**Finding your WeChat ID:** Run this command and look at the folder names:

```bash
ls ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
```

The folder modified most recently contains your active account ID.

### 4. (Optional) Enable full message history

For AI analysis of full conversation content (not just last message), run:

```bash
wxkey bootstrap
```

This grants wechat-cli permission to decrypt your local message database. Required for the AI client analysis feature.

---

## Usage

```bash
# Generate report and open in browser
python3 daily_report.py

# Generate only (no browser)
python3 daily_report.py --no-browser

# Look back 2 days instead of 1
python3 daily_report.py --days 2
```

The report is saved to `report.html` in the project folder.

### Automate with cron (macOS)

To run every morning at 8am automatically:

```bash
crontab -e
```

Add:

```
0 8 * * * cd /path/to/wechat-daily-report && python3 daily_report.py
```

---

## How It Works

WeChat stores your chat data in a local SQLite database on your Mac:

```
~/Library/Containers/com.tencent.xinWeChat/Data/
  Documents/xwechat_files/<your-wxid>/db_storage/
    contact/contact.db      ← contacts (readable directly)
    session/session.db      ← session list (readable directly)
    message/message_0.db    ← message content (encrypted, needs wxkey)
```

`wechat-cli` reads this local database — no WeChat servers involved, no network requests for your data.

The AI analysis (Claude API) only receives message text — never your WeChat ID, contacts list, or any metadata.

---

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `self_wxid` | — | Your WeChat ID (required) |
| `priority_groups` | `[]` | Group names for AI-summarized digest |
| `days_back` | `1` | How many days back to analyze |
| `anthropic_api_key` | `""` | Anthropic API key for AI features |
| `report_title` | `"微信日报"` | Title shown in the report header |

---

## Roadmap

- [ ] Scheduled auto-run via macOS launchd (no cron needed)
- [ ] Keyword alerts (flag messages containing specific terms)
- [ ] Client stage tracking across runs (new → warm → hot → deal)
- [ ] Windows support
- [ ] Web UI with live refresh

---

## Privacy

- All WeChat data is read locally from your Mac
- No data is stored on any server
- If `anthropic_api_key` is empty, no data leaves your machine at all
- `config.json`, `report.html`, and `.state.json` are gitignored by default

---

## License

MIT
