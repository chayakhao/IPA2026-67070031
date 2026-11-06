"""Part 4 student implementation."""

import os

import requests

WEBEX_API_BASE = 'https://webexapis.com/v1'
TIMEOUT = 10


class WebexClient:
    def __init__(self, token=None, session=None):
        # ไม่ส่ง token มา → อ่านจาก environment (ห้ามเขียน token ลงโค้ด)
        self.token = token or os.environ.get("WEBEX_BOT_TOKEN")
        if not self.token:
            raise PermissionError("WEBEX_BOT_TOKEN is not set")
        # test ส่ง session ปลอมมา / ใช้จริงสร้าง requests.Session
        self.session = session or requests.Session()

    @property
    def headers(self):
        # Webex API ยืนยันตัวตนด้วย Bearer token
        return {"Authorization": f"Bearer {self.token}"}

    def get_message(self, message_id):
        """webhook บอกแค่ ID ข้อความ ต้องไปดึงเนื้อหาเอง"""
        response = self.session.get(
            f"{WEBEX_API_BASE}/messages/{message_id}",
            headers=self.headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()      # 4xx/5xx → โยน exception
        return response.json()

    def download_file(self, file_url):
        """ดาวน์โหลดไฟล์แนบ (ต้องใส่ token ด้วย) คืนเป็น bytes"""
        response = self.session.get(
            file_url,
            headers=self.headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.content

    def send_message(self, room_id, text):
        """ส่งข้อความตอบกลับไปที่ห้อง"""
        response = self.session.post(
            f"{WEBEX_API_BASE}/messages",
            headers=self.headers,
            json={"roomId": room_id, "text": text},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_me(self):
        """ถาม Webex ว่า bot ตัวนี้มี personId อะไร (ใช้ตรวจ mention)"""
        response = self.session.get(
            f"{WEBEX_API_BASE}/people/me",
            headers=self.headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()