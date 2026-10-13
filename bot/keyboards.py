import os
from contextvars import ContextVar, Token

from aiogram.enums import ButtonStyle
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.text_format import plain_label
from dotenv import load_dotenv

load_dotenv()

# Replace each value with the sticker's custom_emoji_id to change its button icon.
_DEFAULT_CUSTOM_EMOJI_IDS = {
    "welcome": "5413554183502572090",
    "new_stock": "6267117038808870117",
    "catalog": "5445260044398524944",
    "guide": "5258328383183396223",
    "cart": "5447319442562251569",
    "wallet": "5444960062407732826",
    "add": "5258108352008823107",
    "product": "5258134813302332906",
    "account": "5316727448644103237",
    "support": "5258337316715373336",
    "chat": "5447510826304959724",
    "profile_mail": "5445163772706582819",
    "profile_water": "5444979432710242887",
    "profile_wallet": "5444903695256941915",
    "profile_money": "5444860552310457690",
    "wallet_topup": "5444860552310457690",
    "pay_aba": "6339110599179373483",
    "pay_khqr": "6032693626394382504",
    "mobile": "5445033158456145975",
    "verified": "5445163158526261598",
    "add_more": "5447224884562263112",
    "heart": "5444887644964159628",
    "chat_bubble": "5445140257760639304",
    "warning": "5447381715293074599",
    "cross": "5445092669522996408",
    "wallet_icon": "5444903695256941915",
    "gallery": "5447637214307579793",
    "voice": "5447321534211323909",
    "link": "5447479640547428304",
    "service_check": "5370993432716129583",
    "service_crown": "5229027828527309057",
    "service_down": "6224345808855112919",
    "service_warning": "6226293447674830369",
    "service_arrow": "6224400835976110033",
    "admin_contact": "5316992572680320646",
    "userinfo": "5980828026529649035",
}


def _custom_emoji_ids(environ: dict[str, str] | None = None) -> dict[str, str]:
    values = os.environ if environ is None else environ
    emoji_ids = _DEFAULT_CUSTOM_EMOJI_IDS.copy()
    for name, default_id in _DEFAULT_CUSTOM_EMOJI_IDS.items():
        override = values.get(f"ROKIRA_EMOJI_{name.upper()}", "").strip()
        if not override:
            continue
        if not override.isdecimal():
            raise ValueError(f"ROKIRA_EMOJI_{name.upper()} must contain a numeric custom emoji ID.")
        emoji_ids[name] = override
    return emoji_ids


CUSTOM_EMOJI_IDS = _custom_emoji_ids()
_BUTTON_EMOJI_OVERRIDES: ContextVar[dict[str, str] | None] = ContextVar(
    "customer_button_emoji_overrides",
    default=None,
)

BUTTON_EMOJI_TARGET_LABELS = {
    "welcome": "ស្វាគមន៍",
    "new_stock": "ប្រកាសទំនិញថ្មី",
    "catalog": "ប្រភេទទំនិញ",
    "guide": "វិធីប្រើប្រាស់ / ត្រឡប់",
    "cart": "បញ្ជាទិញ",
    "wallet": "កាបូប",
    "add": "បន្ថែម / បង្កើត",
    "product": "ប៊ូតុងទំនិញ",
    "account": "គណនី / អ្នកប្រើ",
    "support": "ទំនាក់ទំនង Admin",
    "chat": "ភាសា / ជជែក",
    "profile_mail": "ព័ត៌មាន profile",
    "profile_water": "ឈ្មោះ profile",
    "profile_wallet": "កាបូប profile",
    "profile_money": "ប្រាក់ profile",
    "wallet_topup": "បញ្ចូលប្រាក់ / បង់ប្រាក់",
    "pay_aba": "ABA Mobile",
    "pay_khqr": "KHQR",
    "mobile": "ទូរស័ព្ទ / ព័ត៌មាន",
    "verified": "បញ្ជាក់ / ជោគជ័យ",
    "add_more": "បន្ថែម / បញ្ចុះតម្លៃ",
    "heart": "អរគុណ",
    "chat_bubble": "សារជជែក",
    "warning": "ការព្រមាន",
    "cross": "បោះបង់",
    "wallet_icon": "កាបូបប្រាក់",
    "gallery": "រូបភាព / ផលិតផល",
    "voice": "សំឡេង",
    "link": "តំណ",
    "service_check": "បញ្ជាក់សេវា",
    "service_crown": "សេវាកម្ម / Top up",
    "service_down": "ទាញយក / Stars",
    "service_warning": "ព្រមានសេវា",
    "service_arrow": "បន្ត / ទិសដៅ",
    "admin_contact": "ឆាត Admin មេ",
    "userinfo": "រក Telegram ID",
}


