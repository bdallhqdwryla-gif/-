import logging
import asyncio
import sqlite3
import os
import re
from collections import deque                     # 🆕 تعديل 1
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, ChatMemberUpdated, BufferedInputFile
from telethon import TelegramClient, events, utils
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import UserAlreadyParticipantError, InviteRequestSentError, FloodWaitError

BOT_TOKEN = '8813077473:AAF1X8IncXn8V1SMr8PZGa2IyVLt38P_XXU'
API_ID = 32048562
API_HASH = 'b506bb7bd73b2e7833fdffe6553eaa98'

DEV_NAME = "سايكو"
DEV_USERNAME = "Yvvvvvu"
DEV_CHANNEL = "@y_6uu"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

SESSION_FILES = ['session_name', 'session_name_2', 'session_name_3']
clients = []

# 🆕 تعديل 2: deque محدود بدل set لا نهائي لمنع استهلاك الرام
sent_cache = deque(maxlen=5000)

DB_PATH = 'bot_database.db'          # 🆕 تعديل 3: مسار موحّد

def db_connect():                     # 🆕 تعديل 3: دالة موحّدة لقاعدة البيانات
    return sqlite3.connect(DB_PATH, timeout=30.0)

def init_db():
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_monitoring (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_chat_id INTEGER,
            monitor_link TEXT,
            monitor_chat_id INTEGER,
            group_name TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_usernames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            target_chat_id INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(username, target_chat_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            target_chat_id INTEGER PRIMARY KEY,
            photo_only INTEGER DEFAULT 0,
            vip_only INTEGER DEFAULT 0,
            is_paused INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_joins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_chat_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assistant_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_name TEXT UNIQUE,
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    try: cursor.execute('ALTER TABLE pending_joins ADD COLUMN monitor_link TEXT')
    except sqlite3.OperationalError: pass
    try: cursor.execute('ALTER TABLE pending_joins ADD COLUMN group_name TEXT')
    except sqlite3.OperationalError: pass
    try: cursor.execute('ALTER TABLE pending_joins ADD COLUMN hash_str TEXT')
    except sqlite3.OperationalError: pass
    try: cursor.execute('ALTER TABLE pending_joins ADD COLUMN username TEXT')
    except sqlite3.OperationalError: pass
    try: cursor.execute('ALTER TABLE settings ADD COLUMN vip_only INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: cursor.execute('ALTER TABLE settings ADD COLUMN is_paused INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

init_db()


class BotStates(StatesGroup):
    waiting_for_monitor_link = State()
    waiting_for_broadcast_msg = State()
    waiting_for_ban_user = State()
    waiting_for_assistant_session = State()


async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=DEV_CHANNEL, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
    except Exception:
        pass
    return False


def is_user_banned(user_id: int) -> bool:
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM banned_users WHERE user_id = ?', (user_id,))
    res = cursor.fetchone()
    conn.close()
    return True if res else False


def main_menu_keyboard(target_chat_id):
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT photo_only, vip_only, is_paused FROM settings WHERE target_chat_id = ?', (target_chat_id,))
    res = cursor.fetchone()
    photo_only = res[0] if res else 0
    vip_only = res[1] if res and len(res) > 1 else 0
    is_paused = res[2] if res and len(res) > 2 else 0
    conn.close()

    if photo_only == 1:
        filter_text = "الفلترة: [بصورة شخصية فقط]"
    elif vip_only == 1:
        filter_text = "الفلترة: [يوزرات مميزة فقط ⭐]"
    else:
        filter_text = "الفلترة: [الجميع]"

    pause_text = "▶️ استئناف المراقبة" if is_paused == 1 else "⏸️ إيقاف مؤقت للمراقبة"

    keyboard_rows = [
        [
            InlineKeyboardButton(text="الكروبات المخزنة والمجلدات", callback_data="section_storage", text_parse_mode="html", icon_custom_emoji_id="5944970130554359187"),
            InlineKeyboardButton(text="الإحصائيات", callback_data="section_stats", text_parse_mode="html", icon_custom_emoji_id="5328159848427189012")
        ],
        [
            InlineKeyboardButton(text="تفعيل المراقبة لهذا الكروب", callback_data="active_monitor", text_parse_mode="html", icon_custom_emoji_id="5776208830996159244")
        ],
        [
            InlineKeyboardButton(text=pause_text, callback_data="toggle_pause_monitoring", text_parse_mode="html", icon_custom_emoji_id="5251340630306612580")
        ],
        [
            InlineKeyboardButton(text="📢 تاك للكل (بلشو خمط)", callback_data="tag_all_members", text_parse_mode="html", icon_custom_emoji_id="5776208830996159244")
        ],
        [
            InlineKeyboardButton(text=filter_text, callback_data="toggle_filter", text_parse_mode="html", icon_custom_emoji_id="5287696755373124799")
        ],
        [
            InlineKeyboardButton(text="تصدير اليوزرات (.txt)", callback_data="export_users", text_parse_mode="html", icon_custom_emoji_id="5325869406792743566")
        ],
        [
            InlineKeyboardButton(text="إيقاف المراقبة (حذف نهائي)", callback_data="stop_monitoring", text_parse_mode="html", icon_custom_emoji_id="5251340630306612580")
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def is_vip_username(username: str) -> bool:
    if not username:
        return False
    length = len(username)
    if length <= 7:
        return True
    if re.search(r'([a-zA-Z])\1{2,}', username):
        return False
    return False


@dp.message(lambda message: message.text and message.text.strip() == "المطور")
async def dev_info_command(message: types.Message):
    try:
        photos = await bot.get_user_profile_photos(message.from_user.id)
        caption = (
            f"👑 **مطور البوت الأساسي**\n\n"
            f"👤 الاسم: {DEV_NAME}\n"
            f"🔗 المعرف: @{DEV_USERNAME}\n"
            f"💎 القناة الرسمية: {DEV_CHANNEL}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"تواصل مع المطور 👨‍💻", url=f"https://t.me/{DEV_USERNAME}")]
            ]
        )
        
        if photos.total_count > 0:
            photo_file_id = photos.photos[0][-1].file_id
            await message.reply_photo(photo=photo_file_id, caption=caption, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await message.reply(caption, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await message.reply(f"👑 المطور: {DEV_NAME}\n🔗 المعرف: @{DEV_USERNAME}")


@dp.my_chat_member()
async def bot_added_to_group(event: ChatMemberUpdated):
    if event.new_chat_member.status in ['member', 'administrator'] and event.old_chat_member.status in ['left', 'kicked', 'restricted']:
        chat_id = event.chat.id
        try:
            link = await bot.create_chat_invite_link(chat_id=chat_id, member_limit=1)
            invite_link = link.invite_link
            hash_str = invite_link.split('+')[-1]
            for cl in clients:
                try:
                    await cl(ImportChatInviteRequest(hash_str))
                    break
                except FloodWaitError as e:
                    logging.warning(f"FloodWait auto-join: {e.seconds}s")
                    await asyncio.sleep(e.seconds)
                except Exception:
                    continue
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite_link)
            await bot.send_message(chat_id, "✅ **تم إضافة حساب المساعد تلقائياً** لتسهيل التاك والمراقبة!")
        except Exception as e:
            logging.error(f"Auto-join error: {e}")
            await bot.send_message(chat_id, "⚠️ أضفت البوت، لكن تعذر إضافة حساب المساعد تلقائياً.")


@dp.message(lambda message: message.text and message.text.strip() == '/start' and message.chat.type == 'private')
async def start_private_command(message: types.Message):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        return

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO bot_users (user_id, first_name, username) VALUES (?, ?, ?)', 
                   (user_id, message.from_user.first_name, message.from_user.username))
    conn.commit()
    conn.close()

    if not await check_subscription(user_id):
        sub_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="اشتري / اشترك بالقناة 🔔", url=f"https://t.me/{DEV_CHANNEL.replace('@', '')}")],
                [InlineKeyboardButton(text="تحقق من الاشتراك ✅", callback_data="check_sub")]
            ]
        )
        await message.reply(f"⚠️ **عذراً عزيزي!**\n\nيجب عليك الاشتراك في قناة البوت الرسمية أولاً:\n🔗 {DEV_CHANNEL}", reply_markup=sub_keyboard)
        return

    bot_info = await bot.get_me()
    keyboard_rows = [[InlineKeyboardButton(text="اضف البوت الى مقرك وبلش خمط", url=f"https://t.me/{bot_info.username}?startgroup=true", icon_custom_emoji_id="5776012417846745906")]]

    if message.from_user.username and message.from_user.username.lower() == DEV_USERNAME.lower():
        keyboard_rows.append([InlineKeyboardButton(text="🛠️ إدارة الحسابات المساعدة (خاص بالمطور)", callback_data="dev_panel")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await message.reply(f"أهلاً بك يا عزيزي في بوت يوزرات الاحترافي.\n\nالمطور: {DEV_NAME} (@{DEV_USERNAME})", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == 'dev_panel')
async def dev_panel_callback(callback_query: types.CallbackQuery):
    if callback_query.from_user.username.lower() != DEV_USERNAME.lower():
        await callback_query.answer("❌ للمطور فقط!", show_alert=True)
        return
    
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM bot_users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM group_monitoring')
    total_monitored = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM assistant_accounts')
    total_assistants = cursor.fetchone()[0]
    conn.close()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 إدارة وحفظ الحسابات المساعدة", callback_data="dev_manage_assistants")],
            [InlineKeyboardButton(text="📢 إرسال إذاعة للكل", callback_data="dev_broadcast_btn")],
            [InlineKeyboardButton(text="🚫 حظر مستخدم", callback_data="dev_ban_btn")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main_private", text_parse_mode="html", icon_custom_emoji_id="5774029646194547127")]
        ]
    )
    await callback_query.message.edit_text(
        text=f"🛠️ **لوحة تحكم المطور:**\n\n👥 مستخدمي البوت: `{total_users}`\n👁️ المجموعات المراقبة: `{total_monitored}`\n🤖 الحسابات المساعدة: `{total_assistants + len(clients)}`",
        reply_markup=keyboard
    )


@dp.callback_query(lambda c: c.data == 'dev_manage_assistants')
async def dev_manage_assistants(callback_query: types.CallbackQuery):
    if callback_query.from_user.username.lower() != DEV_USERNAME.lower():
        await callback_query.answer("❌ للمطور فقط!", show_alert=True)
        return

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT session_name FROM assistant_accounts')
    assistants = cursor.fetchall()
    conn.close()

    text = "🤖 **قائمة الحسابات المساعدة (UserBots):**\n\n"
    text += f"• الحساب الأساسي: `session_name` (متصل)\n"
    for idx, (s_name,) in enumerate(assistants, 2):
        text += f"• حساب احتياطي {idx}: `{s_name}`\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ إضافة جلسة مساعد جديدة", callback_data="dev_add_assistant")],
            [InlineKeyboardButton(text="⬅️ رجوع لوحة المطور", callback_data="dev_panel")]
        ]
    )
    await callback_query.message.edit_text(text=text, reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == 'dev_add_assistant')
