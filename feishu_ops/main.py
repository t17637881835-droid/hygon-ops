"""FastAPI 主入口"""
from fastapi import FastAPI, Request, HTTPException
from starlette.responses import JSONResponse
import uvicorn
import asyncio
import json
import ipaddress
import os
import shlex
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Tuple

from config import get_config
from logger import get_logger
from message_queue import MessageQueue
from node_probe import LocalNodeProbe, format_probe_result
from skill_invoker import SkillInvoker
from knowledge_search import KnowledgeSearchService
from kb_admin import KBAdminService
from feishu_sender import FeishuSender
from feishu_verifier import verify_request, verify_verification_token
from feishu_event_parser import parse_feishu_event
from feishu_long_connection import FeishuLongConnectionSubscriber
from audit_logger import AuditLogger
from jump_host_executor import JumpHostSshExecutor
from owner_notifier import OwnerNotifier
from resource_approval import (
    format_missing_fields_prompt,
    format_owner_request_notification,
    format_phase1_grant_advice,
    format_user_request_received,
    parse_resource_owner_command,
)
from resource_config import load_resource_pools
from resource_pool import match_resource_pool
from resource_priority import score_resource_request
from resource_prometheus import PrometheusResourceClient
from resource_request_parser import is_resource_request, parse_resource_request
from resource_request_store import ResourceRequestStore
from sshuser_executor import FakeSshuserExecutor
from sshuser_grant_service import SshuserGrantService
from sshuser_safety import validate_linux_username

logger = get_logger("main")


def _csv_to_set(value: str) -> set:
    return {item.strip() for item in value.split(",") if item.strip()}


def _run_sshuser_command(node_ip: str, oa_prefix: str, operation: str):
    if operation not in {"add", "del"}:
        raise ValueError(f"unsupported sshuser operation: {operation}")
    safe_node = str(ipaddress.ip_address((node_ip or "").strip()))
    safe_user = validate_linux_username(oa_prefix)
    sshuser_path = config.resource_request.sshuser_command_path or "/public/bin/sshuser"
    sudo_password = os.getenv("SSHUSER_SUDO_PASSWORD", "")
    sudo_args = ["sudo", "-S"] if sudo_password else ["sudo", "-n"]
    remote_command = " ".join(shlex.quote(item) for item in [*sudo_args, sshuser_path, operation, safe_user])
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={config.resource_request.sshuser_connect_timeout_seconds}",
        safe_node,
        remote_command,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        input=(sudo_password + "\n") if sudo_password else None,
        timeout=config.resource_request.sshuser_command_timeout_seconds,
        check=False,
    )


def _run_sshuser_add(node_ip: str, oa_prefix: str):
    return _run_sshuser_command(node_ip, oa_prefix, "add")


def _run_sshuser_del(node_ip: str, oa_prefix: str):
    return _run_sshuser_command(node_ip, oa_prefix, "del")



def _should_auto_approve(nodes_list: str, usage_hours: float) -> Tuple[bool, str]:
    nodes = [n.strip() for n in nodes_list.split(",") if n.strip()]
    node_count = len(nodes)
    if node_count > config.resource_request.auto_approve_max_nodes:
        return False, f"申请节点数({node_count})超过自动批准上限({config.resource_request.auto_approve_max_nodes})"
    if usage_hours > config.resource_request.auto_approve_max_hours:
        return False, f"申请时长({usage_hours}h)超过自动批准上限({config.resource_request.auto_approve_max_hours}h)"
    return True, ""



def _send_approval_notification(grant_id: int, oa_prefix: str, nodes_list: str, usage_hours: float, request_reason: str, auto_approve_reason: str) -> None:
    duration_text = f"{usage_hours}h"
    card_content = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": "资源申请审批", "tag": "plain_text"}
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**OA前缀**：{oa_prefix}\n**节点列表**：{nodes_list}\n**申请时长**：{duration_text}\n**申请理由**：{request_reason or '无'}\n**审批原因**：{auto_approve_reason}"
                    }
                },
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"content": "批准", "tag": "plain_text"},
                                    "type": "primary",
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {"action": "approve_grant", "grant_id": grant_id}
                                        }
                                    ]
                                }
                            ]
                        },
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"content": "拒绝", "tag": "plain_text"},
                                    "type": "danger",
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {"action": "reject_grant", "grant_id": grant_id}
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    }
    sent_count = 0
    failed_count = 0
    for owner_id in _csv_to_set(config.feishu.owner_user_ids):
        if feishu_sender.send_card(card_content, chat_id=owner_id, receive_id_type="open_id"):
            sent_count += 1
        else:
            failed_count += 1
    logger.info(f"审批通知发送完成: grant_id={grant_id}, oa_prefix={oa_prefix}, sent={sent_count}, failed={failed_count}")


def _short_error_message(text: str) -> str:
    message = (text or "").strip()
    if not message:
        return "未知错误"
    return message[:300]


def _format_duration_hours(usage_hours: float, usage_hours_text: str) -> str:
    total_seconds = int(round(usage_hours * 3600))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours and minutes and seconds:
        readable = f"{hours}h{minutes}min{seconds}s"
    elif hours and minutes:
        readable = f"{hours}h{minutes}min"
    elif hours and seconds:
        readable = f"{hours}h{seconds}s"
    elif hours:
        readable = f"{hours}h"
    elif minutes and seconds:
        readable = f"{minutes}min{seconds}s"
    elif minutes:
        readable = f"{minutes}min"
    else:
        readable = f"{seconds}s"
    return f"{usage_hours_text}h（{readable}）"


def _card_grant_db_path() -> Path:
    return Path(config.resource_request.db_path)


