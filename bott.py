import os
import html
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from turso_db import db as _turso_db

# =========================================================
# ⚙️ تنظیمات اصلی
# =========================================================

# توکن ربات از Environment Variable خوانده می‌شود (روی Render تنظیمش کن).
# برای تست لوکال هم می‌توانی موقتاً همان‌جا مقدار بدهی.
TOKEN = os.environ.get("BOT_TOKEN", "")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "8988535531"))

CHANNEL_USERNAME = "@Linkdouni_Rayegan"
CHANNEL_LINK = "https://t.me/Linkdouni_Rayegan"
SUPPORT_USERNAME = "@LOREN_nothin"

# پاداش دعوت
REFERRAL_REWARD_5 = 5
REFERRAL_REWARD_10 = 10
REFERRAL_REWARD_15 = 15

# =========================================================
# 💾 دیتابیس
# =========================================================

def db():
    return _turso_db()


def init_database():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            joined_at TEXT NOT NULL,
            referred_by INTEGER,
            referral_count INTEGER NOT NULL DEFAULT 0,
            points INTEGER NOT NULL DEFAULT 0,
            referral_reward_5 INTEGER NOT NULL DEFAULT 0,
            referral_reward_10 INTEGER NOT NULL DEFAULT 0,
            referral_reward_15 INTEGER NOT NULL DEFAULT 0,
            is_blocked INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id INTEGER NOT NULL,
            invited_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            qualified INTEGER NOT NULL DEFAULT 0,
            qualified_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ad_type TEXT NOT NULL,
            name TEXT NOT NULL,
            link TEXT NOT NULL,
            description TEXT,
            category TEXT,
            photo_id TEXT,
            members TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            published_message_id INTEGER,
            published_at TEXT,
            duration_days INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            pin_requested INTEGER NOT NULL DEFAULT 0
        )
    """)

    # مهاجرت دیتابیس‌های قدیمی (اگر ستونی از قبل روی Turso وجود داشته باشد، نادیده گرفته می‌شود)
    try:
        cur.execute("PRAGMA table_info(ads)")
        columns = {row[1] for row in cur.fetchall()}

        migrations = {
            "published_message_id": "ALTER TABLE ads ADD COLUMN published_message_id INTEGER",
            "published_at": "ALTER TABLE ads ADD COLUMN published_at TEXT",
            "duration_days": "ALTER TABLE ads ADD COLUMN duration_days INTEGER NOT NULL DEFAULT 0",
            "expires_at": "ALTER TABLE ads ADD COLUMN expires_at TEXT",
            "pin_requested": "ALTER TABLE ads ADD COLUMN pin_requested INTEGER NOT NULL DEFAULT 0",
        }

        for col, sql in migrations.items():
            if col not in columns:
                cur.execute(sql)
    except Exception as e:
        print(f"⚠️ بررسی مهاجرت ستون‌ها انجام نشد (احتمالاً مشکلی نیست): {e}")

    conn.commit()
    conn.close()


def register_user(user, referred_by=None):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    exists = cur.fetchone()

    if not exists:
        cur.execute("""
            INSERT INTO users (
                user_id, first_name, username, joined_at, referred_by
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user.id,
            user.first_name or "",
            user.username or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            referred_by
        ))

        if referred_by and referred_by != user.id:
            cur.execute("""
                INSERT OR IGNORE INTO referrals (
                    inviter_id, invited_id, created_at
                )
                VALUES (?, ?, ?)
            """, (
                referred_by,
                user.id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

    else:
        cur.execute("""
            UPDATE users
            SET first_name=?, username=?
            WHERE user_id=?
        """, (
            user.first_name or "",
            user.username or "",
            user.id
        ))

    conn.commit()
    conn.close()


def save_ad(user_id, ad_data):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO ads (
            user_id, ad_type, name, link, description, category,
            photo_id, members, status, created_at, duration_days
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (
        user_id,
        ad_data.get("ad_type", ""),
        ad_data.get("ad_name", ""),
        ad_data.get("ad_link", ""),
        ad_data.get("description", ""),
        ad_data.get("category", ""),
        ad_data.get("photo"),
        ad_data.get("members"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ad_data.get("duration_days", 0)
    ))

    ad_id = cur.lastrowid
    conn.commit()
    conn.close()
    return ad_id


# =========================================================
# 🧰 ابزارها
# =========================================================

def esc(value):
    return html.escape(str(value or ""))


async def safe_edit(query, context, text, reply_markup=None, parse_mode="HTML"):
    """
    مثل query.edit_message_text عمل می‌کند، اما اگر پیام فعلی عکس باشد
    (که نمی‌شود متنش را ویرایش کرد) پیام قبلی را حذف و پیام جدید متنی
    ارسال می‌کند. همیشه از این به‌جای query.edit_message_text مستقیم
    استفاده کن، چون پیش‌نمایش تبلیغ و صفحه بررسی ادمین ممکن است عکس داشته باشند.
    """
    if query.message and query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return

    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )


def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("📢 ثبت تبلیغ", callback_data="register_ad"),
            InlineKeyboardButton("👤 پنل کاربری", callback_data="profile"),
        ],
        [
            InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="ranking"),
            InlineKeyboardButton("🎁 دعوت و پاداش", callback_data="referral"),
        ],
        [
            InlineKeyboardButton("📜 قوانین", callback_data="rules"),
            InlineKeyboardButton("💬 پشتیبانی", callback_data="support"),
        ],
        [
            InlineKeyboardButton("📣 لینکدونی رایگان", url=CHANNEL_LINK),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ آگهی‌های در انتظار", callback_data="admin_pending")],
        [
            InlineKeyboardButton("📊 آمار کامل", callback_data="admin_stats"),
            InlineKeyboardButton("👥 کاربران", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("📢 تبلیغات منتشرشده", callback_data="admin_published"),
            InlineKeyboardButton("📌 راهنمای کانال", callback_data="admin_channel"),
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو", callback_data="cancel_ad")]
    ])


def category_keyboard():
    categories = [
        ("🎮 گیم", "cat_gaming"),
        ("💻 تکنولوژی", "cat_tech"),
        ("📚 آموزشی", "cat_education"),
        ("🎬 فیلم و سریال", "cat_movie"),
        ("🎵 موسیقی", "cat_music"),
        ("😂 سرگرمی", "cat_fun"),
        ("📰 اخبار", "cat_news"),
        ("💼 کسب‌وکار", "cat_business"),
        ("⚽ ورزشی", "cat_sport"),
        ("💬 عمومی", "cat_general"),
        ("➕ سایر", "cat_other"),
    ]

    keyboard = []
    for i in range(0, len(categories), 2):
        row = [InlineKeyboardButton(categories[i][0], callback_data=categories[i][1])]
        if i + 1 < len(categories):
            row.append(
                InlineKeyboardButton(categories[i + 1][0], callback_data=categories[i + 1][1])
            )
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)


