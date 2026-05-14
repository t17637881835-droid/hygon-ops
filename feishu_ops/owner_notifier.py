"""私聊场景：新消息入队后主动推送通知给 Owner"""
from typing import Set

from logger import get_logger

logger = get_logger("owner_notifier")


class OwnerNotifier:
    """
    当新消息入队后，立即向所有 owner 推送通知，包含：
    - 短序号 #N（人类友好，便于指令引用）
    - 紧急标记（关键词命中时高亮）
    - 三种处理指令：转发回复 / 跳过自动回复 / 立即触发自动回复
    """

    def __init__(self, sender, owner_user_ids: Set[str], timeout_minutes: int = 10):
        self.sender = sender
        self.owner_user_ids = owner_user_ids
        self.timeout_minutes = timeout_minutes

    def notify(
        self,
        user_id: str,
        chat_id: str,
        content: str,
        short_id: int,
        urgent: bool = False,
    ) -> None:
        """向所有 owner 发送新消息通知"""
        if not self.owner_user_ids:
            logger.warning("FEISHU_OWNER_USER_IDS 未配置，跳过 owner 通知")
            return

        preview = content[:120] + ("..." if len(content) > 120 else "")
        prefix = "🚨 紧急消息" if urgent else "📩 新消息待处理"
        countdown = (
            "⚠️ 检测到紧急关键词，建议立即处理\n"
            if urgent
            else f"将在 {self.timeout_minutes} 分钟后自动回复。\n"
        )
        text = (
            f"{prefix}  #{short_id}\n"
            f"来自：{user_id}\n"
            f"内容：{preview}\n\n"
            f"{countdown}"
            f"\n指令（在与 bot 的私聊里发）：\n"
            f"  /reply {short_id} 你的答案    → 用 bot 名义转发给用户\n"
            f"  /skip {short_id}              → 跳过，不自动回复\n"
            f"  /auto {short_id}              → 立即自动回复（不等超时）"
        )

        for owner_id in self.owner_user_ids:
            ok = self.sender.send_text(text, chat_id=owner_id, receive_id_type="open_id")
            if ok:
                logger.info(f"已通知 owner: {owner_id} | #{short_id} | urgent={urgent}")
            else:
                logger.warning(f"通知 owner 失败: {owner_id}")

    def confirm(self, owner_id: str, text: str) -> None:
        """向 owner 回执指令执行结果（如 /reply 成功后）"""
        try:
            self.sender.send_text(text, chat_id=owner_id, receive_id_type="open_id")
        except Exception as e:
            logger.warning(f"向 owner {owner_id} 回执失败: {e}")