def set_button_emoji_overrides(overrides: dict[str, str]) -> Token:
    return _BUTTON_EMOJI_OVERRIDES.set(overrides)


def reset_button_emoji_overrides(token: Token) -> None:
    _BUTTON_EMOJI_OVERRIDES.reset(token)


def _button_emoji_id(icon: str | None, fallback_custom_emoji_id: str | None = None) -> str | None:
    overrides = _BUTTON_EMOJI_OVERRIDES.get()
    if overrides is not None:
        if icon is not None and icon in overrides:
            return overrides[icon] or None
        if overrides.get("__hide_defaults__") == "true":
            return None
    if fallback_custom_emoji_id is not None:
        return fallback_custom_emoji_id
    return CUSTOM_EMOJI_IDS.get(icon) if icon else None

MENU_CATALOG = "បញ្ជាទិញទំនិញ"
MENU_CATALOG_EN = "Browse products"
MENU_TOPUP = "Top up"
MENU_TOPUP_EN = "Top up"
MENU_CREATE_BOT = "បង្កើត bot ផ្ទាល់ខ្លួន"
MENU_CREATE_BOT_EN = "Create my bot"
MENU_ORDERS = "ការបញ្ជាទិញរបស់ខ្ញុំ"
MENU_ORDERS_EN = "My orders"
MENU_PROFILE = "គណនី"
MENU_PROFILE_EN = "My profile"
MENU_WALLET = "បញ្ចូលប្រាក់"
MENU_WALLET_EN = "Wallet top-up"
MENU_SUPPORT = "ជំនួយ"
MENU_CHANNEL = "ចូលរួមឆានែល"
MENU_GUIDE = "វិធីប្រើប្រាស់"
MENU_GUIDE_EN = "How to order"
MENU_DISCOUNT_CODE = "បញ្ចូលកូដបញ្ចុះតម្លៃ"
MENU_DISCOUNT_CODE_EN = "Enter discount code"
MENU_LANGUAGE = "ប្តូរភាសា"
MENU_LANGUAGE_EN = "Change language"
MENU_ADMIN_EN = "Admin dashboard"
LANGUAGE_KM = "ខ្មែរ"
LANGUAGE_EN = "English"
LANGUAGE_BACK = "ត្រឡប់ទៅម៉ឺនុយ"
LANGUAGE_BACK_EN = "Back to menu"
MENU_ADMIN = "ផ្ទាំងគ្រប់គ្រង"
ADMIN_ADD_PRODUCT = "បន្ថែម account ចូល stock"
ADMIN_MANAGE_ACCOUNTS = "គ្រប់គ្រង account"
ADMIN_TELEGRAM_SERVICES = "គ្រប់គ្រងសេវា Telegram"
ADMIN_SERVICE_PREMIUM = "Telegram Premium"
ADMIN_SERVICE_STARS = "Telegram Stars"
ADMIN_SERVICE_MORE = "រក្សាទុក ហើយបន្ថែមមួយទៀត"
ADMIN_SERVICE_DONE = "រួចរាល់"
ADMIN_SERVICE_ADD_OPTION = "+ បញ្ចូលជម្រើស"
ADMIN_SERVICE_ADD_TEN = "បន្ថែម 10 ជម្រើស"
ADMIN_SERVICE_PUBLISH = "Publish"
ADMIN_SERVICE_CANCEL = "បោះបង់"
ADMIN_ITEM_ACCOUNT = "Account ធម្មតា"
ADMIN_ITEM_SERVICE = "សេវា Game / Top up"
ADMIN_ITEM_PRO = "កម្មវិធី Pro"
ADMIN_SERVICE_UID = "Username + Game UID"
ADMIN_SERVICE_TWO_IDS = "Username + Game ID + Zone/Server ID"
ADMIN_DELETE_PRODUCT = "លុបទំនិញ"
ADMIN_PENDING_ORDERS = "Order កំពុងរង់ចាំ"
ADMIN_BROADCAST = "ផ្ញើសារទៅអ្នកប្រើទាំងអស់"
ADMIN_CREATE_DISCOUNT = "បង្កើតកូដបញ្ចុះតម្លៃ"
ADMIN_CONTACT_SETTINGS = "កែព័ត៌មានជំនួយ/ឆានែល"
ADMIN_KHQR_SETTINGS = "កែ KHQR Profile / Secret"
ADMIN_BUTTON_EMOJI = "កែ Emoji ប៊ូតុងនីមួយៗ"
ADMIN_KHQR_PROFILE = "ប្តូរ KHQR Profile ID"
ADMIN_KHQR_SECRET = "ប្តូរ KHQR Secret Key"
ADMIN_EDIT_SUPPORT = "កែ username ជំនួយ"
ADMIN_EDIT_CHANNEL = "កែតំណឆានែល"
ADMIN_SETTINGS_BACK = "ត្រឡប់ទៅផ្ទាំង Admin"
ADMIN_BACK = "ត្រឡប់ទៅម៉ឺនុយ"
ADMIN_PHOTOS_DONE = "បញ្ចប់ការបញ្ចូលរូប"
ADMIN_DESCRIPTION_SKIP = "រំលងការពិពណ៌នា"
ADMIN_CREDENTIAL_SKIP = "រំលង"
ADMIN_DELIVERY_SKIP = "រំលងឯកសារ/Link"
ADMIN_PUBLISH = "បញ្ជាក់ និងប្រកាស stock"
ADMIN_CANCEL = "បោះបង់"


