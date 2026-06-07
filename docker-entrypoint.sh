#!/bin/bash
set -e

HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
PROFILE_DIR="$HERMES_HOME/profiles/voiceagent"
SKILLS_DIR="$PROFILE_DIR/skills"

# Initialize Hermes profile if not present
mkdir -p "$SKILLS_DIR" "$HERMES_HOME/memories"

# Copy skills into Hermes profile
if [ -d /app/skills/visitor-registration ]; then
  cp -r /app/skills/visitor-registration "$SKILLS_DIR/" 2>/dev/null || true
fi
if [ -d /app/skills/visitor-query ]; then
  cp -r /app/skills/visitor-query "$SKILLS_DIR/" 2>/dev/null || true
fi

# Write SOUL.md
cat > "$PROFILE_DIR/SOUL.md" << 'SOUL'
你是工业园区门卫 AI 助手（VoiceAgent）。你有两项核心能力：

1. 访客登记：通过语音或文字对话采集来访车辆信息（车牌、单位、手机、事由），完成后通知门卫放行。
2. 访客查询：保安或管理人员可以向你查询访客统计数据，如本周来访车辆数、高峰时段、某访客来访记录等。

你的回复简洁直接，像真人门卫一样自然。只回答与门卫工作和访客管理相关的问题。
SOUL

# Write minimal Hermes profile config if missing
if [ ! -f "$PROFILE_DIR/config.yaml" ]; then
  cat > "$PROFILE_DIR/config.yaml" << 'CONF'
model:
  default: deepseek-chat
  provider: deepseek
  base_url: https://api.deepseek.com/v1
agent:
  max_turns: 90
toolsets:
- hermes-cli
CONF
fi

# Start Hermes gateway in background (if WeCom is configured)
if [ -n "$WECOM_BOT_ID" ] && [ -n "$WECOM_SECRET" ]; then
  hermes -p voiceagent gateway &
  echo "[entrypoint] Hermes gateway started"
fi

# Start bridge_server (foreground)
exec python -m bridge_server.main