async def dev_add_assistant(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.username.lower() != DEV_USERNAME.lower():
        return
    await callback_query.answer()
    await callback_query.message.reply("✍️ **أرسل اسم ملف الجلسة (Session Name) الجديد للمساعد الاحتياطي:**\n(مثال: `session_name_2`)")
    await state.set_state(BotStates.waiting_for_assistant_session)


@dp.message(BotStates.waiting_for_assistant_session)
async def process_add_assistant(message: types.Message, state: FSMContext):
    if message.from_user.username.lower() != DEV_USERNAME.lower():
        return
    session_name = message.text.strip()
    try:
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO assistant_accounts (session_name) VALUES (?)', (session_name,))
        conn.commit()
        conn.close()

        new_client = TelegramClient(session_name, API_ID, API_HASH)
        # 🆕 تعديل 8: مهلة 60 ثانية لمنع تعليق البوت إذا كانت الجلسة تحتاج تحقق
        await asyncio.wait_for(new_client.start(), timeout=60)
        clients.append(new_client)
        await register_assistant_events(new_client)

        await message.reply(f"✅ **تم إضافة وتفعيل حساب المساعد الاحتياطي ({session_name}) بنجاح!**")
    except asyncio.TimeoutError:
        await message.reply("⚠️ **انتهت مهلة تشغيل الجلسة!**\nقد تحتاج الجلسة إلى رمز تحقق. شغّلها يدوياً أولاً عبر ملف منفصل.")
    except Exception as e:
        await message.reply(f"⚠️ حدث خطأ أثناء تشغيل الجلسة: {str(e)[:100]}")
    await state.clear()


@dp.callback_query(lambda c: c.data == 'dev_broadcast_btn')
async def ask_for_broadcast(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.username.lower() != DEV_USERNAME.lower():
        return
    await callback_query.answer()
    await callback_query.message.reply("✍️ **أرسل الآن رسالة الإذاعة:**")
    await state.set_state(BotStates.waiting_for_broadcast_msg)


@dp.message(BotStates.waiting_for_broadcast_msg)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.username.lower() != DEV_USERNAME.lower():
        return

    text_to_send = message.text
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM bot_users')
    users = cursor.fetchall()
    conn.close()
    
    msg = await message.reply(f"⏳ جاري إرسال الإذاعة لـ {len(users)} مستخدم...")
    success, failed = 0, 0
    for u in users:
        try:
            await bot.send_message(u[0], text_to_send)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            
    await msg.edit_text(f"✅ **تمت الإذاعة بنجاح!**\n\n✔️ نجح: `{success}`\n❌ فشل: `{failed}`", parse_mode="Markdown")
    await state.clear()


@dp.callback_query(lambda c: c.data == 'dev_ban_btn')
async def ask_ban_user(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.username.lower() != DEV_USERNAME.lower():
        return
    await callback_query.answer()
    await callback_query.message.reply("🆔 **أرسل أيدي المستخدم المراد حظره:**")
    await state.set_state(BotStates.waiting_for_ban_user)


@dp.message(BotStates.waiting_for_ban_user)
async def process_ban_user(message: types.Message, state: FSMContext):
    if message.from_user.username.lower() != DEV_USERNAME.lower():
        return
    try:
        b_id = int(message.text.strip())
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)', (b_id,))
        conn.commit()
        conn.close()
        await message.reply(f"🚫 تم حظر المستخدم بنجاح (`{b_id}`)")
    except Exception:
        await message.reply("⚠️ يرجى إرسال أيدي صحيح.")
    await state.clear()


@dp.callback_query(lambda c: c.data == 'check_sub')
async def verify_subscription(callback_query: types.CallbackQuery):
    if not await check_subscription(callback_query.from_user.id):
        await callback_query.answer("❌ لم تقم بالاشتراك في القناة بعد!", show_alert=True)
        return
    await callback_query.answer("✅ تم التحقق من اشتراكك بنجاح!", show_alert=True)
    bot_info = await bot.get_me()
    keyboard_rows = [[InlineKeyboardButton(text="اضف البوت الى مقرك وبلش خمط", url=f"https://t.me/{bot_info.username}?startgroup=true", icon_custom_emoji_id="5776012417846745906")]]
    if callback_query.from_user.username and callback_query.from_user.username.lower() == DEV_USERNAME.lower():
        keyboard_rows.append([InlineKeyboardButton(text="🛠️ إدارة الحسابات المساعدة (خاص بالمطور)", callback_data="dev_panel")])

    await callback_query.message.edit_text(text="أهلاً بك يا عزيزي في بوت يوزرات.", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows))


@dp.message(lambda message: message.text and message.text.strip() == 'يوزرات')
async def send_menu_on_keyword(message: types.Message):
    if message.chat.type == 'private':
        return
    if is_user_banned(message.from_user.id):
        return
    if not await check_subscription(message.from_user.id):
        await message.reply(f"⚠️ عزيزي `{message.from_user.first_name}`، يجب عليك الاشتراك في قناة البوت ({DEV_CHANNEL}) أولاً.")
        return
    
    try:
        bot_member = await bot.get_chat_member(message.chat.id, (await bot.get_me()).id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.reply("⚠️ **يجب أن يكون البوت مشرفاً (أدمن)** لكي تعمل لوحة التحكم والخمط بشكل صحيح!")
            return
    except Exception:
        pass

    await message.reply("⚡ **لوحة تحكم بوت اليوزرات الاحترافي**", reply_markup=main_menu_keyboard(message.chat.id))


@dp.callback_query(lambda c: c.data == 'tag_all_members')
async def process_tag_all(callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    await callback_query.answer("⏳ جاري جمع الأعضاء وتاك الكل...", show_alert=False)
    try:
        participants = []
        for cl in clients:
            try:
                entity = await cl.get_entity(chat_id)
                participants = await cl.get_participants(entity)
                if participants:
                    break
            except FloodWaitError as e:
                logging.warning(f"FloodWait tag_all: {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception:
                continue

        if not participants:
            await callback_query.message.reply("❌ لم يتم العثور على أعضاء عبر الحسابات المساعدة.")
            return

        mentions = []
        for user in participants:
            if user.bot:
                continue
            name = (user.first_name or "عضو").replace("<", "").replace(">", "")
            mentions.append(f"<a href='tg://user?id={user.id}'>{name}</a>")

        for i in range(0, len(mentions), 35):
            chunk = mentions[i:i + 35]
            await bot.send_message(chat_id, "🔥 **بلشو خمط يا شباب!** 🚀\n\n" + " ".join(chunk), parse_mode="HTML")
            await asyncio.sleep(1.5)
    except Exception as e:
        logging.error(f"Error in tag all: {e}")
        await callback_query.message.reply("⚠️ تأكد من أن حساب المساعد متواجد داخل المجموعة.")


@dp.callback_query(lambda c: c.data == 'toggle_pause_monitoring')
async def toggle_pause_monitoring(callback_query: types.CallbackQuery):
    target_chat_id = callback_query.message.chat.id
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT is_paused FROM settings WHERE target_chat_id = ?', (target_chat_id,))
    res = cursor.fetchone()
    current_paused = res[0] if res else 0
    new_paused = 0 if current_paused == 1 else 1

    cursor.execute('INSERT OR IGNORE INTO settings (target_chat_id, is_paused) VALUES (?, ?)', (target_chat_id, new_paused))
    cursor.execute('UPDATE settings SET is_paused = ? WHERE target_chat_id = ?', (new_paused, target_chat_id))
    conn.commit()
    conn.close()

    if new_paused == 1:
        await callback_query.answer("⏸️ تم إيقاف المراقبة مؤقتاً (البيانات محفوظة)", show_alert=True)
    else:
        await callback_query.answer("▶️ تم استئناف المراقبة بنجاح!", show_alert=True)

    await callback_query.message.edit_reply_markup(reply_markup=main_menu_keyboard(target_chat_id))


@dp.callback_query(lambda c: c.data == 'toggle_filter')
async def toggle_filter(callback_query: types.CallbackQuery):
    target_chat_id = callback_query.message.chat.id
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT photo_only, vip_only FROM settings WHERE target_chat_id = ?', (target_chat_id,))
    res = cursor.fetchone()
    
    photo_only = res[0] if res else 0
    vip_only = res[1] if res and len(res) > 1 else 0

    if photo_only == 0 and vip_only == 0:
        new_photo, new_vip = 1, 0
        status_msg = "🖼️ التفعيل: بصورة شخصية فقط!"
    elif photo_only == 1:
        new_photo, new_vip = 0, 1
        status_msg = "⭐ التفعيل: يوزرات مميزة فقط (نظيفة)!"
    else:
        new_photo, new_vip = 0, 0
        status_msg = "👥 التفعيل: الجميع!"

    cursor.execute('INSERT OR IGNORE INTO settings (target_chat_id, photo_only, vip_only) VALUES (?, ?, ?)', (target_chat_id, new_photo, new_vip))
    cursor.execute('UPDATE settings SET photo_only = ?, vip_only = ? WHERE target_chat_id = ?', (new_photo, new_vip, target_chat_id))
    conn.commit()
    conn.close()
    
    await callback_query.answer(status_msg, show_alert=True)
    await callback_query.message.edit_reply_markup(reply_markup=main_menu_keyboard(target_chat_id))


@dp.callback_query(lambda c: c.data == 'export_users')
async def export_users_command(callback_query: types.CallbackQuery):
    target_chat_id = callback_query.message.chat.id
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT username FROM sent_usernames WHERE target_chat_id = ?', (target_chat_id,))
    users = cursor.fetchall()
    conn.close()

    if not users:
        await callback_query.answer("❌ لا يوجد يوزرات في السجل لتصديرها لهذا الكروب!", show_alert=True)
        return

    await callback_query.answer("⏳ جاري تجهيز الملف...")
    file_path = f"users_{target_chat_id}.txt"
    
    with open(file_path, "w", encoding="utf-8") as f:
        for u in users:
            f.write(f"@{u[0]}\n")

    await callback_query.message.reply_document(
        document=FSInputFile(file_path),
        caption=f"📁 **تم تصدير يوزرات الكروب بنجاح!**\n\n👤 العدد الكلي: `{len(users)}` يوزر.",
        parse_mode="Markdown"
    )
    os.remove(file_path)


@dp.callback_query(lambda c: c.data == 'section_stats')
async def process_stats_section(callback_query: types.CallbackQuery):
    await callback_query.answer()
    target_chat_id = callback_query.message.chat.id
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM sent_usernames WHERE target_chat_id = ?', (target_chat_id,))
    total = cursor.fetchone()[0]
    conn.close()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع للقائمة", callback_data="back_to_main", text_parse_mode="html", icon_custom_emoji_id="5774029646194547127")]]
    )
    await callback_query.message.edit_text(text=f"📊 **إحصائيات الخمط لهذه المجموعة:**\n\n💎 إجمالي اليوزرات المسجلة: `{total}`", reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(lambda c: c.data == 'section_storage')
async def process_storage_section(callback_query: types.CallbackQuery):
    await callback_query.answer()
    target_chat_id = callback_query.message.chat.id
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT monitor_chat_id, group_name, monitor_link FROM group_monitoring WHERE target_chat_id = ?', (target_chat_id,))
    rows = cursor.fetchall()
    conn.close()
    
    keyboard_buttons = []
    text = "📁 **قسم المجلدات والكروبات المخزنة:**\n\nاختر الكروب أدناه لاستعراضه أو تصدير يوزراته:\n"
    
    if not rows:
        text += "❌ لا توجد كروبات مراقبة مرتبطة بهذا الكروب حالياً."
    else:
        for (m_chat_id, g_name, link) in rows:
            safe_name = g_name or "كروب بدون اسم"
            keyboard_buttons.append([InlineKeyboardButton(text=f"📁 {safe_name}", callback_data=f"folder_group_{m_chat_id}", text_parse_mode="html", icon_custom_emoji_id="5944970130554359187")])

    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ رجوع للقائمة", callback_data="back_to_main", text_parse_mode="html", icon_custom_emoji_id="5774029646194547127")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await callback_query.message.edit_text(text=text, reply_markup=keyboard, disable_web_page_preview=True)


@dp.callback_query(lambda c: c.data and c.data.startswith('folder_group_'))
async def process_folder_group_click(callback_query: types.CallbackQuery):
    await callback_query.answer()
    target_chat_id = callback_query.message.chat.id
    m_chat_id = int(callback_query.data.split('_')[2])

    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT group_name, monitor_link FROM group_monitoring WHERE target_chat_id = ? AND monitor_chat_id = ?', (target_chat_id, m_chat_id))
    group_info = cursor.fetchone()
    
    cursor.execute('SELECT COUNT(*) FROM sent_usernames WHERE target_chat_id = ?', (target_chat_id,))
    total_users = cursor.fetchone()[0]
    conn.close()

    if not group_info:
        await callback_query.answer("⚠️ عذراً، لم يتم العثور على بيانات هذا الكروب.", show_alert=True)
        return

    g_name, link = group_info

    text = (
        f"📂 **مجلد الكروب: {g_name}**\n\n"
        f"🔗 الرابط: `{link}`\n"
        f"💎 إجمالي اليوزرات المسجلة في هذا الكروب: `{total_users}`\n\n"
        f"اضغط على الزر أدناه لتصدير يوزرات هذا الكروب كملف `.txt` فورا:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 تحميل يوزرات هذا المجلد (.txt)", callback_data="export_users", text_parse_mode="html", icon_custom_emoji_id="5325869406792743566")],
            [InlineKeyboardButton(text="⬅️ رجوع لقائمة المجلدات", callback_data="section_storage", text_parse_mode="html", icon_custom_emoji_id="5774029646194547127")]
        ]
    )
    await callback_query.message.edit_text(text=text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(lambda c: c.data == 'active_monitor')
async def ask_for_monitor_link(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="إلغاء", callback_data="back_to_main", text_parse_mode="html", icon_custom_emoji_id="5774029646194547127")]]
    )
    await callback_query.message.edit_text(text="👁️ **تفعيل المراقبة**\n\nأرسل رابط الكروب المراد مراقبته (رابط خاص أو عام):", reply_markup=keyboard)
    await state.set_state(BotStates.waiting_for_monitor_link)


