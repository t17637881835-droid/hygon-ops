# Resource Request Phase 2 SSHUser Grant/Revoke Design

## 1. Purpose

Phase 2 extends the current Phase 1 resource request workflow from `sshuser` advice-only mode to confirmed, audited, node-local authorization and timed revocation.

The confirmed production model is:

```text
request -> resource pool -> target nodes -> jump host -> node-local sudo /public/bin/sshuser add/del -> timed revocation
```

Phase 2 must keep the safety guarantees from Phase 1:

- Ordinary users request resource pools, not nodes or commands.
- `/approve` creates an authorization plan but does not mutate nodes.
- Real node mutation requires owner confirmation through `/grant ... confirm`.
- Revocation is conservative: only access that the system can prove it created may be removed automatically.

## 2. Confirmed Decisions

- Execution channel: `haiguang-ops` connects to a jump host; the jump host SSHes to target nodes.
- Jump host capability: it can SSH from the jump host to each target node.
- Target node command: `sudo /public/bin/sshuser add <username>` and `sudo /public/bin/sshuser del <username>`.
- Existing access check: read `AllowUsers` from `/etc/ssh/sshd_config` before grant.
- Confirmation model: two-step owner flow.

```text
/approve R1 72h
/grant G1 confirm
```

## 3. Non-goals

- Do not allow Feishu text to become arbitrary shell commands.
- Do not allow users or owners to choose arbitrary target nodes at request time.
- Do not terminate active SSH sessions after revocation.
- Do not implement automatic approval in Phase 2.
- Do not require multi-instance locking in the first implementation.
- Do not introduce direct node SSH from `haiguang-ops`; all node access goes through the jump host.

## 4. High-level Flow

### 4.1 Approval

```text
User submits /apply
  -> system matches resource pool
  -> owner sends /approve R1 72h
  -> system creates resource_grants row
  -> system creates resource_grant_nodes rows
  -> system sends owner and requester a plan summary
```

No node command is executed during `/approve`.

### 4.2 Confirmed grant

```text
Owner sends /grant G1 confirm
  -> validate grant is planned or grant_failed/partial_granted retryable
  -> atomically mark grant as granting
  -> for each target node:
       validate username, node, and sshuser path
       read AllowUsers through the jump host
       if access already exists because another active system grant created it, mark covered_by_active_grant
       if access already exists without an active system-created grant, mark skipped_preexisting
       if access does not exist, execute sudo /public/bin/sshuser add <username>
       record per-node result
  -> aggregate overall grant status
  -> notify owner and requester
```

### 4.3 Timed revocation

```text
Grant reaches valid_until
  -> grant_reaper selects due grants
  -> atomically mark grant as revoking
  -> for each node with grant_status in (succeeded, covered_by_active_grant, skipped_preexisting, failed):
       skip if access existed before this grant
       skip if another active grant still needs same user-node access
       skip if this grant never provided or referenced active system access
       read current AllowUsers
       if user already absent, mark revoke succeeded
       execute sudo /public/bin/sshuser del <username>
       verify user is absent afterward
       record per-node result
  -> aggregate overall revoke status
  -> notify owner; notify requester only on clean completion
```

## 5. Components

### 5.1 `sshuser_grant_service.py`

Business-level service for grant and revoke operations.

Responsibilities:

- Validate grant state transitions.
- Validate usernames, nodes, and configured command paths.
- Create and update per-node grant records.
- Call the access checker and executor.
- Aggregate node results into overall grant status.
- Emit audit events.
- Prepare owner/requester notification summaries.

It depends on:

- `ResourceRequestStore`
- `ResourcePoolsConfig`
- `SshuserExecutor`
- `AuditLogger`
- notifier/sender interfaces

It must not accept raw shell command strings from callers.

### 5.2 `sshuser_executor.py`

Defines the execution interface.

```text
check_access(node, linux_username, sshuser_path) -> AccessCheckResult
grant_access(node, linux_username, sshuser_path) -> NodeCommandResult
revoke_access(node, linux_username, sshuser_path) -> NodeCommandResult
```

