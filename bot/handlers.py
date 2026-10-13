import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message, ReplyKeyboardRemove

from bot.database import ORDER_RESERVATION_MINUTES, Store
from bot.keyboards import (
    ADMIN_BUTTON_EMOJI,
    CUSTOM_EMOJI_IDS,
    BUTTON_EMOJI_TARGET_LABELS,
    ADMIN_ADD_PRODUCT,
    ADMIN_BACK,
    ADMIN_BROADCAST,
    ADMIN_CANCEL,
    ADMIN_CONTACT_SETTINGS,
    ADMIN_KHQR_PROFILE,
    ADMIN_KHQR_SECRET,
    ADMIN_KHQR_SETTINGS,
    ADMIN_CREATE_DISCOUNT,
    ADMIN_DESCRIPTION_SKIP,
    ADMIN_CREDENTIAL_SKIP,
    ADMIN_DELIVERY_SKIP,
    ADMIN_DELETE_PRODUCT,
    ADMIN_EDIT_CHANNEL,
    ADMIN_EDIT_SUPPORT,
    ADMIN_MANAGE_ACCOUNTS,
    ADMIN_PENDING_ORDERS,
    ADMIN_PHOTOS_DONE,
    ADMIN_PUBLISH,
    ADMIN_SERVICE_PREMIUM,
    ADMIN_SERVICE_STARS,
    ADMIN_TELEGRAM_SERVICES,
    ADMIN_ITEM_ACCOUNT,
    ADMIN_ITEM_PRO,
    ADMIN_ITEM_SERVICE,
    ADMIN_SERVICE_UID,
    ADMIN_SERVICE_TWO_IDS,
    ADMIN_SETTINGS_BACK,
    MENU_ADMIN,
    MENU_CATALOG,
    MENU_CATALOG_EN,
    MENU_CREATE_BOT,
    MENU_CREATE_BOT_EN,
    MENU_TOPUP,
    MENU_TOPUP_EN,
    MENU_CHANNEL,
    MENU_DISCOUNT_CODE,
    MENU_DISCOUNT_CODE_EN,
    MENU_GUIDE,
    MENU_GUIDE_EN,
    MENU_LANGUAGE,
    MENU_LANGUAGE_EN,
    MENU_ORDERS,
    MENU_ORDERS_EN,
    MENU_PROFILE,
    MENU_PROFILE_EN,
    MENU_WALLET,
    MENU_WALLET_EN,
    MENU_SUPPORT,
    MENU_ADMIN_EN,
    LANGUAGE_BACK,
    LANGUAGE_BACK_EN,
    LANGUAGE_EN,
    LANGUAGE_KM,
    admin_hide_confirmation,
    admin_account_actions,
    admin_accounts_keyboard,
    admin_contact_keyboard,
    admin_contact_settings_menu,
    admin_khqr_settings_menu,
    admin_panel_shortcut_keyboard,
    button_emoji_targets_keyboard,
    admin_product_kind_menu,
    admin_service_identity_menu,
    admin_menu,
    admin_settings_input_menu,
    admin_service_kind_menu,
    admin_service_editor,
    admin_products_keyboard,
    button,
    confirm_product,
    credential_skip,
    delivery_skip,
    payment_qr_keyboard,
    description_skip,
    games_keyboard,
    main_menu,
    language_menu,
    pending_order_actions,
    pending_orders_list,
    photo_done,
    payment_method_keyboard,
    product_actions,
    products_keyboard,
    stock_announcement,
    sold_confirmation_keyboard,
    service_options_keyboard,
    topup_games_keyboard,
    service_username_confirmation,
    customer_bot_plans_keyboard,
    customer_bot_setup_keyboard,
    open_botfather_keyboard,
    telegram_user_id_help_keyboard,
    customer_bot_invoice_keyboard,
    wallet_custom_amount_keyboard,
    wallet_topup_keyboard,
    wallet_topup_payment_keyboard,
    welcome_actions,
)
from bot.payments import (
    PAYMENT_CHECK_WINDOW_SECONDS,
    download_direct_qr_image,
    render_payment_card_png,
    request_direct_qr,
    verify_transaction,
)
from bot.text_format import html_label

logger = logging.getLogger(__name__)
user_router = Router(name="user")
admin_router = Router(name="admin")
reported_amount_mismatch_orders: set[int] = set()
ADMIN_CONTACT_USERNAME = "ROVISTARCAMBO"
DEFAULT_CHANNEL_URL = "https://t.me/ROVISTARKH"
sensitive_message_cleanup_tasks: set[asyncio.Task] = set()


class AdminProductForm(StatesGroup):
    game = State()
    kind = State()
    identity_fields = State()
    level = State()
    photos = State()
    name = State()
    description = State()
    price = State()
    email = State()
    password = State()
    delivery = State()
    confirm = State()


class AdminTelegramServiceForm(StatesGroup):
    name = State()
    price = State()


class ServiceUsernameForm(StatesGroup):
    username = State()
    identity_value = State()
    confirm = State()


class CustomerBotBuilderForm(StatesGroup):
    token = State()
    admin_user_id = State()


class AdminCredentialForm(StatesGroup):
    email = State()
    password = State()


class WalletTopupForm(StatesGroup):
    amount = State()


class BroadcastForm(StatesGroup):
    message = State()


class DiscountCodeForm(StatesGroup):
    code = State()
    percent = State()


class AdminContactSettingsForm(StatesGroup):
    support_username = State()
    channel_url = State()


class AdminKhqrSettingsForm(StatesGroup):
    profile_id = State()
    secret_key = State()


class AdminButtonEmojiForm(StatesGroup):
    emoji_id = State()


class RedeemDiscountForm(StatesGroup):
    code = State()


def _is_admin(user_id: int, admin_ids: frozenset[int]) -> bool:
    return user_id in admin_ids


def _price(price_cents: int) -> str:
    return f"${price_cents / 100:.2f}"


def _normalize_discount_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(character for character in normalized if character.isalnum()).upper()