@dp.callback_query(lambda c: c.data == 'stop_monitoring')
async def stop_monitoring_group(callback_query: types.CallbackQuery, state: FSMContext):
    target_chat_id = callback_query.message.chat.id
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM group_monitoring WHERE target_chat_id = ?', (target_chat_id,))
    cursor.execute('DELETE FROM sent_usernames WHERE target_chat_id = ?', (target_chat_id,))
    cursor.execute('DELETE FROM pending_joins WHERE target_chat_id = ?', (target_chat_id,))
    cursor.execute('DELETE FROM settings WHERE target_chat_id = ?', (target_chat_id,))
    conn.commit()
    conn.close()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="القائمة الرئيسية", callback_data="back_to_main", text_parse_mode="html", icon_custom_emoji_id="5774029646194547127")]]
    )
    await callback_query.answer("🛑 تم حذف وإيقاف المراقبة نهائياً!", show_alert=True)
    await callback_query.message.edit_text(text="🛑 **تم إيقاف المراقبة وحذف كافة البيانات المرتبطة.**", reply_markup=keyboard)
    await state.clear()


async def _activate_monitoring(target_chat_id, monitor_link, entity, group_name):
    try:
        peer_id_val = utils.get_peer_id(entity)
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO group_monitoring (target_chat_id, monitor_link, monitor_chat_id, group_name) VALUES (?, ?, ?, ?)',
            (target_chat_id, monitor_link, peer_id_val, group_name)
        )
        conn.commit()
        conn.close()
        keyboard_stop = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="إيقاف المراقبة", callback_data="stop_monitoring", text_parse_mode="html", icon_custom_emoji_id="5251340630306612580")]]
        )
        await bot.send_message(
            target_chat_id,
            f"✅ **تمت الموافقة على طلب الانضمام!**\n\n🔥 بدأ الخمط الآن في: **{group_name}**",
            reply_markup=keyboard_stop
        )
    except Exception as e:
        logging.error(f"_activate_monitoring error: {e}")


