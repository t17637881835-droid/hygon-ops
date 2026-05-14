"""配置管理"""
import os
from dataclasses import dataclass
from typing import Optional


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    webhook_url: str
    encrypt_key: str  # 飞书事件订阅的加密密钥
    verification_token: str = ""  # 飞书事件订阅 Verification Token
    owner_user_ids: str = ""
    bot_user_ids: str = ""
    use_long_connection: bool = False  # 启用飞书长连接事件订阅（无需公网 webhook）

@dataclass
class SkillConfig:
    timeout_minutes: int = 10
    knowledge_base_path: str = "./knowledge_base"
    min_confidence_score: float = 0.45
    high_confidence_score: float = 0.85  # 超过此分直接返回 FAQ，不调 LLM
    busy_reply: str = "收到，我现在有点忙，这个问题我稍后确认下再回复你。"
    message_queue_db_path: str = ""
    audit_log_path: str = ""


@dataclass
class ResourceRequestConfig:
    enabled: bool = False
    pools_config_path: str = "./config/resource_pools.yml"
    db_path: str = "./data/resource_requests.db"
    prometheus_url: str = ""
    prometheus_timeout_seconds: int = 5
    sshuser_grant_enabled: bool = False
    sshuser_command_path: str = "/public/bin/sshuser"
    sshuser_remote_exec_enabled: bool = False
    sshuser_connect_timeout_seconds: int = 5
    sshuser_jump_host: str = ""
    sshuser_jump_port: int = 22
    sshuser_jump_user: str = ""
    sshuser_ssh_key_path: str = ""
    sshuser_known_hosts_path: str = ""
    sshuser_target_user: str = ""
    sshuser_target_ssh_port: int = 22
    sshuser_command_timeout_seconds: int = 15
    sshuser_max_retries: int = 2
    card_approval_enabled: bool = False
    auto_approve_max_hours: float = 24
    auto_approve_max_nodes: int = 1
    node_info_link: str = ""
    sshuser_retry_backoff_seconds: int = 3
    sshuser_max_parallel_nodes: int = 1
    sshuser_executor_type: str = "jump_host"
    default_grant_hours: int = 24
    max_grant_hours: int = 168
    grant_confirm_required: bool = True
    expire_check_interval_minutes: int = 5
    expire_remind_hours: int = 2

@dataclass
class AnthropicConfig:
    api_key: str
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096

@dataclass
class Config:
    feishu: FeishuConfig
    skill: SkillConfig
    resource_request: ResourceRequestConfig
    anthropic: AnthropicConfig

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            feishu=FeishuConfig(
                app_id=os.getenv("FEISHU_APP_ID", ""),
                app_secret=os.getenv("FEISHU_APP_SECRET", ""),
                webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
                encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", ""),
                verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", ""),
                owner_user_ids=os.getenv("FEISHU_OWNER_USER_IDS", ""),
                bot_user_ids=os.getenv("FEISHU_BOT_USER_IDS", ""),
                use_long_connection=_env_bool("FEISHU_USE_LONG_CONNECTION", "false"),
            ),
            skill=SkillConfig(
                timeout_minutes=int(os.getenv("SKILL_TIMEOUT_MINUTES", "10")),
                knowledge_base_path=os.getenv("KNOWLEDGE_BASE_PATH", "./knowledge_base"),
                min_confidence_score=float(os.getenv("MIN_CONFIDENCE_SCORE", "0.45")),
                high_confidence_score=float(os.getenv("HIGH_CONFIDENCE_SCORE", "0.85")),
                busy_reply=os.getenv("BUSY_REPLY", "收到，我现在有点忙，这个问题我稍后确认下再回复你。"),
                message_queue_db_path=os.getenv("MESSAGE_QUEUE_DB_PATH", ""),
                audit_log_path=os.getenv("AUDIT_LOG_PATH", "")
            ),
            resource_request=ResourceRequestConfig(
                enabled=_env_bool("RESOURCE_REQUEST_ENABLED", "false"),
                pools_config_path=os.getenv("RESOURCE_POOLS_CONFIG_PATH", "./config/resource_pools.yml"),
                db_path=os.getenv("RESOURCE_REQUEST_DB_PATH", "./data/resource_requests.db"),
                prometheus_url=os.getenv("PROMETHEUS_URL", ""),
                prometheus_timeout_seconds=int(os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "5")),
                sshuser_grant_enabled=_env_bool("SSHUSER_GRANT_ENABLED", "false"),
                sshuser_command_path=os.getenv("SSHUSER_COMMAND_PATH", "/public/bin/sshuser"),
                sshuser_remote_exec_enabled=_env_bool("SSHUSER_REMOTE_EXEC_ENABLED", "false"),
                sshuser_connect_timeout_seconds=int(os.getenv("SSHUSER_CONNECT_TIMEOUT_SECONDS", "5")),
                sshuser_jump_host=os.getenv("SSHUSER_JUMP_HOST", ""),
                sshuser_jump_port=int(os.getenv("SSHUSER_JUMP_PORT", "22")),
                sshuser_jump_user=os.getenv("SSHUSER_JUMP_USER", ""),
                sshuser_ssh_key_path=os.getenv("SSHUSER_SSH_KEY_PATH", ""),
                sshuser_known_hosts_path=os.getenv("SSHUSER_KNOWN_HOSTS_PATH", ""),
                sshuser_target_user=os.getenv("SSHUSER_TARGET_USER", ""),
                sshuser_target_ssh_port=int(os.getenv("SSHUSER_TARGET_SSH_PORT", "22")),
                sshuser_command_timeout_seconds=int(os.getenv("SSHUSER_COMMAND_TIMEOUT_SECONDS", "15")),
                sshuser_max_retries=int(os.getenv("SSHUSER_MAX_RETRIES", "2")),
                sshuser_retry_backoff_seconds=int(os.getenv("SSHUSER_RETRY_BACKOFF_SECONDS", "3")),
                sshuser_max_parallel_nodes=int(os.getenv("SSHUSER_MAX_PARALLEL_NODES", "1")),
                sshuser_executor_type=os.getenv("SSHUSER_EXECUTOR_TYPE", "jump_host"),
                default_grant_hours=int(os.getenv("RESOURCE_DEFAULT_GRANT_HOURS", "24")),
                max_grant_hours=int(os.getenv("RESOURCE_MAX_GRANT_HOURS", "168")),
                grant_confirm_required=_env_bool("RESOURCE_GRANT_CONFIRM_REQUIRED", "true"),
                expire_check_interval_minutes=int(os.getenv("RESOURCE_EXPIRE_CHECK_INTERVAL_MINUTES", "5")),
                expire_remind_hours=int(os.getenv("RESOURCE_EXPIRE_REMIND_HOURS", "2")),
            ),
            anthropic=AnthropicConfig(
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))
            )
        )

_config: Optional[Config] = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config