def _normalize_support_username(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    elif candidate.lower().startswith(("https://t.me/", "https://www.t.me/")):
        parsed = urlsplit(candidate)
        if parsed.netloc.casefold() not in {"t.me", "www.t.me"} or parsed.query or parsed.fragment:
            return None
        candidate = parsed.path.strip("/")
    return candidate if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", candidate) else None


def _normalize_channel_url(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", candidate):
        return f"https://t.me/{candidate}"
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() not in {"t.me", "www.t.me"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = parsed.path.strip("/")
    if not re.fullmatch(r"(?:[A-Za-z][A-Za-z0-9_]{4,31}|\+[A-Za-z0-9_-]{5,})", path):
        return None
    return f"https://t.me/{path}"


def _parse_wallet_amount(text: str) -> int | None:
    value = text.strip()
    if value.upper().endswith("USD"):
        value = value[:-3].strip()
    if value.startswith("$"):
        value = value[1:].strip()
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            return None
        cents = int(amount * 100)
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return cents if 1 <= cents <= 100_000 else None


def _cambodia_time(timestamp: str) -> str:
    expires_at = datetime.fromisoformat(timestamp).astimezone(timezone(timedelta(hours=7)))
    return expires_at.strftime("%H:%M:%S")


def _short_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator or len(local) < 3:
        return "***"
    return f"{local[:2]}***@{domain}"


def _html_field(value: str, value_html: str | None) -> str:
    return html_label(value, value_html)


def _custom_emoji(name: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{CUSTOM_EMOJI_IDS[name]}">{fallback}</tg-emoji>'


def _profile_text(full_name: str, username: str | None, user_id: int, language: str = "km") -> str:
    display_name = escape(full_name)
    display_username = f"@{escape(username)}" if username else "មិនបានកំណត់"
    if language == "en":
        display_username = f"@{escape(username)}" if username else "Not set"
        return (
            f"{_custom_emoji('profile_mail', '📬')} <b>Telegram profile</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{_custom_emoji('profile_water', '💦')} Name: <b>{display_name}</b>\n"
            f"{_custom_emoji('profile_wallet', '👛')} Username: <code>{display_username}</code>\n"
            f"{_custom_emoji('profile_money', '💰')} ID: <code>{user_id}</code>\n"
            "━━━━━━━━━━━━━━━━━━"
        )
    return (
        f"{_custom_emoji('profile_mail', '📬')} <b>គណនី Telegram</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{_custom_emoji('profile_water', '💦')} ឈ្មោះ: <b>{display_name}</b>\n"
        f"{_custom_emoji('profile_wallet', '👛')} Username: <code>{display_username}</code>\n"
        f"{_custom_emoji('profile_money', '💰')} ID: <code>{user_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def _welcome_text(
    language: str = "km",
    user_name: str = "ភ្ញៀវ",
    shop_name: str = "ROKIRA",
) -> str:
    display_name = escape(user_name.strip() or "ភ្ញៀវ")
    display_shop_name = escape(shop_name.strip())
    if language == "en":
        welcome_heading = (
            f"Welcome, {display_name}, to {display_shop_name}"
            if display_shop_name
            else f"Welcome, {display_name}"
        )
        welcome_footer = (
            f"Thank you for visiting {display_shop_name}. Check back for new stock."
            if display_shop_name
            else "Thank you for visiting. Check back for new stock."
        )
        return (
            f"<tg-emoji emoji-id=\"{CUSTOM_EMOJI_IDS['welcome']}\">👋</tg-emoji> "
            f"<b>{welcome_heading}</b>\n"
            f"{_custom_emoji('mobile', '📲')} Digital products, games, and Pro services.\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{_custom_emoji('gallery', '🖼')} <b>Products and services</b>\n"
            "├ Free Fire accounts and other gaming products, with details and prices shown before purchase.\n"
            "├ Robux Gift Cards, subject to current stock.\n"
            "└ Pro apps and other digital services, subject to current listings.\n\n"
            f"{_custom_emoji('verified', '☑️')} <b>How to order</b>\n"
            "├ Select Browse products, then choose a category or product.\n"
            "├ Review photos, description, price, and stock status before ordering.\n"
            "└ Choose a payment method and follow the order updates in this chat.\n\n"
            f"{_custom_emoji('mobile', '📲')} <b>Payment protection</b>\n"
            "├ Pay with KHQR or the bot wallet when available.\n"
            "├ KHQR payments are verified against the exact order amount.\n"
            "└ Account details are not sent until payment is verified.\n\n"
            f"{_custom_emoji('wallet_icon', '👛')} <b>Wallet and order history</b>\n"
            "├ Check your wallet balance and available top-up options.\n"
            "├ Review your order history and order status.\n"
            "└ Cancel a pending order using the button in its payment message.\n\n"
            f"{_custom_emoji('add_more', '➕')} <b>Discount codes</b>\n"
            "└ Enter a code provided by the shop before your next purchase.\n\n"
            f"{_custom_emoji('warning', '⚠️')} <b>Stay safe</b>\n"
            "├ Check the product details and payment amount before confirming.\n"
            "└ Never share your OTP or personal password with anyone.\n\n"
            f"{_custom_emoji('cross', '❌')} Cancel pending orders only with the cancellation button.\n"
            f"{_custom_emoji('heart', '❤️')} {welcome_footer}"
        )
    welcome_heading = (
        f"សួស្តី បងប្រុស {display_name} មកកាន់ {display_shop_name}"
        if display_shop_name
        else f"សួស្តី បងប្រុស {display_name}"
    )
    welcome_footer = (
        f"អរគុណច្រើនដែលបានចូលប្រើប្រាស់ {display_shop_name}។ សូមពិនិត្យ stock និងព័ត៌មានថ្មីៗជាប្រចាំ។"
        if display_shop_name
        else "អរគុណច្រើនដែលបានចូលប្រើប្រាស់។ សូមពិនិត្យ stock និងព័ត៌មានថ្មីៗជាប្រចាំ។"
    )
    return (
        f"<tg-emoji emoji-id=\"{CUSTOM_EMOJI_IDS['welcome']}\">👋</tg-emoji> "
        f"<b>{welcome_heading}</b>\n"
        f"{_custom_emoji('mobile', '📲')} ហាងផលិតផលឌីជីថល ហ្គេម និងសេវាកម្ម Pro។\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_custom_emoji('gallery', '🖼')} <b>ផលិតផល និងសេវាកម្ម</b>\n"
        "├ Free Fire account និងទំនិញហ្គេម ដែលបង្ហាញព័ត៌មាន និងតម្លៃមុនបញ្ជាទិញ។\n"
        "├ Robux Gift Card សម្រាប់អ្នកប្រើ Roblox តាមជម្រើស stock ដែលមាន។\n"
        "└ កម្មវិធី Pro និងសេវាកម្មឌីជីថលផ្សេងៗ អាស្រ័យលើទំនិញដែលហាងកំពុងដាក់លក់។\n\n"
        f"{_custom_emoji('verified', '☑️')} <b>របៀបបញ្ជាទិញ</b>\n"
        "├ ចូលទៅ «បញ្ជាទិញទំនិញ» រួចជ្រើសរើស game, ប្រភេទ ឬទំនិញ។\n"
        "├ ពិនិត្យរូបភាព ការពិពណ៌នា តម្លៃ និងស្ថានភាព stock មុនសម្រេចចិត្ត។\n"
        "└ ជ្រើសរើសវិធីបង់ប្រាក់ ហើយតាមដានលទ្ធផលក្នុងការជជែកនេះ។\n\n"
        f"{_custom_emoji('mobile', '📲')} <b>ការបង់ប្រាក់មានសុវត្ថិភាព</b>\n"
        "├ បង់តាម KHQR ឬកាបូបប្រាក់ក្នុង Bot តាមជម្រើសដែលមាន។\n"
        "├ KHQR ត្រូវបានផ្ទៀងផ្ទាត់ចំនួនទឹកប្រាក់មុនសម្គាល់ថាបានបង់។\n"
        "└ ព័ត៌មានចូល account នឹងមិនបង្ហាញមុនពេលការបង់ប្រាក់ត្រូវបានបញ្ជាក់ឡើយ។\n\n"
        f"{_custom_emoji('wallet_icon', '👛')} <b>កាបូប និងប្រវត្តិ</b>\n"
        "├ ពិនិត្យសមតុល្យ និងបញ្ចូលប្រាក់តាមជម្រើសក្នុងម៉ឺនុយ។\n"
        "├ មើលប្រវត្តិបញ្ជាទិញ និងស្ថានភាព order របស់អ្នក។\n"
        "└ Order ដែលកំពុងរង់ចាំ អាចបោះបង់បានតាមប៊ូតុងដែលបង្ហាញ។\n\n"
        f"{_custom_emoji('add_more', '➕')} <b>កូដបញ្ចុះតម្លៃ</b>\n"
        "└ បញ្ចូលកូដដែលហាងផ្តល់ឱ្យ មុនបញ្ជាទិញ ដើម្បីប្រើការបញ្ចុះតម្លៃតាមលក្ខខណ្ឌកូដ។\n\n"
        f"{_custom_emoji('warning', '⚠️')} <b>ចំណាំសុវត្ថិភាព</b>\n"
        "├ ពិនិត្យព័ត៌មានទំនិញ និងចំនួនបង់ឱ្យបានត្រឹមត្រូវ។\n"
        "└ កុំផ្ញើ OTP ឬលេខសម្ងាត់ផ្ទាល់ខ្លួនទៅអ្នកដទៃ។\n\n"
        f"{_custom_emoji('cross', '❌')} ប្រសិនបើចង់បោះបង់ order សូមប្រើប៊ូតុងបោះបង់ក្នុងសារបង់ប្រាក់។\n"
        f"{_custom_emoji('heart', '❤️')} {welcome_footer}"
    )


async def _welcome_for_user(
    store: Store,
    language: str,
    user_name: str,
    *,
    customer_bot_mode: bool = False,
) -> str:
    shop_name = await store.get_setting("shop_name", "") if customer_bot_mode else "ROKIRA"
    return _welcome_text(language, user_name, shop_name)


async def _welcome_actions_for_user(store: Store, language: str):
    support_username = await store.get_setting("support_username", ADMIN_CONTACT_USERNAME)
    channel_url = await store.get_setting("channel_url", DEFAULT_CHANNEL_URL)
    return welcome_actions(language, support_username, channel_url)


async def _send_product_card(
    bot: Bot,
    chat_id: int,
    store: Store,
    product_id: int,
    language: str = "km",
    *,
    customer_bot_mode: bool = False,
) -> None:
    product = await store.available_product(product_id)
    if product is None:
        await bot.send_message(
            chat_id,
            "Sorry, this item has been reserved or sold." if language == "en" else "សូមអភ័យទោស account នេះត្រូវបានកក់ ឬលក់រួចហើយ។",
        )
        return

    available = product["status"] == "available"
    description_block = (
        f"{_html_field(product.get('description', ''), product.get('description_html'))}\n"
        if product.get("description")
        else ""
    )
    if product.get("service_kind"):
        details = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{_custom_emoji('service_crown', '👑')} <b>{'Telegram service' if language == 'en' else 'សេវាកម្ម Telegram'}</b>\n"
            f"├ {'Service' if language == 'en' else 'សេវា'}: <b>{_html_field(product['game_name'], product.get('game_name_html'))}</b>\n"
            f"├ {'Option' if language == 'en' else 'ជម្រើស'}: <b>{_html_field(product['name'], product.get('name_html'))}</b>\n"
            f"├ {'Price' if language == 'en' else 'តម្លៃ'}: <b>{_price(product['price_cents'])}</b>\n"
            f"{description_block}"
            f"{_custom_emoji('service_warning', '⚠️')} "
            + (
                "A Telegram username is required. The admin will process the service after verified payment."
                if language == "en"
                else "ត្រូវការយូសនាម Telegram។ Admin នឹងចាប់ផ្តើមដំណើរការសេវាក្រោយពេលបង់ប្រាក់ត្រូវបានផ្ទៀងផ្ទាត់។"
            )
            + "\n━━━━━━━━━━━━━━━━━━━━"
        )
    elif language == "en":
        details = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{_custom_emoji('product', '📦')} <b>Product details</b>\n"
            f"├ Name: <b>{_html_field(product['name'], product.get('name_html'))}</b>\n"
            f"├ Category: <b>{_html_field(product['game_name'], product.get('game_name_html'))}</b>\n"
            f"├ Type: <b>{_html_field(product['level_name'], product.get('level_name_html'))}</b>\n"
            f"├ Price: <b>{_price(product['price_cents'])}</b>\n"
            f"{description_block}"
            f"└ Status: <b>{'In stock' if available else 'Sold' if product['status'] == 'sold' else 'Reserved'}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Account details are sent only after KHQR payment is verified."
        )
    else:
        details = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{_custom_emoji('product', '📦')} <b>ព័ត៌មានទំនិញ</b>\n"
        f"├ ឈ្មោះ: <b>{_html_field(product['name'], product.get('name_html'))}</b>\n"
        f"├ ប្រភេទ: <b>{_html_field(product['game_name'], product.get('game_name_html'))}</b>\n"
        f"├ គម្រោង: <b>{_html_field(product['level_name'], product.get('level_name_html'))}</b>\n"
        f"├ តម្លៃ: <b>{_price(product['price_cents'])}</b>\n"
        f"{description_block}"
        f"└ ស្ថានភាព: <b>{'មានក្នុង stock' if available else 'បានលក់រួច' if product['status'] == 'sold' else 'កំពុងកក់'}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Gmail និង password នឹងផ្ញើជូនដោយស្វ័យប្រវត្តិ ក្រោយ KHQR បញ្ជាក់ការបង់ប្រាក់។"
    )
    markup = product_actions(
        product["id"],
        product["status"],
        language,
        is_service=bool(product.get("service_kind")),
        show_default_icon=True,
    )
    photo_file_ids = product["photo_file_ids"]
    try:
        if len(photo_file_ids) == 1:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_file_ids[0],
                caption=details,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return

        for offset in range(0, len(photo_file_ids), 10):
            chunk = photo_file_ids[offset : offset + 10]
            if len(chunk) == 1:
                await bot.send_photo(chat_id=chat_id, photo=chunk[0])
            else:
                await bot.send_media_group(
                    chat_id=chat_id,
                    media=[InputMediaPhoto(media=file_id) for file_id in chunk],
                )
    except TelegramBadRequest as error:
        logger.warning("Could not send photos for product %s: %s", product_id, error)
        await bot.send_message(chat_id, details, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    await bot.send_message(chat_id, details, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _broadcast_new_product(bot: Bot, store: Store, product_id: int) -> tuple[int, int]:
    product = await store.available_product(product_id)
    if product is None:
        return 0, 0

    text = (
        f"<tg-emoji emoji-id=\"{CUSTOM_EMOJI_IDS['new_stock']}\">🎉</tg-emoji> "
        "<b>មាន account ថ្មីចូល stock</b>\n"
        f"Game: <b>{_html_field(product['game_name'], product.get('game_name_html'))}</b>\n"
        f"Level: <b>{_html_field(product['level_name'], product.get('level_name_html'))}</b>\n"
        f"ឈ្មោះ: <b>{_html_field(product['name'], product.get('name_html'))}</b>\n"
        f"តម្លៃ: <b>{_price(product['price_cents'])}</b>"
        + (
            f"\n\n{_html_field(product['description'], product.get('description_html'))}"
            if product.get("description")
            else ""
        )
    )
    user_ids = await store.user_ids()
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=product["photo_file_ids"][0],
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=stock_announcement(product_id),
            )
            sent += 1
        except TelegramAPIError as error:
            failed += 1
            logger.info("Stock announcement not delivered to user %s: %s", user_id, error)
        await asyncio.sleep(0.04)
    return sent, failed


async def _notify_admins(
    bot: Bot,
    admin_ids: frozenset[int],
    text: str,
    reply_markup=None,
) -> None:
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        except TelegramAPIError as error:
            logger.warning("Could not notify admin %s: %s", admin_id, error)


@user_router.message(CommandStart())
async def start(
    message: Message,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None:
        return
    await store.register_user(
        message.from_user.id,
        message.from_user.first_name,
        message.from_user.username,
    )
    language = await store.user_language(message.from_user.id)
    await message.answer(
        await _welcome_for_user(
            store,
            language,
            message.from_user.full_name,
            customer_bot_mode=customer_bot_mode,
        ),
        reply_markup=await _welcome_actions_for_user(store, language),
    )
    await message.answer(
        "Choose a function from the menu below:" if language == "en" else "សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម៖",
        reply_markup=main_menu(
            include_admin=_is_admin(message.from_user.id, admin_ids),
            include_create_bot=not customer_bot_mode,
            language=language,
        ),
    )


@user_router.message(Command("menu"))
async def show_main_menu_command(
    message: Message,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None:
        return
    language = await store.user_language(message.from_user.id)
    await message.answer(
        "Choose a function from the menu below:" if language == "en" else "សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម៖",
        reply_markup=main_menu(
            include_admin=_is_admin(message.from_user.id, admin_ids),
            include_create_bot=not customer_bot_mode,
            language=language,
        ),
    )


@user_router.message(F.text.in_({MENU_LANGUAGE, MENU_LANGUAGE_EN}))
async def show_language_options(message: Message, store: Store) -> None:
    if message.from_user is None:
        return
    language = await store.user_language(message.from_user.id)
    prompt = "Choose your language:" if language == "en" else "សូមជ្រើសរើសភាសា៖"
    await message.answer(prompt, reply_markup=language_menu(language))


@user_router.message(F.text.in_({LANGUAGE_KM, LANGUAGE_EN}))
async def choose_language(
    message: Message,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    language = "en" if message.text == LANGUAGE_EN else "km"
    await store.set_user_language(message.from_user.id, language)
    await message.answer(
        "Language set to English." if language == "en" else "បានប្តូរភាសាទៅខ្មែរ។",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        await _welcome_for_user(
            store,
            language,
            message.from_user.full_name,
            customer_bot_mode=customer_bot_mode,
        ),
        reply_markup=await _welcome_actions_for_user(store, language),
    )
    await message.answer(
        "Choose a function from the menu below:" if language == "en" else "សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម៖",
        reply_markup=main_menu(
            include_admin=_is_admin(message.from_user.id, admin_ids),
            include_create_bot=not customer_bot_mode,
            language=language,
        ),
    )


@user_router.message(F.text.in_({LANGUAGE_BACK, LANGUAGE_BACK_EN}))
async def leave_language_menu(
    message: Message,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None:
        return
    language = await store.user_language(message.from_user.id)
    await message.answer(
        "Choose a function from the menu below:" if language == "en" else "សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម៖",
        reply_markup=main_menu(
            include_admin=_is_admin(message.from_user.id, admin_ids),
            include_create_bot=not customer_bot_mode,
            language=language,
        ),
    )


@user_router.message(F.text.in_({MENU_CATALOG, MENU_CATALOG_EN}))
async def show_games_from_menu(message: Message, store: Store) -> None:
    if message.from_user is None:
        return
    games = await store.games_with_stock()
    language = await store.user_language(message.from_user.id)
    if not games:
        await message.answer(
            "There are no products in stock right now. Please check again later."
            if language == "en"
            else "ពេលនេះមិនទាន់មានទំនិញក្នុង stock ទេ។ សូមត្រឡប់មកពិនិត្យម្ដងទៀត។"
        )
        return
    await message.answer(
        "Choose a product category:" if language == "en" else "សូមជ្រើសរើសប្រភេទទំនិញ៖",
        reply_markup=games_keyboard(games, language),
    )

async def _show_topup_categories(message: Message, store: Store, language: str) -> None:
    games = await store.service_games_with_stock()
    if not games:
        await message.answer(
            "No Telegram Top up packages are available right now."
            if language == "en"
            else "ពេលនេះមិនមានសេវា Telegram Top up ទេ។"
        )
        return
    await message.answer(
        "Choose a Telegram Top up service:" if language == "en" else "សូមជ្រើសរើសសេវា Telegram Top up៖",
        reply_markup=topup_games_keyboard(games, language),
    )


@user_router.message(F.text.in_({MENU_TOPUP, MENU_TOPUP_EN}))
async def open_topup_from_menu(message: Message, store: Store) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    await _show_topup_categories(message, store, await store.user_language(message.from_user.id))


@user_router.callback_query(F.data == "topup")
async def show_topup_categories(query: CallbackQuery, store: Store) -> None:
    await query.answer()
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    await _show_topup_categories(query.message, store, await store.user_language(query.from_user.id))


@user_router.callback_query(F.data.startswith("topup:game:"))
async def show_topup_options(
    query: CallbackQuery,
    store: Store,
    customer_bot_mode: bool = False,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    try:
        game_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        return
    products = [product for product in await store.products_for_game(game_id) if product.get("service_kind")]
    products = [product for product in products if product["status"] == "available"]
    products.sort(key=lambda product: (product["price_cents"], product["id"]))
    language = await store.user_language(query.from_user.id)
    if not products:
        await query.message.answer(
            "No packages are available in this service right now."
            if language == "en"
            else "សេវានេះមិនមាន package ទំនេរទេ។"
        )
        return
    await query.message.answer(
        "Choose a Top up package:" if language == "en" else "សូមជ្រើសរើស package Top up៖",
        reply_markup=service_options_keyboard(
            products,
            language,
            back_callback="topup",
            show_default_icon=True,
        ),
    )


@user_router.message(F.text.in_({MENU_CREATE_BOT, MENU_CREATE_BOT_EN}))
async def begin_customer_bot_builder(
    message: Message,
    state: FSMContext,
    store: Store,
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    if customer_bot_mode:
        await message.answer("Bot ផ្ទាល់ខ្លួននេះមិនអាចបង្កើត bot ផ្សេងទៀតបានទេ។")
        return
    await state.clear()
    setup_orders = await store.customer_bot_setup_orders(message.from_user.id)
    if setup_orders:
        order = setup_orders[0]
        await message.answer(
            f"មាន package {order['duration_days']} ថ្ងៃដែលបានបង់រួច។ ចុចបន្តដើម្បីបញ្ចប់ការភ្ជាប់ bot៖",
            reply_markup=customer_bot_setup_keyboard(int(order["id"])),
        )
        return
    if await store.customer_bot_trial_available(message.from_user.id):
        await _begin_customer_bot_setup(message, state, free_trial=True)
        return
    await message.answer(
        f"{_custom_emoji('service_crown', '👑')} <b>ជ្រើស package បង្កើត Telegram bot</b>\n"
        "បន្ទាប់ពីអ្នកបង់ប្រាក់ជោគជ័យ setup សម្រាប់ bot នឹងបង្ហាញឱ្យបន្ត។",
        parse_mode=ParseMode.HTML,
        reply_markup=customer_bot_plans_keyboard(),
    )


async def _begin_customer_bot_setup(
    message: Message,
    state: FSMContext,
    *,
    order_id: int | None = None,
    free_trial: bool = False,
) -> None:
    await state.clear()
    await state.set_state(CustomerBotBuilderForm.token)
    await state.update_data(
        customer_bot_order_id=order_id,
        customer_bot_free_trial=free_trial,
    )
    await message.answer(
        f"{_custom_emoji('service_check', '✅')} "
        f"{'អ្នកបានបើក Free trial សម្រាប់អ្នកប្រើថ្មី 7 ថ្ងៃ' if free_trial else 'ការបង់ប្រាក់បានបញ្ជាក់រួច'}។\n"
        "ជំហានបន្ទាប់ត្រូវធ្វើក្នុង @BotFather មិនមែនក្នុងឆាតនេះទេ៖\n"
        "1. ចុចប៊ូតុង <b>បើក @BotFather</b> ខាងក្រោម។\n"
        "2. ផ្ញើ <code>/newbot</code> ហើយធ្វើតាមសំណួរដើម្បីបង្កើត bot។\n"
        "3. Copy HTTP API token ដែល BotFather ផ្ញើ រួចត្រឡប់មក paste ក្នុងឆាតនេះ។\n"
        f"{_custom_emoji('service_warning', '⚠️')} Token ជាសម្ងាត់; សារនឹងត្រូវលុប ហើយ token រក្សាទុកដោយ encrypt។",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("បើក BotFather ដើម្បីបង្កើត bot៖", reply_markup=open_botfather_keyboard())


@user_router.callback_query(F.data.startswith("bot:plan:"))
async def choose_customer_bot_plan(
    query: CallbackQuery,
    bot: Bot,
    store: Store,
    state: FSMContext,
    payment_config: dict[str, str],
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមបង្កើត bot ក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    plan_key = (query.data or "").rsplit(":", 1)[1]
    plans = {"monthly": (250, 30, "1 ខែ"), "two_months": (350, 60, "2 ខែ"), "yearly": (1500, 365, "1 ឆ្នាំ")}
    plan = plans.get(plan_key)
    if plan is None:
        await query.answer("Package មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    setup_error = _payment_setup_error(payment_config)
    if setup_error:
        await query.answer(setup_error, show_alert=True)
        return
    amount_cents, duration_days, plan_label = plan
    transaction_id = f"ROKIRABOT_{uuid4().hex}"
    order = await store.create_customer_bot_order(query.from_user.id, plan_key, transaction_id)
    try:
        direct_qr = await request_direct_qr(
            profile_id=payment_config["profile_id"],
            secret_key=payment_config["secret_key"],
            transaction_id=transaction_id,
            amount_cents=amount_cents,
            success_url=payment_config["success_url"],
            remark=f"ROKIRA bot plan {plan_label} order {order['id']}",
        )
        qr_png = (
            render_payment_card_png(direct_qr.content, amount_cents, transaction_id)
            if direct_qr.content
            else await download_direct_qr_image(direct_qr.image_url)
        )
    except (ValueError, aiohttp.ClientError, TimeoutError) as error:
        await store.cancel_customer_bot_order(order["id"], query.from_user.id)
        logger.warning("Could not create customer bot plan invoice: %s", error)
        await query.answer("មិនអាចបង្កើត QR បានទេ។ សូមសាកម្ដងទៀត។", show_alert=True)
        return
    await query.answer()
    await query.message.answer_photo(
        photo=BufferedInputFile(qr_png, filename=f"rokira-bot-plan-{order['id']}.png"),
        caption=(
            f"{_custom_emoji('wallet', '💳')} <b>បង់ package បង្កើត bot</b>\n"
            f"Package៖ <b>{plan_label}</b> · តម្លៃ៖ <b>{_price(amount_cents)}</b>\n"
            "ស្កេន KHQR ក្នុងរយៈពេល 3 នាទី។ ក្រោយ Verify ជោគជ័យ ទើបបង្ហាញជំហាន BotFather និងប្រអប់ដាក់ token។"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=customer_bot_invoice_keyboard(order["id"]),
    )


@user_router.callback_query(F.data.startswith("bot:cancel:"))
async def cancel_customer_bot_plan(query: CallbackQuery, store: Store) -> None:
    try:
        order_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("Invoice មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    cancelled = await store.cancel_customer_bot_order(order_id, query.from_user.id)
    await query.answer("បានបោះបង់ invoice" if cancelled else "Invoice នេះមិនអាចបោះបង់បានទេ។", show_alert=not cancelled)
    if cancelled and isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)


@user_router.callback_query(F.data.startswith("bot:setup:"))
async def continue_paid_customer_bot_setup(
    query: CallbackQuery,
    store: Store,
    state: FSMContext,
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    try:
        order_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("Order មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    order = await store.prepare_customer_bot_setup(order_id, query.from_user.id)
    if order is None:
        await query.answer("Order មិនទាន់បង់ ឬមិនមែនរបស់អ្នកទេ។", show_alert=True)
        return
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=None)
    await _begin_customer_bot_setup(query.message, state, order_id=order_id)


@user_router.message(CustomerBotBuilderForm.token, F.text)
async def receive_customer_bot_token(message: Message, state: FSMContext, store: Store) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    token = (message.text or "").strip()
    if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,100}", token):
        await message.answer("Token មិនត្រឹមត្រូវទេ។ សូម copy HTTP API token ពី BotFather ហើយផ្ញើម្ដងទៀត។")
        return
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning("Could not delete a customer bot token message")
    try:
        await state.update_data(customer_bot_token=token, customer_bot_username="")
        await state.set_state(CustomerBotBuilderForm.admin_user_id)
        await message.answer(
            "បានទទួល token ហើយ។ ឥឡូវសូមបញ្ចូល Telegram User ID របស់អ្នក ដើម្បីកំណត់សិទ្ធិ Admin លើ bot នេះ។ "
            "ចុចប៊ូតុងខាងក្រោមដើម្បីយក ID របស់អ្នក៖",
            reply_markup=telegram_user_id_help_keyboard(),
        )
    except Exception as error:
        logger.exception("Customer bot token received but setup transition failed (%s)", type(error).__name__)
        await message.answer(
            "ទទួល token បាន ប៉ុន្តែការបន្ត setup មានបញ្ហា។ សូមចុចបង្កើត bot ម្តងទៀត ហើយផ្ញើ token នេះវិញ។"
        )


@user_router.message(CustomerBotBuilderForm.admin_user_id, F.text)
async def receive_customer_bot_admin_user_id(message: Message, state: FSMContext, store: Store) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    try:
        admin_user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Telegram User ID ត្រូវជាលេខ។ សូមយកលេខពី @userinfo3bot ហើយផ្ញើម្ដងទៀត។")
        return
    if admin_user_id <= 0:
        await message.answer("Telegram User ID មិនត្រឹមត្រូវទេ។ សូមបញ្ចូលលេខវិជ្ជមាន។")
        return
    if admin_user_id != message.from_user.id:
        await message.answer("ID នេះមិនត្រូវនឹងគណនី Telegram ដែលកំពុងរៀបចំ bot ទេ។ សូមផ្ញើ ID ផ្ទាល់ខ្លួនរបស់អ្នក។")
        return
    await state.update_data(customer_bot_admin_user_id=admin_user_id)
    await finish_customer_bot_setup(message, state, store)


async def finish_customer_bot_setup(
    message: Message,
    state: FSMContext,
    store: Store,
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    data = await state.get_data()
    try:
        admin_user_id = int(data["customer_bot_admin_user_id"])
        if data.get("customer_bot_order_id"):
            activated = await store.activate_paid_customer_bot_order(
                int(data["customer_bot_order_id"]),
                message.from_user.id,
                bot_token=data["customer_bot_token"],
                bot_username=data["customer_bot_username"],
                khqr_profile_id="",
                khqr_secret_key="",
                admin_user_id=admin_user_id,
            )
            if activated is None:
                await state.clear()
                await message.answer("មិនឃើញ order បង់ប្រាក់ដែលត្រូវនឹងគណនីនេះទេ។ សូមជ្រើស package ហើយបង់ម្ដងទៀត។")
                return
            await state.clear()
            bot_label = f"@{escape(activated['bot_username'])}" if activated["bot_username"] else "bot ផ្ទាល់ខ្លួន"
            await message.answer(
                f"{_custom_emoji('service_check', '✅')} <b>បានភ្ជាប់ {bot_label} រួចរាល់</b>\n"
                f"Package៖ <b>{activated['duration_days']} ថ្ងៃ</b> · តម្លៃ៖ <b>{_price(activated['amount_cents'])}</b>\n"
                "Bot នឹងដំណើរការភ្លាមៗ។ បើក bot ថ្មីចុច Start រួចផ្ញើ /admin ដើម្បីបញ្ចូល stock និងកំណត់ KHQR ផ្ទាល់ខ្លួន។",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(
                    include_create_bot=not customer_bot_mode,
                    language=await store.user_language(message.from_user.id),
                ),
            )
            return
        if not data.get("customer_bot_free_trial"):
            await state.clear()
            await message.answer("សូមចាប់ផ្តើមពីប៊ូតុង បង្កើត bot ហើយជ្រើស package មុនសិន។")
            return
        trial = await store.create_first_customer_bot_trial(
            owner_user_id=message.from_user.id,
            bot_token=data["customer_bot_token"],
            bot_username=data["customer_bot_username"],
            khqr_profile_id="",
            khqr_secret_key="",
            admin_user_id=admin_user_id,
        )
    except (KeyError, ValueError) as error:
        await message.answer(f"មិនអាចភ្ជាប់ bot បានទេ៖ {escape(str(error))}")
        return
    if trial:
        await state.clear()
        bot_label = f"@{escape(trial['bot_username'])}" if trial["bot_username"] else "bot ផ្ទាល់ខ្លួន"
        await message.answer(
            f"{_custom_emoji('service_check', '✅')} <b>បានបង្កើត {bot_label} រួចរាល់</b>\n"
            "អ្នកប្រើថ្មីទទួលបាន <b>Free 7 ថ្ងៃ</b>។ បើក bot ថ្មីចុច Start រួចផ្ញើ /admin ដើម្បីបញ្ចូល stock និងកំណត់ KHQR ផ្ទាល់ខ្លួន។",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(
                include_create_bot=not customer_bot_mode,
                language=await store.user_language(message.from_user.id),
            ),
        )
        return
    await state.clear()
    await message.answer(
        "គណនីនេះបានប្រើ Free trial រួចហើយ។ សូមជ្រើស package បង់ប្រាក់ដើម្បីបង្កើត bot បន្ថែម។",
        reply_markup=customer_bot_plans_keyboard(),
    )


@user_router.message(F.text.in_({MENU_ORDERS, MENU_ORDERS_EN}))
async def show_orders_from_menu(message: Message, store: Store) -> None:
    if message.from_user is None:
        return
    orders = await store.order_history(message.from_user.id)
    language = await store.user_language(message.from_user.id)
    if not orders:
        await message.answer("You do not have any orders yet." if language == "en" else "អ្នកមិនទាន់មានប្រវត្តិបញ្ជាទិញទេ។")
        return
    statuses = {
        "pending": "Awaiting payment",
        "paid": "Paid",
        "cancelled": "Cancelled",
        "expired": "Expired",
    } if language == "en" else {
        "pending": "កំពុងរង់ចាំការបង់ប្រាក់",
        "paid": "បានបង់ប្រាក់",
        "cancelled": "បានបោះបង់",
        "expired": "ផុតកំណត់",
    }
    lines = ["━━━━━━━━━━━━━━━━━━━━", "<b>Your orders</b>" if language == "en" else "<b>ការបញ្ជាទិញរបស់អ្នក</b>"]
    for order in orders:
        lines.append(
            f"#{order['id']} · {_html_field(order['name'], order.get('name_html'))} · {_price(order['price_cents'])} · "
            f"{statuses.get(order['status'], order['status'])}"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@user_router.message(F.text.in_({MENU_PROFILE, MENU_PROFILE_EN}))
async def show_profile_from_menu(message: Message, store: Store) -> None:
    if message.from_user is None:
        return
    await message.answer(
        _profile_text(
            message.from_user.full_name,
            message.from_user.username,
            message.from_user.id,
            await store.user_language(message.from_user.id),
        ),
        parse_mode=ParseMode.HTML,
    )


@user_router.message(F.text.in_({MENU_WALLET, MENU_WALLET_EN}))
async def show_wallet_from_menu(message: Message, store: Store) -> None:
    if message.from_user is None:
        return
    balance = await store.wallet_balance_cents(message.from_user.id)
    language = await store.user_language(message.from_user.id)
    await message.answer(
        f"{_custom_emoji('wallet_topup', '💰')} <b>{'Wallet' if language == 'en' else 'កាបូបប្រាក់'}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{'Balance' if language == 'en' else 'សមតុល្យ'}: <b>{_price(balance)}</b>\n"
        f"{_custom_emoji('new_stock', '🎁')} {'Top up $5 or more and get a 5% bonus' if language == 'en' else 'បញ្ចូលចាប់ពី $5 ទទួលបាន Bonus 5%'}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose a top-up amount:" if language == "en" else "សូមជ្រើសរើសចំនួនប្រាក់៖",
        parse_mode=ParseMode.HTML,
        reply_markup=wallet_topup_keyboard(),
    )


@user_router.callback_query(F.data == "wallet:custom")
async def request_custom_wallet_amount(query: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមបញ្ចូលប្រាក់ក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    await state.clear()
    await state.set_state(WalletTopupForm.amount)
    await query.answer()
    await query.message.answer("សូមវាយចំនួនប្រាក់ជា USD ចាប់ពី $0.01 ដល់ $1,000 (ឧទាហរណ៍ 0.01 ឬ 7.50)៖")


@user_router.message(WalletTopupForm.amount, F.text)
async def receive_custom_wallet_amount(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    amount_cents = _parse_wallet_amount(message.text or "")
    if amount_cents is None:
        await message.answer("ចំនួនមិនត្រឹមត្រូវទេ។ សូមបញ្ចូល $0.01 ដល់ $1,000.00 ដោយមានមិនលើស 2 ខ្ទង់ទសភាគ។")
        return
    await state.clear()
    await message.answer(
        f"បញ្ជាក់ចំនួនបញ្ចូលប្រាក់: <b>{_price(amount_cents)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=wallet_custom_amount_keyboard(amount_cents),
    )


@user_router.callback_query(F.data.startswith("wallet:topup:"))
async def create_wallet_topup(
    query: CallbackQuery,
    bot: Bot,
    store: Store,
    admin_ids: frozenset[int],
    payment_config: dict[str, str],
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមបញ្ចូលប្រាក់ក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    try:
        amount_cents = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("ចំនួនប្រាក់មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    if not 1 <= amount_cents <= 100_000:
        await query.answer("ចំនួនប្រាក់មិនស្ថិតក្នុងចន្លោះដែលអនុញ្ញាតទេ។", show_alert=True)
        return
    secret_key = payment_config.get("secret_key", "").strip()
    if payment_config.get("enabled", "false").lower() != "true" or not secret_key:
        await query.answer("ការបង់ KHQR មិនទាន់បើកទេ។ សូមទាក់ទង Admin។", show_alert=True)
        return

    bonus_cents = (amount_cents * 5 + 50) // 100 if amount_cents >= 500 else 0
    await store.register_user(
        query.from_user.id,
        query.from_user.first_name,
        query.from_user.username,
    )
    topup = await store.create_wallet_topup(
        query.from_user.id,
        amount_cents,
        amount_cents + bonus_cents,
    )
    transaction_id = f"ROKIRA_TOPUP_{topup['id']}_{uuid4().hex[:8]}"
    try:
        direct_qr = await request_direct_qr(
            profile_id=payment_config["profile_id"],
            secret_key=secret_key,
            transaction_id=transaction_id,
            amount_cents=amount_cents,
            success_url=payment_config["success_url"],
            remark=f"ROKIRA wallet top-up #{topup['id']}",
        )
        qr_png = (
            render_payment_card_png(direct_qr.content, amount_cents, transaction_id)
            if direct_qr.content
            else await download_direct_qr_image(direct_qr.image_url)
        )
    except (ValueError, aiohttp.ClientError, TimeoutError) as error:
        logger.warning("Could not create KHQR for wallet top-up %s: %s", topup["id"], error)
        await query.answer("មិនអាចបង្កើត KHQR បានទេ។ សូមសាកម្ដងទៀត។", show_alert=True)
        await _notify_admins(
            bot,
            admin_ids,
            f"មិនអាចបង្កើត KHQR សម្រាប់ការបញ្ចូលប្រាក់ #{topup['id']} បានទេ។",
        )
        return

    if not await store.set_wallet_topup_transaction(topup["id"], transaction_id):
        await query.answer("សំណើផុតកំណត់។ សូមសាកម្ដងទៀត។", show_alert=True)
        return
    await query.answer("បានបង្កើត QR សម្រាប់បញ្ចូលប្រាក់។")
    await query.message.answer_photo(
        photo=BufferedInputFile(qr_png, filename=f"rokira-topup-{topup['id']}.png"),
        caption=(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{_custom_emoji('wallet_topup', '💰')} <b>បញ្ចូលប្រាក់ទៅកាបូប</b>\n"
            f"ចំនួនបង់: <b>{_price(amount_cents)}</b>\n"
            f"Bonus: <b>{_price(bonus_cents)}</b>\n"
            f"នឹងបញ្ចូលសរុប: <b>{_price(topup['credit_cents'])}</b>\n"
            f"ផុតកំណត់: {_cambodia_time(topup['expires_at'])} (ម៉ោងកម្ពុជា)\n"
            "សមតុល្យនឹងកើនឡើងក្រោយ KHQR Verify បញ្ជាក់ការបង់ និងចំនួនប្រាក់ត្រូវគ្នាប៉ុណ្ណោះ។\n"
            "━━━━━━━━━━━━━━━━━━━━"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=wallet_topup_payment_keyboard(),
    )


async def _admin_profile_photo_id(
    bot: Bot,
    admin_ids: frozenset[int],
    username: str,
    contact_user_id: int | None = None,
) -> str | None:
    if contact_user_id is not None:
        try:
            photos = await bot.get_user_profile_photos(contact_user_id, limit=1)
        except TelegramAPIError:
            return None
        return photos.photos[0][-1].file_id if photos.photos and photos.photos[0] else None

    for admin_id in sorted(admin_ids):
        try:
            chat = await bot.get_chat(admin_id)
        except TelegramAPIError:
            continue
        if (chat.username or "").lstrip("@").casefold() != username.casefold():
            continue
        try:
            photos = await bot.get_user_profile_photos(admin_id, limit=1)
        except TelegramAPIError:
            return None
        return photos.photos[0][-1].file_id if photos.photos and photos.photos[0] else None
    return None


async def _send_admin_contact(
    bot: Bot,
    chat_id: int,
    admin_ids: frozenset[int],
    contact_user_id: int | None = None,
    username: str = ADMIN_CONTACT_USERNAME,
) -> None:
    text = (
        f"{_custom_emoji('support', '☎️')} <b>ទាក់ទងទៅកាន់ Admin</b>\n"
        f"Username: <code>@{escape(username)}</code>\n"
        "ចុចប៊ូតុងខាងក្រោម ដើម្បីបើក private chat។"
    )
    markup = admin_contact_keyboard(username)
    photo_id = await _admin_profile_photo_id(bot, admin_ids, username, contact_user_id)
    if photo_id:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_id,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return
        except TelegramAPIError as error:
            logger.info("Could not send admin contact photo: %s", error)
    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)


@user_router.message(F.text == MENU_SUPPORT)
async def show_support_from_menu(
    message: Message,
    bot: Bot,
    store: Store,
    admin_ids: frozenset[int],
    admin_contact_user_id: int | None = None,
) -> None:
    username = await store.get_setting("support_username", ADMIN_CONTACT_USERNAME)
    await _send_admin_contact(bot, message.chat.id, admin_ids, admin_contact_user_id, username)


@user_router.message(F.text == MENU_CHANNEL)
async def show_channel_link(message: Message, store: Store) -> None:
    channel_url = await store.get_setting("channel_url", DEFAULT_CHANNEL_URL)
    await message.answer(
        f'ចុចតំណនេះដើម្បីចូលរួមឆានែល៖ <a href="{escape(channel_url, quote=True)}">ចូលរួមឆានែល</a>',
        parse_mode=ParseMode.HTML,
    )


@user_router.message(F.text.in_({MENU_GUIDE, MENU_GUIDE_EN}))
async def show_guide_from_menu(message: Message, store: Store) -> None:
    language = await store.user_language(message.from_user.id) if message.from_user else "km"
    if language == "en":
        await message.answer(
            "How to order:\n"
            "1. Select Browse products and choose a category.\n"
            "2. Review each product's photos, details, price, and availability.\n"
            "3. Press Buy now and choose a payment method.\n"
            "4. Digital delivery is sent only after payment verification succeeds."
        )
        return
    await message.answer(
        "របៀបបញ្ជាទិញ៖\n"
        "១. ចុច បញ្ជាទិញទំនិញ ហើយជ្រើស game។\n"
        "២. រូបភាព ព័ត៌មាន level និងតម្លៃ account ក្នុង game នោះនឹងបង្ហាញភ្លាមៗ។\n"
        "៣. ចុច ទិញឥឡូវនេះ លើ account ដែលចង់បាន។\n"
        "៤. បង់តាម KHQR។ ក្រោយពេលប្រព័ន្ធផ្ទៀងផ្ទាត់ជោគជ័យ Bot នឹងផ្ញើព័ត៌មានចូល account ជូន។"
    )


@user_router.message(F.text.in_({MENU_DISCOUNT_CODE, MENU_DISCOUNT_CODE_EN}))
async def begin_redeem_discount_code(message: Message, state: FSMContext, store: Store) -> None:
    await state.clear()
    await state.set_state(RedeemDiscountForm.code)
    await state.update_data(checkout_product_id=None)
    language = await store.user_language(message.from_user.id) if message.from_user else "km"
    await message.answer(
        "Enter your discount code:" if language == "en" else "សូមបញ្ចូលកូដបញ្ចុះតម្លៃ៖",
        reply_markup=ReplyKeyboardRemove(),
    )


@user_router.callback_query(F.data.startswith("discount:"))
async def begin_checkout_discount_code(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមបញ្ចូលកូដក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").partition(":")[2])
    except ValueError:
        await query.answer("សំណើមិនត្រឹមត្រូវ។", show_alert=True)
        return
    if product_id <= 0:
        await query.answer("សំណើមិនត្រឹមត្រូវ។", show_alert=True)
        return
    await query.answer()
    previous_data = await state.get_data()
    service_checkout_data = {
        key: value
        for key, value in previous_data.items()
        if key.startswith("service_")
    }
    service_checkout_data["service_product_id"] = product_id if service_checkout_data.get("service_username") else None
    await state.clear()
    await state.set_state(RedeemDiscountForm.code)
    service_checkout_data["checkout_product_id"] = product_id
    await state.update_data(**service_checkout_data)
    language = await store.user_language(query.from_user.id)
    await query.message.answer(
        "Enter your discount code:" if language == "en" else "សូមបញ្ចូលកូដបញ្ចុះតម្លៃ៖",
    )


@user_router.message(RedeemDiscountForm.code, F.text)
async def redeem_discount_code(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    code = _normalize_discount_code(message.text or "")
    percent = await store.apply_discount_code(message.from_user.id, code)
    language = await store.user_language(message.from_user.id)
    checkout_data = await state.get_data()
    checkout_product_id = checkout_data.get("checkout_product_id")
    service_checkout_data = {
        key: value
        for key, value in checkout_data.items()
        if key.startswith("service_")
    }
    await state.clear()
    if checkout_product_id is not None and service_checkout_data.get("service_username"):
        service_checkout_data["service_product_id"] = int(checkout_product_id)
        await state.update_data(**service_checkout_data)
    reply_markup = (
        payment_method_keyboard(int(checkout_product_id), language)
        if checkout_product_id is not None
        else main_menu(
            include_admin=_is_admin(message.from_user.id, admin_ids),
            include_create_bot=not customer_bot_mode,
            language=language,
        )
    )
    if percent is None:
        await message.answer(
            "This code is invalid or inactive. Please check it and try again."
            if language == "en"
            else "កូដនេះមិនត្រឹមត្រូវ ឬត្រូវបានបិទទេ។ សូមពិនិត្យកូដម្ដងទៀត។",
            reply_markup=reply_markup,
        )
        return
    await message.answer(
        f"Code <code>{escape(code)}</code> applied. You get {percent}% off your next purchase."
        if language == "en"
        else f"បានប្រើកូដ <code>{escape(code)}</code> រួចហើយ។ អ្នកទទួលបានបញ្ចុះតម្លៃ {percent}% លើការទិញបន្ទាប់។",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


@user_router.message(F.text == MENU_ADMIN)
async def open_admin_from_menu(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមបើកផ្ទាំង admin ក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())


@user_router.callback_query(F.data == "catalog")
async def show_games(query: CallbackQuery, store: Store) -> None:
    await query.answer()
    games = await store.games_with_stock()
    language = await store.user_language(query.from_user.id)
    if isinstance(query.message, Message):
        if not games:
            await query.message.answer(
                "There are no products in stock right now. Please check again later."
                if language == "en"
                else "ពេលនេះមិនទាន់មានទំនិញក្នុង stock ទេ។ សូមត្រឡប់មកពិនិត្យម្ដងទៀត។"
            )
            return
        await query.message.answer(
            "Choose a product category:" if language == "en" else "សូមជ្រើសរើសប្រភេទទំនិញ៖",
            reply_markup=games_keyboard(games, language),
        )


@user_router.callback_query(F.data == "menu")
async def return_to_main_menu(
    query: CallbackQuery,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    language = await store.user_language(query.from_user.id)
    await query.message.answer(
        "Choose a function from the menu below:" if language == "en" else "សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម៖",
        reply_markup=main_menu(
            include_admin=_is_admin(query.from_user.id, admin_ids),
            include_create_bot=not customer_bot_mode,
            language=language,
        ),
    )


@user_router.callback_query(F.data.startswith("game:"))
async def show_levels(
    query: CallbackQuery,
    bot: Bot,
    store: Store,
    customer_bot_mode: bool = False,
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer(
            "Please shop in a private chat with the bot." if query.from_user else "សូមបើកការបញ្ជាទិញក្នុង private chat ជាមួយ bot។",
            show_alert=True,
        )
        return
    await query.answer()
    language = await store.user_language(query.from_user.id)
    try:
        game_id = int((query.data or "").partition(":")[2])
    except ValueError:
        return
    products = await store.products_for_game(game_id)
    if not products:
        await query.message.answer(
            "There are no products in this category." if language == "en" else "ប្រភេទទំនិញនេះមិនមានក្នុង stock ទេ។"
        )
        return
    if all(product.get("service_kind") for product in products):
        service_products = [product for product in products if product["status"] == "available"]
        service_products.sort(key=lambda product: (product["price_cents"], product["id"]))
        if not service_products:
            await query.message.answer(
                "No top-up packages are available right now."
                if language == "en"
                else "ពេលនេះមិនមាន package Top up ទំនេរទេ។"
            )
            return
        await query.message.answer(
            "Choose a Top up package:" if language == "en" else "សូមជ្រើសរើស package Top up៖",
            reply_markup=service_options_keyboard(
                service_products,
                language,
                show_default_icon=True,
            ),
        )
        return
    for product in products:
        await _send_product_card(
            bot,
            query.message.chat.id,
            store,
            product["id"],
            language,
            customer_bot_mode=customer_bot_mode,
        )
        await asyncio.sleep(0.08)


@user_router.callback_query(F.data.startswith("level:"))
async def show_products(
    query: CallbackQuery,
    store: Store,
    customer_bot_mode: bool = False,
) -> None:
    await query.answer()
    try:
        level_id = int((query.data or "").partition(":")[2])
    except ValueError:
        return
    products = await store.products_for_level(level_id)
    language = await store.user_language(query.from_user.id)
    if isinstance(query.message, Message):
        if not products:
            await query.message.answer(
                "Products in this type are sold or reserved." if language == "en" else "ទំនិញក្នុងប្រភេទនេះត្រូវបានលក់ ឬកក់អស់ហើយ។"
            )
            return
        await query.message.answer(
            "Choose a product to view:" if language == "en" else "សូមជ្រើសរើសទំនិញដែលចង់មើល៖",
            reply_markup=products_keyboard(
                products,
                language,
                show_default_icon=True,
            ),
        )


@user_router.callback_query(F.data.startswith("product:"))
async def show_product(
    query: CallbackQuery,
    bot: Bot,
    store: Store,
    customer_bot_mode: bool = False,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    if query.message.chat.type != ChatType.PRIVATE:
        language = await store.user_language(query.from_user.id)
        await query.message.answer(
            "Open a private chat with the bot to view and order products."
            if language == "en"
            else "សូមបើកការសន្ទនាផ្ទាល់ជាមួយ bot ដើម្បីមើលទំនិញ និងបញ្ជាទិញ។"
        )
        return
    try:
        product_id = int((query.data or "").partition(":")[2])
    except ValueError:
        return
    await _send_product_card(
        bot,
        query.message.chat.id,
        store,
        product_id,
        await store.user_language(query.from_user.id),
        customer_bot_mode=customer_bot_mode,
    )


@user_router.callback_query(F.data.startswith("sold:"))
async def sold_product_notice(query: CallbackQuery) -> None:
    await query.answer("account នេះបានលក់រួចហើយ។", show_alert=True)


@user_router.callback_query(F.data.startswith("reserved:"))
async def reserved_product_notice(query: CallbackQuery) -> None:
    await query.answer("account នេះកំពុងត្រូវបានកក់។ សូមជ្រើសរើស account ផ្សេង។", show_alert=True)


@user_router.callback_query(F.data.startswith("buy:"))
async def buy_product(
    query: CallbackQuery,
    store: Store | None = None,
    state: FSMContext | None = None,
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមធ្វើការបញ្ជាទិញក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").partition(":")[2])
    except ValueError:
        await query.answer("សំណើមិនត្រឹមត្រូវ។", show_alert=True)
        return

    get_product = getattr(store, "available_product", None)
    product = await get_product(product_id) if get_product else None
    if get_product and (product is None or product["status"] != "available"):
        await query.answer("ទំនិញនេះមិនមានសម្រាប់ទិញទៀតទេ។", show_alert=True)
        return
    await query.answer()
    if product and product.get("service_kind"):
        if state is None:
            await query.answer("មិនអាចបើកការបញ្ជាទិញបានទេ។ សូមសាកល្បងម្ដងទៀត។", show_alert=True)
            return
        await state.clear()
        await state.update_data(
            service_product_id=product_id,
            service_identity_fields=product.get("identity_fields", []),
        )
        await state.set_state(ServiceUsernameForm.username)
        await query.message.answer(
            f"{_custom_emoji('service_crown', '👑')} <b>ជំហានបន្ទាប់៖ បញ្ចូល Telegram Username</b>\n"
            f"{_custom_emoji('service_arrow', '➡️')} សេវា៖ <b>{_html_field(product['game_name'], product.get('game_name_html'))}</b>\n"
            f"{_custom_emoji('service_down', '⬇️')} ជម្រើស៖ <b>{_html_field(product['name'], product.get('name_html'))}</b> · "
            f"<b>{_price(product['price_cents'])}</b>\n\n"
            "<blockquote>សូមផ្ញើយូសនាម Telegram របស់អ្នក ដោយមានឬគ្មាន @ ក៏បាន។\n"
            "សូមពិនិត្យឱ្យត្រឹមត្រូវ ព្រោះ admin នឹងប្រើ username នេះដើម្បីដំណើរការសេវា។</blockquote>"
            f"{_custom_emoji('service_warning', '⚠️')} មិនត្រូវផ្ញើលេខសម្ងាត់ ឬ OTP ទេ។",
            parse_mode=ParseMode.HTML,
        )
        return
    get_language = getattr(store, "user_language", None)
    language = await get_language(query.from_user.id) if get_language else "km"
    await query.message.answer(
        "សូមជ្រើសរើសវិធីបង់ប្រាក់៖ ABA Mobile ដោយស្កេន QR ឬកាត់ពីកាបូបប្រាក់ក្នុង bot។",
        reply_markup=payment_method_keyboard(product_id, language),
    )


@user_router.message(ServiceUsernameForm.username, F.text)
async def receive_service_username(message: Message, state: FSMContext, store: Store) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    username = (message.text or "").strip().removeprefix("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        await message.answer(
            "យូសនាមមិនត្រឹមត្រូវទេ។ សូមបញ្ចូល username Telegram ដែលមាន ៥–៣២ តួអក្សរ ហើយចាប់ផ្តើមដោយអក្សរ។"
        )
        return
    data = await state.get_data()
    product_id = data.get("service_product_id")
    product = await store.available_product(int(product_id)) if product_id else None
    if product is None or not product.get("service_kind") or product["status"] != "available":
        await state.clear()
        await message.answer("ជម្រើសសេវានេះមិនមានទៀតទេ។ សូមជ្រើសរើសម្ដងទៀត។")
        return
    normalized_username = f"@{username}"
    identity_fields = product.get("identity_fields", [])
    await state.update_data(service_username=normalized_username, service_identity_values={})
    if identity_fields:
        await state.update_data(service_identity_index=0)
        await state.set_state(ServiceUsernameForm.identity_value)
        await message.answer(
            f"ឥឡូវសូមបញ្ចូល <b>{escape(identity_fields[0])}</b> សម្រាប់សេវា "
            f"<b>{_html_field(product['name'], product.get('name_html'))}</b>៖",
            parse_mode=ParseMode.HTML,
        )
        return
    await _show_service_order_confirmation(message, state, product, normalized_username, {})


async def _show_service_order_confirmation(
    message: Message,
    state: FSMContext,
    product: dict,
    username: str,
    identity_values: dict[str, str],
) -> None:
    await state.set_state(ServiceUsernameForm.confirm)
    identity_lines = "".join(
        f"{_custom_emoji('service_arrow', '➡️')} {escape(label)}៖ <code>{escape(value)}</code>\n"
        for label, value in identity_values.items()
    )
    await message.answer(
        f"{_custom_emoji('service_check', '✅')} <b>សូមពិនិត្យការបញ្ជាទិញរបស់អ្នក</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{_custom_emoji('service_crown', '👑')} សេវា៖ <b>{_html_field(product['game_name'], product.get('game_name_html'))}</b>\n"
        f"{_custom_emoji('service_down', '⬇️')} ជម្រើស៖ <b>{_html_field(product['name'], product.get('name_html'))}</b>\n"
        f"{_custom_emoji('service_arrow', '➡️')} តម្លៃ៖ <b>{_price(product['price_cents'])}</b>\n"
        f"{_custom_emoji('account', '👤')} Telegram username៖ <code>{escape(username)}</code>\n"
        f"{identity_lines}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{_custom_emoji('service_warning', '⚠️')} <blockquote>បើ username ត្រឹមត្រូវ សូមបញ្ជាក់ខាងក្រោម។ "
        "បើខុស សូមចុចកែ username មុនបង់ប្រាក់។</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=service_username_confirmation(),
    )


@user_router.message(ServiceUsernameForm.identity_value, F.text)
async def receive_service_identity_value(message: Message, state: FSMContext, store: Store) -> None:
    if message.from_user is None or message.chat.type != ChatType.PRIVATE:
        return
    value = (message.text or "").strip()
    if not 1 <= len(value) <= 100:
        await message.answer("ID ត្រូវមាន ១–១០០ តួអក្សរ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    data = await state.get_data()
    product_id = data.get("service_product_id")
    product = await store.available_product(int(product_id)) if product_id else None
    fields = data.get("service_identity_fields", [])
    index = int(data.get("service_identity_index", 0))
    if product is None or not product.get("service_kind") or index >= len(fields):
        await state.clear()
        await message.answer("ព័ត៌មានសេវាមិនមានទៀតទេ។ សូមចាប់ផ្តើមការបញ្ជាទិញម្ដងទៀត។")
        return
    identity_values = dict(data.get("service_identity_values", {}))
    identity_values[fields[index]] = value
    index += 1
    await state.update_data(service_identity_values=identity_values, service_identity_index=index)
    if index < len(fields):
        await message.answer(f"សូមបញ្ចូល <b>{escape(fields[index])}</b>៖", parse_mode=ParseMode.HTML)
        return
    await _show_service_order_confirmation(
        message,
        state,
        product,
        data.get("service_username", ""),
        identity_values,
    )


@user_router.callback_query(F.data.in_({"service:confirm", "service:edit"}))
async def confirm_service_username(query: CallbackQuery, state: FSMContext, store: Store) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមបន្តក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    data = await state.get_data()
    product_id = data.get("service_product_id")
    product = await store.available_product(int(product_id)) if product_id else None
    if product is None or not product.get("service_kind") or product["status"] != "available":
        await state.clear()
        await query.answer("ជម្រើសសេវានេះមិនមានទៀតទេ។", show_alert=True)
        return
    if query.data == "service:edit":
        await state.set_state(ServiceUsernameForm.username)
        await query.answer()
        await query.message.answer("សូមបញ្ចូល Telegram username ម្តងទៀត៖")
        return
    username = data.get("service_username", "")
    identity_fields = data.get("service_identity_fields", [])
    identity_values = data.get("service_identity_values", {})
    if not username or any(not identity_values.get(field) for field in identity_fields):
        await query.answer("សូមបញ្ចូលព័ត៌មានអត្តសញ្ញាណឱ្យគ្រប់សិន។", show_alert=True)
        return
    await state.update_data(service_identity_values=identity_values)
    await state.set_state(None)
    await query.answer("បានបញ្ជាក់ username រួច")
    await query.message.answer(
        f"បានជ្រើសរើស <b>{_html_field(product['name'], product.get('name_html'))}</b> "
        f"សម្រាប់ <code>{escape(username)}</code>។ សូមជ្រើសរើសវិធីបង់ប្រាក់៖",
        parse_mode=ParseMode.HTML,
        reply_markup=payment_method_keyboard(product_id, await store.user_language(query.from_user.id)),
    )


def _payment_setup_error(payment_config: dict[str, str]) -> str | None:
    profile_id = payment_config.get("profile_id", "").strip()
    secret_key = payment_config.get("secret_key", "").strip()
    if payment_config.get("enabled", "true").lower() != "true":
        return "KHQR កំពុងផ្អាក ព្រោះ Direct QR API របស់ KHQRcc មិនអាចភ្ជាប់បាន។ សូមសាក network ដែលអាចបើក khqr.cc។"
    if not profile_id or not secret_key or secret_key.lower().startswith(("your_", "replace_", "placeholder")):
        return "KHQR មិនទាន់កំណត់ទេ។ សូមចូលផ្ទាំង Admin ហើយកំណត់ Profile ID និង Secret Key ផ្ទាល់ខ្លួន។"
    return None


@user_router.callback_query(F.data.startswith("pay:"))
async def choose_product_payment_method(
    query: CallbackQuery,
    bot: Bot,
    store: Store,
    admin_ids: frozenset[int],
    payment_config: dict[str, str],
    state: FSMContext | None = None,
) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមបង់ប្រាក់ក្នុង private chat ជាមួយ bot។", show_alert=True)
        return
    try:
        _, raw_product_id, payment_method = (query.data or "").split(":")
        product_id = int(raw_product_id)
    except ValueError:
        await query.answer("សំណើបង់ប្រាក់មិនត្រឹមត្រូវ។", show_alert=True)
        return
    if payment_method == "khqr":
        payment_method = "aba"
    if payment_method not in {"aba", "wallet"}:
        await query.answer("វិធីបង់ប្រាក់នេះមិនមានទេ។", show_alert=True)
        return
    get_product = getattr(store, "available_product", None)
    product = await get_product(product_id) if get_product else None
    checkout_data = await state.get_data() if state is not None else {}
    service_username = ""
    service_identity_values: dict[str, str] = {}
    if product and product.get("service_kind"):
        if checkout_data.get("service_product_id") == product_id:
            service_username = checkout_data.get("service_username", "")
            service_identity_values = checkout_data.get("service_identity_values", {})
        if not service_username:
            await query.answer("សូមបញ្ចូល និងបញ្ជាក់ Telegram username មុនសិន។", show_alert=True)
            if state is None:
                return
            await state.clear()
            await state.update_data(service_product_id=product_id)
            await state.set_state(ServiceUsernameForm.username)
            await query.message.answer("សូមផ្ញើយូសនាម Telegram របស់អ្នក ដោយមានឬគ្មាន @ ក៏បាន៖")
            return
        if checkout_data.get("service_pending_product_id") == product_id:
            await query.answer("Order សេវានេះកំពុងរង់ចាំការបង់ប្រាក់រួចហើយ។", show_alert=True)
            return
        if any(not service_identity_values.get(field) for field in product.get("identity_fields", [])):
            await query.answer("សូមបញ្ចូល Game ID ឱ្យគ្រប់មុនបង់ប្រាក់។", show_alert=True)
            return
    if payment_method == "wallet":
        if service_username:
            paid_order = await store.pay_product_with_wallet(
                product_id,
                query.from_user.id,
                service_username,
                service_identity_values,
            )
        else:
            paid_order = await store.pay_product_with_wallet(product_id, query.from_user.id)
        if paid_order["status"] == "unavailable":
            await query.answer("ទំនិញនេះត្រូវបានលក់ ឬកក់រួចហើយ។", show_alert=True)
            return
        if paid_order["status"] == "insufficient":
            shortfall = paid_order["price_cents"] - paid_order["balance_cents"]
            await query.answer("សមតុល្យកាបូបមិនគ្រប់គ្រាន់ទេ។", show_alert=True)
            await query.message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"{_custom_emoji('wallet_topup', '💰')} <b>សមតុល្យមិនគ្រប់គ្រាន់</b>\n"
                f"សមតុល្យបច្ចុប្បន្ន: <b>{_price(paid_order['balance_cents'])}</b>\n"
                f"តម្លៃទំនិញ: <b>{_price(paid_order['price_cents'])}</b>\n"
                f"នៅខ្វះ: <b>{_price(shortfall)}</b>\n"
                "សូមបញ្ចូលប្រាក់ឱ្យគ្រប់ រួចត្រឡប់មកទិញម្ដងទៀត។\n"
                "━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML,
                reply_markup=wallet_topup_keyboard(),
            )
            return
        await query.answer("បានកាត់ប្រាក់ពីកាបូប និងបង់ជោគជ័យ។")
        if state is not None:
            await state.clear()
        await _deliver_paid_account(
            bot,
            paid_order,
            admin_ids,
            payment_method="កាបូបប្រាក់ក្នុង bot",
        )
        return

    error = _payment_setup_error(payment_config)
    if error:
        await query.answer(error, show_alert=True)
        return
    await query.answer("កំពុងបង្កើត KHQR...")
    await _create_product_payment(
        query,
        bot,
        store,
        admin_ids,
        payment_config,
        product_id,
        payment_method,
        service_username,
        state,
        service_identity_values,
    )


async def _create_product_payment(
    query: CallbackQuery,
    bot: Bot,
    store: Store,
    admin_ids: frozenset[int],
    payment_config: dict[str, str],
    product_id: int,
    payment_method: str,
    service_username: str = "",
    state: FSMContext | None = None,
    identity_values: dict[str, str] | None = None,
) -> None:
    if not isinstance(query.message, Message):
        return
    secret_key = payment_config.get("secret_key", "").strip()
    if service_username:
        order = await store.create_order(
            product_id,
            query.from_user.id,
            service_username,
            identity_values or {},
        )
    else:
        order = await store.create_order(product_id, query.from_user.id)
    if order is None:
        await query.message.answer("account នេះទើបត្រូវបានកក់ ឬលក់រួចហើយ។ សូមជ្រើសរើស account ផ្សេង។")
        return

    transaction_id = f"ROKIRA_{order['id']}_{uuid4().hex[:8]}"
    success_url = payment_config["success_url"]
    remark = f"ROKIRA order {order['id']}"
    try:
        direct_qr = await request_direct_qr(
            profile_id=payment_config["profile_id"],
            secret_key=secret_key,
            transaction_id=transaction_id,
            amount_cents=order["price_cents"],
            success_url=success_url,
            remark=remark,
        )
        qr_png = (
            render_payment_card_png(direct_qr.content, order["price_cents"], transaction_id)
            if direct_qr.content
            else await download_direct_qr_image(direct_qr.image_url)
        )
    except (ValueError, aiohttp.ClientError, TimeoutError) as error:
        await store.cancel_order(order["id"])
        logger.warning("Could not create KHQR for order %s: %s", order["id"], error)
        await query.message.answer(
            "KHQRcc មិនបានផ្ញើ QR ត្រឹមត្រូវមកទេ។ Order ត្រូវបានបោះបង់ ហើយ account ត្រឡប់ទៅ stock វិញ។ សូមទាក់ទង admin។"
        )
        await _notify_admins(
            bot,
            admin_ids,
            f"មិនអាចបង្កើត KHQR សម្រាប់ order #{order['id']} បានទេ។ "
            f"មូលហេតុ: <code>{escape(str(error)[:180])}</code>",
        )
        return

    if not await store.set_payment_transaction(order["id"], transaction_id):
        await store.cancel_order(order["id"])
        await query.message.answer("Order ផុតកំណត់ពេលបង្កើត QR; account ត្រឡប់ទៅ stock វិញហើយ។ សូមសាកល្បងម្ដងទៀត។")
        return

    if service_username and state is not None:
        await state.update_data(
            service_pending_product_id=product_id,
            service_pending_order_id=order["id"],
        )
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass

    username_line = (
        f"Telegram username: <code>{escape(order['telegram_username'])}</code>\n"
        if order.get("telegram_username")
        else ""
    )
    identity_line = "".join(
        f"{escape(label)}: <code>{escape(value)}</code>\n"
        for label, value in order.get("identity_values", {}).items()
    )
    await query.message.answer_photo(
        photo=BufferedInputFile(qr_png, filename=f"rokira-khqr-{order['id']}.png"),
        caption=(
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{_custom_emoji('wallet', '💳')} <b>ស្កេន QR ដើម្បីបង់ប្រាក់ · Order #{order['id']}</b>\n"
        f"{_html_field(order['game_name'], order.get('game_name_html'))} · "
        f"{_html_field(order['level_name'], order.get('level_name_html'))}\n"
        f"{_html_field(order['name'], order.get('name_html'))} · <b>{_price(order['price_cents'])}</b>\n"
        f"{username_line}\n"
        f"{identity_line}\n"
        f"កំណត់ពេលបង់៖ {ORDER_RESERVATION_MINUTES} នាទី · ផុតកំណត់ម៉ោង {_cambodia_time(order['expires_at'])} (ម៉ោងកម្ពុជា)\n"
        "ស្ថានភាព៖ មិនទាន់បានបញ្ជាក់ការបង់ប្រាក់ទេ។\n"
        f"វិធីបង់ដែលបានជ្រើស៖ <b>{'ABA Mobile' if payment_method == 'aba' else 'KHQR តាមធនាគារដែលគាំទ្រ'}</b>\n"
        "ស្កេន QR នេះពី app ធនាគារដែលបានជ្រើស។ Account នឹងផ្ញើក្រោយ Verify បញ្ជាក់ការបង់ប្រាក់ និង amount ត្រូវគ្នាប៉ុណ្ណោះ។\n"
        "━━━━━━━━━━━━━━━━━━━━"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=payment_qr_keyboard(order["id"], payment_method),
    )
    admin_text = (
        f"<b>មាន order ថ្មី #{order['id']}</b>\n"
        f"អតិថិជន: <code>{order['user_id']}</code>\n"
        f"{_html_field(order['game_name'], order.get('game_name_html'))} · "
        f"{_html_field(order['level_name'], order.get('level_name_html'))}\n"
        f"{_html_field(order['name'], order.get('name_html'))} · <b>{_price(order['price_cents'])}</b>\n"
        f"{username_line}"
        f"{identity_line}"
        f"កក់ផុតកំណត់: <code>{escape(order['expires_at'])}</code>\n\n"
        f"Transaction: <code>{escape(transaction_id)}</code>\n"
        f"Payment method: {escape(payment_method)}\n"
        "Status: PENDING។ មិនទាន់បានបញ្ជាក់ថាបង់រួចទេ; Account នឹងមិនចេញរហូតដល់ Verify V2 បញ្ជាក់ payment និង amount ត្រូវគ្នា។"
    )
    await _notify_admins(bot, admin_ids, admin_text, pending_order_actions(order["id"]))


@user_router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_own_order(query: CallbackQuery, bot: Bot, store: Store) -> None:
    try:
        order_id = int((query.data or "").partition(":")[2])
    except ValueError:
        await query.answer("Order មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    order = await store.cancel_order(order_id, user_id=query.from_user.id)
    if order is None:
        await query.answer("Order នេះមិនអាចបោះបង់បានទេ។", show_alert=True)
        return
    await query.answer("បានបោះបង់ការកក់")
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer("បានបោះបង់ការកក់។ Account ត្រឡប់ទៅ stock វិញហើយ។")


@user_router.callback_query(F.data == "my_orders")
async def show_my_orders(query: CallbackQuery, store: Store) -> None:
    await query.answer()
    orders = await store.order_history(query.from_user.id)
    if not isinstance(query.message, Message):
        return
    if not orders:
        await query.message.answer("អ្នកមិនទាន់មានប្រវត្តិបញ្ជាទិញទេ។")
        return
    statuses = {
        "pending": "កំពុងរង់ចាំការបង់ប្រាក់",
        "paid": "បានបង់ប្រាក់",
        "cancelled": "បានបោះបង់",
        "expired": "ផុតកំណត់",
    }
    lines = ["<b>ការបញ្ជាទិញរបស់អ្នក</b>"]
    for order in orders:
        lines.append(
            f"#{order['id']} · {_html_field(order['name'], order.get('name_html'))} · {_price(order['price_cents'])} · "
            f"{statuses.get(order['status'], order['status'])}"
        )
    await query.message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@user_router.callback_query(F.data == "profile")
async def show_profile(query: CallbackQuery) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(
            _profile_text(query.from_user.full_name, query.from_user.username, query.from_user.id),
            parse_mode=ParseMode.HTML,
        )


@user_router.callback_query(F.data == "support")
async def show_support(
    query: CallbackQuery,
    store: Store,
    admin_ids: frozenset[int],
    admin_contact_user_id: int | None = None,
) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        username = await store.get_setting("support_username", ADMIN_CONTACT_USERNAME)
        await _send_admin_contact(query.bot, query.message.chat.id, admin_ids, admin_contact_user_id, username)


@user_router.callback_query(F.data == "guide")
async def show_guide(query: CallbackQuery) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(
            "របៀបបញ្ជាទិញ៖\n"
            "១. ចុច បញ្ជាទិញទំនិញ ហើយជ្រើស game។\n"
            "២. រូបភាព ព័ត៌មាន level និងតម្លៃ account ក្នុង game នោះនឹងបង្ហាញភ្លាមៗ។\n"
            "៣. ចុច ទិញឥឡូវនេះ លើ account ដែលចង់បាន។\n"
            "៤. បង់តាម KHQR។ ក្រោយពេលប្រព័ន្ធផ្ទៀងផ្ទាត់ជោគជ័យ Bot នឹងផ្ញើព័ត៌មានចូល account ជូន។"
        )


@admin_router.message(Command("admin"))
async def open_admin_panel(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមបើកផ្ទាំង admin ក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())


async def _show_admin_contact_settings(message: Message, store: Store) -> None:
    support_username = await store.get_setting("support_username", ADMIN_CONTACT_USERNAME)
    channel_url = await store.get_setting("channel_url", DEFAULT_CHANNEL_URL)
    await message.answer(
        "<b>កែព័ត៌មានជំនួយ និងឆានែល</b>\n"
        f"ជំនួយ៖ <code>@{escape(support_username.lstrip('@'))}</code>\n"
        f"ឆានែល៖ <code>{escape(channel_url)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_contact_settings_menu(),
    )


@admin_router.message(F.text == ADMIN_CONTACT_SETTINGS)
async def open_admin_contact_settings(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមកែព័ត៌មានក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await _show_admin_contact_settings(message, store)


@admin_router.message(F.text == ADMIN_EDIT_SUPPORT)
async def begin_edit_support_username(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមកែព័ត៌មានក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await state.set_state(AdminContactSettingsForm.support_username)
    await message.answer(
        "សូមផ្ញើ Telegram username សម្រាប់ផ្នែកជំនួយ (ឧទាហរណ៍ @ROVISTARCAMBO)៖",
        reply_markup=admin_settings_input_menu(),
    )


@admin_router.message(AdminContactSettingsForm.support_username, F.text)
async def save_support_username(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    if message.text == ADMIN_SETTINGS_BACK:
        await state.clear()
        await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())
        return
    if message.text == ADMIN_CANCEL:
        await state.clear()
        await _show_admin_contact_settings(message, store)
        return
    username = _normalize_support_username(message.text or "")
    if username is None:
        await message.answer("Username មិនត្រឹមត្រូវទេ។ សូមផ្ញើ @username ដែលមាន ៥–៣២ តួអក្សរ។")
        return
    await store.set_setting("support_username", username)
    await state.clear()
    await _show_admin_contact_settings(message, store)


@admin_router.message(F.text == ADMIN_EDIT_CHANNEL)
async def begin_edit_channel_url(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមកែព័ត៌មានក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await state.set_state(AdminContactSettingsForm.channel_url)
    await message.answer(
        "សូមផ្ញើ @username ឬតំណ https://t.me/... សម្រាប់ឆានែល៖",
        reply_markup=admin_settings_input_menu(),
    )


@admin_router.message(AdminContactSettingsForm.channel_url, F.text)
async def save_channel_url(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    if message.text == ADMIN_SETTINGS_BACK:
        await state.clear()
        await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())
        return
    if message.text == ADMIN_CANCEL:
        await state.clear()
        await _show_admin_contact_settings(message, store)
        return
    channel_url = _normalize_channel_url(message.text or "")
    if channel_url is None:
        await message.answer("តំណមិនត្រឹមត្រូវទេ។ សូមប្រើ @username ឬ https://t.me/username។")
        return
    await store.set_setting("channel_url", channel_url)
    await state.clear()
    await _show_admin_contact_settings(message, store)


@admin_router.message(F.text == ADMIN_SETTINGS_BACK)
async def close_admin_contact_settings(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    await state.clear()
    await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())


async def _show_admin_khqr_settings(message: Message, store: Store, payment_config: dict[str, str]) -> None:
    profile_id = await store.get_setting("khqr_profile_id", payment_config.get("profile_id", ""))
    secret_key = await store.get_secret_setting("khqr_secret_key", payment_config.get("secret_key", ""))
    master_admin_username = await store.get_setting("master_admin_username", ADMIN_CONTACT_USERNAME)
    await message.answer(
        "<b>ការកំណត់ KHQRcc</b>\n"
        f"Profile ID៖ <code>{escape(profile_id) if profile_id else 'មិនទាន់កំណត់'}</code>\n"
        f"Secret Key៖ <b>{'បានកំណត់' if secret_key else 'មិនទាន់កំណត់'}</b>\n"
        "Secret Key មិនត្រូវបានបង្ហាញក្នុងសារទេ។",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_khqr_settings_menu(),
    )
    await message.answer(
        "សម្រាប់ជំនួយ សូមឆាតទៅ Admin មេ៖",
        reply_markup=admin_panel_shortcut_keyboard(master_admin_username),
    )


async def _read_button_emoji_overrides(store: Store) -> dict[str, str]:
    raw = await store.get_setting("button_emoji_overrides", "{}")
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return values if isinstance(values, dict) else {}


@admin_router.message(F.text == ADMIN_BUTTON_EMOJI)
async def begin_button_emoji_setup(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    await state.clear()
    await state.set_state(AdminButtonEmojiForm.emoji_id)
    await message.answer(
        "ផ្ញើ custom emoji ឬ Emoji ID ពី @emojiid_get_bot។ បន្ទាប់មកជ្រើសប៊ូតុងពីបញ្ជីខាងក្រោម។\n"
        "ផ្ញើ <code>0</code> ដើម្បីដក Emoji ពីគោលដៅដែលអ្នកជ្រើស។",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_settings_input_menu(),
    )


@admin_router.message(AdminButtonEmojiForm.emoji_id, F.text)
async def receive_button_emoji_id(message: Message, state: FSMContext, store: Store, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    value = (message.text or "").strip()
    if value in {ADMIN_CANCEL, ADMIN_SETTINGS_BACK}:
        await state.clear()
        await message.answer("បានចាកចេញពីការកំណត់ Emoji។", reply_markup=admin_menu())
        return
    emoji_id = next(
        (entity.custom_emoji_id for entity in (message.entities or []) if str(entity.type) == "custom_emoji"),
        value,
    )
    if emoji_id != "0" and not emoji_id.isdecimal():
        match = re.search(r"(?<!\d)(\d{8,32})(?!\d)", emoji_id)
        if match:
            emoji_id = match.group(1)
    if emoji_id != "0" and (not emoji_id.isdecimal() or len(emoji_id) > 32):
        await message.answer("Emoji ID មិនត្រឹមត្រូវទេ។ ផ្ញើ custom emoji ឬ ID លេខពី @emojiid_get_bot។")
        return
    await state.update_data(button_emoji_pending_id=emoji_id)
    await state.set_state(None)
    await message.answer(
        "ជ្រើសរើសប៊ូតុងដែលចង់ប្ដូរ Emoji៖",
        reply_markup=button_emoji_targets_keyboard(0, await _read_button_emoji_overrides(store)),
    )


@admin_router.callback_query(F.data.startswith("admin:button_emoji:page:"))
async def page_button_emoji_targets(query: CallbackQuery, store: Store, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids) or not isinstance(query.message, Message):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    try:
        page = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("ទំព័រមិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    await query.answer()
    await query.message.edit_reply_markup(
        reply_markup=button_emoji_targets_keyboard(page, await _read_button_emoji_overrides(store))
    )


@admin_router.callback_query(F.data == "admin:button_emoji:cancel")
async def cancel_button_emoji_setup(query: CallbackQuery, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    await state.clear()
    await query.answer("បានបោះបង់")
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)


@admin_router.callback_query(F.data == "admin:button_emoji:reset_all")
async def reset_all_button_emojis(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    await store.set_setting("button_emoji_overrides", "{}")
    await store.set_setting("product_button_emoji_id", "")
    await state.clear()
    await query.answer("បាន reset Emoji ប៊ូតុងទាំងអស់")
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer("បាន reset Emoji ទាំងអស់ត្រឡប់ទៅដើម។", reply_markup=admin_menu())


@admin_router.callback_query(F.data.startswith("admin:button_emoji:target:"))
async def apply_button_emoji_target(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    target = (query.data or "").rsplit(":", 1)[1]
    if target not in BUTTON_EMOJI_TARGET_LABELS:
        await query.answer("ប៊ូតុងគោលដៅមិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    data = await state.get_data()
    emoji_id = data.get("button_emoji_pending_id")
    if not isinstance(emoji_id, str):
        await query.answer("សូមផ្ញើ Emoji ID ម្តងទៀត។", show_alert=True)
        return
    overrides = await _read_button_emoji_overrides(store)
    if emoji_id == "0":
        overrides.pop(target, None)
    else:
        overrides[target] = emoji_id
    await store.set_setting("button_emoji_overrides", json.dumps(overrides, ensure_ascii=True, sort_keys=True))
    if target == "product":
        await store.set_setting("product_button_emoji_id", "")
    await state.clear()
    await query.answer("បានរក្សាទុក Emoji")
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
        action = "បានដក Emoji" if emoji_id == "0" else "បានកំណត់ Emoji"
        await query.message.answer(
            f"{action} សម្រាប់ «{BUTTON_EMOJI_TARGET_LABELS[target]}» រួចរាល់។ អ្នកអាចប្ដូរវិញពេលណាក៏បាន។",
            reply_markup=admin_menu(),
        )


@admin_router.message(F.text == ADMIN_KHQR_SETTINGS)
async def open_admin_khqr_settings(
    message: Message,
    state: FSMContext,
    store: Store,
    payment_config: dict[str, str],
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមកែ KHQR ក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await _show_admin_khqr_settings(message, store, payment_config)


@admin_router.message(F.text == ADMIN_KHQR_PROFILE)
async def begin_edit_khqr_profile(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    await state.clear()
    await state.set_state(AdminKhqrSettingsForm.profile_id)
    await message.answer("សូមផ្ញើ KHQRCC_PROFILE_ID ថ្មី៖", reply_markup=admin_settings_input_menu())


@admin_router.message(AdminKhqrSettingsForm.profile_id, F.text)
async def save_khqr_profile(
    message: Message,
    state: FSMContext,
    store: Store,
    payment_config: dict[str, str],
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    if message.text == ADMIN_SETTINGS_BACK:
        await state.clear()
        await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())
        return
    if message.text == ADMIN_CANCEL:
        await state.clear()
        await _show_admin_khqr_settings(message, store, payment_config)
        return
    profile_id = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", profile_id):
        await message.answer("Profile ID មិនត្រឹមត្រូវទេ។ សូមផ្ញើ ID ដែលមាន ៦–១២៨ តួអក្សរ។")
        return
    await store.set_setting("khqr_profile_id", profile_id)
    payment_config["profile_id"] = profile_id
    if payment_config.get("verify_url_overridden", "false").lower() != "true":
        payment_config["verify_url"] = (
            f"https://khqr.cc/api/{profile_id}/payment-gateway/v1/payments/check-transv2-khqrcc"
        )
    await state.clear()
    await _show_admin_khqr_settings(message, store, payment_config)


@admin_router.message(F.text == ADMIN_KHQR_SECRET)
async def begin_edit_khqr_secret(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    await state.clear()
    await state.set_state(AdminKhqrSettingsForm.secret_key)
    await message.answer(
        "សូមផ្ញើ KHQRCC_SECRET_KEY ថ្មី។ Bot នឹងលុបសារនេះបន្ទាប់ពីទទួលបាន។",
        reply_markup=admin_settings_input_menu(),
    )


@admin_router.message(AdminKhqrSettingsForm.secret_key, F.text)
async def save_khqr_secret(
    message: Message,
    state: FSMContext,
    store: Store,
    payment_config: dict[str, str],
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    secret_key = (message.text or "").strip()
    if message.text in {ADMIN_SETTINGS_BACK, ADMIN_CANCEL}:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await state.clear()
        if message.text == ADMIN_SETTINGS_BACK:
            await message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())
        else:
            await _show_admin_khqr_settings(message, store, payment_config)
        return
    try:
        await message.delete()
    except TelegramAPIError:
        logger.warning("Could not delete a KHQR secret message from admin chat")
    if not 8 <= len(secret_key) <= 512:
        await message.answer("Secret Key មិនត្រឹមត្រូវទេ។ សូមពិនិត្យប្រវែង ហើយផ្ញើម្ដងទៀត។")
        return
    await store.set_secret_setting("khqr_secret_key", secret_key)
    payment_config["secret_key"] = secret_key
    await state.clear()
    await _show_admin_khqr_settings(message, store, payment_config)


async def _broadcast_to_users(bot: Bot, store: Store, text: str) -> tuple[int, int]:
    sent = 0
    failed = 0
    for user_id in await store.user_ids():
        try:
            await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
            sent += 1
        except TelegramAPIError:
            failed += 1
        await asyncio.sleep(0.04)
    return sent, failed


@admin_router.message(F.text == ADMIN_BROADCAST)
async def begin_broadcast(message: Message, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមប្រើការផ្ញើសារក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await state.set_state(BroadcastForm.message)
    await message.answer("សូមផ្ញើសារដែលចង់ផ្សាយទៅអ្នកប្រើទាំងអស់៖", reply_markup=ReplyKeyboardRemove())


@admin_router.message(BroadcastForm.message, F.text)
async def send_broadcast(
    message: Message,
    state: FSMContext,
    store: Store,
    bot: Bot,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    text = (message.html_text or "").strip()
    if not text:
        await message.answer("សារមិនអាចទទេបានទេ។ សូមផ្ញើសារម្ដងទៀត។")
        return
    await state.clear()
    sent, failed = await _broadcast_to_users(bot, store, text)
    await message.answer(
        f"បានផ្ញើសារទៅអ្នកប្រើ {sent} នាក់។ មិនអាចផ្ញើបាន {failed} នាក់។",
        reply_markup=admin_menu(),
    )


@admin_router.message(F.text == ADMIN_CREATE_DISCOUNT)
async def begin_create_discount(message: Message, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមបង្កើតកូដក្នុង private chat ជាមួយ bot។")
        return
    await state.clear()
    await state.set_state(DiscountCodeForm.code)
    await message.answer("សូមបញ្ចូលកូដជាអក្សរ ឬលេខ ៣–៣២ តួ (ឧទាហរណ៍ SAVE10)៖", reply_markup=ReplyKeyboardRemove())


@admin_router.message(DiscountCodeForm.code, F.text)
async def receive_discount_code(message: Message, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    code = _normalize_discount_code(message.text or "")
    if not 3 <= len(code) <= 32:
        await message.answer("កូដត្រូវមានអក្សរ ឬលេខ ៣–៣២ តួ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    await state.update_data(discount_code=code)
    await state.set_state(DiscountCodeForm.percent)
    await message.answer("សូមបញ្ចូលភាគរយបញ្ចុះតម្លៃ ចាប់ពី ១ ដល់ ៩០៖")


@admin_router.message(DiscountCodeForm.percent, F.text)
async def receive_discount_percent(
    message: Message,
    state: FSMContext,
    store: Store,
    bot: Bot,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    try:
        percent = int((message.text or "").strip())
    except ValueError:
        percent = 0
    if not 1 <= percent <= 90:
        await message.answer("ភាគរយត្រូវជាលេខពី ១ ដល់ ៩០។ សូមបញ្ចូលម្ដងទៀត។")
        return
    code = str((await state.get_data()).get("discount_code", ""))
    created = await store.create_discount_code(code, percent, message.from_user.id)
    if not created:
        await state.set_state(DiscountCodeForm.code)
        await message.answer("កូដនេះមានរួចហើយ។ សូមបញ្ចូលកូដថ្មី៖")
        return
    await state.clear()
    notice = (
        f"កូដបញ្ចុះតម្លៃថ្មី: <code>{escape(code)}</code>\n"
        f"បញ្ចុះ {percent}% លើការទិញបន្ទាប់។ សូមចុច «បញ្ចូលកូដបញ្ចុះតម្លៃ» ក្នុងម៉ឺនុយ រួចបញ្ចូលកូដនេះ។"
    )
    sent, failed = await _broadcast_to_users(bot, store, notice)
    await message.answer(
        f"បានបង្កើតកូដ និងផ្សាយទៅអ្នកប្រើ {sent} នាក់។ មិនអាចផ្ញើបាន {failed} នាក់។",
        reply_markup=admin_menu(),
    )


@admin_router.message(F.text == ADMIN_BACK)
async def return_to_customer_menu(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
    customer_bot_mode: bool = False,
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    await state.clear()
    language = await store.user_language(message.from_user.id)
    await message.answer(
        await _welcome_for_user(
            store,
            language,
            message.from_user.full_name,
            customer_bot_mode=customer_bot_mode,
        ),
        reply_markup=await _welcome_actions_for_user(store, language),
    )
    await message.answer(
        "Choose a function from the menu below:" if language == "en" else "សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម៖",
        reply_markup=main_menu(
            include_admin=True,
            include_create_bot=not customer_bot_mode,
            language=language,
        ),
    )


@admin_router.callback_query(F.data == "admin:home")
async def admin_home(query: CallbackQuery, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    await state.clear()
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer("<b>ផ្ទាំងគ្រប់គ្រង</b>", parse_mode=ParseMode.HTML, reply_markup=admin_menu())


def _admin_account_status(status: str) -> str:
    return {
        "available": "មានក្នុង stock",
        "reserved": "កំពុងកក់",
        "sold": "បានលក់រួច",
    }.get(status, status)


async def _send_admin_account_card(message: Message, product: dict, email_preview: str | None) -> None:
    text = (
        f"<b>គ្រប់គ្រង account #{product['id']}</b>\n"
        f"ឈ្មោះ: {_html_field(product['name'], product.get('name_html'))}\n"
        f"Game: {_html_field(product['game_name'], product.get('game_name_html'))}\n"
        f"Level: {_html_field(product['level_name'], product.get('level_name_html'))}\n"
        f"តម្លៃ: <b>{_price(product['price_cents'])}</b>\n"
        f"ស្ថានភាព: {_admin_account_status(product['status'])}\n\n"
        f"Gmail: <code>{escape(email_preview or '***')}</code>\n"
        "Password: រក្សាទុកជាសម្ងាត់\n"
        "Gmail បង្ហាញជាផ្នែកដើម្បីសម្គាល់ account; password ដើមមិនបង្ហាញក្នុង chat ទេ។ អាចប្តូរទាំងពីរបាន។"
    )
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=admin_account_actions(product["id"], product["status"]),
    )


async def _delete_sensitive_message_later(message: Message) -> None:
    await asyncio.sleep(60)
    try:
        await message.delete()
    except TelegramAPIError:
        pass


def _schedule_sensitive_message_cleanup(message: Message) -> None:
    task = asyncio.create_task(_delete_sensitive_message_later(message))
    sensitive_message_cleanup_tasks.add(task)
    task.add_done_callback(sensitive_message_cleanup_tasks.discard)


@admin_router.message(F.text == ADMIN_MANAGE_ACCOUNTS)
async def show_admin_accounts(message: Message, store: Store, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមប្រើការគ្រប់គ្រង account ក្នុង private chat ជាមួយ bot។")
        return
    products = await store.admin_products()
    if not products:
        await message.answer("មិនទាន់មាន account ក្នុង database ទេ។", reply_markup=admin_menu())
        return
    await message.answer(
        "ជ្រើសរើស account ដែលចង់គ្រប់គ្រង៖",
        reply_markup=admin_accounts_keyboard(products),
    )


@admin_router.callback_query(F.data.startswith("admin:account:"))
async def show_admin_account(query: CallbackQuery, store: Store, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមប្រើការគ្រប់គ្រង account ក្នុង private chat។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("Account មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    product = next((item for item in await store.admin_products() if item["id"] == product_id), None)
    await query.answer()
    if product is None:
        await query.message.answer("រក account នេះមិនឃើញទេ។")
        return
    email_preview = await store.product_email_preview(product_id)
    await _send_admin_account_card(query.message, product, email_preview)


@admin_router.callback_query(F.data.startswith("admin:view_credentials:"))
async def reveal_admin_account_credentials(
    query: CallbackQuery,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("ព័ត៌មាននេះអាចមើលបានតែក្នុង private chat របស់ admin។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("Account មិនត្រឹមត្រូវទេ។", show_alert=True)
        return

    credentials = await store.admin_product_credentials(product_id)
    await query.answer()
    if credentials is None:
        await query.message.answer("បង្ហាញ credential បានតែ account ដែលនៅមាន stock; account នេះត្រូវបានកក់ ឬលក់រួចហើយ។")
        return
    sensitive_message = await query.message.answer(
        f"<b>ព័ត៌មាន account #{product_id}</b>\n"
        f"Gmail: <code>{escape(credentials['email'])}</code>\n"
        f"Password: <code>{escape(credentials['password'])}</code>\n\n"
        "សារនេះការពារការបញ្ជូនបន្ត និងលុបដោយស្វ័យប្រវត្តិក្នុង 60 វិនាទី។",
        parse_mode=ParseMode.HTML,
        protect_content=True,
    )
    _schedule_sensitive_message_cleanup(sensitive_message)


async def _begin_credential_change(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
    field: str,
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        await query.answer("សូមប្តូរព័ត៌មានក្នុង private chat។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("Account មិនត្រឹមត្រូវទេ។", show_alert=True)
        return
    product = next((item for item in await store.admin_products() if item["id"] == product_id), None)
    if product is None or product["status"] != "available":
        await query.answer("អាចប្តូរបានតែ account ដែលមិនទាន់លក់ ឬកំពុងកក់ទេ។", show_alert=True)
        return
    await state.clear()
    await state.update_data(credential_product_id=product_id)
    if field == "email":
        await state.set_state(AdminCredentialForm.email)
        prompt = "បញ្ចូល Gmail ថ្មី។ សារនេះនឹងត្រូវលុបចេញពី chat។"
    else:
        await state.set_state(AdminCredentialForm.password)
        prompt = "បញ្ចូល password ថ្មី។ សារនេះនឹងត្រូវលុបចេញពី chat។"
    await query.answer()
    await query.message.answer(prompt, reply_markup=ReplyKeyboardRemove())


@admin_router.callback_query(F.data.startswith("admin:edit_email:"))
async def begin_email_change(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    await _begin_credential_change(query, state, store, admin_ids, "email")


@admin_router.callback_query(F.data.startswith("admin:edit_password:"))
async def begin_password_change(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    await _begin_credential_change(query, state, store, admin_ids, "password")


@admin_router.message(AdminCredentialForm.email, F.text == ADMIN_CANCEL)
@admin_router.message(AdminCredentialForm.password, F.text == ADMIN_CANCEL)
async def cancel_credential_change(message: Message, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    await state.clear()
    await message.answer("បានបោះបង់ការប្តូរព័ត៌មាន។", reply_markup=admin_menu())


@admin_router.message(AdminCredentialForm.email, F.text)
async def receive_updated_email(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if (
        message.from_user is None
        or not _is_admin(message.from_user.id, admin_ids)
        or message.chat.type != ChatType.PRIVATE
    ):
        return
    email = (message.text or "").strip()
    if "@" not in email or len(email) > 254:
        await message.answer("Gmail មិនត្រឹមត្រូវទេ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    data = await state.get_data()
    try:
        await message.delete()
    except TelegramAPIError:
        logger.info("Could not delete an admin credential update message")
    updated = await store.update_product_email(data.get("credential_product_id", 0), email)
    await state.clear()
    await message.answer(
        "បានប្តូរ Gmail និងរក្សាទុកដោយអ៊ិនគ្រីប។" if updated else "មិនអាចប្តូរបានទេ។ Account ត្រូវបានកក់ ឬលក់រួចហើយ។",
        reply_markup=admin_menu(),
    )


@admin_router.message(AdminCredentialForm.password, F.text)
async def receive_updated_password(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if (
        message.from_user is None
        or not _is_admin(message.from_user.id, admin_ids)
        or message.chat.type != ChatType.PRIVATE
    ):
        return
    password = (message.text or "").strip()
    if not password or len(password) > 200:
        await message.answer("Password មិនត្រឹមត្រូវទេ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    data = await state.get_data()
    try:
        await message.delete()
    except TelegramAPIError:
        logger.info("Could not delete an admin credential update message")
    updated = await store.update_product_password(data.get("credential_product_id", 0), password)
    await state.clear()
    await message.answer(
        "បានប្តូរ password និងរក្សាទុកដោយអ៊ិនគ្រីប។" if updated else "មិនអាចប្តូរបានទេ។ Account ត្រូវបានកក់ ឬលក់រួចហើយ។",
        reply_markup=admin_menu(),
    )


@admin_router.message(F.text == ADMIN_ADD_PRODUCT)
async def begin_add_product(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមបើកផ្ទាំង admin ក្នុង private chat។")
        return
    await state.clear()
    await state.set_state(AdminProductForm.game)
    await message.answer("បញ្ចូលឈ្មោះ game ឧទាហរណ៍៖ Free Fire", reply_markup=ReplyKeyboardRemove())


@admin_router.message(F.text == ADMIN_TELEGRAM_SERVICES)
async def begin_telegram_services(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមគ្រប់គ្រងសេវា Telegram ក្នុង private chat។")
        return
    await state.clear()
    await message.answer(
        "ជ្រើសរើសសេវាដែលចង់បន្ថែមជម្រើស៖",
        reply_markup=admin_service_kind_menu(),
    )


@admin_router.message(F.text.in_({ADMIN_SERVICE_PREMIUM, ADMIN_SERVICE_STARS}))
async def choose_telegram_service_kind(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    service_kind = "telegram_premium" if message.text == ADMIN_SERVICE_PREMIUM else "telegram_stars"
    await state.clear()
    await state.update_data(service_kind=service_kind, service_options=[], service_option_slots=20)
    await message.answer(
        f"<b>កំណត់ជម្រើស {escape(message.text or '')}</b>\n"
        "ចុចប៊ូតុងលេខណាមួយ ដើម្បីបញ្ចូលឈ្មោះ និងតម្លៃ។ អាចពង្រីកបន្ថែមបាន។",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_service_editor(20, 0),
    )


@admin_router.callback_query(F.data.startswith("admin:service:slot:"))
async def begin_telegram_service_option(
    query: CallbackQuery,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    try:
        slot_index = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("ជម្រើសមិនត្រឹមត្រូវ។", show_alert=True)
        return
    data = await state.get_data()
    if data.get("service_kind") not in {"telegram_premium", "telegram_stars"}:
        await query.answer("សូមចាប់ផ្តើមផ្ទាំងសេវាម្ដងទៀត។", show_alert=True)
        return
    slots = int(data.get("service_option_slots", 20))
    if slot_index < 0 or slot_index >= slots:
        await query.answer("ប៊ូតុងនេះមិនមានទៀតទេ។", show_alert=True)
        return
    await state.update_data(service_editing_index=slot_index)
    await state.set_state(AdminTelegramServiceForm.name)
    await query.answer()
    await query.message.answer(f"បញ្ចូលឈ្មោះសម្រាប់ជម្រើស #{slot_index + 1}៖", reply_markup=ReplyKeyboardRemove())


@admin_router.callback_query(F.data == "admin:service:add10")
async def add_ten_telegram_service_slots(
    query: CallbackQuery,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    data = await state.get_data()
    if data.get("service_kind") not in {"telegram_premium", "telegram_stars"}:
        await query.answer("សូមចាប់ផ្តើមផ្ទាំងសេវាម្ដងទៀត។", show_alert=True)
        return
    slots = min(100, int(data.get("service_option_slots", 20)) + 10)
    options = data.get("service_options", [])
    await state.update_data(service_option_slots=slots)
    await query.answer("បានបន្ថែមប៊ូតុង 10")
    await query.message.edit_reply_markup(reply_markup=admin_service_editor(slots, len(options)))


@admin_router.callback_query(F.data == "admin:service:publish")
async def publish_telegram_service_options(
    query: CallbackQuery,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    data = await state.get_data()
    options = [option for option in data.get("service_options", []) if option]
    service_kind = data.get("service_kind")
    if service_kind not in {"telegram_premium", "telegram_stars"} or not options:
        await query.answer("សូមបញ្ចូលយ៉ាងតិចមួយជម្រើសសិន។", show_alert=True)
        return
    if await state.get_state() in {
        AdminTelegramServiceForm.name.state,
        AdminTelegramServiceForm.price.state,
    }:
        await query.answer("សូមបញ្ចប់ការបញ្ចូលជម្រើសបច្ចុប្បន្នសិន។", show_alert=True)
        return
    service_title = "Telegram Premium" if service_kind == "telegram_premium" else "Telegram Stars"
    created_ids = []
    for option in options:
        created_ids.append(
            await store.create_product(
                game=service_title,
                level="សេវាកម្ម",
                name=option["name"],
                price_cents=option["price_cents"],
                photo_file_ids=[],
                account_email="",
                account_password="",
                created_by=query.from_user.id,
                name_html=option.get("name_html", ""),
                service_kind=service_kind,
            )
        )
    await state.clear()
    await query.answer("បាន Publish ជោគជ័យ")
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.answer(
        f"បាន Publish {len(created_ids)} ជម្រើសក្នុង {service_title} រួចរាល់។",
        reply_markup=admin_menu(),
    )


@admin_router.callback_query(F.data == "admin:service:cancel")
async def cancel_telegram_service_options(
    query: CallbackQuery,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    if not isinstance(query.message, Message) or query.message.chat.type != ChatType.PRIVATE:
        return
    await state.clear()
    await query.answer("បានបោះបង់")
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.answer("បានបោះបង់ការបង្កើតជម្រើសសេវា។", reply_markup=admin_menu())


@admin_router.message(AdminTelegramServiceForm.name, F.text)
async def receive_telegram_service_name(message: Message, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 80:
        await message.answer("ឈ្មោះជម្រើសត្រូវមាន ១–៨០ តួអក្សរ។")
        return
    await state.update_data(name=name, name_html=(message.html_text or escape(name)).strip())
    await state.set_state(AdminTelegramServiceForm.price)
    await message.answer("បញ្ចូលតម្លៃ USD ឧទាហរណ៍៖ 5.00", reply_markup=ReplyKeyboardRemove())


@admin_router.message(AdminTelegramServiceForm.price, F.text)
async def receive_telegram_service_price(
    message: Message,
    state: FSMContext,
    store: Store,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    raw_price = (message.text or "").strip().replace("$", "").replace("USD", "").strip()
    try:
        amount = Decimal(raw_price)
        if not amount.is_finite():
            raise InvalidOperation
        price_cents = int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    except (InvalidOperation, ValueError, OverflowError):
        await message.answer("តម្លៃមិនត្រឹមត្រូវទេ។ សូមបញ្ចូល USD ឧទាហរណ៍ 5.00។")
        return
    if not 1 <= price_cents <= 100_000_000:
        await message.answer("តម្លៃត្រូវធំជាង 0 និងមិនលើស $1,000,000។")
        return
    data = await state.get_data()
    service_kind = data.get("service_kind")
    if service_kind not in {"telegram_premium", "telegram_stars"}:
        await state.clear()
        await message.answer("សំណើមិនត្រឹមត្រូវ។ សូមបើកផ្ទាំងសេវា Telegram ម្ដងទៀត។", reply_markup=admin_menu())
        return
    options = list(data.get("service_options", []))
    slot_index = int(data.get("service_editing_index", 0))
    option = {
        "name": data["name"],
        "name_html": data.get("name_html", ""),
        "price_cents": price_cents,
    }
    if slot_index < len(options):
        options[slot_index] = option
    else:
        options.extend({} for _ in range(len(options), slot_index))
        options.append(option)
    slots = int(data.get("service_option_slots", 20))
    await state.update_data(service_options=options)
    await state.set_state(None)
    service_title = "Telegram Premium" if service_kind == "telegram_premium" else "Telegram Stars"
    await message.answer(
        f"<b>{service_title}</b> · បានបញ្ចូល {sum(bool(item) for item in options)} ជម្រើស។",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_service_editor(slots, sum(bool(item) for item in options)),
    )


@admin_router.message(AdminProductForm.game, F.text)
async def receive_game(message: Message, state: FSMContext) -> None:
    game = (message.text or "").strip()
    if not 2 <= len(game) <= 40:
        await message.answer("ឈ្មោះ game ត្រូវមាន ២–៤០ តួអក្សរ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    await state.update_data(game=game, game_html=(message.html_text or escape(game)).strip())
    await state.set_state(AdminProductForm.kind)
    await message.answer("ជ្រើសប្រភេទ listing៖", reply_markup=admin_product_kind_menu())


async def _request_product_level(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminProductForm.level)
    await message.answer("បញ្ចូលប្រភេទ/គម្រោង ឧទាហរណ៍៖ Level 50 ឬ Package 1000 Diamonds", reply_markup=ReplyKeyboardRemove())


@admin_router.message(AdminProductForm.kind, F.text)
async def choose_product_kind(message: Message, state: FSMContext, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    if message.text == ADMIN_CANCEL:
        await state.clear()
        await message.answer("បានបោះបង់។", reply_markup=admin_menu())
        return
    kinds = {
        ADMIN_ITEM_ACCOUNT: ("account", ""),
        ADMIN_ITEM_SERVICE: ("service", "game_service"),
        ADMIN_ITEM_PRO: ("pro", ""),
    }
    selected = kinds.get(message.text or "")
    if selected is None:
        await message.answer("សូមជ្រើសប្រភេទពីប៊ូតុងខាងក្រោម។", reply_markup=admin_product_kind_menu())
        return
    item_kind, service_kind = selected
    await state.update_data(product_kind=item_kind, service_kind=service_kind, identity_fields=[])
    if item_kind == "service":
        await state.set_state(AdminProductForm.identity_fields)
        await message.answer(
            "សេវា Game ត្រូវការ Telegram username និង ID របស់ game។ ជ្រើសរើស field បន្ថែម៖",
            reply_markup=admin_service_identity_menu(),
        )
        return
    await _request_product_level(message, state)


@admin_router.message(AdminProductForm.identity_fields, F.text)
async def choose_service_identity_fields(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        return
    if message.text == ADMIN_CANCEL:
        await state.clear()
        await message.answer("បានបោះបង់។", reply_markup=admin_menu())
        return
    fields = {
        ADMIN_SERVICE_UID: ["Game UID"],
        ADMIN_SERVICE_TWO_IDS: ["Game ID", "Zone/Server ID"],
    }.get(message.text or "")
    if fields is None:
        await message.answer("សូមជ្រើស field ពីប៊ូតុងខាងក្រោម។", reply_markup=admin_service_identity_menu())
        return
    await state.update_data(identity_fields=fields)
    await _request_product_level(message, state)


@admin_router.message(AdminProductForm.level, F.text)
async def receive_level(message: Message, state: FSMContext) -> None:
    level = (message.text or "").strip()
    if not 1 <= len(level) <= 40:
        await message.answer("Level ត្រូវមាន ១–៤០ តួអក្សរ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    await state.update_data(
        level=level,
        level_html=(message.html_text or escape(level)).strip(),
        photo_file_ids=[],
    )
    await state.set_state(AdminProductForm.photos)
    await message.answer(
        "ផ្ញើរូប account ចំនួនប៉ុន្មានក៏បាន (យ៉ាងតិច ១ សន្លឹក)។ អាចផ្ញើជា album ឬផ្ញើម្តងមួយបាន។\n"
        "ពេលរួចរាល់ សូមចុចប៊ូតុងខាងក្រោម។",
        reply_markup=photo_done(),
    )


@admin_router.message(AdminProductForm.photos, F.photo)
async def receive_product_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = data.get("photo_file_ids", [])
    photo_id = message.photo[-1].file_id
    if photo_id not in photos:
        photos.append(photo_id)
        await state.update_data(photo_file_ids=photos)


@admin_router.message(AdminProductForm.photos, F.text == ADMIN_PHOTOS_DONE)
async def finish_product_photos(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    data = await state.get_data()
    photo_count = len(data.get("photo_file_ids", []))
    if photo_count < 1:
        await message.answer("សូមផ្ញើយ៉ាងតិច ១ សន្លឹកសិន។")
        return
    await state.set_state(AdminProductForm.name)
    await message.answer("បញ្ចូលឈ្មោះ account ដែលអ្នកទិញនឹងឃើញ។", reply_markup=ReplyKeyboardRemove())


@admin_router.message(AdminProductForm.name, F.text)
async def receive_product_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 80:
        await message.answer("ឈ្មោះ account ត្រូវមាន ២–៨០ តួអក្សរ។")
        return
    await state.update_data(name=name, name_html=(message.html_text or escape(name)).strip())
    await state.set_state(AdminProductForm.description)
    await message.answer(
        "សរសេរការពិពណ៌នាផ្សព្វផ្សាយដែលអតិថិជននឹងឃើញ។ អាចច្រើនបន្ទាត់ មាន bold, link និង custom emoji បាន។\n"
        "កំណត់ត្រឹម ៦០០ តួអក្សរ ឬចុចរំលង។",
        reply_markup=description_skip(),
    )


async def _request_product_price(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminProductForm.price)
    await message.answer("បញ្ចូលតម្លៃ USD ឧទាហរណ៍៖ 5.00", reply_markup=ReplyKeyboardRemove())


@admin_router.message(AdminProductForm.description, F.text == ADMIN_DESCRIPTION_SKIP)
async def skip_product_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description="", description_html="")
    await _request_product_price(message, state)


@admin_router.message(AdminProductForm.description, F.text)
async def receive_product_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description.encode("utf-16-le")) // 2 > 600:
        await message.answer("ការពិពណ៌នាវែងពេក។ សូមកំណត់ត្រឹម ៦០០ តួអក្សរ ឬចុចរំលង។")
        return
    await state.update_data(
        description=description,
        description_html=(message.html_text or escape(description)).strip(),
    )
    await _request_product_price(message, state)


@admin_router.message(AdminProductForm.price, F.text)
async def receive_product_price(message: Message, state: FSMContext) -> None:
    raw_price = (message.text or "").strip().replace("$", "").replace("USD", "").strip()
    try:
        parsed_price = Decimal(raw_price)
        if not parsed_price.is_finite():
            raise InvalidOperation
        amount = parsed_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        price_cents = int(amount * 100)
    except (InvalidOperation, ValueError, OverflowError):
        await message.answer("តម្លៃមិនត្រឹមត្រូវទេ។ សូមបញ្ចូលជាលេខ USD ឧទាហរណ៍ 5.00។")
        return
    if price_cents <= 0 or price_cents > 100_000_000:
        await message.answer("តម្លៃត្រូវធំជាង 0 និងមិនលើស $1,000,000។")
        return
    await state.update_data(price_cents=price_cents)
    await state.set_state(AdminProductForm.email)
    await message.answer(
        "បញ្ចូល Gmail/email សម្រាប់ account ឬចុចរំលង ប្រសិនបើជាកម្មវិធី/ផលិតផលដែលមិនប្រើ Gmail។",
        reply_markup=credential_skip(),
    )


async def _request_product_password(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminProductForm.password)
    await message.answer(
        "បញ្ចូល password របស់ account។ សារដែលបញ្ចូលនឹងត្រូវលុបចេញពី chat ឬចុចរំលង ប្រសិនបើមិនមាន password។",
        reply_markup=credential_skip(),
    )


async def _request_product_delivery(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminProductForm.delivery)
    await message.answer(
        "ផ្ញើឯកសារដែលអតិថិជនត្រូវទាញយក ឬបញ្ចូល HTTPS link។ Bot នឹងផ្ញើវាក្រោយ Verify ការបង់ប្រាក់ជោគជ័យ។\n"
        "បើមិនមានឯកសារ ឬ link សូមចុចរំលង។",
        reply_markup=delivery_skip(),
    )


async def _confirm_product_details(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    credentials = []
    if data.get("account_email"):
        credentials.append(f"Gmail: <code>{escape(_short_email(data['account_email']))}</code>")
    if data.get("account_password"):
        credentials.append("Password: រក្សាទុកដោយអ៊ិនគ្រីប។")
    if not credentials:
        credentials.append("Gmail/password: មិនប្រើ")
    delivery = (
        "ឯកសារផ្ញើក្រោយបង់: មានឯកសារ"
        if data.get("delivery_file_id")
        else "ឯកសារផ្ញើក្រោយបង់: មាន HTTPS link"
        if data.get("delivery_url")
        else "ឯកសារផ្ញើក្រោយបង់: មិនមាន"
    )
    summary = (
        "<b>ពិនិត្យព័ត៌មាន listing មុនដាក់ចូល stock</b>\n"
        f"Game: {_html_field(data['game'], data.get('game_html'))}\n"
        f"Level: {_html_field(data['level'], data.get('level_html'))}\n"
        f"ឈ្មោះ: {_html_field(data['name'], data.get('name_html'))}\n"
        + (
            f"\n{_html_field(data['description'], data.get('description_html'))}\n"
            if data.get("description")
            else ""
        )
        + f"តម្លៃ: <b>{_price(data['price_cents'])}</b>\n"
        + (
            "អ្នកទិញត្រូវបញ្ជាក់ Telegram username និង៖ "
            + ", ".join(escape(field) for field in data.get("identity_fields", []))
            + "\n"
            if data.get("product_kind") == "service"
            else ""
        )
        + f"រូបភាព: {len(data['photo_file_ids'])} សន្លឹក\n"
        + "\n".join(credentials)
        + f"\n{delivery}\n\n"
        "បន្ទាប់ពីបញ្ជាក់ listing នឹងចូល stock។ ឯកសារ/link និងព័ត៌មានចូលនឹងផ្ញើក្រោយ Verify បង់ប្រាក់ជោគជ័យ។"
    )
    await state.set_state(AdminProductForm.confirm)
    await message.answer(summary, parse_mode=ParseMode.HTML, reply_markup=confirm_product())


@admin_router.message(AdminProductForm.email, F.text == ADMIN_CREDENTIAL_SKIP)
async def skip_account_email(message: Message, state: FSMContext) -> None:
    await state.update_data(account_email="")
    await _request_product_password(message, state)


@admin_router.message(AdminProductForm.email, F.text)
async def receive_account_email(message: Message, state: FSMContext) -> None:
    email = (message.text or "").strip()
    if "@" not in email or len(email) > 254:
        await message.answer("Email មិនត្រឹមត្រូវទេ។ សូមពិនិត្យហើយបញ្ចូលម្ដងទៀត។")
        return
    await state.update_data(account_email=email)
    try:
        await message.delete()
    except TelegramAPIError:
        logger.info("Could not delete an admin credential message")
    await _request_product_password(message, state)


@admin_router.message(AdminProductForm.password, F.text == ADMIN_CREDENTIAL_SKIP)
async def skip_account_password(message: Message, state: FSMContext) -> None:
    await state.update_data(account_password="")
    await _request_product_delivery(message, state)


@admin_router.message(AdminProductForm.password, F.text)
async def receive_account_password(message: Message, state: FSMContext) -> None:
    password = (message.text or "").strip()
    if not password or len(password) > 200:
        await message.answer("Password មិនត្រឹមត្រូវទេ។ សូមបញ្ចូលម្ដងទៀត។")
        return
    await state.update_data(account_password=password)
    try:
        await message.delete()
    except TelegramAPIError:
        logger.info("Could not delete an admin credential message")
    await _request_product_delivery(message, state)


@admin_router.message(AdminProductForm.delivery, F.document)
async def receive_product_delivery_file(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    await state.update_data(delivery_file_id=message.document.file_id, delivery_url="")
    await _confirm_product_details(message, state)


@admin_router.message(AdminProductForm.delivery, F.text == ADMIN_DELIVERY_SKIP)
async def skip_product_delivery(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    await state.update_data(delivery_file_id="", delivery_url="")
    await _confirm_product_details(message, state)


@admin_router.message(AdminProductForm.delivery, F.text)
async def receive_product_delivery_link(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    delivery_url = (message.text or "").strip()
    try:
        parsed_url = urlsplit(delivery_url)
        valid_url = (
            parsed_url.scheme.lower() == "https"
            and bool(parsed_url.hostname)
            and parsed_url.username is None
            and parsed_url.password is None
            and len(delivery_url) <= 2048
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        await message.answer("Link មិនត្រឹមត្រូវទេ។ សូមបញ្ចូល HTTPS link ដែលមានសុវត្ថិភាព ឬចុចរំលង។")
        return
    await state.update_data(delivery_file_id="", delivery_url=delivery_url)
    try:
        await message.delete()
    except TelegramAPIError:
        logger.info("Could not delete an admin delivery link message")
    await _confirm_product_details(message, state)


@admin_router.message(AdminProductForm.confirm, F.text == ADMIN_PUBLISH)
async def publish_product(
    message: Message,
    state: FSMContext,
    store: Store,
    bot: Bot,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    data = await state.get_data()
    try:
        product_id = await store.create_product(
            game=data["game"],
            level=data["level"],
            name=data["name"],
            price_cents=data["price_cents"],
            photo_file_ids=data["photo_file_ids"],
            account_email=data["account_email"],
            account_password=data["account_password"],
            created_by=message.from_user.id,
            game_html=data.get("game_html", ""),
            level_html=data.get("level_html", ""),
            name_html=data.get("name_html", ""),
            description=data.get("description", ""),
            description_html=data.get("description_html", ""),
            delivery_file_id=data.get("delivery_file_id", ""),
            delivery_url=data.get("delivery_url", ""),
            service_kind=data.get("service_kind", ""),
            identity_fields=data.get("identity_fields", []),
        )
    except (KeyError, ValueError):
        await message.answer("ព័ត៌មាន listing មិនពេញលេញទេ។ សូមចាប់ផ្ដើមម្ដងទៀត។")
        await state.clear()
        return

    await state.clear()
    sent, failed = await _broadcast_new_product(bot, store, product_id)
    await message.answer(
        f"បានបង្កើត listing #{product_id}។\n"
        f"ប្រកាសទៅអ្នកប្រើបាន {sent} នាក់; មិនអាចផ្ញើទៅ {failed} នាក់។",
        reply_markup=admin_menu(),
    )


@admin_router.message(AdminProductForm.confirm, F.text == ADMIN_CANCEL)
async def cancel_product_form(
    message: Message,
    state: FSMContext,
    admin_ids: frozenset[int],
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    await state.clear()
    await message.answer("បានបោះបង់។", reply_markup=admin_menu())


@admin_router.message(F.text == ADMIN_PENDING_ORDERS)
async def show_pending_orders(message: Message, store: Store, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមប្រើផ្ទាំង admin ក្នុង private chat ជាមួយ bot។")
        return
    orders = await store.pending_orders()
    if not orders:
        await message.answer("មិនមាន order កំពុងរង់ចាំទេ។", reply_markup=admin_menu())
        return
    await message.answer("<b>ជ្រើសរើស order ដែលកំពុងរង់ចាំ</b>", parse_mode=ParseMode.HTML, reply_markup=pending_orders_list(orders))


@admin_router.message(F.text == ADMIN_DELETE_PRODUCT)
async def show_products_to_delete(message: Message, store: Store, admin_ids: frozenset[int]) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("សូមប្រើផ្ទាំង admin ក្នុង private chat ជាមួយ bot។")
        return
    products = await store.admin_products()
    deletable = [product for product in products if product["is_listed"] and product["status"] != "reserved"]
    if not deletable:
        await message.answer("មិនមានទំនិញដែលអាចលុបបានទេ។", reply_markup=admin_menu())
        return
    await message.answer("ជ្រើសរើសទំនិញដែលចង់លុបចេញពីបញ្ជីលក់៖", reply_markup=admin_products_keyboard(deletable))


@admin_router.callback_query(F.data.startswith("admin:select_hide:"))
async def confirm_hide_product(query: CallbackQuery, store: Store, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        return
    product = await store.available_product(product_id)
    await query.answer()
    if product is None:
        await query.message.answer("ទំនិញនេះត្រូវបានលុបចេញ ឬមិនមានទៀតទេ។")
        return
    await query.message.answer(
        f"តើបញ្ជាក់លុប <b>{_html_field(product['name'], product.get('name_html'))}</b> ចេញពីបញ្ជីលក់ឬ?\n"
        "ប្រវត្តិ order ចាស់នឹងនៅរក្សាទុក។",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_hide_confirmation(product_id),
    )


@admin_router.callback_query(F.data.startswith("admin:hide:"))
async def hide_product(query: CallbackQuery, store: Store, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    try:
        product_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        return
    hidden = await store.hide_product(product_id)
    await query.answer("បានលុបចេញពីបញ្ជី" if hidden else "ទំនិញនេះកំពុងកក់ ឬត្រូវបានលុបរួចហើយ។", show_alert=not hidden)
    if hidden and isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer("បានលុបទំនិញចេញពីបញ្ជីលក់។", reply_markup=admin_menu())


@admin_router.callback_query(F.data == "admin:delete_list")
async def return_to_delete_list(query: CallbackQuery, store: Store, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    products = await store.admin_products()
    deletable = [product for product in products if product["is_listed"] and product["status"] != "reserved"]
    await query.answer()
    if isinstance(query.message, Message):
        if deletable:
            await query.message.answer("ជ្រើសរើសទំនិញដែលចង់លុបចេញពីបញ្ជីលក់៖", reply_markup=admin_products_keyboard(deletable))
        else:
            await query.message.answer("មិនមានទំនិញដែលអាចលុបបានទេ។", reply_markup=admin_menu())


@admin_router.callback_query(F.data.startswith("admin:order:"))
async def show_admin_order(query: CallbackQuery, store: Store, admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    try:
        order_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        return
    orders = await store.pending_orders()
    order = next((item for item in orders if item["id"] == order_id), None)
    await query.answer()
    if order is None:
        if isinstance(query.message, Message):
            await query.message.answer("Order នេះបានបញ្ចប់ ឬផុតកំណត់ហើយ។")
        return
    if isinstance(query.message, Message):
        username_line = (
            f"Telegram username: <code>{escape(order['telegram_username'])}</code>\n"
            if order.get("telegram_username")
            else ""
        )
        identity_line = "".join(
            f"{escape(label)}: <code>{escape(value)}</code>\n"
            for label, value in order.get("identity_values", {}).items()
        )
        await query.message.answer(
            f"<b>Order #{order['id']}</b>\n"
            f"អតិថិជន: <code>{order['user_id']}</code>\n"
            f"Game: {_html_field(order['game_name'], order.get('game_name_html'))} · "
            f"Level: {_html_field(order['level_name'], order.get('level_name_html'))}\n"
            f"Account: {_html_field(order['name'], order.get('name_html'))}\n"
            f"តម្លៃ: <b>{_price(order['price_cents'])}</b>\n"
            f"{username_line}"
            f"{identity_line}"
            f"ផុតកំណត់: <code>{escape(order['expires_at'])}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=pending_order_actions(order_id),
        )


async def _deliver_paid_account(
    bot: Bot,
    order: dict,
    admin_ids: frozenset[int],
    payment_method: str = "ABA / KHQR ដែលបាន Verify",
) -> None:
    if order.get("service_kind"):
        service_title = _html_field(order["game_name"], order.get("game_name_html"))
        option_name = _html_field(order["name"], order.get("name_html"))
        telegram_username = escape(order.get("telegram_username", ""))
        identity_lines = "".join(
            f"{escape(label)}៖ <code>{escape(value)}</code>\n"
            for label, value in order.get("identity_values", {}).items()
        )
        service_receipt = (
            f"{_custom_emoji('service_check', '✅')} <b>ការទូទាត់សេវាជោគជ័យ</b>\n"
            f"<b>Order #{order['id']}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{_custom_emoji('service_crown', '👑')} សេវា៖ <b>{service_title}</b>\n"
            f"{_custom_emoji('service_down', '⬇️')} ជម្រើស៖ <b>{option_name}</b>\n"
            f"{_custom_emoji('account', '👤')} Telegram username៖ <code>{telegram_username}</code>\n"
            f"{identity_lines}"
            f"តម្លៃ៖ <b>{_price(order['price_cents'])}</b>\n"
            f"វិធីបង់៖ <b>{escape(payment_method)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Admin បានទទួលព័ត៌មានការបញ្ជាទិញ ហើយនឹងដំណើរការសេវាជូនអ្នក។"
        )
        try:
            await bot.send_message(order["user_id"], service_receipt, parse_mode=ParseMode.HTML)
        except TelegramAPIError as error:
            logger.warning("Could not notify service customer for order %s: %s", order["id"], error)
        await _notify_admins(
            bot,
            admin_ids,
            f"{_custom_emoji('service_check', '✅')} <b>សេវា Telegram បានបង់ប្រាក់រួច · Order #{order['id']}</b>\n"
            f"អតិថិជន: <code>{order['user_id']}</code>\n"
            f"សេវា៖ <b>{service_title}</b> · ជម្រើស៖ <b>{option_name}</b>\n"
            f"Telegram username៖ <code>{telegram_username}</code>\n"
            f"{identity_lines}"
            f"តម្លៃ៖ <b>{_price(order['price_cents'])}</b> · វិធីបង់៖ <b>{escape(payment_method)}</b>\n"
            "សូមដំណើរការសេវាទៅ username ខាងលើ។",
        )
        return

    credential_lines = []
    if order.get("account_email"):
        credential_lines.append(f"├ Gmail: <code>{escape(order['account_email'])}</code>")
    if order.get("account_password"):
        credential_lines.append(f"└ Password: <code>{escape(order['account_password'])}</code>")
    credential_text = (
        f"{_custom_emoji('account', '👤')} <b>ព័ត៌មានចូល account</b>\n"
        + "\n".join(credential_lines)
        if credential_lines
        else ""
    )
    delivery_url = order.get("delivery_url", "")
    download_text = (
        f"\n{_custom_emoji('new_stock', '🎁')} ឯកសារ/Link ទាញយក: "
        f'<a href="{escape(delivery_url, quote=True)}">ចុចទីនេះ</a>\n'
        if delivery_url
        else ""
    )
    wallet_balance_text = (
        f"សមតុល្យកាបូបក្រោយបង់: <b>{_price(order['balance_cents'])}</b>\n"
        if "balance_cents" in order
        else ""
    )
    text = (
        f"{_custom_emoji('wallet', '💳')} <b>ការបង់ប្រាក់ជោគជ័យ</b>\n"
        f"<b>Order #{order['id']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{_custom_emoji('cart', '🛒')} <b>ព័ត៌មានការបញ្ជាទិញ</b>\n"
        f"├ Account: <b>{_html_field(order['name'], order.get('name_html'))}</b>\n"
        f"├ Game: <b>{_html_field(order['game_name'], order.get('game_name_html'))}</b>\n"
        f"├ Level: <b>{_html_field(order['level_name'], order.get('level_name_html'))}</b>\n"
        f"└ តម្លៃ: <b>{_price(order['price_cents'])}</b>\n"
        f"វិធីបង់: <b>{escape(payment_method)}</b>\n"
        f"{wallet_balance_text}"
        f"━━━━━━━━━━━━━━━━━━━━\n{credential_text}{download_text}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "សូមរក្សាទុកព័ត៌មាន និងឯកសារនេះឱ្យបានសុវត្ថិភាព។"
    )
    try:
        await bot.send_message(order["user_id"], text, parse_mode=ParseMode.HTML)
    except TelegramAPIError as error:
        logger.warning("Could not deliver account for order %s: %s", order["id"], error)
        await _notify_admins(
            bot,
            admin_ids,
            f"ទូទាត់ order #{order['id']} ជោគជ័យ ប៉ុន្តែផ្ញើសារទៅអតិថិជនមិនបាន។\n"
            f"User ID: <code>{order['user_id']}</code>។ សូមពិនិត្យហើយទាក់ទងអតិថិជន។",
        )
        return

    delivery_file_id = order.get("delivery_file_id", "")
    if delivery_file_id:
        try:
            await bot.send_document(
                order["user_id"],
                delivery_file_id,
                caption=f"{_custom_emoji('new_stock', '🎁')} ឯកសារសម្រាប់ order #{order['id']}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramAPIError as error:
            logger.warning("Could not deliver file for order %s: %s", order["id"], error)
            await _notify_admins(
                bot,
                admin_ids,
                f"បង់ order #{order['id']} ជោគជ័យ ប៉ុន្តែផ្ញើឯកសារមិនបាន។\n"
                f"User ID: <code>{order['user_id']}</code>។ សូមពិនិត្យ product file ហើយទាក់ទងអតិថិជន។",
            )
            return

    await _notify_admins(
        bot,
        admin_ids,
        f"ការទូទាត់ order #{order['id']} តាម {escape(payment_method)} ជោគជ័យ។ "
        "ទំនិញត្រូវបានផ្ញើទៅអតិថិជន និងសម្គាល់ថាលក់រួច។",
    )


async def poll_khqr_payments(
    bot: Bot,
    store: Store,
    payment_config: dict[str, str],
    admin_ids: frozenset[int],
) -> None:
    if payment_config.get("enabled", "true").lower() != "true":
        return
    secret_key = payment_config.get("secret_key", "").strip()
    if not secret_key or secret_key.lower().startswith(("your_", "replace_", "placeholder")):
        return

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        orders = await store.pending_payment_orders()
        order_checks = await _verify_pending_batch(orders, secret_key, payment_config["verify_url"], session)
        for pending, check in zip(orders, order_checks):
            if check.status != "success" or check.amount_cents is None:
                continue
            if check.amount_cents != pending["price_cents"]:
                logger.error("KHQR amount mismatch for order %s", pending["id"])
                if pending["id"] not in reported_amount_mismatch_orders:
                    reported_amount_mismatch_orders.add(pending["id"])
                    await _notify_admins(
                        bot,
                        admin_ids,
                        f"KHQR order #{pending['id']} មានចំនួនទឹកប្រាក់មិនត្រូវនឹងតម្លៃ order។ "
                        "មិនបានផ្ញើ account ទេ; សូមពិនិត្យ merchant dashboard។",
                    )
                continue
            paid_order = await store.complete_paid_order(
                pending["id"],
                verified_amount_cents=check.amount_cents,
            )
            if paid_order is not None:
                await _deliver_paid_account(bot, paid_order, admin_ids)

        topups = await store.pending_wallet_topups()
        topup_checks = await _verify_pending_batch(topups, secret_key, payment_config["verify_url"], session)
        for pending, check in zip(topups, topup_checks):
            if check.status != "success" or check.amount_cents is None:
                continue
            if check.amount_cents != pending["amount_cents"]:
                logger.error("KHQR amount mismatch for wallet top-up %s", pending["id"])
                continue
            credited = await store.complete_wallet_topup(
                pending["id"],
                verified_amount_cents=check.amount_cents,
            )
            if credited is None:
                continue
            try:
                username = f"@{escape(credited['username'])}" if credited.get("username") else "មិនមាន username"
                bonus_cents = credited["credit_cents"] - credited["amount_cents"]
                await bot.send_message(
                    credited["user_id"],
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"{_custom_emoji('wallet_topup', '💰')} <b>បញ្ចូលប្រាក់បានជោគជ័យ</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"សួស្តី <b>{escape(credited.get('first_name') or 'អតិថិជន')}</b>!\n"
                    f"{_custom_emoji('account', '👤')} យូសនាម: <code>{username}</code>\n"
                    f"{_custom_emoji('wallet', '💳')} ប្រាក់ដែលបានបង់: <b>{_price(credited['amount_cents'])}</b>\n"
                    f"{_custom_emoji('new_stock', '🎁')} Bonus: <b>{_price(bonus_cents)}</b>\n"
                    f"{_custom_emoji('wallet_topup', '💰')} បានបញ្ចូលកាបូប: <b>{_price(credited['credit_cents'])}</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"សមតុល្យថ្មី: <b>{_price(credited['balance_cents'])}</b>\n"
                    "សូមបើកម៉ឺនុយ <b>កាបូបប្រាក់</b> ដើម្បីពិនិត្យសមតុល្យ។\n"
                    "ការទូទាត់បានផ្ទៀងផ្ទាត់ដោយ KHQR រួចរាល់។",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramAPIError as error:
                logger.warning("Could not notify user after wallet top-up %s: %s", credited["id"], error)
            await _notify_admins(
                bot,
                admin_ids,
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"{_custom_emoji('wallet_topup', '💰')} <b>មានការបញ្ចូលប្រាក់ជោគជ័យ</b> · #{credited['id']}\n"
                f"ឈ្មោះ: <b>{escape(credited.get('first_name') or 'មិនស្គាល់')}</b>\n"
                f"Username: <code>{'@' + escape(credited['username']) if credited.get('username') else 'មិនមាន'}</code>\n"
                f"User ID: <code>{credited['user_id']}</code>\n"
                f"បានបង់: <b>{_price(credited['amount_cents'])}</b> · Bonus: <b>{_price(bonus_cents)}</b>\n"
                f"ចូលកាបូប: <b>{_price(credited['credit_cents'])}</b> · សមតុល្យ: <b>{_price(credited['balance_cents'])}</b>\n"
                "KHQR Verify V2: <b>ជោគជ័យ</b>\n"
                "━━━━━━━━━━━━━━━━━━━━",
            )


async def _verify_pending_batch(
    pending_items: list[dict],
    secret_key: str,
    verify_url: str,
    session: aiohttp.ClientSession,
) -> list:
    semaphore = asyncio.Semaphore(10)

    async def verify_one(item: dict):
        async with semaphore:
            return await verify_transaction(
                transaction_id=item["payment_tx_id"],
                secret_key=secret_key,
                verify_url=verify_url,
                session=session,
            )

    return await asyncio.gather(*(verify_one(item) for item in pending_items))


@admin_router.callback_query(F.data.startswith("admin:check:"))
async def check_order_payment(query: CallbackQuery, store: Store, payment_config: dict[str, str], admin_ids: frozenset[int]) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    try:
        order_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        return
    pending = next((order for order in await store.pending_payment_orders() if order["id"] == order_id), None)
    if pending is None:
        await query.answer("Order មិនទាន់មានការទូទាត់កំពុងរង់ចាំទេ។", show_alert=True)
        return
    result = await verify_transaction(
        transaction_id=pending["payment_tx_id"],
        secret_key=payment_config.get("secret_key", ""),
        verify_url=payment_config["verify_url"],
    )
    if result.status == "success" and result.amount_cents == pending["price_cents"]:
        order = await store.complete_paid_order(order_id, verified_amount_cents=result.amount_cents)
        if order is not None:
            await _deliver_paid_account(query.bot, order, admin_ids)
            await query.answer("បានផ្ទៀងផ្ទាត់ការបង់ប្រាក់ជោគជ័យ")
            return
    await query.answer("មិនទាន់ឃើញ payment ជោគជ័យទេ។", show_alert=True)


@admin_router.callback_query(F.data.startswith("admin:cancel_order:"))
async def cancel_pending_order(
    query: CallbackQuery,
    store: Store,
    bot: Bot,
    admin_ids: frozenset[int],
) -> None:
    if not _is_admin(query.from_user.id, admin_ids):
        await query.answer("មិនមានសិទ្ធិចូលប្រើទេ។", show_alert=True)
        return
    try:
        order_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        return
    order = await store.cancel_order(order_id)
    if order is None:
        await query.answer("Order មិនអាចបោះបង់បានទេ។", show_alert=True)
        return
    await query.answer("បានបោះបង់ order និងដោះ stock")
    try:
        await bot.send_message(
            order["user_id"],
            f"ការបញ្ជាទិញ #{order_id} ({_html_field(order['name'], order.get('name_html'))}) ត្រូវបានបោះបង់។ Account បានត្រឡប់ទៅ stock វិញ។",
            parse_mode=ParseMode.HTML,
        )
    except TelegramAPIError:
        pass
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer(f"បានបោះបង់ order #{order_id} ហើយ។")


async def notify_expired_orders(bot: Bot, store: Store) -> None:
    expired = await store.expire_orders()
    for order in expired:
        try:
            await bot.send_message(
                order["user_id"],
                f"ការកក់ #{order['id']} ({_html_field(order['name'], order.get('name_html'))}) បានផុតកំណត់។ Account ត្រឡប់ទៅ stock វិញហើយ។",
                parse_mode=ParseMode.HTML,
            )
        except TelegramAPIError:
            pass
