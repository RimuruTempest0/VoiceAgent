# 技术报告 — Day 1

## 1. 项目背景与目标
工业园区门卫数字化：访客拨打/接入号码 → AI Agent 自然对话采集车牌/单位/事由/手机号 → 推送结构化访客信息到保安微信 → 保安远程放行。

**硬约束**：从 Agent 开始说话到微信消息发出 ≤ 25s，对话必须像真人而非机械问答。

## 2. 技术选型与决策

### 2.1 接入层：浏览器麦克风 vs Twilio PSTN
**选择浏览器（WebRTC/Web Audio API）**。

- Twilio 试用号会强制播放 5-8s 英文广告，吃掉宝贵的 25s 预算；
- Twilio 中国号码不可申请，跨境呼叫延迟+合规复杂；
- 阿里云语音服务（dyvmsapi）主要面向外呼场景，CCC 联络中心面向企业级，对一周作业过重；
- 浏览器麦克风是 WebRTC 通用能力，PCM 16k 直传 bridge，**没有 μ-law 编解码、没有运营商协议、没有公网号码合规**。

题目允许"号码方案你说了算"，README 与答辩中明确说明此 trade-off。

### 2.2 STT/TTS：阿里云 NLS vs Groq Whisper / Edge TTS
**选择阿里云智能语音交互（NLS）**：
- 国内节点延迟低（实测 ASR partial < 300ms，TTS 首字节 ~300ms）；
- 中文场景识别质量明显优于 Whisper（虽然 Whisper-large 也强，但 Groq 海外节点延迟+90ms）；
- 免费额度 3 个月够整个开发周期；
- WebSocket 双向流式协议适合 Voice Agent。

### 2.3 LLM 层：Hermes Agent
沿用用户已配好的 Hermes：OpenAI 兼容、`xiaomi/mimo-v2.5-pro → deepseek v4 via OpenRouter`、已有微信通道（备用）。
**调试中发现 Hermes 自带 14k system prompt 会"软化"我们的 skill 约束** → 改进见 §3.3。

### 2.4 推送层：企业微信群机器人 webhook
对比方案：
- 个人微信 (itchat/wxauto)：协议易变、易被封；
- Hermes 自带的微信 DM：能用但绑定到单个收件人，调试时较难触发；
- **企微群机器人 webhook**：1 个 POST 搞定，响应 <1s，门卫加进群即可，最稳。

## 3. 关键问题与解决

### 3.1 Python 3.13 移除 `audioop`
旧 μ-law/PCM 转换代码作废。**因为已改用浏览器直传 16k PCM，不需要转换**。删除 `audio_utils.py`。

### 3.2 NLS ASR Gateway IDLE_TIMEOUT
NLS 闲置 10s 会切断 WebSocket。bridge 之前会因 send_pcm 失败而崩链。
**解决**：`AsrSession.alive` 属性 + bridge 在 send_pcm 前判断，断了就透明重启（`_start_asr()`）。浏览器无感。

### 3.3 Hermes 不严格遵守 SKILL.md
DeepSeek 之前要求"确认后输出 register_visitor JSON"，结果输出 markdown 列表，并跑题去问"叉车需要吗"。
诊断：Hermes 自带 14k system prompt 把 skill 当成"建议"。
**解决**：bridge 启动时加载 SKILL.md → 每次 `chat_completions` 请求把内容作为 `role:system` 第一条消息注入。curl 验证 Hermes 透传 system role（A2 海盗测试通过，A3 强制 JSON 输出通过），代码已落地，**待 e2e 复测**。

### 3.4 流式 → 按句 TTS 流水线
原本等 LLM 全部输出完再 TTS，首字节延迟 ≈ LLM 完整时间 + TTS handshake。
**改造**：producer 任务读 Hermes SSE，按 `。！？\n` 切句推入 queue；consumer 任务取一句就调 NLS TTS，PCM 边到边推送回浏览器。**Hermes 第一句出来即可开口**。
实测在 DeepSeek 切换后，turn1 用户 final → 用户听到首字节降至 ~4.5s。

### 3.5 Hermes JSON 抽取与过滤
`_extract_visitor_json` 支持 fenced (` ```json ... ``` `) 和 bare JSON，匹配 `action == "register_visitor"`。
`_looks_like_json_fragment` 在分句送 TTS 前过滤掉 JSON 片段，避免门卫听到"花括号 action 冒号 register…"。

## 4. 实测延迟（DeepSeek + system-prompt 注入前）

```
0.0s   WS open
1.7s   greeting 播完
6.4s   user final
11.4s  Hermes 首字节（thinking 5.0s）
11.7s  user 听到首音节（系统延迟 +5.3s）
15.0s  Agent 一轮说完
16.0s  user 说「对」
19.2s  Hermes turn2 首字节（thinking 2.2s）
23.5s  Agent 二轮说完
```

JSON 未输出 → push 未触发。

**目标**：注入 system prompt 后，turn2 输出 `register_visitor` JSON，触发 push，总 ≤ 22s。

## 5. 当前进度
- ✅ bridge_server 骨架（FastAPI + WS）
- ✅ 阿里云 NLS Token 签名（手写 HMAC-SHA1，无 SDK 依赖）
- ✅ NLS 流式 ASR/TTS 客户端
- ✅ Hermes 流式 + 按句 TTS 流水线
- ✅ ASR 自动重启容错
- ✅ JSON 检测器（fenced/bare，单测覆盖）
- ✅ 企微 webhook 推送（实测群里收到访客卡片）
- ✅ web/index.html 简洁页面（Tailwind CDN，黑底高对比，25s 计时）
- ✅ headless 2-turn e2e 脚本
- ✅ SKILL.md system-role 注入（代码已落地，**待 e2e 复测**）

## 6. 待解决（下一次开工）
1. **复测 system-prompt 注入效果**：跑 `e2e_register.py`，看 Hermes 是否输出 JSON、是否成功推送。
2. **NLS 热词**：去控制台加 `蓝色鲸鱼:50`，修正 ASR 把"鲸鱼"识成"金域/金运/金玉"。
3. **回访识别**：现有 transcripts 已经有用户登记历史；演示加分项可在 Hermes 的 memories 里写入 `(plate, company, phone, purpose)` 字典，开场用 NLS 先识别车牌再触发短路确认 ("张师傅您好，今天还是…?")。
4. **demo 视频脚本**：1-2 分钟，覆盖正常路径 + 边缘场景（用户改口/纠正）。
5. **README 加 Demo GIF / 录屏**：让 reviewer 不跑也能看到效果。

## 7. 进一步可挖
- **Serverless 部署**：bridge_server 是无状态的（除了内存里的 SessionManager），可拆成 Cloudflare Workers + Durable Object，或直接 Fly.io / Render。但本地够 demo，不强求。
- **多路并发**：FastAPI + asyncio 单进程已天然并发，瓶颈在 Hermes 单例的 HTTP 队列上。如要严肃压测，需要 Hermes 横向扩展。
- **门卫查询 Agent**：交付要求里的加分项。可以在 bridge 加一个 `/admin/voice` 端点，相同管道，跑另一个 skill（visitor-query）。SQLite 持久化已经在 `~/.hermes/state.db` 等位置存在，可借力。
