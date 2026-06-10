# Cisco Switch SSH Simulator

A lightweight Cisco IOS CLI simulator for practicing UCXX-style IT Infrastructure interview tasks.

## Quick Start

### 1. Deploy to VM

Copy the project files to your VM, then run the install script as root:

```bash
# Ubuntu
scp -r . root@<vm-ip>:/tmp/cisco-sim-install
ssh root@<vm-ip> "cd /tmp/cisco-sim-install && bash install_ubuntu.sh"

# Rocky Linux
scp -r . root@<vm-ip>:/tmp/cisco-sim-install
ssh root@<vm-ip> "cd /tmp/cisco-sim-install && bash install_rocky.sh"
```

### 2. Connect

```bash
ssh root@<vm-ip>
# Password: cisco

# If legacy KEX is required:
ssh -oKexAlgorithms=+diffie-hellman-group14-sha1 root@<vm-ip>
```

---

## Interview Workflow

```
1.  ssh -oKexAlgorithms=+diffie-hellman-group14-sha1 root@<vm-ip>

2.  show interfaces description       → find port labeled "laptop"
3.  show vlan brief                   → confirm VLAN 400 = SANDBOX_NET
4.  conf t
5.  interface Gi1/0/1
6.  switchport access vlan 400
7.  no shutdown
8.  end
9.  write memory
10. show interfaces status            → verify connected, VLAN 400
11. ping infra-test-laptop.lab        → should succeed
```

---

## Scenario Practice

```
cisco-lab-switch# task network
```

Loads a random broken scenario. Do not read the source for hints.

```
cisco-lab-switch# grade
```

Scores the session against a checklist of required steps.

---

## Supported Commands

### Navigation
```
enable
configure terminal  (or: conf t)
interface Gi1/0/X
exit
end
```

### Show
```
show vlan brief
show interfaces status
show interfaces description
show interfaces trunk
show running-config
show running-config interface Gi1/0/X
show mac address-table
show version
show ip interface brief
show cdp neighbors
show logging
```

### Config (persisted)
```
description <text>
switchport mode access
switchport mode trunk
switchport access vlan <id>
switchport trunk allowed vlan <list>       e.g. 10,20,30
switchport trunk allowed vlan add <list>
switchport trunk allowed vlan remove <list>
shutdown
no shutdown
write memory   (or: copy running-config startup-config)
```

### Utility
```
ping <hostname-or-ip>
task network       # start a broken scenario
grade              # score the current session
reset sim          # restore factory defaults
```

---

## Service Management

```bash
systemctl status  cisco-sim
systemctl restart cisco-sim
systemctl stop    cisco-sim
journalctl -u cisco-sim -f
```

## Reset to Defaults

```bash
# From the CLI:
cisco-lab-switch# reset sim

# From the shell:
python3 /opt/cisco-sim/simulator.py --reset
systemctl restart cisco-sim
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on port 22 | `systemctl start cisco-sim` |
| `kex mismatch` | Add `-oKexAlgorithms=+diffie-hellman-group14-sha1` |
| Auth rejected | Password is `cisco` |
| State not saving | Check `/opt/cisco-sim/` is writable by root |
| Service crashes | `journalctl -u cisco-sim -n 50` |

---

## Files

| File | Purpose |
|---|---|
| `simulator.py` | SSH server + CLI engine |
| `state.json` | Live switch state |
| `state_default.json` | Factory defaults (for reset) |
| `tasks.py` | Scenario definitions + grader |
| `endpoints.py` | Fake ping/MAC table |
| `cisco-sim.service` | systemd unit |
| `install_ubuntu.sh` | Ubuntu deploy script |
| `install_rocky.sh` | Rocky Linux deploy script |
