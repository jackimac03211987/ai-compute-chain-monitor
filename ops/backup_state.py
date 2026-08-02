#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create and verify consistent backups of private beta state."""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

from aicm_io import data_lock


CRITICAL_FILES = ("admin_token.txt", "user_watchlist.json", "watchlist.json")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source, target):
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        os.chmod(target, 0o600)


def _credential_inventory(base):
    rows = []
    root = base / "data" / "tenants"
    for path in root.glob("*/users/*/interface_registry.json") if root.exists() else ():
        try: payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception: continue
        relative = path.relative_to(root).parts
        tenant_id, user_id = relative[0], relative[2]
        for record in payload.get("interfaces", []):
            if record.get("credential_configured"):
                rows.append({"tenant_id":tenant_id,"user_id":user_id,"interface_id":record.get("id"),"auth_type":record.get("auth_type")})
    return rows


def create_backup(base, destination, retain=24, now=None):
    base = Path(base).resolve(); destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True); os.chmod(destination, 0o700)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"aicm-{stamp}"
    stage = destination / f".aicm-{stamp}.{os.getpid()}.tmp"
    if final.exists(): raise FileExistsError(final)
    stage.mkdir(mode=0o700)
    try:
        source_db = base / "data" / "auth" / "aicm_identity.db"
        if not source_db.exists(): raise FileNotFoundError(source_db)
        target_db = stage / "data" / "auth" / "aicm_identity.db"
        target_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(source_db), timeout=15) as source, sqlite3.connect(str(target_db)) as target:
            source.backup(target)
        os.chmod(target_db, 0o600)
        with data_lock(base):
            tenants = base / "data" / "tenants"
            if tenants.exists(): shutil.copytree(tenants, stage / "data" / "tenants")
            for name in CRITICAL_FILES: _copy_file(base / "data" / name, stage / "data" / name)
            inventory = _credential_inventory(base)
        inventory_path = stage / "credentials_inventory.json"
        inventory_path.write_text(json.dumps({"items":inventory,"secrets_included":False},ensure_ascii=False,indent=2),encoding="utf-8")
        os.chmod(inventory_path, 0o600)
        files = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                os.chmod(path, 0o600)
                files[str(path.relative_to(stage))] = {"sha256":_sha256(path),"bytes":path.stat().st_size}
        manifest = {
            "schema_version":1,"created_at":dt.datetime.now(dt.timezone.utc).isoformat(),
            "source":str(base),"files":files,"credential_secret_count":len(inventory),
            "credential_secrets_included":False,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        os.chmod(stage / "manifest.json", 0o600)
        stage.rename(final)
        verify_backup(final)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    backups = sorted((path for path in destination.glob("aicm-*") if path.is_dir()), reverse=True)
    for old in backups[max(1,int(retain)):]: shutil.rmtree(old)
    return final


def verify_backup(path):
    root = Path(path).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    errors = []
    for relative, expected in manifest.get("files", {}).items():
        target = root / relative
        if not target.is_file(): errors.append(f"missing:{relative}")
        elif _sha256(target) != expected.get("sha256"): errors.append(f"checksum:{relative}")
    db = root / "data" / "auth" / "aicm_identity.db"
    try:
        with sqlite3.connect(str(db)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok": errors.append(f"sqlite:{result}")
    except Exception as exc: errors.append(f"sqlite:{exc}")
    if errors: raise RuntimeError("backup verification failed: " + ", ".join(errors))
    return {"ok":True,"path":str(root),"files":len(manifest.get("files",{})),"credential_secret_count":manifest.get("credential_secret_count",0)}


def restore_to(backup, target):
    source = Path(backup).resolve(); target = Path(target).resolve()
    verify_backup(source)
    if target.exists() and any(target.iterdir()): raise RuntimeError("restore target must be empty")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "data", target / "data", dirs_exist_ok=True)
    return target


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base",default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--destination",default="~/Backups/ai-compute-chain-monitor")
    parser.add_argument("--retain",type=int,default=24)
    parser.add_argument("--verify")
    parser.add_argument("--restore-to")
    args=parser.parse_args()
    if args.verify:
        result=verify_backup(args.verify)
        if args.restore_to: result["restored_to"]=str(restore_to(args.verify,args.restore_to))
    else: result=verify_backup(create_backup(args.base,args.destination,args.retain))
    print(json.dumps(result,ensure_ascii=False))


if __name__ == "__main__": main()