async def poll_pending_joins():
    while True:
        await asyncio.sleep(20)
        try:
            conn = db_connect()
            cursor = conn.cursor()
            cursor.execute('SELECT id, target_chat_id, monitor_link, hash_str, username, group_name FROM pending_joins')
            rows = cursor.fetchall()
            conn.close()

            for row in rows:
                row_id, target_chat_id, monitor_link, hash_str, username, pending_name = row
                entity = None
                group_name = pending_name or "كروب مراقبة"

                for cl in clients:
                    try:
                        if hash_str:
                            res = await cl(CheckChatInviteRequest(hash_str))
                            chat_obj = getattr(res, 'chat', None)
                            if chat_obj:
                                entity = chat_obj
                                group_name = getattr(entity, 'title', group_name)
                                break
                        elif username:
                            try:
                                await cl(JoinChannelRequest(username))
                                entity = await cl.get_entity(username)
                                group_name = getattr(entity, 'title', group_name)
                                break
                            except UserAlreadyParticipantError:
                                entity = await cl.get_entity(username)
                                group_name = getattr(entity, 'title', group_name)
                                break
                    except FloodWaitError as e:
                        logging.warning(f"FloodWait pending_joins: {e.seconds}s")
                        await asyncio.sleep(e.seconds)
                        continue
                    except Exception:
                        continue

                await asyncio.sleep(2)
                if entity:
                    conn2 = db_connect()
                    cur2 = conn2.cursor()
                    cur2.execute('DELETE FROM pending_joins WHERE id = ?', (row_id,))
                    conn2.commit()
                    conn2.close()
                    await _activate_monitoring(target_chat_id, monitor_link, entity, group_name)
        except Exception as e:
            logging.error(f"poll_pending_joins error: {e}")


