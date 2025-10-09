# simple_tracker.py
import requests
import time
import csv
import os
import json
from datetime import datetime, timezone
import http.server
import socketserver
import threading

# ---------------- RENDER.COM KONFIGŪRACIJA ----------------
PORT = int(os.environ.get('PORT', 8000))
RENDER = os.environ.get('RENDER', False)

# ---------------- CONFIG ----------------
RPC_ENDPOINTS = [
    "https://rpc.ankr.com/solana",
    "https://api.mainnet-beta.solana.com", 
    "https://solana-rpc.publicnode.com"
]

WATCHED_WALLETS = [
    "4Vgu5AHT1ndczhdgqAipNDqLsCPjBS5jMXkEg8yzhT9c",
    "8AHdpimBNQhhb6FwUy49txZ7ahWGQz2iECbbN843RfZY",
    "3AS449MJzuwX7jqxX7yYmc19EiELiRuxffKxYfFkcxfY",
    "14KBXMEiDZj6JWoSfyHKM8n8hAuF6rjJdXQRbaD9nZe",
    "3uaYZQCCLsg8ZMbMtrEyHLNVR4XLmJUYXHfZGrkJj48a",
    "GrkYZgtiQGmZrSbSc7MPJfo6UL9zo2uP5sKPNB7nUyEa",
    "2j668MqJFYoxUwxeFv7zfT8jo9DaQ92Siu3hvc6U7t1F",
    "3pjpB6CQz5gkwMy7XAxStNzrsmdH6pMvXzs8Uy8rqxMk"
]

CSV_FILE = os.path.join(os.getcwd(), "wallet_ca_events.csv")
SEEN_FILE = os.path.join(os.getcwd(), "seen_signatures.json")

POLL_INTERVAL = 20
SIG_LIMIT = 20
THROTTLE = 0.12
HAS_PLYER = False

# ---------------- WALLET VALIDATION (PRIDĖTA ČIA) ----------------
def validate_wallet_address(wallet):
    """Validuoti wallet adresą"""
    if not wallet or not isinstance(wallet, str):
        return False
    if len(wallet) != 44:
        return False
    return True

def get_valid_wallets():
    """Gauti tik validžius wallet'us"""
    valid_wallets = []
    for wallet in WATCHED_WALLETS:
        if validate_wallet_address(wallet):
            valid_wallets.append(wallet)
        else:
            print(f"❌ Removing invalid wallet: {wallet}")
    return valid_wallets

# INICIJUOTI VALID_WALLETS čia, ne main() funkcijoje
VALID_WALLETS = get_valid_wallets()

# ---------------- LIKĘS KODAS BE PAKEITIMŲ ----------------
session = requests.Session()

def validate_config():
    """Validuoti konfigūraciją"""
    assert len(RPC_ENDPOINTS) > 0, "Need at least one RPC endpoint"
    assert len(VALID_WALLETS) > 0, "Need at least one wallet to watch"
    assert POLL_INTERVAL >= 5, "Poll interval too short"
    assert THROTTLE >= 0.1, "Throttle too aggressive"

def safe_rpc_call(method, params, timeout=10, max_retries=3):
    """Saugus RPC call su retry mechanizmu"""
    for attempt in range(max_retries):
        result = rpc_call(method, params, timeout)
        if result is not None:
            return result
        if attempt < max_retries - 1:
            time.sleep(1 * (attempt + 1))
    return None

def rpc_call(method, params, timeout=10):
    """RPC call su failover per visus endpoint'us"""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_err = None
    for rpc in RPC_ENDPOINTS:
        try:
            r = session.post(rpc, json=payload, timeout=timeout)
            if r.status_code != 200:
                last_err = f"{rpc} HTTP {r.status_code}"
                continue
            j = r.json()
            if "result" in j and j["result"] is not None:
                return j["result"]
            last_err = j.get("error", {}).get("message", "no result")
        except Exception as e:
            last_err = str(e)
            continue
    print(f"RPC failed for {method}: {last_err}")
    return None

def init_csv():
    """Inicializuoti CSV failą"""
    if not os.path.exists(CSV_FILE):
        try:
            with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["timestamp_local","wallet","signature","action","mint","amount","fee_sol","block_time"])
            print(f"✅ Initialized CSV: {CSV_FILE}")
        except Exception as e:
            print(f"❌ CSV init error: {e}")
    else:
        print(f"✅ CSV exists: {CSV_FILE}")

