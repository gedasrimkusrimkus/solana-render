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
import urllib.parse

# ---------------- RENDER.COM KONFIGŪRACIJA ----------------
PORT = int(os.environ.get('PORT', 8000))
RENDER = os.environ.get('RENDER', False)

# ---------------- CONFIG ----------------
RPC_ENDPOINTS = [
    "https://rpc.ankr.com/solana",
    "https://api.mainnet-beta.solana.com", 
    "https://solana-rpc.publicnode.com"
]

# Wallet'ų failas
WALLETS_FILE = os.path.join(os.getcwd(), "watched_wallets.json")

# Default wallet'ai
DEFAULT_WALLETS = [
    "4Vgu5AHT1ndczhdgqAipNDqLsCPjBS5jMXkEg8yzhT9c",
    "8AHdpimBNQhhb6FwUy49txZ7ahWGQz2iECbbN843RfZY",
    "3AS449MJzuwX7jqxX7yYmc19EiELiRuxffKxYfFkcxfY",
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

# ---------------- WALLET MANAGEMENT ----------------
def load_wallets():
    """Įkelti wallet'us iš failo arba naudoti default'us"""
    try:
        if os.path.exists(WALLETS_FILE):
            with open(WALLETS_FILE, 'r') as f:
                wallets = json.load(f)
                print(f"✅ Loaded {len(wallets)} wallets from file")
                return wallets
    except Exception as e:
        print(f"❌ Error loading wallets: {e}")
    
    print("✅ Using default wallets")
    return DEFAULT_WALLETS.copy()

def save_wallets(wallets):
    """Išsaugoti wallet'us į failą"""
    try:
        with open(WALLETS_FILE, 'w') as f:
            json.dump(wallets, f, indent=2)
        print(f"✅ Saved {len(wallets)} wallets to file")
    except Exception as e:
        print(f"❌ Error saving wallets: {e}")

def validate_wallet_address(wallet):
    """Validuoti wallet adresą"""
    if not wallet or not isinstance(wallet, str):
        return False
    if len(wallet) != 44:
        return False
    # Paprastas Solana address validavimas
    valid_chars = set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
    return all(c in valid_chars for c in wallet)

def get_valid_wallets():
    """Gauti tik validžius wallet'us"""
    wallets = load_wallets()
    valid_wallets = []
    for wallet in wallets:
        if validate_wallet_address(wallet):
            valid_wallets.append(wallet)
        else:
            print(f"❌ Removing invalid wallet: {wallet}")
    return valid_wallets

# INICIJUOTI VALID_WALLETS kaip global kintamąjį
VALID_WALLETS = get_valid_wallets()

# ---------------- LIKĘS KODAS BE PAKEITIMŲ ----------------
session = requests.Session()

def validate_config():
    """Validuoti konfigūraciją"""
    if len(RPC_ENDPOINTS) == 0:
        raise Exception("Need at least one RPC endpoint")
    if len(VALID_WALLETS) == 0:
        raise Exception("Need at least one wallet to watch")
    if POLL_INTERVAL < 5:
        raise Exception("Poll interval too short")
    if THROTTLE < 0.1:
        raise Exception("Throttle too aggressive")

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
            # Pridėti naujus wallet'us į seen data
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
    """Paprastas CSV įrašymas su naujausiais įrašais viršuje"""
    try:
        # Perskaityti esamus įrašus
        existing_rows = []
        if os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if headers:
                    existing_rows = list(reader)
        
        # Įrašyti naują įrašą + esamus įrašus
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Headers
            writer.writerow(["timestamp_local","wallet","signature","action","mint","amount","fee_sol","block_time"])
            # Naujas įrašas
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
            # Esami įrašai
            writer.writerows(existing_rows)
        
        print(f"✅ CSV: {row['action']} {row['amount']} {row['mint'][:12]}...")
    except Exception as e:
        print(f"❌ CSV write error: {e}")

def notify_user(title, message):
    """Pranešti vartotojui su garso signalu"""
    print(f"NOTIFY: {title} - {message}")
    
    # Garso pranešimas
    try:
        import winsound
        
        if "BUY" in title.upper():
            # Trumpas optimistiškas garsas pirkimui
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
            print("🔔 BUY sound played!")
        elif "SELL" in title.upper():
            # Ilgesnis garsas pardavimui
            winsound.PlaySound("SystemHand", winsound.SND_ALIAS)
            print("🔔 SELL sound played!")
        else:
            # Standartinis garsas
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)
            print("🔔 Transaction sound played!")
            
    except Exception as e:
        print(f"🔇 Sound not available: {e}")

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