### 5.3 `jump_host_executor.py`

Phase 2 implementation of `SshuserExecutor`.

Execution path:

```text
haiguang-ops
  -> SSH jump host
  -> SSH target node
  -> sudo /public/bin/sshuser add|del <username>
```

The executor only supports structured operations:

- `check_access`
- `add`
- `del`

It must not expose a generic `execute(command: str)` method to business code.

### 5.4 `grant_reaper.py`

Background job for expiry reminders and due revocations.

Responsibilities:

- Send one-time expiry reminders before `valid_until`.
- Scan due grants.
- Trigger conservative revocation.
- Retry transient failures up to configured limits.
- Notify owner about failures and manual actions.

## 6. Data Model

### 6.1 `resource_grants`

Existing table remains the aggregate grant table.

Phase 2 adds these columns:

```text
grant_started_at text
grant_finished_at text
revoke_started_at text
revoke_finished_at text
expire_reminded_at text
```

Recommended `status` values:

```text
planned
granting
granted
partial_granted
grant_failed
revoking
revoked
partial_revoked
revoke_failed
```

`expired` is not used as a separate final state. A grant that expires and is successfully handled becomes `revoked`; one that expires and fails remains `partial_revoked` or `revoke_failed`.

### 6.2 `resource_grant_nodes`

New per-node table.

```text
id integer primary key
grant_code text not null
request_code text not null
linux_username text not null
pool_id text not null
node text not null
sshuser_path text not null

access_existed_before integer not null default 0
access_check_status text not null
access_check_error text

grant_status text not null
grant_attempts integer not null default 0
grant_last_error text
granted_at text

revoke_status text not null
revoke_attempts integer not null default 0
revoke_last_error text
revoked_at text

created_at text not null
updated_at text not null
```

Unique constraint:

```text
unique(grant_code, node)
```

### 6.3 Access check statuses

```text
unchecked
present
absent
failed
```

Rules:

- `present`: user had access before this grant; do not auto-delete on expiry.
- `absent`: user did not have access; a successful add may later be revoked.
- `failed`: do not add or delete automatically.

### 6.4 Per-node grant statuses

```text
planned
checking
skipped_preexisting
covered_by_active_grant
granting
succeeded
failed
```

`skipped_preexisting` means the user already had access outside this system and must never be auto-deleted by this grant.

`covered_by_active_grant` means the user already had access because another active system-created grant for the same user-node exists. This grant participates in the active reference count and may become responsible for final deletion when it is the last active non-preexisting grant.

### 6.5 Per-node revoke statuses

```text
not_due
skipped_preexisting
skipped_active_grant
skipped_not_granted
revoking
succeeded
failed
succeeded_manual
```

`skipped_*` statuses mean the current grant needs no further automatic action for that node.

## 7. State Aggregation

### 7.1 Grant aggregation

After `/grant G1 confirm` or `/grant G1 retry`:

```text
all node grant_status in (succeeded, skipped_preexisting, covered_by_active_grant)
  -> resource_grants.status = granted

some node grant_status in (succeeded, skipped_preexisting, covered_by_active_grant)
and some node grant_status = failed
  -> resource_grants.status = partial_granted

all target nodes grant_status = failed
  -> resource_grants.status = grant_failed
```

### 7.2 Revoke aggregation

Successful revoke terminal statuses:

```text
succeeded
skipped_preexisting
skipped_active_grant
skipped_not_granted
succeeded_manual
```

Rules:

```text
all nodes in terminal successful revoke statuses
  -> resource_grants.status = revoked

some nodes terminal successful and some failed
  -> resource_grants.status = partial_revoked

all nodes that required revocation failed
  -> resource_grants.status = revoke_failed
```

## 8. Safety Rules

### 8.1 Username validation

Default Linux username regex:

```text
^[a-z_][a-z0-9_-]{0,31}$
```

Invalid usernames must block remote execution.

