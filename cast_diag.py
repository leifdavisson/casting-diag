#!/usr/bin/env python3
"""
Casting Diagnostic Suite (cast-diag)
Enterprise network verification tool for Miracast, Airtame, Apple AirPlay, and Google Cast.

Licensed under GNU AGPLv3.
"""

import sys
import socket
import struct
import time
import argparse
import select
from typing import Dict, List, Tuple

# Multicast & Protocol Constants
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900

CAST_SERVICES = {
    "_display._tcp.local": {
        "name": "Miracast over Infrastructure (MS-MICE)",
        "protocol": "Miracast",
        "description": "Windows 10/11 native screen casting discovery",
    },
    "_airtame._tcp.local": {
        "name": "Airtame Native Discovery",
        "protocol": "Airtame",
        "description": "Airtame discovery and status broadcast",
    },
    "_airplay._tcp.local": {
        "name": "Apple AirPlay Mirroring",
        "protocol": "AirPlay",
        "description": "Apple TV and AirPlay video mirroring",
    },
    "_raop._tcp.local": {
        "name": "Apple AirPlay Audio (RAOP)",
        "protocol": "AirPlay",
        "description": "Remote Audio Output Protocol",
    },
    "_googlecast._tcp.local": {
        "name": "Google Cast / Chromecast",
        "protocol": "Google Cast",
        "description": "Chromecast and Google Nest hub discovery",
    },
}

PORT_MATRIX = {
    "Miracast": [
        {"port": 7236, "proto": "TCP", "name": "RTSP Control", "critical": True, "desc": "Miracast session setup & PIN verification"},
        {"port": 7250, "proto": "UDP", "name": "RTP Video", "critical": False, "desc": "RTP media payload"},
    ],
    "Airtame": [
        {"port": 80, "proto": "TCP", "name": "HTTP API", "critical": True, "desc": "Local device management & pairing fallback"},
        {"port": 443, "proto": "TCP", "name": "HTTPS API", "critical": False, "desc": "TLS management endpoint"},
        {"port": 8001, "proto": "TCP", "name": "Streaming Control", "critical": False, "desc": "Airtame proprietary streaming"},
    ],
    "AirPlay": [
        {"port": 7000, "proto": "TCP", "name": "AirPlay Service", "critical": True, "desc": "AirPlay stream initiation"},
        {"port": 7100, "proto": "TCP", "name": "AirPlay Mirroring", "critical": True, "desc": "Screen mirroring session"},
    ],
    "Google Cast": [
        {"port": 8008, "proto": "TCP", "name": "HTTP API", "critical": True, "desc": "DIAL / Cast REST control"},
        {"port": 8009, "proto": "TCP", "name": "TLS Cast Channel", "critical": True, "desc": "Encrypted protobuf cast control"},
    ],
}

def build_dns_query(service_name: str) -> bytes:
    """Builds a raw DNS PTR query for mDNS."""
    # Transaction ID = 0, Flags = 0 (standard query), Questions = 1
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    query = bytearray(header)
    for part in service_name.strip(".").split("."):
        query.append(len(part))
        query.extend(part.encode("utf-8"))
    query.append(0)  # null terminator
    # Type PTR = 12, Class IN = 1 (with unicast-response bit = 0)
    query.extend(struct.pack(">HH", 12, 1))
    return bytes(query)

def parse_dns_names(data: bytes, offset: int = 0) -> Tuple[str, int]:
    """Basic extraction of domain labels from DNS packet."""
    labels = []
    curr = offset
    visited = set()
    jumped = False
    next_offset = offset

    while curr < len(data):
        length = data[curr]
        if length == 0:
            if not jumped:
                next_offset = curr + 1
            break
        elif (length & 0xC0) == 0xC0:  # Pointer compression
            if curr + 1 >= len(data):
                break
            pointer = struct.unpack(">H", data[curr:curr+2])[0] & 0x3FFF
            if not jumped:
                next_offset = curr + 2
                jumped = True
            if pointer in visited:
                break
            visited.add(pointer)
            curr = pointer
        else:
            curr += 1
            labels.append(data[curr:curr+length].decode("utf-8", errors="replace"))
            curr += length
            if not jumped:
                next_offset = curr

    return ".".join(labels), next_offset

