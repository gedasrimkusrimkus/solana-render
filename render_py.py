import requests
import time
import csv
import os
import json
import threading
import asyncio
import websockets
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# --- Config ---
CSV_FILE = "transactions.csv"
WALLET_FILE = "wallets.json"
SEEN_FILE = "seen_signatures.json"
REFRESH_INTERVAL = 5
DEFAULT_PORT = int(os.environ.get("PORT", 8000))
DEFAULT_WALLETS = ["DEMO_WALLET_1", "DEMO_WALLET_2"]

RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://api.metaplex.solana.com",
]

# --- Utils ---
def load_json_file(filename, default):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default
    return default

def save_json_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

wallets = load_json_file(WALLET_FILE, DEFAULT_WALLETS)
seen_signatures = set(load_json_file(SEEN_FILE, []))
ws_clients = set()

# --- RPC helpers ---
def safe_rpc_call(payload):
    for endpoint in RPC_ENDPOINTS:
        try:
            r = requests.post(endpoint, json=payload, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None

def fetch_transactions(wallet):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [wallet, {"limit": 20}],
    }
    data = safe_rpc_call(payload)
    if not data or "result" not in data:
        return []

    txs = []
    for sig in data["result"]:
        signature = sig["signature"]
        if signature in seen_signatures:
            continue

        seen_signatures.add(signature)
        payload_tx = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [signature, "jsonParsed"],
        }
        tx_data = safe_rpc_call(payload_tx)
        if not tx_data or "result" not in tx_data:
            continue
        tx_info = tx_data["result"]

        txs.append({
            "wallet": wallet,
            "signature": signature,
            "slot": tx_info.get("slot"),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(tx_info.get("blockTime", 0))),
            "type": tx_info.get("meta", {}).get("err", None) and "Error" or "Success",
            "token": tx_info.get("transaction", {}).get("message", {}).get("accountKeys", [{}])[0].get("pubkey", ""),
            "amount": tx_info.get("meta", {}).get("fee", 0),
        })
    return txs

def save_transactions(txs):
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["wallet", "signature", "slot", "time", "type", "token", "amount"])
        if not file_exists:
            writer.writeheader()
        for tx in txs:
            writer.writerow(tx)

# --- Dashboard handler ---
class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            total_tx, unique_wallets, unique_tokens = 0, set(), set()
            if os.path.exists(CSV_FILE):
                with open(CSV_FILE, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    total_tx = len(rows)
                    for row in rows:
                        unique_wallets.add(row["wallet"])
                        unique_tokens.add(row["token"])

            html = f"""
            <html>
            <head>
            <title>Solana Tracker</title>
            <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1 {{ color: #2c3e50; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; }}
            th {{ background-color: #f2f2f2; }}
            </style>
            </head>
            <body>
            <h1>Solana Wallet Tracker</h1>
            <p><b>Tracking {len(wallets)} wallet(s)</b></p>
            <p>Total Transactions: {total_tx} | Unique Wallets: {len(unique_wallets)} | Unique Tokens: {len(unique_tokens)}</p>

            <h2>Manage Wallets</h2>
            <form method="POST" action="/add_wallet">
                <input type="text" name="wallet" placeholder="Enter wallet address" required>
                <input type="submit" value="Add Wallet">
            </form>
            <ul>
                {"".join(f"<li>{w} <a href='/remove_wallet?wallet={w}'>[remove]</a></li>" for w in wallets)}
            </ul>

            <h2>Transactions (Real-Time)</h2>
            <table>
                <tr><th>Time</th><th>Wallet</th><th>Signature</th><th>Token</th><th>Type</th><th>Amount</th></tr>
                <tbody id="tx-body"></tbody>
            </table>

            <script>
            const ws = new WebSocket("ws://" + location.host + "/ws");
            ws.onmessage = (event) => {{
                const tx = JSON.parse(event.data);
                const tbody = document.getElementById("tx-body");
                const row = document.createElement("tr");
                row.innerHTML = `
                    <td>${{tx.time}}</td>
                    <td>${{tx.wallet}}</td>
                    <td>${{tx.signature}}</td>
                    <td>${{tx.token}}</td>
                    <td>${{tx.type}}</td>
                    <td>${{tx.amount}}</td>
                `;
                tbody.insertBefore(row, tbody.firstChild);
            }};
            </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode())

        elif parsed.path == "/remove_wallet":
            qs = parse_qs(parsed.query)
            wallet = qs.get("wallet", [""])[0]
            if wallet in wallets:
                wallets.remove(wallet)
                save_json_file(WALLET_FILE, wallets)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/add_wallet":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            data = parse_qs(body)
            wallet = data.get("wallet", [""])[0]
            if wallet and wallet not in wallets:
                wallets.append(wallet)
                save_json_file(WALLET_FILE, wallets)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

# --- Background job ---
async def broadcast(tx):
    if ws_clients:
        msg = json.dumps(tx)
        await asyncio.wait([client.send(msg) for client in ws_clients])

def background_task(loop):
    while True:
        new_txs = []
        for wallet in wallets:
            txs = fetch_transactions(wallet)
            if txs:
                new_txs.extend(txs)
        if new_txs:
            save_transactions(new_txs)
            save_json_file(SEEN_FILE, list(seen_signatures))
            for tx in new_txs:
                asyncio.run_coroutine_threadsafe(broadcast(tx), loop)
        time.sleep(REFRESH_INTERVAL)

async def ws_handler(websocket, path):
    if path == "/ws":
        ws_clients.add(websocket)
        try:
            async for _ in websocket:
                pass
        finally:
            ws_clients.remove(websocket)

# --- Run ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    threading.Thread(target=background_task, args=(loop,), daemon=True).start()

    start_server = websockets.serve(ws_handler, "0.0.0.0", DEFAULT_PORT+1)
    loop.run_until_complete(start_server)

    try:
        server = HTTPServer(("", DEFAULT_PORT), CustomHandler)
        print(f"Server started at http://localhost:{DEFAULT_PORT}")
        loop.run_in_executor(None, server.serve_forever)
        loop.run_forever()
    except OSError:
        fallback_port = 8001
        server = HTTPServer(("", fallback_port), CustomHandler)
        print(f"Port {DEFAULT_PORT} busy. Using http://localhost:{fallback_port}")
        loop.run_in_executor(None, server.serve_forever)
        loop.run_forever()
