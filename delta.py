import os
import time
import threading
import gc
import json
from datetime import datetime
from collections import deque
import requests
from flask import Flask

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

# =====================================================================
# 1. KONFIGURASI PUSAT & DATABASE DATA RINGAN
# =====================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7123456789:ABCdefGhIJKlmNoPQRstUVwxYz")
SIGNAL_CHANNEL_ID = os.getenv("SIGNAL_CHANNEL_ID", "-1001234567890")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "123456789")

TARGET_CHAINS = ["solana", "base", "bsc"]
MIN_AGE_HOURS = 1.0       
MIN_LIQUIDITY = 30000.0   
MIN_VOLUME_24H = 50000.0  
MIN_MARKET_CAP = 100000.0 
MAX_MARKET_CAP = 5000000.0
MIN_HOLDERS = 100         

# Fail Stor Simpanan Jurnal (Kalis Restart Pelayan)
JOURNAL_FILE = "quant_journal.json"

# Shared Memory + Locks
WATCHLIST = {}            
ACTIVE_TRADES = {}        # Format: {pool_address: {token_info, vwap, tp1, tp2, tp3, sl, message_id, hit_status}}
watchlist_lock = threading.Lock()
trade_lock = threading.Lock()

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Nova7 Core Matrix: Tracking Active Trades & Auto-Journal Active!", 200

# =====================================================================
# 2. SISTEM PENGURUSAN BUKU JURNAL (JSON FILE SYSTEM)
# =====================================================================
def log_trade_to_journal(symbol, outcome, pct_change):
    """Menyimpan setiap rekod kemenangan/kekalahan secara kekal ke fail JSON."""
    data = {"total_signals": 0, "win_tp": 0, "loss_sl": 0, "net_r": 0.0, "trades": []}
    
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
            
    data["total_signals"] += 1
    if "TP" in outcome:
        data["win_tp"] += 1
        # Anggaran pulangan unit R gred institusi
        data["net_r"] += 1.5 if "TP1" in outcome else (3.0 if "TP2" in outcome else 5.0)
    else:
        data["loss_sl"] += 1
        data["net_r"] -= 1.5

    data["trades"].append({
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "outcome": outcome,
        "pct": round(pct_change, 2)
    })
    
    with open(JOURNAL_FILE, "w") as f:
        json.dump(data, f, indent=4)

# =====================================================================
# 3. ENJIN ANTI-BAN API & TRANSMISI TELEGRAM
# =====================================================================
def safe_api_request(method, url, json_data=None, timeout=5):
    try:
        if method.upper() == "GET":
            res = requests.get(url, timeout=timeout)
        else:
            res = requests.post(url, json=json_data, timeout=timeout)
        if res.status_code == 429:
            time.sleep(int(res.headers.get("Retry-After", 3)))
            return None
        return res
    except Exception:
        return None

def send_telegram_msg(payload, method="sendMessage"):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    res = safe_api_request("POST", url, json_data=payload)
    if res and res.status_code == 200:
        return res.json()
    return {}

def send_admin_alert(message):
    payload = {"chat_id": ADMIN_CHAT_ID, "text": f"⚠️ <b>[ALERT LOG]</b>\n{message}", "parse_mode": "HTML"}
    send_telegram_msg(payload)

# =====================================================================
# 4. ENJIN MATEMATIK SEBENAR & CARTA
# =====================================================================
def calculate_pure_mathematical_metrics(candles):
    total_pv = 0.0
    total_delta_vol = 0.0
    cvd = 0.0
    prev_close = None
    tr_values = []
    prices_list = []
    
    for c in candles:
        close_p = c['close']
        vol_d = c['volume']
        prices_list.append(close_p)
        
        total_pv += close_p * vol_d
        total_delta_vol += vol_d
        
        if prev_close is not None:
            if close_p > prev_close:
                cvd += vol_d
            elif close_p < prev_close:
                cvd -= vol_d
            tr_values.append(max(c['high'] - c['low'], abs(c['high'] - prev_close), abs(c['low'] - prev_close)))
        prev_close = close_p
        
    vwap = total_pv / total_delta_vol if total_delta_vol > 0 else candles[-1]['close']
    atr = sum(tr_values) / len(tr_values) if tr_values else (candles[-1]['close'] * 0.02)
    local_min = min(prices_list)
    
    is_sweep = False
    if len(candles) >= 3:
        if candles[-1]['close'] > vwap and cvd > 0:
            is_sweep = True
            
    return vwap, cvd, atr, local_min, is_sweep

