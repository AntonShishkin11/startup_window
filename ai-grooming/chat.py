"""Run the public-facing demo chat separately from the n8n editor."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PAGE = Path(__file__).with_name("channel") / "index.html"


def handler_for(webhook):
    class Chat(BaseHTTPRequestHandler):
        def reply(self, status, data, kind="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def error(self, status, text):
            self.reply(status, json.dumps({"error": text}, ensure_ascii=False).encode())

        def do_GET(self):
            if self.path == "/":
                self.reply(200, PAGE.read_bytes(), "text/html")
            else:
                self.error(404, "Страница не найдена.")

        def do_POST(self):
            if self.path != "/ask":
                self.error(404, "Страница не найдена.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= 4096:
                self.error(400, "Некорректный размер запроса.")
                return
            request = Request(webhook, data=self.rfile.read(length), method="POST",
                              headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=195) as response:
                    self.reply(response.status, response.read())
            except HTTPError as exc:
                with exc:
                    self.reply(exc.code, exc.read())
            except (TimeoutError, URLError):
                self.error(502, "Не удалось связаться с n8n. Проверьте, что workflow опубликован.")

    return Chat


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--webhook", default="http://127.0.0.1:5678/webhook/grooming-ai")
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args.webhook)) as server:
        print(f"Чат: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
