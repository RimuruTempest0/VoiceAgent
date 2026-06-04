import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    hermes_base_url: str = os.getenv("HERMES_BASE_URL", "http://localhost:8642/v1")
    hermes_api_key: str = os.getenv("HERMES_API_KEY", "voiceagent-secret-key-2024")
    hermes_model: str = os.getenv("HERMES_MODEL", "hermes-agent")
    skill_path: str = os.getenv(
        "SKILL_PATH",
        os.path.expanduser("~/.hermes/profiles/voiceagent/skills/visitor-registration/SKILL.md"),
    )

    aliyun_ak_id: str = os.getenv("ALIYUN_AK_ID", "")
    aliyun_ak_secret: str = os.getenv("ALIYUN_AK_SECRET", "")
    aliyun_nls_appkey: str = os.getenv("ALIYUN_NLS_APPKEY", "")
    aliyun_nls_region: str = os.getenv("ALIYUN_NLS_REGION", "cn-shanghai")
    aliyun_nls_tts_voice: str = os.getenv("ALIYUN_NLS_TTS_VOICE", "zhixiaoxia")

    wechat_webhook_url: str = os.getenv("WECHAT_WEBHOOK_URL", "")


settings = Settings()
