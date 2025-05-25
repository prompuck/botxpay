import os
import re
import json
import time
import glob
import hashlib
import requests
import gspread
import openai
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
from oauth2client.service_account import ServiceAccountCredentials
from telethon import TelegramClient, events

# ─── LOAD ENVIRONMENT ───────────────────────────────────────────
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
SHEET_URL = os.getenv("SHEET_URL")
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials.json")

openai.api_key = OPENAI_API_KEY
client = openai.Client()
telegram_client = TelegramClient("session_name_dev", API_ID, API_HASH)

last_instruction_hash = None

# ─── MULTI-MERCHANT CONFIG ─────────────────────────────────────
MERCHANT_CREDENTIALS = [
    {
        "MERCHANT_CODE": "M035600",
        "API_KEY": "ZlK1lUxLs6jUsldpfpnzGyAfkBYy7n09EPKBaGiFGKo6uA9JvqWC9XawtezPjDZ6",
        "SECRET_KEY": "eZNbyCgxm3bgTqVd4X3JWLa9XnMIdzHNuRulzNDYHewcZqHjnf6pts0TLMXs5Jm1KdLdlJ6eRMiUm39NK8Ci8DYJUVvJ8wcCGc7ENfJjb01nhK2yJjfS0HqxtVLriOmT9VvAwKgyWhFvWLauKLpokunopOTaFGOXKeK9w"
    },
    {
        "MERCHANT_CODE": "M036002",
        "API_KEY": "DUbfbV906ODN1pnZxxvmFlAp1iHQapfwHJYgMBBv1e3WoRAmec8F68SEURJizPCs",
        "SECRET_KEY": "fxFOXJF69YckEDYlV2hwCpTQyz4nyqezKjCBFgg0HlcQCdyhkhrSS08lOceMB7ejDHTKxDiadf3KkEecioiGcHpNWsO4XTGU5Fbfg1oN9uQ1LDuszw9EALgygOQBjLukHqai1bmWLiQLCVyqD7QLL1578d1eoA7l9LKq4"
    },
    {
        "MERCHANT_CODE": "M035188",
        "API_KEY": "9UKFVfFgBRJVpfdeK6oz5GvliRjFh2ms7in82HTRtonYogZSeCyuqgv0LU77W9w8",
        "SECRET_KEY": "djtolRoy5fYxA1fAC2Tl0u41CfpISHC3YyGVSQIiUsBsD7P2VS9uTCL5D8Dg2l8mYAr1ZNiUWdGvOLUUUGsLoX7Ec6Ldl72lFiC8yDpvp0opCTzrnLh0PEnsvZC8y8GTtXXVzdk14jjj3Rvw2tbdEN8GHuvNqv1v9EY1V"
    }
]

# ─── GOOGLE SHEET ───────────────────────────────────────────────
def init_google_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
    return gspread.authorize(creds).open_by_url(SHEET_URL).worksheet("Report")

sheet = init_google_sheet()

# ─── SHEET AUTO CLEAR ───────────────────────────────────────────
def clear_sheet_every_3_days():
    log_path = "clear_log.json"
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    try:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                last_clear_date = datetime.strptime(json.load(f)["last_clear"], "%Y-%m-%d").date()
        else:
            last_clear_date = None
        if not last_clear_date or (today - last_clear_date).days >= 3:
            print("🧹 Clearing sheet data (every 3 days)...")
            sheet.batch_clear(["A2:Z1000"])
            print("✅ Sheet cleared successfully.")
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump({"last_clear": today.strftime("%Y-%m-%d")}, f, ensure_ascii=True)
        else:
            print(f"📅 Last cleared on {last_clear_date}, skipping.")
    except Exception as e:
        print(f"❌ Failed to manage sheet clearing: {e}")

# ─── OTP OPEN/CLOSE SWITCH ─────────────────────────────────────
def is_otp_input_open():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
        client_gs = gspread.authorize(creds)
        otp_sheet = client_gs.open_by_url(SHEET_URL).worksheet("OTP Input")
        value = otp_sheet.acell("C3").value.strip().lower()
        return value == "open"
    except Exception as e:
        print(f"⚠️ Failed to read C3 in OTP Input: {e}")
        return False

# ─── INSTRUCTION LOADER ────────────────────────────────────────
def fetch_instructions_from_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
        client_gs = gspread.authorize(creds)
        otp_sheet = client_gs.open_by_url(SHEET_URL).worksheet("Instruction Summary")
        instruction_cells = otp_sheet.range("A2:A1000")
        return "\n".join(cell.value.strip() for cell in instruction_cells if cell.value.strip())
    except Exception as e:
        print(f"❌ Failed to load instructions: {e}")
        return None

# ─── TRANSACTION HANDLERS ──────────────────────────────────────
def extract_transaction_ids(message):
    return re.findall(r"\b(?:TH[A-Za-z0-9]{10,20}|FO[A-Za-z0-9]{10,20})\b", message)

