# VoiceAgent — 园区访客登记语音 Agent

替代保安人工问询的语音 AI：访客在浏览器（或 H5/小程序）通过麦克风讲话，Agent 自然对话采集车牌/单位/事由/手机号，确认后推送结构化访客信息到保安的企业微信群。

## 架构

```
浏览器 (web/index.html)
   │  WebSocket (PCM16 / 16kHz, 双向)
   ▼
bridge_server (FastAPI)  ←  注入 SKILL.md 为 system role
   ├──► 阿里云 NLS 实时 ASR   (wss, 流式 SentenceEnd)
   ├──► Hermes  /v1/chat/completions  (OpenAI 兼容，DeepSeek v4 via OpenRouter)
   ├──► 阿里云 NLS 流式 TTS   (按句切分，首字节 <300ms)
   └──► 企微群机器人 webhook  (检测到 register_visitor JSON 后推送)
```

**关键设计**
- 浏览器麦克风替代 PSTN，避开 Twilio/SIP 的号码、备案、跨境合规问题。题目允许"号码方案你说了算"。
- Hermes 流式 → 按句切（。！？\n）→ 分句 TTS：让用户在 Agent 思考结束、整段文字未完时就听到第一句。
- ASR 自动重启：NLS 闲置 10s 会切断 WS，bridge 检测后透明重建，浏览器无感。
- SKILL.md 通过 `role:system` 注入：Hermes 自身的 14k system prompt 会软化 skill 约束，注入解决之。

详细技术决策与 trade-off 见 [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md)。

## 部署

依赖：Python 3.13、Hermes 已在 `http://localhost:8642/v1` 运行、阿里云 NLS 已开通、企微群机器人。

```bash
git clone <repo>
cd VoiceAgent

python3 -m venv .venv
.venv/bin/pip install -r bridge_server/requirements.txt

cp bridge_server/.env.example .env
# 编辑 .env，填入阿里云 AK、AppKey、企微 webhook URL

.venv/bin/python -m uvicorn bridge_server.main:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://127.0.0.1:8000` → 点中间绿色按钮 → 对麦克风说话。
门卫的企业微信群里会收到访客卡片。

冒烟测试：
```bash
.venv/bin/python bridge_server/tests/e2e_register.py     # 2-turn 全链路
```

## 环境变量

| 变量 | 说明 | 示例 |
|---|---|---|
| `HOST` / `PORT` | bridge 监听 | `0.0.0.0` / `8000` |
| `HERMES_BASE_URL` | Hermes OpenAI 兼容端点 | `http://localhost:8642/v1` |
| `HERMES_API_KEY` | Hermes 鉴权 | `voiceagent-secret-key-2024` |
| `HERMES_MODEL` | Hermes 模型 alias | `hermes-agent` |
| `SKILL_PATH` | SKILL.md 路径，启动时加载注入 | `~/.hermes/profiles/voiceagent/skills/visitor-registration/SKILL.md` |
| `ALIYUN_AK_ID` / `ALIYUN_AK_SECRET` | NLS Token 签名 | RAM 子账号 + `AliyunNLSFullAccess` 策略 |
| `ALIYUN_NLS_APPKEY` | NLS 项目 AppKey | NLS 控制台「全部项目」 |
| `ALIYUN_NLS_REGION` | NLS 区域 | `cn-shanghai` |
| `ALIYUN_NLS_TTS_VOICE` | TTS 音色 | `zhixiaoxia` |
| `WECHAT_WEBHOOK_URL` | 企微群机器人 webhook | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` |

> 凭证类变量都在 `.env`（已 gitignore）。
