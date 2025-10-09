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
# Render.com suteikia PORT kaip environment kintamąjį
PORT = int(os.environ.get('PORT', 8000))  # Render naudoja savo PORT
RENDER = os.environ.get('RENDER', False)  # Ar veikiame Render aplinkoje

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

# Render.com failų sistemoje naudokime absoliučius kelius
CSV_FILE = os.path.join(os.getcwd(), "wallet_ca_events.csv")
SEEN_FILE = os.path.join(os.getcwd(), "seen_signatures.json")

POLL_INTERVAL = 20
SIG_LIMIT = 20
THROTTLE = 0.12

# optional notifications (Render.com neturi desktop, tad praleisime)
HAS_PLYER = False

# ... (VISAS LIKĘS KODAS BE PAKEITIMŲ IKI WEB DASHBOARD SECTION) ...

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
                    /* ... (TAS PATS CSS) ... */
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
            
            # ... (TAS PATS HTML GENERAVIMAS) ...
            
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
        # Fallback port
        with socketserver.TCPServer(("", 8001), CSVHandler) as httpd:
            print(f"🌐 Web dashboard started on fallback: http://0.0.0.0:8001")
            httpd.serve_forever()

# ... (VISAS LIKĘS KODAS BE PAKEITIMŲ) ...

def main():
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