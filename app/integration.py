"""Part 4 student implementation."""

import yaml

from app.core import error, validate_request
from app.dispatcher import dispatch_request
from app.request_handler import validate_webex_envelope


def process_request(mentioned_people, attachment_path, bot_person_id):
    # 1) ตรวจ mention + มีไฟล์แนบไหม (Part 1)
    envelope_error = validate_webex_envelope(
        mentioned_people, attachment_path, bot_person_id
    )
    if envelope_error is not None:
        return envelope_error

    # 2) อ่าน YAML ครั้งเดียว
    try:
        with open(attachment_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, UnicodeDecodeError):
        return error("invalid_yaml")

    # 3) ตรวจเนื้อหา YAML (Part 1) ไม่ผ่าน → ตอบ error ไม่แตะ router
    validation = validate_request(data)
    if validation["status"] != "ok":
        return validation

    # 4) ผ่านหมด → ส่งให้ backend ทำงานจริง (Part 3)
    try:
        return dispatch_request(data)
    except Exception:
        return error("backend_failed")   # กัน bot ล้มถ้ามีอะไรไม่คาดคิด