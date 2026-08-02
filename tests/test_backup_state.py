import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.backup_state import create_backup, restore_to, verify_backup


class BackupStateTests(unittest.TestCase):
    def test_backup_verifies_and_restores_to_an_empty_directory(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root)/"project"; auth=base/"data"/"auth"; auth.mkdir(parents=True)
            with sqlite3.connect(str(auth/"aicm_identity.db")) as db:
                db.execute("CREATE TABLE users(id TEXT PRIMARY KEY)"); db.execute("INSERT INTO users VALUES('u1')")
            user=base/"data"/"tenants"/"tenant_a"/"users"/"user_a"; user.mkdir(parents=True)
            (user/"interface_registry.json").write_text(json.dumps({"interfaces":[{"id":"custom_1","credential_configured":True,"auth_type":"bearer"}]}))
            (base/"data"/"admin_token.txt").write_text("secret\n")
            backup=create_backup(base,Path(root)/"backups",retain=2)
            result=verify_backup(backup)
            self.assertTrue(result["ok"]); self.assertEqual(result["credential_secret_count"],1)
            restored=restore_to(backup,Path(root)/"restore")
            with sqlite3.connect(str(restored/"data"/"auth"/"aicm_identity.db")) as db:
                self.assertEqual(db.execute("SELECT id FROM users").fetchone()[0],"u1")
            self.assertTrue((restored/"data"/"tenants"/"tenant_a"/"users"/"user_a"/"interface_registry.json").exists())


if __name__ == "__main__": unittest.main()
