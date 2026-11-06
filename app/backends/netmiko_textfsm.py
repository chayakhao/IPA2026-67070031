import os
import socket


def error(code):
    return {"status": "error", "result": code}


NOT_FOUND = {"status": "ok", "result": "not_found", "interface": None}


def _normalize_name(name):
    """แปลงชื่อย่อ เช่น 'Lo123' → 'Loopback123' เพื่อให้จับคู่ได้"""
    name = (name or "").strip()
    if name.startswith("Lo") and not name.startswith("Loopback"):
        return "Loopback" + name[2:]
    return name


# ---------- ส่วนต่อ router จริง (ใช้เมื่อ test ไม่ได้ส่ง fake มา) ----------
def _connect(router):
    from netmiko import ConnectHandler

    return ConnectHandler(
        device_type="cisco_ios",      # บอก Netmiko ว่าเป็น Cisco IOS (รู้ prompt, คำสั่งเข้า config)
        host=router,
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        timeout=10,
    )


def _read(conn, interface_name):
    """รัน 2 คำสั่ง show แล้วให้ TextFSM แปลงเป็น list ของ dict"""
    from netmiko.utilities import get_structured_data_textfsm

    raw = conn.send_command(f"show ip interface {interface_name}")
    # interface ไม่มีอยู่ → IOS ตอบ "% Invalid input detected at '^' marker."
    # ห้ามส่งข้อความนี้เข้า TextFSM เพราะ template จะ error
    if "Invalid input" in raw or raw.strip().startswith("%"):
        ip_info = []
    else:
        ip_info = get_structured_data_textfsm(
            raw, platform="cisco_ios", command="show ip interface"
        )

    descriptions = conn.send_command("show interfaces description", use_textfsm=True)
    if not isinstance(ip_info, list):
        ip_info = []
    if not isinstance(descriptions, list):
        descriptions = []
    return ip_info, descriptions


def _real_netmiko_get(router, interface_name):
    with _connect(router) as conn:
        return _read(conn, interface_name)


def _real_netmiko_delete(router, interface_name):
    with _connect(router) as conn:
        # 1) เช็กก่อนว่ามีไหม ไม่มี → not_found (ลบซ้ำก็ไม่พัง = idempotent)
        if _parse(interface_name, *_read(conn, interface_name)) is None:
            return "not_found"
        # 2) เข้า config mode แล้วสั่งลบ (send_config_set จัดการ conf t / end ให้)
        conn.send_config_set([f"no interface {interface_name}"])
        # 3) ตรวจซ้ำว่าหายไปจริงไหม
        if _parse(interface_name, *_read(conn, interface_name)) is not None:
            raise RuntimeError("interface still present after delete")
        return "deleted"


def _map_exception(exc):
    """แปลง exception เป็น error code ตาม spec"""
    if isinstance(exc, PermissionError):
        return error("authentication_failed")
    if isinstance(exc, (ConnectionError, TimeoutError, socket.error)):
        return error("connection_failed")
    try:
        from netmiko.exceptions import (
            NetmikoAuthenticationException,   # user/pass ผิด
            NetmikoTimeoutException,          # ต่อไม่ติด / หมดเวลา
        )

        if isinstance(exc, NetmikoAuthenticationException):
            return error("authentication_failed")
        if isinstance(exc, NetmikoTimeoutException):
            return error("connection_failed")
    except ImportError:
        pass
    return error("backend_failed")


def _parse(interface_name, ip_info, descriptions):
    """แปลงผล TextFSM เป็นรูปแบบกลาง (normalized) ถ้าไม่เจอคืน None"""
    # หา record ของ interface ที่ต้องการจาก show ip interface
    record = None
    for row in ip_info:
        if _normalize_name(row.get("interface")) == interface_name:
            record = row
            break
    if record is None:
        return None

    # IP กับ prefix เป็น list (interface มีได้หลาย IP) ใช้ตัวแรก
    ipv4 = None
    addresses = record.get("ip_address") or []
    prefixes = record.get("prefix_length") or []
    if addresses and prefixes:
        ipv4 = f"{addresses[0]}/{prefixes[0]}"

    # description มาจากอีกคำสั่ง ต้องจับคู่ชื่อย่อ Lo123 ↔ Loopback123
    description = None
    for row in descriptions:
        if _normalize_name(row.get("port")) == interface_name:
            description = row.get("description") or row.get("descrip") or None
            break

    link = (record.get("link_status") or "").lower()
    return {
        "name": interface_name,
        "ipv4": ipv4,
        "description": description,
        "admin_state": "down" if "administratively down" in link else "up",
    }


# ======================= STATUS =======================
def get_interface_state(
    router,
    interface_name,
    netmiko_get=None,
):
    """Return normalized interface status using Netmiko + TextFSM."""
    if netmiko_get is None:
        netmiko_get = _real_netmiko_get

    try:
        ip_info, descriptions = netmiko_get(router, interface_name)
    except Exception as exc:
        return _map_exception(exc)

    try:
        interface = _parse(interface_name, ip_info, descriptions)
    except Exception:
        return error("backend_failed")   # parse พัง

    if interface is None:
        return dict(NOT_FOUND)
    return {"status": "ok", "result": "found", "interface": interface}


# ======================= DELETE =======================
def delete_interface(
    router,
    interface_name,
    netmiko_delete=None,
):
    """Delete an interface using Netmiko."""
    if netmiko_delete is None:
        netmiko_delete = _real_netmiko_delete

    try:
        result = netmiko_delete(router, interface_name)
    except Exception as exc:
        return _map_exception(exc)

    if result == "deleted":
        return {"status": "ok", "result": "deleted"}
    if result == "not_found":
        return dict(NOT_FOUND)
    return error("backend_failed")
