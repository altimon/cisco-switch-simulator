#!/usr/bin/env python3
"""
Cisco Switch SSH Simulator
Provides a realistic Cisco IOS CLI over SSH for interview practice.

Usage:
    python3 simulator.py           # start SSH server on port 22
    python3 simulator.py --port 2222
    python3 simulator.py --reset   # restore state.json to defaults and exit
"""

from __future__ import annotations

import asyncio
import asyncssh
import json
import os
import sys
import re
import copy
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tasks as task_engine
import endpoints as ep

# ─── Paths & config ──────────────────────────────────────────────────────────

BASE_DIR       = Path("/opt/cisco-sim")
STATE_FILE     = BASE_DIR / "state.json"
DEFAULT_FILE   = BASE_DIR / "state_default.json"
HOST_KEY_FILE  = BASE_DIR / "ssh_host_key"

log = logging.getLogger("cisco-sim")

# ─── Default initial state (mirrors state.json) ──────────────────────────────

DEFAULT_STATE = {
    "hostname": "cisco-lab-switch",
    "version": "15.2(4)E7",
    "vlans": {
        "1":   "default",
        "10":  "MGMT",
        "20":  "SERVERS",
        "30":  "STORAGE",
        "40":  "RADIOLOGY",
        "50":  "VMOTION",
        "99":  "UNUSED",
        "400": "SANDBOX_NET",
    },
    "ports": {
        "Gi1/0/1":  {"description": "laptop",              "mode": "access", "access_vlan": 20,  "trunk_vlans": [],          "status": "connected",  "admin_down": False},
        "Gi1/0/2":  {"description": "PACS workstation",    "mode": "access", "access_vlan": 20,  "trunk_vlans": [],          "status": "connected",  "admin_down": False},
        "Gi1/0/3":  {"description": "NetApp data LIF",     "mode": "access", "access_vlan": 30,  "trunk_vlans": [],          "status": "connected",  "admin_down": False},
        "Gi1/0/4":  {"description": "VMware host uplink",  "mode": "trunk",  "access_vlan": 1,   "trunk_vlans": [10, 20, 50],"status": "connected",  "admin_down": False},
        "Gi1/0/5":  {"description": "Radiology server",    "mode": "access", "access_vlan": 40,  "trunk_vlans": [],          "status": "connected",  "admin_down": False},
        "Gi1/0/6":  {"description": "printer",             "mode": "access", "access_vlan": 10,  "trunk_vlans": [],          "status": "connected",  "admin_down": False},
        "Gi1/0/7":  {"description": "sandbox workstation", "mode": "access", "access_vlan": 400, "trunk_vlans": [],          "status": "connected",  "admin_down": False},
        "Gi1/0/8":  {"description": "",                    "mode": "access", "access_vlan": 99,  "trunk_vlans": [],          "status": "notconnect", "admin_down": False},
        "Gi1/0/9":  {"description": "unused",              "mode": "access", "access_vlan": 99,  "trunk_vlans": [],          "status": "admin down", "admin_down": True},
        "Gi1/0/10": {"description": "",                    "mode": "access", "access_vlan": 10,  "trunk_vlans": [],          "status": "notconnect", "admin_down": False},
        **{f"Gi1/0/{n}": {"description": "", "mode": "access", "access_vlan": 99, "trunk_vlans": [], "status": "notconnect", "admin_down": False}
           for n in range(11, 24)},
        "Gi1/0/24": {"description": "uplink",              "mode": "trunk",  "access_vlan": 1,   "trunk_vlans": [10, 20, 30],"status": "connected",  "admin_down": False},
    },
    "log_entries": [
        "%SYS-5-CONFIG_I: Configured from console by vty0 (172.16.0.1)",
        "%LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet1/0/9, changed state to down",
        "%LINK-3-UPDOWN: Interface GigabitEthernet1/0/8, changed state to down",
        "%SYS-5-CONFIG_I: Configured from console by vty1 (192.168.1.5)",
    ],
}

PORT_ORDER = [f"Gi1/0/{n}" for n in range(1, 25)]

# ─── Port name normalisation ──────────────────────────────────────────────────

