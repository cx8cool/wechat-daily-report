# 微信日报

一个本地 AI 日报工具。每天自动读取你的微信私聊数据，用 Claude AI 分析哪些客户最重要，生成一份可在浏览器查看的本地 HTML 日报。

**适合人群：** 房产经纪、销售、顾问——任何每天要在大量微信消息里判断轻重缓急的人。

**数据完全本地。** 微信数据只在你的电脑上读取，不上传到任何服务器。

---

## 日报包含什么

| 模块 | 内容 |
|---|---|
| **待回复警示** | 有未读私聊时红色置顶，显示是谁在等你回复 |
| **AI 核心判断** | 哪些客户必须今天跟进、谁在升温、风险信号 |
| **时间故事线** | 最近私聊按时间排序，红点 = 对方在等你 |
| **最近新联系人** | 最近加过的联系人，今日新增有标签 |

---

## 环境要求

| 要求 | 说明 |
|---|---|
| macOS | Windows/Linux 暂不支持 |
| 微信 Mac 版 | 需正在运行 |
| Python 3.9+ | `python3 --version` 查看 |
| Node.js | 用于安装 wechat-cli |
| Anthropic API Key | 可选，用于 AI 分析（[免费申请](https://console.anthropic.com)） |

---

## 快速开始

```bash
git clone https://github.com/cx8cool/wechat-daily-report.git
cd wechat-daily-report
bash setup.sh
```

`setup.sh` 会全程引导你完成所有配置，包括：
- 检查并安装依赖
- 自动检测你的微信 ID
- 填入 Anthropic API Key
- 设置每日定时自动生成

---

## 手动配置（跳过向导）

### 1. 安装 wechat-cli

```bash
npm install -g @canghe_ai/wechat-cli
sudo wechat-cli init   # 微信正在运行时执行，只需一次
```

### 2. 安装 Python 依赖

```bash
pip3 install -r requirements.txt
```

### 3. 创建配置文件

```bash
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
  "self_wxid": "你的微信ID",
  "days_back": 1,
  "anthropic_api_key": "sk-ant-...",
  "report_title": "微信日报"
}
```

**找你的微信 ID：**

```bash
ls ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
```

最近修改的那个文件夹名就是你的 ID（类似 `wxid_xxx` 或 `qq123_xxx`）。

### 4. 生成日报

```bash
python3 daily_report.py
```

---

## 每日自动生成

```bash
bash schedule.sh        # 默认每天 8:00 生成
bash schedule.sh 7      # 改为每天 7:00
bash schedule.sh --uninstall   # 取消
```

---

## 开启完整 AI 分析

默认 AI 只能看到每个会话的最后一条消息摘要。开启完整对话分析：

```bash
sudo /Users/你的用户名/.local/share/wechat-cli/wxkey bootstrap
```

成功后再生成日报，AI 就能分析完整的聊天记录了。

> 注：每次重启微信后需重新运行 `wxkey bootstrap`

---

## 日常使用

```bash
python3 daily_report.py           # 生成并打开
python3 daily_report.py --days 2  # 分析最近 2 天
python3 daily_report.py --no-browser  # 只生成不打开
```

---

## 配置项说明

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `self_wxid` | — | 你的微信 ID（必填） |
| `days_back` | `1` | 分析最近几天的消息 |
| `anthropic_api_key` | `""` | Anthropic API Key，留空则不启用 AI |
| `report_title` | `"微信日报"` | 日报标题 |

---

## 数据安全说明

- 所有微信数据只在本地读取，不上传
- `config.json`、`report.html`、`.state.json` 已加入 `.gitignore`，不会被 git 追踪
- 如果不填 `anthropic_api_key`，任何数据都不会离开你的电脑

---

## 工作原理

微信 Mac 版将聊天数据存储在本地 SQLite 数据库里：

```
~/Library/Containers/com.tencent.xinWeChat/Data/
  Documents/xwechat_files/<你的wxid>/db_storage/
    contact/contact.db      ← 联系人（无需解密）
    session/session.db      ← 会话列表（无需解密）
    message/message_0.db    ← 消息内容（需要 wxkey 解密）
```

`wechat-cli` 直接读取本地数据库，不经过微信服务器。AI 分析（Claude）只收到消息文本，不包含你的微信 ID 或联系人列表。

---

## License

MIT