# ---------------- WEB DASHBOARD SU WALLET PRIDĖJIMU ----------------
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
                    .wallet-management {
                        background: #e3f2fd;
                        padding: 20px;
                        margin: 20px;
                        border-radius: 10px;
                        border: 2px solid #2196f3;
                        max-width: 100%;
                        overflow: hidden;
                    }
                    .wallet-form {
                        display: flex;
                        gap: 10px;
                        align-items: end;
                        margin-bottom: 15px;
                        flex-wrap: wrap;
                    }
                    .wallet-input {
                        flex: 1;
                        min-width: 300px;
                    }
                    .wallet-input label {
                        display: block;
                        margin-bottom: 5px;
                        font-weight: bold;
                        color: #2c3e50;
                    }
                    .wallet-input input {
                        width: 100%;
                        padding: 12px;
                        border: 2px solid #bdc3c7;
                        border-radius: 8px;
                        font-size: 16px;
                        transition: border-color 0.3s;
                        font-family: monospace;
                    }
                    .wallet-input input:focus {
                        border-color: #3498db;
                        outline: none;
                    }
                    .add-btn {
                        padding: 12px 24px;
                        background: #27ae60;
                        color: white;
                        border: none;
                        border-radius: 8px;
                        cursor: pointer;
                        font-size: 16px;
                        transition: background 0.3s;
                        white-space: nowrap;
                    }
                    .add-btn:hover {
                        background: #219a52;
                    }
                    .current-wallets {
                        margin-top: 20px;
                    }
                    .wallet-list {
                        display: grid;
                        grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
                        gap: 12px;
                        margin-top: 10px;
                    }
                    .wallet-item {
                        background: white;
                        padding: 12px 15px;
                        border-radius: 8px;
                        border: 1px solid #ecf0f1;
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        transition: transform 0.2s;
                    }
                    .wallet-item:hover {
                        transform: translateY(-2px);
                        box-shadow: 0 4px 8px rgba(0,0,0,0.15);
                    }
                    .wallet-address {
                        flex: 1;
                        font-family: 'Courier New', monospace;
                        font-size: 13px;
                        word-break: break-all;
                        color: #2c3e50;
                        font-weight: 500;
                    }
                    .remove-btn {
                        background: #e74c3c;
                        color: white;
                        border: none;
                        padding: 6px 12px;
                        border-radius: 5px;
                        cursor: pointer;
                        font-size: 12px;
                        margin-left: 10px;
                        white-space: nowrap;
                        transition: background 0.3s;
                    }
                    .remove-btn:hover {
                        background: #c0392b;
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
                        .wallet-management {
                            margin: 10px;
                            padding: 15px;
                        }
                        .wallet-form {
                            flex-direction: column;
                        }
                        .wallet-input {
                            min-width: 100%;
                        }
                        .wallet-list {
                            grid-template-columns: 1fr;
                        }
                        .wallet-item {
                            flex-direction: column;
                            align-items: flex-start;
                            gap: 10px;
                        }
                        .remove-btn {
                            align-self: flex-end;
                        }
                    }
                    @media (max-width: 480px) {
                        .wallet-address {
                            font-size: 12px;
                        }
                        .wallet-list {
                            grid-template-columns: 1fr;
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
                # Statistics
                total_tx = 0
                unique_wallets = len(VALID_WALLETS)
                unique_tokens = 0
                
                if os.path.exists(CSV_FILE):
                    with open(CSV_FILE, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                        total_tx = len(rows)
                        unique_tokens = len(set(row['mint'] for row in rows if row.get('mint')))
                
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
                
                # Wallet Management Section
                html += """
                <div class="wallet-management">
                    <h3>🔧 Wallet Management</h3>
                    <form class="wallet-form" action="/add-wallet" method="post">
                        <div class="wallet-input">
                            <label for="wallet">Add New Wallet:</label>
                            <input type="text" id="wallet" name="wallet" 
                                   placeholder="Enter Solana wallet address (44 characters)" 
                                   required maxlength="44" pattern="[A-Za-z0-9]{44}"
                                   title="Solana wallet address must be exactly 44 characters">
                        </div>
                        <button type="submit" class="add-btn">➕ Add Wallet</button>
                    </form>
                    
                    <div class="current-wallets">
                        <h4>Currently Watching (<span id="wallet-count">""" + str(len(VALID_WALLETS)) + """</span> wallets):</h4>
                        <div class="wallet-list" id="wallet-list">
                """
                
                for wallet in VALID_WALLETS:
                    html += f"""
                            <div class="wallet-item">
                                <span class="wallet-address">{wallet}</span>
                                <button class="remove-btn" onclick="removeWallet('{wallet}')">🗑️ Remove</button>
                            </div>
                    """
                
                html += """
                        </div>
                    </div>
                </div>
                """
                
                # Transactions Table
                if os.path.exists(CSV_FILE):
                    with open(CSV_FILE, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                        # Nereikia reverse, nes dabar CSV jau turi naujausius viršuje
                    
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
                    
                    for row in rows[:50]:  # Rodyti tik pirmus 50 įrašų
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
                                    <td class="address-cell">
                                        {wallet[:10]}...{wallet[-10:] if len(wallet) > 20 else ''}
                                        <button class="copy-btn" onclick="copyToClipboard('{wallet}')">Copy</button>
                                    </td>
                                    <td class="action {action_class}">{row.get('action', '')}</td>
                                    <td class="amount">{amount:,.2f}</td>
                                    <td class="address-cell">
                                        {mint[:10]}...{mint[-10:] if len(mint) > 20 else ''}
                                        <button class="copy-btn" onclick="copyToClipboard('{mint}')">Copy</button>
                                    </td>
                                    <td class="fee">{fee:.6f}</td>
                                    <td class="address-cell">
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
                        showNotification('✓ Copied to clipboard!', 'success');
                    }).catch(function(err) {
                        console.error('Could not copy text: ', err);
                        showNotification('❌ Copy failed', 'error');
                    });
                }
                
                // Remove wallet function
                function removeWallet(wallet) {
                    if (confirm('Are you sure you want to remove this wallet?')) {
                        fetch('/remove-wallet', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                            },
                            body: 'wallet=' + encodeURIComponent(wallet)
                        }).then(response => {
                            location.reload();
                        });
                    }
                }
                
                // Show notification
                function showNotification(message, type) {
                    const notification = document.createElement('div');
                    notification.style.cssText = `
                        position: fixed;
                        top: 20px;
                        right: 20px;
                        background: ${type === 'success' ? '#27ae60' : '#e74c3c'};
                        color: white;
                        padding: 12px 24px;
                        border-radius: 5px;
                        z-index: 10000;
                        font-size: 14px;
                        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    `;
                    notification.textContent = message;
                    document.body.appendChild(notification);
                    
                    setTimeout(() => {
                        document.body.removeChild(notification);
                    }, 3000);
                }
            </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        """Handle POST requests for wallet management"""
        if self.path == '/add-wallet':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            parsed_data = urllib.parse.parse_qs(post_data)
            wallet = parsed_data.get('wallet', [''])[0].strip()
            
            if wallet and validate_wallet_address(wallet):
                if wallet not in VALID_WALLETS:
                    VALID_WALLETS.append(wallet)
                    save_wallets(VALID_WALLETS)
                    message = f"✅ Wallet {wallet[:8]}... added successfully!"
                    print(f"➕ Added new wallet: {wallet}")
                else:
                    message = f"⚠️ Wallet already exists!"
            else:
                message = "❌ Invalid wallet address!"
            
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f'<script>alert("{message}"); window.location="/";</script>'.encode())
            
        elif self.path == '/remove-wallet':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            parsed_data = urllib.parse.parse_qs(post_data)
            wallet = parsed_data.get('wallet', [''])[0].strip()
            
            if wallet in VALID_WALLETS:
                VALID_WALLETS.remove(wallet)
                save_wallets(VALID_WALLETS)
                print(f"🗑️ Removed wallet: {wallet}")
            
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<script>window.location="/";</script>')
        else:
            super().do_POST()

