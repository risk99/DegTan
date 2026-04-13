import telebot
import requests
import time
import os
import mpmath
from datetime import datetime, timedelta, timezone

# ========== CONFIGURATION ========== 
BOT_TOKEN = '8790969710:AAE--5mRAKZQV5Uz5NSouc34WCG6AZp56Ws' 
CHANNEL_ID = '-1003968918064'   

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
    "current_prediction": {
        "period_full": None, 
        "block": None, 
        "side": None, 
        "note": "Processing..."
    }
}

# mpmath အတွက် တွက်ချက်မှု တိကျမှုကို နေရာ 60 အထိ သတ်မှတ်သည် (Rounding Error မဖြစ်စေရန်)
mpmath.mp.dps = 60

# --- ၀။ TIMEZONE UTILS (မြန်မာစံတော်ချိန်အတွက်) ---
def get_mm_time():
    return datetime.now(timezone.utc) + timedelta(hours=6, minutes=30)

# --- ၁။ NEW STRATEGY (HIGH PRECISION TAN DEGREE) ---
def algo_tan_deg(latest):
    """
    Block Number ရဲ့ နောက်ဆုံး ၃ လုံးကို ယူပြီး ဖုန်း Calculator နှင့် ထပ်တူကျစေရန်
    Decimal 41 နေရာအထိ အတိအကျ ဖြတ်ယူတွက်ချက်မည်။
    """
    block_num_str = str(latest.get('blockNumber', '0'))
    
    last_3_str = block_num_str.zfill(3)[-3:]
    val = int(last_3_str)
    
    # Degree မှ Radian သို့ ပြောင်းသည်
    rad_val = mpmath.radians(val)
    
    # Tan တန်ဖိုးတွက်သည်
    tan_val = mpmath.tan(rad_val)
    
    # ဖုန်း Calculator အတိုင်း Decimal 41 နေရာ ကွက်တိရရန် String Format ပြောင်းသည်
    tan_str = f"{abs(tan_val):.41f}"
    
    # နောက်က ဂဏန်းအပို '0' များပါလာလျှင် ဖယ်ရှားရန်
    if '.' in tan_str:
        tan_str = tan_str.rstrip('0')
        if tan_str.endswith('.'):
            tan_str += '0'
            
    # နောက်ဆုံးဂဏန်းကို ယူသည်
    last_digit_str = tan_str[-1]
    last_digit = int(last_digit_str)
    
    # 0-4 = SMALL, 5-9 = BIG
    side = "BIG" if last_digit >= 5 else "SMALL"
    
    return side, tan_str, last_3_str, block_num_str

# --- ၂။ PREDICTION ENGINE ---
def get_prediction(history_data):
    try:
        data_list = sorted(history_data, key=lambda x: int(x['issueNumber']), reverse=True)
        latest = data_list[0]
        
        side, tan_str, last_3, block_num = algo_tan_deg(latest)
        
        note = f"tan({last_3}°) = \n<code>{tan_str}</code>\n🍁Lᴀꜱᴛ Dɪɢɪᴛ: <b>[{tan_str[-1]}]</b> -> <b>{side}</b>"
        
        return side, note, block_num
    except Exception as e:
        return None, f"Error: {e}", None

# --- ၃။ STATS & UTILS ---
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

# --- ၄။ MESSAGE BUILDERS ---
def build_live_msg(remaining_sec):
    total = state["total_wins"] + state["total_losses"]
    win_rate = (state["total_wins"] / total * 100) if total > 0 else 0
    curr = state['current_prediction']
    
    msg = f"<b>🍁GLOBAL TRX LIVE - WWC LABS</b>\n"
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
    
    msg += f"🍁ᴘᴇʀɪᴏᴅ: <b>{curr['period_full'][-17:] if curr['period_full'] else '----'}</b>\n"
    msg += f"🍁Bʟᴏᴄᴋ: <b>{curr['block'] or '----'}</b>\n"
    msg += f"🍁Fᴏʀᴍᴜʟᴀ: {curr['note']}\n"
    msg += f"🍁ᴘʀᴇᴅɪᴄᴛɪᴏɴ: <b>{curr['side'] or 'WAITING'}</b>\n"
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

# --- ၅။ MAIN LOOP ---
def main_loop():
    print("Bot starting with High Precision Tan(DEG) Strategy...")
    state["last_day"] = get_mm_time().strftime("%d,%m,%Y")
    
    while True:
        try:
            res = requests.get(f"{API_URL}?pageSize=50&pageNo=1&ts={int(time.time())}", headers=HEADERS, timeout=15)
            if res.status_code == 200:
                data = res.json().get('data', {}).get('list', [])
                for i in data: state["history"][i['issueNumber']] = i
                
                latest_p = sorted(state["history"].keys(), reverse=True)[0]
                next_p = str(int(latest_p) + 1)
                
                if state["current_prediction"]["period_full"] != next_p:
                    # Prediction အသစ်တွက်မည်
                    side, note, b_num = get_prediction(list(state["history"].values()))
                        
                    state["current_prediction"] = {
                        "period_full": next_p, 
                        "block": b_num,
                        "side": side, 
                        "note": note
                    }
                    if side: 
                        state["predictions_memory"][next_p] = side

                rem_sec = 60 - get_mm_time().second
                
                # Update Messages
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
