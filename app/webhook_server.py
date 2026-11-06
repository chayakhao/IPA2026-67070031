"""Part 4 student implementation."""

import json

import logging

import os

from concurrent.futures import ThreadPoolExecutor

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

from tempfile import TemporaryDirectory

from app.integration import process_request

from app.webex_client import WEBEX_API_BASE, WebexClient

log = logging.getLogger("ipa2026")

MAX_BODY = 1_000_000


class WebexEventHandler:
    """จัดการ event หนึ่งรายการจาก Webex"""

    def __init__(self, client, bot_person_id, processor=None):
        self.client = client
        self.bot_person_id = bot_person_id
        self.processor = processor or process_request

    def handle_event(self, event):
        # สนใจเฉพาะ event "มีข้อความใหม่" ที่มี message ID
        if not isinstance(event, dict):
            return None
        if event.get("resource") != "messages" or event.get("event") != "created":
            return None
        data = event.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            return None
        # ข้อความที่ bot ส่งเอง → ข้าม (กัน bot ตอบตัวเองวนไม่จบ)
        if data.get("personId") == self.bot_person_id:
            return None

        message = self.client.get_message(data["id"])
        if message.get("personId") == self.bot_person_id:   # เช็กซ้ำหลังดึงข้อความ
            return None

        room_id = message.get("roomId")                     # ตอบกลับห้องเดิมเสมอ
        mentioned = message.get("mentionedPeople") or []
        files = message.get("files") or []

        if len(files) > 1:
            # แนบหลายไฟล์ → error ทันที ไม่ดาวน์โหลด
            result = {"status": "error", "result": "multiple_attachments"}
        elif not files:
            # ไม่แนบไฟล์ → ให้ process_request ตอบ no_yaml (หรือ missing_bot_mention)
            result = self.processor(mentioned, None, self.bot_person_id)
        else:
            # แนบ 1 ไฟล์ → ดาวน์โหลดลงโฟลเดอร์ชั่วคราว ประมวลผล แล้วลบทิ้งอัตโนมัติ
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / "request.yaml"
                path.write_bytes(self.client.download_file(files[0]))
                result = self.processor(mentioned, path, self.bot_person_id)

        self.client.send_message(room_id, json.dumps(result))   # ส่งผลเป็น JSON
        return result


def make_http_handler(submit_event):
    """สร้าง HTTP handler: /health กับ /webhook"""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body):
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/health":            # ใช้ให้ Docker healthcheck เรียก
                self._reply(200, {"status": "ok", "result": "healthy"})
            else:
                self._reply(404, {"status": "error", "result": "not_found"})

        def do_POST(self):
            if self.path != "/webhook":
                self._reply(404, {"status": "error", "result": "not_found"})
                return
            invalid = {"status": "error", "result": "invalid_event"}

            # ตรวจขนาด body (1 ถึง 1,000,000 ไบต์)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, invalid)
                return
            if length < 1 or length > MAX_BODY:
                self._reply(400, invalid)
                return

            # body ต้องเป็น JSON object
            try:
                event = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                self._reply(400, invalid)
                return
            if not isinstance(event, dict):
                self._reply(400, invalid)
                return

            submit_event(event)                       # ส่งไปทำเบื้องหลัง
            self._reply(200, {"status": "ok", "result": "accepted"})  # ตอบทันที ไม่รอ router

        def log_message(self, format, *args):
            log.info("%s - %s", self.address_string(), format % args)

    return Handler


def main():
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8000"))

    client = WebexClient()
    bot_person_id = client.get_me()["id"]       # personId ของ bot เรา
    handler = WebexEventHandler(client, bot_person_id)

    # max_workers=1 → ทำทีละคำขอ ไม่ให้การแก้ router ซ้อนกัน
    executor = ThreadPoolExecutor(max_workers=1)

    def safe_handle(event):
        try:
            handler.handle_event(event)
        except Exception:
            log.exception("failed to handle event")   # error ไม่ทำให้ server ล้ม

    def submit_event(event):
        executor.submit(safe_handle, event)

    server = ThreadingHTTPServer((host, port), make_http_handler(submit_event))
    log.info("listening on %s:%s (Webex API %s)", host, port, WEBEX_API_BASE)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        executor.shutdown(wait=False)


if __name__ == "__main__":
    main()