def type_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 کانال", callback_data="type_channel"),
            InlineKeyboardButton("👥 گروه", callback_data="type_group"),
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
    ])


def duration_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🕐 ۷ روز", callback_data="dur_7"),
            InlineKeyboardButton("📅 ۳۰ روز", callback_data="dur_30"),
        ],
        [InlineKeyboardButton("♾️ دائمی", callback_data="dur_perm")],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel_ad")],
    ])


def duration_label(days):
    if days == 7:
        return "🕐 ۷ روز"
    if days == 30:
        return "📅 ۳۰ روز"
    return "♾️ دائمی"


def is_valid_telegram_link(text):
    pattern = r"^https?://(t\.me|telegram\.me)/[A-Za-z0-9_+/\-?=%.]+$"
    return bool(re.match(pattern, text.strip()))


def safe_text(value, limit=3500):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit - 3] + "..."


# =========================================================
# 👤 سیستم دعوت
# =========================================================

async def check_channel_member(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except (BadRequest, Forbidden):
        return False
    except Exception:
        return False


async def qualify_referral(bot, invited_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT inviter_id, qualified
        FROM referrals
        WHERE invited_id=?
    """, (invited_id,))
    row = cur.fetchone()

    if not row or row["qualified"]:
        conn.close()
        return

    inviter_id = row["inviter_id"]

    if not await check_channel_member(bot, invited_id):
        conn.close()
        return

    cur.execute("""
        UPDATE referrals
        SET qualified=1, qualified_at=?
        WHERE invited_id=?
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        invited_id
    ))

    cur.execute("""
        UPDATE users
        SET referral_count=referral_count+1,
            points=points+1
        WHERE user_id=?
    """, (inviter_id,))

    conn.commit()
    conn.close()

    # بررسی پاداش‌ها
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT referral_count, referral_reward_5,
               referral_reward_10, referral_reward_15
        FROM users
        WHERE user_id=?
    """, (inviter_id,))
    u = cur.fetchone()

    rewards = []
    if u:
        if u["referral_count"] >= 5 and not u["referral_reward_5"]:
            cur.execute(
                "UPDATE users SET referral_reward_5=1 WHERE user_id=?",
                (inviter_id,)
            )
            rewards.append("🎁 پاداش ۵ دعوت واقعی فعال شد.")

        if u["referral_count"] >= 10 and not u["referral_reward_10"]:
            cur.execute(
                "UPDATE users SET referral_reward_10=1 WHERE user_id=?",
                (inviter_id,)
            )
            rewards.append("🔥 پاداش ۱۰ دعوت واقعی فعال شد.")

        if u["referral_count"] >= 15 and not u["referral_reward_15"]:
            cur.execute(
                "UPDATE users SET referral_reward_15=1 WHERE user_id=?",
                (inviter_id,)
            )
            rewards.append("🚀 پاداش ۱۵ دعوت واقعی فعال شد.")

    conn.commit()
    conn.close()

    if rewards:
        try:
            await bot.send_message(
                inviter_id,
                "🏆 <b>تبریک!</b>\n\n" + "\n".join(rewards),
                parse_mode="HTML"
            )
        except Exception:
            pass


# =========================================================
# 🏠 /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    user = update.effective_user
    referred_by = None

    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referred_by = int(arg[4:])
            except ValueError:
                referred_by = None

    register_user(user, referred_by)

    await update.message.reply_text(
        "🤖 <b>Mr Beni Link</b>\n\n"
        "سلام! 👋\n"
        "به ربات لینکدونی رایگان خوش اومدی 💙\n\n"
        "📢 ثبت تبلیغ کانال و گروه\n"
        "🎁 دعوت و پاداش\n"
        "🏆 رتبه‌بندی کاربران\n"
        "👤 پنل کاربری\n\n"
        "👇 از منوی زیر انتخاب کن:",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )


# =========================================================
# 👑 /admin
# =========================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ شما دسترسی به پنل مدیریت ندارید.")
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c FROM users")
    users = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM ads")
    total_ads = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM ads WHERE status='pending'")
    pending = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM ads WHERE status='approved'")
    approved = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM ads WHERE status='rejected'")
    rejected = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM ads WHERE published_message_id IS NOT NULL")
    published = cur.fetchone()["c"]

    conn.close()

    await update.message.reply_text(
        "👑 <b>پنل مدیریت Mr Beni Link</b>\n\n"
        f"👥 کاربران: <b>{users}</b>\n"
        f"📢 کل تبلیغات: <b>{total_ads}</b>\n"
        f"⏳ در انتظار بررسی: <b>{pending}</b>\n"
        f"✅ تأییدشده: <b>{approved}</b>\n"
        f"❌ ردشده: <b>{rejected}</b>\n"
        f"📣 منتشرشده: <b>{published}</b>\n\n"
        "👇 بخش موردنظر را انتخاب کن:",
        reply_markup=admin_menu(),
        parse_mode="HTML"
    )


# =========================================================
# 📢 ثبت تبلیغ
# =========================================================

async def register_ad_menu(query):
    await query.edit_message_text(
        "📢 <b>ثبت تبلیغ جدید</b>\n\n"
        "نوع تبلیغت رو انتخاب کن:",
        reply_markup=type_keyboard(),
        parse_mode="HTML"
    )


async def send_preview(bot, chat_id, context):
    data = context.user_data

    preview = (
        "👀 <b>پیش‌نمایش تبلیغ</b>\n\n"
        f"📌 نوع: <b>{esc(data.get('ad_type'))}</b>\n"
        f"📝 نام: <b>{esc(data.get('ad_name'))}</b>\n"
        f"🔗 لینک: {esc(data.get('ad_link'))}\n"
        f"📄 توضیحات: {esc(data.get('description') or 'ندارد')}\n"
        f"🏷️ دسته‌بندی: {esc(data.get('category'))}\n"
        f"👥 اعضا: {esc(data.get('members') or 'ثبت نشده')}\n"
        f"🖼️ عکس: {'دارد' if data.get('photo') else 'ندارد'}\n"
        f"⏳ مدت نمایش: <b>{esc(duration_label(data.get('duration_days', 0)))}</b>\n\n"
        "اگر همه‌چیز درسته، تأیید کن."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأیید", callback_data="confirm_ad"),
            InlineKeyboardButton("✏️ ویرایش", callback_data="edit_ad"),
        ],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel_ad")]
    ])

    if data.get("photo"):
        await bot.send_photo(
            chat_id=chat_id,
            photo=data["photo"],
            caption=safe_text(preview, 1000),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=safe_text(preview),
            reply_markup=keyboard,
            parse_mode="HTML"
        )


# =========================================================
# 👑 لیست تبلیغات ادمین
# =========================================================

async def show_pending_ads(query):
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, ad_type, category, created_at
        FROM ads
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT 20
    """)
    ads = cur.fetchall()
    conn.close()

    if not ads:
        await query.edit_message_text(
            "⏳ <b>آگهی‌های در انتظار</b>\n\n"
            "🎉 فعلاً آگهی‌ای برای بررسی وجود ندارد.",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
        return

    keyboard = []
    for ad in ads:
        keyboard.append([
            InlineKeyboardButton(
                f"📢 #{ad['id']} | {safe_text(ad['name'], 24)}",
                callback_data=f"admin_ad_{ad['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔄 تازه‌سازی", callback_data="admin_pending"),
        InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin_back"),
    ])

    await query.edit_message_text(
        "⏳ <b>آگهی‌های در انتظار بررسی</b>\n\n"
        "برای بررسی روی آگهی بزن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def show_admin_ad(query, context, ad_id):
    if query.from_user.id != ADMIN_ID:
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ads WHERE id=?", (ad_id,))
    ad = cur.fetchone()
    conn.close()

    if not ad:
        await query.edit_message_text(
            "❌ آگهی پیدا نشد.",
            reply_markup=admin_menu()
        )
        return

    text = (
        "👑 <b>بررسی تبلیغ</b>\n\n"
        f"🆔 شماره: <code>#{ad['id']}</code>\n"
        f"👤 User ID: <code>{ad['user_id']}</code>\n"
        f"📌 نوع: {esc(ad['ad_type'])}\n"
        f"📝 نام: {esc(ad['name'])}\n"
        f"🔗 لینک: {esc(ad['link'])}\n"
        f"📄 توضیحات: {esc(ad['description'] or 'ندارد')}\n"
        f"🏷️ دسته‌بندی: {esc(ad['category'] or 'ندارد')}\n"
        f"👥 اعضا: {esc(ad['members'] or 'ثبت نشده')}\n"
        f"🖼️ عکس: {'دارد' if ad['photo_id'] else 'ندارد'}\n"
        f"📅 تاریخ ثبت: {esc(ad['created_at'])}\n"
        f"📊 وضعیت: {esc(ad['status'])}\n\n"
        "⚠️ قبل از تأیید، لینک و محتوای تبلیغ را بررسی کن."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأیید و انتشار", callback_data=f"approve_{ad_id}"),
            InlineKeyboardButton("📌 تأیید + پین", callback_data=f"approvepin_{ad_id}"),
        ],
        [
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{ad_id}"),
        ],
        [
            InlineKeyboardButton("🔙 لیست آگهی‌ها", callback_data="admin_pending")
        ]
    ])

    if ad["photo_id"]:
        try:
            await query.message.delete()
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=ad["photo_id"],
                caption=safe_text(text, 1000),
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return
        except Exception:
            pass

    await query.edit_message_text(
        safe_text(text),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# 📣 انتشار خودکار در کانال
# =========================================================

async def publish_ad(bot, ad_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ads WHERE id=?", (ad_id,))
    ad = cur.fetchone()

    if not ad:
        conn.close()
        return False, "آگهی پیدا نشد."

    if ad["published_message_id"]:
        conn.close()
        return True, "این آگهی قبلاً منتشر شده."

    # فقط تبلیغ تأییدشده منتشر شود.
    if ad["status"] != "approved":
        conn.close()
        return False, "وضعیت آگهی approved نیست."

    title = esc(ad["name"])
    desc = esc(ad["description"] or "")
    category = esc(ad["category"] or "")
    members = esc(ad["members"] or "")

    caption = (
        f"📢 <b>{title}</b>\n\n"
        f"{desc}\n\n"
        f"🏷️ {category}"
    )

    if members and members != "ثبت نشده":
        caption += f"\n👥 اعضا: {members}"

    caption += "\n\n🔗 برای مشاهده، روی دکمه زیر بزن."

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 مشاهده کانال / گروه", url=ad["link"])]
    ])

    try:
        if ad["photo_id"]:
            message = await bot.send_photo(
                chat_id=CHANNEL_USERNAME,
                photo=ad["photo_id"],
                caption=safe_text(caption, 1000),
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            message = await bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=safe_text(caption, 4000),
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=False
            )

        now_dt = datetime.now()
        now = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        if ad["duration_days"] and ad["duration_days"] > 0:
            expires_at = (now_dt + timedelta(days=ad["duration_days"])).strftime("%Y-%m-%d %H:%M:%S")
        else:
            expires_at = None  # دائمی

        cur.execute("""
            UPDATE ads
            SET published_message_id=?, published_at=?, expires_at=?
            WHERE id=?
        """, (message.message_id, now, expires_at, ad_id))

        conn.commit()
        conn.close()

        # اگر ادمین درخواست پین کرده باشد
        if ad["pin_requested"]:
            try:
                await bot.pin_chat_message(
                    chat_id=CHANNEL_USERNAME,
                    message_id=message.message_id,
                    disable_notification=True
                )
            except Exception:
                pass

        return True, message.message_id

    except Exception as e:
        conn.close()
        return False, str(e)


