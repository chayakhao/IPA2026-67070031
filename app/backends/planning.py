from app.planner import build_plan   # ใช้ planner จาก Part 2 ไม่เขียนซ้ำ


def plan_interface(
    router,
    desired_interface,
    get_interface_state,   # ฟังก์ชันอ่านสถานะ (ของ restconf หรือ netconf ก็ได้)
):
    """Build a Part 2 plan from backend status."""
    # 1) อ่านสถานะปัจจุบันจาก router
    status = get_interface_state(router, desired_interface["name"])

    # 2) ถ้าอ่านไม่สำเร็จ (เช่น connection_failed) ส่ง error กลับไปตามเดิม
    if status["status"] != "ok":
        return status

    # 3) แปลงผลเป็น current: เจอ → dict ของ interface, ไม่เจอ → None
    if status["result"] == "found":
        current = status["interface"]
    else:
        current = None

    # 4) ให้ Part 2 เทียบแล้วตอบ create / update / no_change
    return build_plan(desired_interface, current)