_IF_RE = re.compile(
    r'^(?:gi(?:gabitethernet)?|ge)(\d+/\d+/\d+)$', re.I
)

def _normalise_if(name: str) -> str | None:
    """Convert 'gi1/0/3' or 'GigabitEthernet1/0/3' → 'Gi1/0/3', else None."""
    m = _IF_RE.match(name.strip())
    if m:
        return f"Gi{m.group(1)}"
    return None


def _parse_vlan_list(s: str) -> list[int]:
    """'10,20,30-35,50' → [10, 20, 30, 31, 32, 33, 34, 35, 50]"""
    vlans = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            vlans.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            vlans.append(int(part))
    return sorted(set(vlans))


# ─── State persistence ───────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            log.warning("Could not load state.json: %s — using defaults", e)
    return copy.deepcopy(DEFAULT_STATE)


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def reset_state():
    save_state(copy.deepcopy(DEFAULT_STATE))
    print("State reset to defaults.")


# ─── CLI session ─────────────────────────────────────────────────────────────

MODE_EXEC      = "exec"
MODE_CONFIG    = "config"
MODE_CONFIG_IF = "config-if"


class CiscoSession:
    """Pure CLI logic — no asyncssh inheritance. Driven by handle_client()."""

    def __init__(self, state: dict):
        self._state      = state
        self._mode       = MODE_EXEC
        self._current_if = None
        self._out        = None   # set by attach()
        self._cmd_log: list[str] = []
        self._scenario: dict | None = None
        self.closed      = False  # set True on logout/exit to break process loop

    def attach(self, stdout) -> None:
        self._out = stdout

    # ── Output helpers ──────────────────────────────────────────────────────

    def _write(self, text: str):
        if self._out:
            self._out.write(text)

    def _writeln(self, text: str = ""):
        if self._out:
            self._out.write(text + "\r\n")

    def _prompt(self):
        hn = self._state.get("hostname", "switch")
        if self._mode == MODE_EXEC:
            self._write(f"{hn}#")
        elif self._mode == MODE_CONFIG:
            self._write(f"{hn}(config)#")
        elif self._mode == MODE_CONFIG_IF:
            self._write(f"{hn}(config-if)#")

    # ── Command dispatcher ──────────────────────────────────────────────────

    def _dispatch(self, line: str):
        tokens = line.strip().split()
        if not tokens:
            return
        cmd = tokens[0].lower()

        if self._mode == MODE_EXEC:
            self._exec_mode(line, tokens, cmd)
        elif self._mode == MODE_CONFIG:
            self._config_mode(line, tokens, cmd)
        elif self._mode == MODE_CONFIG_IF:
            self._config_if_mode(line, tokens, cmd)

    # ── EXEC mode ───────────────────────────────────────────────────────────

    def _exec_mode(self, line: str, tokens: list, cmd: str):
        lline = line.lower().strip()

        if cmd in ("enable", "en"):
            pass  # already in privileged exec

        elif lline in ("exit", "quit", "logout"):
            self._writeln("Goodbye.")
            self.closed = True

        elif lline.startswith("configure terminal") or lline.startswith("conf t"):
            self._mode = MODE_CONFIG
            self._writeln("Enter configuration commands, one per line.  End with CNTL/Z.")

        elif lline.startswith("show"):
            self._show(lline, tokens)

        elif lline.startswith("ping"):
            self._ping(tokens)

        elif lline in ("write memory", "write mem", "wr"):
            self._write_memory()

        elif lline in ("copy running-config startup-config", "copy run start"):
            self._write_memory()

        elif lline == "task network":
            self._start_task()

        elif lline == "grade":
            self._grade()

        elif lline == "reset score":
            task_engine.reset_scores()
            self._writeln("Scenario usage counts reset.")

        elif lline in ("reset sim", "reset"):
            self._reset_sim()

        else:
            self._writeln(f"% Unknown command: {tokens[0]}")

    # ── CONFIG mode ─────────────────────────────────────────────────────────

    def _config_mode(self, line: str, tokens: list, cmd: str):
        lline = line.lower().strip()

        if cmd in ("exit", "end") or lline == "\x1a":
            self._mode = MODE_EXEC
            self._writeln()

        elif lline == "end" or lline == "\x1a":
            self._mode = MODE_EXEC

        elif lline.startswith("interface"):
            rest = lline[len("interface"):].strip()
            if_id = _normalise_if(rest)
            if if_id and if_id in self._state["ports"]:
                self._current_if = if_id
                self._mode = MODE_CONFIG_IF
            else:
                self._writeln(f"% Invalid interface: {rest}")

        elif lline.startswith("hostname"):
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                self._state["hostname"] = parts[1]

        elif lline in ("write memory", "write mem", "wr"):
            self._mode = MODE_EXEC
            self._write_memory()

        elif lline in ("copy running-config startup-config", "copy run start"):
            self._mode = MODE_EXEC
            self._write_memory()

        else:
            self._writeln(f"% Unknown command: {tokens[0]}")

    # ── CONFIG-IF mode ───────────────────────────────────────────────────────

    def _config_if_mode(self, line: str, tokens: list, cmd: str):
        lline = line.lower().strip()
        port  = self._state["ports"][self._current_if]

        if lline == "exit":
            self._mode = MODE_CONFIG
            self._current_if = None

        elif lline == "end":
            self._mode = MODE_EXEC
            self._current_if = None

        elif lline.startswith("description"):
            desc = line.strip()[len("description"):].strip()
            port["description"] = desc

        elif lline == "switchport mode access":
            port["mode"] = "access"

        elif lline == "switchport mode trunk":
            port["mode"] = "trunk"

        elif lline.startswith("switchport access vlan"):
            rest = lline[len("switchport access vlan"):].strip()
            if rest.isdigit():
                vlan_id = int(rest)
                if str(vlan_id) in self._state["vlans"]:
                    port["access_vlan"] = vlan_id
                else:
                    self._writeln(f"% VLAN {vlan_id} not in database")
            else:
                self._writeln("% Invalid VLAN number")

        elif lline.startswith("switchport trunk allowed vlan"):
            rest = lline[len("switchport trunk allowed vlan"):].strip()
            if rest.startswith("add "):
                add_list = _parse_vlan_list(rest[4:])
                port["trunk_vlans"] = sorted(set(port["trunk_vlans"]) | set(add_list))
            elif rest.startswith("remove "):
                rm_list = _parse_vlan_list(rest[7:])
                port["trunk_vlans"] = sorted(set(port["trunk_vlans"]) - set(rm_list))
            else:
                port["trunk_vlans"] = _parse_vlan_list(rest)

        elif lline == "shutdown":
            port["admin_down"] = True
            port["status"]     = "admin down"
            self._writeln(f"%LINK-5-CHANGED: Interface {self._current_if}, changed state to administratively down")

        elif lline == "no shutdown":
            port["admin_down"] = False
            if port["status"] == "admin down":
                port["status"] = "connected"
            self._writeln(f"%LINK-3-UPDOWN: Interface {self._current_if}, changed state to up")
            self._writeln(f"%LINEPROTO-5-UPDOWN: Line protocol on Interface {self._current_if}, changed state to up")

        elif lline in ("write memory", "write mem", "wr"):
            self._mode = MODE_EXEC
            self._current_if = None
            self._write_memory()

        elif lline in ("copy running-config startup-config", "copy run start"):
            self._mode = MODE_EXEC
            self._current_if = None
            self._write_memory()

        else:
            self._writeln(f"% Unknown command: {tokens[0]}")

    # ── Show commands ────────────────────────────────────────────────────────

    def _show(self, lline: str, tokens: list):
        if lline == "show vlan brief" or lline == "show vlan":
            self._show_vlan_brief()
        elif lline == "show interfaces status":
            self._show_interfaces_status()
        elif lline == "show interfaces description":
            self._show_interfaces_description()
        elif lline == "show interfaces trunk":
            self._show_interfaces_trunk()
        elif lline.startswith("show running-config interface") or lline.startswith("show run int"):
            rest = re.sub(r'^show\s+r\S*\s+int\S*\s*', '', lline).strip()
            if_id = _normalise_if(rest) if rest else None
            self._show_running_config_if(if_id)
        elif lline in ("show running-config", "show run"):
            self._show_running_config()
        elif lline == "show mac address-table":
            self._show_mac_table()
        elif lline == "show version":
            self._show_version()
        elif lline == "show ip interface brief" or lline == "show ip int brief":
            self._show_ip_int_brief()
        elif lline == "show cdp neighbors":
            self._show_cdp_neighbors()
        elif lline == "show logging":
            self._show_logging()
        elif lline == "show score":
            self._writeln(task_engine.get_score_summary())
        else:
            self._writeln(f"% Unknown show command: {lline}")

    def _show_vlan_brief(self):
        state = self._state
        lines = [
            "VLAN Name                             Status    Ports",
            "---- -------------------------------- --------- -------------------------------",
        ]
        for vid_str in sorted(state["vlans"], key=lambda x: int(x)):
            vname  = state["vlans"][vid_str]
            vid    = int(vid_str)
            status = "active"
            ports  = []
            for pid in PORT_ORDER:
                p = state["ports"].get(pid, {})
                if p.get("mode") == "access" and p.get("access_vlan") == vid and not p.get("admin_down"):
                    ports.append(pid)
            port_str = ", ".join(ports)
            lines.append(f"{vid:<4} {vname:<32} {status:<9} {port_str}")
        self._writeln("\r\n".join(lines))

    def _show_interfaces_status(self):
        lines = [
            "Port      Name               Status       Vlan       Duplex  Speed Type",
        ]
        for pid in PORT_ORDER:
            p    = self._state["ports"][pid]
            name = (p["description"] or "")[:18]
            st   = "admin down" if p["admin_down"] else p["status"]
            if p["mode"] == "trunk":
                vlan = "trunk"
            else:
                vlan = str(p["access_vlan"])
            lines.append(
                f"{pid:<10}{name:<19}{st:<13}{vlan:<11}a-full  a-1G  10/100/1000BaseTX"
            )
        self._writeln("\r\n".join(lines))

    def _show_interfaces_description(self):
        lines = [
            "Interface                      Status         Protocol Description",
        ]
        for pid in PORT_ORDER:
            p      = self._state["ports"][pid]
            status = "admin down" if p["admin_down"] else p["status"]
            proto  = "down" if (p["admin_down"] or p["status"] == "notconnect") else "up"
            desc   = p["description"] or ""
            lines.append(f"{pid:<31}{status:<15}{proto:<9}{desc}")
        self._writeln("\r\n".join(lines))

    def _show_interfaces_trunk(self):
        trunks = [(pid, self._state["ports"][pid])
                  for pid in PORT_ORDER
                  if self._state["ports"][pid]["mode"] == "trunk"]
        if not trunks:
            self._writeln("% No trunk interfaces configured")
            return

        lines = [
            "Port        Mode             Encapsulation  Status        Native vlan",
        ]
        for pid, p in trunks:
            st = "trunking" if not p["admin_down"] else "not-trunking"
            lines.append(f"{pid:<12}{'on':<17}{'802.1q':<15}{st:<14}{'1'}")
        lines.append("")
        lines.append("Port        Vlans allowed on trunk")
        for pid, p in trunks:
            vlans = ",".join(str(v) for v in p["trunk_vlans"]) or "none"
            lines.append(f"{pid:<12}{vlans}")
        lines.append("")
        lines.append("Port        Vlans allowed and active in management domain")
        for pid, p in trunks:
            active = [v for v in p["trunk_vlans"] if str(v) in self._state["vlans"]]
            lines.append(f"{pid:<12}{','.join(str(v) for v in active) or 'none'}")
        self._writeln("\r\n".join(lines))

    def _show_running_config_if(self, if_id: str | None):
        if if_id is None:
            self._writeln("% Specify an interface: show running-config interface Gi1/0/X")
            return
        if if_id not in self._state["ports"]:
            self._writeln(f"% Invalid interface {if_id}")
            return
        p     = self._state["ports"][if_id]
        lines = [
            "!",
            f"interface {if_id}",
        ]
        if p["description"]:
            lines.append(f" description {p['description']}")
        if p["mode"] == "access":
            lines.append(" switchport mode access")
            lines.append(f" switchport access vlan {p['access_vlan']}")
        else:
            lines.append(" switchport mode trunk")
            if p["trunk_vlans"]:
                vlans = ",".join(str(v) for v in p["trunk_vlans"])
                lines.append(f" switchport trunk allowed vlan {vlans}")
        if p["admin_down"]:
            lines.append(" shutdown")
        lines.append("!")
        self._writeln("\r\n".join(lines))

    def _show_running_config(self):
        hn    = self._state["hostname"]
        ver   = self._state.get("version", "15.2(4)E7")
        lines = [
            "!",
            f"! Last configuration change",
            "!",
            f"version {ver}",
            "service timestamps debug datetime msec",
            "service timestamps log datetime msec",
            "!",
            f"hostname {hn}",
            "!",
        ]
        for vid_str in sorted(self._state["vlans"], key=lambda x: int(x)):
            if vid_str == "1":
                continue
            lines += [f"vlan {vid_str}", f" name {self._state['vlans'][vid_str]}", "!"]
        lines.append("!")
        for pid in PORT_ORDER:
            p = self._state["ports"][pid]
            lines.append(f"interface {pid}")
            if p["description"]:
                lines.append(f" description {p['description']}")
            if p["mode"] == "access":
                lines.append(" switchport mode access")
                lines.append(f" switchport access vlan {p['access_vlan']}")
            else:
                lines.append(" switchport mode trunk")
                if p["trunk_vlans"]:
                    vlans = ",".join(str(v) for v in p["trunk_vlans"])
                    lines.append(f" switchport trunk allowed vlan {vlans}")
            if p["admin_down"]:
                lines.append(" shutdown")
            lines.append("!")
        lines += ["!", "end"]
        self._writeln("\r\n".join(lines))

    def _show_mac_table(self):
        lines = [
            "          Mac Address Table",
            "-------------------------------------------",
            "",
            "Vlan    Mac Address       Type        Ports",
            "----    -----------       --------    -----",
        ]
        for mac, entry in ep.MAC_TABLE.items():
            lines.append(f"{entry['vlan']:<8}{mac:<18}DYNAMIC     {entry['port']}")
        lines.append("")
        lines.append(f"Total Mac Addresses for this criterion: {len(ep.MAC_TABLE)}")
        self._writeln("\r\n".join(lines))

    def _show_version(self):
        ver = self._state.get("version", "15.2(4)E7")
        self._writeln(
            f"Cisco IOS Software, Version {ver}, RELEASE SOFTWARE (fc4)\n"
            "Technical Support: http://www.cisco.com/techsupport\n"
            "\n"
            f"cisco WS-C2960X-24TS-L (PowerPC405) processor with 262144K bytes of memory.\n"
            "Processor board ID FCW1234X0AB\n"
            "24 FastEthernet interfaces\n"
            "2 Gigabit Ethernet interfaces\n"
            "63488K bytes of flash-simulated non-volatile configuration memory.\n"
            "\n"
            "Configuration register is 0xF"
        )

    def _show_ip_int_brief(self):
        lines = [
            "Interface              IP-Address      OK? Method Status                Protocol",
            "Vlan1                  unassigned      YES unset  up                    up",
            "Vlan10                 10.10.10.1      YES manual up                    up",
        ]
        for pid in PORT_ORDER:
            p  = self._state["ports"][pid]
            st = "administratively down" if p["admin_down"] else p["status"]
            pr = "down" if (p["admin_down"] or p["status"] in ("notconnect", "admin down")) else "up"
            lines.append(f"{pid:<23}unassigned      YES unset  {st:<22}{pr}")
        self._writeln("\r\n".join(lines))

    def _show_cdp_neighbors(self):
        self._writeln(
            "Capability Codes: R - Router, T - Trans Bridge, B - Source Route Bridge\n"
            "                  S - Switch, H - Host, I - IGMP, r - Repeater\n"
            "\n"
            "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\n"
            "core-sw-01       Gi1/0/24          155            R S     WS-C4507R Gi2/0/1\n"
        )

    def _show_logging(self):
        lines = [
            "Syslog logging: enabled (0 messages dropped, 0 flushes, 0 overruns)",
            "Console logging: level debugging, 128 messages logged",
            "Monitor logging: level debugging, 0 messages logged",
            "Buffer logging:  level debugging, 128 messages logged",
            "",
            "Log Buffer (4096 bytes):",
        ]
        lines += self._state.get("log_entries", [])
        self._writeln("\r\n".join(lines))

    # ── Operational commands ─────────────────────────────────────────────────

    def _ping(self, tokens: list):
        if len(tokens) < 2:
            self._writeln("Usage: ping <hostname-or-ip>")
            return
        target = tokens[1]
        _ok, output = ep.ping(target, self._state)
        self._writeln(output)

    def _write_memory(self):
        save_state(self._state)
        self._writeln(
            "Building configuration...\r\n"
            "[OK]"
        )

    def _start_task(self):
        self._state = load_state()  # fresh copy from disk
        self._cmd_log = []
        self._scenario = task_engine.load_next_scenario(self._state)
        self._writeln(
            "\r\n"
            "╔══════════════════════════════════════════════════╗\r\n"
            f"║  SCENARIO: {self._scenario['title']:<38}║\r\n"
            "╚══════════════════════════════════════════════════╝\r\n"
            "\r\n" +
            self._scenario["description"] + "\r\n"
        )

    def _grade(self):
        if self._scenario is None:
            self._writeln("% No active scenario. Run 'task network' first.")
            return
        report = task_engine.grade_session(self._scenario, self._state, self._cmd_log)
        self._writeln(report)

    def _reset_sim(self):
        reset_state()
        self._state = load_state()
        self._mode  = MODE_EXEC
        self._current_if = None
        self._cmd_log = []
        self._scenario = None
        self._writeln("Simulator reset to factory defaults.")


