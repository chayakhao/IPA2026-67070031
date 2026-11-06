import os
from ipaddress import IPv4Interface   # ใช้แปลง IP/netmask <-> CIDR

import requests
import urllib3

from app.backends import planning

# router ใช้ self-signed certificate จึงปิดคำเตือน SSL ไว้
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# RESTCONF ใช้ media type เฉพาะของ YANG ไม่ใช่ application/json ธรรมดา
HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}
TIMEOUT = 10   # วินาที ถ้า router ไม่ตอบให้ยอมแพ้ (Live test ให้เวลาแค่ 20 วิ)


def error(code):
    return {"status": "error", "result": code}


def _auth():
    # username/password อ่านจาก environment ห้ามเขียนลงโค้ด (ห้าม commit รหัสผ่าน)
    return (os.environ.get("ROUTER_USER", ""), os.environ.get("ROUTER_PASS", ""))


# ---------- ตัวส่ง HTTP จริง (ใช้เมื่อ test ไม่ได้ส่ง fake มา) ----------
def _real_get(url):
    return requests.get(url, auth=_auth(), headers=HEADERS,
                        verify=False, timeout=TIMEOUT)


def _real_put(url, json):
    return requests.put(url, json=json, auth=_auth(), headers=HEADERS,
                        verify=False, timeout=TIMEOUT)


def _map_exception(exc):
    """แปลง exception เป็น error code ตาม spec"""
    if isinstance(exc, PermissionError):
        return error("authentication_failed")
    # ⚠️ requests.exceptions.ConnectionError ไม่ใช่ลูกของ ConnectionError ของ Python
    #    จึงต้องเขียนแยกทั้งสองแบบ
    if isinstance(exc, (ConnectionError, TimeoutError,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout)):
        return error("connection_failed")
    return error("backend_failed")


# ======================= STATUS (อ่าน) =======================
def get_interface_state(
    router,
    interface_name,
    http_get=None,
):
    """Return normalized interface status using RESTCONF."""
    if http_get is None:
        http_get = _real_get

    # อ่านด้วย YANG model มาตรฐาน ietf-interfaces
    url = (
        f"https://{router}/restconf/data/"
        f"ietf-interfaces:interfaces/interface={interface_name}"
    )

    try:
        resp = http_get(url)                      # HTTP GET
    except Exception as exc:
        return _map_exception(exc)

    # เช็ก status code ก่อนเรียก .json() เสมอ (ตอน 404 ไม่มี body)
    if resp.status_code == 404:                   # ไม่มี interface นี้ (ไม่ใช่ error)
        return {"status": "ok", "result": "not_found", "interface": None}
    if resp.status_code in (401, 403):            # user/pass ผิด หรือไม่มีสิทธิ์
        return error("authentication_failed")
    if resp.status_code != 200:                   # อย่างอื่นถือว่า backend พัง
        return error("backend_failed")

    try:
        raw = resp.json()["ietf-interfaces:interface"]

        # ip + netmask → CIDR เช่น 172.23.123.1 + 255.255.255.255 → 172.23.123.1/32
        addresses = raw.get("ietf-ip:ipv4", {}).get("address", [])
        ipv4 = None
        if addresses:
            first = addresses[0]
            ipv4 = IPv4Interface(f"{first['ip']}/{first['netmask']}").with_prefixlen

        # แปลงเป็นรูปแบบกลาง (normalized) ที่ทุก backend ใช้เหมือนกัน
        interface = {
            "name": raw["name"],
            "ipv4": ipv4,
            "description": raw.get("description"),             # ไม่มี → None
            "admin_state": "up" if raw.get("enabled") else "down",  # enabled true/false
        }
    except Exception:
        return error("backend_failed")            # JSON หน้าตาไม่ตรงที่คาด

    return {"status": "ok", "result": "found", "interface": interface}


# ======================= PLAN =======================
def plan_interface(
    router,
    desired_interface,
    http_get=None,
):
    """Return create/update/no_change plan using RESTCONF status."""
    # ห่อ get_interface_state ให้จำ http_get ไว้ แล้วส่งให้ planning ตัวกลาง
    def status(r, name):
        return get_interface_state(r, name, http_get)

    return planning.plan_interface(router, desired_interface, status)


# ======================= APPLY (เขียน) =======================
def apply_interface(
    router,
    desired_interface,
    http_put=None,
):
    """Apply complete desired interface state using RESTCONF."""
    if http_put is None:
        http_put = _real_put

    # "Loopback123" → 123 (Cisco native model ใช้แค่ตัวเลขเป็น key)
    number = int(desired_interface["name"][len("Loopback"):])
    ipv4 = IPv4Interface(desired_interface["ipv4"])   # แยก IP กับ netmask

    # เขียนด้วย YANG model ของ Cisco: Cisco-IOS-XE-native
    loopback = {
        "name": number,
        "description": desired_interface["description"],
        "ip": {
            "address": {
                "primary": {
                    "address": str(ipv4.ip),        # 172.23.123.1
                    "mask": str(ipv4.netmask),      # 255.255.255.255
                }
            }
        },
    }
    # down → ใส่ shutdown, up → ไม่ใส่ (ไม่มี shutdown = เปิดอยู่)
    if desired_interface["admin_state"] == "down":
        loopback["shutdown"] = [None]

    url = (
        f"https://{router}/restconf/data/"
        f"Cisco-IOS-XE-native:native/interface/Loopback={number}"
    )
    payload = {"Cisco-IOS-XE-native:Loopback": loopback}

    try:
        # PUT = แทนที่ทั้งก้อน → apply ซ้ำได้ผลเหมือนเดิม (idempotent)
        resp = http_put(url, json=payload)
    except Exception as exc:
        return _map_exception(exc)

    if resp.status_code in (200, 201, 204):       # สำเร็จ
        return {"status": "ok", "result": "applied"}
    if resp.status_code in (401, 403):
        return error("authentication_failed")
    return error("backend_failed")