def load_seen():
    """Įkelti jau matytas transakcijas"""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for w in VALID_WALLETS:
                data.setdefault(w, [])
            return {k: set(v) for k, v in data.items()}
        except Exception as e:
            print(f"Seen load error: {e}")
    return {w: set() for w in VALID_WALLETS}

def atomic_write_seen(seen_data):
    """Išsaugoti matytas transakcijas"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in seen_data.items()}, f, indent=2)
    except Exception as e:
        print(f"Seen save error: {e}")

def simple_csv_row(row):
    """Paprastas CSV įrašymas"""
    try:
        file_exists = os.path.exists(CSV_FILE)
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp_local","wallet","signature","action","mint","amount","fee_sol","block_time"])
            writer.writerow([
                row.get("timestamp_local", ""),
                row.get("wallet", ""),
                row.get("signature", ""),
                row.get("action", ""),
                row.get("mint", ""),
                row.get("amount", 0),
                row.get("fee_sol", 0),
                row.get("block_time", "")
            ])
        print(f"✅ CSV: {row['action']} {row['amount']} {row['mint'][:12]}...")
    except Exception as e:
        print(f"❌ CSV write error: {e}")

def notify_user(title, message):
    """Pranešti vartotojui"""
    if HAS_PLYER:
        try:
            notification.notify(title=title, message=message, timeout=6)
        except Exception:
            print(f"NOTIFY: {title} - {message}")
    else:
        print(f"NOTIFY: {title} - {message}")

def validate_transaction_data(tx_json):
    """Validuoti transakcijos duomenis"""
    if not tx_json or not isinstance(tx_json, dict):
        return False
    if "meta" not in tx_json or "transaction" not in tx_json:
        return False
    if tx_json.get("meta") is None:
        return False
    return True

def extract_token_deltas(meta, wallet):
    """Išgauti tokenų balanso pokyčius"""
    if not meta:
        return {}
    token_deltas = {}
    try:
        pre_map = {}
        for e in meta.get("preTokenBalances", []):
            if isinstance(e, dict) and e.get("owner") == wallet:
                mint = e.get("mint")
                ui_amount = (e.get("uiTokenAmount") or {}).get("uiAmount")
                if mint and ui_amount is not None:
                    pre_map[mint] = pre_map.get(mint, 0.0) + float(ui_amount)
        
        post_map = {}
        for e in meta.get("postTokenBalances", []):
            if isinstance(e, dict) and e.get("owner") == wallet:
                mint = e.get("mint")
                ui_amount = (e.get("uiTokenAmount") or {}).get("uiAmount")
                if mint and ui_amount is not None:
                    post_map[mint] = post_map.get(mint, 0.0) + float(ui_amount)
        
        for mint in set(list(pre_map.keys()) + list(post_map.keys())):
            delta = post_map.get(mint, 0.0) - pre_map.get(mint, 0.0)
            if abs(delta) > 1e-9:
                token_deltas[mint] = delta
    except Exception as e:
        print(f"Token delta error: {e}")
    return token_deltas

def extract_fee_and_sol_delta(meta, tx_json, wallet):
    """Išgauti mokesčius ir SOL balanso pokytį"""
    try:
        fee_sol = meta.get("fee", 0) / 1e9
        message = tx_json.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])
        pubkeys = [k.get("pubkey") if isinstance(k, dict) else k for k in account_keys]
        indices = [i for i, pk in enumerate(pubkeys) if pk == wallet]
        
        sol_delta = 0.0
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        if pre_balances and post_balances and len(pre_balances) == len(post_balances):
            for i in indices:
                if i < len(pre_balances) and i < len(post_balances):
                    sol_delta += (post_balances[i] - pre_balances[i])
        return fee_sol, sol_delta / 1e9
    except Exception as e:
        return 0.0, 0.0

def process_transaction_for_wallet(signature, wallet):
    """Apdoroti vieną transakciją"""
    try:
        tx_json = safe_rpc_call("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx_json or not validate_transaction_data(tx_json):
            return []
        
        meta = tx_json.get("meta", {})
        token_deltas = extract_token_deltas(meta, wallet)
        fee_sol, _ = extract_fee_and_sol_delta(meta, tx_json, wallet)
        
        rows = []
        for mint, delta in token_deltas.items():
            if delta > 1e-9:
                action, amount = "BUY", float(delta)
            elif delta < -1e-9:
                action, amount = "SELL", float(abs(delta))
            else:
                action, amount = "TRANSFER", 0.0
            
            rows.append({
                "timestamp_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "wallet": wallet,
                "signature": signature,
                "action": action,
                "mint": mint,
                "amount": round(amount, 9),
                "fee_sol": round(fee_sol, 9),
                "block_time": tx_json.get("blockTime")
            })
        return rows
    except Exception as e:
        print(f"Transaction process error: {e}")
        return []

def process_wallet_transactions(wallet, seen):
    """Apdoroti visus wallet'o transakcijas"""
    try:
        sigs = safe_rpc_call("getSignaturesForAddress", [wallet, {"limit": SIG_LIMIT}])
        if not sigs:
            return seen
        
        new_sigs = 0
        for entry in sigs:
            if not isinstance(entry, dict):
                continue
                
            sig = entry.get("signature")
            if not sig or sig in seen.get(wallet, set()):
                continue
            
            rows = process_transaction_for_wallet(sig, wallet)
            for r in rows:
                simple_csv_row(r)
                mint_short = r['mint'][:8] + '...' if len(r['mint']) > 8 else r['mint']
                notify_user("Wallet CA event", f"{r['action']} {r['amount']} of {mint_short} ({wallet[:6]}...)")
                print(f"{r['timestamp_local']} | {r['wallet'][:8]}... | {r['action']:6} | {r['amount']:8.4f} | {r['mint'][:12]}... | fee {r['fee_sol']:.6f}")
            
            seen[wallet].add(sig)
            new_sigs += 1
            time.sleep(THROTTLE)
        
        if new_sigs > 0:
            print(f"📥 Processed {new_sigs} new transactions for {wallet[:8]}...")
    except Exception as e:
        print(f"Wallet process error: {e}")
    return seen