def payment_transactions(order_nos):
    results = []
    for order_no in order_nos:
        if len(order_no) != 16:
            results.append({"error": "Invalid TH transaction ID length.", "OrderNo": order_no})
            continue

        found = False
        for creds in MERCHANT_CREDENTIALS:
            checksum = hashlib.md5(("TransactionIdDESC11" + order_no + creds["SECRET_KEY"]).encode()).hexdigest()
            payload = {
                "OrderBy": "TransactionId", "OrderDir": "DESC", "PageSize": 1,
                "PageNumber": 1, "OrderNo": order_no, "Checksum": checksum
            }
            headers = {
                "Content-Type": "application/json",
                "CHILLPAY-MerchantCode": creds["MERCHANT_CODE"],
                "CHILLPAY-ApiKey": creds["API_KEY"]
            }
            try:
                res = requests.post("https://api-transaction.chillpay.co/api/v1/payment/search", headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json().get("data")
                    if data:
                        results.append(data[0])
                        found = True
                        break
            except Exception as e:
                print(f"⚠️ Error with merchant {creds['MERCHANT_CODE']}: {e}")
        if not found:
            results.append({"error": "No data found.", "OrderNo": order_no})
    return results

def payout_transactions(order_nos):
    results = []
    for order_no in order_nos:
        if len(order_no) != 18:
            results.append({"error": "Invalid FO transaction ID length.", "OrderNo": order_no})
            continue

        found = False
        for creds in MERCHANT_CREDENTIALS:
            checksum = hashlib.md5(("TransactionIdDESC11" + order_no + creds["SECRET_KEY"]).encode()).hexdigest()
            payload = {
                "OrderBy": "TransactionId", "OrderDir": "DESC", "PageSize": 1,
                "PageNumber": 1, "SearchText": order_no, "Checksum": checksum
            }
            headers = {
                "Content-Type": "application/json",
                "CHILLPAY-MerchantCode": creds["MERCHANT_CODE"],
                "CHILLPAY-ApiKey": creds["API_KEY"]
            }
            try:
                res = requests.post("https://api-payout.chillpay.co/api/v1/transaction/search", headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json().get("data")
                    if data:
                        results.append(data[0])
                        found = True
                        break
            except Exception as e:
                print(f"⚠️ Error with merchant {creds['MERCHANT_CODE']}: {e}")
        if not found:
            results.append({"error": "No data found.", "OrderNo": order_no})
    return results

def fetch_transactions(transaction_ids):
    th_ids = [tid for tid in transaction_ids if tid.startswith("TH")]
    fo_ids = [tid for tid in transaction_ids if tid.startswith("FO")]
    return payment_transactions(th_ids) + payout_transactions(fo_ids)

# ─── OPENAI ASSISTANT ──────────────────────────────────────────
def ask_openai(user_message):
    global last_instruction_hash
    instructions = fetch_instructions_from_sheet()
    if not instructions:
        return "Failed to load assistant instructions."
    current_hash = hashlib.md5(instructions.encode()).hexdigest()
    if current_hash != last_instruction_hash or not hasattr(ask_openai, "cached_assistant"):
        assistant = client.beta.assistants.create(name="Orders Assistant", model="gpt-4o", instructions=instructions)
        ask_openai.cached_assistant = assistant
        last_instruction_hash = current_hash
    else:
        assistant = ask_openai.cached_assistant

    transaction_ids = extract_transaction_ids(user_message)
    results = fetch_transactions(transaction_ids)
    content = f"User message: {user_message}\n\nOrder results: {json.dumps(results, ensure_ascii=False)}\n\nProvide a clear and professional response."

    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=content)
    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)
    while run.status != "completed":
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
    messages = client.beta.threads.messages.list(thread_id=thread.id, order="asc")
    return next((msg.content[0].text.value for msg in reversed(messages.data) if msg.role == "assistant"), "No response.")

