# -*- coding: utf-8 -*-
"""Private interface result history and adaptive scheduling primitives."""
import datetime as dt, os, threading, time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor


def next_delay_minutes(consecutive_failures, interval_minutes):
    return {1:1,2:3,3:10}.get(int(consecutive_failures),int(interval_minutes))


def is_due(latest, interval_minutes, now=None):
    if not latest:
        return True
    now=now or dt.datetime.now(dt.timezone.utc)
    try:
        tested=dt.datetime.fromisoformat(str(latest.get("tested_at") or "").replace("Z","+00:00"))
        if tested.tzinfo is None: tested=tested.replace(tzinfo=dt.timezone.utc)
    except (TypeError,ValueError):
        return True
    failures=int(latest.get("consecutive_failures") or 0) if latest.get("status")!="healthy" else 0
    delay=next_delay_minutes(failures,int(interval_minutes)) if failures else int(interval_minutes)
    return tested+dt.timedelta(minutes=delay)<=now


class InterfaceResultStore:
    def __init__(self, workspace): self.workspace=workspace
    def _load(self):
        payload=self.workspace.read_json("interface_results.json",{"version":1,"items":{}})
        return payload if isinstance(payload.get("items"),dict) else {"version":1,"items":{}}
    def record(self, interface_id, result):
        payload=self._load(); rows=list(payload["items"].get(interface_id,[])); rows.append(dict(result))
        cutoff=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=30); kept=[]
        for row in rows:
            try: stamp=dt.datetime.fromisoformat(str(row.get("tested_at")).replace("Z","+00:00"))
            except Exception: stamp=cutoff
            if stamp>=cutoff: kept.append(row)
        payload["items"][interface_id]=kept[-500:]; self.workspace.write_json("interface_results.json",payload); return dict(result)
    def history(self, interface_id, limit=100):
        rows=self._load()["items"].get(interface_id,[]); size=max(1,min(int(limit),1000)); return {"items":list(reversed(rows[-size:])),"total":len(rows)}
    def latest(self, interface_id):
        rows=self._load()["items"].get(interface_id,[]); return dict(rows[-1]) if rows else None


class InterfaceMonitorScheduler:
    def __init__(self, probe=None, max_workers=4, per_host_limit=2, poll_seconds=10, max_pending=None, tenant_hourly_budget=None):
        self.probe=probe
        self.executor=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix="aicm-private-monitor")
        self.per_host_limit=max(1,int(per_host_limit)); self.poll_seconds=max(.01,float(poll_seconds))
        self.max_pending=max(1,int(max_pending or os.getenv("AICM_MONITOR_MAX_PENDING","16")))
        self.tenant_hourly_budget=max(1,int(tenant_hourly_budget or os.getenv("AICM_TENANT_HOURLY_PROBES","120")))
        self.active=set(); self.host_active={}; self.lock=threading.Lock(); self.stop_event=threading.Event(); self.thread=None; self.stopped=False
        self.tenant_probes=defaultdict(deque); self.dropped=defaultdict(int)

    def _budget_available(self, tenant, now):
        probes=self.tenant_probes[str(tenant)]; cutoff=now-3600
        while probes and probes[0]<cutoff: probes.popleft()
        if len(probes)>=self.tenant_hourly_budget: return False
        probes.append(now); return True

    def _submit(self, key, operation, callback=None, host="local", budgeted=True):
        if callback is None:
            callback=operation; operation=self.probe
        if not callable(operation) or not callable(callback):
            raise TypeError("operation and callback must be callable")
        host=str(host or "local").lower()
        with self.lock:
            if self.stopped: return "stopped"
            if key in self.active: return "active"
            if self.host_active.get(host,0)>=self.per_host_limit: return "host_limit"
            if len(self.active)>=self.max_pending:
                self.dropped["capacity"]+=1; return "capacity"
            tenant=key[0] if isinstance(key,(tuple,list)) and key else "default"
            if budgeted and not self._budget_available(tenant,time.monotonic()):
                self.dropped["budget"]+=1; return "budget"
            self.active.add(key); self.host_active[host]=self.host_active.get(host,0)+1
        def run():
            try:
                try: result=operation()
                except Exception: result={"status":"failed","tested_at":dt.datetime.now(dt.timezone.utc).isoformat(),"errors":["probe operation failed"]}
                callback(result)
            finally:
                with self.lock:
                    self.active.discard(key); remaining=self.host_active.get(host,1)-1
                    if remaining>0: self.host_active[host]=remaining
                    else: self.host_active.pop(host,None)
        self.executor.submit(run); return "submitted"

    def submit(self, key, operation, callback=None, host="local"):
        return self._submit(key,operation,callback,host)=="submitted"

    def run_due(self, items, now=None):
        submitted=0
        for item in items or []:
            if not item.get("enabled",True) or not is_due(item.get("latest"),item.get("interval_minutes",15),now): continue
            if self._submit(item["key"],item["operation"],item["callback"],item.get("host") or "local",item.get("budgeted",True))=="submitted": submitted+=1
        return submitted

    def stats(self):
        with self.lock:
            return {"active":len(self.active),"max_pending":self.max_pending,"per_host_limit":self.per_host_limit,"tenant_hourly_budget":self.tenant_hourly_budget,"dropped":dict(self.dropped)}

    def start(self, provider):
        with self.lock:
            if self.thread and self.thread.is_alive(): return False
            if self.stopped: raise RuntimeError("scheduler has been stopped")
        def loop():
            while not self.stop_event.is_set():
                try: self.run_due(provider())
                except Exception: pass
                self.stop_event.wait(self.poll_seconds)
        self.thread=threading.Thread(target=loop,name="aicm-private-scheduler",daemon=True); self.thread.start(); return True

    def stop(self):
        with self.lock:
            if self.stopped: return
            self.stopped=True
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(timeout=max(1,self.poll_seconds+1))
        self.executor.shutdown(wait=True)
