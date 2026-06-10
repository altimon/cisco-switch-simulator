"""
Broken-scenario task engine.
Each scenario defines how to corrupt state and what checks verify a correct fix.
"""

import random
import copy
import json
from pathlib import Path

SCORES_FILE = Path("/opt/cisco-sim/scores.json")

# ─── Score persistence ────────────────────────────────────────────────────────

def load_scores() -> dict:
    if SCORES_FILE.exists():
        try:
            with open(SCORES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"scenario_counts": {}}


def save_scores(scores: dict):
    SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCORES_FILE, "w") as f:
        json.dump(scores, f, indent=2)


def reset_scores():
    save_scores({"scenario_counts": {}})


# ─── Scenario definitions ─────────────────────────────────────────────────────

SCENARIOS = [
    {
        "id": 1,
        "title": "Laptop in wrong VLAN",
        "description": (
            "A technician reports that the laptop at port Gi1/0/1 cannot reach the\n"
            "SANDBOX_NET network. The device should be on VLAN 400.\n"
            "Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/1", access_vlan=20),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/1)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/1"),
            },
            {
                "label": "Checked VLAN name (show vlan brief)",
                "fn": lambda s, log: _cmd_used(log, "show vlan brief"),
            },
            {
                "label": "Entered config mode",
                "fn": lambda s, log: _cmd_used(log, "configure terminal") or _cmd_used(log, "conf t"),
            },
            {
                "label": "Set correct VLAN (400)",
                "fn": lambda s, log: s["ports"]["Gi1/0/1"]["access_vlan"] == 400,
            },
            {
                "label": "Port is up",
                "fn": lambda s, log: not s["ports"]["Gi1/0/1"]["admin_down"],
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
            {
                "label": "Verified with show command",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status") or _cmd_used(log, "show running-config interface"),
            },
        ],
    },
    {
        "id": 2,
        "title": "PACS workstation on wrong VLAN",
        "description": (
            "The PACS workstation at Gi1/0/2 is currently on VLAN 20 (SERVERS) but\n"
            "must be moved to VLAN 40 (RADIOLOGY) to reach imaging systems.\n"
            "Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/2", access_vlan=20),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/2)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/2"),
            },
            {
                "label": "Entered config mode",
                "fn": lambda s, log: _cmd_used(log, "configure terminal") or _cmd_used(log, "conf t"),
            },
            {
                "label": "Set correct VLAN (40)",
                "fn": lambda s, log: s["ports"]["Gi1/0/2"]["access_vlan"] == 40,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
            {
                "label": "Verified with show command",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status") or _cmd_used(log, "show running-config interface"),
            },
        ],
    },
    {
        "id": 3,
        "title": "Trunk Gi1/0/24 missing VLAN 30",
        "description": (
            "Storage traffic is not reaching the core. The uplink trunk at Gi1/0/24\n"
            "should carry VLANs 10, 20, 30, and 50, but VLAN 30 (STORAGE) is missing.\n"
            "Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/24", trunk_vlans=[10, 20]),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/24)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/24"),
            },
            {
                "label": "Checked trunk config (show interfaces trunk)",
                "fn": lambda s, log: _cmd_used(log, "show interfaces trunk"),
            },
            {
                "label": "Added VLAN 30 to trunk",
                "fn": lambda s, log: 30 in s["ports"]["Gi1/0/24"]["trunk_vlans"],
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
        ],
    },
    {
        "id": 4,
        "title": "NetApp data LIF in wrong VLAN",
        "description": (
            "The NetApp storage appliance at Gi1/0/3 is unreachable from the storage\n"
            "network. It should be on VLAN 30 (STORAGE) but was recently misconfigured.\n"
            "Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/3", access_vlan=20),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/3)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/3"),
            },
            {
                "label": "Set correct VLAN (30)",
                "fn": lambda s, log: s["ports"]["Gi1/0/3"]["access_vlan"] == 30,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
            {
                "label": "Verified with show command",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status") or _cmd_used(log, "show running-config interface"),
            },
        ],
    },
    {
        "id": 5,
        "title": "Port admin down — bring it up",
        "description": (
            "A newly cabled device at Gi1/0/10 is not showing link. The port was\n"
            "previously disabled by an administrator. Bring the port up.\n"
            "The port should remain on VLAN 10 (MGMT)."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/10", admin_down=True, status="admin down"),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/10)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/10"),
            },
            {
                "label": "Ran no shutdown",
                "fn": lambda s, log: _cmd_used(log, "no shutdown"),
            },
            {
                "label": "Port is now up",
                "fn": lambda s, log: not s["ports"]["Gi1/0/10"]["admin_down"],
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
        ],
    },
    {
        "id": 6,
        "title": "Connected port missing description",
        "description": (
            "Port Gi1/0/8 has a device connected but has no description set.\n"
            "Asset management requires a description on all active ports.\n"
            "Add an appropriate description and save the config."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/8", description="", status="connected"),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/8)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/8"),
            },
            {
                "label": "Added description",
                "fn": lambda s, log: len(s["ports"]["Gi1/0/8"].get("description", "")) > 0,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
            {
                "label": "Verified with show interfaces description",
                "fn": lambda s, log: _cmd_used(log, "show interfaces description"),
            },
        ],
    },
    {
        "id": 7,
        "title": "Laptop port: correct VLAN but port is down",
        "description": (
            "Port Gi1/0/1 (laptop) is configured for VLAN 400 but the laptop still\n"
            "cannot reach the network. The port is administratively down.\n"
            "Investigate and bring the port up."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/1", access_vlan=400, admin_down=True, status="admin down"),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/1)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/1"),
            },
            {
                "label": "Checked interfaces status",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status"),
            },
            {
                "label": "Ran no shutdown",
                "fn": lambda s, log: _cmd_used(log, "no shutdown"),
            },
            {
                "label": "Port is now up",
                "fn": lambda s, log: not s["ports"]["Gi1/0/1"]["admin_down"],
            },
            {
                "label": "VLAN remains 400",
                "fn": lambda s, log: s["ports"]["Gi1/0/1"]["access_vlan"] == 400,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
        ],
    },
    {
        "id": 8,
        "title": "VMware trunk missing VLAN 50 (VMOTION)",
        "description": (
            "VMotion traffic is failing on all VMware hosts. The uplink at Gi1/0/4\n"
            "should carry VLANs 10, 20, and 50, but VLAN 50 (VMOTION) was dropped\n"
            "during a recent maintenance window. Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/4", trunk_vlans=[10, 20]),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/4)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/4"),
            },
            {
                "label": "Checked trunk (show interfaces trunk)",
                "fn": lambda s, log: _cmd_used(log, "show interfaces trunk"),
            },
            {
                "label": "Added VLAN 50 to trunk",
                "fn": lambda s, log: 50 in s["ports"]["Gi1/0/4"]["trunk_vlans"],
            },
            {
                "label": "VLANs 10 and 20 still present",
                "fn": lambda s, log: 10 in s["ports"]["Gi1/0/4"]["trunk_vlans"] and 20 in s["ports"]["Gi1/0/4"]["trunk_vlans"],
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
        ],
    },
    {
        "id": 9,
        "title": "Radiology server on wrong VLAN",
        "description": (
            "Radiology workstations cannot reach the imaging server at Gi1/0/5.\n"
            "The server should be on VLAN 40 (RADIOLOGY) but is currently on the\n"
            "SERVERS VLAN. Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/5", access_vlan=20),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/5)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/5"),
            },
            {
                "label": "Entered config mode",
                "fn": lambda s, log: _cmd_used(log, "configure terminal") or _cmd_used(log, "conf t"),
            },
            {
                "label": "Set correct VLAN (40)",
                "fn": lambda s, log: s["ports"]["Gi1/0/5"]["access_vlan"] == 40,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
            {
                "label": "Verified with show command",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status") or _cmd_used(log, "show running-config interface"),
            },
        ],
    },
    {
        "id": 10,
        "title": "Printer on wrong VLAN",
        "description": (
            "Users report the network printer is unreachable. The printer at Gi1/0/6\n"
            "should be on VLAN 10 (MGMT) but was accidentally moved to VLAN 20\n"
            "during a bulk reconfiguration. Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/6", access_vlan=20),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/6)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/6"),
            },
            {
                "label": "Entered config mode",
                "fn": lambda s, log: _cmd_used(log, "configure terminal") or _cmd_used(log, "conf t"),
            },
            {
                "label": "Set correct VLAN (10)",
                "fn": lambda s, log: s["ports"]["Gi1/0/6"]["access_vlan"] == 10,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
            {
                "label": "Verified with show command",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status") or _cmd_used(log, "show running-config interface"),
            },
        ],
    },
    {
        "id": 11,
        "title": "Port misconfigured as trunk instead of access",
        "description": (
            "The PACS workstation at Gi1/0/2 lost connectivity after a config change.\n"
            "The port was accidentally set to trunk mode. It should be an access port\n"
            "on VLAN 20 (SERVERS). Investigate and fix the issue."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/2", mode="trunk", trunk_vlans=[20], access_vlan=1),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/2)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/2"),
            },
            {
                "label": "Checked interfaces status or trunk",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status") or _cmd_used(log, "show interfaces trunk"),
            },
            {
                "label": "Set mode to access",
                "fn": lambda s, log: s["ports"]["Gi1/0/2"]["mode"] == "access",
            },
            {
                "label": "Set correct VLAN (20)",
                "fn": lambda s, log: s["ports"]["Gi1/0/2"]["access_vlan"] == 20,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
        ],
    },
    {
        "id": 12,
        "title": "Sandbox workstation port admin down",
        "description": (
            "A developer cannot reach the sandbox network from their workstation.\n"
            "Port Gi1/0/7 is showing no link. The port appears to have been\n"
            "administratively disabled. Investigate and restore connectivity."
        ),
        "apply": lambda state: _set_port(state, "Gi1/0/7", admin_down=True, status="admin down"),
        "checks": [
            {
                "label": "Found correct port (Gi1/0/7)",
                "fn": lambda s, log: _cmd_used(log, "interface gi1/0/7"),
            },
            {
                "label": "Checked interfaces status",
                "fn": lambda s, log: _cmd_used(log, "show interfaces status"),
            },
            {
                "label": "Ran no shutdown",
                "fn": lambda s, log: _cmd_used(log, "no shutdown"),
            },
            {
                "label": "Port is now up",
                "fn": lambda s, log: not s["ports"]["Gi1/0/7"]["admin_down"],
            },
            {
                "label": "VLAN 400 still set",
                "fn": lambda s, log: s["ports"]["Gi1/0/7"]["access_vlan"] == 400,
            },
            {
                "label": "Saved config",
                "fn": lambda s, log: _cmd_used(log, "write memory") or _cmd_used(log, "copy running-config startup-config"),
            },
        ],
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _set_port(state, port_id, **kwargs):
    for k, v in kwargs.items():
        state["ports"][port_id][k] = v


def _cmd_used(log: list, fragment: str) -> bool:
    fragment = fragment.lower()
    return any(fragment in cmd.lower() for cmd in log)


# ─── Public API ───────────────────────────────────────────────────────────────

def load_next_scenario(state: dict) -> dict:
    """
    Select the least-used scenario (ties broken randomly), apply it to state,
    and increment its usage count.
    """
    scores  = load_scores()
    counts  = scores.get("scenario_counts", {})

    min_count  = min(counts.get(str(sc["id"]), 0) for sc in SCENARIOS)
    candidates = [sc for sc in SCENARIOS if counts.get(str(sc["id"]), 0) == min_count]
    scenario   = random.choice(candidates)

    counts[str(scenario["id"])] = counts.get(str(scenario["id"]), 0) + 1
    scores["scenario_counts"] = counts
    save_scores(scores)

    scenario["apply"](state)
    return scenario


def get_score_summary() -> str:
    scores = load_scores()
    counts = scores.get("scenario_counts", {})
    lines  = ["\nScenario usage counts:", "-" * 40]
    for sc in SCENARIOS:
        sid   = str(sc["id"])
        count = counts.get(sid, 0)
        lines.append(f"  [{count:>2}x]  #{sc['id']:>2}  {sc['title']}")
    total = sum(counts.get(str(sc["id"]), 0) for sc in SCENARIOS)
    lines.append("-" * 40)
    lines.append(f"  Total attempts: {total}")
    return "\r\n".join(lines)


def grade_session(scenario: dict, state: dict, cmd_log: list) -> str:
    checks = scenario["checks"]
    passed = 0
    lines  = []

    lines.append(f"\nGrade report — Scenario: {scenario['title']}")
    lines.append("=" * 60)

    for check in checks:
        ok   = check["fn"](state, cmd_log)
        mark = "[PASS]" if ok else "[FAIL]"
        lines.append(f"  {mark}  {check['label']}")
        if ok:
            passed += 1

    total = len(checks)
    score = int(passed / total * 100)
    lines.append("=" * 60)
    lines.append(f"  Score: {passed}/{total}  ({score}%)")

    if score == 100:
        lines.append("  Result: EXCELLENT — all steps completed correctly.")
    elif score >= 70:
        lines.append("  Result: PASS — review the failed items above.")
    else:
        lines.append("  Result: NEEDS WORK — re-read the scenario and try again.")

    return "\r\n".join(lines)
