import os
import socket
import xml.etree.ElementTree as ET          # ใช้อ่าน (parse) XML ที่ router ตอบกลับมา
from ipaddress import IPv4Interface
from xml.sax.saxutils import escape         # กันอักขระพิเศษ เช่น < & ใน XML

from app.backends import planning

# namespace ของ XML: บอกว่าแท็กนี้มาจาก YANG model ไหน
IF_NS = "urn:ietf:params:xml:ns:yang:ietf-interfaces"      # อ่าน: ietf-interfaces
IP_NS = "urn:ietf:params:xml:ns:yang:ietf-ip"              # อ่าน: ietf-ip
NC_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"          # ของ NETCONF เอง
NATIVE_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-native" # เขียน: Cisco native


def error(code):
    return {"status": "error", "result": code}


# ---------- ตัวเชื่อมต่อจริง (ใช้เมื่อ test ไม่ได้ส่ง fake มา) ----------
def _connect(router):
    from ncclient import manager   # import ในฟังก์ชัน → test รันได้แม้ไม่ได้ต่อ router

    return manager.connect(
        host=router,
        port=830,                          # NETCONF over SSH ใช้ port 830
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        hostkey_verify=False,              # lab: ไม่ตรวจ SSH host key
        device_params={"name": "iosxe"},   # บอกว่าเป็นอุปกรณ์ Cisco IOS XE
        timeout=10,
    )


def _real_get_config(router, filter_xml):
    with _connect(router) as m:            # with = ปิด session ให้อัตโนมัติ
        # อ่าน config จาก datastore "running" เฉพาะส่วนที่ filter ระบุ
        return m.get_config(source="running", filter=("subtree", filter_xml)).data_xml


def _real_edit_config(router, config_xml):
    with _connect(router) as m:
        # เขียน config ลง datastore "running" (มีผลทันที)
        m.edit_config(target="running", config=config_xml)


def _map_exception(exc):
    """แปลง exception เป็น error code ตาม spec"""
    if isinstance(exc, PermissionError):
        return error("authentication_failed")
    if isinstance(exc, (ConnectionError, TimeoutError, socket.error)):
        return error("connection_failed")
    try:
        # exception ของ ncclient จริง
        from ncclient.transport.errors import AuthenticationError, SSHError, TransportError
        from ncclient.operations.errors import TimeoutExpiredError

        if isinstance(exc, AuthenticationError):
            return error("authentication_failed")
        if isinstance(exc, (SSHError, TransportError, TimeoutExpiredError)):
            return error("connection_failed")
    except ImportError:
        pass
    return error("backend_failed")   # อย่างอื่น เช่น RPCError → backend_failed


# ======================= STATUS (อ่าน) =======================
def get_interface_state(
    router,
    interface_name,
    netconf_get_config=None,
):
    """Return normalized interface status using NETCONF."""
    if netconf_get_config is None:
        netconf_get_config = _real_get_config

    # subtree filter: "ขอเฉพาะ interface ที่ชื่อนี้"
    filter_xml = (
        f'<interfaces xmlns="{IF_NS}">'
        f"<interface><name>{escape(interface_name)}</name></interface>"
        f"</interfaces>"
    )

    try:
        reply = netconf_get_config(router, filter_xml)
    except Exception as exc:
        return _map_exception(exc)

    try:
        root = ET.fromstring(reply)

        # หา <interface> ที่ <name> ตรงกับชื่อที่ต้องการ
        # {namespace}tag คือวิธีอ้างแท็กที่มี namespace ใน ElementTree
        found = None
        for intf in root.iter(f"{{{IF_NS}}}interface"):
            if intf.findtext(f"{{{IF_NS}}}name", "").strip() == interface_name:
                found = intf
                break

        # NETCONF ไม่มี 404: ถ้าไม่เจอ router จะตอบ <data> ว่างๆ กลับมา
        if found is None:
            return {"status": "ok", "result": "not_found", "interface": None}

        # ip + netmask → CIDR (ใช้ namespace ietf-ip)
        ipv4 = None
        address = found.find(f"{{{IP_NS}}}ipv4/{{{IP_NS}}}address")
        if address is not None:
            ip = address.findtext(f"{{{IP_NS}}}ip", "").strip()
            mask = address.findtext(f"{{{IP_NS}}}netmask", "").strip()
            ipv4 = IPv4Interface(f"{ip}/{mask}").with_prefixlen

        description = found.findtext(f"{{{IF_NS}}}description")
        enabled = found.findtext(f"{{{IF_NS}}}enabled", "true").strip()  # XML เป็น text "true"/"false"

        interface = {
            "name": interface_name,
            "ipv4": ipv4,
            "description": description.strip() if description is not None else None,
            "admin_state": "up" if enabled == "true" else "down",
        }
    except Exception:
        return error("backend_failed")   # XML พังหรือหน้าตาไม่ตรงที่คาด

    return {"status": "ok", "result": "found", "interface": interface}


# ======================= PLAN =======================
def plan_interface(
    router,
    desired_interface,
    netconf_get_config=None,
):
    """Return create/update/no_change plan using NETCONF status."""
    def status(r, name):
        return get_interface_state(r, name, netconf_get_config)

    return planning.plan_interface(router, desired_interface, status)


# ======================= APPLY (เขียน) =======================
def apply_interface(
    router,
    desired_interface,
    netconf_edit_config=None,
):
    """Apply complete desired interface state using NETCONF."""
    if netconf_edit_config is None:
        netconf_edit_config = _real_edit_config

    number = desired_interface["name"][len("Loopback"):]     # "Loopback123" → "123"
    ipv4 = IPv4Interface(desired_interface["ipv4"])
    # down → ใส่แท็ก <shutdown/>, up → ไม่ใส่
    shutdown = "<shutdown/>" if desired_interface["admin_state"] == "down" else ""

    # nc:operation="replace" → แทนที่ Loopback ทั้งก้อน (เหมือน PUT ของ RESTCONF)
    config_xml = f"""
<config xmlns="{NC_NS}" xmlns:nc="{NC_NS}">
  <native xmlns="{NATIVE_NS}">
    <interface>
      <Loopback nc:operation="replace">
        <name>{number}</name>
        <description>{escape(desired_interface["description"])}</description>
        <ip>
          <address>
            <primary>
              <address>{ipv4.ip}</address>
              <mask>{ipv4.netmask}</mask>
            </primary>
          </address>
        </ip>
        {shutdown}
      </Loopback>
    </interface>
  </native>
</config>"""

    try:
        netconf_edit_config(router, config_xml)
    except Exception as exc:
        return _map_exception(exc)

    return {"status": "ok", "result": "applied"}
