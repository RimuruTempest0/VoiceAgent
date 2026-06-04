"""企业微信群机器人 webhook 推送 — 访客登记完成时由 bridge 调用。

机器人配置：企业微信群 > 添加机器人 > 获得 webhook URL，形如：
  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxx

接口文档：
  https://developer.work.weixin.qq.com/document/path/91770
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _format_markdown(info: dict) -> str:
    return (
        f"## 🚗 访客登记 {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"> **车牌号**：<font color=\"info\">{info.get('plate','-')}</font>\n"
        f"> **来访单位**：{info.get('company','-')}\n"
        f"> **来访事由**：{info.get('purpose','-')}\n"
        f"> **手机号**：{info.get('phone','-')}\n\n"
        f"请确认后放行 ✅"
    )


async def send_visitor(info: dict) -> bool:
    """Push a visitor record to the configured 企微群 webhook.

    Returns True on success, False if pushing is disabled or fails.
    """
    url = settings.wechat_webhook_url
    if not url:
        logger.warning("WECHAT_WEBHOOK_URL not set — would have pushed: %s", info)
        return False
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": _format_markdown(info)},
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") not in (0, None):
                logger.warning("WeChat webhook returned %s", data)
                return False
            logger.info(
                "WeChat push ok: plate=%s company=%s", info.get("plate"), info.get("company")
            )
            return True
    except Exception:
        logger.exception("WeChat push failed")
        return False
