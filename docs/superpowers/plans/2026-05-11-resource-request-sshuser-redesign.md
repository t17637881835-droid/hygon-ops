# Resource Request SSHUser Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the LDAP group authorization assumption with node-local `/public/bin/sshuser add/del` advice while keeping Phase 1 non-mutating.

**Architecture:** Users still apply for resource pools. Each pool maps to configured nodes; approval creates a grant plan containing target nodes and an sshuser command path, then sends owner/requester advice listing per-node add/del commands. No remote command execution is introduced in this phase.

**Tech Stack:** Python 3, FastAPI app modules, SQLite, PyYAML, unittest.

---

## File Map

- `feishu_ops/resource_config.py`: resource pool loader; remove LDAP group requirement and add `sshuser_path`.
- `feishu_ops/resource_approval.py`: replace LDAP grant advice formatter with sshuser node advice formatter.
- `feishu_ops/resource_request_store.py`: store target nodes and sshuser path in grant plans.
- `feishu_ops/main.py`: approval flow passes pool nodes/path into grant plan and advice; health reports sshuser advice mode.
- `feishu_ops/config.py`, `feishu_ops/config_check.py`: replace LDAP mutation config with sshuser remote execution config placeholders.
- `config/resource_pools.example.yml`, `.env.example`, `docker/docker-compose.yml`, `README.md`: document sshuser model.
- `docs/superpowers/specs/2026-05-11-resource-request-design.md`: update canonical design away from LDAP.
- `tests/test_resource_*.py`: update tests to assert sshuser advice and no LDAP dependency.

## Task 1: RED tests for sshuser authorization model

- [ ] Modify `tests/test_resource_config.py` so pool config without `ldap_group` loads and exposes `sshuser_path`.
- [ ] Modify `tests/test_resource_approval.py` so Phase 1 advice contains `/public/bin/sshuser add zhangsan` and `/public/bin/sshuser del zhangsan`, and does not mention LDAP/usermod.
- [ ] Modify `tests/test_resource_request_store.py` so grant plans store `target_nodes` and `sshuser_path`.
- [ ] Modify webhook/health tests to expect `sshuser_advice_only` and sshuser advice.
- [ ] Run `python -m unittest discover -s tests -p "test_resource_*.py"` and confirm failures are the expected LDAP-to-sshuser gaps.

## Task 2: Implement config, store, and advice changes

- [ ] Update `ResourcePool` with `sshuser_path: str = "/public/bin/sshuser"`; keep `nodes` required.
- [ ] Replace `allowed_ldap_groups` with `allowed_nodes` or remove the whitelist if unused by Phase 1.
- [ ] Change `ResourceGrantRecord` and `resource_grants` schema to persist `target_nodes` JSON and `sshuser_path`.
- [ ] Change `create_grant_plan(...)` signature to accept `target_nodes` and `sshuser_path`.
- [ ] Change advice formatter to generate per-node `sshuser add/del` instructions and explain Phase 1 does not execute node commands.

## Task 3: Wire approval flow and health/config

- [ ] In `main.py`, approval should create grant plan from `pool.nodes` and `pool.sshuser_path`.
- [ ] Owner/requester messages should use the sshuser advice formatter.
- [ ] Health should report `sshuser_grant_enabled` and `mode=sshuser_advice_only` when remote execution is disabled.
- [ ] Rename env examples from LDAP grant settings to `SSHUSER_GRANT_ENABLED`, `SSHUSER_COMMAND_PATH`, and `SSHUSER_REMOTE_EXEC_ENABLED`.

## Task 4: Documentation updates

- [ ] Update README to explain node-local `sshuser` modifies `sshd_config AllowUsers`.
- [ ] Update resource pool example to remove required `ldap_group` and show node list as authorization target.
- [ ] Update the design doc authorization model to `request -> pool -> nodes -> sshuser add/del -> timed revocation`.
- [ ] Mark Phase 2 risks: preexisting access protection, multi-grant reference counting, node whitelist, username validation, no arbitrary shell commands.

## Task 5: Verification

- [ ] Run `python -m unittest discover -s tests -p "test_resource_*.py"`; expected all pass.
- [ ] Run `python -m unittest discover -s tests -p "test_*.py"`; expected all pass except existing skipped test.
- [ ] Run `python -m compileall feishu_ops tests`; expected no compile errors.
