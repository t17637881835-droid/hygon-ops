"""飞书消息发送模块（支持重试队列）"""
import json
import time
import requests
from dataclasses import dataclass
from typing import Optional, List
from threading import Thread, Lock

from logger import get_logger

logger = get_logger("feishu_sender")

# 重试配置
MAX_RETRIES = 3
RETRY_INTERVALS = [1, 5, 30]  # 秒


@dataclass
class PendingMessage:
    content: str
    chat_id: Optional[str] = None
    retry_count: int = 0
    next_retry_time: float = 0


class RetryQueue:
    """发送失败消息的重试队列"""

    def __init__(self):
        self._queue: List[PendingMessage] = []
        self._lock = Lock()

    def add(self, content: str, chat_id: Optional[str] = None):
        with self._lock:
            self._queue.append(PendingMessage(
                content=content,
                chat_id=chat_id,
                next_retry_time=time.time()
            ))

    def get_due_messages(self) -> List[PendingMessage]:
        current = time.time()
        due = []
        with self._lock:
            remaining = []
            for msg in self._queue:
                if current >= msg.next_retry_time:
                    due.append(msg)
                else:
                    remaining.append(msg)
            self._queue = remaining
        return due

    def reschedule(self, msg: PendingMessage, retry_count: int):
        if retry_count < len(RETRY_INTERVALS):
            msg.retry_count = retry_count + 1
            msg.next_retry_time = time.time() + RETRY_INTERVALS[retry_count]
            with self._lock:
                self._queue.append(msg)

    def size(self) -> int:
        with self._lock:
            return len(self._queue)