def button(
    text: str,
    callback_data: str,
    *,
    style: ButtonStyle = ButtonStyle.PRIMARY,
    icon: str | None = None,
    custom_emoji_id: str | None = None,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        style=style,
        icon_custom_emoji_id=_button_emoji_id(icon, custom_emoji_id),
    )


def reply_button(text: str, *, style: ButtonStyle = ButtonStyle.PRIMARY, icon: str | None = None) -> KeyboardButton:
    return KeyboardButton(
        text=text,
        style=style,
        icon_custom_emoji_id=_button_emoji_id(icon),
    )


def _short_label(text: str, limit: int) -> str:
    label = plain_label(text)
    return f"{label[:limit]}{'…' if len(label) > limit else ''}"


def main_menu(
    *,
    include_admin: bool = False,
    include_create_bot: bool = True,
    language: str = "km",
) -> ReplyKeyboardMarkup:
    english = language == "en"
    rows = [
        [
            reply_button(MENU_CATALOG_EN if english else MENU_CATALOG, icon="cart"),
            reply_button(MENU_TOPUP_EN if english else MENU_TOPUP, icon="service_crown"),
        ],
        [
            reply_button(MENU_ORDERS_EN if english else MENU_ORDERS, icon="product"),
            reply_button(MENU_WALLET_EN if english else MENU_WALLET, icon="wallet_topup"),
        ],
        [
            reply_button(MENU_PROFILE_EN if english else MENU_PROFILE, icon="account"),
            reply_button(MENU_GUIDE_EN if english else MENU_GUIDE, icon="guide"),
        ],
    ]
    if include_create_bot:
        rows.append(
            [
                reply_button(MENU_CREATE_BOT_EN if english else MENU_CREATE_BOT, icon="add"),
                reply_button(MENU_LANGUAGE_EN if english else MENU_LANGUAGE, style=ButtonStyle.DANGER, icon="chat"),
            ]
        )
    else:
        rows.append([reply_button(MENU_LANGUAGE_EN if english else MENU_LANGUAGE, style=ButtonStyle.DANGER, icon="chat")])
    if include_admin:
        rows.append([reply_button(MENU_ADMIN_EN if english else MENU_ADMIN, style=ButtonStyle.PRIMARY, icon="add")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=False,
        input_field_placeholder="ជ្រើសរើសមុខងារ",
    )


def language_menu(language: str = "km") -> ReplyKeyboardMarkup:
    english = language == "en"
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(LANGUAGE_KM), reply_button(LANGUAGE_EN)],
            [reply_button(LANGUAGE_BACK_EN if english else LANGUAGE_BACK)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose a language" if english else "ជ្រើសរើសភាសា",
    )


