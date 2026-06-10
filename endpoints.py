"""
Fake endpoint/ping table.
ping() returns True only when the port conditions are met.
"""

ENDPOINTS = {
    "infra-test-laptop.lab": {
        "ip":       "192.168.52.46",
        "mac":      "aa:bb:cc:dd:ee:01",
        "port":     "Gi1/0/1",
        "vlan_req": 400,
    }
}

MAC_TABLE = {
    "aa:bb:cc:dd:ee:01": {"port": "Gi1/0/1", "vlan": 400},
    "00:50:56:ab:12:34": {"port": "Gi1/0/2", "vlan": 20},
    "00:a0:98:cd:56:78": {"port": "Gi1/0/3", "vlan": 30},
    "00:50:56:00:11:22": {"port": "Gi1/0/4", "vlan": 10},
    "00:0c:29:ef:aa:bb": {"port": "Gi1/0/5", "vlan": 40},
    "b4:b5:b6:b7:b8:b9": {"port": "Gi1/0/6", "vlan": 10},
    "de:ad:be:ef:ca:fe": {"port": "Gi1/0/7", "vlan": 400},
}


def ping(hostname: str, state: dict) -> tuple[bool, str]:
    """
    Simulate a ping. Returns (success, output_text).
    For known endpoints, success requires port to be up, access mode, correct VLAN.
    """
    endpoint = ENDPOINTS.get(hostname)
    if endpoint is None:
        return False, (
            f"Pinging {hostname} ...\n"
            "Request timeout for icmp_seq 0\n"
            "Request timeout for icmp_seq 1\n"
            "Request timeout for icmp_seq 2\n"
            "Request timeout for icmp_seq 3\n"
            "\n"
            f"--- {hostname} ping statistics ---\n"
            "4 packets transmitted, 0 received, 100% packet loss"
        )

    port_id   = endpoint["port"]
    vlan_req  = endpoint["vlan_req"]
    ip        = endpoint["ip"]
    port_cfg  = state["ports"].get(port_id, {})

    up         = not port_cfg.get("admin_down", False) and port_cfg.get("status") == "connected"
    access     = port_cfg.get("mode") == "access"
    right_vlan = port_cfg.get("access_vlan") == vlan_req

    if up and access and right_vlan:
        return True, (
            f"Type escape sequence to abort.\n"
            f"Sending 5, 100-byte ICMP Echos to {ip}, timeout is 2 seconds:\n"
            "!!!!!\n"
            f"Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms"
        )
    else:
        reasons = []
        if not up:
            reasons.append("port is down")
        if not access:
            reasons.append("port is not in access mode")
        if not right_vlan:
            reasons.append(f"port VLAN is {port_cfg.get('access_vlan')}, need {vlan_req}")
        reason_str = "; ".join(reasons)
        return False, (
            f"Type escape sequence to abort.\n"
            f"Sending 5, 100-byte ICMP Echos to {ip}, timeout is 2 seconds:\n"
            ".....\n"
            f"Success rate is 0 percent (0/5)\n"
            f"% Destination unreachable [{reason_str}]"
        )
