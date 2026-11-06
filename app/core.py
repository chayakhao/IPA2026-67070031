import yaml

ALLOWED_ROUTERS = {
    "10.0.29.101",
    "10.0.29.102",
    "10.0.29.103",
    "10.0.29.104",
    "10.0.29.105",
}

SUPPORTED_ACTIONS = {
    "restconf": {"status", "plan", "apply"},
    "netconf": {"status", "plan", "apply"},
    "ansible": {"apply"},
    "netmiko-textfsm": {"status", "delete"},
}

def error(code):
    #สร้าง responsed error ตาม response-spec
    return {"status": "error", "result": code}

def validate_request(data):
    """
    Validate one IPA2026 desired-state request.

    Return a response dictionary according to
    specs/response-spec-v1.md.
    """
    if not isinstance(data, dict):
        return error("invalid_yaml")

    version = data.get("version")
    if type(version) is not int or version != 1:
        return error("invalid_version")

    router = data.get("router")
    if router is None:
        return error("missing_router")
    if router not in ALLOWED_ROUTERS:
        return error("invalid_router")

    method = data.get("method")
    if method is None:
        return error("missing_method")
    if method not in SUPPORTED_ACTIONS:
        return error("invalid_method")
   
    action = data.get("action")
    if action is None:
        return error("missing_action")
    if action not in SUPPORTED_ACTIONS[method]:
        return error("invalid_action")

    desired = data.get("desired")
    if not isinstance(desired, dict):
        return error ("missing_desired")

    interface = desired.get("interface")
    if not isinstance(interface, dict):
        return error("missing_interface")

    if not interface.get("name"):
        return error("missing_interface_name")

    if action in {"plan", "apply"}:
        if not interface.get("ipv4"):
            return error("missing_ipv4")
        if not interface.get("description"):
            return error("missing_description")
        admin_state = interface.get("admin_state")
        if admin_state is None:
            return error("missing_admin_state")
        if admin_state not in {"up", "down"}:
            return error("invalid_admin_state")
    
    return {"status": "ok", "result": "accepted"}

def process_request_file(path):
    """
    Read a YAML request file, validate it,
    and return a response dictionary.
    """
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        return error("invalid_yaml")
    return validate_request(data)