def welcome_actions(
    language: str = "km",
    support_username: str = "ROVISTARCAMBO",
    channel_url: str = "https://t.me/ROVISTARKH",
) -> InlineKeyboardMarkup:
    english = language == "en"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Join channel" if english else "ចូលរួមឆានែល",
                    url=channel_url,
                    style=ButtonStyle.DANGER,
                    icon_custom_emoji_id=_button_emoji_id("link"),
                ),
                InlineKeyboardButton(
                    text="Contact owner" if english else "ទាក់ទង Owner",
                    url=f"https://t.me/{support_username.lstrip('@')}",
                    style=ButtonStyle.DANGER,
                    icon_custom_emoji_id=_button_emoji_id("voice"),
                ),
            ]
        ]
    )


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_ADD_PRODUCT, icon="add")],
            [reply_button(ADMIN_MANAGE_ACCOUNTS, icon="account")],
            [reply_button(ADMIN_TELEGRAM_SERVICES, icon="service_crown")],
            [
                reply_button(ADMIN_DELETE_PRODUCT, icon="product"),
                reply_button(ADMIN_PENDING_ORDERS, icon="cart"),
            ],
            [reply_button(ADMIN_BROADCAST, icon="chat")],
            [reply_button(ADMIN_CREATE_DISCOUNT, icon="wallet_topup")],
            [reply_button(ADMIN_CONTACT_SETTINGS, icon="support")],
            [reply_button(ADMIN_KHQR_SETTINGS, icon="pay_khqr")],
            [reply_button(ADMIN_BUTTON_EMOJI, icon="add_more")],
            [reply_button(ADMIN_BACK, icon="guide")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="មុខងារគ្រប់គ្រង",
    )


def admin_contact_settings_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_EDIT_SUPPORT, icon="support")],
            [reply_button(ADMIN_EDIT_CHANNEL, icon="link")],
            [reply_button(ADMIN_SETTINGS_BACK, icon="guide")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="កែព័ត៌មានទំនាក់ទំនង",
    )


def admin_khqr_settings_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_KHQR_PROFILE, icon="pay_khqr")],
            [reply_button(ADMIN_KHQR_SECRET, style=ButtonStyle.DANGER, icon="warning")],
            [reply_button(ADMIN_SETTINGS_BACK, icon="guide")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="កែ KHQR credentials",
    )


def admin_panel_shortcut_keyboard(username: str) -> InlineKeyboardMarkup:
    safe_username = username.strip().lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ឆាតទៅ Admin មេ",
                    url=f"https://t.me/{safe_username}",
                    style=ButtonStyle.PRIMARY,
                    icon_custom_emoji_id=_button_emoji_id("admin_contact"),
                )
            ]
        ]
    )