If production usernames require dots, the regex may be explicitly changed to:

```text
^[a-z_][a-z0-9_.-]{0,31}$
```

The initial implementation should use the stricter default.

### 8.2 Node validation

A target node is valid only if both are true:

```text
node in resource_pools_config.allowed_nodes
node matches ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$
```

### 8.3 Command path validation

Real execution should require:

```text
pool.sshuser_path == config.resource_request.sshuser_command_path
config.resource_request.sshuser_command_path == /public/bin/sshuser
```

If paths differ, execution is refused and the system returns advice-only output.

### 8.4 No arbitrary shell

Business code must call structured executor methods. It must not construct or pass arbitrary command strings.

Only these remote operations are allowed:

```text
check AllowUsers
sudo /public/bin/sshuser add <safe_username>
sudo /public/bin/sshuser del <safe_username>
```

### 8.5 Existing access protection

Before grant, the system reads `AllowUsers` from each node.

If the user already exists and another active system-created grant covers the same user-node:

```text
access_existed_before = 0
grant_status = covered_by_active_grant
```

If the user already exists and no active system-created grant covers the same user-node:

```text
access_existed_before = 1
grant_status = skipped_preexisting
```

On expiry:

```text
revoke_status = skipped_preexisting
no sshuser del is executed
```

### 8.6 Multiple active grants protection

Before `sshuser del`, the system checks for another active grant:

```text
same linux_username
same node
other grant_code != current
other grant status in (granted, partial_granted, revoking)
other valid_until > now
other node grant_status in (succeeded, covered_by_active_grant)
```

If found:

```text
revoke_status = skipped_active_grant
no sshuser del is executed
```

If no other active grant is found and the current node `grant_status` is `covered_by_active_grant`, the current grant may execute `sshuser del` because it is now the last active non-preexisting reference to that user-node access.

### 8.7 Check failure behavior

If `AllowUsers` cannot be read or parsed:

```text
do not add
do not del
mark node failed
notify owner
```

This avoids creating access that the system cannot later revoke safely.

## 9. Jump Host Execution

### 9.1 Required configuration

```env
SSHUSER_GRANT_ENABLED=true
SSHUSER_REMOTE_EXEC_ENABLED=true
SSHUSER_JUMP_HOST=ops-jump.example.com
SSHUSER_JUMP_PORT=22
SSHUSER_JUMP_USER=resource_bot
SSHUSER_SSH_KEY_PATH=/app/secrets/resource_bot_id_rsa
SSHUSER_KNOWN_HOSTS_PATH=/app/secrets/known_hosts
SSHUSER_TARGET_USER=resource_exec
SSHUSER_TARGET_SSH_PORT=22
SSHUSER_COMMAND_PATH=/public/bin/sshuser
SSHUSER_COMMAND_PREFIX=sudo
SSHUSER_CONNECT_TIMEOUT_SECONDS=5
SSHUSER_COMMAND_TIMEOUT_SECONDS=15
SSHUSER_MAX_RETRIES=2
SSHUSER_RETRY_BACKOFF_SECONDS=3
SSHUSER_MAX_PARALLEL_NODES=1
```

`SSHUSER_MAX_PARALLEL_NODES=1` means the first implementation executes nodes serially. The config exists for later parallelism.

### 9.2 SSH options

Both jump-host and target-node SSH should use:

```text
BatchMode=yes
StrictHostKeyChecking=yes
UserKnownHostsFile=<configured known_hosts>
ConnectTimeout=<configured seconds>
```

Password prompts are not allowed.

### 9.3 Target node sudoers

The target node execution identity should have a narrow sudoers rule allowing only the required command family.

Conceptual policy:

```text
resource_exec may run sudo /public/bin/sshuser add <username>
resource_exec may run sudo /public/bin/sshuser del <username>
resource_exec may read /etc/ssh/sshd_config for AllowUsers checks
```

A dedicated read-only helper script is preferred later, but Phase 2 can start with a controlled `grep` or `cat` of `/etc/ssh/sshd_config`.

