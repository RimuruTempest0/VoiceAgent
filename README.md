# VoiceAgent — 园区访客登记语音 AI

用语音 AI 替代保安人工问询：访客对麦克风说话，Agent 通过自然对话采集车牌/单位/事由/手机号，确认后自动推送访客信息到保安企业微信群。保安也可以在企业微信 @机器人 查询访客统计。



## 架构

```
浏览器 (web/index.html)
   │  WebSocket (PCM16 16kHz 双向)
   ▼
bridge_server (FastAPI, port 8000)
   ├──► 阿里云 NLS 实时 ASR (wss, 流式)
   ├──► Hermes /v1/chat/completions (流式)
   ├──► 阿里云 NLS 流式 TTS (按句切分)
   ├──► 企微 webhook (访客通知)
   └──► SQLite visitors.db (记录持久化)

Hermes Gateway (企业微信 AI Bot)
   └──► 保安 @机器人 查询 → visitor-query skill → SQLite
```

## 快速部署（Docker）

前提：宿主机已安装并运行 Hermes Agent（`hermes -p voiceagent serve`，默认监听 8642 端口）。

```bash
git clone https://github.com/RimuruTempest0/VoiceAgent.git
cd VoiceAgent

# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入阿里云 NLS 凭证、企微 webhook 等

# 2. 构建镜像
docker build -t voiceagent .

# 3. 启动服务
docker run -d \
  --name voiceagent \
  --env-file .env \
  -e HERMES_BASE_URL=http://host.docker.internal:8642/v1 \
  -p 8000:8000 \
  -v ./data:/app/data \
  voiceagent

# 4. 验证
curl http://localhost:8000/health
```

> 容器通过 `host.docker.internal` 连接宿主机上的 Hermes 服务。

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

# 3. 安装 Hermes Agent (如果还没有)
# 参考 https://github.com/NousResearch/hermes-agent

# 4. 启动服务
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
