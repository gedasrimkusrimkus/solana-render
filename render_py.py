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
import math
from collections import Counter, defaultdict

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

# ---------------- STATISTICS FUNCTIONS ----------------
def get_wallet_activity_stats():
    """Gauti wallet'ų aktyvumo statistiką"""
    stats = {
        'total_transactions': 0,
        'wallet_activity': {},
        'recent_transactions': 0,
        'top_tokens': [],
        'hourly_activity': defaultdict(int)
    }
    
    try:
        if not os.path.exists(CSV_FILE):
            return stats
            
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        stats['total_transactions'] = len(rows)
        
        # Wallet activity counting
        wallet_counts = Counter()
        token_counts = Counter()
        
        for row in rows:
            wallet = row.get('wallet', '')
            token = row.get('mint', '')
            timestamp = row.get('timestamp_local', '')
            
            if wallet:
                wallet_counts[wallet] += 1
                
            if token:
                token_counts[token] += 1
                
            # Count recent transactions (last 24 hours)
            try:
                tx_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - tx_time).total_seconds() <= 24 * 3600:
                    stats['recent_transactions'] += 1
                    
                # Hourly activity
                hour_key = tx_time.strftime("%H:00")
                stats['hourly_activity'][hour_key] += 1
            except:
                pass
        
        # Convert to sorted list
        stats['wallet_activity'] = [
            {'wallet': wallet, 'count': count, 'short': f"{wallet[:8]}...{wallet[-6:]}"}
            for wallet, count in wallet_counts.most_common()
        ]
        
        stats['top_tokens'] = [
            {'token': token, 'count': count, 'short': f"{token[:8]}...{token[-6]}" if len(token) > 15 else token}
            for token, count in token_counts.most_common(10)
        ]
        
    except Exception as e:
        print(f"❌ Error calculating stats: {e}")
        
    return stats

def generate_activity_chart(wallet_activity, max_height=120):
    """Sugeneruoti ASCII stulpelinę diagramą"""
    if not wallet_activity:
        return "No activity data available"
    
    chart_lines = []
    max_count = max([wa['count'] for wa in wallet_activity]) if wallet_activity else 1
    
    for wa in wallet_activity[:8]:  # Top 8 wallets
        wallet_short = wa['short']
        count = wa['count']
        
        # Calculate bar length
        bar_length = int((count / max_count) * 20) if max_count > 0 else 0
        bar = '█' * bar_length
        
        chart_lines.append(f"{wallet_short:<16} {bar} {count}")
    
    return "\n".join(chart_lines)

def generate_hourly_chart(hourly_activity):
    """Sugeneruoti valandinės aktyvumo diagramą"""
    if not hourly_activity:
        return "No hourly data available"
    
    # Sort hours
    sorted_hours = sorted(hourly_activity.keys())
    max_count = max(hourly_activity.values()) if hourly_activity else 1
    
    chart_lines = ["🕒 Hourly Activity:"]
    
    for hour in sorted_hours[-12:]:  # Last 12 hours
        count = hourly_activity[hour]
        bar_length = int((count / max_count) * 15) if max_count > 0 else 0
        bar = '█' * bar_length
        
        chart_lines.append(f"{hour:<6} {bar} {count}")
    
    return "\n".join(chart_lines)

# ---------------- PATAISYTI FUNKCIJAS ----------------
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
            from plyer import notification
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
    """Apdoroti vieną transakciją - PATAISYTA!"""
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
                continue  # Skip zero deltas
            
            # PATAISYTA: Naudoti teisingą blockTime lauką
            block_time = tx_json.get("blockTime")
            if block_time:
                # Konvertuoti blockTime į skaitomą datą
                try:
                    dt = datetime.fromtimestamp(block_time, tz=timezone.utc)
                    readable_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    readable_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            else:
                readable_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            
            rows.append({
                "timestamp_local": readable_time,
                "wallet": wallet,
                "signature": signature,
                "action": action,
                "mint": mint,
                "amount": round(amount, 9),
                "fee_sol": round(fee_sol, 9),
                "block_time": block_time
            })
        return rows
    except Exception as e:
        print(f"Transaction process error: {e}")
        return []

def process_wallet_transactions(wallet, seen):
    """Apdoroti visus wallet'o transakcijas - PATAISYTA!"""
    try:
        sigs = safe_rpc_call("getSignaturesForAddress", [wallet, {"limit": SIG_LIMIT}])
        if not sigs:
            return seen
        
        new_sigs = 0
        for entry in sigs:
            if not isinstance(entry, dict):
                continue
                
            sig = entry.get("signature")
            if not sig:
                continue
                
            # PATIKSLINTA: Tikrinti ar signature jau matytas
            if sig in seen.get(wallet, set()):
                continue
            
            rows = process_transaction_for_wallet(sig, wallet)
            if not rows:
                continue
                
            for r in rows:
                # PAPILDOMAS PATIKRINIMAS: ar transakcija jau egzistuoja CSV faile
                if not is_transaction_already_recorded(r['signature'], r['mint'], r['action'], r['amount']):
                    simple_csv_row(r)
                    mint_short = r['mint'][:8] + '...' if len(r['mint']) > 8 else r['mint']
                    notify_user("Wallet CA event", f"{r['action']} {r['amount']} of {mint_short} ({wallet[:6]}...)")
                    print(f"{r['timestamp_local']} | {r['wallet'][:8]}... | {r['action']:6} | {r['amount']:8.4f} | {r['mint'][:12]}... | fee {r['fee_sol']:.6f}")
                else:
                    print(f"⏭️  Skipping duplicate: {r['action']} {r['amount']} {r['mint'][:12]}...")
            
            seen[wallet].add(sig)
            new_sigs += 1
            time.sleep(THROTTLE)
        
        if new_sigs > 0:
            print(f"📥 Processed {new_sigs} new transactions for {wallet[:8]}...")
            
            # Rodyti statistiką po kiekvieno apdorojimo
            stats = get_wallet_activity_stats()
            if stats['wallet_activity']:
                print("📊 Wallet Activity Ranking:")
                print(generate_activity_chart(stats['wallet_activity']))
                print()
                
    except Exception as e:
        print(f"Wallet process error: {e}")
    return seen

