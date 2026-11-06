import json
import os
import subprocess          # ใช้สั่งรันโปรแกรมภายนอก (ansible-playbook)
import tempfile
from ipaddress import IPv4Interface
from pathlib import Path

# ตำแหน่งโฟลเดอร์ ansible/ (ขึ้นไป 2 ชั้นจากไฟล์นี้)
ANSIBLE_DIR = Path(__file__).resolve().parents[2] / "ansible"


def error(code):
    return {"status": "error", "result": code}


def _real_run_playbook(variables):
    """รัน ansible-playbook จริง (ใช้เมื่อ test ไม่ได้ส่ง fake มา)"""
    # ansible_host = IP ของ router ที่จะต่อ
    extra = dict(variables, ansible_host=variables["router"])

    # เขียนตัวแปรลงไฟล์ JSON ชั่วคราว เพื่อให้ shutdown เป็น true/false จริง (ไม่ใช่ string)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(extra, f)
        vars_file = f.name

    env = dict(
        os.environ,
        ANSIBLE_HOST_KEY_CHECKING="False",          # lab: ไม่ตรวจ SSH host key
        ANSIBLE_PERSISTENT_COMMAND_TIMEOUT="15",
        ANSIBLE_PERSISTENT_CONNECT_TIMEOUT="15",
    )
    try:
        proc = subprocess.run(
            [
                "ansible-playbook",
                "-i", str(ANSIBLE_DIR / "inventory.ini"),   # inventory
                str(ANSIBLE_DIR / "apply_interface.yml"),   # playbook
                "-e", f"@{vars_file}",                      # extra vars จากไฟล์
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=18,          # Live test ให้แค่ 20 วิ ต้องจบก่อน
        )
    except subprocess.TimeoutExpired:
        raise ConnectionError("ansible-playbook timed out")
    finally:
        os.unlink(vars_file)     # ลบไฟล์ชั่วคราวทิ้ง

    if proc.returncode == 0:     # exit code 0 = สำเร็จ
        return

    # ล้มเหลว: ดูจากข้อความ output ว่าเป็นปัญหาแบบไหน
    output = (proc.stdout + proc.stderr).lower()
    if "authentication" in output or "permission denied" in output:
        raise PermissionError("authentication failed")
    if "unreachable" in output or "timed out" in output or "timeout" in output:
        raise ConnectionError("router unreachable")
    raise RuntimeError("playbook failed")


def apply_interface(
    router,
    desired_interface,
    run_playbook=None,
):
    """Apply complete desired interface state using Ansible."""
    if run_playbook is None:
        run_playbook = _real_run_playbook

    # แปลง desired state เป็นตัวแปรที่ playbook ใช้ (ชื่อตัวแปรต้องตรงที่ test เช็ก)
    ipv4 = IPv4Interface(desired_interface["ipv4"])
    variables = {
        "router": router,
        "interface_name": desired_interface["name"],
        "description": desired_interface["description"],
        "ipv4_address": str(ipv4.ip),          # 172.23.123.1
        "netmask": str(ipv4.netmask),          # 255.255.255.255
        "shutdown": desired_interface["admin_state"] == "down",   # True/False
    }

    try:
        run_playbook(variables)
    except PermissionError:
        return error("authentication_failed")
    except (ConnectionError, TimeoutError):
        return error("connection_failed")
    except Exception:
        return error("backend_failed")

    return {"status": "ok", "result": "applied"}