async def register_assistant_events(cl: TelegramClient):
    @cl.on(events.ChatAction)
    async def on_assistant_added(event):
        try:
            me = await cl.get_me()
            if not (event.user_added or event.user_joined):
                return
            if event.user_id != me.id:
                return
            chat = event.chat
            if not chat:
                return

            chat_obj_id = getattr(chat, 'id', None)
            chat_username = getattr(chat, 'username', None)
            group_name = getattr(chat, 'title', 'كروب مراقبة')

            conn = db_connect()
            cursor = conn.cursor()
            cursor.execute('SELECT id, target_chat_id, monitor_link, hash_str, username, group_name FROM pending_joins')
            rows = cursor.fetchall()
            conn.close()

            for row in rows:
                row_id, target_chat_id, monitor_link, hash_str, pending_username, pending_name = row
                matched = False
                if pending_username and chat_username and pending_username.lower() == chat_username.lower():
                    matched = True
                elif hash_str:
                    try:
                        res = await cl(CheckChatInviteRequest(hash_str))
                        res_chat = getattr(res, 'chat', None)
                        if res_chat and getattr(res_chat, 'id', None) == chat_obj_id:
                            matched = True
                    except Exception:
                        pass
                if matched:
                    conn2 = db_connect()
                    cur2 = conn2.cursor()
                    cur2.execute('DELETE FROM pending_joins WHERE id = ?', (row_id,))
                    conn2.commit()
                    conn2.close()
                    await _activate_monitoring(target_chat_id, monitor_link, chat, pending_name or group_name)
                    break
        except FloodWaitError as e:
            logging.warning(f"FloodWait on_assistant_added: {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"on_assistant_added error: {e}")

    @cl.on(events.NewMessage(incoming=True))
    async def monitor_groups(event):
        if not (event.is_group or event.is_channel):
            return

        # 🆕 تعديل 5: حماية من chat_id أو chat فارغين (سبب شائع لتعليق الجلسة)
        if event.chat_id is None or event.chat is None:
            return

        try:
            sender = await event.get_sender()
        except FloodWaitError as e:
            logging.warning(f"FloodWait get_sender: {e.seconds}s")
            await asyncio.sleep(e.seconds)
            return
        except Exception:
            return

        if not sender or not getattr(sender, 'username', None) or getattr(sender, 'bot', False):
            return

        username = sender.username
        profile_link = f"https://t.me/{username}"

        cache_key = username.lower()
        if cache_key in sent_cache:
            return

        conn = db_connect()
        cursor = conn.cursor()
        try:
            chat_id_val = event.chat_id
            try:
                peer_id_val = utils.get_peer_id(event.chat)
            except Exception:
                peer_id_val = chat_id_val

            possible_ids = [chat_id_val, -chat_id_val, peer_id_val, -peer_id_val]
            if chat_id_val > 0:
                possible_ids.append(-int(f"-100{chat_id_val}" if not str(chat_id_val).startswith("100") else chat_id_val))

            placeholders = ','.join(['?'] * len(set(possible_ids)))
            cursor.execute(f'SELECT target_chat_id FROM group_monitoring WHERE monitor_chat_id IN ({placeholders})', tuple(set(possible_ids)))
            rows = cursor.fetchall()

            for row in rows:
                target_chat_id = row[0]

                cursor.execute('SELECT photo_only, vip_only, is_paused FROM settings WHERE target_chat_id = ?', (target_chat_id,))
                sett = cursor.fetchone()
                photo_only = sett[0] if sett else 0
                vip_only = sett[1] if sett and len(sett) > 1 else 0
                is_paused = sett[2] if sett and len(sett) > 2 else 0

                if is_paused == 1:
                    continue

                if photo_only == 1 and not getattr(sender, 'photo', None):
                    continue
                if vip_only == 1 and not is_vip_username(username):
                    continue

                cursor.execute('SELECT 1 FROM sent_usernames WHERE username = ? AND target_chat_id = ? LIMIT 1', (username, target_chat_id))
                if cursor.fetchone():
                    continue

                try:
                    sent_cache.append(cache_key)   # 🆕 تعديل 6: append بدل add
                    await bot.send_message(chat_id=target_chat_id, text=profile_link)
                    cursor.execute('INSERT OR IGNORE INTO sent_usernames (username, target_chat_id) VALUES (?, ?)', (username, target_chat_id))
                    conn.commit()
                except Exception as e:
                    logging.error(f"Error sending username: {e}")
        except FloodWaitError as e:
            logging.warning(f"FloodWait monitor_groups: {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"monitor_groups error: {e}")
        finally:
            conn.close()


@dp.message(BotStates.waiting_for_monitor_link)
async def join_and_save_monitor_group(message: types.Message, state: FSMContext):
    link = message.text.strip()
    target_chat_id = message.chat.id
    await message.reply("⏳ **جاري فحص الرابط وتفعيل المراقبة...**")

    try:
        entity = None
        group_name = "كروب تليجرام"
        hash_str = None
        username_part = None
        join_requested = False

        if '+' in link or 'joinchat' in link:
            try:
                parts = link.split('/')
                for p in parts:
                    if '+' in p:
                        hash_str = p.replace('+', '').split('?')[0]
                        break
                if not hash_str:
                    hash_str = link.split('+')[-1].split('/')[0].split('?')[0]

                for cl in clients:
                    try:
                        updates = await cl(ImportChatInviteRequest(hash_str))
                        if updates.chats:
                            entity = updates.chats[0]
                            group_name = getattr(entity, 'title', 'كروب مراقبة')
                            break
                    except UserAlreadyParticipantError:
                        res = await cl(CheckChatInviteRequest(hash_str))
                        chat_obj = getattr(res, 'chat', None)
                        if chat_obj:
                            entity = chat_obj
                            group_name = getattr(entity, 'title', 'كروب مراقبة')
                            break
                    except InviteRequestSentError:
                        join_requested = True
                        break
                    except FloodWaitError as e:
                        logging.warning(f"FloodWait join private: {e.seconds}s")
                        await asyncio.sleep(e.seconds)
                        continue
                    except Exception:
                        continue
            except Exception as e:
                logging.error(f"Private link error: {e}")
        else:
            if 't.me/' in link:
                username_part = link.split('t.me/')[-1].replace('@', '').split('/')[0].split('?')[0]
            else:
                username_part = link.replace('@', '').strip()

            group_name = f"@{username_part}"
            for cl in clients:
                try:
                    await cl(JoinChannelRequest(username_part))
                    entity = await cl.get_entity(username_part)
                    group_name = getattr(entity, 'title', group_name)
                    break
                except UserAlreadyParticipantError:
                    entity = await cl.get_entity(username_part)
                    group_name = getattr(entity, 'title', group_name)
                    break
                except InviteRequestSentError:
                    join_requested = True
                    break
                except FloodWaitError as e:
                    logging.warning(f"FloodWait join public: {e.seconds}s")
                    await asyncio.sleep(e.seconds)
                    continue
                except Exception:
                    continue

        if join_requested:
            conn = db_connect()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM pending_joins WHERE target_chat_id = ? AND monitor_link = ?',
                (target_chat_id, link)
            )
            if not cursor.fetchone():
                cursor.execute(
                    'INSERT INTO pending_joins (target_chat_id, monitor_link, group_name, hash_str, username) VALUES (?, ?, ?, ?, ?)',
                    (target_chat_id, link, group_name, hash_str, username_part)
                )
                conn.commit()
            conn.close()

            keyboard_stop = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="إيقاف المراقبة", callback_data="stop_monitoring", text_parse_mode="html", icon_custom_emoji_id="5251340630306612580")]]
            )
            await message.reply(
                f"📨 **تم إرسال طلب انضمام للحساب المساعد إلى ({group_name})**\n\n"
                f"⏳ سيبدأ الخمط تلقائياً بمجرد موافقة إدارة الكروب.",
                reply_markup=keyboard_stop
            )
            await state.clear()
            return

        if entity is None:
            await message.reply("⚠️ لم يتم التعرف على الكروب. تأكد أن الرابط صحيح.")
            await state.clear()
            return

        monitor_chat_id = utils.get_peer_id(entity)

        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO group_monitoring (target_chat_id, monitor_link, monitor_chat_id, group_name) VALUES (?, ?, ?, ?)',
            (target_chat_id, link, monitor_chat_id, group_name)
        )
        conn.commit()
        conn.close()

        keyboard_stop = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="إيقاف المراقبة", callback_data="stop_monitoring", text_parse_mode="html", icon_custom_emoji_id="5251340630306612580")]]
        )
        await message.reply(f"🔥 **تم تفعيل المراقبة بنجاح لـ ({group_name}) وبدأ الخمط فوراً!**", reply_markup=keyboard_stop)
        await state.clear()

    except Exception as e:
        logging.error(f"Monitor error: {e}")
        await message.reply(f"⚠️ تعذر تفعيل المراقبة بسبب خطأ تقني: {str(e)[:150]}")
        await state.clear()


