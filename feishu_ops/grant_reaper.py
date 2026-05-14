"""Expiry scanner for resource grants."""
from datetime import datetime, timezone
from typing import List


class GrantReaper:
    def __init__(self, store, grant_service):
        self.store = store
        self.grant_service = grant_service

    def revoke_due_grants(self) -> List:
        now_iso = datetime.now(timezone.utc).isoformat()
        results = []
        for grant in self.store.list_due_grants(now_iso):
            results.append(self.grant_service.revoke_grant(grant.grant_code, actor="grant_reaper"))
        return results
