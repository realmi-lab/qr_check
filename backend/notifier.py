"""알림 보내기. 슬랙 토큰이 없으면 터미널에 출력.

여러 명에게 보낼 때는 send_many()로 동시에 보냄 (한 명씩 보내면 0.5초씩 걸려 수십 명이면 몇십 초).
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from backend import rooms
from frontend.blocks import Reply, context
from backend.config import settings

log = logging.getLogger(__name__)

Message = Reply | str


def _parts(msg: Message) -> tuple[str, list[dict] | None]:
    if isinstance(msg, Reply):
        return msg.summary, msg.blocks
    return msg, None


class ConsoleNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def dm(self, employee: dict, msg: Message) -> None:
        text = f"{msg.summary}\n{msg.text}" if isinstance(msg, Reply) else msg
        with self._lock:
            self.sent.append((employee["name"], text))
            print(f"[DM → {employee['name']}] {text}\n")

    def channel(self, msg: Message) -> None:
        text = f"{msg.summary}\n{msg.text}" if isinstance(msg, Reply) else msg
        with self._lock:
            self.sent.append(("#channel", text))
            print(f"[채널] {text}\n")

    def send_many(self, items: list[tuple[dict, Message]]) -> None:
        for emp, msg in items:
            self.dm(emp, msg)


class SlackNotifier:
    def __init__(self, client: WebClient) -> None:
        self.client = client
        self._not_in_slack: set[str] = set()   # 슬랙에 없는 이메일은 다시 찾지 않음
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="slack-dm")

    def slack_user_for(self, employee: dict) -> str | None:
        """직원 → 슬랙 사용자. 연결 기록이 없으면 이메일로 찾아서 연결해 둠."""
        uid = rooms.slack_user_of(employee["id"])
        if uid:
            return uid
        if employee["email"] in self._not_in_slack:
            return None
        try:
            uid = self.client.users_lookupByEmail(email=employee["email"])["user"]["id"]
        except SlackApiError:
            self._not_in_slack.add(employee["email"])
            return None
        rooms.link_slack_user(employee["id"], uid)
        return uid

    def dm(self, employee: dict, msg: Message) -> None:
        text, blocks = _parts(msg)
        if settings.notify_override_slack_user:
            uid = settings.notify_override_slack_user
            blocks = (blocks or []) + [context(f"_시연: 원래 받는 사람 {employee['name']}_")]
        else:
            uid = self.slack_user_for(employee)
        if not uid:
            log.debug("슬랙 계정을 찾지 못해 DM을 건너뜀: %s <%s>", employee["name"], employee["email"])
            return
        try:
            self.client.chat_postMessage(channel=uid, text=text, blocks=blocks, unfurl_links=False)
        except SlackApiError as e:
            log.warning("DM 실패 (%s): %s", employee["name"], e.response.get("error"))

    def channel(self, msg: Message) -> None:
        if not settings.slack_notify_channel:
            return
        text, blocks = _parts(msg)
        try:
            self.client.chat_postMessage(channel=settings.slack_notify_channel, text=text, blocks=blocks,
                                         unfurl_links=False)
        except SlackApiError as e:
            log.warning("채널 공지 실패: %s", e.response.get("error"))

    def send_many(self, items: list[tuple[dict, Message]]) -> None:
        list(self._pool.map(lambda it: self.dm(*it), items))


def default_notifier():
    if settings.slack_bot_token:
        return SlackNotifier(WebClient(token=settings.slack_bot_token))
    return ConsoleNotifier()