@dp.callback_query(lambda c: c.data == 'back_to_main')
async def back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.clear()
    await callback_query.message.edit_text(
        text=f"⚡ **لوحة تحكم بوت اليوزرات الاحترافي**\n\nالمطور: {DEV_NAME} (@{DEV_USERNAME})",
        reply_markup=main_menu_keyboard(callback_query.message.chat.id)
    )


@dp.callback_query(lambda c: c.data == 'back_to_main_private')
async def back_to_main_private(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.clear()
    bot_info = await bot.get_me()
    keyboard_rows = [[InlineKeyboardButton(text="اضف البوت الى مقرك وبلش خمط", url=f"https://t.me/{bot_info.username}?startgroup=true", icon_custom_emoji_id="5776012417846745906")]]
    if callback_query.from_user.username and callback_query.from_user.username.lower() == DEV_USERNAME.lower():
        keyboard_rows.append([InlineKeyboardButton(text="🛠️ إدارة الحسابات المساعدة (خاص بالمطور)", callback_data="dev_panel")])
    
    await callback_query.message.edit_text(
        text=f"أهلاً بك يا عزيزي في بوت يوزرات الاحترافي.\n\nالمطور: {DEV_NAME} (@{DEV_USERNAME})",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    )


async def main():
    all_sessions = SESSION_FILES.copy()
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute('SELECT session_name FROM assistant_accounts')
    for (s_name,) in cursor.fetchall():
        if s_name not in all_sessions:
            all_sessions.append(s_name)
    conn.close()

    for s_file in all_sessions:
        try:
            cl = TelegramClient(s_file, API_ID, API_HASH)
            # 🆕 تعديل 7: مهلة 30 ثانية لمنع تعليق البوت إذا كانت الجلسة تحتاج تحقق
            await asyncio.wait_for(cl.start(), timeout=30)
            clients.append(cl)
            await register_assistant_events(cl)
            logging.info(f"Successfully started assistant session: {s_file}")
        except asyncio.TimeoutError:
            logging.error(f"⏱️ Session {s_file} timeout - requires manual login. Skipping.")
        except Exception as e:
            logging.error(f"Failed to start session {s_file}: {e}")

    asyncio.create_task(poll_pending_joins())

    # 🆕 تعديل 4: إعادة تشغيل تلقائية عند أي انهيار في polling
    while True:
        try:
            logging.info("🚀 Starting polling...")
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"💥 Polling crashed: {e}")
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())