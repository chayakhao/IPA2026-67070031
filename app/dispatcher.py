"""Part 4 student implementation."""

from app.backends import ansible, netconf, netmiko_textfsm, restconf

# method → โมดูล backend
BACKENDS = {
    "restconf": restconf,
    "netconf": netconf,
    "ansible": ansible,
    "netmiko-textfsm": netmiko_textfsm,
}

# action → ชื่อฟังก์ชันในโมดูล backend
FUNCTIONS = {
    "status": "get_interface_state",
    "plan": "plan_interface",
    "apply": "apply_interface",
    "delete": "delete_interface",
}


def dispatch_request(data):
    """รับ request ที่ผ่านการตรวจแล้ว แล้วส่งต่อให้ backend ที่ถูกต้อง"""
    router = data["router"]
    action = data["action"]
    interface = data["desired"]["interface"]

    backend = BACKENDS[data["method"]]
    # getattr(โมดูล, "ชื่อ") = ดึงฟังก์ชันตามชื่อที่เป็น string
    function = getattr(backend, FUNCTIONS[action])

    # status/delete ใช้แค่ชื่อ interface, plan/apply ใช้ข้อมูลครบทั้ง dict
    if action in ("status", "delete"):
        return function(router, interface["name"])
    return function(router, interface)