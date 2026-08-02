#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small copy-truncate log rotation suitable for LaunchAgent-owned files."""

import argparse
import shutil
from pathlib import Path


def rotate(directory, max_bytes=5*1024*1024, keep=5):
    rotated=[]
    for path in sorted(Path(directory).glob("*.log")):
        if not path.is_file() or path.stat().st_size<=max_bytes: continue
        oldest=path.with_name(path.name+f".{keep}"); oldest.unlink(missing_ok=True)
        for number in range(keep-1,0,-1):
            source=path.with_name(path.name+f".{number}")
            if source.exists(): source.replace(path.with_name(path.name+f".{number+1}"))
        shutil.copy2(path,path.with_name(path.name+".1"))
        path.write_bytes(b""); rotated.append(path.name)
    return rotated


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--directory",default=str(Path(__file__).resolve().parents[1]/"logs"))
    parser.add_argument("--max-mb",type=float,default=5.0); parser.add_argument("--keep",type=int,default=5)
    args=parser.parse_args(); rotate(args.directory,max(1,int(args.max_mb*1024*1024)),max(1,args.keep))
if __name__ == "__main__": main()
