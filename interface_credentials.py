# -*- coding: utf-8 -*-
"""User-scoped macOS Keychain credentials with a fake-runner test seam."""
import json, subprocess


class SecurityRunner:
    service = "AICM.InterfaceMonitor"
    def put(self, account, value):
        subprocess.run(
            ["/usr/bin/security", "add-generic-password", "-U", "-s", self.service, "-a", account, "-w"],
            input=value + "\n", check=True, capture_output=True, text=True,
        )
    def get(self, account):
        result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", self.service, "-a", account, "-w"], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    def delete(self, account):
        return subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", self.service, "-a", account], capture_output=True).returncode == 0


class KeychainStore:
    def __init__(self, auth, runner=None): self.auth, self.runner = auth, runner or SecurityRunner()
    def _account(self, interface_id): return f"tenant:{self.auth.tenant_id}:user:{self.auth.user_id}:interface:{interface_id}"
    def put(self, interface_id, bundle):
        self.runner.put(self._account(interface_id), json.dumps(bundle, separators=(",", ":")))
        return {"credential_configured": True, "credential_ref": str(interface_id)}
    def get(self, interface_id):
        value = self.runner.get(self._account(interface_id))
        return json.loads(value) if value else {}
    def delete(self, interface_id): return self.runner.delete(self._account(interface_id))
    def stage(self, preview_id, row_number, bundle):
        ref = f"preview:{preview_id}:{int(row_number)}"
        self.put(ref, bundle); return ref
    def promote(self, staged_ref, interface_id):
        bundle = self.get(staged_ref)
        if bundle: self.put(interface_id, bundle)
        self.delete(staged_ref); return str(interface_id)
    def clear_preview(self, preview_id):
        prefix = self._account(f"preview:{preview_id}:")
        items = getattr(self.runner, "items", {})
        accounts = [key for key in list(items) if key.startswith(prefix)]
        for account in accounts: self.runner.delete(account)
        return len(accounts)
