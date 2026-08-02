# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os

seq = 0

class Tap(BaseHTTPRequestHandler):
    def do_POST(self):
        global seq

        # 1. 收下請求
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # 2. 存成檔案，這包東西太大，印在畫面上根本看不完
        seq += 1
        name = f"captured/request-{seq}.json"
        secret_headers = {"authorization", "x-api-key", "anthropic-oauth-token"}
        saved_headers = {
            k: "<已遮蔽>" if k.lower() in secret_headers else v
            for k, v in self.headers.items()
        }
        record = {"method": self.command, "path": self.path,
                  "headers": saved_headers, "body": json.loads(body)}
        with open(name, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        print("攔到一包，存到", name)

        # 3. 原封不動轉交給真正的 Anthropic
        conn = http.client.HTTPSConnection("api.anthropic.com")
        headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
        conn.request("POST", self.path, body, headers)
        resp = conn.getresponse()

        # 再把回應送回給 Claude Code
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection"):
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.read())

os.makedirs("captured", exist_ok=True)
print("proxy 已啟動，監聽 127.0.0.1:9527，按 Ctrl-C 結束")
ThreadingHTTPServer(("127.0.0.1", 9527), Tap).serve_forever()