def button_emoji_targets_keyboard(
    page: int = 0,
    overrides: dict[str, str] | None = None,
) -> InlineKeyboardMarkup:
    targets = list(BUTTON_EMOJI_TARGET_LABELS.items())
    page_size = 10
    page_count = max(1, (len(targets) + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    visible = targets[page * page_size : (page + 1) * page_size]
    rows = []
    for offset in range(0, len(visible), 2):
        row = []
        for key, label in visible[offset : offset + 2]:
            selected = bool(overrides and overrides.get(key))
            row.append(
                button(
                    f"{label}{' ✓' if selected else ''}",
                    f"admin:button_emoji:target:{key}",
                    style=ButtonStyle.SUCCESS if selected else ButtonStyle.PRIMARY,
                    icon=key,
                )
            )
        rows.append(row)
    navigation = []
    if page > 0:
        navigation.append(button("មុន", f"admin:button_emoji:page:{page - 1}"))
    if page + 1 < page_count:
        navigation.append(button("បន្ទាប់", f"admin:button_emoji:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([button("Reset emoji ទាំងអស់", "admin:button_emoji:reset_all", style=ButtonStyle.DANGER, icon="cross")])
    rows.append([button("បោះបង់", "admin:button_emoji:cancel", style=ButtonStyle.DANGER, icon="cross")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_service_kind_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_SERVICE_PREMIUM, icon="service_crown")],
            [reply_button(ADMIN_SERVICE_STARS, icon="service_down")],
            [reply_button(ADMIN_BACK, icon="guide")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="ជ្រើសរើសសេវា Telegram",
    )


def admin_product_kind_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_ITEM_ACCOUNT, icon="account")],
            [reply_button(ADMIN_ITEM_SERVICE, style=ButtonStyle.SUCCESS, icon="service_crown")],
            [reply_button(ADMIN_ITEM_PRO, icon="product")],
            [reply_button(ADMIN_CANCEL, style=ButtonStyle.DANGER, icon="cross")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="ជ្រើសប្រភេទទំនិញ",
    )


def admin_service_identity_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_SERVICE_UID, style=ButtonStyle.SUCCESS, icon="account")],
            [reply_button(ADMIN_SERVICE_TWO_IDS, style=ButtonStyle.SUCCESS, icon="mobile")],
            [reply_button(ADMIN_CANCEL, style=ButtonStyle.DANGER, icon="cross")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="ជ្រើស field សម្រាប់ game",
    )


def admin_service_option_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_SERVICE_MORE, style=ButtonStyle.SUCCESS, icon="add_more")],
            [reply_button(ADMIN_SERVICE_DONE, style=ButtonStyle.DANGER, icon="verified")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="បន្ថែមជម្រើស ឬបញ្ចប់",
    )


def admin_service_editor(option_count: int, saved_count: int) -> InlineKeyboardMarkup:
    option_count = max(1, min(option_count, 100))
    rows = []
    for offset in range(0, option_count, 2):
        row = []
        for index in range(offset, min(offset + 2, option_count)):
            row.append(
                button(
                    f"#{index + 1} · + បញ្ចូលជម្រើស",
                    f"admin:service:slot:{index}",
                    style=ButtonStyle.SUCCESS,
                    icon="add_more",
                )
            )
        rows.append(row)
    if option_count < 100:
        rows.append(
            [button("⊕ បន្ថែម 10 ជម្រើស", "admin:service:add10", style=ButtonStyle.PRIMARY, icon="add_more")]
        )
    rows.append(
        [
            button(
                f"រក្សាទុក និង Publish ({saved_count})",
                "admin:service:publish",
                style=ButtonStyle.SUCCESS,
                icon="verified",
            )
        ]
    )
    rows.append([button("បោះបង់", "admin:service:cancel", style=ButtonStyle.DANGER, icon="cross")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_username_confirmation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("ខ្ញុំប្រាកដណាស់ថា នេះគឺជា Username ពិតរបស់ខ្ញុំ", "service:confirm", style=ButtonStyle.SUCCESS, icon="service_check")],
            [button("កែ Username", "service:edit", style=ButtonStyle.DANGER, icon="service_warning")],
        ]
    )


def customer_bot_plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("$2.50 · 1 ខែ", "bot:plan:monthly", style=ButtonStyle.SUCCESS, icon="wallet_topup")],
            [button("$3.50 · 2 ខែ", "bot:plan:two_months", style=ButtonStyle.SUCCESS, icon="wallet_topup")],
            [button("$15.00 · 1 ឆ្នាំ", "bot:plan:yearly", style=ButtonStyle.SUCCESS, icon="wallet_topup")],
        ]
    )


