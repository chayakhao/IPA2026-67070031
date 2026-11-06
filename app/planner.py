MANAGED_FIELDS = ("ipv4", "description", "admin_state")


def planned(operation, changes):
    return {
        "status": "ok",
        "result": "planned",
        "operation": operation,
        "changes": changes,
    }


def build_plan(desired_interface, current_interface):
    """
    Compare desired interface state with normalized current state
    and return a plan according to specs/plan-spec-v1.md.
    """
    # กรณี 1: ยังไม่มี interface → create
    if current_interface is None:
        changes = {}
        for field in MANAGED_FIELDS:
            changes[field] = {"to": desired_interface[field]}
        return planned("create", changes)

    # กรณี 2 และ 3: มีอยู่แล้ว → เทียบทีละฟิลด์
    changes = {}
    for field in MANAGED_FIELDS:
        want = desired_interface.get(field)
        have = current_interface.get(field)
        if want != have:                                 
            changes[field] = {"from": have, "to": want}

    if changes:
        return planned("update", changes)              
    return planned("no_change", {})