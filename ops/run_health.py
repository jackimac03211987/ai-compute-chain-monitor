#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the existing health suite and atomically persist its latest result."""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

import health_check
from aicm_io import write_json_atomic


def run(base, token_file, output):
    result=health_check.check(base)
    token_path=Path(token_file)
    token=token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    admin=health_check.check_admin(base,token)
    result["errors"].extend(admin.pop("errors")); result.update(admin)
    result["checked_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
    result["ok"]=not result["errors"]
    write_json_atomic(output,result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base",default="http://127.0.0.1:18911")
    parser.add_argument("--admin-token-file",default=str(PROJECT_ROOT/"data"/"admin_token.txt"))
    parser.add_argument("--output",default=str(PROJECT_ROOT/"data"/"health_status.json"))
    args=parser.parse_args(); result=run(args.base,args.admin_token_file,args.output)
    if not result["ok"]: print(json.dumps(result,ensure_ascii=False))
    return 0 if result["ok"] else 1
if __name__ == "__main__": raise SystemExit(main())