def customer_bot_setup_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("បង់រួច · បន្តបង្កើត Bot", f"bot:setup:{order_id}", style=ButtonStyle.SUCCESS, icon="verified")]
        ]
    )


def open_botfather_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="បើក @BotFather",
                    url="https://t.me/BotFather",
                    style=ButtonStyle.PRIMARY,
                    icon_custom_emoji_id=_button_emoji_id("add"),
                )
            ]
        ]
    )


def customer_bot_open_keyboard(username: str) -> InlineKeyboardMarkup:
    safe_username = username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ចូលប្រើ bot របស់ខ្ញុំ",
                    url=f"https://t.me/{safe_username}",
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=_button_emoji_id("add"),
                )
            ]
        ]
    )


def customer_bot_invoice_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="បើក ABA Mobile",
                    url="https://aba.onelink.me/oRF8",
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=_button_emoji_id("pay_aba"),
                )
            ],
            [button("បោះបង់ invoice", f"bot:cancel:{order_id}", style=ButtonStyle.DANGER, icon="cross")],
        ]
    )


def admin_settings_input_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button(ADMIN_CANCEL, style=ButtonStyle.PRIMARY)],
            [reply_button(ADMIN_SETTINGS_BACK, icon="guide")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="បញ្ចូលព័ត៌មានថ្មី",
    )


