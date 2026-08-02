# -*- coding: utf-8 -*-
"""Bounded, SSRF-aware interface health probes and rule evaluation."""
import base64, datetime as dt, ipaddress, json, re, socket, time, urllib.parse, urllib.request

MAX_BODY = 1024 * 1024
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
METADATA_HOSTS = {"metadata.google.internal", "metadata.azure.internal"}
METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("100.100.100.200")}


class TargetPolicy:
    def __init__(self, resolve=None): self.resolve = resolve or self._resolve
    def _resolve(self, host): return sorted({row[4][0] for row in socket.getaddrinfo(host, None)})
    def validate(self, url, allow_private=False):
        parsed = urllib.parse.urlsplit(str(url or ""))
        if parsed.scheme not in {"http","https"} or not parsed.hostname: raise ValueError("only HTTP/HTTPS targets are allowed")
        if parsed.username or parsed.password: raise ValueError("URL user-info is forbidden")
        host = parsed.hostname.lower()
        if host in METADATA_HOSTS: raise ValueError("cloud metadata target is forbidden")
        addresses = []
        for value in self.resolve(host):
            address = ipaddress.ip_address(value); addresses.append(str(address))
            if address in METADATA_IPS: raise ValueError("cloud metadata target is forbidden")
            unsafe = address.is_loopback or address.is_private or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved or address in TAILSCALE
            if unsafe and not allow_private: raise ValueError("private target requires explicit permission")
        return {"url": parsed.geturl(), "host": host, "addresses": addresses}


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy, allow_private):
        super().__init__(); self.policy=policy; self.allow_private=allow_private; self.count=0
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 3: raise ValueError("too many redirects")
        self.policy.validate(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _json_path(payload, path):
    value = payload
    for part in str(path or "").split("."):
        if not part: continue
        value = value[int(part)] if isinstance(value,list) else value[part]
    return value


def _time(value):
    if isinstance(value,(int,float)): return dt.datetime.fromtimestamp(value,dt.timezone.utc)
    return dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))


def evaluate_rules(record, status, latency_ms, body, now=None):
    now = now or dt.datetime.now(dt.timezone.utc); errors=[]; text=body.decode("utf-8","replace")
    if int(status) not in [int(x) for x in record.get("expected_statuses") or [200]]: errors.append(f"unexpected HTTP status {status}")
    if record.get("max_latency_ms") and latency_ms > float(record["max_latency_ms"]): errors.append("latency exceeds configured maximum")
    if record.get("body_keyword") and str(record["body_keyword"]) not in text: errors.append("response keyword missing")
    payload = None
    if record.get("json_path") or record.get("freshness_json_path"):
        try: payload=json.loads(text)
        except Exception: errors.append("response is not valid JSON")
    if payload is not None and record.get("json_path"):
        try:
            actual=_json_path(payload,record["json_path"])
            if actual != record.get("expected_value"): errors.append("JSON expected value mismatch")
        except Exception: errors.append("JSON path not found")
    if payload is not None and record.get("freshness_json_path"):
        try:
            age=(now-_time(_json_path(payload,record["freshness_json_path"]))).total_seconds()/60
            if age > float(record.get("max_age_minutes") or 0): errors.append("response data is stale")
        except Exception: errors.append("freshness value is invalid")
    return {"status":"failed" if errors else "healthy","latency_ms":round(float(latency_ms),1),"http_status":int(status),"errors":errors}


def sanitize_probe_error(value):
    text=str(value or "")
    text=re.sub(r"([?&](?:api[_-]?key|token|secret|password|authorization)=)[^&\s]+",r"\1[REDACTED]",text,flags=re.I)
    text=re.sub(r"(Authorization:\s*)(?:Bearer|Basic)\s+\S+",r"\1[REDACTED]",text,flags=re.I)
    return text[:1000]


def probe_interface(base, record, credential_store=None, opener=None, now=None):
    tested=(now or dt.datetime.now(dt.timezone.utc)).isoformat(); mode=record.get("monitor_mode")
    if mode in {"local_task","local_file"} and not record.get("url"):
        return {"status":"healthy","tested_at":tested,"latency_ms":0,"errors":[]}
    policy=TargetPolicy(); allow_private=bool(record.get("allow_private_target")); policy.validate(record.get("url"),allow_private)
    headers=dict(record.get("headers") or {}); bundle=credential_store.get(record["id"]) if credential_store else {}
    if bundle.get("type")=="bearer": headers["Authorization"]="Bearer "+bundle.get("token","")
    elif bundle.get("type")=="api_key": headers[bundle.get("header","X-API-Key")]=bundle.get("value","")
    elif bundle.get("type")=="basic": headers["Authorization"]="Basic "+base64.b64encode(f'{bundle.get("username","")}:{bundle.get("password","")}'.encode()).decode()
    elif bundle.get("type")=="secret_headers": headers.update(bundle.get("headers") or {})
    request=urllib.request.Request(record["url"],headers=headers,method=record.get("method","GET")); started=time.monotonic()
    try:
        client=opener or urllib.request.build_opener(SafeRedirectHandler(policy,allow_private))
        request_fn=client.open if hasattr(client,"open") else client.urlopen
        response=request_fn(request,timeout=int(record.get("timeout_seconds") or 8)); body=response.read(MAX_BODY+1)
        if len(body)>MAX_BODY: raise ValueError("response body exceeds 1 MB")
        result=evaluate_rules(record,response.status,(time.monotonic()-started)*1000,body,now)
    except Exception as exc:
        result={"status":"failed","latency_ms":round((time.monotonic()-started)*1000,1),"errors":[sanitize_probe_error(exc)]}
    result["tested_at"]=tested; return result
