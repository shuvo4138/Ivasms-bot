import logging
import asyncio
import re
import time
import httpx
from datetime import datetime
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# =============================================
#              CONFIG
# =============================================
BOT_TOKEN = "8609781731:AAEZzctHzmLndplCWf0XhuY9RyJvRoTLAfk"
ADMIN_ID = 1984916365
CHANNEL_USERNAME = "@alwaysrvice24hours"
CHANNEL_LINK = "https://t.me/alwaysrvice24hours"

IVASMS_EMAIL = "shovosrb168@gmail.com"
IVASMS_PASSWORD = "Shuvo.99@@"
IVASMS_BASE = "https://www.ivasms.com"

logging.basicConfig(level=logging.INFO)

# =============================================
#         DATA STORE
# =============================================

# Number pool — admin add করবে
# Format: {number: {"status": "available/taken", "user_id": None, "otp": None}}
number_pool = {}

# User data
user_data = {}

# ivasms session cache
_ivasms_session = {"cookies": None, "time": 0}

# =============================================
#         IVASMS LOGIN & OTP
# =============================================

async def ivasms_login():
    global _ivasms_session
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=HEADERS) as client:
            # Login page load (CSRF token নাও)
            r1 = await client.get(f"{IVASMS_BASE}/portal/login")
            csrf = ""
            for pattern in [
                r'name="_token"\s+value="([^"]+)"',
                r'_token["\s]+value=["\']([^"\']+)["\']',
                r'"_token":"([^"]+)"',
            ]:
                match = re.search(pattern, r1.text)
                if match:
                    csrf = match.group(1)
                    break

            await asyncio.sleep(1)

            # Login
            r2 = await client.post(
                f"{IVASMS_BASE}/portal/login",
                data={
                    "email": IVASMS_EMAIL,
                    "password": IVASMS_PASSWORD,
                    "_token": csrf
                },
                headers={
                    **HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{IVASMS_BASE}/portal/login",
                    "Origin": IVASMS_BASE,
                }
            )

            if r2.status_code in [200, 302] and "login" not in str(r2.url):
                cookies = dict(client.cookies)
                _ivasms_session = {"cookies": cookies, "time": time.time()}
                logging.info("✅ ivasms login সফল!")
                return cookies

            # Try with session cookies anyway
            cookies = dict(client.cookies)
            if cookies:
                _ivasms_session = {"cookies": cookies, "time": time.time()}
                logging.info("✅ ivasms session saved!")
                return cookies

            logging.error(f"ivasms login failed: {r2.status_code} {r2.url}")
            return None
    except Exception as e:
        logging.error(f"ivasms login error: {e}")
        return None

async def get_ivasms_session():
    if _ivasms_session["cookies"] and (time.time() - _ivasms_session["time"]) < 1800:
        return _ivasms_session["cookies"]
    return await ivasms_login()

async def check_otp_ivasms(number, wait=120):
    """ivasms থেকে OTP check করো"""
    clean = str(number).replace("+", "").strip()
    start = time.time()

    while (time.time() - start) < wait:
        try:
            cookies = await get_ivasms_session()
            if not cookies:
                await asyncio.sleep(10)
                continue

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15,
                cookies=cookies
            ) as client:
                res = await client.get(
                    f"{IVASMS_BASE}/portal/live/my_sms",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Referer": f"{IVASMS_BASE}/portal/dashboard"
                    }
                )
                html = res.text

                # Number match করো
                if clean in html or clean[-8:] in html:
                    # OTP extract করো
                    patterns = [
                        rf'{re.escape(clean[-8:])}.*?(\d{{4,8}})',
                        r'(\d{4,8})\s*is your.*?code',
                        r'code[:\s]+(\d{4,8})',
                        r'OTP[:\s]+(\d{4,8})',
                        r'\b(\d{6})\b',
                        r'\b(\d{5})\b',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, html, re.IGNORECASE)
                        if match:
                            return match.group(1)

        except Exception as e:
            logging.error(f"ivasms OTP check error: {e}")

        await asyncio.sleep(5)

    return None

# =============================================
#         HELPERS
# =============================================

def init_user(user_id):
    if user_id not in user_data:
        user_data[user_id] = {
            "name": "User",
            "current_number": None,
            "waiting_for": None,
            "joined": datetime.now().strftime("%Y-%m-%d %H:%M")
        }

async def check_joined(user_id, bot):
    now = time.time()
    cached = user_data.get(user_id, {}).get("join_cache")
    if cached and (now - cached["time"]) < 600:
        return cached["joined"]
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        joined = member.status in ["member", "administrator", "creator"]
        if user_id not in user_data:
            init_user(user_id)
        user_data[user_id]["join_cache"] = {"joined": joined, "time": now}
        return joined
    except:
        return True

