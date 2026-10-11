#!/usr/bin/env python3
"""
Casting Diagnostic Suite (cast-diag)
Enterprise & Education Network Verification Tool for Screen Casting & Wireless Presentation.

Supported Ecosystems:
- Enterprise Gateways: Airtame, Barco ClickShare, Crestron AirMedia, Mersive Solstice, ScreenBeam, Kramer VIA
- Education / Classroom IFPs: SMART Board (iQ), Promethean ActivPanel, Newline Interactive, ViewSonic ViewBoard, Clevertouch
- Consumer / Standard: Apple TV (AirPlay 2), Google Cast / Chromecast, Miracast (MS-MICE), DLNA/UPnP/DIAL

Licensed under GNU AGPLv3.
"""

import argparse
import select
import socket
import struct
import sys
import time

# ==============================================================================
# Protocol & Multicast Constants
# ==============================================================================
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900

CAST_SERVICES = {
    # Native Standards
    "_display._tcp.local": {
        "name": "Miracast over Infrastructure (MS-MICE)",
        "category": "Native Windows",
        "desc": "Windows 10/11 native screen mirroring",
    },
    "_airplay._tcp.local": {
        "name": "Apple AirPlay Mirroring",
        "category": "Native Apple",
        "desc": "Apple TV, macOS, iOS screen mirroring",
    },
    "_raop._tcp.local": {
        "name": "Apple AirPlay Audio (RAOP)",
        "category": "Native Apple",
        "desc": "Remote Audio Output Protocol",
    },
    "_googlecast._tcp.local": {
        "name": "Google Cast / Chromecast",
        "category": "Native Google",
        "desc": "Chromecast, Chrome browser, ChromeOS discovery",
    },
    # Enterprise Wireless Presentation
    "_airtame._tcp.local": {
        "name": "Airtame Discovery",
        "category": "Enterprise Gateway",
        "desc": "Airtame 2 & Airtame Hub status & beacon",
    },
    "_clickshare._tcp.local": {
        "name": "Barco ClickShare",
        "category": "Enterprise Gateway",
        "desc": "Barco ClickShare base units (C/CX series)",
    },
    "_solstice._tcp.local": {
        "name": "Mersive Solstice Pod",
        "category": "Enterprise Gateway",
        "desc": "Mersive Solstice collaboration displays",
    },
    "_crestron._tcp.local": {
        "name": "Crestron AirMedia",
        "category": "Enterprise Gateway",
        "desc": "Crestron AM series presentation gateways",
    },
    # Classroom Interactive Flat Panels (IFP)
    "_smart-iq._tcp.local": {
        "name": "SMART Board (iQ / SMART Mirror)",
        "category": "Classroom IFP",
        "desc": "SMART Technologies interactive displays",
    },
    "_promethean._tcp.local": {
        "name": "Promethean ActivPanel",
        "category": "Classroom IFP",
        "desc": "Promethean Screen Share service",
    },
    "_newline._tcp.local": {
        "name": "Newline Interactive Cast",
        "category": "Classroom IFP",
        "desc": "Newline Cast / DisplayCast wireless receiver",
    },
    "_vcast._tcp.local": {
        "name": "ViewSonic vCastReceiver",
        "category": "Classroom IFP",
        "desc": "ViewSonic ViewBoard vCast screen sharing",
    },
    # Consumer & Smart TV Casting
    "_amzn-wplay._tcp.local": {
        "name": "Amazon Fire TV (Fling / WPlay)",
        "category": "Smart TV / Media Hub",
        "desc": "Amazon Fire TV casting & screen mirroring",
    },
    "_samsung._tcp.local": {
        "name": "Samsung Smart View / Tizen",
        "category": "Smart TV / Media Hub",
        "desc": "Samsung TV screen mirroring discovery",
    },
}