def games_keyboard(games: list[dict], language: str = "km") -> InlineKeyboardMarkup:
    english = language == "en"
    rows = [
        [
            button(
                f"{plain_label(game['name'])} · {game['product_count']} {'items' if english else 'មុខ'}",
                f"game:{game['id']}",
                style=ButtonStyle.SUCCESS,
                icon="cart",
            )
        ]
        for game in games
    ]
    rows.append([button("Back to menu" if english else "ត្រឡប់ទៅម៉ឺនុយ", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_games_keyboard(games: list[dict], language: str = "km") -> InlineKeyboardMarkup:
    english = language == "en"
    rows = [
        [
            button(
                f"{plain_label(game['name'])} · {game['available_count']} {'packages' if english else 'package'}",
                f"topup:game:{game['id']}",
                style=ButtonStyle.SUCCESS,
                icon="service_crown",
            )
        ]
        for game in games
        if game["available_count"]
    ]
    rows.append([button("Back to menu" if english else "ត្រឡប់ទៅម៉ឺនុយ", "menu", style=ButtonStyle.DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def levels_keyboard(levels: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                f"{plain_label(level['name'])} · {level['product_count']} account",
                f"level:{level['id']}",
                style=ButtonStyle.SUCCESS,
                icon="account",
            )
        ]
        for level in levels
    ]
    rows.append([button("ត្រឡប់ទៅ game", "catalog")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def products_keyboard(
    products: list[dict],
    language: str = "km",
    *,
    custom_emoji_id: str | None = None,
    show_default_icon: bool = True,
) -> InlineKeyboardMarkup:
    english = language == "en"
    status_labels = (
        {"sold": "SOLD", "reserved": "Reserved", "available": "In stock"}
        if english
        else {"sold": "បានលក់", "reserved": "កំពុងកក់", "available": "មានក្នុង stock"}
    )
    rows = [
        [
            button(
                (
                    f"{_short_label(product['name'], 35)} · "
                    f"${product['price_cents'] / 100:.2f} · "
                    f"{status_labels[product['status']]}"
                ),
                f"product:{product['id']}",
                style=ButtonStyle.SUCCESS if product["status"] == "available" else ButtonStyle.DANGER,
                icon="product" if show_default_icon else None,
                custom_emoji_id=custom_emoji_id,
            )
        ]
        for product in products
    ]
    level_id = products[0]["level_id"] if products else 0
    rows.append([button("Back to category" if english else "ត្រឡប់ទៅប្រភេទ", f"level:{level_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_options_keyboard(
    products: list[dict],
    language: str = "km",
    *,
    back_callback: str = "catalog",
    custom_emoji_id: str | None = None,
    show_default_icon: bool = True,
) -> InlineKeyboardMarkup:
    english = language == "en"
    rows = [
        [
            button(
                f"{_short_label(product['name'], 42)} · ${product['price_cents'] / 100:.2f}",
                f"buy:{product['id']}",
                style=ButtonStyle.PRIMARY,
                icon="wallet_topup" if show_default_icon else None,
                custom_emoji_id=custom_emoji_id,
            )
        ]
        for product in products
        if product.get("status") == "available"
    ]
    rows.append(
        [button("Back to Top up" if english else "ត្រឡប់ទៅ Top up", back_callback, style=ButtonStyle.DANGER)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_actions(
    product_id: int,
    status: str = "available",
    language: str = "km",
    *,
    is_service: bool = False,
    custom_emoji_id: str | None = None,
    show_default_icon: bool = True,
) -> InlineKeyboardMarkup:
    english = language == "en"
    rows = []
    if status == "available":
        label = ("Top up" if english else "បញ្ចូលឥឡូវនេះ") if is_service else ("Buy now" if english else "ទិញឥឡូវនេះ")
        icon = ("wallet_topup" if is_service else "cart") if show_default_icon else None
        rows.append(
            [
                button(
                    label,
                    f"buy:{product_id}",
                    style=ButtonStyle.SUCCESS,
                    icon=icon,
                    custom_emoji_id=custom_emoji_id,
                )
            ]
        )
    elif status == "sold":
        rows.append([button("SOLD", f"sold:{product_id}", style=ButtonStyle.DANGER, icon="product")])
    else:
        rows.append([button("Reserved for payment" if english else "កំពុងរង់ចាំការបង់ប្រាក់", f"reserved:{product_id}", style=ButtonStyle.DANGER)])
    rows.append([button("Back to products" if english else "ត្រឡប់ទៅជ្រើសរើសទំនិញ", "catalog")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def sold_confirmation_keyboard(product_id: int, language: str = "km") -> InlineKeyboardMarkup:
    label = "SOLD" if language == "en" else "ទំនិញត្រូវបានលក់រួច"
    return InlineKeyboardMarkup(
        inline_keyboard=[[button(label, f"sold:{product_id}", style=ButtonStyle.DANGER, icon="product")]]
    )


def stock_announcement(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("មើលព័ត៌មាន និងទិញឥឡូវនេះ", f"product:{product_id}", style=ButtonStyle.SUCCESS, icon="new_stock")]
        ]
    )


def payment_method_keyboard(product_id: int, language: str = "km") -> InlineKeyboardMarkup:
    english = language == "en"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("ABA Mobile", f"pay:{product_id}:aba", style=ButtonStyle.SUCCESS, icon="pay_aba"),
                button("Wallet" if english else "កាបូបប្រាក់", f"pay:{product_id}:wallet", style=ButtonStyle.PRIMARY, icon="wallet_topup"),
            ],
            [button("Enter discount code" if english else "បញ្ចូលកូដបញ្ចុះតម្លៃ", f"discount:{product_id}", style=ButtonStyle.PRIMARY, icon="add_more")],
        ]
    )


def payment_qr_keyboard(order_id: int, payment_method: str = "aba") -> InlineKeyboardMarkup:
    rows = []
    if payment_method == "aba":
        rows.append(
            [
                InlineKeyboardButton(
                    text="បើក ABA Mobile",
                    url="https://aba.onelink.me/oRF8",
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=_button_emoji_id("pay_aba"),
                )
            ]
        )
    rows.append([button("បោះបង់ការកក់", f"cancel_order:{order_id}", style=ButtonStyle.DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_topup_keyboard() -> InlineKeyboardMarkup:
    amounts = (100, 200, 500, 1000, 2000, 5000)
    rows = [
        [
            button(
                f"${amount // 100}",
                f"wallet:topup:{amount}",
                style=ButtonStyle.SUCCESS,
                icon="wallet_topup",
            )
            for amount in amounts[offset : offset + 3]
        ]
        for offset in range(0, len(amounts), 3)
    ]
    rows.append([button("ចំនួនផ្សេង", "wallet:custom", style=ButtonStyle.PRIMARY, icon="wallet_topup")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_custom_amount_keyboard(amount_cents: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    f"បន្តបង់ ${amount_cents / 100:.2f}",
                    f"wallet:topup:{amount_cents}",
                    style=ButtonStyle.SUCCESS,
                    icon="wallet_topup",
                )
            ]
        ]
    )


def wallet_topup_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="បើក ABA Mobile", url="https://aba.onelink.me/oRF8", style=ButtonStyle.SUCCESS)]
        ]
    )


def admin_contact_keyboard(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ចូលឆាតជាមួយ Admin",
                    url=f"https://t.me/{username.lstrip('@')}",
                    style=ButtonStyle.DANGER,
                    icon_custom_emoji_id=_button_emoji_id("support"),
                )
            ]
        ]
    )


def telegram_user_id_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="យក Telegram ID របស់ខ្ញុំ",
                    url="https://t.me/userinfo3bot",
                    style=ButtonStyle.PRIMARY,
                    icon_custom_emoji_id=_button_emoji_id("userinfo"),
                )
            ]
        ]
    )