# =========================================================
# 🎛️ مدیریت Callbackها
# =========================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    # ثبت تبلیغ
    if data == "register_ad":
        await register_ad_menu(query)
        return

    # نوع کانال
    if data == "type_channel":
        context.user_data.clear()
        context.user_data["ad_type"] = "کانال"
        context.user_data["step"] = "name"

        await query.edit_message_text(
            "📢 <b>ثبت کانال</b>\n\n"
            "نام کانال رو بفرست:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # نوع گروه
    if data == "type_group":
        context.user_data.clear()
        context.user_data["ad_type"] = "گروه"
        context.user_data["step"] = "name"

        await query.edit_message_text(
            "👥 <b>ثبت گروه</b>\n\n"
            "نام گروه رو بفرست:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # ادامه بعد از لینک
    if data == "continue_ad":
        context.user_data["step"] = "description"
        await query.edit_message_text(
            "📄 <b>توضیحات</b>\n\n"
            "یک توضیح کوتاه بفرست.\n"
            "این بخش اختیاری است؛ اگر توضیح نداری بنویس «ندارد».",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # دسته‌بندی
    if data.startswith("cat_"):
        names = {
            "cat_gaming": "🎮 گیم",
            "cat_tech": "💻 تکنولوژی",
            "cat_education": "📚 آموزشی",
            "cat_movie": "🎬 فیلم و سریال",
            "cat_music": "🎵 موسیقی",
            "cat_fun": "😂 سرگرمی",
            "cat_news": "📰 اخبار",
            "cat_business": "💼 کسب‌وکار",
            "cat_sport": "⚽ ورزشی",
            "cat_general": "💬 عمومی",
            "cat_other": "➕ سایر",
        }

        context.user_data["category"] = names.get(data, "➕ سایر")
        context.user_data["step"] = "photo"

        await query.edit_message_text(
            "🖼️ <b>عکس یا لوگو</b>\n\n"
            "اگر عکس داری همینجا بفرست.\n"
            "یا برای ادامه بدون عکس، دکمه زیر را بزن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ بدون عکس", callback_data="no_photo")],
                [InlineKeyboardButton("❌ لغو", callback_data="cancel_ad")]
            ]),
            parse_mode="HTML"
        )
        return

    # بدون عکس
    if data == "no_photo":
        context.user_data["photo"] = None
        context.user_data["step"] = "members"

        await query.edit_message_text(
            "👥 <b>تعداد اعضا</b>\n\n"
            "تعداد تقریبی اعضا را بفرست.\n"
            "اگر نمی‌خواهی وارد کنی، بنویس «ندارد».",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # انتخاب مدت نمایش
    if data.startswith("dur_"):
        mapping = {"dur_7": 7, "dur_30": 30, "dur_perm": 0}
        context.user_data["duration_days"] = mapping.get(data, 0)

        try:
            await query.message.delete()
        except Exception:
            pass

        await send_preview(context.bot, query.message.chat_id, context)
        return

    # تأیید نهایی تبلیغ
    if data == "confirm_ad":
        try:
            ad_id = save_ad(user_id, context.user_data)
        except Exception as e:
            await safe_edit(
                query, context,
                f"❌ خطا هنگام ذخیره تبلیغ:\n<code>{esc(e)}</code>",
                reply_markup=main_menu(),
                parse_mode="HTML"
            )
            return

        ad_name_for_admin = context.user_data.get("ad_name", "")
        context.user_data.clear()

        await safe_edit(
            query, context,
            "🎉 <b>تبلیغ با موفقیت ثبت شد!</b>\n\n"
            f"🆔 شماره تبلیغ: <code>#{ad_id}</code>\n"
            "⏳ وضعیت: در انتظار بررسی مدیر\n\n"
            "بعد از تأیید مدیر، تبلیغ به‌صورت خودکار در کانال منتشر می‌شود. 💙",
            reply_markup=main_menu(),
            parse_mode="HTML"
        )

        # اطلاع فوری به ادمین
        try:
            await context.bot.send_message(
                ADMIN_ID,
                "🔔 <b>تبلیغ جدید برای بررسی</b>\n\n"
                f"🆔 <code>#{ad_id}</code>\n"
                f"📢 {esc(ad_name_for_admin)}\n\n"
                "از /admin وارد پنل شو.",
                parse_mode="HTML"
            )
        except Exception:
            pass

        return

    # ویرایش
    if data == "edit_ad":
        context.user_data["step"] = "name"
        await safe_edit(
            query, context,
            "✏️ <b>ویرایش تبلیغ</b>\n\n"
            "نام کانال یا گروه را دوباره بفرست:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # لغو
    if data == "cancel_ad":
        context.user_data.clear()
        await safe_edit(
            query, context,
            "❌ ثبت تبلیغ لغو شد.\n\n"
            "هر وقت خواستی دوباره شروع کن.",
            reply_markup=main_menu()
        )
        return

    # =====================================================
    # 👑 پنل ادمین
    # =====================================================

    if data.startswith("admin_") and user_id != ADMIN_ID:
        return

    if data == "admin_pending":
        await show_pending_ads(query)
        return

    if data == "admin_back":
        await query.edit_message_text(
            "👑 <b>پنل مدیریت Mr Beni Link</b>\n\n"
            "👇 بخش موردنظر را انتخاب کن:",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
        return

    if data == "admin_stats":
        conn = db()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) AS c FROM users")
        users = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ads")
        total = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ads WHERE status='pending'")
        pending = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ads WHERE status='approved'")
        approved = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ads WHERE status='rejected'")
        rejected = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM ads WHERE published_message_id IS NOT NULL")
        published = cur.fetchone()["c"]
        cur.execute("SELECT COALESCE(SUM(referral_count),0) AS c FROM users")
        referrals = cur.fetchone()["c"]

        conn.close()

        await query.edit_message_text(
            "📊 <b>آمار کامل ربات</b>\n\n"
            f"👥 کاربران: <b>{users}</b>\n"
            f"📢 کل تبلیغات: <b>{total}</b>\n"
            f"⏳ در انتظار: <b>{pending}</b>\n"
            f"✅ تأییدشده: <b>{approved}</b>\n"
            f"❌ ردشده: <b>{rejected}</b>\n"
            f"📣 منتشرشده: <b>{published}</b>\n"
            f"🎁 دعوت‌های معتبر: <b>{referrals}</b>",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
        return

    if data == "admin_users":
        conn = db()
        cur = conn.cursor()
        cur.execute("""
            SELECT first_name, username, user_id, referral_count, points
            FROM users
            ORDER BY points DESC, referral_count DESC
            LIMIT 15
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            text = "👥 <b>کاربران</b>\n\nهنوز کاربری ثبت نشده."
        else:
            lines = ["👥 <b>برترین کاربران</b>\n"]
            for i, row in enumerate(rows, 1):
                uname = f"@{row['username']}" if row["username"] else "بدون یوزرنیم"
                lines.append(
                    f"{i}. {esc(row['first_name'])} — {uname}\n"
                    f"   🎁 دعوت: {row['referral_count']} | ⭐ امتیاز: {row['points']}"
                )
            text = "\n".join(lines)

        await query.edit_message_text(
            safe_text(text),
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
        return

    if data == "admin_published":
        conn = db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, published_at
            FROM ads
            WHERE published_message_id IS NOT NULL
            ORDER BY id DESC
            LIMIT 15
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            text = "📢 <b>تبلیغات منتشرشده</b>\n\nهنوز تبلیغی منتشر نشده."
        else:
            lines = ["📢 <b>آخرین تبلیغات منتشرشده</b>\n"]
            for row in rows:
                lines.append(
                    f"• #{row['id']} — {esc(row['name'])}\n"
                    f"  📅 {esc(row['published_at'])}"
                )
            text = "\n".join(lines)

        await query.edit_message_text(
            safe_text(text),
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
        return

    if data == "admin_channel":
        await query.edit_message_text(
            "📌 <b>تنظیمات لازم برای کانال</b>\n\n"
            f"📣 کانال: {CHANNEL_USERNAME}\n"
            "1️⃣ ربات باید داخل کانال ادمین باشد.\n"
            "2️⃣ دسترسی <b>Post Messages</b> روشن باشد.\n"
            "3️⃣ برای قابلیت پین خودکار، دسترسی <b>Pin Messages</b> هم لازم است.\n\n"
            "بعد از تأیید تبلیغ، ربات آن را خودکار در کانال منتشر می‌کند.",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )
        return

    if data.startswith("admin_ad_"):
        try:
            ad_id = int(data.replace("admin_ad_", ""))
        except ValueError:
            return
        await show_admin_ad(query, context, ad_id)
        return

    # =====================================================
    # ✅ تأیید + انتشار خودکار
    # =====================================================

    if data.startswith("approvepin_") or data.startswith("approve_"):
        if user_id != ADMIN_ID:
            return
        want_pin = data.startswith("approvepin_")
        try:
            ad_id = int(data.replace("approvepin_" if want_pin else "approve_", ""))
        except ValueError:
            return

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT user_id, name, status FROM ads WHERE id=?", (ad_id,))
        ad = cur.fetchone()

        if not ad:
            conn.close()
            await query.edit_message_text(
                "❌ آگهی پیدا نشد.",
                reply_markup=admin_menu()
            )
            return

        if ad["status"] == "rejected":
            conn.close()
            await query.edit_message_text(
                "⚠️ این آگهی قبلاً رد شده است.",
                reply_markup=admin_menu()
            )
            return

        cur.execute(
            "UPDATE ads SET status='approved', rejection_reason=NULL, pin_requested=? WHERE id=?",
            (1 if want_pin else 0, ad_id)
        )
        conn.commit()
        conn.close()

        ok, result = await publish_ad(context.bot, ad_id)

        if ok:
            msg = (
                "✅ <b>تأیید و انتشار انجام شد!</b>\n\n"
                f"🆔 تبلیغ: <code>#{ad_id}</code>\n"
                f"📢 نام: {esc(ad['name'])}\n"
                f"📣 پیام کانال: <code>{result}</code>\n\n"
                "🚀 تبلیغ داخل کانال منتشر شد."
            )
        else:
            msg = (
                "⚠️ <b>تبلیغ تأیید شد، اما انتشار انجام نشد.</b>\n\n"
                f"🆔 تبلیغ: <code>#{ad_id}</code>\n"
                f"📢 نام: {esc(ad['name'])}\n\n"
                f"❌ خطا:\n<code>{esc(result)}</code>\n\n"
                "دسترسی ربات به کانال و Post Messages را بررسی کن."
            )

        await safe_edit(
            query, context,
            safe_text(msg),
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )

        try:
            if ok:
                await context.bot.send_message(
                    ad["user_id"],
                    "🎉 <b>تبلیغت تأیید و منتشر شد!</b>\n\n"
                    f"📢 {esc(ad['name'])}\n"
                    f"🆔 شماره تبلیغ: <code>#{ad_id}</code>\n\n"
                    "تبلیغت الان در لینکدونی رایگان قرار گرفته. 💙",
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    ad["user_id"],
                    "✅ تبلیغت توسط مدیر تأیید شد.\n\n"
                    "⚠️ انتشار در کانال با خطا مواجه شده و مدیر در حال بررسی آن است.",
                )
        except Exception:
            pass

        return

    # =====================================================
    # ❌ رد تبلیغ
    # =====================================================

    if data.startswith("reject_"):
        if user_id != ADMIN_ID:
            return
        try:
            ad_id = int(data.replace("reject_", ""))
        except ValueError:
            return

        context.user_data["reject_ad_id"] = ad_id
        context.user_data["step"] = "reject_reason"

        await safe_edit(
            query, context,
            "❌ <b>رد تبلیغ</b>\n\n"
            "دلیل رد شدن تبلیغ را بنویس:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ لغو", callback_data="admin_pending")]
            ]),
            parse_mode="HTML"
        )
        return

    # =====================================================
    # 👤 پنل کاربری
    # =====================================================

    if data == "profile":
        user = query.from_user

        conn = db()
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) AS c FROM ads WHERE user_id=?",
            (user.id,)
        )
        ads_count = cur.fetchone()["c"]

        cur.execute("""
            SELECT COUNT(*) AS c
            FROM ads
            WHERE user_id=? AND status='approved'
        """, (user.id,))
        approved_count = cur.fetchone()["c"]

        cur.execute("""
            SELECT referral_count, points
            FROM users
            WHERE user_id=?
        """, (user.id,))
        u = cur.fetchone()

        conn.close()

        referrals = u["referral_count"] if u else 0
        points = u["points"] if u else 0

        await query.edit_message_text(
            "👤 <b>پنل کاربری</b>\n\n"
            f"👤 نام: {esc(user.first_name)}\n"
            f"🆔 شناسه: <code>{user.id}</code>\n\n"
            f"📢 تبلیغات ثبت‌شده: <b>{ads_count}</b>\n"
            f"✅ تبلیغات تأییدشده: <b>{approved_count}</b>\n"
            f"🎁 دعوت‌های معتبر: <b>{referrals}</b>\n"
            f"⭐ امتیاز: <b>{points}</b>",
            reply_markup=back_button(),
            parse_mode="HTML"
        )
        return

    # =====================================================
    # 🏆 رتبه‌بندی
    # =====================================================

    if data == "ranking":
        conn = db()
        cur = conn.cursor()
        cur.execute("""
            SELECT first_name, username, referral_count, points
            FROM users
            ORDER BY referral_count DESC, points DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            text = "🏆 <b>رتبه‌بندی</b>\n\nهنوز داده‌ای برای رتبه‌بندی وجود ندارد."
        else:
            medals = ["🥇", "🥈", "🥉"]
            lines = ["🏆 <b>رتبه‌بندی کاربران</b>\n"]
            for i, row in enumerate(rows, 1):
                medal = medals[i - 1] if i <= 3 else f"{i}️⃣"
                lines.append(
                    f"{medal} {esc(row['first_name'])} — "
                    f"🎁 {row['referral_count']} دعوت | ⭐ {row['points']} امتیاز"
                )
            text = "\n".join(lines)

        await query.edit_message_text(
            safe_text(text),
            reply_markup=back_button(),
            parse_mode="HTML"
        )
        return

    # =====================================================
    # 🎁 دعوت و پاداش
    # =====================================================

    if data == "referral":
        link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT referral_count FROM users WHERE user_id=?",
            (user_id,)
        )
        row = cur.fetchone()
        conn.close()

        count = row["referral_count"] if row else 0

        await query.edit_message_text(
            "🎁 <b>دعوت و پاداش</b>\n\n"
            f"👥 دعوت‌های معتبر تو: <b>{count}</b>\n\n"
            "🔗 لینک دعوت اختصاصی:\n"
            f"<code>{esc(link)}</code>\n\n"
            "🎁 ۵ دعوت واقعی → تبلیغ رایگان\n"
            "🔥 ۱۰ دعوت واقعی → مزایای بیشتر\n"
            "🚀 ۱۵ دعوت واقعی → تبلیغ ویژه + مزایای بیشتر\n\n"
            "⚠️ دعوت فقط وقتی معتبر می‌شود که کاربر به کانال اصلی بپیوندد و شرایط سیستم را رعایت کند.",
            reply_markup=back_button(),
            parse_mode="HTML"
        )
        return

    # =====================================================
    # 📜 قوانین
    # =====================================================

    if data == "rules":
        await query.edit_message_text(
            "📜 <b>قوانین Mr Beni Link</b>\n\n"
            "1️⃣ فقط تبلیغات مجاز پذیرفته می‌شوند.\n"
            "2️⃣ اعضای فیک و رباتی معتبر محسوب نمی‌شوند.\n"
            "3️⃣ تبلیغات مشکوک ممکن است برای بررسی بیشتر نگه داشته شوند.\n"
            "4️⃣ تبلیغات خلاف قوانین رد خواهند شد.\n"
            "5️⃣ سوءاستفاده از سیستم دعوت می‌تواند باعث حذف امتیاز یا محدودیت کاربر شود.\n"
            "6️⃣ مسئولیت محتوای تبلیغ با ارسال‌کننده است.\n"
            "7️⃣ مدیر حق رد یا حذف تبلیغ را دارد.",
            reply_markup=back_button(),
            parse_mode="HTML"
        )
        return

    # =====================================================
    # 💬 پشتیبانی
    # =====================================================

    if data == "support":
        await query.edit_message_text(
            "💬 <b>پشتیبانی</b>\n\n"
            f"👤 {esc(SUPPORT_USERNAME)}\n\n"
            "برای مشکلات مربوط به تبلیغ، دعوت یا ربات با پشتیبانی در تماس باش.",
            reply_markup=back_button(),
            parse_mode="HTML"
        )
        return

    # بازگشت
    if data == "back_main":
        context.user_data.clear()
        await query.edit_message_text(
            "🤖 <b>Mr Beni Link</b>\n\n"
            "👇 از منوی زیر انتخاب کن:",
            reply_markup=main_menu(),
            parse_mode="HTML"
        )


# =========================================================
# 📝 پیام‌های متنی
# =========================================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    register_user(user)

    step = context.user_data.get("step")
    if not step:
        return

    text = update.message.text.strip()

    # نام
    if step == "name":
        if len(text) < 2:
            await update.message.reply_text("❌ نام خیلی کوتاهه. دوباره بفرست.")
            return

        context.user_data["ad_name"] = text
        context.user_data["step"] = "link"

        await update.message.reply_text(
            "🔗 حالا لینک کانال یا گروه رو بفرست.\n\n"
            "مثال:\n"
            "<code>https://t.me/example</code>\n\n"
            "⚠️ لینک باید عمومی و قابل باز شدن باشد.",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # لینک
    if step == "link":
        if not is_valid_telegram_link(text):
            await update.message.reply_text(
                "❌ لینک معتبر نیست.\n\n"
                "لینک را مثل این بفرست:\n"
                "https://t.me/example",
                reply_markup=cancel_keyboard()
            )
            return

        context.user_data["ad_link"] = text
        context.user_data["step"] = None

        await update.message.reply_text(
            "✅ اطلاعات اولیه ثبت شد!\n\n"
            f"📌 نوع: <b>{esc(context.user_data['ad_type'])}</b>\n"
            f"📝 نام: <b>{esc(context.user_data['ad_name'])}</b>\n"
            f"🔗 لینک: {esc(text)}\n\n"
            "برای ادامه، دکمه زیر را بزن:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➡️ ادامه", callback_data="continue_ad")],
                [InlineKeyboardButton("❌ لغو", callback_data="cancel_ad")]
            ]),
            parse_mode="HTML"
        )
        return

    # توضیحات
    if step == "description":
        if text == "ندارد":
            context.user_data["description"] = ""
        else:
            context.user_data["description"] = text[:1000]

        context.user_data["step"] = "category"

        await update.message.reply_text(
            "🏷️ <b>دسته‌بندی تبلیغ</b>\n\n"
            "دسته‌بندی مناسب را انتخاب کن:",
            reply_markup=category_keyboard(),
            parse_mode="HTML"
        )
        return

    # اعضا
    if step == "members":
        if text == "ندارد":
            context.user_data["members"] = "ثبت نشده"
        elif text.isdigit():
            context.user_data["members"] = text
        else:
            await update.message.reply_text(
                "❌ لطفاً فقط عدد وارد کن.\n"
                "مثلاً: 2500\n\n"
                "یا بنویس: ندارد"
            )
            return

        context.user_data["step"] = None

        await update.message.reply_text(
            "⏳ <b>مدت نمایش تبلیغ</b>\n\n"
            "تبلیغ چه مدت در کانال بماند؟",
            reply_markup=duration_keyboard(),
            parse_mode="HTML"
        )
        return

    # دلیل رد
    if step == "reject_reason":
        if user.id != ADMIN_ID:
            context.user_data.clear()
            return

        ad_id = context.user_data.get("reject_ad_id")
        if not ad_id:
            context.user_data.clear()
            return

        reason = text[:1000]

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, name FROM ads WHERE id=?",
            (ad_id,)
        )
        ad = cur.fetchone()

        if not ad:
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(
                "❌ آگهی پیدا نشد.",
                reply_markup=admin_menu()
            )
            return

        cur.execute("""
            UPDATE ads
            SET status='rejected', rejection_reason=?
            WHERE id=?
        """, (reason, ad_id))

        conn.commit()
        conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "❌ <b>آگهی رد شد.</b>\n\n"
            f"🆔 شماره: <code>#{ad_id}</code>\n"
            f"📢 نام: {esc(ad['name'])}\n"
            f"📝 دلیل: {esc(reason)}",
            reply_markup=admin_menu(),
            parse_mode="HTML"
        )

        try:
            await context.bot.send_message(
                ad["user_id"],
                "❌ <b>تبلیغت رد شد.</b>\n\n"
                f"📢 {esc(ad['name'])}\n"
                f"🆔 شماره تبلیغ: <code>#{ad_id}</code>\n\n"
                f"📝 دلیل رد:\n{esc(reason)}",
                parse_mode="HTML"
            )
        except Exception:
            pass

        return