PORT_MATRIX = {
    "Miracast (MS-MICE)": [
        {"port": 7236, "proto": "TCP", "name": "Miracast RTSP Control", "critical": True, "desc": "PIN handshake & session setup"},
        {"port": 7250, "proto": "UDP", "name": "Miracast RTP Video", "critical": False, "desc": "Audio/video RTP stream"},
    ],
    "Apple AirPlay": [
        {"port": 7000, "proto": "TCP", "name": "AirPlay Service Control", "critical": True, "desc": "Stream initiation and setup"},
        {"port": 7100, "proto": "TCP", "name": "AirPlay Mirroring Session", "critical": True, "desc": "H.264 video mirroring channel"},
        {"port": 319, "proto": "UDP", "name": "PTP Event Clock", "critical": False, "desc": "Precision Time Protocol (A/V sync)"},
    ],
    "Google Cast / DIAL": [
        {"port": 8008, "proto": "TCP", "name": "DIAL / Cast HTTP", "critical": True, "desc": "DIAL REST control & 1st gen Cast"},
        {"port": 8009, "proto": "TCP", "name": "Cast TLS Control", "critical": True, "desc": "Encrypted protobuf cast session"},
    ],
    "Airtame": [
        {"port": 80, "proto": "TCP", "name": "Airtame Web / API", "critical": True, "desc": "Device management and pairing fallback"},
        {"port": 443, "proto": "TCP", "name": "Airtame HTTPS", "critical": False, "desc": "TLS management"},
        {"port": 8001, "proto": "TCP", "name": "Airtame Streaming", "critical": False, "desc": "Proprietary desktop streaming channel"},
    ],
    "Classroom IFPs (Newline / SMART / Promethean)": [
        {"port": 8000, "proto": "TCP", "name": "Newline / IFP Mirroring", "critical": False, "desc": "Newline Cast / Clevershare channel"},
        {"port": 8443, "proto": "TCP", "name": "IFP Management Portal", "critical": False, "desc": "Web admin / pairing PIN interface"},
        {"port": 51200, "proto": "TCP", "name": "ScreenBeam / IFP CMS", "critical": False, "desc": "CMS receiver control"},
    ],
    "Enterprise AV (Barco / Crestron / Mersive)": [
        {"port": 8080, "proto": "TCP", "name": "Barco ClickShare Web", "critical": False, "desc": "ClickShare control & streaming"},
        {"port": 41794, "proto": "TCP", "name": "Crestron CIP Control", "critical": False, "desc": "Crestron IP presentation control"},
    ],
    "Legacy DLNA / UPnP": [
        {"port": 2869, "proto": "TCP", "name": "UPnP Eventing (Windows)", "critical": False, "desc": "Windows Media Player 'Cast to Device'"},
        {"port": 5000, "proto": "TCP", "name": "DLNA MediaRenderer", "critical": False, "desc": "UPnP Smart TV renderer endpoint"},
    ],
}

VENDOR_SIGNATURES = [
    {"name": "Airtame (Airtame 2 / Hub)", "keywords": ["airtame", "airtame2", "airtame-hub"]},
    {"name": "Newline Interactive Board", "keywords": ["newline", "newlinecast", "displaycast", "newline-board"]},
    {"name": "SMART Board (iQ / SMART Mirror)", "keywords": ["smarttech", "smart-iq", "smartmirror", "smartboard"]},
    {"name": "Promethean ActivPanel", "keywords": ["promethean", "activpanel", "screenshare"]},
    {"name": "ViewSonic ViewBoard", "keywords": ["viewsonic", "viewboard", "vcast"]},
    {"name": "Barco ClickShare", "keywords": ["clickshare", "barco"]},
    {"name": "Crestron AirMedia", "keywords": ["crestron", "airmedia", "am-100", "am-200", "am-3200"]},
    {"name": "Mersive Solstice", "keywords": ["mersive", "solstice"]},
    {"name": "Apple TV", "keywords": ["appletv", "apple tv", "airplay"]},
    {"name": "Google Chromecast", "keywords": ["chromecast", "google cast", "google home", "eureka"]},
]

# ==============================================================================
# Phase 0: Host System & Environmental Prerequisites Check
# ==============================================================================
def check_prerequisites() -> bool:
    """
    Validates host system capabilities before running diagnostic tests:
    1. Python version (3.7+)
    2. Active network interfaces & IP routing
    3. Multicast socket binding capability
    4. Optional wireless & Bluetooth controllers for discovery
    """
    print("\n" + "=" * 78)
    print("  PHASE 0: PRE-FLIGHT SYSTEM & ENVIRONMENT PREREQUISITES CHECK")
    print("=" * 78)

    passed_all = True

    # 1. Python runtime
    py_ver = sys.version_info
    if py_ver >= (3, 7):
        print(f"  [+] Python Runtime: {py_ver.major}.{py_ver.minor}.{py_ver.micro} (Supported)")
    else:
        print(f"  [!] Python Runtime: {py_ver.major}.{py_ver.minor} (Requires Python 3.7+)")
        passed_all = False

    # 2. Local IP & Gateway Connectivity
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"  [+] Active Network Interface IP: {local_ip} (Active route detected)")
    except Exception as e:
        print(f"  [!] Network Route Error: No active network route detected ({e})")
        passed_all = False

    # 3. UDP Multicast Socket Test
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        test_sock.bind(("", 0))
        test_sock.close()
        print("  [+] Multicast Socket Creation: Success (Permitted by OS / Firewall)")
    except Exception as e:
        print(f"  [!] Multicast Socket Error: Unable to bind UDP sockets ({e})")
        passed_all = False

    # 4. Optional Bluetooth Subsystem (Informational)
    import shutil
    import subprocess
    bt_tool = shutil.which("bluetoothctl")
    if bt_tool:
        try:
            res = subprocess.run([bt_tool, "show"], capture_output=True, timeout=2, text=True)
            if "Powered: yes" in res.stdout:
                print("  [+] Bluetooth Subsystem: Active & Powered ON (BLE proximity discovery available)")
            else:
                print("  [-] Bluetooth Subsystem: Present but Powered OFF (BLE proximity discovery inactive)")
        except Exception:
            print("  [-] Bluetooth Subsystem: Present (Status check timed out)")
    else:
        print("  [-] Bluetooth Subsystem: Not detected (Wi-Fi only testing)")

    print("-" * 78)
    if not passed_all:
        print("  [!] CRITICAL: One or more required prerequisites failed! Exiting.\n")
        return False
    return True

