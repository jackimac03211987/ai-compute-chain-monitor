import datetime as dt, json, threading, unittest, urllib.error, urllib.parse, urllib.request
from unittest.mock import patch
import app
from tests.helpers import ProjectFixture


class PrivateApiTests(unittest.TestCase):
    def setUp(self):
        self.fx=ProjectFixture(); (self.fx.data/"admin_token.txt").write_text("admin-token\n")
        self.fx.write_json("active_universe.json",{"companies":[]}); self.fx.write_json("live.json",{"items":{}}); self.fx.write_json("live_status.json",{})
        (self.fx.base/"index.html").write_text("dashboard")
        self.old=(app.BASE,app.DATA,app.ADMIN_TOKEN_PATH); app.BASE=self.fx.base; app.DATA=self.fx.data; app.ADMIN_TOKEN_PATH=self.fx.data/"admin_token.txt"; app.reset_admin_runtime()
        self.server=app.FastThreadingHTTPServer(("127.0.0.1",0),app.AppHandler); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start(); self.url=f"http://127.0.0.1:{self.server.server_port}"
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); app.BASE,app.DATA,app.ADMIN_TOKEN_PATH=self.old; app.reset_admin_runtime(); self.fx.close()
    def request(self,method,path,body=None,admin=False,user_token=None):
        headers={"Content-Type":"application/json"}
        if admin: headers["X-AICM-Admin-Token"]="admin-token"
        if user_token: headers["Authorization"]="Bearer "+user_token
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        try:
            res=urllib.request.urlopen(req,timeout=5); return res.status,json.loads(res.read())
        except urllib.error.HTTPError as exc: return exc.code,json.loads(exc.read())

    def raw_request(self,method,path,body=None,content_type=None,admin=False,user_token=None):
        headers={}
        if content_type: headers["Content-Type"]=content_type
        if admin: headers["X-AICM-Admin-Token"]="admin-token"
        if user_token: headers["Authorization"]="Bearer "+user_token
        req=urllib.request.Request(self.url+path,data=body,headers=headers,method=method)
        try:
            res=urllib.request.urlopen(req,timeout=5); return res.status,res.headers,res.read()
        except urllib.error.HTTPError as exc: return exc.code,exc.headers,exc.read()
    def create_user(self,name):
        status,payload=self.request("POST","/api/admin/users",{"display_name":name,"role":"operator"},admin=True); self.assertEqual(status,201); return payload["data"]

    def test_personal_routes_require_valid_token_and_revoke_immediately(self):
        self.assertEqual(self.request("GET","/api/v1/me/profile")[0],403)
        user=self.create_user("User One"); token=user["token"]
        self.assertEqual(self.request("GET","/api/v1/me/profile",user_token=token)[1]["data"]["user_id"],user["user_id"])
        self.assertEqual(self.request("POST",f'/api/admin/users/{user["user_id"]}/revoke',{},admin=True)[0],200)
        self.assertEqual(self.request("GET","/api/v1/me/profile",user_token=token)[0],403)

    def test_admin_auth_failures_are_rate_limited_with_retry_after(self):
        for _ in range(5):
            self.assertEqual(self.raw_request("GET","/api/admin/overview")[0],403)
        status,headers,raw=self.raw_request("GET","/api/admin/overview")
        self.assertEqual(status,429); self.assertGreater(int(headers["Retry-After"]),0)
        self.assertEqual(json.loads(raw)["error"]["code"],"auth_rate_limited")
        self.assertEqual(self.request("GET","/api/admin/overview",admin=True)[0],200)

    def test_two_users_have_isolated_watchlists_and_interfaces(self):
        one=self.create_user("One"); two=self.create_user("Two")
        self.request("POST","/api/v1/me/watchlist/add",{"items":[{"ticker":"NVDA","name":"NVIDIA"}]},user_token=one["token"])
        self.request("POST","/api/v1/me/watchlist/add",{"items":[{"ticker":"AMD","name":"AMD"}]},user_token=two["token"])
        first=self.request("GET","/api/v1/me/watchlist",user_token=one["token"])[1]["data"]
        second=self.request("GET","/api/v1/me/watchlist",user_token=two["token"])[1]["data"]
        self.assertEqual([x["ticker"] for x in first["items"]],["NVDA"]); self.assertEqual([x["ticker"] for x in second["items"]],["AMD"])
        self.request("POST","/api/v1/me/interfaces",{"name":"Private API","provider":"X","category":"provider","purpose":"Test","monitor_mode":"http","method":"GET","url":"https://example.com","timeout_seconds":8,"interval_minutes":15},user_token=one["token"])
        self.assertEqual(self.request("GET","/api/v1/me/interfaces",user_token=one["token"])[1]["data"]["total"],9)
        self.assertEqual(self.request("GET","/api/v1/me/interfaces",user_token=two["token"])[1]["data"]["total"],8)

    def test_admin_user_summary_does_not_include_private_content(self):
        self.create_user("Summary User")
        payload=self.request("GET","/api/admin/users",admin=True)[1]["data"]
        text=json.dumps(payload)
        self.assertNotIn("watchlist",text); self.assertNotIn("interface_registry",text); self.assertGreaterEqual(payload["total"],2)

    def test_interface_crud_manual_test_and_history(self):
        user=self.create_user("Interface Owner"); token=user["token"]
        created=self.request("POST","/api/v1/me/interfaces",{
            "name":"Local check","provider":"Private","category":"task","purpose":"Health",
            "monitor_mode":"local_task","method":"GET","timeout_seconds":8,"interval_minutes":15,
        },user_token=token)
        self.assertEqual(created[0],201); interface_id=created[1]["data"]["id"]
        updated=self.request("PATCH",f"/api/v1/me/interfaces/{interface_id}",{"interval_minutes":30},user_token=token)
        self.assertEqual(updated[0],200); self.assertEqual(updated[1]["data"]["interval_minutes"],30)
        tested=self.request("POST",f"/api/v1/me/interfaces/{interface_id}/test",{},user_token=token)
        self.assertEqual(tested[0],200); self.assertEqual(tested[1]["data"]["status"],"healthy")
        history=self.request("GET",f"/api/v1/me/interfaces/{interface_id}/history",user_token=token)
        self.assertEqual(history[0],200); self.assertEqual(history[1]["data"]["total"],1)
        deleted=self.request("DELETE",f"/api/v1/me/interfaces/{interface_id}",user_token=token)
        self.assertEqual(deleted[0],200)
        self.assertEqual(self.request("DELETE","/api/v1/me/interfaces/web_api",user_token=token)[0],409)

    def test_personal_user_cannot_enable_private_target(self):
        user=self.create_user("Untrusted Probe User")
        status,payload=self.request("POST","/api/v1/me/interfaces",{
            "name":"Internal target","provider":"Private","category":"provider","purpose":"Forbidden",
            "monitor_mode":"http","method":"GET","url":"http://127.0.0.1:8911",
            "allow_private_target":True,"timeout_seconds":8,"interval_minutes":15,
        },user_token=user["token"])
        self.assertEqual(status,403); self.assertEqual(payload["error"]["code"],"private_target_forbidden")

    def test_private_interface_transfer_template_preview_apply_and_export(self):
        user=self.create_user("Transfer User"); token=user["token"]
        status,headers,template=self.raw_request("GET","/api/v1/me/interfaces/template?variant=example",user_token=token)
        self.assertEqual(status,200); self.assertGreater(len(template),1000)
        payload=json.dumps({"interfaces":[{
            "name":"Imported local","provider":"Private","category":"task","purpose":"Import",
            "monitor_mode":"local_task","method":"GET","timeout_seconds":8,"interval_minutes":20,
        }]}).encode()
        quoted=urllib.parse.quote("interfaces.json")
        status,_,raw=self.raw_request("POST",f"/api/v1/me/interfaces/import/preview?filename={quoted}",payload,"application/json",user_token=token)
        self.assertEqual(status,200); preview=json.loads(raw)["data"]
        applied=self.request("POST","/api/v1/me/interfaces/import/apply",{"preview_id":preview["preview_id"]},user_token=token)
        self.assertEqual(applied[0],200); self.assertEqual(applied[1]["data"]["applied"],1)
        status,headers,exported=self.raw_request("GET","/api/v1/me/interfaces/export?format=json",user_token=token)
        self.assertEqual(status,200); self.assertIn(b"Imported local",exported)
        self.assertNotIn(b"credential_value",exported.lower()); self.assertNotIn(b"authorization",exported.lower())

    def test_user_controls_scoped_support_grant_and_personal_audit(self):
        user=self.create_user("Support User"); token=user["token"]
        expires=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)).isoformat()
        created=self.request("POST","/api/v1/me/support-grants",{
            "administrator_id":"owner","scopes":["interfaces"],"reason":"Investigate private interface",
            "expires_at":expires,
        },user_token=token)
        self.assertEqual(created[0],201); grant_id=created[1]["data"]["grant_id"]
        support=self.request("GET",f"/api/admin/support-grants/{grant_id}/profile",admin=True)
        self.assertEqual(support[0],200); self.assertEqual(support[1]["data"]["effective_user_id"],user["user_id"])
        audit=self.request("GET","/api/v1/me/audit",user_token=token)
        self.assertEqual(audit[0],200)
        self.assertTrue(all(row.get("effective_user_id")==user["user_id"] for row in audit[1]["data"]["items"]))
        self.assertEqual(self.request("POST",f"/api/v1/me/support-grants/{grant_id}/revoke",{},user_token=token)[0],200)
        self.assertEqual(self.request("GET",f"/api/admin/support-grants/{grant_id}/profile",admin=True)[0],403)

    def test_monitor_items_are_scoped_and_include_private_callbacks(self):
        user=self.create_user("Scheduled User")
        items=app.private_monitor_items()
        personal=[item for item in items if item["key"][1]==user["user_id"]]
        self.assertEqual(len(personal),8)
        self.assertTrue(all(item["key"][0]==user["tenant_id"] for item in personal))
        self.assertTrue(all(callable(item["operation"]) and callable(item["callback"]) for item in personal))
        self.assertFalse(any(item["key"][1]=="owner" for item in items))

    def test_interface_credentials_are_written_without_secret_echo(self):
        user=self.create_user("Credential User"); token=user["token"]
        created=self.request("POST","/api/v1/me/interfaces",{
            "name":"Secured API","provider":"Private","category":"provider","purpose":"Health",
            "monitor_mode":"http","method":"GET","url":"https://example.com","timeout_seconds":8,"interval_minutes":15,
        },user_token=token)[1]["data"]
        captured={}
        class FakeKeychain:
            def __init__(self,auth): self.auth=auth
            def put(self,interface_id,bundle): captured[interface_id]=bundle; return {"credential_configured":True,"credential_ref":interface_id}
            def delete(self,interface_id): return captured.pop(interface_id,None) is not None
        with patch("app.KeychainStore",FakeKeychain):
            status,payload=self.request("POST",f'/api/v1/me/interfaces/{created["id"]}/credentials',{
                "auth_type":"bearer","token":"do-not-echo-this-secret",
            },user_token=token)
            self.assertEqual(status,200); self.assertTrue(payload["data"]["credential_configured"])
            self.assertNotIn("do-not-echo-this-secret",json.dumps(payload))
            self.assertEqual(captured[created["id"]]["token"],"do-not-echo-this-secret")
            self.assertEqual(self.request("DELETE",f'/api/v1/me/interfaces/{created["id"]}/credentials',user_token=token)[0],200)


if __name__=="__main__": unittest.main()