# =========================================================
# 🖼️ دریافت عکس
# =========================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step") != "photo":
        return

    photo = update.message.photo[-1]
    context.user_data["photo"] = photo.file_id
    context.user_data["step"] = "members"

    await update.message.reply_text(
        "✅ عکس دریافت شد!\n\n"
        "👥 حالا تعداد تقریبی اعضای کانال یا گروه را بفرست.\n\n"
        "اگر نمی‌خواهی وارد کنی، بنویس:\n"
        "<code>ندارد</code>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# 🛠️ دستور بررسی دعوت‌ها
# =========================================================

async def check_referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT invited_id
        FROM referrals
        WHERE qualified=0
    """)
    pending = cur.fetchall()
    conn.close()

    checked = 0
    for row in pending:
        await qualify_referral(context.bot, row["invited_id"])
        checked += 1

    await update.message.reply_text(
        f"🔄 بررسی دعوت‌ها انجام شد.\n\n"
        f"👥 موارد بررسی‌شده: {checked}"
    )


# =========================================================
# ⏱️ بررسی دوره‌ای دعوت‌ها (شرط ۲۴ ساعت ماندن)
# =========================================================

async def process_pending_referrals_job(context: ContextTypes.DEFAULT_TYPE):
    """
    این تابع هر چند دقیقه یک‌بار اجرا می‌شود و فقط دعوت‌هایی را بررسی
    می‌کند که حداقل ۲۴ ساعت از ثبتشان گذشته باشد. اگر کاربر دعوت‌شده
    هنوز عضو کانال اصلی باشد، دعوت معتبر می‌شود.
    """
    conn = db()
    cur = conn.cursor()
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT invited_id
        FROM referrals
        WHERE qualified=0 AND created_at <= ?
    """, (cutoff,))
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        await qualify_referral(context.bot, row["invited_id"])