# ==============================================================================
# Helper Functions: mDNS & DNS Packing
# ==============================================================================
def build_dns_query(service_name: str) -> bytes:
    """Builds a raw DNS PTR query for mDNS."""
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    query = bytearray(header)
    for part in service_name.strip(".").split("."):
        query.append(len(part))
        query.extend(part.encode("utf-8"))
    query.append(0)
    query.extend(struct.pack(">HH", 12, 1))
    return bytes(query)

# ==============================================================================
# Phase 1: L2 Multicast & AirGroup/Bonjour Gateway Audit
# ==============================================================================
def run_phase1_discovery(timeout: float = 3.0, monitor_interval: int = 0) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    Audits L2 Multicast (mDNS & SSDP). If monitor_interval > 0, performs an IGMP
    longevity test to detect premature multicast group pruning.
    """
    print("\n" + "=" * 78)
    print("  PHASE 1: L2 MULTICAST & AIRGROUP/BONJOUR GATEWAY AUDIT")
    print("=" * 78)
    print("[*] Transmitting mDNS (UDP 5353) and SSDP (UDP 1900) discovery datagrams...")

    # Set up mDNS socket
    sock_mdns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock_mdns.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock_mdns.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock_mdns.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock_mdns.settimeout(timeout)

    try:
        sock_mdns.bind(("", 0))
    except Exception as e:
        print(f"[-] Could not bind mDNS socket: {e}")
        return {}, []

    # Set up SSDP socket
    sock_ssdp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock_ssdp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock_ssdp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock_ssdp.settimeout(timeout)
    try:
        sock_ssdp.bind(("", 0))
    except Exception as e:
        print(f"[-] Could not bind SSDP socket: {e}")

    # Send mDNS queries
    for service in CAST_SERVICES:
        pkt = build_dns_query(service)
        try:
            sock_mdns.sendto(pkt, (MDNS_GROUP, MDNS_PORT))
        except Exception:
            pass

    # Send SSDP queries across standard search targets (DIAL, UPnP MediaRenderer, Samsung, Fire TV)
    ssdp_search_targets = [
        "urn:dial-multiscreen-org:service:dial:1",
        "urn:schemas-upnp-org:device:MediaRenderer:1",
        "urn:schemas-upnp-org:device:tvdevice:1",
        "urn:samsung.com:device:RemoteControlReceiver:1",
        "upnp:rootdevice",
        "ssdp:all",
    ]
    for st_target in ssdp_search_targets:
        ssdp_req = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            f"ST: {st_target}\r\n\r\n"
        ).encode()
        try:
            sock_ssdp.sendto(ssdp_req, (SSDP_GROUP, SSDP_PORT))
        except Exception:
            pass

    discovered_mdns = {k: [] for k in CAST_SERVICES}
    discovered_ssdp = []
    seen_ssdp_ips = set()

    total_time = monitor_interval if monitor_interval > 0 else timeout
    start_time = time.time()
    last_tick = start_time

    if monitor_interval > 0:
        print(f"[*] Running IGMP Longevity Monitor for {monitor_interval} seconds to test switch snooping/querier...")

    while time.time() - start_time < total_time:
        now = time.time()
        if monitor_interval > 0 and (now - last_tick >= 30):
            elapsed = int(now - start_time)
            print(f"    ... IGMP Monitor alive: {elapsed}/{monitor_interval}s elapsed ...")
            last_tick = now

        remaining = total_time - (now - start_time)
        if remaining <= 0:
            break

        r, _, _ = select.select([sock_mdns, sock_ssdp], [], [], min(remaining, 1.0))
        for ready_sock in r:
            if ready_sock == sock_mdns:
                try:
                    data, addr = sock_mdns.recvfrom(4096)
                    for service in CAST_SERVICES:
                        svc_bytes = service.encode("utf-8")
                        prefix_bytes = service.split(".")[0].encode("utf-8")
                        if svc_bytes in data or prefix_bytes in data:
                            record = {"ip": addr[0], "port": addr[1], "service": service, "raw": data}
                            if not any(x["ip"] == addr[0] and x["service"] == service for x in discovered_mdns[service]):
                                discovered_mdns[service].append(record)
                except Exception:
                    pass
            elif ready_sock == sock_ssdp:
                try:
                    data, addr = sock_ssdp.recvfrom(4096)
                    ip = addr[0]
                    if ip not in seen_ssdp_ips:
                        seen_ssdp_ips.add(ip)
                        text = data.decode("utf-8", errors="replace")
                        srv, st, loc = "", "", ""
                        friendly_name = ""
                        for line in text.splitlines():
                            low = line.lower()
                            if low.startswith("server:"):
                                srv = line.split(":", 1)[1].strip()
                            elif low.startswith("st:"):
                                st = line.split(":", 1)[1].strip()
                            elif low.startswith("location:"):
                                loc = line.split(":", 1)[1].strip()

                        # Attempt fast parse of XML location for friendlyName
                        if loc and loc.startswith("http"):
                            try:
                                import urllib.request
                                import xml.etree.ElementTree as ET
                                with urllib.request.urlopen(loc, timeout=1.0) as resp:
                                    root_xml = ET.fromstring(resp.read(4096))
                                    for elem in root_xml.iter():
                                        if "friendlyName" in elem.tag and elem.text:
                                            friendly_name = elem.text.strip()
                                            break
                            except Exception:
                                pass

                        discovered_ssdp.append({
                            "ip": ip,
                            "server": srv or "Generic UPnP",
                            "st": st or "Unknown",
                            "location": loc,
                            "friendly_name": friendly_name,
                            "raw": text
                        })
                except Exception:
                    pass

    sock_mdns.close()
    sock_ssdp.close()

    # Display findings
    mdns_count = sum(len(v) for v in discovered_mdns.values())
    print("\n--- mDNS Discovery Results (UDP 5353) ---")
    if mdns_count > 0:
        for svc, records in discovered_mdns.items():
            if records:
                meta = CAST_SERVICES[svc]
                print(f"  [+] {meta['name']} ({meta['category']}):")
                for r in records:
                    print(f"      -> {r['ip']}:{r['port']}")
    else:
        print("  [-] No mDNS advertisements received.")
        print("      * Root Cause: Mist/Aruba/Cisco mDNS Gateway disabled, or dropping 224.0.0.251:5353.")

    print("\n--- SSDP / UPnP Discovery Results (UDP 1900) ---")
    if discovered_ssdp:
        for d in discovered_ssdp:
            display_str = f"'{d['friendly_name']}'" if d.get('friendly_name') else d['server']
            print(f"  [+] Device at {d['ip']}: {display_str} (ST: {d['st']})")
    else:
        print("  [-] No SSDP / UPnP responses received.")

    return discovered_mdns, discovered_ssdp

# ==============================================================================
# Phase 2: Vendor Fingerprinting & Ecosystem Identification
# ==============================================================================
def run_phase2_fingerprint(target_ip: str, discovered_mdns: dict[str, list[dict]], discovered_ssdp: list[dict]) -> str:
    """
    Inspects responses and performs banner audits to identify the vendor/device.
    """
    print("\n" + "=" * 78)
    print(f"  PHASE 2: VENDOR FINGERPRINTING & HARDWARE RECOGNITION ({target_ip})")
    print("=" * 78)

    fingerprint_signals = []

    # Check SSDP payloads for this IP
    for s in discovered_ssdp:
        if s["ip"] == target_ip:
            fingerprint_signals.append(s["server"])
            fingerprint_signals.append(s["st"])
            fingerprint_signals.append(s["location"])

    # Check mDNS responses
    for svc, records in discovered_mdns.items():
        for r in records:
            if r["ip"] == target_ip:
                fingerprint_signals.append(svc)
                try:
                    fingerprint_signals.append(r["raw"].decode("utf-8", errors="ignore"))
                except Exception:
                    pass

    # Probe HTTP banner (port 80 / 8008 / 8080) for signature identification
    for port in [80, 8008, 8080, 8443]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((target_ip, port))
            s.sendall(b"GET / HTTP/1.0\r\nHost: target\r\n\r\n")
            banner = s.recv(1024).decode("utf-8", errors="ignore")
            fingerprint_signals.append(banner)
            s.close()
        except Exception:
            pass

    combined_text = " ".join(fingerprint_signals).lower()

    identified_vendor = "Generic / Unknown Display"
    for v in VENDOR_SIGNATURES:
        if any(k in combined_text for k in v["keywords"]):
            identified_vendor = v["name"]
            break

    print(f"  [+] Identified Receiver Ecosystem: >> {identified_vendor} <<")
    if "newline" in combined_text:
        print("      -> Detected Newline Interactive IFP (Newline Cast / DisplayCast).")
    elif "airtame" in combined_text:
        print("      -> Detected Airtame 2 / Airtame Hub.")
    elif "smart" in combined_text:
        print("      -> Detected SMART Technologies Interactive Display (iQ).")
    elif "promethean" in combined_text:
        print("      -> Detected Promethean ActivPanel (Screen Share).")
    elif "clickshare" in combined_text:
        print("      -> Detected Barco ClickShare Base Unit.")
    elif "apple" in combined_text:
        print("      -> Detected Apple TV / AirPlay Receiver.")

    return identified_vendor

# ==============================================================================
# Phase 3: L4 Port & Firewall Inspection
# ==============================================================================
def test_tcp_port(ip: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    start = time.perf_counter()
    try:
        s.connect((ip, port))
        ms = (time.perf_counter() - start) * 1000
        s.close()
        return True, f"{ms:.1f}ms (Open)"
    except TimeoutError:
        return False, "TIMEOUT (Firewall / Filter Dropped)"
    except ConnectionRefusedError:
        return False, "REFUSED (Host up, Port closed)"
    except Exception as e:
        return False, str(e)

def test_udp_port(ip: str, port: int) -> tuple[bool, str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    try:
        s.sendto(b"\x00", (ip, port))
        s.close()
        return True, "SENT (Stateless)"
    except Exception as e:
        return False, str(e)

def run_phase3_ports(target_ip: str):
    print("\n" + "=" * 78)
    print(f"  PHASE 3: L4 PROTOCOL & FIREWALL PERMISSION AUDIT ({target_ip})")
    print("=" * 78)
    print(f"{'Protocol Family':<26} | {'Port':<8} | {'Service':<25} | {'Status':<8} | {'Latency / Detail'}")
    print("-" * 78)

    results = {}
    for group, ports in PORT_MATRIX.items():
        for p in ports:
            port_num = p["port"]
            proto = p["proto"]
            if proto == "TCP":
                ok, detail = test_tcp_port(target_ip, port_num)
                status = "PASS" if ok else "FAIL"
            else:
                ok, detail = test_udp_port(target_ip, port_num)
                status = "PASS" if ok else "WARN"

            results[f"{proto}:{port_num}"] = ok
            print(f"{group:<26} | {proto} {port_num:<4} | {p['name']:<25} | {status:<8} | {detail}")

    print("-" * 78)
    return results

# ==============================================================================
# Phase 4: Root Cause Synthesis & Protocol-Level Remediation
# ==============================================================================
def run_phase4_guidance(target_ip: str, port_results: dict[str, bool], vendor_name: str):
    print("\n" + "=" * 78)
    print("  PHASE 4: ARCHITECTURAL ROOT CAUSE SYNTHESIS & RESOLUTION")
    print("=" * 78)

    rtsp_mice = port_results.get("TCP:7236", False)

    print(f"\n[TARGET ECOSYSTEM]: {vendor_name} ({target_ip})")

    # Issue A: RTSP / Miracast Signaling Blocked
    if not rtsp_mice:
        print("\n  [PROBLEM 1: CONTROL SIGNALING PORT BLOCKED (TCP 7236)]")
        print("  * Underlying Technical Cause:")
        print("    Native Miracast (MS-MICE) uses TCP 7236 for session setup and RTSP negotiation.")
        print("    When blocked, the client cannot request the session token or on-screen PIN.")
        print("    The display detects connection timeout and prompts the user to download a desktop app.")
        print("  * Resolution:")
        print("    -> In WLAN / SSID Security Policies: Permit inbound TCP port 7236 and UDP 7250-7254.")
        print("    -> In Layer 2 Isolation: Disable peer-to-peer / client isolation for devices on this VLAN,")
        print("       or define an explicit whitelist / walled-garden entry for the display's IP.")
    else:
        print("\n  [VERIFIED: CONTROL SIGNALING (TCP 7236) ACCESSIBLE]")
        print("  * RTSP signaling is open. If native pairing fails, inspect multicast discovery below.")

    # Issue B: Multicast Discovery / Filtering
    print("\n  [PROBLEM 2: MULTICAST & ZERO-CONF DISCOVERY NOT PROPAGATING]")
    print("  * Underlying Technical Cause:")
    print("    Casting devices advertise services via link-local multicast (mDNS 224.0.0.251 and SSDP 239.255.255.250).")
    print("    Wireless Access Points and Layer 2 switches often drop or filter multicast by default to conserve airtime.")
    print("  * Resolution:")
    print("    -> Multicast Optimization: Enable 'Multicast-to-Unicast conversion' on the wireless controller.")
    print("       This converts unreliable 802.11 multicast frames into reliable unicast 802.11 frames for clients.")
    print("    -> Service Gateway / mDNS Proxy: Enable an mDNS / ZeroConf gateway service on the wireless controller")
    print("       and ensure the following service PTR records are explicitly permitted:")
    print("         * Miracast:  _display._tcp")
    print("         * AirPlay:   _airplay._tcp, _raop._tcp")
    print("         * Cast:      _googlecast._tcp")
    print("         * Web/AV:    _airtame._tcp, _clickshare._tcp, _solstice._tcp")

    # Issue C: Layer 2 Multicast Longevity (IGMP Snooping & Querier)
    print("\n  [PROBLEM 3: DISPLAYS DISAPPEAR AFTER 3 TO 5 MINUTES (IGMP PRUNING)]")
    print("  * Underlying Technical Cause:")
    print("    When IGMP Snooping is enabled on managed switches without an active IGMP Querier on the subnet,")
    print("    the switch stops receiving membership reports and prunes multicast groups from access ports.")
    print("  * Resolution:")
    print("    -> Enable an IGMP Querier on the default gateway or Layer 3 switch SVI for this VLAN.")
    print("    -> Ensure query intervals are set to standard timings (typically 60-125 seconds).")

    # Issue D: Inter-VLAN Routing & TTL Constraints
    print("\n  [PROBLEM 4: CROSS-VLAN CASTING (CLIENTS & DISPLAYS ON DIFFERENT SUBNETS)]")
    print("  * Underlying Technical Cause:")
    print("    mDNS packets are link-local with TTL=1 and cannot traverse Layer 3 routers without proxying.")
    print("  * Resolution:")
    print("    -> Deploy an mDNS Proxy/Reflector at the router or access point layer.")
    print("    -> Ensure inter-VLAN ACLs permit client-to-display TCP control ports and bidirectional UDP media.")

    print("\n" + "=" * 78)
    print("Diagnostics completed.\n")

# ==============================================================================
# Helpdesk Ticket Generator & Comparison Engine
# ==============================================================================
def generate_ticket_snippet(target_ip: str, vendor_name: str, port_results: dict[str, bool], local_ip: str) -> str:
    """Generates an executive verdict and a pre-formatted snippet ready for Jira/ServiceNow."""
    rtsp_ok = port_results.get("TCP:7236", False)
    airplay_ok = port_results.get("TCP:7000", False) or port_results.get("TCP:7100", False)
    cast_ok = port_results.get("TCP:8009", False) or port_results.get("TCP:8008", False)

    if not rtsp_ok:
        verdict = "FAIL - Wi-Fi/Switch Firewall Policy Blocking Miracast Signaling (TCP 7236)"
        action = "Network Engineering: Permit TCP 7236 & UDP 7250-7254 on SSID profile or remove client isolation."
    elif not (airplay_ok or cast_ok):
        verdict = "WARNING - Signaling Reachable, but Native Mirroring Services Unresponsive"
        action = "Check display settings to ensure AirPlay/Cast/Miracast toggles are enabled on the receiver."
    else:
        verdict = "PASS - Core L4 Transport Reachable (Inspect L2 mDNS/Bonjour Gateway if discovery fails)"
        action = "Network Engineering: Verify mDNS Gateway maps _display._tcp / _airplay._tcp on wireless controller."

    snippet = f"""