def get_available_number():
    for num, info in number_pool.items():
        if info["status"] == "available":
            return num
    return None

def count_numbers():
    total = len(number_pool)
    available = sum(1 for v in number_pool.values() if v["status"] == "available")
    taken = sum(1 for v in number_pool.values() if v["status"] == "taken")
    return total, available, taken

# =============================================
#         KEYBOARDS
# =============================================

def main_keyboard(user_id=None):
    buttons = [
        [KeyboardButton("🏠 Home"), KeyboardButton("📞 Get Number")],
        [KeyboardButton("👁️ Check OTP"), KeyboardButton("📋 My Number")],
    ]
    if user_id and user_id == ADMIN_ID:
        buttons.append([KeyboardButton("👑 Admin Panel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def after_number_keyboard(number):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁️ Check OTP", callback_data=f"checkotp_{number}")],
        [InlineKeyboardButton("🔄 New Number", callback_data="get_number"),
         InlineKeyboardButton("🏠 Home", callback_data="go_home")],
    ])

# =============================================
#         COMMAND HANDLERS
# =============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    init_user(user_id)
    user_data[user_id]["name"] = user.first_name or "User"

    joined = await check_joined(user_id, context.bot)
    if not joined:
        await update.message.reply_text(
            "⚠️ Channel Join করুন!\n\nBot ব্যবহার করতে channel join করতে হবে।",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Channel Join করুন", url=CHANNEL_LINK)
            ]])
        )
        return

    total, available, taken = count_numbers()

    await update.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, {user.first_name}!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 NUMBER OTP BOT\n\n"
        f"📊 Numbers Available: {available}\n\n"
        f"👇 নিচের button চাপুন:\n"
        f"━━━━━━━━━━━━━━━━━━",
        reply_markup=main_keyboard(user_id)
    )

async def cmd_get_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    init_user(user_id)

    joined = await check_joined(user_id, context.bot)
    if not joined:
        await update.message.reply_text(
            "⚠️ Channel Join করুন!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Join", url=CHANNEL_LINK)
            ]])
        )
        return

    # আগের number আছে কিনা check
    current = user_data[user_id].get("current_number")
    if current and number_pool.get(current, {}).get("status") == "taken":
        await update.message.reply_text(
            f"⚠️ তোমার কাছে আগের number আছে!\n\n"
            f"📞 `{current}`\n\n"
            f"আগে OTP নাও বা নতুন নিতে চাইলে বলো।",
            parse_mode="Markdown",
            reply_markup=after_number_keyboard(current)
        )
        return

    # নতুন number দাও
    number = get_available_number()
    if not number:
        await update.message.reply_text(
            "❌ এখন কোনো number available নেই!\n\nAdmin কে জানাও।",
            reply_markup=main_keyboard(user_id)
        )
        return

    # Number টা assign করো
    number_pool[number]["status"] = "taken"
    number_pool[number]["user_id"] = user_id
    user_data[user_id]["current_number"] = number

    total, available, taken = count_numbers()

    await update.message.reply_text(
        f"✅ Number পাওয়া গেছে!\n\n"
        f"📞 `{number}`\n\n"
        f"🔍 OTP আসার অপেক্ষায়...\n"
        f"📊 Remaining: {available}",
        parse_mode="Markdown",
        reply_markup=after_number_keyboard(number)
    )

    # Auto OTP check শুরু করো
    asyncio.create_task(auto_otp_check(update.message, number, user_id))

async def auto_otp_check(message, number, user_id):
    """Auto OTP check করো ivasms থেকে"""
    otp = await check_otp_ivasms(number, wait=120)

    if user_data.get(user_id, {}).get("current_number") != number:
        return

    if otp:
        await message.reply_text(
            f"🔑 OTP পাওয়া গেছে!\n\n"
            f"📞 Number: `{number}`\n"
            f"🔑 OTP: `{otp}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(user_id)
        )
        # Number free করো
        if number in number_pool:
            number_pool[number]["status"] = "available"
            number_pool[number]["user_id"] = None
        user_data[user_id]["current_number"] = None
    else:
        await message.reply_text(
            f"⏳ OTP আসেনি!\n\n"
            f"📞 Number: `{number}`\n\n"
            f"আবার try করুন।",
            parse_mode="Markdown",
            reply_markup=after_number_keyboard(number)
        )

# =============================================
#         ADMIN COMMANDS
# =============================================