# =========================================================
# ⏳ بررسی دوره‌ای انقضای تبلیغات
# =========================================================

async def process_expired_ads_job(context: ContextTypes.DEFAULT_TYPE):
    """
    این تابع تبلیغاتی را که مدت‌شان تمام شده پیدا می‌کند، در صورت لزوم
    از حالت پین خارج و از کانال حذف می‌کند و وضعیتشان را expired می‌کند.
    تبلیغ‌های دائمی (duration_days=0 و expires_at خالی) هرگز منقضی نمی‌شوند.
    """
    conn = db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT id, user_id, name, published_message_id, pin_requested
        FROM ads
        WHERE status='approved' AND expires_at IS NOT NULL AND expires_at <= ?
    """, (now,))
    rows = cur.fetchall()

    for ad in rows:
        if ad["published_message_id"]:
            if ad["pin_requested"]:
                try:
                    await context.bot.unpin_chat_message(
                        chat_id=CHANNEL_USERNAME,
                        message_id=ad["published_message_id"]
                    )
                except Exception:
                    pass
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_USERNAME,
                    message_id=ad["published_message_id"]
                )
            except Exception:
                pass

        cur.execute("UPDATE ads SET status='expired' WHERE id=?", (ad["id"],))
        conn.commit()

        try:
            await context.bot.send_message(
                ad["user_id"],
                "⏳ <b>مدت نمایش تبلیغت تمام شد.</b>\n\n"
                f"📢 {esc(ad['name'])}\n\n"
                "اگر می‌خواهی دوباره تبلیغ کنی، از منوی ربات ثبت کن.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    conn.close()


# =========================================================
# 🚀 اجرای ربات
# =========================================================

def main():
    init_database()

    if not TOKEN or TOKEN.startswith("توکن_"):
        print("❌ ابتدا TOKEN را در bot.py وارد کن.")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("checkref", check_referrals_command))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(filters.PHOTO, photo_handler)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    if app.job_queue is not None:
        # هر ۱۵ دقیقه دعوت‌هایی که ۲۴ ساعت از ثبتشان گذشته را بررسی کن
        app.job_queue.run_repeating(process_pending_referrals_job, interval=900, first=15)
        # هر ۱۰ دقیقه تبلیغات منقضی‌شده را بررسی و جمع کن
        app.job_queue.run_repeating(process_expired_ads_job, interval=600, first=30)
    else:
        print(
            "⚠️ JobQueue فعال نیست. برای اجرای خودکار شرط ۲۴ ساعته و انقضای تبلیغ،\n"
            "این پکیج را نصب کن: pip install \"python-telegram-bot[job-queue]\""
        )

    print("🤖 Mr Beni Link is running...")
    print(f"📣 Channel: {CHANNEL_USERNAME}")
    print("👑 Admin panel: /admin")

    app.run_polling()


if __name__ == "__main__":
    main()