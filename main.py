import telebot
import requests
import time
import os
import math
from datetime import datetime, timedelta, timezone

# ========== CONFIGURATION ========== 
BOT_TOKEN = '8790969710:AAE--5mRAKZQV5Uz5NSouc34WCG6AZp56Ws'
CHANNEL_ID = '-1003770494230'

API_URL = "https://draw.ar-lottery01.com/TrxWinGo/TrxWinGo_1M/GetHistoryIssuePage.json"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

bot = telebot.TeleBot(BOT_TOKEN)

state = {
    "history": {},
    "total_wins": 0,
    "total_losses": 0,
    "current_loss_streak": 0,
    "max_loss_data": {}, 
    "last_day": "",
    "loss_msg_id": None, 
    "live_msg_id": None, 
    "predictions_memory": {}, 
    "processed_periods": set(),
    "current_prediction": {"period_full": None, "block": None, "side": None, "conf": 0, "note": "Processing..."},
    
    # HTML နဲ့ တစ်ထပ်တည်းတူအောင် Live Stats ပဲ မှတ်ပါတော့မယ်
    "method_stats": {
        "MT": {"correct": 0, "total": 0},
        "LCG": {"correct": 0, "total": 0},
        "WH": {"correct": 0, "total": 0},
        "ACORN": {"correct": 0, "total": 0},
        "BCN": {"correct": 0, "total": 0}
    },
    "last_api_period": None,
    "current_algo_preds": {},
    "target_next_period": None
}

def get_mm_time():
    return datetime.now(timezone.utc) + timedelta(hours=6, minutes=30)

# --- ၁။ 100% IDENTICAL GENERATORS (Javascript -> Python) ---

def to_int32(n):
    n = n & 0xFFFFFFFF
    return n | (-(n & 0x80000000))

def make_seed(period_str):
    seed = 0
    for char in period_str:
        seed = to_int32(to_int32(seed << 5) - seed + ord(char))
    return (seed & 0xFFFFFFFF) + 1

class MersenneTwister:
    def __init__(self, seed):
        self.N = 624
        self.M = 397
        self.MATRIX_A = 0x9908b0df
        self.UPPER_MASK = 0x80000000
        self.LOWER_MASK = 0x7fffffff
        self.mt = [0] * self.N
        self.mti = self.N + 1
        self.init_seed(seed)

    def init_seed(self, seed):
        self.mt[0] = seed & 0xFFFFFFFF
        for self.mti in range(1, self.N):
            s = self.mt[self.mti-1] ^ (self.mt[self.mti-1] >> 30)
            part1 = (((s & 0xffff0000) >> 16) * 1812433253) << 16
            part2 = (s & 0xffff) * 1812433253
            self.mt[self.mti] = (part1 + part2 + self.mti) & 0xFFFFFFFF

    def generate(self):
        for kk in range(self.N - self.M):
            y = (self.mt[kk] & self.UPPER_MASK) | (self.mt[kk+1] & self.LOWER_MASK)
            self.mt[kk] = self.mt[kk+self.M] ^ (y >> 1) ^ (self.MATRIX_A if y & 1 else 0)
        for kk in range(self.N - self.M, self.N - 1):
            y = (self.mt[kk] & self.UPPER_MASK) | (self.mt[kk+1] & self.LOWER_MASK)
            self.mt[kk] = self.mt[kk+(self.M-self.N)] ^ (y >> 1) ^ (self.MATRIX_A if y & 1 else 0)
        y = (self.mt[self.N-1] & self.UPPER_MASK) | (self.mt[0] & self.LOWER_MASK)
        self.mt[self.N-1] = self.mt[self.M-1] ^ (y >> 1) ^ (self.MATRIX_A if y & 1 else 0)
        self.mti = 0

    def nextInt(self):
        if self.mti >= self.N:
            self.generate()
        y = self.mt[self.mti]
        self.mti += 1
        y ^= (y >> 11)
        y ^= (y << 7) & 0x9d2c5680
        y ^= (y << 15) & 0xefc60000
        y ^= (y >> 18)
        return y & 0xFFFFFFFF

    def nextDouble(self):
        return self.nextInt() * (1.0 / 4294967296.0)

    def predict(self):
        return "SMALL" if self.nextDouble() < 0.5 else "BIG"

class LCG:
    def __init__(self, seed):
        self.state = seed if seed else 123456789
    def predict(self):
        self.state = (self.state * 1103515245 + 12345) & 0x7FFFFFFF
        val = self.state / 0x7FFFFFFF
        return "SMALL" if val < 0.5 else "BIG"