async def cmd_addnumber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin number add করবে — /addnumber 23276782565"""
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        user_data[ADMIN_ID]["waiting_for"] = "add_number"
        await update.message.reply_text(
            "📞 Number দিন:\n\nএকটা number দিন বা একটা .txt file send করুন।"
        )
        return

    number = context.args[0].replace("+", "").strip()
    if number in number_pool:
        await update.message.reply_text(f"⚠️ {number} আগে থেকেই আছে!")
        return

    number_pool[number] = {"status": "available", "user_id": None, "otp": None}
    total, available, taken = count_numbers()
    await update.message.reply_text(
        f"✅ Number add হয়েছে!\n\n"
        f"📞 {number}\n"
        f"📊 Total: {total} | Available: {available}"
    )

async def cmd_removenumber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin number remove করবে — /removenumber 23276782565"""
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /removenumber <number>")
        return

    number = context.args[0].replace("+", "").strip()
    if number in number_pool:
        del number_pool[number]
        await update.message.reply_text(f"✅ {number} remove হয়েছে!")
    else:
        await update.message.reply_text(f"❌ {number} পাওয়া যায়নি!")

async def cmd_listnumbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin সব numbers দেখবে"""
    if update.effective_user.id != ADMIN_ID:
        return

    if not number_pool:
        await update.message.reply_text("❌ কোনো number নেই!")
        return

    total, available, taken = count_numbers()
    msg = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 NUMBER LIST\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Total: {total}\n"
        f"✅ Available: {available}\n"
        f"❌ Taken: {taken}\n\n"
    )

    for num, info in list(number_pool.items())[:20]:
        status = "✅" if info["status"] == "available" else "❌"
        msg += f"{status} {num}\n"

    await update.message.reply_text(msg)

async def cmd_clearnumbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin সব numbers clear করবে"""
    if update.effective_user.id != ADMIN_ID:
        return
    number_pool.clear()
    await update.message.reply_text("✅ সব numbers clear হয়েছে!")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    total, available, taken = count_numbers()
    cookies = await get_ivasms_session()
    ivasms_status = "✅ Connected" if cookies else "❌ Disconnected"
    await update.message.reply_text(
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 BOT STATS\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Users: {len(user_data)}\n"
        f"📞 Numbers: {total}\n"
        f"✅ Available: {available}\n"
        f"❌ Taken: {taken}\n"
        f"🔗 ivasms: {ivasms_status}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

# =============================================
#         CALLBACK HANDLER
# =============================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    init_user(user_id)
    data = query.data

    if data == "go_home":
        total, available, taken = count_numbers()
        await query.message.reply_text(
            f"🏠 Home\n\n📊 Numbers Available: {available}",
            reply_markup=main_keyboard(user_id)
        )

    elif data == "get_number":
        # নতুন number দাও
        current = user_data[user_id].get("current_number")
        if current and number_pool.get(current, {}).get("status") == "taken":
            number_pool[current]["status"] = "available"
            number_pool[current]["user_id"] = None
            user_data[user_id]["current_number"] = None

        number = get_available_number()
        if not number:
            await query.message.reply_text(
                "❌ কোনো number available নেই!",
                reply_markup=main_keyboard(user_id)
            )
            return

        number_pool[number]["status"] = "taken"
        number_pool[number]["user_id"] = user_id
        user_data[user_id]["current_number"] = number

        total, available, taken = count_numbers()

        await query.edit_message_text(
            f"✅ Number পাওয়া গেছে!\n\n"
            f"📞 `{number}`\n\n"
            f"🔍 OTP আসার অপেক্ষায়...\n"
            f"📊 Remaining: {available}",
            parse_mode="Markdown",
            reply_markup=after_number_keyboard(number)
        )
        asyncio.create_task(auto_otp_check(query.message, number, user_id))

    elif data.startswith("checkotp_"):
        number = data.replace("checkotp_", "")
        await query.message.reply_text(f"⏳ OTP check হচ্ছে...")
        otp = await check_otp_ivasms(number, wait=30)
        if otp:
            await query.message.reply_text(
                f"🔑 OTP পাওয়া গেছে!\n\n"
                f"📞 Number: `{number}`\n"
                f"🔑 OTP: `{otp}`",
                parse_mode="Markdown",
                reply_markup=main_keyboard(user_id)
            )
            if number in number_pool:
                number_pool[number]["status"] = "available"
                number_pool[number]["user_id"] = None
            user_data[user_id]["current_number"] = None
        else:
            await query.message.reply_text(
                "⏳ OTP এখনো আসেনি। কিছুক্ষণ পর আবার try করুন।",
                reply_markup=after_number_keyboard(number)
            )

# =============================================
#         MESSAGE HANDLER
# =============================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    user = update.effective_user
    user_id = user.id
    init_user(user_id)
    user_data[user_id]["name"] = user.first_name or "User"

    joined = await check_joined(user_id, context.bot)
    if not joined:
        await update.message.reply_text(
            "⚠️ Channel Join করুন!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Join", url=CHANNEL_LINK)
            ]])
        )
        return

    if text == "🏠 Home":
        await start(update, context)
        return

    if text == "📞 Get Number":
        await cmd_get_number(update, context)
        return

    if text == "👁️ Check OTP":
        number = user_data[user_id].get("current_number")
        if not number:
            await update.message.reply_text(
                "❌ তোমার কাছে কোনো number নেই!\n\nআগে number নাও।",
                reply_markup=main_keyboard(user_id)
            )
            return
        await update.message.reply_text(f"⏳ OTP check হচ্ছে...")
        otp = await check_otp_ivasms(number, wait=30)
        if otp:
            await update.message.reply_text(
                f"🔑 OTP পাওয়া গেছে!\n\n"
                f"📞 Number: `{number}`\n"
                f"🔑 OTP: `{otp}`",
                parse_mode="Markdown",
                reply_markup=main_keyboard(user_id)
            )
            if number in number_pool:
                number_pool[number]["status"] = "available"
            user_data[user_id]["current_number"] = None
        else:
            await update.message.reply_text(
                "⏳ OTP এখনো আসেনি।",
                reply_markup=after_number_keyboard(number)
            )
        return

    if text == "📋 My Number":
        number = user_data[user_id].get("current_number")
        if number:
            await update.message.reply_text(
                f"📞 তোমার current number:\n\n`{number}`",
                parse_mode="Markdown",
                reply_markup=after_number_keyboard(number)
            )
        else:
            await update.message.reply_text(
                "❌ তোমার কাছে কোনো number নেই!",
                reply_markup=main_keyboard(user_id)
            )
        return

    if text == "👑 Admin Panel":
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Admin access নেই!")
            return
        total, available, taken = count_numbers()
        await update.message.reply_text(
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👑 ADMIN PANEL\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 /addnumber <number> — number add\n"
            f"❌ /removenumber <number> — number remove\n"
            f"📋 /listnumbers — সব numbers দেখো\n"
            f"🗑️ /clearnumbers — সব clear\n"
            f"📊 /stats — bot stats\n\n"
            f"📊 Numbers: {total} | Available: {available}\n\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return

    # Admin number add waiting
    if user_id == ADMIN_ID and user_data[user_id].get("waiting_for") == "add_number":
        user_data[user_id]["waiting_for"] = None
        number = text.replace("+", "").strip()
        if re.match(r'^\d{7,15}$', number):
            number_pool[number] = {"status": "available", "user_id": None, "otp": None}
            total, available, taken = count_numbers()
            await update.message.reply_text(
                f"✅ Number add হয়েছে!\n\n"
                f"📞 {number}\n"
                f"📊 Total: {total} | Available: {available}"
            )
        else:
            await update.message.reply_text("❌ Invalid number!")
        return

    # Admin txt file handle
    if user_id == ADMIN_ID and update.message.document:
        return

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """TXT file থেকে numbers import করো"""
    if update.effective_user.id != ADMIN_ID:
        return

    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        await update.message.reply_text("❌ শুধু .txt file!")
        return

    file = await context.bot.get_file(doc.file_id)
    content = await file.download_as_bytearray()
    text = content.decode("utf-8")

    added = 0
    for line in text.strip().split("\n"):
        number = line.replace("+", "").strip()
        if re.match(r'^\d{7,15}$', number) and number not in number_pool:
            number_pool[number] = {"status": "available", "user_id": None, "otp": None}
            added += 1

    total, available, taken = count_numbers()
    await update.message.reply_text(
        f"✅ {added}টা number add হয়েছে!\n\n"
        f"📊 Total: {total} | Available: {available}"
    )

# =============================================
#              MAIN
# =============================================

async def post_init(application):
    """Bot start হলে ivasms login করো"""
    cookies = await ivasms_login()
    if cookies:
        logging.info("✅ ivasms session ready!")
    else:
        logging.warning("⚠️ ivasms login failed!")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addnumber", cmd_addnumber))
    app.add_handler(CommandHandler("removenumber", cmd_removenumber))
    app.add_handler(CommandHandler("listnumbers", cmd_listnumbers))
    app.add_handler(CommandHandler("clearnumbers", cmd_clearnumbers))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ ivasms OTP Bot running...")
    app.run_polling(drop_pending_updates=True)