def start_simple_server():
    """Paleisti web serverį su Render.com PORT"""
    try:
        with socketserver.TCPServer(("", PORT), CSVHandler) as httpd:
            print(f"🌐 Web dashboard started: http://0.0.0.0:{PORT}")
            print(f"👀 Watching {len(VALID_WALLETS)} wallets:")
            for wallet in VALID_WALLETS:
                print(f"   - {wallet}")
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
    
    # Perkrauti wallet'us
    global VALID_WALLETS
    VALID_WALLETS = get_valid_wallets()
    
    if not VALID_WALLETS:
        print("❌ No valid wallets! Adding default ones...")
        VALID_WALLETS = ["4Vgu5AHT1ndczhdgqAipNDqLsCPjBS5jMXkEg8yzhT9c"]
        save_wallets(VALID_WALLETS)
    
    print(f"✅ Final wallet count: {len(VALID_WALLETS)}")
    
    # Start web server in background thread
    server_thread = threading.Thread(target=start_simple_server, daemon=True)
    server_thread.start()
    
    print(f"✅ Web dashboard available on port {PORT}")
    
    # Tik dabar kiti dalykai
    try:
        validate_config()
        print("✅ Configuration validated successfully")
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        print("🔄 Continuing anyway...")
    
    init_csv()
    seen = load_seen()
    
    for w in VALID_WALLETS:
        seen.setdefault(w, set())
    
    print(f"👀 Watching {len(VALID_WALLETS)} wallets")
    print(f"⏰ Poll interval: {POLL_INTERVAL}s")
    print("⏹️  Press Ctrl+C to stop\n")
    print("🌐 Web dashboard should be running now!")

    error_count = 0
    max_errors = 10
    
    try:
        while True:
            try:
                current_wallets = get_valid_wallets()
                for w in current_wallets:
                    seen.setdefault(w, set())
                
                for w in current_wallets:
                    seen = process_wallet_transactions(w, seen)
                
                atomic_write_seen(seen)
                error_count = 0
                print(f"💤 Sleeping for {POLL_INTERVAL}s...")
                
            except Exception as e:
                error_count += 1
                print(f"⚠️ Main loop error #{error_count}: {e}")
                if error_count >= max_errors:
                    print("❌ Too many errors, but keeping web server alive...")
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