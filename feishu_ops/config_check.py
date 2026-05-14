"""部署配置自检"""
import os
from pathlib import Path
from typing import Dict, List, Tuple

REQUIRED_ENV_VARS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "ANTHROPIC_API_KEY",
)


def validate_env(env: Dict[str, str]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    for name in REQUIRED_ENV_VARS:
        if not env.get(name):
            errors.append(f"{name} is required")

    if not env.get("FEISHU_WEBHOOK_URL"):
        warnings.append("FEISHU_WEBHOOK_URL is empty; Webhook fallback will be unavailable")

    if not env.get("FEISHU_OWNER_USER_IDS"):
        warnings.append("FEISHU_OWNER_USER_IDS is empty; manual reply cancellation may not work")

    kb_path = env.get("KNOWLEDGE_BASE_PATH")
    if kb_path and not Path(kb_path).exists():
        warnings.append(f"KNOWLEDGE_BASE_PATH does not exist: {kb_path}")

    for path_var in ("MESSAGE_QUEUE_DB_PATH", "AUDIT_LOG_PATH"):
        path_value = env.get(path_var)
        if path_value:
            parent = Path(path_value).parent
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                errors.append(f"{path_var} parent directory is not writable: {parent} ({exc})")

    resource_enabled = _env_bool(env, "RESOURCE_REQUEST_ENABLED", "false")
    if resource_enabled:
        pools_path = env.get("RESOURCE_POOLS_CONFIG_PATH", "./config/resource_pools.yml")
        if not pools_path:
            errors.append("RESOURCE_POOLS_CONFIG_PATH is required when RESOURCE_REQUEST_ENABLED=true")
        elif not Path(pools_path).exists():
            errors.append(f"RESOURCE_POOLS_CONFIG_PATH does not exist: {pools_path}")
        db_path = env.get("RESOURCE_REQUEST_DB_PATH", "./data/resource_requests.db")
        if db_path:
            try:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                errors.append(f"RESOURCE_REQUEST_DB_PATH parent directory is not writable: {Path(db_path).parent} ({exc})")

    sshuser_enabled = _env_bool(env, "SSHUSER_GRANT_ENABLED", "false")
    if sshuser_enabled:
        if not env.get("SSHUSER_COMMAND_PATH", "/public/bin/sshuser"):
            errors.append("SSHUSER_COMMAND_PATH is required when SSHUSER_GRANT_ENABLED=true")
        if not _env_bool(env, "SSHUSER_REMOTE_EXEC_ENABLED", "false"):
            warnings.append("SSHUSER_GRANT_ENABLED=true but SSHUSER_REMOTE_EXEC_ENABLED=false; approvals will remain advice-only")
        if _env_bool(env, "SSHUSER_REMOTE_EXEC_ENABLED", "false"):
            required_remote = ["SSHUSER_JUMP_HOST", "SSHUSER_JUMP_USER", "SSHUSER_TARGET_USER"]
            for name in required_remote:
                if not env.get(name):
                    errors.append(f"{name} is required when SSHUSER_REMOTE_EXEC_ENABLED=true")
            if env.get("SSHUSER_COMMAND_PATH", "/public/bin/sshuser") != "/public/bin/sshuser":
                errors.append("SSHUSER_COMMAND_PATH must be /public/bin/sshuser when SSHUSER_REMOTE_EXEC_ENABLED=true")
            for name in ["SSHUSER_SSH_KEY_PATH", "SSHUSER_KNOWN_HOSTS_PATH"]:
                value = env.get(name)
                if not value:
                    errors.append(f"{name} is required when SSHUSER_REMOTE_EXEC_ENABLED=true")
                elif not Path(value).exists():
                    errors.append(f"{name} does not exist: {value}")

    return errors, warnings


def _env_bool(env: Dict[str, str], name: str, default: str = "false") -> bool:
    return str(env.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def assert_valid_env(env: Dict[str, str]) -> None:
    errors, warnings = validate_env(env)
    if errors:
        raise RuntimeError("Configuration check failed: " + "; ".join(errors))
    for warning in warnings:
        print(f"WARN: {warning}")


def main() -> int:
    errors, warnings = validate_env(dict(os.environ))
    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("OK: configuration check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
