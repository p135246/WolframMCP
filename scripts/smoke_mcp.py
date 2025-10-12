#!/usr/bin/env python
"""Quick smoke test for the Wolfram MCP server over stdio.

This script spawns the server, performs a few JSON-RPC calls, prints results, then exits.
"""
from __future__ import annotations
import json
import subprocess
import sys

SERVER_CMD = ["wolfram-mcp", "--transport", "stdio"]
TIMEOUT = 15

def send(proc: subprocess.Popen, obj: dict) -> None:
    line = json.dumps(obj)
    proc.stdin.write(line + "\n")
    proc.stdin.flush()

def recv(proc: subprocess.Popen) -> dict:
    raw = proc.stdout.readline()
    if not raw:
        raise RuntimeError("No response from server")
    return json.loads(raw)

def main():
    print("Starting server:", " ".join(SERVER_CMD))
    proc = subprocess.Popen(
        SERVER_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        # 0. Initialize handshake (basic capabilities skeleton)
        init_req = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "smoke-script", "version": "0.0.1"},
                "capabilities": {}
            }
        }
        send(proc, init_req)
        init_resp = recv(proc)
        print("INIT RESPONSE:", init_resp)

        # 0b. signal initialized
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # List tools
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools_resp = recv(proc)
        print("TOOLS RESPONSE:", tools_resp)
        # Call evaluate (lazy kernel starts here)
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "evaluate", "arguments": {"code": "2+2"}}})
        eval_resp = recv(proc)
        print("EVALUATE RESPONSE:", eval_resp)
        # Create notebook
        send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "create_notebook", "arguments": {"path": "smoke.nb", "cells": ["2+2"]}}})
        nb_resp = recv(proc)
        print("CREATE NOTEBOOK RESPONSE:", nb_resp)
        # Read resources list
        send(proc, {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
        res_list = recv(proc)
        print("RESOURCES LIST RESPONSE:", res_list)
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
    # Print any stderr lines (best-effort)
    stderr = proc.stderr.read()
    if stderr:
        print("[STDERR]\n" + stderr)

if __name__ == "__main__":
    sys.exit(main())