class WichmannHill:
    def __init__(self, seed):
        self.s1 = (seed % 30269) or 12345
        self.s2 = (seed % 30307) or 23456
        self.s3 = (seed % 30323) or 34567
    def predict(self):
        self.s1 = (171 * self.s1) % 30269
        self.s2 = (172 * self.s2) % 30307
        self.s3 = (170 * self.s3) % 30323
        val = (self.s1/30269.0 + self.s2/30307.0 + self.s3/30323.0) % 1.0
        return "SMALL" if val < 0.5 else "BIG"

class ACORN:
    def __init__(self, seed, order=8):
        self.order = order
        self.state = [0] * (order + 1)
        m = 2**30
        self.state[0] = seed % m
        for i in range(1, order + 1):
            self.state[i] = (self.state[i-1] + seed) % m
    def predict(self):
        m = 2**30
        self.state[0] = (self.state[0] + 1) % m
        for i in range(1, self.order + 1):
            self.state[i] = (self.state[i] + self.state[i-1]) % m
        val = self.state[self.order] / m
        return "SMALL" if val < 0.5 else "BIG"

class BCN:
    def __init__(self, seed):
        self.state = seed if seed else 12345
    def predict(self):
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        x = self.state / 0xFFFFFFFF
        r = (x + math.sin(self.state)*0.3 + math.cos(self.state*0.7)*0.2) % 1.0
        val = -r if r < 0 else r
        return "SMALL" if val < 0.5 else "BIG"

def get_all_predictions(period_str):
    seed = make_seed(period_str)
    return {
        "MT": MersenneTwister(seed).predict(),
        "LCG": LCG(seed).predict(),
        "WH": WichmannHill(seed).predict(),
        "ACORN": ACORN(seed).predict(),
        "BCN": BCN(seed).predict()
    }

# --- ၂။ STATS & UTILS ---

def update_loss_stats(streak):
    if streak <= 0: return
    now = get_mm_time()
    today = now.strftime("%d,%m,%Y")
    if state["last_day"] != today:
        state["max_loss_data"] = {}
        state["last_day"] = today
    if streak not in state["max_loss_data"]:
        state["max_loss_data"][streak] = {"times": 1, "last_time": now.strftime("%I:%M %p")}
    else:
        state["max_loss_data"][streak]["times"] += 1
        state["max_loss_data"][streak]["last_time"] = now.strftime("%I:%M %p")

# --- ၃။ MESSAGE BUILDERS ---

def build_live_msg(remaining_sec):
    total = state["total_wins"] + state["total_losses"]
    win_rate = (state["total_wins"] / total * 100) if total > 0 else 0
    curr = state['current_prediction']
    
    msg = f"<b>🍁GLOBAL TRX LIVE - ZX WIN AI</b>\n"
    msg += f"🍁ʜɪꜱᴛᴏʀʏ: <b>W-{state['total_wins']} | L-{state['total_losses']}</b>\n"
    msg += f"🍁ᴡɪɴʀᴀᴛᴇ: <b>{win_rate:.1f}%</b> \n"    
    msg += f"🍁ᴛɪᴍᴇ ʀᴇᴍᴀɪɴɪɴɢ: <b>{remaining_sec}s</b>\n"
    
    table = "📄     Period Number     • Result   •  W/L •\n"
                
    sorted_hist = sorted(state["history"].values(), key=lambda x: int(x['issueNumber']), reverse=True)
    
    for item in sorted_hist[:10]:
        p = str(item['issueNumber'])
        num = int(item['number'])
        actual_side = "BIG" if num >= 5 else "SMALL"
        
        wl = "▫️"
        if p in state["predictions_memory"]:
            predicted = state["predictions_memory"][p]
            if predicted == actual_side:
                wl = "🍏"
                if p not in state["processed_periods"]:
                    update_loss_stats(state["current_loss_streak"])
                    state["total_wins"] += 1
                    state["current_loss_streak"] = 0
                    state["processed_periods"].add(p)
            else:
                wl = "🍎"
                if p not in state["processed_periods"]:
                    state["total_losses"] += 1
                    state["current_loss_streak"] += 1
                    state["processed_periods"].add(p)
        
        table += f"🍁 {p[-17:]}  •  {num}-{actual_side[:1]}     • {wl:^3} •\n"

    msg += f"<pre>{table}</pre>"
        
    msg += f"🍁ᴘᴇʀɪᴏᴅ: {curr['period_full'][-17:] if curr['period_full'] else '----'}\n"
    msg += f"🍁ᴘʀᴇᴅɪᴄᴛɪᴏɴ: <b>{curr['side'] or 'WAITING'}</b>\n"
    msg += f"🍁ʟᴏɢɪᴄ: <i>{curr['note']}</i>\n"
    msg += f"🍁ᴄʀᴇᴀᴛᴏʀ: @XQNSY"

    return msg