def generate_maximized_rr_chart(candles, entry, tp1, tp2, tp3, sl, symbol):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6, 4.5), sharex=True, gridspec_kw={'height_ratios': [3, 1]}, facecolor='#0d0d0d')
    ax1.set_facecolor('#0d0d0d')
    ax2.set_facecolor('#0d0d0d')
    indices = list(range(len(candles)))
    time_labels = [c['time_str'] for c in candles]
    
    for i, c in enumerate(candles):
        color = '#26a69a' if c['close'] >= c['open'] else '#ef5350'
        ax1.vlines(i, c['low'], c['high'], color=color, linewidth=1)
        ax1.bar(i, c['close'] - c['open'], bottom=c['open'], width=0.6, color=color, edgecolor=color)
        ax2.bar(i, c['volume'], width=0.6, color=color, alpha=0.8)
        
    ax1.axhline(y=tp3, color='#ff9800', linestyle='--', linewidth=0.8)
    ax1.axhline(y=tp2, color='#00e676', linestyle='--', linewidth=0.8)
    ax1.axhline(y=tp1, color='#00e676', linestyle='--', linewidth=0.8)
    ax1.axhline(y=entry, color='#a15c03', linestyle='-', linewidth=1.2)
    ax1.axhline(y=sl, color='#ff5252', linestyle='-', linewidth=1.2)
    
    ax1.set_title(f"{symbol} | Nova7 Matrix", color='#ffffff', fontsize=9, fontweight='bold', pad=8)
    ax2.set_xticks(indices[::max(1, len(indices)//4)])
    ax2.set_xticklabels([time_labels[idx] for idx in ax2.get_xticks()], rotation=25, ha='right', color='#787b86', fontsize=7)
    plt.tight_layout()
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', dpi=130, facecolor=fig.get_facecolor(), edgecolor='none')
    img_buf.seek(0)
    plt.clf()
    plt.close(fig)
    return img_buf

def perform_security_audit(chain, token_address):
    chain_ids = {"solana": "501", "base": "8453", "bsc": "56"}
    goplus_id = chain_ids.get(chain)
    audit_result = {"lp_locked": "🔒 Unverified", "holders": "0", "safe": False}
    if not goplus_id: return audit_result
    
    url = f"https://api.gopluslabs.io/api/v1/token_security/{goplus_id}?contract_addresses={token_address}"
    res = safe_api_request("GET", url)
    if res and res.status_code == 200:
        data = res.json().get("result", {}).get(token_address.lower(), {})
        if data.get("is_honeypot") == "1" or data.get("cannot_sell") == "1": return audit_result
        holder_count = int(data.get("holder_count", 0))
        if holder_count < MIN_HOLDERS and chain != "solana": return audit_result
        audit_result = {"lp_locked": "🟢 Locked (100%)" if data.get("is_true_token", "1") == "1" else "⚠️ Partial Lock", "holders": str(holder_count), "safe": True}
    return audit_result

# =====================================================================
# 5. TRANSMISI & PENGHANTARAN ISYARAT (MENDAPATKAN MESSAGE ID)
# =====================================================================
def send_mathematical_signal(token_info, vwap, cvd, atr, local_min, is_sweep):
    base_price = token_info['price']
    chain = token_info['chain'].lower()
    symbol = token_info['symbol'].upper()
    address = token_info['address']
    pool = token_info['pool_address']
    
    sl_val = max(0.0, local_min - (0.5 * atr))
    entry_val = max(sl_val + (0.75 * atr), vwap - (0.1 * atr))
    tp1_val = entry_val + (1.618 * atr)
    tp2_val = entry_val + (2.618 * atr)
    tp3_val = entry_val + (4.236 * atr)
    
    sl_pct = ((entry_val - sl_val) / entry_val) * 100 if entry_val > 0 else 0.0
    tp1_pct = ((tp1_val - entry_val) / entry_val) * 100
    tp2_pct = ((tp2_val - entry_val) / entry_val) * 100
    tp3_pct = ((tp3_val - entry_val) / entry_val) * 100
    one_r_pct = (atr / entry_val) * 100 if entry_val > 0 else 0.0
    recommended_allocation = (1.0 / sl_pct) * 100 if sl_pct > 0 else 10.0

    chain_emoji = "🟣" if chain == "solana" else "🔵"
    msg = (
        f"{chain_emoji} {chain.upper()} 🎯 <b>PULLBACK ENTRY</b>\n"
        f"────────────────────\n"
        f"<b>{symbol}</b>\n"
        f"<code>{address}</code>\n"
        f"────────────────────\n"
        f"• Entry: <code>{entry_val:.8f}</code>\n"
        f"• 1R = {one_r_pct:.1f}%\n"
        f"• 🧠 <b>Risk Size:</b> Max <code>{recommended_allocation:.1f}%</code> Bag\n"
        f"────────────────────\n"
        f"❌ TP1: <code>{tp1_val:.8f}</code> +{tp1_pct:.1f}% [50%]\n"
        f"❌ TP2: <code>{tp2_val:.8f}</code> +{tp2_pct:.1f}% [30%]\n"
        f"❌ TP3: <code>{tp3_val:.8f}</code> +{tp3_pct:.1f}% [20%]\n"
        f"💥 SL: <code>{sl_val:.8f}</code> -{sl_pct:.1f}%\n"
        f"────────────────────\n"
        f"🛡️ Status: <b>ACTIVE_QUANT</b>\n"
        f"📊 Structure: {'Wyckoff Spring' if is_sweep else 'Momentum Breakout'}"
    )

    # Tetapan Papan Kekunci Interaktif mengikut rantaian
    if chain == "solana":
        keyboard = {"inline_keyboard": [[{"text": "🤖 BonkBot", "url": f"https://t.me/bonkbot_bot?start=ref_quant_{address}"}],[{"text": "📊 Chart", "url": f"https://dexscreener.com/solana/{pool}"}]]}
    else:
        keyboard = {"inline_keyboard": [[{"text": "📊 DexScreener", "url": f"https://dexscreener.com/{chain}/{pool}"}]]}

    chart_file = generate_maximized_rr_chart(list(token_info["candles"]), entry_val, tp1_val, tp2_val, tp3_val, sl_val, symbol)
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": (f"{symbol}.png", chart_file, "image/png")}
    payload = {"chat_id": SIGNAL_CHANNEL_ID, "caption": msg, "parse_mode": "HTML", "reply_markup": json.dumps(keyboard)}
    
    try:
        telegram_response = requests.post(url, data=payload, files=files, timeout=12).json()
        if telegram_response.get("ok"):
            # Tangkap message_id unik daripada Telegram untuk keupayaan fungsi balas (reply)
            msg_id = telegram_response.get("result", {}).get("message_id")
            
            with trade_lock:
                ACTIVE_TRADES[pool] = {
                    "symbol": symbol, "entry": entry_val, "sl": sl_val,
                    "tp1": tp1_val, "tp2": tp2_val, "tp3": tp3_val,
                    "sl_pct": sl_pct, "tp1_pct": tp1_pct, "tp2_pct": tp2_pct, "tp3_pct": tp3_pct,
                    "message_id": msg_id, "chain": chain, "hits": set()
                }
    except Exception as e:
        print(f"Signal Post Error: {e}")
        
    chart_file.close()
    gc.collect()

# =====================================================================
# BARU: ENJIN PEMANTAU AKTIF & THREADED REPLY ALERTS (HIT TP/SL)
# =====================================================================
def check_active_trade_targets(pool_addr, current_price):
    """Menyemak koordinat harga semasa dengan aras TP/SL, kemudian memicu threaded reply."""
    with trade_lock:
        if pool_addr not in ACTIVE_TRADES: return
        trade = ACTIVE_TRADES[pool_addr]
        
    symbol = trade["symbol"]
    msg_id = trade["message_id"]
    reply_payload = {"chat_id": SIGNAL_CHANNEL_ID, "reply_to_message_id": msg_id, "parse_mode": "HTML"}
    alert_triggered = False
    outcome_str = ""
    pct_val = 0.0

    # 1. Semakan Henti Rugi (Stop Loss Hit)
    if current_price <= trade["sl"] and "SL" not in trade["hits"]:
        trade["hits"].add("SL")
        reply_payload["text"] = f"💥 <b>STATUS UPDATE: ${symbol}</b>\n────────────────────\n🔴 <b>HIT STOP LOSS (SL)</b>\n• Exit Price: <code>{current_price:.8f}</code>\n• Loss: <code>-{trade['sl_pct']:.1f}%</code>\n────────────────────\n🔒 <i>Position Closed. Journal Updated.</i>"
        send_telegram_msg(reply_payload)
        outcome_str = "HIT_SL"
        pct_val = -trade['sl_pct']
        alert_triggered = True

    # 2. Semakan Sasaran Ambil Untung 1 (TP1 Hit)
    elif current_price >= trade["tp1"] and "TP1" not in trade["hits"]:
        trade["hits"].add("TP1")
        reply_payload["text"] = f"💰 <b>STATUS UPDATE: ${symbol}</b>\n────────────────────\n🟢 <b>HIT TAKE PROFIT 1 (TP1)</b>\n• Exit Price: <code>{current_price:.8f}</code>\n• Profit: <code>+{trade['tp1_pct']:.1f}%</code>\n────────────────────\n💰 <i>Secure 50% bag. Move SL to Entry.</i>"
        send_telegram_msg(reply_payload)
        log_trade_to_journal(symbol, "HIT_TP1", trade['tp1_pct'])

    # 3. Semakan Sasaran Ambil Untung 2 (TP2 Hit)
    elif current_price >= trade["tp2"] and "TP2" not in trade["hits"]:
        trade["hits"].add("TP2")
        reply_payload["text"] = f"🔥 <b>STATUS UPDATE: ${symbol}</b>\n────────────────────\n🟢 <b>HIT TAKE PROFIT 2 (TP2)</b>\n• Exit Price: <code>{current_price:.8f}</code>\n• Profit: <code>+{trade['tp2_pct']:.1f}%</code>\n────────────────────\n🚀 <i>Secure 30% bag. Let 20% ride to TP3.</i>"
        send_telegram_msg(reply_payload)
        log_trade_to_journal(symbol, "HIT_TP2", trade['tp2_pct'])

    # 4. Semakan Sasaran Ambil Untung Maksimum (TP3 Hit)
    elif current_price >= trade["tp3"] and "TP3" not in trade["hits"]:
        trade["hits"].add("TP3")
        reply_payload["text"] = f"🏆 <b>STATUS UPDATE: ${symbol}</b>\n────────────────────\n👑 <b>MAXIMUM TARGET HIT (TP3)</b>\n• Exit Price: <code>{current_price:.8f}</code>\n• Profit: <code>+{trade['tp3_pct']:.1f}%</code>\n────────────────────\n🏁 <i>100% Position Cleared. Ride completed.</i>"
        send_telegram_msg(reply_payload)
        outcome_str = "HIT_TP3"
        pct_val = trade['tp3_pct']
        alert_triggered = True

    # Jika terkena SL atau TP3 tamat, bersihkan dari balang aktif
    if alert_triggered:
        if outcome_str == "HIT_SL":
            log_trade_to_journal(symbol, outcome_str, pct_val)
        with trade_lock:
            ACTIVE_TRADES.pop(pool_addr, None)

# =====================================================================
# BARU: THREAD 3 — CRON JURNAL AUTOMATIK (AHAD 10:00 PM MALAYSIA)
# =====================================================================
def weekly_journal_scheduler_loop():
    """Menyemak masa dan memancarkan laporan jurnal setiap Ahad jam 10 PM MYT (14:00 UTC)."""
    print("Enjin Penjadualan Jurnal Mingguan Diaktifkan...")
    while True:
        try:
            now = datetime.utcnow()
            # Weekday 6 = Hari Ahad. Jam 14 = Jam 2 PM UTC (Jam 10 PM Malaysia)
            if now.weekday() == 6 and now.hour == 14 and now.minute == 0:
                if os.path.exists(JOURNAL_FILE):
                    with open(JOURNAL_FILE, "r") as f:
                        data = json.load(f)
                        
                    if data["total_signals"] > 0:
                        win_rate = (data["win_tp"] / data["total_signals"]) * 100
                        report = (
                            f"📅 <b>WEEKLY PERFORMANCE JOURNAL REPORT</b>\n"
                            f"────────────────────\n"
                            f"• <b>Total Signals:</b> {data['total_signals']}\n"
                            f"• <b>Hit Targets (TP):</b> {data['win_tp']} ✅\n"
                            f"• <b>Hit Invalidation (SL):</b> {data['loss_sl']} ❌\n"
                            f"• <b>Win Rate:</b> <code>{win_rate:.1f}%</code>\n"
                            f"• <b>Net Growth (Weighted):</b> <code>{data['net_r']:.2f}R</code>\n"
                            f"────────────────────\n"
                            f"🧠 <i>Sistem Pembekal Isyarat Nova7 Matematik Kuantitatif Selesai Ditentusahkan Secara Telus.</i>"
                        )
                        # Hantar ke Channel Premium
                        send_telegram_msg({"chat_id": SIGNAL_CHANNEL_ID, "text": report, "parse_mode": "HTML"})
                        
                    # Reset data buku jurnal baharu untuk minggu hadapan
                    os.remove(JOURNAL_FILE)
                time.sleep(65) # Jeda seketika supaya tidak memicu mesej berulang dalam minit yang sama
            time.sleep(20)
        except Exception as e:
            time.sleep(10)

# =====================================================================
# 7. ENJIN UTAMA REST API POLLING MULTI-THREAD
# =====================================================================
def scanner_thread_loop():
    while True:
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=solana%20base%20bsc"
            res = safe_api_request("GET", url)
            if res and res.status_code == 200:
                pairs = res.json().get("pairs", [])
                for pair in pairs:
                    chain = pair.get("chainId")
                    if chain not in TARGET_CHAINS: continue
                    pool_addr = pair.get("pairAddress")
                    base_token = pair.get("baseToken", {})
                    
                    liq = pair.get("liquidity", {}).get("usd", 0.0)
                    vol24 = pair.get("volume", {}).get("h24", 0.0)
                    mcap = pair.get("fdv", 0.0)
                    
                    if (liq > MIN_LIQUIDITY and vol24 > MIN_VOLUME_24H and MIN_MARKET_CAP <= mcap <= MAX_MARKET_CAP):
                        created_at = pair.get("pairCreatedAt", 0)
                        age = (time.time() - (created_at / 1000)) / 3600 if created_at > 0 else 1.5
                        
                        if age >= MIN_AGE_HOURS:
                            with watchlist_lock:
                                if pool_addr not in WATCHLIST and pool_addr not in ACTIVE_TRADES:
                                    WATCHLIST[pool_addr] = {
                                        "symbol": base_token.get("symbol", "UNKNWN"), "address": base_token.get("address"),
                                        "pool_address": pool_addr, "chain": chain, "candles": deque(maxlen=25),
                                        "last_volume": vol24, "current_candle": None
                                    }
            time.sleep(15)
        except Exception as e:
            time.sleep(5)

def monitor_thread_loop():
    while True:
        try:
            with watchlist_lock: pools = list(WATCHLIST.keys())
            for pool_addr in pools:
                if pool_addr in ALREADY_SIGNALED: continue
                with watchlist_lock:
                    if pool_addr not in WATCHLIST: continue
                    token_info = WATCHLIST[pool_addr]
                
                url = f"https://api.geckoterminal.com/api/v2/networks/{token_info['chain']}/pools/{pool_addr}"
                res = safe_api_request("GET", url)
                
                if res and res.status_code == 200:
                    attr = res.json().get("data", {}).get("attributes", {})
                    price = attr.get("base_token_price_usd")
                    v_usd = float(attr.get("volume_usd", {}).get("h24", 0.0))
                    
                    if price:
                        current_p = float(price)
                        delta_v = max(1.0, v_usd - token_info["last_volume"])
                        token_info["last_volume"] = v_usd
                        
                        time_str = datetime.now().strftime("%H:%M")
                        curr_c = token_info["current_candle"]
                        
                        if curr_c is None or len(token_info["candles"]) == 0:
                            token_info["current_candle"] = {"open": current_p, "high": current_p, "low": current_p, "close": current_p, "volume": delta_v, "time_str": time_str, "ticks_count": 1}
                            token_info["candles"].append(token_info["current_candle"])
                        else:
                            if curr_c["ticks_count"] < 4:
                                curr_c["high"] = max(curr_c["high"], current_p)
                                curr_c["low"] = min(curr_c["low"], current_p)
                                curr_c["close"] = current_p
                                curr_c["volume"] += delta_v
                                curr_c["ticks_count"] += 1
                            else:
                                token_info["current_candle"] = {"open": current_p, "high": current_p, "low": current_p, "close": current_p, "volume": delta_v, "time_str": time_str, "ticks_count": 1}
                                token_info["candles"].append(token_info["current_candle"])
                        
                        # Semak status token yang sedang bergerak aktif
                        check_active_trade_targets(pool_addr, current_p)
                        
                        candles_list = token_info["candles"]
                        if len(candles_list) >= 6:
                            vwap, cvd, atr, local_min, is_sweep = calculate_pure_mathematical_metrics(candles_list)
                            if current_p > vwap and cvd > 0:
                                audit = perform_security_audit(token_info["chain"], token_info["address"])
                                if token_info["chain"] == "solana" or audit["safe"]:
                                    token_info['price'] = current_p
                                    send_mathematical_signal(token_info, vwap, cvd, atr, local_min, is_sweep)
                                    ALREADY_SIGNALED.add(pool_addr)
                                    with watchlist_lock: WATCHLIST.pop(pool_addr, None)
                                    
                time.sleep(1) # Pemantauan rest-api polling berkelajuan tinggi
                
            # Pemantauan lanjutan untuk token dalam senarai ACTIVE_TRADES yang sudah terkeluar dari Watchlist
            with trade_lock: active_pools = list(ACTIVE_TRADES.keys())
            for p_addr in active_pools:
                with trade_lock:
                    if p_addr not in ACTIVE_TRADES: continue
                    t_chain = ACTIVE_TRADES[p_addr]["chain"]
                url = f"https://api.geckoterminal.com/api/v2/networks/{t_chain}/pools/{p_addr}"
                res = safe_api_request("GET", url)
                if res and res.status_code == 200:
                    p_usd = res.json().get("data", {}).get("attributes", {}).get("base_token_price_usd")
                    if p_usd: check_active_trade_targets(p_addr, float(p_usd))
                time.sleep(1)

            if len(ALREADY_SIGNALED) > 100: ALREADY_SIGNALED.clear()
            gc.collect()
            time.sleep(1)
        except Exception as e:
            time.sleep(2)

if __name__ == "__main__":
    send_admin_alert("Nova7 Automated Engine System Deployment Update: Auto-Reply Target Alerts & Weekly 10PM Cron Journaling Enabled.")
    threading.Thread(target=scanner_thread_loop, daemon=True).start()
    threading.Thread(target=monitor_thread_loop, daemon=True).start()
    threading.Thread(target=weekly_journal_scheduler_loop, daemon=True).start() # Thread Penjadualan Jurnal Mingguan
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
