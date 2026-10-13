import os
import socket
import json
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("CONCAT_API_HOST", "127.0.0.1")
PORT = int(os.getenv("CONCAT_API_PORT", "7420"))
TOKEN = os.getenv("CONCAT_API_TOKEN")

if not TOKEN:
    raise RuntimeError("Missing CONCAT_API_TOKEN")

request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "version",
    "params": {}
}

print(f"Connecting to Concat at {HOST}:{PORT}...")

with socket.create_connection((HOST, PORT), timeout=5) as sock:
    sock.settimeout(5)

    # Candidate authentication handshake.
    auth = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "auth",
        "params": {"token": TOKEN}
    }

    sock.sendall(
        (json.dumps(auth) + "\n").encode()
    )

    reader = sock.makefile("r")

    print("Authentication response:")
    print(reader.readline().strip())

    sock.sendall(
        (json.dumps(request) + "\n").encode()
    )

    print("Version response:")
    print(reader.readline().strip())