## 10. Access Check Parsing

The check operation should read the `AllowUsers` line and parse it locally in Python.

Remote read:

```text
sudo grep -E '^AllowUsers[[:space:]]+' /etc/ssh/sshd_config
```

Local parsing rules:

- Exactly one `AllowUsers` line is supported.
- Split by whitespace after `AllowUsers`.
- Match the sanitized username as an exact token.
- If multiple `AllowUsers` lines are found, mark `access_check_status=failed`.
- If the file cannot be read, mark `access_check_status=failed`.

The sanitized username should not be interpolated into a remote grep regex.

## 11. Retry and Failure Handling

### 11.1 Retryable errors

Automatically retry:

```text
timeout
ssh_failed
jump_host_unavailable
```

### 11.2 Non-retryable errors

Do not auto-retry:

```text
invalid_username
node_not_allowed
permission_denied
sudo_requires_password
command_not_found
path_not_allowed
access_check_parse_failed
```

### 11.3 Manual retry commands

Owner commands:

```text
/grant G1 retry
/revoke G1 retry
```

Rules:

- `/grant G1 retry` retries only nodes with `grant_status=failed`.
- `/revoke G1 retry` retries only nodes with `revoke_status=failed`.
- Successful or skipped nodes are not reprocessed.

### 11.4 Manual mark done

Owner command:

```text
/revoke G1 mark-done node02,node03
```

Rules:

- Only owner may use it.
- Only nodes in the grant may be marked.
- It sets `revoke_status=succeeded_manual`.
- It records actor and audit details.

## 12. Owner and Requester Notifications

### 12.1 Grant success to owner

```text
✅ 授权完成：R1 / G1
用户：zhangsan
资源池：k100_train
有效期至：2026-05-11 18:00

节点：
- node01: 已授权
- node02: 授权前已有权限，已跳过 add
```

### 12.2 Partial grant to owner

```text
⚠️ 授权部分完成：R1 / G1

成功节点：
- node01

失败节点：
- node02: SSH timeout

可重试：
/grant G1 retry
```

### 12.3 Grant to requester

```text
✅ 资源申请已授权：R1
资源池：k100_train
有效期至：2026-05-11 18:00
可登录节点：
node01
node02
```

For partial grants, requester sees only current availability and a simple note that operations is handling failed nodes.

### 12.4 Revoke success to owner

```text
✅ 授权已到期并完成撤权：R1 / G1
用户：zhangsan
资源池：k100_train

节点：
- node01: 已撤权
- node02: 授权前已有权限，未删除
```

### 12.5 Revoke failure to owner

```text
🚨 授权到期，但部分撤权失败：R1 / G1
用户：zhangsan

失败节点：
- node03: SSH timeout

已跳过节点：
- node02: 授权前已有权限，未删除
- node04: 仍有其他有效授权，未删除

可重试：
/revoke G1 retry
```

Requester should not receive detailed revoke failure messages. Owner handles revoke failures.

## 13. Audit Events

Add audit events:

```text
resource_grant_confirmed
sshuser_access_check_started
sshuser_access_check_finished
sshuser_grant_node_started
sshuser_grant_node_finished
resource_grant_completed
resource_grant_partial_failed
resource_grant_expire_reminded
sshuser_revoke_node_started
sshuser_revoke_node_finished
resource_revoke_completed
resource_revoke_partial_failed
resource_revoke_manual_mark_done
```

Each event should include:

```text
request_code
grant_code
linux_username
pool_id
node
operation
actor
result
error_type
error_message
timestamp
```

Do not log secret contents.

## 14. Configuration and Health

### 14.1 Config validation

If both are true:

```text
SSHUSER_GRANT_ENABLED=true
SSHUSER_REMOTE_EXEC_ENABLED=true
```

then config check requires:

```text
SSHUSER_JUMP_HOST
SSHUSER_JUMP_USER
SSHUSER_SSH_KEY_PATH existing and readable
SSHUSER_KNOWN_HOSTS_PATH existing and readable
SSHUSER_TARGET_USER
SSHUSER_COMMAND_PATH=/public/bin/sshuser
```

