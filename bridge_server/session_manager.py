"""Per-conversation session state for the web voice channel.

One session corresponds to one browser tab that connected to /voice.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CallSession:
    session_id: str
    started_at: float = field(default_factory=time.monotonic)
    transcript: list[dict] = field(default_factory=list)
    visitor_info: dict = field(default_factory=dict)  # populated by Hermes skill output
    completed: bool = False

    def append_user_turn(self, text: str) -> None:
        self.transcript.append({"role": "user", "content": text})

    def append_agent_turn(self, text: str) -> None:
        self.transcript.append({"role": "assistant", "content": text})

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}

    def create(self) -> CallSession:
        session_id = uuid.uuid4().hex[:12]
        session = CallSession(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[CallSession]:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> Optional[CallSession]:
        return self._sessions.pop(session_id, None)

    def active_count(self) -> int:
        return len(self._sessions)


sessions = SessionManager()
