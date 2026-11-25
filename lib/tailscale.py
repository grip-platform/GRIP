#!/usr/bin/env python3
"""
Tailscale Helper - Remote pairing over Tailscale VPN for GRIP.

Wraps the tailscale CLI for pair-mode server exposure and peer discovery.
All subprocess calls use asyncio.create_subprocess_exec per python-async-safety rules.

Usage:
  python3 tailscale.py status          # Check daemon status + local IP
  python3 tailscale.py peers           # List devices on tailnet
  python3 tailscale.py expose PORT     # Expose port via tailscale serve
  python3 tailscale.py verify IP PORT  # Test TCP connectivity
  python3 tailscale.py unexpose        # Remove exposed service

Version: 1.0.0
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"


def log(level: str, message: str):
    """Append to GRIP hook log."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [TAILSCALE][{level}] {message}\n")
    except Exception:
        pass


async def run_cmd(*args, timeout: float = 10.0) -> tuple:
    """Run a command asynchronously. Returns (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "tailscale not found"


async def status() -> dict:
    """Check Tailscale daemon status and get local IP."""
    code, out, err = await run_cmd("tailscale", "status", "--json")
    if code != 0:
        return {
            "installed": False,
            "running": False,
            "error": err or "tailscale not available",
        }

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"installed": True, "running": False, "error": "invalid JSON"}

    self_node = data.get("Self", {})
    ips = self_node.get("TailscaleIPs", [])
    return {
        "installed": True,
        "running": data.get("BackendState", "") == "Running",
        "hostname": self_node.get("HostName", ""),
        "tailscale_ip": ips[0] if ips else None,
        "os": self_node.get("OS", ""),
        "online": self_node.get("Online", False),
        "tailnet": data.get("MagicDNSSuffix", ""),
    }


async def peers() -> list:
    """List devices on the tailnet."""
    code, out, err = await run_cmd("tailscale", "status", "--json")
    if code != 0:
        return []

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []

    result = []
    for node_id, node in data.get("Peer", {}).items():
        ips = node.get("TailscaleIPs", [])
        result.append(
            {
                "hostname": node.get("HostName", ""),
                "ip": ips[0] if ips else None,
                "os": node.get("OS", ""),
                "online": node.get("Online", False),
                "last_seen": node.get("LastSeen", ""),
            }
        )

    return sorted(result, key=lambda p: (not p["online"], p["hostname"]))


async def expose(port: int) -> dict:
    """Expose a local port via tailscale serve."""
    code, out, err = await run_cmd(
        "tailscale", "serve", "--bg", str(port), timeout=15.0
    )
    if code != 0:
        return {"success": False, "error": err}
    log("INFO", f"Exposed port {port} via tailscale serve")
    return {"success": True, "port": port, "output": out}


async def verify(peer_ip: str, port: int, timeout: float = 5.0) -> dict:
    """Test TCP connectivity to a peer."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(peer_ip, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return {"reachable": True, "ip": peer_ip, "port": port}
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
        return {
            "reachable": False,
            "ip": peer_ip,
            "port": port,
            "error": str(e),
        }


async def unexpose() -> dict:
    """Remove all tailscale serve configurations."""
    code, out, err = await run_cmd("tailscale", "serve", "reset", timeout=10.0)
    if code != 0:
        return {"success": False, "error": err}
    log("INFO", "Tailscale serve reset")
    return {"success": True, "output": out}


async def cli_main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: tailscale.py <status|peers|expose|verify|unexpose> [args]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        result = await status()
        print(json.dumps(result, indent=2))

    elif cmd == "peers":
        result = await peers()
        if not result:
            print("No peers found (is Tailscale running?)")
            return
        print(f"\n{'Hostname':20s}  {'IP':18s}  {'OS':8s}  {'Online':6s}")
        print("-" * 60)
        for p in result:
            online = "YES" if p["online"] else "no"
            print(
                f"{p['hostname']:20s}  {p.get('ip') or '?':18s}  "
                f"{p.get('os', '?'):8s}  {online:6s}"
            )

    elif cmd == "expose":
        if len(sys.argv) < 3:
            print("Usage: tailscale.py expose PORT")
            sys.exit(1)
        port = int(sys.argv[2])
        result = await expose(port)
        print(json.dumps(result, indent=2))

    elif cmd == "verify":
        if len(sys.argv) < 4:
            print("Usage: tailscale.py verify IP PORT")
            sys.exit(1)
        result = await verify(sys.argv[2], int(sys.argv[3]))
        print(json.dumps(result, indent=2))

    elif cmd == "unexpose":
        result = await unexpose()
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(cli_main())
