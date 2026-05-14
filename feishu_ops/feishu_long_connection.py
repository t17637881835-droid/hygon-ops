"""飞书长连接事件订阅（WebSocket）

使用 lark-oapi 提供的长连接客户端订阅飞书事件，避免依赖公网 webhook。
仅依赖 app_id / app_secret，飞书侧需在开发者后台 "事件订阅 → 订阅方式" 选择
"使用长连接接收"。所有事件经过同一个回调转成 dict，再交由 main.py 的
通用 dispatch 逻辑处理，保持与 webhook 路径的等价语义。
"""
import json
import threading
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from logger import get_logger

logger = get_logger("feishu_ws")

PayloadHandler = Callable[[dict], None]


def _marshal_to_dict(data) -> dict:
    """把 lark-oapi 的强类型事件对象转成 webhook 等价的 dict。"""
    return json.loads(lark.JSON.marshal(data))


class FeishuLongConnectionSubscriber:
    """飞书长连接订阅客户端。

    在独立的守护线程中跑 ``lark.ws.Client.start()``，该方法内部会自建 asyncio
    事件循环并阻塞，因此不能直接放在主线程 / FastAPI 的事件循环里。
    """

    def __init__(self, app_id: str, app_secret: str, payload_handler: PayloadHandler):
        if not app_id or not app_secret:
            raise ValueError("FEISHU_APP_ID / FEISHU_APP_SECRET 不能为空")
        self.app_id = app_id
        self.app_secret = app_secret
        self.payload_handler = payload_handler
        self._thread: threading.Thread = None
        self._client: lark.ws.Client = None

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        try:
            payload = _marshal_to_dict(data)
            event_id = (payload.get("header") or {}).get("event_id", "")
            event = payload.get("event") or {}
            sender = (event.get("sender") or {}).get("sender_id") or {}
            message = event.get("message") or {}
            logger.info(
                "长连接收到 im.message.receive_v1 事件: "
                f"event_id={event_id} sender_open_id={sender.get('open_id', '')} "
                f"sender_user_id={sender.get('user_id', '')} chat_id={message.get('chat_id', '')}"
            )
            self.payload_handler(payload)
        except Exception:
            logger.exception("处理长连接消息事件失败")

    def _on_card_action(self, data) -> None:
        try:
            payload = _marshal_to_dict(data)
            event_id = (payload.get("header") or {}).get("event_id", "")
            logger.info(f"长连接收到 card.action.trigger 事件: event_id={event_id}")
            self.payload_handler(payload)
        except Exception:
            logger.exception("处理长连接卡片事件失败")

    def _build_event_handler(self):
        builder = lark.EventDispatcherHandler.builder("", "")
        builder = builder.register_p2_im_message_receive_v1(self._on_message)
        register_card = getattr(builder, "register_p2_card_action_trigger", None)
        if callable(register_card):
            builder = register_card(self._on_card_action)
        else:
            logger.warning(
                "当前 lark-oapi 版本不支持 register_p2_card_action_trigger，"
                "卡片交互事件将不可用（消息事件仍正常）。"
            )
        return builder.build()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("飞书长连接订阅已在运行，跳过重复启动")
            return

        event_handler = self._build_event_handler()
        self._client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        def _run():
            logger.info("飞书长连接客户端启动中…")
            try:
                self._client.start()
            except Exception:
                logger.exception("飞书长连接客户端异常退出")

        self._thread = threading.Thread(target=_run, name="feishu-ws", daemon=True)
        self._thread.start()