==================== TICKET ESCALATION SUMMARY (COPY & PASTE) ====================
Incident Category: Screen Casting / Wireless Presentation Failure
Client Source IP : {local_ip}
Target Display IP: {target_ip} ({vendor_name})
Executive Verdict: {verdict}
Tested Transport : Miracast RTSP (7236): {'OPEN' if rtsp_ok else 'BLOCKED'} | HTTP API: {'OPEN' if port_results.get('TCP:80') else 'CLOSED'}
Escalation Action: {action}
Timestamp        : {time.strftime('%Y-%m-%d %H:%M:%S')}
==================================================================================
"""
    return snippet

def run_ab_comparison(ip_working: str, ip_failing: str, local_ip: str):
    """Compares a working display against a failing display side-by-side."""
    print("\n" + "=" * 78)
    print("  A/B COMPARISON MODE: WORKING DISPLAY VS. FAILING DISPLAY")
    print("=" * 78)
    print(f"  Working Target : {ip_working}")
    print(f"  Failing Target : {ip_failing}\n")

    ports_working = run_phase3_ports(ip_working)
    ports_failing = run_phase3_ports(ip_failing)

    print("\n" + "=" * 78)
    print(f"{'PROTOCOL / PORT':<28} | {'WORKING (' + ip_working + ')':<22} | {'FAILING (' + ip_failing + ')':<22}")
    print("-" * 78)

    keys = set(ports_working.keys()).union(set(ports_failing.keys()))
    for k in sorted(keys):
        w_status = "PASS (Open)" if ports_working.get(k) else "FAIL / Closed"
        f_status = "PASS (Open)" if ports_failing.get(k) else "FAIL / Closed"
        marker = " " if ports_working.get(k) == ports_failing.get(k) else "*"
        print(f"{marker} {k:<26} | {w_status:<22} | {f_status:<22}")

    print("-" * 78)
    print("  * Indicates a difference between the two devices.")

    # High-level diff analysis
    if ports_working.get("TCP:7236") and not ports_failing.get("TCP:7236"):
        print("\n  [ROOT CAUSE IDENTIFIED BY COMPARISON]:")
        print(f"  TCP 7236 (Miracast RTSP) is OPEN on {ip_working} but BLOCKED on {ip_failing}.")
        print("  -> Conclusion: The failing room is connected to a switch port or VLAN with an active ACL")
        print("     or guest isolation policy that the working room is not subject to.")

    snippet = generate_ticket_snippet(ip_failing, "Failing Room Display", ports_failing, local_ip)
    print(snippet)

# ==============================================================================
# Main Orchestrator: Guided Workflow (Default) & Zero-Config Batch Mode
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Enterprise & Education Casting Diagnostic Suite (cast-diag)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  Default (Guided) : Step-by-step interactive workflow tailored for Helpdesk and field techs.
  --zero-config    : Automated non-interactive batch mode (audits all discovered targets).
  --compare <W> <F>: A/B diff test between a working display (W) and a failing display (F).
        """
    )
    parser.add_argument("-t", "--target", help="Target Display IP address")
    parser.add_argument("--compare", nargs=2, metavar=("WORKING_IP", "FAILING_IP"), help="Run side-by-side A/B comparison")
    parser.add_argument("--zero-config", action="store_true", help="Non-interactive batch mode; auto-audit all discovered devices")
    parser.add_argument("--export", action="store_true", help="Export summary to a text file for ticket attachments")
    parser.add_argument("--monitor", type=int, default=0, help="Run IGMP Longevity Monitor for N seconds (detects switch snooping drops)")
    parser.add_argument("--phase", type=int, choices=[0, 1, 2, 3, 4], help="Run only a specific diagnostic phase (0-4)")
    parser.add_argument("--skip-prereqs", action="store_true", help="Skip Phase 0 system prerequisites validation")

    args = parser.parse_args()

    # Determine local client IP
    local_ip = "127.0.0.1"
    try:
        s_ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_ip.connect(("8.8.8.8", 80))
        local_ip = s_ip.getsockname()[0]
        s_ip.close()
    except Exception:
        pass

    # A/B Comparison Shortcut
    if args.compare:
        run_ab_comparison(args.compare[0], args.compare[1], local_ip)
        return

    # Phase 0: System Prerequisites Check
    if not args.skip_prereqs and (args.phase is None or args.phase == 0):
        if not check_prerequisites():
            sys.exit(1)
        if args.phase == 0:
            sys.exit(0)

    # Phase 1: L2 Discovery
    if args.phase is None or args.phase == 1:
        discovered_mdns, discovered_ssdp = run_phase1_discovery(timeout=3.0, monitor_interval=args.monitor)
    else:
        discovered_mdns, discovered_ssdp = {}, []

    # Collect unique IPs found in Phase 1
    found_ips = set()
    for records in discovered_mdns.values():
        for r in records:
            found_ips.add(r["ip"])
    for s in discovered_ssdp:
        found_ips.add(s["ip"])
    sorted_ips = sorted(found_ips)

    targets_to_audit = []

    # --------------------------------------------------------------------------
    # Flow A: Explicit Target Provided via CLI (-t)
    # --------------------------------------------------------------------------
    if args.target:
        targets_to_audit.append(args.target)

    # --------------------------------------------------------------------------
    # Flow B: Zero-Config Automated Mode (--zero-config)
    # --------------------------------------------------------------------------
    elif args.zero_config:
        if sorted_ips:
            print(f"\n[--zero-config] Auditing all {len(sorted_ips)} discovered targets automatically...")
            targets_to_audit.extend(sorted_ips)
        else:
            print("\n[--zero-config] No casting devices discovered automatically on local subnet.")

    # --------------------------------------------------------------------------
    # Flow C: Default Guided Helpdesk Workflow
    # --------------------------------------------------------------------------
    else:
        print("\n" + "=" * 78)
        print("  GUIDED HELPDESK WORKFLOW: TARGET IDENTIFICATION")
        print("=" * 78)

        if not sorted_ips:
            print("  [!] No wireless displays were discovered automatically.")
            print("      Common cause: The display is on another VLAN or mDNS is blocked by Wi-Fi.")
            try:
                manual_ip = input("\n  -> Enter the IP address shown on the physical TV screen: ").strip()
                if manual_ip:
                    targets_to_audit.append(manual_ip)
            except (EOFError, KeyboardInterrupt):
                pass
        elif len(sorted_ips) == 1:
            display_ip = sorted_ips[0]
            name = ""
            for s in discovered_ssdp:
                if s["ip"] == display_ip and s.get("friendly_name"):
                    name = f" ('{s['friendly_name']}')"
            print(f"  [+] Detected 1 screen: {display_ip}{name}")
            try:
                confirm = input("  -> Audit this display? [Y/n or enter different IP]: ").strip()
                if not confirm or confirm.lower().startswith("y"):
                    targets_to_audit.append(display_ip)
                elif confirm.lower().startswith("n"):
                    diff_ip = input("  -> Enter the IP address to audit: ").strip()
                    if diff_ip:
                        targets_to_audit.append(diff_ip)
                else:
                    # User typed an IP address directly
                    targets_to_audit.append(confirm)
            except (EOFError, KeyboardInterrupt):
                targets_to_audit.append(display_ip)
        else:
            print(f"  [+] Discovered {len(sorted_ips)} displays on this network:\n")
            for idx, ip in enumerate(sorted_ips, 1):
                name = ""
                for s in discovered_ssdp:
                    if s["ip"] == ip and s.get("friendly_name"):
                        name = f" - '{s['friendly_name']}'"
                        break
                print(f"      [{idx}] {ip}{name}")
            print("\n      [C] Compare two devices (A/B Test: Working room vs. Broken room)")
            print("      [A] Audit all discovered displays")
            print("      [M] Enter a different IP not listed above")

            try:
                choice = input("\n  -> Select option [Default: 1]: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = "1"

            if not choice or choice == "1":
                targets_to_audit.append(sorted_ips[0])
            elif choice.upper() == "A":
                targets_to_audit.extend(sorted_ips)
            elif choice.upper() == "C":
                try:
                    w_ip = input("     -> Enter WORKING display IP: ").strip()
                    f_ip = input("     -> Enter FAILING display IP: ").strip()
                    if w_ip and f_ip:
                        run_ab_comparison(w_ip, f_ip, local_ip)
                        return
                except (EOFError, KeyboardInterrupt):
                    return
            elif choice.upper() == "M":
                try:
                    m_ip = input("     -> Enter target display IP: ").strip()
                    if m_ip:
                        targets_to_audit.append(m_ip)
                except (EOFError, KeyboardInterrupt):
                    pass
            elif choice.isdigit() and 1 <= int(choice) <= len(sorted_ips):
                targets_to_audit.append(sorted_ips[int(choice) - 1])
            else:
                targets_to_audit.append(sorted_ips[0])

    # Run remaining phases against selected target(s)
    if targets_to_audit:
        full_ticket_reports = []
        for target in targets_to_audit:
            vendor = run_phase2_fingerprint(target, discovered_mdns, discovered_ssdp) if (args.phase is None or args.phase == 2) else "Unknown"
            port_results = run_phase3_ports(target) if (args.phase is None or args.phase == 3) else {}
            if args.phase is None or args.phase == 4:
                run_phase4_guidance(target, port_results, vendor)

            # Generate Ticket Snippet
            ticket_block = generate_ticket_snippet(target, vendor, port_results, local_ip)
            print(ticket_block)
            full_ticket_reports.append(ticket_block)

        # Export if requested
        if args.export:
            filename = f"cast_audit_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            with open(filename, "w") as f:
                f.write("\n".join(full_ticket_reports))
            print(f"[+] Full audit report exported to file: {filename}\n")

if __name__ == "__main__":
    main()