def build_loss_msg():
    msg = f"<b>⏰ Max Loss History</b>\n"
    msg += f"<i>🗓️ Date: {state['last_day']}</i>\n\n"
    if not state["max_loss_data"]:
        msg += "▫️ No loss streaks recorded yet."
    else:
        for s in sorted(state["max_loss_data"].keys(), reverse=True):
            d = state["max_loss_data"][s]
            msg += f"<code>⚡{s}x {d['times']}Time {d['last_time']}</code>\n"
    return msg

# --- ၄။ MAIN LOOP ---

def main_loop():
    print("Bot starting... 100% Identical to pro.html (Exact Tracker Match)")
    state["last_day"] = get_mm_time().strftime("%d,%m,%Y")
    
    while True:
        try:
            res = requests.get(f"{API_URL}?pageSize=50&pageNo=1&ts={int(time.time())}", headers=HEADERS, timeout=15)
            if res.status_code == 200:
                data = res.json().get('data', {}).get('list', [])
                for i in data: state["history"][i['issueNumber']] = i
                
                sorted_hist = sorted(state["history"].values(), key=lambda x: int(x['issueNumber']), reverse=True)
                latest = sorted_hist[0]
                latest_p = str(latest['issueNumber'])
                
                # Result အသစ်ထွက်လာပြီဆိုရင် - HTML ထဲကအတိုင်း Live Update လုပ်မယ်
                if state["last_api_period"] != latest_p:
                    
                    # အကယ်၍ အရင်ပွဲအတွက် Algorithm တွေက ခန့်မှန်းပေးထားခဲ့တာရှိရင် အမှန်/အမှား တိုက်စစ်ပြီး Update လုပ်မယ်
                    if state["target_next_period"] == latest_p and state["current_algo_preds"]:
                        actual_size = "BIG" if int(latest['number']) >= 5 else "SMALL"
                        for method, pred in state["current_algo_preds"].items():
                            if pred == actual_size:
                                state["method_stats"][method]["correct"] += 1
                            state["method_stats"][method]["total"] += 1
                            
                    state["last_api_period"] = latest_p
                    
                    # ပွဲစဉ်အသစ်အတွက် Algorithm ၅ ခုလုံးကို ခန့်မှန်းခိုင်းမယ်
                    next_p = str(int(latest_p) + 1)
                    state["target_next_period"] = next_p
                    state["current_algo_preds"] = get_all_predictions(next_p)
                    
                    # ဘယ်ကောင် အကောင်းဆုံးလဲ (HTML နဲ့ အတိအကျတူအောင် ရွေးမယ်)
                    best_method = "MT"
                    best_score = -1
                    
                    for method in ["MT", "LCG", "WH", "ACORN", "BCN"]:
                        stat = state["method_stats"][method]
                        score = (stat["correct"] / stat["total"]) if stat["total"] > 0 else 0
                        if score > best_score:
                            best_score = score
                            best_method = method
                            
                    final_side = state["current_algo_preds"][best_method]
                    display_acc = int(best_score * 100) if best_score >= 0 else 0
                    
                    state["current_prediction"] = {
                        "period_full": next_p,
                        "block": latest.get('blockNumber'),
                        "side": final_side,
                        "conf": display_acc,
                        "note": f"Used: {best_method} ({display_acc}% historical)"
                    }
                    
                    if final_side:
                        state["predictions_memory"][next_p] = final_side

                rem_sec = 60 - get_mm_time().second
                
                l_text = build_loss_msg()
                if state["loss_msg_id"] is None:
                    m = bot.send_message(CHANNEL_ID, l_text, parse_mode='HTML')
                    state["loss_msg_id"] = m.message_id
                else:
                    try: bot.edit_message_text(l_text, CHANNEL_ID, state["loss_msg_id"], parse_mode='HTML')
                    except: pass

                v_text = build_live_msg(rem_sec)
                if state["live_msg_id"] is None:
                    m = bot.send_message(CHANNEL_ID, v_text, parse_mode='HTML')
                    state["live_msg_id"] = m.message_id
                else:
                    try: bot.edit_message_text(v_text, CHANNEL_ID, state["live_msg_id"], parse_mode='HTML')
                    except: pass

                time.sleep(5)
            else:
                time.sleep(10)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main_loop()