class FeishuSender:
    def __init__(self, webhook_url: str, app_id: str = "", app_secret: str = ""):
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_access_token = ""
        self._tenant_access_token_expire_at = 0.0
        self.retry_queue = RetryQueue()
        self._worker = None
        self._running = False

    def send_text(self, content: str, chat_id: Optional[str] = None, receive_id_type: str = "chat_id") -> bool:
        """发送文本消息，优先按 chat_id 通过 Bot API 回复，失败时降级 Webhook"""
        if chat_id and self.app_id and self.app_secret:
            if self._send_text_by_bot_api(content, chat_id, receive_id_type=receive_id_type):
                return True
            logger.warning("Bot API 发送失败，降级为 Webhook 发送")

        return self._send_text_by_webhook(content, chat_id)

    def _send_text_by_webhook(self, content: str, chat_id: Optional[str] = None) -> bool:
        payload = {
            "msg_type": "text",
            "content": {"text": content}
        }

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"飞书消息发送成功: {content[:30]}...")
                return True
            else:
                logger.warning(f"飞书消息发送失败: status={response.status_code}")
                self._schedule_retry(content, chat_id)
                return False
        except Exception as e:
            logger.error(f"飞书消息发送异常: {e}")
            self._schedule_retry(content, chat_id)
            return False

    def _send_text_by_bot_api(self, content: str, chat_id: str, receive_id_type: str = "chat_id") -> bool:
        token = self._get_tenant_access_token()
        if not token:
            return False

        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": content}, ensure_ascii=False),
        }
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                json=payload,
                headers=headers,
                timeout=10,
            )
            if response.status_code != 200:
                return False
            data = response.json()
            return data.get("code", 0) == 0
        except Exception as e:
            logger.error(f"飞书 Bot API 发送异常: {e}")
            return False

    def _get_tenant_access_token(self) -> str:
        if self._tenant_access_token and time.time() < self._tenant_access_token_expire_at:
            return self._tenant_access_token

        try:
            response = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=10,
            )
            if response.status_code != 200:
                return ""
            data = response.json()
            if data.get("code", 0) != 0:
                return ""
            self._tenant_access_token = data.get("tenant_access_token", "")
            expire = int(data.get("expire", 7200))
            self._tenant_access_token_expire_at = time.time() + max(expire - 300, 60)
            return self._tenant_access_token
        except Exception as e:
            logger.error(f"获取 tenant_access_token 异常: {e}")
            return ""

    def _schedule_retry(self, content: str, chat_id: Optional[str] = None):
        """将发送失败的消息加入重试队列"""
        self.retry_queue.add(content, chat_id)
        logger.warning(f"消息已加入重试队列，当前队列大小: {self.retry_queue.size()}")

    def start_retry_worker(self):
        """启动重试worker"""
        self._running = True
        self._worker = Thread(target=self._retry_loop, daemon=True)
        self._worker.start()
        logger.info("飞书发送重试worker已启动")

    def stop_retry_worker(self):
        self._running = False
        if self._worker:
            self._worker.join(timeout=5)

    def _retry_loop(self):
        """重试循环"""
        while self._running:
            due_messages = self.retry_queue.get_due_messages()
            for msg in due_messages:
                if self._attempt_send(msg.content, msg.retry_count, msg.chat_id):
                    logger.info(f"重试发送成功: {msg.content[:30]}...")
                else:
                    if msg.retry_count < MAX_RETRIES:
                        self.retry_queue.reschedule(msg, msg.retry_count)
                        logger.warning(f"重试失败，将再次重试（次数={msg.retry_count + 1}）")
                    else:
                        logger.error(f"重试次数耗尽，放弃发送: {msg.content[:30]}...")

            time.sleep(1)

    def _attempt_send(self, content: str, retry_count: int, chat_id: Optional[str] = None) -> bool:
        """尝试发送消息"""
        return self.send_text(content, chat_id=chat_id)

    def send_at_message(self, content: str, at_user_ids: list = None) -> bool:
        """发送@消息"""
        payload = {
            "msg_type": "text",
            "content": {"text": content}
        }
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            if response.status_code != 200:
                self._schedule_retry(content)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"飞书@消息发送异常: {e}")
            self._schedule_retry(content)
            return False

    def send_card(self, card_content: dict, chat_id: Optional[str] = None, receive_id_type: str = "chat_id") -> bool:
        """发送卡片消息"""
        if not chat_id or not self.app_id or not self.app_secret:
            logger.warning("发送卡片需要 chat_id 和 app_id/app_secret")
            return False

        token = self._get_tenant_access_token()
        if not token:
            return False

        # 根据文档，使用 message/v4/send 接口
        payload = {
            "msg_type": "interactive",
            "card": card_content,
        }

        # 根据文档，使用 chat_id/open_id/user_id/email 中的一个
        if receive_id_type == "chat_id":
            payload["chat_id"] = chat_id
        elif receive_id_type == "open_id":
            payload["open_id"] = chat_id
        elif receive_id_type == "user_id":
            payload["user_id"] = chat_id
        else:
            payload["chat_id"] = chat_id

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        try:
            response = requests.post(
                "https://open.feishu.cn/open-apis/message/v4/send/",
                json=payload,
                headers=headers,
                timeout=10,
            )
            if response.status_code != 200:
                logger.warning(f"卡片发送失败: status={response.status_code}")
                return False
            data = response.json()
            if data.get("code", 0) != 0:
                logger.warning(f"卡片发送失败: code={data.get('code')} msg={data.get('msg')}")
                return False
            logger.info(f"卡片发送成功: chat_id={chat_id}")
            return True
        except Exception as e:
            logger.error(f"卡片发送异常: {e}")
            return False

    def update_card(self, card_content: dict, message_id: str) -> bool:
        """更新已发送的卡片消息"""
        if not message_id or not self.app_id or not self.app_secret:
            logger.warning("更新卡片需要 message_id 和 app_id/app_secret")
            return False

        token = self._get_tenant_access_token()
        if not token:
            return False

        # 根据飞书文档，使用 im/v1/messages/{message_id} 接口
        payload = {
            "msg_type": "interactive",
            "content": json.dumps(card_content),
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        try:
            response = requests.patch(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                json=payload,
                headers=headers,
                timeout=10,
            )
            if response.status_code != 200:
                logger.warning(f"卡片更新失败: status={response.status_code} response={response.text}")
                return False
            data = response.json()
            if data.get("code", 0) != 0:
                logger.warning(f"卡片更新失败: code={data.get('code')} msg={data.get('msg')}")
                return False
            logger.info(f"卡片更新成功: message_id={message_id}")
            return True
        except Exception as e:
            logger.error(f"卡片更新异常: {e}")
            return False