If `SSHUSER_GRANT_ENABLED=true` but `SSHUSER_REMOTE_EXEC_ENABLED=false`, approvals remain advice-only and config check emits a warning.

### 14.2 Health output

`/health` should report configuration readiness without doing real SSH probes by default.

Example:

```json
{
  "resource_request": {
    "sshuser_grant_enabled": true,
    "sshuser_remote_exec_enabled": true,
    "mode": "sshuser_mutation",
    "jump_host_configured": true,
    "ssh_key_configured": true,
    "known_hosts_configured": true
  }
}
```

A separate owner-only preflight command or endpoint may later perform real jump-host connectivity checks.

## 15. Concurrency and Locking

Initial deployment is single-instance. Use SQLite compare-and-set status updates to avoid duplicate work inside one process or accidental concurrent requests.

Example revoke claim:

```text
UPDATE resource_grants
SET status = 'revoking'
WHERE grant_code = ?
  AND status IN ('granted', 'partial_granted', 'partial_revoked', 'revoke_failed')
```

Only proceed if one row was updated.

Future multi-instance deployment requires a distributed lock and is outside Phase 2 initial implementation.

## 16. Testing Strategy

### 16.1 Unit tests

- Username validation accepts safe Linux names and rejects command injection strings.
- Node validation rejects nodes outside `allowed_nodes`.
- Path validation rejects non-configured `sshuser_path`.
- AllowUsers parser handles present, absent, missing, and multiple-line cases.
- Grant aggregation returns `granted`, `partial_granted`, or `grant_failed` correctly.
- Revoke aggregation returns `revoked`, `partial_revoked`, or `revoke_failed` correctly.
- Pre-existing access prevents auto-delete.
- Active overlapping grant prevents auto-delete.
- A later overlapping grant marked `covered_by_active_grant` can perform final delete only after earlier active grants have expired.

### 16.2 Store tests

- `resource_grant_nodes` rows are created for each target node.
- Per-node grant and revoke statuses can be updated independently.
- Due grants can be listed by `valid_until` and status.
- Manual mark-done updates only nodes inside the grant.

### 16.3 Executor tests

Use a fake executor for normal test runs.

- Fake check returns present/absent/failed.
- Fake grant and revoke return success/failure by node.
- Service records stdout, stderr, exit code, and error type.
- No test requires real SSH.

### 16.4 Webhook integration tests

- `/approve R1 72h` still only creates a plan.
- `/grant G1 confirm` invokes fake executor and updates statuses.
- `/grant G1 retry` retries only failed nodes.
- Reaper revokes due grants through fake executor.
- Reaper skips pre-existing and active-overlap nodes.
- Owner notifications include per-node summaries.

## 17. Rollout Plan

1. Keep defaults safe:

```env
SSHUSER_GRANT_ENABLED=false
SSHUSER_REMOTE_EXEC_ENABLED=false
```

2. Implement schema and fake executor tests first.
3. Implement service logic with fake executor.
4. Add jump-host executor behind config flags.
5. Enable in staging with a small test pool.
6. Verify grant, retry, revoke, and mark-done flows.
7. Enable production only after sudoers, known_hosts, and key permissions are reviewed.

## 18. Acceptance Criteria

Phase 2 is acceptable when:

- `/approve` never mutates nodes.
- `/grant G1 confirm` mutates only configured nodes through the jump host.
- Remote commands are limited to fixed `sshuser add/del` and AllowUsers checks.
- Invalid usernames, nodes, or paths block execution.
- Per-node grant and revoke results are persisted.
- Pre-existing access is never auto-deleted.
- Overlapping active grants prevent auto-delete.
- Failed nodes can be retried without reprocessing successful nodes.
- Due grants are revoked or produce owner alerts.
- All significant operations are audited.
- Unit and integration tests pass with a fake executor.