# ─── SSH Server ───────────────────────────────────────────────────────────────

class CiscoSSHServer(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True   # require password auth

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return password in ("cisco", "Cisco123", "lab", "")


# ─── asyncssh process handler ────────────────────────────────────────────────

def make_process_handler(state: dict):
    """Return an async handler suitable for asyncssh process_factory."""

    async def handle_client(process: asyncssh.SSHServerProcess) -> None:
        session = CiscoSession(state)
        session.attach(process.stdout)

        process.stdout.write(
            "\r\n"
            "cisco-lab-switch IOS Software, Version 15.2(4)E7\r\n"
            "Type 'task network' to start a scenario, 'grade' to evaluate.\r\n"
            "\r\n"
        )
        session._prompt()

        try:
            # asyncssh's line editor delivers complete lines; we process them.
            async for line in process.stdin:
                line = line.rstrip("\r\n")
                if line:
                    session._cmd_log.append(line)
                    session._dispatch(line)
                if session.closed:
                    break
                session._prompt()
        except Exception as e:
            log.error("Session error: %s", e, exc_info=True)
        finally:
            process.exit(0)

    return handle_client


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run_server(port: int, host: str = "0.0.0.0"):
    state = load_state()

    if not HOST_KEY_FILE.exists():
        HOST_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-rsa", comment="cisco-sim")
        key.write_private_key(str(HOST_KEY_FILE))
        os.chmod(str(HOST_KEY_FILE), 0o600)
        log.info("Generated new SSH host key at %s", HOST_KEY_FILE)

    server = await asyncssh.create_server(
        CiscoSSHServer,
        host=host,
        port=port,
        server_host_keys=[str(HOST_KEY_FILE)],
        process_factory=make_process_handler(state),
    )

    log.info("Cisco SSH Simulator listening on %s:%s", host, port)
    async with server:
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description="Cisco Switch SSH Simulator")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--reset", action="store_true", help="Reset state.json to defaults and exit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.reset:
        reset_state()
        sys.exit(0)

    try:
        asyncio.run(run_server(port=args.port, host=args.host))
    except KeyboardInterrupt:
        log.info("Shutting down.")
    except PermissionError:
        log.error("Permission denied binding to port %s. Try --port 2222 or run as root.", args.port)
        sys.exit(1)


if __name__ == "__main__":
    main()