def _init_card_grant_store() -> None:
    db_file = _card_grant_db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS card_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                oa_prefix TEXT NOT NULL,
                nodes_list TEXT NOT NULL,
                usage_hours REAL NOT NULL,
                start_at TEXT NOT NULL,
                expire_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                approval_status TEXT NOT NULL DEFAULT 'auto_approved',
                feishu_user_id TEXT NOT NULL DEFAULT '',
                feishu_chat_id TEXT NOT NULL DEFAULT '',
                request_reason TEXT,
                approval_user_id TEXT,
                approval_at TEXT,
                approval_reason TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                last_error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_grants_due ON card_grants(status, expire_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_grants_approval ON card_grants(approval_status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_grants_user ON card_grants(oa_prefix, status)")
        conn.commit()
    finally:
        conn.close()


def _record_card_grant(oa_prefix: str, nodes_list: str, usage_hours: float, start_at: str, expire_at: str, feishu_user_id: str, feishu_chat_id: str, approval_status: str, request_reason: str = "") -> int:
    db_file = _card_grant_db_path()
    conn = sqlite3.connect(str(db_file))
    try:
        cursor = conn.execute(
            """
            INSERT INTO card_grants (
                oa_prefix, nodes_list, usage_hours, start_at, expire_at,
                status, approval_status, feishu_user_id, feishu_chat_id, request_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?, ?)
            """,
            (oa_prefix, nodes_list, usage_hours, start_at, expire_at, approval_status, feishu_user_id or "", feishu_chat_id or "", request_reason, start_at),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _list_due_card_grants(now_iso: str):
    conn = sqlite3.connect(str(_card_grant_db_path()))
    try:
        return conn.execute(
            """
            SELECT id, oa_prefix, nodes_list, expire_at, feishu_chat_id
            FROM card_grants
            WHERE status = 'active' AND expire_at <= ?
            ORDER BY expire_at
            """,
            (now_iso,),
        ).fetchall()
    finally:
        conn.close()


def _claim_card_grant_revoke(grant_id: int) -> bool:
    conn = sqlite3.connect(str(_card_grant_db_path()))
    try:
        cursor = conn.execute(
            "UPDATE card_grants SET status = 'revoking' WHERE id = ? AND status = 'active'",
            (grant_id,),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def _has_other_active_card_grant(oa_prefix: str, nodes_list: str, grant_id: int, now_iso: str) -> bool:
    conn = sqlite3.connect(str(_card_grant_db_path()))
    try:
        nodes = [n.strip() for n in nodes_list.split(",") if n.strip()]
        for node_ip in nodes:
            row = conn.execute(
                """
                SELECT 1
                FROM card_grants
                WHERE oa_prefix = ? AND nodes_list LIKE ? AND id != ?
                  AND status = 'active' AND expire_at > ?
                LIMIT 1
                """,
                (oa_prefix, f"%{node_ip}%", grant_id, now_iso),
            ).fetchone()
            if row:
                return True
        return False
    finally:
        conn.close()


def _finish_card_grant_revoke(grant_id: int, status: str, revoked_at: str, last_error: str = "") -> None:
    conn = sqlite3.connect(str(_card_grant_db_path()))
    try:
        conn.execute(
            "UPDATE card_grants SET status = ?, revoked_at = ?, last_error = ? WHERE id = ?",
            (status, revoked_at, last_error, grant_id),
        )
        conn.commit()
    finally:
        conn.close()


def _revoke_due_card_grants() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    for grant_id, oa_prefix, nodes_list, expire_at, feishu_chat_id in _list_due_card_grants(now_iso):
        if not _claim_card_grant_revoke(grant_id):
            continue
        if _has_other_active_card_grant(oa_prefix, nodes_list, grant_id, now_iso):
            _finish_card_grant_revoke(grant_id, "revoked", now_iso, "skipped: covered_by_active_grant")
            logger.info(f"授权到期但存在其他有效授权，跳过撤权: id={grant_id}, nodes={nodes_list}, user={oa_prefix}")
            continue
        nodes = [n.strip() for n in nodes_list.split(",") if n.strip()]
        failed_nodes = []
        for node_ip in nodes:
            try:
                result = _run_sshuser_del(node_ip, oa_prefix)
                if result.returncode != 0:
                    failed_nodes.append(f"{node_ip}({_short_error_message(result.stderr or result.stdout)})")
            except subprocess.TimeoutExpired:
                failed_nodes.append(f"{node_ip}(执行超时)")
            except Exception as exc:
                failed_nodes.append(f"{node_ip}({_short_error_message(str(exc))})")
        if failed_nodes:
            _finish_card_grant_revoke(grant_id, "revoke_failed", now_iso, ', '.join(failed_nodes))
            logger.warning(f"部分节点撤权失败: id={grant_id}, failed_nodes={failed_nodes}")
        else:
            _finish_card_grant_revoke(grant_id, "revoked", now_iso, "")
            logger.info(f"所有节点撤权成功: id={grant_id}, nodes={nodes_list}, user={oa_prefix}")
            if feishu_chat_id:
                feishu_sender.send_text(f"资源申请已到期，已自动回收 {oa_prefix} 在节点 {nodes_list} 的登录权限。", chat_id=feishu_chat_id)


_card_grant_reaper_task = None


async def _card_grant_reaper_loop():
    while True:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _revoke_due_card_grants)
        except Exception as exc:
            logger.warning(f"授权到期回收任务异常: {exc}")
        await asyncio.sleep(30)


app = FastAPI()
config = get_config()
_init_card_grant_store()
message_queue = MessageQueue(
    timeout_seconds=config.skill.timeout_minutes * 60,
    db_path=config.skill.message_queue_db_path,
)
knowledge_search = KnowledgeSearchService(config.skill.knowledge_base_path)
skill_invoker = SkillInvoker(knowledge_search=knowledge_search)
feishu_sender = FeishuSender(
    config.feishu.webhook_url,
    app_id=config.feishu.app_id,
    app_secret=config.feishu.app_secret,
)
audit_logger = AuditLogger(config.skill.audit_log_path)
kb_admin = KBAdminService(knowledge_search, audit_logger=audit_logger)
owner_notifier = OwnerNotifier(
    feishu_sender,
    owner_user_ids=_csv_to_set(config.feishu.owner_user_ids),
    timeout_minutes=config.skill.timeout_minutes,
)
local_node_probe = LocalNodeProbe()
resource_pools_config = None
resource_request_store = None
resource_prometheus_client = None
sshuser_executor = None
sshuser_grant_service = None
if config.resource_request.enabled:
    resource_pools_config = load_resource_pools(config.resource_request.pools_config_path)
    resource_request_store = ResourceRequestStore(config.resource_request.db_path)
    resource_prometheus_client = PrometheusResourceClient(
        config.resource_request.prometheus_url,
        timeout_seconds=config.resource_request.prometheus_timeout_seconds,
    )
    if config.resource_request.sshuser_grant_enabled and config.resource_request.sshuser_remote_exec_enabled:
        all_nodes = set()
        for pool in resource_pools_config.pools:
            all_nodes.update(pool.nodes)
        if config.resource_request.sshuser_executor_type == "fake":
            sshuser_executor = FakeSshuserExecutor()
        else:
            sshuser_executor = JumpHostSshExecutor(
                jump_host=config.resource_request.sshuser_jump_host,
                jump_port=config.resource_request.sshuser_jump_port,
                jump_user=config.resource_request.sshuser_jump_user,
                ssh_key_path=config.resource_request.sshuser_ssh_key_path,
                known_hosts_path=config.resource_request.sshuser_known_hosts_path,
                target_user=config.resource_request.sshuser_target_user,
                target_port=config.resource_request.sshuser_target_ssh_port,
                connect_timeout_seconds=config.resource_request.sshuser_connect_timeout_seconds,
                command_timeout_seconds=config.resource_request.sshuser_command_timeout_seconds,
            )
        sshuser_grant_service = SshuserGrantService(
            store=resource_request_store,
            executor=sshuser_executor,
            allowed_nodes=all_nodes,
            configured_sshuser_path=config.resource_request.sshuser_command_path,
            audit_logger=audit_logger,
        )

logger.info("服务启动完成")

@app.on_event("startup")
async def start_card_grant_reaper():
    global _card_grant_reaper_task
    if _card_grant_reaper_task is None or _card_grant_reaper_task.done():
        _card_grant_reaper_task = asyncio.create_task(_card_grant_reaper_loop())
        logger.info("授权到期回收任务已启动")


_feishu_long_connection_subscriber = None


@app.on_event("startup")
async def start_feishu_long_connection():
    """按需启动飞书长连接订阅，避免依赖公网 webhook。"""
    global _feishu_long_connection_subscriber
    if not config.feishu.use_long_connection:
        logger.info("FEISHU_USE_LONG_CONNECTION=false，跳过长连接订阅")
        return
    if not (config.feishu.app_id and config.feishu.app_secret):
        logger.warning("启用长连接需配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET，已跳过")
        return
    if _feishu_long_connection_subscriber is not None:
        return
    try:
        _feishu_long_connection_subscriber = FeishuLongConnectionSubscriber(
            app_id=config.feishu.app_id,
            app_secret=config.feishu.app_secret,
            payload_handler=_handle_feishu_payload,
        )
        _feishu_long_connection_subscriber.start()
        logger.info("飞书长连接订阅已启动")
    except Exception:
        logger.exception("启动飞书长连接订阅失败")


@app.on_event("shutdown")
async def stop_card_grant_reaper():
    global _card_grant_reaper_task
    if _card_grant_reaper_task is not None:
        _card_grant_reaper_task.cancel()
        _card_grant_reaper_task = None



@app.get("/webhook")
async def webhook_get():
    return {"status": "ok", "message": "webhook expects POST"}


@app.post("/webhook")
async def webhook(request: Request):
    """接收飞书消息（带签名验证）"""
    body = await request.body()
    debug_headers = {
        "content-type": request.headers.get("content-type", ""),
        "user-agent": request.headers.get("user-agent", ""),
        "x-lark-request-nonce": request.headers.get("x-lark-request-nonce", ""),
        "x-lark-signature": request.headers.get("x-lark-signature", ""),
        "x-lark-timestamp": request.headers.get("x-lark-timestamp", ""),
    }
    logger.info(f"webhook raw request: headers={debug_headers} body={body.decode('utf-8', errors='replace')}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning("webhook invalid json: %s", exc)
        raise HTTPException(status_code=400, detail="invalid json")
    challenge = payload.get("challenge") or payload.get("CHALLENGE")
    if challenge:
        logger.info("收到飞书 URL challenge 验证请求")
        response_payload = {"challenge": str(challenge)}
        logger.info(f"webhook challenge response: {response_payload}")
        return JSONResponse(content=response_payload)

    if not await verify_request(request, config):
        logger.warning("签名验证失败")
        raise HTTPException(status_code=401, detail="签名验证失败")

    if not verify_verification_token(config.feishu.verification_token, payload):
        logger.warning("飞书 verification token 验证失败")
        raise HTTPException(status_code=401, detail="verification token 验证失败")

    return _handle_feishu_payload(payload)


def _handle_feishu_payload(payload: dict) -> dict:
    """解析并派发飞书事件，被 webhook 与长连接共享。"""
    parsed = parse_feishu_event(
        payload,
        owner_user_ids=_csv_to_set(config.feishu.owner_user_ids),
        bot_user_ids=_csv_to_set(config.feishu.bot_user_ids),
    )
    return _dispatch_parsed_event(parsed)


def _dispatch_parsed_event(parsed) -> dict:
    """同步派发已解析的事件。"""
    if parsed.action == "challenge":
        return {"challenge": parsed.content}

    if parsed.action == "cancel":
        cancelled = message_queue.cancel_by_chat(parsed.chat_id, parsed.content)
        logger.info(f"人工回复取消待处理消息: chat={parsed.chat_id} | cancelled={cancelled}")
        return {"status": "cancelled", "cancelled": cancelled}

    if parsed.action == "forward":
        # owner 通过 /reply N 内容 转发：以 bot 名义发到用户的 chat
        msg = message_queue.get_by_short_id(parsed.short_id)
        if not msg:
            owner_notifier.confirm(parsed.user_id, f"⚠️ 未找到 #{parsed.short_id}，可能已处理或不存在")
            return {"status": "not_found", "short_id": parsed.short_id}
        sent = feishu_sender.send_text(parsed.content, chat_id=msg.chat_id)
        if sent:
            message_queue.mark_replied(msg.message_id, parsed.content)
            message_queue.remove(msg.message_id)
            audit_logger.record(
                event="owner_forward",
                short_id=parsed.short_id,
                message_id=msg.message_id,
                user_id=msg.user_id,
                chat_id=msg.chat_id,
                question=msg.content,
                response=parsed.content,
                owner_id=parsed.user_id,
            )
            owner_notifier.confirm(parsed.user_id, f"✅ 已转发 #{parsed.short_id} 给 {msg.user_id}")
        else:
            owner_notifier.confirm(parsed.user_id, f"❌ 转发 #{parsed.short_id} 失败，已加入重试队列")
        return {"status": "forwarded" if sent else "send_failed", "short_id": parsed.short_id}

    if parsed.action == "cancel_by_short_id":
        msg = message_queue.get_by_short_id(parsed.short_id)
        if not msg:
            owner_notifier.confirm(parsed.user_id, f"⚠️ 未找到 #{parsed.short_id}")
            return {"status": "not_found", "short_id": parsed.short_id}
        message_queue.remove(msg.message_id)
        audit_logger.record(
            event="owner_skip",
            short_id=parsed.short_id,
            message_id=msg.message_id,
            user_id=msg.user_id,
            chat_id=msg.chat_id,
            owner_id=parsed.user_id,
        )
        owner_notifier.confirm(parsed.user_id, f"✅ 已跳过 #{parsed.short_id}（不会自动回复）")
        return {"status": "skipped", "short_id": parsed.short_id}

    if parsed.action == "trigger_auto":
        msg = message_queue.force_timeout(parsed.short_id)
        if not msg:
            owner_notifier.confirm(parsed.user_id, f"⚠️ 未找到 #{parsed.short_id}")
            return {"status": "not_found", "short_id": parsed.short_id}
        owner_notifier.confirm(parsed.user_id, f"✅ #{parsed.short_id} 将在下一轮检查时立即自动回复")
        return {"status": "scheduled_auto", "short_id": parsed.short_id}

    if parsed.action == "resource_owner_command":
        return _handle_resource_owner_command(parsed)

    if parsed.action == "ping":
        feishu_sender.send_text("pong", chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "pong"}

    if parsed.action == "apply_card":
        return _handle_apply_card(parsed)

    if parsed.action == "card_interaction":
        return _handle_card_interaction(parsed)

    if parsed.action == "node_owner_command":
        return _handle_node_owner_command(parsed)

    if parsed.action == "kb_owner_command":
        return _handle_kb_owner_command(parsed)

    if parsed.action == "ignore":
        logger.info(f"忽略飞书事件: reason={parsed.reason} | event_type={parsed.event_type}")
        return {"status": "ignored", "reason": parsed.reason}

    logger.info(
        f"收到消息: {parsed.message_id} | user={parsed.user_id} | "
        f"urgent={parsed.urgent} | content={parsed.content[:50]}..."
    )

    if config.resource_request.enabled and is_resource_request(parsed.content):
        return _handle_resource_apply(parsed)

    msg = message_queue.add(
        message_id=parsed.message_id,
        user_id=parsed.user_id,
        chat_id=parsed.chat_id,
        content=parsed.content
    )
    owner_notifier.notify(
        parsed.user_id, parsed.chat_id, parsed.content,
        short_id=msg.short_id, urgent=parsed.urgent,
    )
    return {"status": "ok", "short_id": msg.short_id}


def _resource_workflow_ready() -> bool:
    return bool(
        config.resource_request.enabled
        and resource_pools_config
        and resource_request_store
        and resource_prometheus_client
    )


def _handle_node_owner_command(parsed):
    parts = parsed.content.strip().split()
    # 跳过 @ 开头的 token（如 @_user_1）
    parts = [p for p in parts if not p.startswith("@")]
    # 找到 /node 所在的位置
    node_idx = next((i for i, p in enumerate(parts) if p.lower().startswith("/node")), -1)
    if node_idx == -1:
        feishu_sender.send_text("不支持的节点命令，请使用 /node ping local 或 /node status local", chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "invalid_node_command"}
    action = parts[node_idx + 1].lower() if len(parts) > node_idx + 1 else ""
    node = parts[node_idx + 2] if len(parts) > node_idx + 2 else "local"
    if action == "ping":
        result = local_node_probe.ping(node)
    elif action == "status":
        result = local_node_probe.status(node)
    else:
        feishu_sender.send_text("不支持的节点命令，请使用 /node ping local 或 /node status local", chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "invalid_node_command"}
    feishu_sender.send_text(format_probe_result(result), chat_id=parsed.user_id, receive_id_type="open_id")
    audit_logger.record(event="node_owner_command", owner_id=parsed.user_id, action=action, node=node, success=result.success)
    return {"status": "node_command_done", "action": action, "node": node, "success": result.success}


def _handle_kb_owner_command(parsed):
    """owner 私聊 /kb 命令：知识库运营接口（reload/stats/search/show/add/del）。"""
    try:
        reply = kb_admin.handle(parsed.content)
    except Exception as e:
        logger.exception("/kb 命令处理异常")
        reply = f"❌ /kb 处理异常：{e}"
    feishu_sender.send_text(reply, chat_id=parsed.user_id, receive_id_type="open_id")
    audit_logger.record(event="kb_owner_command", owner_id=parsed.user_id, content=parsed.content[:200])
    return {"status": "kb_command_done"}


def _handle_apply_card(parsed):
    """发送资源申请卡片"""
    node_info_link = config.resource_request.node_info_link or "https://kcnm6g5dkw5p.feishu.cn/wiki/CSxLwa8c4iI89Zk9PTvcZyvVnsh?from=from_copylink"
    card_content = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "title": {
                "content": "资源申请",
                "tag": "plain_text"
            }
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "请填写以下信息申请计算节点资源："
                    }
                },
                {
                    "tag": "form",
                    "name": "resource_apply_form",
                    "elements": [
                        {
                            "tag": "input",
                            "name": "oa_prefix",
                            "label": {
                                "tag": "plain_text",
                                "content": "申请人OA前缀"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "例如：lvjj1"
                            },
                            "required": True
                        },
                        {
                            "tag": "input",
                            "name": "nodes_list",
                            "label": {
                                "tag": "plain_text",
                                "content": "计算节点IP列表"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "多个IP用逗号分隔，例如：10.16.1.93,10.16.1.94"
                            },
                            "required": True
                        },
                        {
                            "tag": "input",
                            "name": "usage_time",
                            "label": {
                                "tag": "plain_text",
                                "content": "使用时长(h)"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "例如：1、8、24"
                            },
                            "required": True
                        },
                        {
                            "tag": "input",
                            "name": "request_reason",
                            "label": {
                                "tag": "plain_text",
                                "content": "申请理由"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "说明使用场景和需求"
                            },
                            "required": False
                        },
                        {
                            "tag": "column_set",
                            "columns": [
                                {
                                    "tag": "column",
                                    "elements": [
                                        {
                                            "tag": "button",
                                            "text": {
                                                "tag": "plain_text",
                                                "content": "查看可申请节点列表"
                                            },
                                            "type": "default",
                                            "behaviors": [
                                                {
                                                    "type": "open_url",
                                                    "default_url": node_info_link
                                                }
                                            ]
                                        }
                                    ]
                                },
                                {
                                    "tag": "column",
                                    "elements": [
                                        {
                                            "tag": "button",
                                            "text": {
                                                "tag": "plain_text",
                                                "content": "提交申请"
                                            },
                                            "type": "primary",
                                            "form_action_type": "submit",
                                            "name": "submit_button",
                                            "behaviors": [
                                                {
                                                    "type": "callback",
                                                    "value": {
                                                        "action": "submit_form"
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    }
    sent = feishu_sender.send_card(card_content, chat_id=parsed.chat_id, receive_id_type="chat_id")
    if sent:
        logger.info(f"资源申请卡片发送成功: chat_id={parsed.chat_id}")
        audit_logger.record(event="apply_card_sent", user_id=parsed.user_id, chat_id=parsed.chat_id)
        return {"status": "card_sent"}
    else:
        logger.warning(f"资源申请卡片发送失败: chat_id={parsed.chat_id}")
        return {"status": "card_send_failed"}


def _handle_card_interaction(parsed):
    """处理卡片交互事件"""
    try:
        action_value = json.loads(parsed.content) if parsed.content else {}
    except Exception:
        action_value = {}

    logger.info(f"收到卡片交互事件: action_value={action_value}, token={parsed.token}")

    # 检查是否是表单提交（包含表单字段）
    if "oa_prefix" in action_value and "nodes_list" in action_value and "usage_time" in action_value:
        # 打印接收到的表单数据
        logger.info(f"收到资源申请表单提交: OA前缀={action_value.get('oa_prefix')}, 节点列表={action_value.get('nodes_list')}, 使用时长={action_value.get('usage_time')}, 申请理由={action_value.get('request_reason')}")
        oa_prefix = (action_value.get("oa_prefix") or "").strip()
        nodes_list = (action_value.get("nodes_list") or "").strip()
        request_reason = (action_value.get("request_reason") or "").strip()
        usage_hours = None
        usage_hours_text = (action_value.get("usage_time") or "").strip()
        start_at_iso = ""
        expire_at_iso = ""

        # 计算到期时间
        try:
            usage_hours = float(usage_hours_text or "0")
            start_at = datetime.now(timezone.utc)
            expire_at = start_at + timedelta(hours=usage_hours)
            start_at_iso = start_at.isoformat()
            expire_at_iso = expire_at.isoformat()
            tz = timezone(timedelta(hours=8))
            start_time = start_at.astimezone(tz)
            expire_time = expire_at.astimezone(tz)
            start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
            expire_str = expire_time.strftime("%Y-%m-%d %H:%M:%S")
            expire_time_str = f"{start_str} 至 {expire_str}"
        except (ValueError, TypeError):
            expire_time_str = "计算失败"
        if usage_hours is not None and usage_hours.is_integer():
            usage_hours_text = str(int(usage_hours))
        elif usage_hours is not None:
            usage_hours_text = str(usage_hours)

        auto_approve, auto_approve_reason = _should_auto_approve(nodes_list, usage_hours) if usage_hours is not None else (False, "时长无效")
        approval_status = "auto_approved" if auto_approve else "pending_approval"
        duration_text = _format_duration_hours(usage_hours, usage_hours_text) if usage_hours is not None else usage_hours_text
        grant_id = 0
        if usage_hours is not None and start_at_iso and expire_at_iso:
            grant_id = _record_card_grant(
                oa_prefix=oa_prefix,
                nodes_list=nodes_list,
                usage_hours=usage_hours,
                start_at=start_at_iso,
                expire_at=expire_at_iso,
                feishu_user_id=parsed.user_id,
                feishu_chat_id=parsed.chat_id,
                approval_status=approval_status,
                request_reason=request_reason,
            )
            logger.info(f"资源申请记录已写入: id={grant_id}, nodes={nodes_list}, user={oa_prefix}, approval_status={approval_status}")

        if auto_approve:
            nodes = [n.strip() for n in nodes_list.split(",") if n.strip()]
            failed_nodes = []
            for node_ip in nodes:
                try:
                    grant_result = _run_sshuser_add(node_ip, oa_prefix)
                    if grant_result.returncode == 0:
                        logger.info(f"sshuser add 执行成功: node={node_ip}, user={oa_prefix}, stdout={grant_result.stdout.strip()}")
                    else:
                        failed_nodes.append(f"{node_ip}({_short_error_message(grant_result.stderr or grant_result.stdout)})")
                        logger.warning(f"sshuser add 执行失败: node={node_ip}, user={oa_prefix}, returncode={grant_result.returncode}, stderr={grant_result.stderr.strip()}, stdout={grant_result.stdout.strip()}")
                except subprocess.TimeoutExpired:
                    failed_nodes.append(f"{node_ip}(执行超时)")
                    logger.warning(f"sshuser add 执行超时: node={node_ip}, user={oa_prefix}")
                except Exception as exc:
                    failed_nodes.append(f"{node_ip}({_short_error_message(str(exc))})")
                    logger.warning(f"sshuser add 执行异常: node={node_ip}, user={oa_prefix}, error={exc}")
            if failed_nodes:
                conn = sqlite3.connect(str(_card_grant_db_path()))
                try:
                    conn.execute("UPDATE card_grants SET status = 'grant_failed', last_error = ? WHERE id = ?", (", ".join(failed_nodes), grant_id))
                    conn.commit()
                finally:
                    conn.close()
                card_content = f"⚠️ 部分节点授权失败\n**失败节点**：{', '.join(failed_nodes)}\n请联系管理员处理。"
                toast_content = "部分授权失败"
            else:
                conn = sqlite3.connect(str(_card_grant_db_path()))
                try:
                    conn.execute("UPDATE card_grants SET status = 'active' WHERE id = ?", (grant_id,))
                    conn.commit()
                finally:
                    conn.close()
                nodes_text = nodes_list if len(nodes) > 1 else nodes[0]
                card_content = f"✅ 已经添加 **{oa_prefix}** 的 **{nodes_text}** 的权限\n**申请时长**：{duration_text}\n**有效时间**：{expire_time_str}\n时间到节点权限会自动收回，请妥善使用。"
                toast_content = "授权成功！"
        else:
            card_content = f"📋 资源申请已提交，等待审批\n**OA前缀**：{oa_prefix}\n**节点列表**：{nodes_list}\n**申请时长**：{duration_text}\n**申请理由**：{request_reason or '无'}\n**审批原因**：{auto_approve_reason}\n审批通过后会自动授权。"
            toast_content = "申请已提交，等待审批"
            _send_approval_notification(grant_id, oa_prefix, nodes_list, usage_hours, request_reason, auto_approve_reason)

        # 构造更新后的卡片
        updated_card = {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "content": "资源申请",
                    "tag": "plain_text"
                }
            },
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": card_content
                        }
                    }
                ]
            }
        }

        # 返回响应包含 toast 和更新后的卡片（参考飞书官方示例）
        response = {
            "toast": {
                "type": "info",
                "content": toast_content,
            },
            "card": {
                "type": "raw",
                "data": updated_card
            }
        }
        logger.info(f"返回卡片交互响应: 包含 toast 和更新后的卡片")
        return response

    # 根据按钮值执行不同操作
    if action_value.get("action") == "open_form":
        # 返回空响应（只包含 token，表示不需要更新卡片）
        response = {"token": parsed.token}
        logger.info(f"返回卡片交互响应: {response}")
        return response
    
    # 审批按钮处理：批准
    if action_value.get("action") == "approve_grant":
        grant_id = action_value.get("grant_id")
        if not grant_id:
            logger.warning("审批请求缺少 grant_id")
            return {"status": "invalid_grant_id"}
        
        # 查询申请记录
        conn = sqlite3.connect(str(_card_grant_db_path()))
        try:
            row = conn.execute(
                "SELECT oa_prefix, nodes_list, usage_hours, start_at, expire_at, approval_status, feishu_user_id, feishu_chat_id, request_reason FROM card_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()
            if not row:
                logger.warning(f"未找到申请记录: grant_id={grant_id}")
                return {"status": "grant_not_found"}
            
            oa_prefix, nodes_list, usage_hours, start_at, expire_at, approval_status, feishu_user_id, feishu_chat_id, request_reason = row
            if approval_status != "pending_approval":
                logger.warning(f"申请状态不是待审批: grant_id={grant_id}, status={approval_status}")
                return {"status": "invalid_approval_status"}
            
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # 执行授权
            nodes = [n.strip() for n in nodes_list.split(",") if n.strip()]
            failed_nodes = []
            for node_ip in nodes:
                try:
                    grant_result = _run_sshuser_add(node_ip, oa_prefix)
                    if grant_result.returncode != 0:
                        failed_nodes.append(f"{node_ip}({_short_error_message(grant_result.stderr or grant_result.stdout)})")
                except subprocess.TimeoutExpired:
                    failed_nodes.append(f"{node_ip}(执行超时)")
                except Exception as exc:
                    failed_nodes.append(f"{node_ip}({_short_error_message(str(exc))})")
            
            if failed_nodes:
                conn.execute(
                    "UPDATE card_grants SET approval_status = 'approved', status = 'revoke_failed', approval_user_id = ?, approval_at = ?, last_error = ? WHERE id = ?",
                    (parsed.user_id, now_iso, ', '.join(failed_nodes), grant_id),
                )
                conn.commit()
                logger.warning(f"审批通过但部分节点授权失败: grant_id={grant_id}, failed_nodes={failed_nodes}")
                card_content = f"⚠️ 审批通过但部分节点授权失败\n**失败节点**：{', '.join(failed_nodes)}\n请联系管理员处理。"
            else:
                conn.execute(
                    "UPDATE card_grants SET approval_status = 'approved', status = 'active', approval_user_id = ?, approval_at = ? WHERE id = ?",
                    (parsed.user_id, now_iso, grant_id),
                )
                conn.commit()
                logger.info(f"审批通过并授权成功: grant_id={grant_id}, nodes={nodes_list}")
                duration_text = _format_duration_hours(usage_hours, str(usage_hours))
                tz = timezone(timedelta(hours=8))
                start_time = datetime.fromisoformat(start_at).astimezone(tz)
                expire_time = datetime.fromisoformat(expire_at).astimezone(tz)
                start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
                expire_str = expire_time.strftime("%Y-%m-%d %H:%M:%S")
                expire_time_str = f"{start_str} 至 {expire_str}"
                card_content = f"✅ 审批通过，已添加 **{oa_prefix}** 的节点权限\n**节点列表**：{nodes_list}\n**申请时长**：{duration_text}\n**有效时间**：{expire_time_str}\n时间到节点权限会自动收回，请妥善使用。"
            
            # 通知申请人
            if feishu_chat_id:
                feishu_sender.send_text(card_content, chat_id=feishu_chat_id)
            
            updated_card = {
                "schema": "2.0",
                "config": {"wide_screen_mode": True},
                "header": {"title": {"content": "资源申请审批", "tag": "plain_text"}},
                "body": {
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": card_content
                            }
                        }
                    ]
                }
            }
            response = {
                "toast": {"type": "info", "content": "审批通过"},
                "card": {"type": "raw", "data": updated_card}
            }
            logger.info(f"审批通过响应: grant_id={grant_id}")
            return response
        
        finally:
            conn.close()
    
    # 拒绝按钮：弹出输入框
    if action_value.get("action") == "reject_grant":
        grant_id = action_value.get("grant_id")
        if not grant_id:
            logger.warning("拒绝请求缺少 grant_id")
            return {"status": "invalid_grant_id"}
        
        # 查询申请记录
        conn = sqlite3.connect(str(_card_grant_db_path()))
        try:
            row = conn.execute(
                "SELECT oa_prefix, nodes_list, usage_hours, request_reason, approval_status FROM card_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()
            if not row:
                logger.warning(f"未找到申请记录: grant_id={grant_id}")
                return {"status": "grant_not_found"}
            
            oa_prefix, nodes_list, usage_hours, request_reason, approval_status = row
            if approval_status != "pending_approval":
                logger.warning(f"申请状态不是待审批: grant_id={grant_id}, status={approval_status}")
                return {"status": "invalid_approval_status"}
        finally:
            conn.close()
        
        # 返回带输入框的卡片
        updated_card = {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {"title": {"content": "拒绝申请", "tag": "plain_text"}},
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**OA前缀**：{oa_prefix}\n**节点列表**：{nodes_list}\n**申请时长**：{usage_hours}h\n**申请理由**：{request_reason or '无'}"
                        }
                    },
                    {
                        "tag": "form",
                        "name": "reject_reason_form",
                        "elements": [
                            {
                                "tag": "input",
                                "name": "reject_reason",
                                "label": {"tag": "plain_text", "content": "拒绝理由"},
                                "placeholder": {"tag": "plain_text", "content": "请输入拒绝原因"}
                            },
                            {
                                "tag": "column_set",
                                "columns": [
                                    {
                                        "tag": "column",
                                        "elements": [
                                            {
                                                "tag": "button",
                                                "text": {"content": "取消", "tag": "plain_text"},
                                                "type": "default",
                                                "behaviors": [
                                                    {"type": "callback", "value": {"action": "cancel_reject", "grant_id": grant_id}}
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        "tag": "column",
                                        "elements": [
                                            {
                                                "tag": "button",
                                                "text": {"content": "确认拒绝", "tag": "plain_text"},
                                                "type": "danger",
                                                "form_action_type": "submit",
                                                "name": "confirm_reject_button",
                                                "behaviors": [
                                                    {"type": "callback", "value": {"action": "confirm_reject", "grant_id": grant_id}}
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        }
        response = {
            "toast": {"type": "info", "content": "请输入拒绝理由"},
            "card": {"type": "raw", "data": updated_card}
        }
        logger.info(f"返回拒绝理由输入框: grant_id={grant_id}")
        return response
    
    # 确认拒绝（带理由）
    if action_value.get("action") == "confirm_reject" and "reject_reason" in action_value:
        grant_id = action_value.get("grant_id")
        reject_reason = (action_value.get("reject_reason") or "").strip()
        if not grant_id:
            logger.warning("拒绝请求缺少 grant_id")
            return {"status": "invalid_grant_id"}
        
        # 查询申请记录
        conn = sqlite3.connect(str(_card_grant_db_path()))
        try:
            row = conn.execute(
                "SELECT oa_prefix, nodes_list, approval_status, feishu_chat_id FROM card_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()
            if not row:
                logger.warning(f"未找到申请记录: grant_id={grant_id}")
                return {"status": "grant_not_found"}
            
            oa_prefix, nodes_list, approval_status, feishu_chat_id = row
            if approval_status != "pending_approval":
                logger.warning(f"申请状态不是待审批: grant_id={grant_id}, status={approval_status}")
                return {"status": "invalid_approval_status"}
            
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE card_grants SET approval_status = 'rejected', status = 'rejected', approval_user_id = ?, approval_at = ?, approval_reason = ? WHERE id = ?",
                (parsed.user_id, now_iso, reject_reason or "未填写拒绝理由", grant_id),
            )
            conn.commit()
            logger.info(f"申请已拒绝: grant_id={grant_id}, reason={reject_reason}")
            
            # 通知申请人
            if feishu_chat_id:
                feishu_sender.send_text(f"❌ 资源申请被拒绝\n**节点列表**：{nodes_list}\n**拒绝原因**：{reject_reason or '未填写'}\n请联系管理员了解详情。", chat_id=feishu_chat_id)
        finally:
            conn.close()
        
        updated_card = {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {"title": {"content": "资源申请审批", "tag": "plain_text"}},
            "body": {
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"❌ 已拒绝申请\n**OA前缀**：{oa_prefix}\n**节点列表**：{nodes_list}\n**拒绝原因**：{reject_reason or '未填写'}"
                        }
                    }
                ]
            }
        }
        response = {
            "toast": {"type": "info", "content": "已拒绝申请"},
            "card": {"type": "raw", "data": updated_card}
        }
        logger.info(f"拒绝完成响应: grant_id={grant_id}")
        return response
    
    # 取消拒绝
    if action_value.get("action") == "cancel_reject":
        grant_id = action_value.get("grant_id")
        if not grant_id:
            logger.warning("取消请求缺少 grant_id")
            return {"status": "invalid_grant_id"}
        
        # 重新发送审批卡片
        conn = sqlite3.connect(str(_card_grant_db_path()))
        try:
            row = conn.execute(
                "SELECT oa_prefix, nodes_list, usage_hours, request_reason, approval_status FROM card_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()
            if not row:
                logger.warning(f"未找到申请记录: grant_id={grant_id}")
                return {"status": "grant_not_found"}
            
            oa_prefix, nodes_list, usage_hours, request_reason, approval_status = row
            if approval_status != "pending_approval":
                logger.warning(f"申请状态不是待审批: grant_id={grant_id}, status={approval_status}")
                return {"status": "invalid_approval_status"}
        finally:
            conn.close()
        
        reason = f"申请时长({usage_hours}h)超过自动批准上限(24h)" if usage_hours > 24 else f"申请节点数超过自动批准上限(1)"
        _send_approval_notification(grant_id, oa_prefix, nodes_list, usage_hours, request_reason or "", reason)
        
        response = {"token": parsed.token}
        logger.info(f"取消拒绝，重新发送审批卡片: grant_id={grant_id}")
        return response

    return {"status": "unknown_action"}


def _handle_resource_apply(parsed):
    if not _resource_workflow_ready():
        feishu_sender.send_text("资源申请功能暂不可用，请稍后联系运维。", chat_id=parsed.chat_id)
        return {"status": "resource_request_unavailable"}

    parse_result = parse_resource_request(parsed.content)
    if not parse_result.valid:
        feishu_sender.send_text(format_missing_fields_prompt(parse_result.missing_fields), chat_id=parsed.chat_id)
        return {"status": "resource_request_missing_fields", "missing_fields": parse_result.missing_fields}

    request_data = parse_result.request
    pool = match_resource_pool(resource_pools_config, request_data.resource_type, request_data.resource_amount)
    if not pool:
        feishu_sender.send_text("暂未找到匹配的资源池，请补充资源类型或联系运维确认。", chat_id=parsed.chat_id)
        return {"status": "resource_pool_not_found"}

    pool_status = resource_prometheus_client.get_pool_status(pool)
    pool_can_satisfy = pool_status.free_devices is None or pool_status.free_devices >= request_data.resource_amount
    pool_is_tight = pool_status.free_devices is not None and pool_status.free_devices < pool.min_free_devices_for_auto_suggest
    priority = score_resource_request(
        urgency=request_data.urgency,
        deadline=request_data.deadline,
        reason=request_data.reason,
        pool_can_satisfy=pool_can_satisfy,
        pool_is_tight=pool_is_tight,
        accept_queue=request_data.accept_queue,
        accept_downgrade=request_data.accept_downgrade,
    )
    record = resource_request_store.create_request(
        feishu_user_id=parsed.user_id,
        linux_username=request_data.linux_username,
        project_name=request_data.project_name,
        resource_type=request_data.resource_type,
        resource_amount=request_data.resource_amount,
        duration_hours=request_data.duration_hours,
        urgency=request_data.urgency,
        deadline=request_data.deadline,
        reason=request_data.reason,
        accept_queue=request_data.accept_queue,
        accept_downgrade=request_data.accept_downgrade,
        matched_pool_id=pool.pool_id,
        priority_score=priority.score,
        priority_reasons=priority.reasons,
    )
    feishu_sender.send_text(
        format_user_request_received(record.request_code, record.matched_pool_id, record.priority_score),
        chat_id=parsed.chat_id,
    )
    _send_resource_owner_message(
        format_owner_request_notification(record, pool_name=pool.name, free_devices=pool_status.free_devices)
    )
    audit_logger.record(
        event="resource_request_created",
        request_code=record.request_code,
        user_id=parsed.user_id,
        linux_username=record.linux_username,
        pool_id=record.matched_pool_id,
        priority_score=record.priority_score,
    )
    return {"status": "resource_request_created", "request_code": record.request_code}


def _handle_resource_owner_command(parsed):
    if not _resource_workflow_ready():
        owner_notifier.confirm(parsed.user_id, "⚠️ 资源申请功能未启用")
        return {"status": "resource_request_unavailable"}

    command = parse_resource_owner_command(parsed.content)
    if not command:
        owner_notifier.confirm(parsed.user_id, "⚠️ 资源审批命令格式不正确")
        return {"status": "invalid_resource_owner_command"}

    if command.action == "grant" and command.confirm:
        if not (config.resource_request.sshuser_grant_enabled and config.resource_request.sshuser_remote_exec_enabled and sshuser_grant_service):
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前为 advice-only 模式，请使用 /approve 生成 sshuser 授权建议")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.confirm_grant(command.grant_code, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        if result.requester_message:
            grant = resource_request_store.get_grant(command.grant_code)
            request = resource_request_store.get_request(grant.request_code) if grant else None
            if request:
                feishu_sender.send_text(result.requester_message, chat_id=request.feishu_user_id, receive_id_type="open_id")
        audit_logger.record(event="resource_grant_confirmed", grant_code=command.grant_code, owner_id=parsed.user_id, status=result.status)
        return {"status": "resource_grant_confirmed", "grant_code": command.grant_code, "grant_status": result.status}

    if command.action == "grant" and command.operation == "retry":
        if not sshuser_grant_service:
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前未启用 sshuser 远程执行")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.retry_grant(command.grant_code, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "resource_grant_retry", "grant_code": command.grant_code, "grant_status": result.status}

    if command.action == "revoke" and command.operation == "retry":
        if not sshuser_grant_service:
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前未启用 sshuser 远程执行")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.retry_revoke(command.grant_code, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "resource_revoke_retry", "grant_code": command.grant_code, "grant_status": result.status}

    if command.action == "revoke" and command.operation == "mark-done":
        if not sshuser_grant_service:
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前未启用 sshuser 远程执行")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.mark_revoke_done(command.grant_code, command.nodes, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        audit_logger.record(event="resource_revoke_manual_mark_done", grant_code=command.grant_code, owner_id=parsed.user_id, nodes=command.nodes, status=result.status)
        return {"status": "resource_revoke_mark_done", "grant_code": command.grant_code, "grant_status": result.status}

    record = resource_request_store.get_request(command.request_code)
    if not record:
        owner_notifier.confirm(parsed.user_id, f"⚠️ 未找到申请 #{command.request_code}")
        return {"status": "not_found", "request_code": command.request_code}

    if command.action == "reject":
        resource_request_store.reject_request(record.request_code, rejected_by=parsed.user_id, reason=command.reason)
        feishu_sender.send_text(f"申请 #{record.request_code} 已驳回：{command.reason or '未填写原因'}", chat_id=parsed.user_id, receive_id_type="open_id")
        feishu_sender.send_text(f"你的资源申请 #{record.request_code} 已驳回：{command.reason or '未填写原因'}", chat_id=record.feishu_user_id, receive_id_type="open_id")
        audit_logger.record(event="resource_request_rejected", request_code=record.request_code, owner_id=parsed.user_id, reason=command.reason)
        return {"status": "resource_rejected", "request_code": record.request_code}

    if command.action == "approve":
        duration_hours = command.duration_hours or record.duration_hours
        resource_request_store.approve_request(record.request_code, approved_by=parsed.user_id, duration_hours=duration_hours)
        pool = resource_pools_config.get_pool(record.matched_pool_id)
        grant = resource_request_store.create_grant_plan(
            request_code=record.request_code,
            linux_username=record.linux_username,
            pool_id=pool.pool_id,
            target_nodes=pool.nodes,
            sshuser_path=pool.sshuser_path,
            duration_hours=duration_hours,
            planned_by=parsed.user_id,
        )
        advice = format_phase1_grant_advice(
            request_code=record.request_code,
            linux_username=record.linux_username,
            pool_id=pool.pool_id,
            target_nodes=pool.nodes,
            sshuser_path=pool.sshuser_path,
            duration_hours=duration_hours,
        )
        feishu_sender.send_text(advice, chat_id=parsed.user_id, receive_id_type="open_id")
        feishu_sender.send_text(advice, chat_id=record.feishu_user_id, receive_id_type="open_id")
        audit_logger.record(
            event="resource_request_approved",
            request_code=record.request_code,
            grant_code=grant.grant_code,
            owner_id=parsed.user_id,
            target_nodes=pool.nodes,
        )
        return {"status": "resource_approved", "request_code": record.request_code, "grant_code": grant.grant_code}

    owner_notifier.confirm(parsed.user_id, "⚠️ Phase 1 不执行真实节点授权，请使用 /approve 生成 sshuser 授权建议")
    return {"status": "sshuser_grant_disabled"}


def _send_resource_owner_message(content: str) -> None:
    for owner_id in _csv_to_set(config.feishu.owner_user_ids):
        feishu_sender.send_text(content, chat_id=owner_id, receive_id_type="open_id")


@app.get("/health")
async def health():
    """健康检查（完善版）"""
    return {
        "status": "healthy",
        "queue_size": message_queue.size(),
        "anthropic_configured": bool(config.anthropic.api_key),
        "resource_request": _resource_health(),
    }


def _resource_health() -> dict:
    pools_loaded = len(resource_pools_config.pools) if resource_pools_config else 0
    return {
        "enabled": bool(config.resource_request.enabled),
        "ready": _resource_workflow_ready(),
        "pools_loaded": pools_loaded,
        "sshuser_grant_enabled": bool(config.resource_request.sshuser_grant_enabled),
        "sshuser_remote_exec_enabled": bool(config.resource_request.sshuser_remote_exec_enabled),
        "mode": "sshuser_mutation" if config.resource_request.sshuser_grant_enabled and config.resource_request.sshuser_remote_exec_enabled else "sshuser_advice_only",
        "jump_host_configured": bool(config.resource_request.sshuser_jump_host),
        "ssh_key_configured": bool(config.resource_request.sshuser_ssh_key_path),
        "known_hosts_configured": bool(config.resource_request.sshuser_known_hosts_path),
        "prometheus_configured": bool(config.resource_request.prometheus_url),
    }


@app.get("/metrics")
async def metrics():
    """Prometheus 风格监控指标"""
    from metrics_collector import get_metrics
    return get_metrics()


def start_scheduler():
    """启动定时任务检查超时消息"""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        check_timeout_messages,
        'interval',
        minutes=1
    )
    scheduler.start()
    logger.info("调度器启动")


def check_timeout_messages():
    """检查超时消息并调用 skill"""
    from metrics_collector import metrics
    timeout_messages = message_queue.get_timeout_messages()

    if not timeout_messages:
        return

    logger.info(f"检测到 {len(timeout_messages)} 条超时消息")

    for msg in timeout_messages:
        logger.info(f"处理超时消息: {msg.message_id}")
        result = skill_invoker.invoke(msg.content)

        top_score = float(result.get("score", 0.0) or 0.0)
        top_id = result.get("top_id", "")

        if result["success"]:
            message_queue.mark_replied(msg.message_id, result["response"])
            send_success = feishu_sender.send_text(result["response"], chat_id=msg.chat_id)
            message_queue.remove(msg.message_id)
            metrics.increment("messages_processed_success")
            # 记录命中率到 KBAdminService
            if result.get("from_kb"):
                kb_admin.record_hit("kb_hit", score=top_score, source=top_id)
            elif result.get("from_llm"):
                kb_admin.record_hit("llm_hit", score=top_score, source=top_id)
            elif result.get("low_confidence"):
                kb_admin.record_hit("miss", score=top_score, source=top_id)
            audit_logger.record(
                event="auto_reply_success",
                message_id=msg.message_id,
                user_id=msg.user_id,
                chat_id=msg.chat_id,
                question=msg.content,
                response=result["response"],
                low_confidence=bool(result.get("low_confidence")),
                from_kb=bool(result.get("from_kb")),
                from_llm=bool(result.get("from_llm")),
                score=top_score,
                top_id=top_id,
                send_success=send_success,
            )
            logger.info(f"消息处理成功: {msg.message_id} | score={top_score:.3f} top_id={top_id}")
        else:
            kb_admin.record_hit("fail", score=top_score, source=top_id)
            metrics.increment("messages_processed_failure")
            audit_logger.record(
                event="auto_reply_failure",
                message_id=msg.message_id,
                user_id=msg.user_id,
                chat_id=msg.chat_id,
                question=msg.content,
                error=result.get("error"),
            )
            logger.error(f"消息处理失败: {msg.message_id} | error={result.get('error')}")


if __name__ == "__main__":
    feishu_sender.start_retry_worker()
    start_scheduler()
    uvicorn.run(app, host="0.0.0.0", port=8000)