def photo_done() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button("បញ្ចប់ការបញ្ចូលរូប", icon="guide")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def description_skip() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button(ADMIN_DESCRIPTION_SKIP, icon="guide")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def credential_skip() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button(ADMIN_CREDENTIAL_SKIP, style=ButtonStyle.PRIMARY)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def delivery_skip() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[reply_button(ADMIN_DELIVERY_SKIP, style=ButtonStyle.PRIMARY)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def confirm_product() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [reply_button("បញ្ជាក់ និងប្រកាស stock", icon="new_stock")],
            [reply_button("បោះបង់")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_products_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                f"លុប · #{product['id']} · {_short_label(product['name'], 30)}",
                f"admin:select_hide:{product['id']}",
                style=ButtonStyle.DANGER,
                icon="product",
            )
        ]
        for product in products
        if product["is_listed"] and product["status"] != "reserved"
    ]
    rows.append([button("ត្រឡប់ទៅ admin menu", "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_accounts_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    status_labels = {
        "available": "មានក្នុង stock",
        "reserved": "កំពុងកក់",
        "sold": "បានលក់",
    }
    rows = [
        [
            button(
                f"#{product['id']} · {_short_label(product['name'], 30)} · "
                f"${product['price_cents'] / 100:.2f} · "
                f"{status_labels.get(product['status'], product['status'])}",
                f"admin:account:{product['id']}",
                icon="account",
            )
        ]
        for product in products
    ]
    rows.append([button("ត្រឡប់ទៅផ្ទាំង admin", "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_account_actions(product_id: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "available":
        rows.extend(
            [
                [button("មើល Gmail / password", f"admin:view_credentials:{product_id}", icon="account")],
                [button("ប្តូរ Gmail", f"admin:edit_email:{product_id}", icon="account")],
                [button("ប្តូរ password", f"admin:edit_password:{product_id}", icon="wallet")],
            ]
        )
    rows.append([button("ត្រឡប់ទៅបញ្ជី account", "admin:manage_accounts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_hide_confirmation(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("បញ្ជាក់លុបចេញពីបញ្ជី", f"admin:hide:{product_id}", style=ButtonStyle.DANGER)],
            [button("បោះបង់", "admin:delete_list")],
        ]
    )


def pending_order_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("ពិនិត្យស្ថានភាព KHQR ម្ដងទៀត", f"admin:check:{order_id}", style=ButtonStyle.SUCCESS, icon="wallet")],
            [button("បដិសេធ និងដោះ stock", f"admin:cancel_order:{order_id}", style=ButtonStyle.DANGER)],
        ]
    )


def pending_orders_list(orders: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                f"Order #{order['id']} · {order['name']} · ${order['price_cents'] / 100:.2f}",
                f"admin:order:{order['id']}",
                icon="product",
            )
        ]
        for order in orders
    ]
    rows.append([button("ត្រឡប់ទៅផ្ទាំង admin", "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
