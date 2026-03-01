"""Minimal Wolfram Language LSP client for code intelligence.

Ported from Serena's solidlsp, simplified for Wolfram-only use.
Communicates with the official WolframResearch LSPServer paclet via JSON-RPC over stdio.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import subprocess
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

logger = logging.getLogger("wolfram_mcp.lsp_client")

ENCODING = "utf-8"
_REQUEST_TIMEOUT = 60.0
_INIT_TIMEOUT = 120.0


class LSPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code

    @classmethod
    def from_lsp(cls, d: dict) -> LSPError:
        return LSPError(d.get("code", -1), d.get("message", "Unknown LSP error"))

    def __str__(self) -> str:
        return f"{super().__str__()} (code={self.code})"


class WolframLSPClient:
    def __init__(self, kernel_path: str, project_root: str) -> None:
        self._kernel_path = kernel_path
        self._project_root = os.path.abspath(project_root)
        self._process: subprocess.Popen | None = None
        self._started = False
        self._request_id = 0
        self._pending: dict[int, Queue] = {}
        self._stdin_lock = threading.Lock()
        self._request_id_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._diagnostics: dict[str, list] = {}
        self._open_files: set[str] = set()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def _ensure_started(self) -> None:
        if self._started:
            return
        wolfram_code = 'Needs["LSPServer`"];LSPServer`StartServer[]'
        if platform.system() == "Windows":
            cmd = [self._kernel_path, "-noprompt", "-noinit", "-run", wolfram_code]
        else:
            cmd = f"{shlex.quote(self._kernel_path)} -noprompt -noinit -run {shlex.quote(wolfram_code)}"

        logger.info("Starting Wolfram LSP: %s", cmd)
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=not isinstance(cmd, list),
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        root_uri = Path(self._project_root).as_uri()
        init_params = {
            "processId": os.getpid(),
            "rootPath": self._project_root,
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {"workspaceFolders": True},
                "textDocument": {
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "hover": {
                        "dynamicRegistration": True,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "formatting": {"dynamicRegistration": True},
                    "publishDiagnostics": {"relatedInformation": True},
                },
            },
            "workspaceFolders": [
                {"uri": root_uri, "name": os.path.basename(self._project_root)}
            ],
        }
        logger.info("Sending initialize request (timeout=%ss)...", _INIT_TIMEOUT)
        response = self._send_request("initialize", init_params, timeout=_INIT_TIMEOUT)
        caps = list(response.get("capabilities", {}).keys()) if response else []
        logger.info("Wolfram LSP capabilities: %s", caps)
        self._send_notification("initialized", {})
        self._started = True
        logger.info("Wolfram LSP initialized and ready.")

    # --- JSON-RPC transport ---

    def _send_payload(self, payload: dict) -> None:
        if not self._process or not self._process.stdin:
            return
        body = json.dumps(payload, check_circular=False, ensure_ascii=False, separators=(",", ":")).encode(ENCODING)
        # Wolfram LSP crashes if Content-Type header is included — only send Content-Length
        header = f"Content-Length: {len(body)}\r\n\r\n".encode(ENCODING)
        with self._stdin_lock:
            try:
                self._process.stdin.write(header + body)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                logger.error("Failed to write to LSP stdin: %s", e)

    def _send_request(self, method: str, params: dict | None = None, timeout: float = _REQUEST_TIMEOUT) -> Any:
        self._ensure_started() if method != "initialize" else None
        with self._request_id_lock:
            req_id = self._request_id
            self._request_id += 1

        queue: Queue = Queue()
        with self._pending_lock:
            self._pending[req_id] = queue

        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if method in ("shutdown", "exit"):
            pass  # omit params
        elif params is not None:
            msg["params"] = params
        else:
            msg["params"] = {}
        self._send_payload(msg)

        try:
            result = queue.get(timeout=timeout)
        except Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"LSP request {method} (id={req_id}) timed out after {timeout}s")

        if isinstance(result, Exception):
            raise result
        return result

    def _send_notification(self, method: str, params: dict | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if method in ("shutdown", "exit"):
            pass
        elif params is not None:
            msg["params"] = params
        else:
            msg["params"] = {}
        self._send_payload(msg)

    # --- stdout reader thread ---

    def _read_stdout(self) -> None:
        try:
            while self._process and self._process.stdout:
                if self._process.poll() is not None:
                    break
                line = self._process.stdout.readline()
                if not line:
                    continue
                num_bytes = self._parse_content_length(line)
                if num_bytes is None:
                    continue
                # Skip remaining headers until blank line
                while line and line.strip():
                    line = self._process.stdout.readline()
                if not line:
                    continue
                body = self._read_exact(num_bytes)
                if body is None:
                    continue
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.error("JSON decode error: %s", e)
                    continue
                self._dispatch(payload)
        except Exception as e:
            logger.error("LSP stdout reader error: %s", e)

    def _parse_content_length(self, line: bytes) -> int | None:
        if line.startswith(b"Content-Length: "):
            try:
                return int(line.split(b"Content-Length: ")[1].strip())
            except (ValueError, IndexError):
                return None
        return None

    def _read_exact(self, num_bytes: int) -> bytes | None:
        data = b""
        while len(data) < num_bytes:
            if self._process is None or self._process.stdout is None:
                return None
            chunk = self._process.stdout.read(num_bytes - len(data))
            if not chunk:
                if self._process.poll() is not None:
                    return None
                time.sleep(0.01)
                continue
            data += chunk
        return data

    def _dispatch(self, payload: dict) -> None:
        if "method" in payload:
            if "id" in payload:
                self._handle_server_request(payload)
            else:
                self._handle_notification(payload)
        elif "id" in payload:
            self._handle_response(payload)

    def _handle_response(self, response: dict) -> None:
        resp_id = response.get("id")
        with self._pending_lock:
            queue = self._pending.pop(resp_id, None)
            if queue is None and isinstance(resp_id, str) and resp_id.isdigit():
                queue = self._pending.pop(int(resp_id), None)
        if queue is None:
            logger.debug("No pending request for response id=%s", resp_id)
            return
        if "error" in response and "result" not in response:
            queue.put(LSPError.from_lsp(response["error"]))
        else:
            queue.put(response.get("result"))

    def _handle_notification(self, notification: dict) -> None:
        method = notification.get("method", "")
        params = notification.get("params", {})
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            self._diagnostics[uri] = params.get("diagnostics", [])
        elif method == "window/logMessage":
            logger.info("Wolfram LSP: %s", params.get("message", ""))
        # Ignore $/progress and other notifications

    def _handle_server_request(self, request: dict) -> None:
        method = request.get("method", "")
        req_id = request.get("id")
        if method == "client/registerCapability":
            self._send_payload({"jsonrpc": "2.0", "id": req_id, "result": None})
        else:
            logger.debug("Unhandled server request: %s", method)
            self._send_payload({"jsonrpc": "2.0", "id": req_id, "result": None})

    # --- stderr reader thread ---

    def _read_stderr(self) -> None:
        try:
            while self._process and self._process.stderr:
                if self._process.poll() is not None:
                    break
                line = self._process.stderr.readline()
                if not line:
                    continue
                logger.debug("Wolfram LSP stderr: %s", line.decode(ENCODING, errors="replace").rstrip())
        except Exception:
            pass

    # --- File management ---

    def _open_file(self, abs_path: str) -> str:
        uri = Path(abs_path).as_uri()
        if uri in self._open_files:
            return uri
        try:
            text = Path(abs_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise FileNotFoundError(f"Cannot read {abs_path}: {e}") from e
        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "wolfram",
                    "version": 0,
                    "text": text,
                }
            },
        )
        self._open_files.add(uri)
        return uri

    # --- Public LSP methods ---

    def document_symbols(self, abs_path: str) -> list[dict]:
        self._ensure_started()
        uri = self._open_file(abs_path)
        result = self._send_request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        return result if isinstance(result, list) else []

    def hover(self, abs_path: str, line: int, character: int) -> dict | None:
        self._ensure_started()
        uri = self._open_file(abs_path)
        result = self._send_request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )
        return result if isinstance(result, dict) else None

    def definition(self, abs_path: str, line: int, character: int) -> list[dict]:
        self._ensure_started()
        uri = self._open_file(abs_path)
        result = self._send_request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return result
        return []

    def references(self, abs_path: str, line: int, character: int) -> list[dict]:
        self._ensure_started()
        uri = self._open_file(abs_path)
        result = self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            },
        )
        return result if isinstance(result, list) else []

    def get_diagnostics(self, uri: str | None = None) -> dict[str, list]:
        if uri is not None:
            return {uri: self._diagnostics.get(uri, [])}
        return dict(self._diagnostics)

    def stop(self) -> None:
        if not self._started or self._process is None:
            return
        try:
            self._send_request("shutdown", timeout=10.0)
        except Exception:
            pass
        try:
            self._send_notification("exit")
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        self._started = False
        logger.info("Wolfram LSP stopped.")
