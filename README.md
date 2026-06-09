# VoiceAgent — 园区访客登记语音 AI

用语音 AI 替代保安人工问询：访客对麦克风说话，Agent 通过自然对话采集车牌/单位/事由/手机号，确认后自动推送访客信息到保安企业微信群。保安也可以在企业微信配置机器人查询访客统计。

本项目为采用传统打电话的方式建立语音交互，而是通过微信扫码导航至网页进行语音交互。

## 技术选型

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | 原生 HTML + Web Audio API | 单页面，通过 `AudioWorklet` 采集麦克风 PCM16 并双向 WebSocket 传输 |
| 后端框架 | FastAPI + Uvicorn | 异步 WebSocket 服务，处理音频流管道 |
| 语音识别 (ASR) | 阿里云 NLS 实时语音识别 | 流式识别，支持中间结果与句尾检测 |
| 语音合成 (TTS) | 阿里云 NLS 流式语音合成 | 按句切分合成，低延迟返回音频流 |
| LLM 推理 | DeepSeek (经 Hermes Gateway 路由) | OpenAI 兼容接口，流式输出；通过注入 SKILL.md 控制对话行为 |
| Agent 框架 | Hermes Agent | 管理 profile/skill/channel，提供企微 AI Bot 能力 |
| 数据存储 | SQLite | 轻量持久化访客记录，挂载 volume 保证数据安全 |
| 消息推送 | 企业微信群机器人 Webhook | 登记完成后自动推送访客信息到保安群 |
| 部署 | Docker + ngrok | 容器化部署，ngrok 提供公网临时访问 |

## 架构

```
浏览器 (web/index.html)
   │  WebSocket (PCM16 16kHz 双向)
   ▼
bridge_server (FastAPI)
   ├──► 阿里云 NLS 实时 ASR (流式)
   ├──► Hermes /v1/chat/completions (流式)
   ├──► 阿里云 NLS 流式 TTS (按句切分)
   ├──► 企微 webhook (访客通知)
   └──► SQLite visitors.db (记录持久化)

Hermes Gateway (企业微信 AI Bot)
   └──► 保安 @机器人 查询 → visitor-query skill → SQLite
```

## 快速部署（Docker）

前提：宿主机已安装 Hermes Agent 并手动启动 gateway，创建 profile 'voiceagent'（企微 AI Bot 功能依赖此服务）。

```bash
# 0. 启动 Hermes gateway（宿主机上运行，保持常驻）
hermes -p voiceagent gateway

# 1. 克隆项目并配置
git clone https://github.com/RimuruTempest0/VoiceAgent.git
cd VoiceAgent
cp .env.example .env
# 编辑 .env，填入阿里云 NLS、企微 webhook、DEEPSEEK_API_KEY 等

# 2. 构建镜像
docker build -t voiceagent .

# 3. 启动服务
docker run -d \
  --name voiceagent \
  --env-file .env \
  -e PORT=8000 \
  -p 8000:8000 \
  -v ./data:/app/data \
  voiceagent

# 4. 验证
curl http://localhost:8000/health
```

### 公网访问（ngrok）

为了使公网可以访问服务，使用 ngrok 将本地端口暴露到公网。ngrok 运行在宿主机上（不在容器内），避免容器重启导致隧道断开。

```bash
ngrok http 8000
```

ngrok 免费版每次启动分配随机 URL，重启后需更新企微后台回调地址。

浏览器打开 `http://localhost:8000` 即可使用。

## 本地开发部署

依赖：Python 3.11+、Hermes Agent、阿里云 NLS 已开通。

```bash
# 1. 安装 Python 依赖
python3 -m venv .venv
.venv/bin/pip install -r bridge_server/requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env

# 3. 启动服务
.venv/bin/python -m bridge_server.main
```

服务启动后会自动：
- 预缓存 greeting TTS 音频
- 启动 Hermes gateway（如果配置了企业微信 AI Bot）

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `ALIYUN_AK_ID` | 是 | 阿里云 AccessKey ID |
| `ALIYUN_AK_SECRET` | 是 | 阿里云 AccessKey Secret |
| `ALIYUN_NLS_APPKEY` | 是 | NLS 项目 AppKey |
| `ALIYUN_NLS_REGION` | 否 | NLS 区域，默认 `cn-shanghai` |
| `ALIYUN_NLS_TTS_VOICE` | 否 | TTS 音色，默认 `zhixiaoxia` |
| `WECHAT_WEBHOOK_URL` | 是 | 企微群机器人 webhook URL |
| `HERMES_BASE_URL` | 否 | Hermes 端点，默认 `http://localhost:8642/v1` |
| `WECOM_BOT_ID` | 否 | 企微 AI Bot ID（查询功能） |
| `WECOM_SECRET` | 否 | 企微 AI Bot Secret |
| `HOST` / `PORT` | 否 | 监听地址，默认 `0.0.0.0:8000` |

## 项目结构

```
VoiceAgent/
├── bridge_server/          # FastAPI 主服务
│   ├── main.py             # WebSocket 路由 + 流式管道
│   ├── stt_service.py      # 阿里云 NLS ASR
│   ├── tts_service.py      # 阿里云 NLS TTS
│   ├── hermes_client.py    # Hermes LLM 客户端
│   ├── visitor_store.py    # SQLite 访客存储
│   ├── wechat_push.py      # 企微 webhook 推送
│   └── config.py           # 配置加载
├── web/                    # 前端页面
│   └── index.html          # 单页面，含麦克风采集 + 音频播放
├── skills/                 # Hermes 技能定义
│   ├── visitor-registration/  # 访客登记 skill
│   └── visitor-query/         # 访客查询 skill
├── data/                   # 运行时数据（gitignore）
│   └── visitors.db         # SQLite 数据库
├── Dockerfile              # Docker 构建
├── docker-entrypoint.sh    # 容器启动脚本
└── .env.example            # 环境变量模板
```
