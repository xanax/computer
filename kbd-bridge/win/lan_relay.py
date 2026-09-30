#!/usr/bin/env python3
"""
lan_relay.py — expose the WSL kbd-bridge server to the LAN (Windows side).

Runs on Windows. Listens on the machine's LAN address and forwards raw TCP to
127.0.0.1:<port>, which WSL2's localhost forwarding maps to the server inside
WSL. It binds the LAN address specifically so it never clashes with the WSL
forwarder (which owns 127.0.0.1).

Why: WSL2 uses NAT networking, so devices on the LAN (the ESP32) cannot reach
the server's WSL IP. This relay bridges that gap with no admin rights, and it
keeps working when the WSL IP changes after a restart.

    python lan_relay.py                       # bind auto-detected LAN IP
    python lan_relay.py --listen 192.168.0.56
    python lan_relay.py --port 8766
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys


def detect_lan_ip() -> str:
    """Best-effort LAN IP of this machine (no traffic actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "0.0.0.0"
    finally:
        s.close()


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(client_reader, client_writer, upstream_host, upstream_port) -> None:
    peer = None
    try:
        peer = client_writer.get_extra_info("peername")
        up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)
    except Exception as exc:
        print(f"[relay] upstream {upstream_host}:{upstream_port} unreachable: {exc}", flush=True)
        try:
            client_writer.close()
        except Exception:
            pass
        return

    print(f"[relay] {peer} -> {upstream_host}:{upstream_port}", flush=True)
    await asyncio.gather(
        pipe(client_reader, up_writer),
        pipe(up_reader, client_writer),
    )
    try:
        client_writer.close()
    except Exception:
        pass


async def main() -> None:
    ap = argparse.ArgumentParser(description="LAN relay for the kbd-bridge server")
    ap.add_argument("--listen", default="", help="address to bind (default: auto-detect LAN IP)")
    ap.add_argument("--port", type=int, default=8766, help="port to listen on (default 8766)")
    ap.add_argument("--upstream-host", default="127.0.0.1")
    ap.add_argument("--upstream-port", type=int, default=0)
    args = ap.parse_args()

    listen_ip = args.listen or detect_lan_ip()
    up_port = args.upstream_port or args.port

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, args.upstream_host, up_port),
        host=listen_ip,
        port=args.port,
        reuse_address=True,
    )
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"[relay] listening on {addrs}  ->  {args.upstream_host}:{up_port}", flush=True)
    print("[relay] point the ESP32 at this address, e.g. http://%s:%d" % (listen_ip, args.port), flush=True)
    if listen_ip == "0.0.0.0":
        print("[relay] WARNING: could not detect a LAN IP; bound to all interfaces, which may "
              "conflict with the WSL localhost forwarder on the same port.", flush=True)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