def is_transaction_already_recorded(signature, mint, action, amount):
    """Patikrinti ar transakcija jau egzistuoja CSV faile - NAUJA FUNKCIJA!"""
    try:
        if not os.path.exists(CSV_FILE):
            return False
            
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get('signature') == signature and 
                    row.get('mint') == mint and 
                    row.get('action') == action and
                    abs(float(row.get('amount', 0)) - amount) < 1e-6):
                    return True
    except Exception as e:
        print(f"❌ Error checking duplicate: {e}")
    
    return False

# ---------------- LIKĘS KODAS BE PAKEITIMŲ ----------------
# (CSVHandler klasė ir main() funkcija lieka tokios pačios)
# ... [CSVHandler klasė ir main() funkcija lieka nepakitusios] ...

class CSVHandler(http.server.SimpleHTTPRequestHandler):
    # ... [visas HTML kodas lieka toks pat] ...
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            # Gauti statistiką
            stats = get_wallet_activity_stats()
            
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Wallet CA Tracker</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    /* ... [CSS styles remain the same] ... */
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>💰 Wallet CA Tracker</h1>
                        <p>Real-time Solana wallet transaction monitoring</p>
                    </div>
            """
            
            try:
                # Statistics
                html += f"""
                <div class="stats">
                    <div class="stat-card">
                        <div class="stat-number">{stats['total_transactions']}</div>
                        <div class="stat-label">Total Transactions</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{len(VALID_WALLETS)}</div>
                        <div class="stat-label">Watched Wallets</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{len(stats['wallet_activity'])}</div>
                        <div class="stat-label">Active Wallets</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{stats['recent_transactions']}</div>
                        <div class="stat-label">Last 24h</div>
                    </div>
                </div>
                """
                
                # Analytics Section
                html += """
                <div class="analytics-section">
                    <div class="chart-container">
                        <div class="chart-title">📊 Wallet Activity Ranking</div>
                """
                
                if stats['wallet_activity']:
                    max_activity = max([wa['count'] for wa in stats['wallet_activity']])
                    for wa in stats['wallet_activity'][:6]:
                        percentage = (wa['count'] / max_activity * 100) if max_activity > 0 else 0
                        html += f"""
                        <div class="wallet-bar">
                            <div class="wallet-name" title="{wa['wallet']}">{wa['short']}</div>
                            <div class="bar-container">
                                <div class="bar-fill" style="width: {percentage}%"></div>
                            </div>
                            <div class="bar-count">{wa['count']}</div>
                        </div>
                        """
                else:
                    html += "<p style='color: #7f8c8d; text-align: center;'>No activity data yet</p>"
                
                html += """
                    </div>
                    
                    <div class="chart-container">
                        <div class="chart-title">🕒 Hourly Activity</div>
                        <div class="hourly-chart">
                """
                
                if stats['hourly_activity']:
                    sorted_hours = sorted(stats['hourly_activity'].keys())[-12:]
                    max_hourly = max(stats['hourly_activity'].values()) if stats['hourly_activity'] else 1
                    
                    for hour in sorted_hours:
                        count = stats['hourly_activity'][hour]
                        height = (count / max_hourly * 100) if max_hourly > 0 else 5
                        html += f"""
                            <div class="hour-bar" style="height: {height}%" title="{hour}: {count} transactions">
                                <div class="hour-label">{hour.split(':')[0]}</div>
                            </div>
                        """
                else:
                    html += "<p style='color: #7f8c8d; text-align: center;'>No hourly data yet</p>"
                
                html += """
                        </div>
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
                                   required maxlength="44">
                        </div>
                        <button type="submit" class="add-btn">➕ Add Wallet</button>
                    </form>
                    
                    <div class="current-wallets">
                        <h4>Currently Watching (<span id="wallet-count">""" + str(len(VALID_WALLETS)) + """</span> wallets):</h4>
                        <div class="wallet-list">
                """
                
                for wallet in VALID_WALLETS:
                    html += f"""
                            <div class="wallet-item">
                                <span class="wallet-address" title="{wallet}">{wallet[:12]}...{wallet[-12:]}</span>
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
                        rows.reverse()
                    
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
    print("🚀 Starting Wallet CA Tracker with Analytics Dashboard...")
    
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
    
    # Initialize other components
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
    print("📊 Analytics dashboard should be running now!")

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