def probe_mdns(timeout: float = 3.0) -> Dict[str, List[Dict]]:
    """Probes the local L2 segment for mDNS casting advertisements."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass

    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.settimeout(timeout)

    discovered = {k: [] for k in CAST_SERVICES}

    # Bind to mDNS port or ephemeral port
    try:
        sock.bind(("", 0))
    except Exception as e:
        print(f"[-] Could not bind socket: {e}")
        return discovered

    # Transmit queries for each casting service
    for service in CAST_SERVICES:
        packet = build_dns_query(service)
        try:
            sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
        except Exception as e:
            print(f"[-] Failed sending query for {service}: {e}")

    start_time = time.time()
    while time.time() - start_time < timeout:
        remaining = timeout - (time.time() - start_time)
        if remaining <= 0:
            break
        r, _, _ = select.select([sock], [], [], remaining)
        if not r:
            break
        try:
            data, addr = sock.recvfrom(4096)
            for service, details in CAST_SERVICES.items():
                target_bytes = service.encode("utf-8")
                if target_bytes in data or service.split(".")[0].encode("utf-8") in data:
                    # Found match
                    record = {
                        "ip": addr[0],
                        "port": addr[1],
                        "service": service,
                        "raw_bytes": len(data)
                    }
                    if not any(x["ip"] == addr[0] and x["service"] == service for x in discovered[service]):
                        discovered[service].append(record)
        except Exception:
            break

    sock.close()
    return discovered

def test_tcp_port(ip: str, port: int, timeout: float = 2.0) -> Tuple[bool, str]:
    """Tests TCP connection to target port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    start = time.perf_counter()
    try:
        s.connect((ip, port))
        latency_ms = (time.perf_counter() - start) * 1000
        s.close()
        return True, f"{latency_ms:.1f}ms"
    except socket.timeout:
        return False, "TIMEOUT (Firewall / Filter Dropped)"
    except ConnectionRefusedError:
        return False, "REFUSED (Host up, Port closed)"
    except Exception as e:
        return False, str(e)

def test_udp_port(ip: str, port: int, timeout: float = 1.0) -> Tuple[bool, str]:
    """Basic UDP reachability test using an empty probe."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(b"\x00", (ip, port))
        s.close()
        return True, "SENT (UDP Stateless)"
    except Exception as e:
        return False, str(e)

def run_diagnostics(target_ip: str = None):
    print("=" * 75)
    print("  ENTERPRISE CASTING DIAGNOSTIC SUITE (cast-diag)")
    print("  Miracast (MS-MICE) | Airtame | Apple AirPlay | Google Cast")
    print("=" * 75)

    print("\n[*] Phase 1: Probing Multicast DNS (mDNS) Discovery on local segment...")
    mdns_results = probe_mdns(timeout=3.0)

    any_found = False
    for service, records in mdns_results.items():
        svc_meta = CAST_SERVICES[service]
        if records:
            any_found = True
            print(f"  [+] FOUND {svc_meta['name']} ({service}):")
            for r in records:
                print(f"      -> Device at {r['ip']}:{r['port']}")
        else:
            print(f"  [-] NOT DETECTED: {svc_meta['name']} ({service})")

    if not any_found:
        print("\n  [!] CAUTION: No casting mDNS advertisements received!")
        print("      * Juniper Mist: Check if 'Bonjour Gateway' is enabled and maps these services.")
        print("      * Juniper Mist: Verify 'Multicast to Unicast conversion' is enabled.")
        print("      * Junos Switch: Verify IGMP snooping querier is active.")

    if target_ip:
        print(f"\n[*] Phase 2: Direct L4 Transport & Port Audit on Target: {target_ip}")
        print("-" * 75)
        print(f"{'Protocol':<12} | {'Port':<8} | {'Service':<22} | {'Status':<8} | {'Details'}")
        print("-" * 75)

        for proto_category, ports in PORT_MATRIX.items():
            for p in ports:
                port_num = p["port"]
                proto = p["proto"]
                if proto == "TCP":
                    success, detail = test_tcp_port(target_ip, port_num)
                    status_str = "PASS" if success else "FAIL"
                else:
                    success, detail = test_udp_port(target_ip, port_num)
                    status_str = "PASS" if success else "WARN"

                print(f"{proto_category:<12} | {proto} {port_num:<4} | {p['name']:<22} | {status_str:<8} | {detail}")

        print("-" * 75)
        print("\n[*] Phase 3: Actionable Findings & Analysis:")
        
        # Specific check for Miracast RTSP 7236
        rtsp_ok, _ = test_tcp_port(target_ip, 7236, timeout=1.5)
        if not rtsp_ok:
            print("  [CRITICAL] Miracast RTSP (TCP 7236) is BLOCKED or UNREACHABLE!")
            print("  -> Symptoms: Windows will show 'Connecting...' and fail, or prompt for the Airtame app.")
            print("  -> Fix: On Juniper Mist, inspect WLAN L2/L3 Access Lists / Policies and allow TCP 7236.")
        else:
            print("  [SUCCESS] Miracast RTSP (TCP 7236) is open and responding.")
            print("  -> If Windows still does not discover it, the issue is 100% mDNS discovery (_display._tcp).")

    print("\n" + "=" * 75)
    print("Diagnostics completed.")

def main():
    parser = argparse.ArgumentParser(description="Enterprise Casting Diagnostic Suite")
    parser.add_argument("-t", "--target", help="Target Airtame, AppleTV, or Chromecast IP address", default=None)
    args = parser.parse_args()

    run_diagnostics(target_ip=args.target)

if __name__ == "__main__":
    main()