# ─── TELEGRAM HANDLER ──────────────────────────────────────────
@telegram_client.on(events.NewMessage)
async def handler(event):
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()
        group_name = getattr(chat, "title", None) or "Private"
        username = sender.username or f"{sender.first_name} {sender.last_name or ''}".strip()

        allowed_groups = [
            # "Test Chillpay CLP & XPY Operation [M035600][M035274]",
            "G: Chillpay CLP & XPY Operation [M035600][M035274]",
            "Chillpay CLP & XPY Operation [M035600][M035274]",
            "Test Bot-Chillpay CLP & XPY Operation [M035600][M035274]",
        ]
        if group_name not in allowed_groups:
            print(f"⛔ Message from non-allowed group: {group_name}, ignored.")
            return

        if not is_otp_input_open():
            print("🔒 OTP Input is set to 'close'. Skipping reply.")
            return

        new_message = event.message.message.strip()

        if re.fullmatch(r"(noted\s*team(\s*thank\s*you)?)\s*[.!]*", new_message.strip().lower()):
            print("🙏 Exact thank you message, responding with empty.")
            await event.reply("")
            return
        if re.fullmatch(r"(thank\s*you(\s*team)?|thanks(\s*team)?)\s*[.!]*", new_message.strip().lower()):
            print("🙏 Detected thank you message, replying with 'My pleasure.'")
            await event.reply("My pleasure.")
            return
        
        replied_msg_text = ""
        if event.message.is_reply:
            replied_msg = await event.message.get_reply_message()
            if replied_msg and replied_msg.message:
                replied_msg_text = replied_msg.message.strip()

        combined_message = f"{replied_msg_text}\n{new_message}".strip()

        ask_time = datetime.now(ZoneInfo("Asia/Bangkok")).strftime('%d/%m/%Y %H:%M:%S')
        print(f"📨 Message from {username}:\n{combined_message}")
        response = ask_openai(combined_message)
        reply_time = datetime.now(ZoneInfo("Asia/Bangkok")).strftime('%d/%m/%Y %H:%M:%S')

        cleaned_response = re.sub(r"\*\*(.*?)\*\*", r"\1", response)
        cleaned_response = re.sub(r"###\s*(.*?)", r"\1", cleaned_response)

        print(f"Assistant Response:\n{cleaned_response}")
        await event.reply(cleaned_response)

        # ─── SPECIAL RESPONSE LOGGING ─────────────────────
        special_phrases = [
            "I’ve received your request. Please wait a moment while I check it for you.",
            "I’ve received your message and will check it during business hours."
        ]

        if any(phrase in cleaned_response for phrase in special_phrases):
            try:
                scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
                client_gs = gspread.authorize(creds)
                slip_sheet = client_gs.open_by_url(SHEET_URL).worksheet("Report Slip")
                slip_sheet.append_row([username, ask_time, combined_message, reply_time, cleaned_response])
            except Exception as e:
                print(f"❌ Failed to write to 'Report Slip': {e}")

        # ─── NORMAL LOGGING ───────────────────────────────
        sheet.append_row([username, ask_time, combined_message, reply_time, cleaned_response])

    except Exception as e:
        print(f"❌ Error in Telegram handler: {e}")

# ─── OTP CODE RETRIEVAL ────────────────────────────────────────
def get_otp_from_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
        client_gs = gspread.authorize(creds)
        otp_sheet = client_gs.open_by_url(SHEET_URL).worksheet("OTP Input")
        return otp_sheet.acell("B2").value.strip()
    except Exception:
        return None

# ─── SESSION FILE CLEANUP ──────────────────────────────────────
def delete_old_session():
    for f in glob.glob("session_name_dev*"):
        try:
            os.remove(f)
            print(f"🧹 Deleted old session file: {f}")
        except Exception as e:
            print(f"⚠️ Failed to delete session file {f}: {e}")

# ─── MAIN FUNCTION ─────────────────────────────────────────────
def main():
    print("🤖 Starting Telegram client using OTP from Google Sheet...")
    clear_sheet_every_3_days()

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
    client_gs = gspread.authorize(creds)
    otp_sheet = client_gs.open_by_url(SHEET_URL).worksheet("OTP Input")

    def code_callback():
        print("📥 Waiting for OTP from Google Sheet...")
        try:
            otp_sheet.update_acell("B2", "")
            otp_sheet.update_acell("C2", "📥 Waiting for OTP from Google Sheet...")
        except Exception as update_error:
            print(f"⚠️ Failed to update waiting status: {update_error}")

        for i in range(120):
            otp = get_otp_from_google_sheet()
            if otp:
                message = f"✅ OTP Received: {otp}"
                print("\n" + message)
                try:
                    otp_sheet.update_acell("B2", "")
                    otp_sheet.update_acell("C2", message)
                except Exception:
                    pass
                return otp
            else:
                wait_msg = f"⌛ Waiting... {i+1}/120"
                print(f"\r{wait_msg}", end='', flush=True)
                try:
                    otp_sheet.update_acell("C2", wait_msg)
                except Exception:
                    pass
            time.sleep(1)

        final_msg = "❌ Invalid code. Please try again."
        print("\n" + final_msg)
        try:
            otp_sheet.update_acell("C2", final_msg)
        except Exception:
            pass
        raise TimeoutError(final_msg)

    try:
        telegram_client.start(phone=PHONE_NUMBER, code_callback=code_callback)
        otp_sheet.update_acell("C2", "✅ Telegram client started successfully.")
        print("✅ Telegram client started successfully.")
        telegram_client.run_until_disconnected()
    except TimeoutError as te:
        print(str(te))
    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        if "session" in str(e).lower():
            print("❌ Session expired. ลบ session เก่าและรอเข้าสู่ระบบใหม่...")
            delete_old_session()

if __name__ == "__main__":
    main()