# ---------------- WEB DASHBOARD ----------------
class CSVHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Wallet CA Tracker</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    * {
                        margin: 0;
                        padding: 0;
                        box-sizing: border-box;
                    }
                    body { 
                        font-family: 'Segoe UI', Arial, sans-serif; 
                        margin: 0; 
                        padding: 20px; 
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        min-height: 100vh;
                    }
                    .container {
                        max-width: 1400px;
                        margin: 0 auto;
                        background: white;
                        border-radius: 15px;
                        box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                        overflow: hidden;
                    }
                    .header {
                        background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
                        color: white;
                        padding: 30px;
                        text-align: center;
                    }
                    .header h1 {
                        margin: 0;
                        font-size: 2.5em;
                        font-weight: 300;
                    }
                    .stats {
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 20px;
                        padding: 20px;
                        background: #f8f9fa;
                    }
                    .stat-card {
                        background: white;
                        padding: 20px;
                        border-radius: 10px;
                        text-align: center;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    }
                    .stat-number {
                        font-size: 2em;
                        font-weight: bold;
                        color: #2c3e50;
                    }
                    .stat-label {
                        color: #7f8c8d;
                        font-size: 0.9em;
                    }
                    .table-container {
                        overflow-x: auto;
                        padding: 20px;
                    }
                    table {
                        width: 100%;
                        border-collapse: collapse;
                        margin: 20px 0;
                        font-size: 0.85em;
                        min-width: 1200px;
                    }
                    th {
                        background: #34495e;
                        color: white;
                        padding: 12px 8px;
                        text-align: left;
                        font-weight: 600;
                        position: sticky;
                        top: 0;
                    }
                    td {
                        padding: 10px 8px;
                        border-bottom: 1px solid #ecf0f1;
                        max-width: 200px;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }
                    tr:hover {
                        background: #f8f9fa;
                    }
                    .buy { 
                        color: #27ae60; 
                        font-weight: bold;
                    }
                    .sell { 
                        color: #e74c3c; 
                        font-weight: bold;
                    }
                    .transfer {
                        color: #95a5a6;
                    }
                    .refresh-info {
                        text-align: center;
                        padding: 10px;
                        background: #ecf0f1;
                        color: #7f8c8d;
                        font-size: 0.9em;
                    }
                    .address-cell {
                        cursor: pointer;
                        position: relative;
                        max-width: 180px;
                    }
                    .address-cell:hover::after {
                        content: attr(data-full);
                        position: absolute;
                        left: 0;
                        top: 100%;
                        background: #2c3e50;
                        color: white;
                        padding: 8px;
                        border-radius: 4px;
                        white-space: nowrap;
                        z-index: 1000;
                        font-size: 0.8em;
                        max-width: 400px;
                        overflow: hidden;
                        text-overflow: ellipsis;
                    }
                    .copy-btn {
                        background: #3498db;
                        color: white;
                        border: none;
                        padding: 3px 8px;
                        border-radius: 3px;
                        cursor: pointer;
                        font-size: 0.75em;
                        margin-left: 5px;
                        transition: background 0.2s;
                    }
                    .copy-btn:hover {
                        background: #2980b9;
                    }
                    .timestamp {
                        min-width: 140px;
                    }
                    .action {
                        min-width: 70px;
                        text-align: center;
                    }
                    .amount {
                        min-width: 100px;
                        text-align: right;
                    }
                    .fee {
                        min-width: 90px;
                        text-align: right;
                    }
                    @media (max-width: 768px) {
                        .container {
                            margin: 10px;
                            border-radius: 10px;
                        }
                        .header {
                            padding: 20px;
                        }
                        .header h1 {
                            font-size: 2em;
                        }
                        .stats {
                            grid-template-columns: repeat(2, 1fr);
                            padding: 15px;
                            gap: 15px;
                        }
                        .table-container {
                            padding: 10px;
                        }
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>💰 Wallet CA Tracker</h1>
                        <p>Real-time Solana wallet transaction monitoring</p>
                        <p style="font-size: 0.8em; opacity: 0.8;">🚀 Hosted on Render.com</p>
                    </div>
            """
            
            try:
                if os.path.exists(CSV_FILE):
                    with open(CSV_FILE, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                        rows.reverse()  # newest first
                    
                    # Statistics
                    total_tx = len(rows)
                    unique_wallets = len(set(row['wallet'] for row in rows if row.get('wallet')))
                    unique_tokens = len(set(row['mint'] for row in rows if row.get('mint')))
                    latest_time = rows[0]['timestamp_local'] if rows else 'No transactions'
                    
                    html += f"""
                    <div class="stats">
                        <div class="stat-card">
                            <div class="stat-number">{total_tx}</div>
                            <div class="stat-label">Total Transactions</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number">{len(VALID_WALLETS)}</div>
                            <div class="stat-label">Watched Wallets</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number">{unique_wallets}</div>
                            <div class="stat-label">Active Wallets</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number">{unique_tokens}</div>
                            <div class="stat-label">Unique Tokens</div>
                        </div>
                    </div>
                    """
                    
                    # Transactions Table
                    html += """
                    <div class="table-container">
                        <h2 style="color: #2c3e50; margin-bottom: 20px;">Latest Transactions</h2>
                        <table>
                            <thead>
                                <tr>
                                    <th>Time</th>
                                    <th>Wallet Address</th>
                                    <th>Action</th>
                                    <th>Amount</th>
                                    <th>Token CA Address</th>
                                    <th>Fee (SOL)</th>
                                    <th>Signature</th>
                                </tr>
                            </thead>
                            <tbody>
                    """
                    
                    for row in rows[:50]:
                        if not all(key in row for key in ['wallet', 'mint', 'signature']):
                            continue
                            
                        action_class = {
                            'BUY': 'buy',
                            'SELL': 'sell', 
                            'TRANSFER': 'transfer'
                        }.get(row.get('action', ''), '')
                        
                        try:
                            amount = float(row.get('amount', 0))
                            fee = float(row.get('fee_sol', 0))
                        except (ValueError, TypeError):
                            amount = 0
                            fee = 0
                        
                        wallet = row.get('wallet', '')
                        mint = row.get('mint', '')
                        signature = row.get('signature', '')
                        timestamp = row.get('timestamp_local', '')
                        
                        html += f"""
                                <tr>
                                    <td class="timestamp">{timestamp}</td>
                                    <td class="address-cell" data-full="{wallet}">
                                        {wallet[:10]}...{wallet[-10:] if len(wallet) > 20 else ''}
                                        <button class="copy-btn" onclick="copyToClipboard('{wallet}')">Copy</button>
                                    </td>
                                    <td class="action {action_class}">{row.get('action', '')}</td>
                                    <td class="amount">{amount:,.2f}</td>
                                    <td class="address-cell" data-full="{mint}">
                                        {mint[:10]}...{mint[-10:] if len(mint) > 20 else ''}
                                        <button class="copy-btn" onclick="copyToClipboard('{mint}')">Copy</button>
                                    </td>
                                    <td class="fee">{fee:.6f}</td>
                                    <td class="address-cell" data-full="{signature}">
                                        {signature[:10]}...{signature[-10:] if len(signature) > 20 else ''}
                                        <button class="copy-btn" onclick="copyToClipboard('{signature}')">Copy</button>
                                    </td>
                                </tr>
                        """
                    
                    html += """
                            </tbody>
                        </table>
                    </div>
                    """
                else:
                    html += """
                    <div style="padding: 40px; text-align: center;">
                        <h2 style="color: #7f8c8d;">No transactions yet</h2>
                        <p>Waiting for wallet activity...</p>
                    </div>
                    """
                    
            except Exception as e:
                html += f"""
                <div style="padding: 40px; text-align: center; color: #e74c3c;">
                    <h2>Error loading data</h2>
                    <p>{str(e)}</p>
                </div>
                """
            
            html += """
                <div class="refresh-info">
                    🔄 Page auto-refreshes every 5 seconds | Made with Python | Hosted on Render.com
                </div>
            </div>
            <script>
                // Auto-refresh every 5 seconds
                setTimeout(() => location.reload(), 5000);
                
                // Copy to clipboard function
                function copyToClipboard(text) {
                    navigator.clipboard.writeText(text).then(function() {
                        // Show subtle notification
                        const notification = document.createElement('div');
                        notification.style.cssText = `
                            position: fixed;
                            top: 20px;
                            right: 20px;
                            background: #27ae60;
                            color: white;
                            padding: 10px 20px;
                            border-radius: 5px;
                            z-index: 10000;
                            font-size: 14px;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                        `;
                        notification.textContent = '✓ Copied to clipboard!';
                        document.body.appendChild(notification);
                        
                        setTimeout(() => {
                            document.body.removeChild(notification);
                        }, 2000);
                    }).catch(function(err) {
                        console.error('Could not copy text: ', err);
                    });
                }
            </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        else:
            super().do_GET()

def start_simple_server():
    """Paleisti web serverį su Render.com PORT"""
    try:
        with socketserver.TCPServer(("", PORT), CSVHandler) as httpd:
            print(f"🌐 Web dashboard started: http://0.0.0.0:{PORT}")
            if RENDER:
                print("🚀 Running on Render.com")
            else:
                print("💻 Running locally")
            httpd.serve_forever()
    except OSError as e:
        print(f"❌ Port {PORT} error: {e}")
        with socketserver.TCPServer(("", 8001), CSVHandler) as httpd:
            print(f"🌐 Web dashboard started on fallback: http://0.0.0.0:8001")
            httpd.serve_forever()

def main():
    """Pagrindinė programa"""
    print("🚀 Starting Wallet CA Tracker with Web Dashboard...")
    
    if RENDER:
        print("🌍 Render.com environment detected")
    
    if not VALID_WALLETS:
        print("❌ No valid wallets!")
        return
    
    # Start web server in background thread
    server_thread = threading.Thread(target=start_simple_server, daemon=True)
    server_thread.start()
    
    print(f"✅ Web dashboard available on port {PORT}")
    
    try:
        validate_config()
        print("✅ Configuration validated successfully")
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return
    
    init_csv()
    seen = load_seen()
    
    for w in VALID_WALLETS:
        seen.setdefault(w, set())
    
    print(f"👀 Watching {len(VALID_WALLETS)} wallets")
    print(f"⏰ Poll interval: {POLL_INTERVAL}s")
    print("⏹️  Press Ctrl+C to stop\n")

    error_count = 0
    max_errors = 10
    
    try:
        while True:
            try:
                for w in VALID_WALLETS:
                    seen = process_wallet_transactions(w, seen)
                
                atomic_write_seen(seen)
                error_count = 0
                print(f"💤 Sleeping for {POLL_INTERVAL}s...")
                
            except Exception as e:
                error_count += 1
                print(f"⚠️ Main loop error #{error_count}: {e}")
                if error_count >= max_errors:
                    print("❌ Too many errors, exiting...")
                    break
                time.sleep(5)
            
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
        atomic_write_seen(seen)
        print("✅ Clean shutdown completed")
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        atomic_write_seen(seen)
        print("✅ Emergency shutdown completed")

if __name__ == "__main__":
    main()