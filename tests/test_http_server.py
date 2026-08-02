import http.client
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch

from app import FastThreadingHTTPServer


class BlockingHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    entered=None
    release=None
    entered_count=0
    entered_lock=threading.Lock()
    def do_GET(self):
        with self.entered_lock:
            type(self).entered_count+=1
            if type(self).entered_count>=4: self.entered.set()
        self.release.wait(5)
        body=b"ok"; self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass


class HttpServerTests(unittest.TestCase):
    def test_http11_keepalive_and_global_concurrency_rejection(self):
        entered=threading.Event(); release=threading.Event(); BlockingHandler.entered=entered; BlockingHandler.release=release; BlockingHandler.entered_count=0
        with patch.dict(os.environ,{"AICM_MAX_CONCURRENCY":"4","AICM_CONNECTION_TIMEOUT":"5"}):
            server=FastThreadingHTTPServer(("127.0.0.1",0),BlockingHandler)
        runner=threading.Thread(target=server.serve_forever,daemon=True); runner.start(); connections=[]
        try:
            for _ in range(4):
                connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5); connection.request("GET","/"); connections.append(connection)
            self.assertTrue(entered.wait(2))
            rejected=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5); rejected.request("GET","/"); response=rejected.getresponse()
            self.assertEqual(response.status,503); self.assertEqual(response.version,11); self.assertEqual(response.getheader("Retry-After"),"2"); response.read(); rejected.close()
            release.set()
            for connection in connections:
                response=connection.getresponse(); self.assertEqual(response.status,200); self.assertEqual(response.version,11); response.read(); connection.close()
        finally:
            release.set(); server.shutdown(); server.server_close(); runner.join(timeout=5)


if __name__ == "__main__": unittest.main()
