import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
from emoji import replace_emoji

from bot.database import Store
from bot.handlers import (
    _normalize_channel_url,
    _normalize_discount_code,
    _normalize_support_username,
    _profile_text,
    _send_admin_contact,
    _welcome_text,
)
from aiogram.enums import ButtonStyle

from bot.keyboards import (
    CUSTOM_EMOJI_IDS,
    _custom_emoji_ids,
    admin_contact_keyboard,
    admin_menu,
    admin_settings_input_menu,
    games_keyboard,
    main_menu,
    payment_method_keyboard,
    products_keyboard,
    service_options_keyboard,
    topup_games_keyboard,
    welcome_actions,
    button,
    reset_button_emoji_overrides,
    set_button_emoji_overrides,
)
from bot.text_format import html_label, plain_label


class LabelFormattingTests(unittest.TestCase):
    def test_profile_text_uses_custom_emoji_and_safe_separate_lines(self) -> None:
        rendered = _profile_text("Rovistar & <b>Shop</b>", "ROVISTARCAMBO", 8607930927)

        self.assertEqual(len(rendered.splitlines()), 6)
        self.assertIn("Rovistar &amp; &lt;b&gt;Shop&lt;/b&gt;", rendered)
        self.assertIn("<code>@ROVISTARCAMBO</code>", rendered)
        self.assertIn("<code>8607930927</code>", rendered)
        for emoji_id in (
            "5445163772706582819",
            "5444979432710242887",
            "5444903695256941915",
            "5444860552310457690",
        ):
            self.assertIn(f'<tg-emoji emoji-id="{emoji_id}">', rendered)

    def test_plain_label_removes_emoji_and_collapses_spacing(self) -> None:
        self.assertEqual(plain_label("Free Fire ✅ · 1 account"), "Free Fire · 1 account")

    def test_html_label_removes_regular_emoji_but_keeps_custom_tag(self) -> None:
        rendered = html_label(
            "Name ✅",
            'Name ✅ <tg-emoji emoji-id="123">🔥</tg-emoji> 🌟',
        )
        outside_custom = re.sub(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", "", rendered, flags=re.S)
        self.assertEqual(replace_emoji(outside_custom, replace=""), outside_custom)
        self.assertIn('<tg-emoji emoji-id="123">🔥</tg-emoji>', rendered)

    def test_game_button_uses_clean_label_and_custom_icon(self) -> None:
        button = games_keyboard([{"id": 1, "name": "Free Fire ✅", "product_count": 1}]).inline_keyboard[0][0]
        self.assertNotIn("✅", button.text)
        self.assertEqual(button.icon_custom_emoji_id, CUSTOM_EMOJI_IDS["cart"])

    def test_custom_emoji_ids_can_be_overridden_by_environment_key(self) -> None:
        configured = _custom_emoji_ids({"ROKIRA_EMOJI_CART": "123456789"})
        self.assertEqual(configured["cart"], "123456789")
        self.assertEqual(configured["support"], CUSTOM_EMOJI_IDS["support"])
        with self.assertRaisesRegex(ValueError, "ROKIRA_EMOJI_CART"):
            _custom_emoji_ids({"ROKIRA_EMOJI_CART": "not-an-id"})

    def test_customer_keyboard_omits_contact_buttons_moved_to_welcome(self) -> None:
        customer = main_menu()
        admin = admin_menu()

        self.assertTrue(
            all(button.style == ButtonStyle.PRIMARY for rows in admin.keyboard for button in rows)
        )
        customer_texts = {button.text for row in customer.keyboard for button in row}
        self.assertIn("Top up", customer_texts)
        self.assertNotIn("អញ្ជើញមិត្ត · មិត្តបាន Free 7 ថ្ងៃ", customer_texts)
        self.assertNotIn("ជំនួយ", customer_texts)
        self.assertNotIn("ចូលរួមឆានែល", customer_texts)
        self.assertFalse(any(button.text == "បញ្ចូលកូដបញ្ចុះតម្លៃ" for row in customer.keyboard for button in row))
        self.assertTrue(any(button.text == "ផ្ញើសារទៅអ្នកប្រើទាំងអស់" for row in admin.keyboard for button in row))
        self.assertTrue(any(button.text == "បង្កើតកូដបញ្ចុះតម្លៃ" for row in admin.keyboard for button in row))
        self.assertTrue(any(button.text == "កែព័ត៌មានជំនួយ/ឆានែល" for row in admin.keyboard for button in row))

    def test_settings_cancel_button_uses_blue_style(self) -> None:
        cancel_button = admin_settings_input_menu().keyboard[0][0]

        self.assertEqual(cancel_button.text, "បោះបង់")
        self.assertEqual(cancel_button.style, ButtonStyle.PRIMARY)

    def test_tenant_product_buttons_use_owner_emoji_or_no_default(self) -> None:
        product = {
            "id": 1,
            "name": "Canva Pro",
            "price_cents": 100,
            "status": "available",
            "level_id": 1,
        }
        master_button = products_keyboard([product]).inline_keyboard[0][0]
        custom_button = products_keyboard([product], custom_emoji_id="1234567890", show_default_icon=False).inline_keyboard[0][0]
        no_icon_button = products_keyboard([product], show_default_icon=False).inline_keyboard[0][0]
        service_button = service_options_keyboard(
            [{**product, "service_kind": "game_service"}],
            custom_emoji_id="1234567890",
            show_default_icon=False,
        ).inline_keyboard[0][0]

        self.assertEqual(master_button.icon_custom_emoji_id, CUSTOM_EMOJI_IDS["product"])
        self.assertEqual(custom_button.icon_custom_emoji_id, "1234567890")
        self.assertIsNone(no_icon_button.icon_custom_emoji_id)
        self.assertEqual(service_button.icon_custom_emoji_id, "1234567890")

    def test_button_emoji_override_is_scoped_to_selected_target(self) -> None:
        token = set_button_emoji_overrides({"cart": "1234567890", "__hide_defaults__": "true"})
        try:
            cart = button("Cart", "cart", icon="cart")
            product = button("Product", "product", icon="product")
        finally:
            reset_button_emoji_overrides(token)
        master_cart = button("Cart", "cart", icon="cart")

        self.assertEqual(cart.icon_custom_emoji_id, "1234567890")
        self.assertIsNone(product.icon_custom_emoji_id)
        self.assertEqual(master_cart.icon_custom_emoji_id, CUSTOM_EMOJI_IDS["cart"])

    def test_only_master_menu_shows_customer_bot_creation(self) -> None:
        master_keyboard = main_menu().keyboard
        master_texts = {button.text for row in master_keyboard for button in row}
        tenant_texts = {
            button.text
            for row in main_menu(include_create_bot=False).keyboard
            for button in row
        }

        self.assertIn("បង្កើត bot ផ្ទាល់ខ្លួន", master_texts)
        self.assertNotIn("បង្កើត bot ផ្ទាល់ខ្លួន", tenant_texts)
        self.assertEqual(len(master_keyboard[0]), 2)
        self.assertEqual(master_keyboard[0][1].text, "Top up")
        self.assertEqual(len(master_keyboard[1]), 2)
        self.assertEqual(len(master_keyboard[2]), 2)

    def test_discount_code_button_is_below_payment_methods(self) -> None:
        rows = payment_method_keyboard(27).inline_keyboard

        self.assertEqual([button.callback_data for button in rows[0]], ["pay:27:aba", "pay:27:wallet"])
        self.assertEqual(rows[1][0].callback_data, "discount:27")
        self.assertEqual(rows[1][0].text, "បញ្ចូលកូដបញ្ចុះតម្លៃ")

    def test_discount_code_normalizes_copy_paste_formatting(self) -> None:
        self.assertEqual(_normalize_discount_code("1246GFJGHFJT09"), "1246GFJGHFJT09")
        self.assertEqual(_normalize_discount_code(" 1246\u200bGFJGHFJT09 "), "1246GFJGHFJT09")

    def test_welcome_text_mentions_requested_products_and_contacts(self) -> None:
        text = _welcome_text()
        for item in ("Free Fire", "Robux Gift Card", "Pro"):
            self.assertIn(item, text)
        self.assertIn("អរគុណច្រើន", text)
        self.assertNotIn("គាំទ្រ ROKIRA", text)
        self.assertNotIn("ROVISTARCAMBO", text)
        self.assertNotIn("ROVISTARKH", text)
        for key in ("mobile", "verified", "add_more", "heart", "warning", "cross", "wallet_icon", "gallery"):
            self.assertIn(f'emoji-id="{CUSTOM_EMOJI_IDS[key]}"', text)

    def test_welcome_addresses_user_and_uses_configured_contacts(self) -> None:
        text = _welcome_text(
            user_name="Sok & Dara",
        )

        self.assertIn("សួស្តី បងប្រុស Sok &amp; Dara មកកាន់ ROKIRA", text)
        self.assertNotIn("Support:", text)
        self.assertNotIn("ឆានែលផ្លូវការ", text)

    def test_tenant_welcome_omits_master_brand_and_accepts_shop_name(self) -> None:
        generic = _welcome_text(user_name="Sok", shop_name="")
        branded = _welcome_text(user_name="Sok", shop_name="JASON Shop")

        self.assertIn("សួស្តី បងប្រុស Sok", generic)
        self.assertNotIn("ROKIRA", generic)
        self.assertIn("មកកាន់ JASON Shop", branded)
        self.assertNotIn("ROKIRA", branded)

    def test_welcome_buttons_use_configured_links_and_red_style(self) -> None:
        buttons = welcome_actions(support_username="HELPER", channel_url="https://t.me/SHOPKH").inline_keyboard[0]

        self.assertEqual([button.url for button in buttons], ["https://t.me/SHOPKH", "https://t.me/HELPER"])
        self.assertTrue(all(button.style == ButtonStyle.DANGER for button in buttons))

    def test_contact_destination_normalizers_reject_unsafe_urls(self) -> None:
        self.assertEqual(_normalize_support_username("@HELPER_1"), "HELPER_1")
        self.assertEqual(_normalize_support_username("https://t.me/HELPER"), "HELPER")
        self.assertIsNone(_normalize_support_username("https://example.com/HELPER"))
        self.assertEqual(_normalize_channel_url("@SHOPKH"), "https://t.me/SHOPKH")
        self.assertEqual(_normalize_channel_url("https://t.me/+Invite_123"), "https://t.me/+Invite_123")
        self.assertIsNone(_normalize_channel_url("https://example.com/SHOPKH"))


class AdminContactTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_settings_persist_and_return_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "settings.sqlite3", Fernet.generate_key().decode("ascii"))
            await store.initialize()

            self.assertEqual(await store.get_setting("support_username", "ROVISTARCAMBO"), "ROVISTARCAMBO")
            await store.set_setting("support_username", "NEWOWNER")
            self.assertEqual(await store.get_setting("support_username"), "NEWOWNER")

    async def test_khqr_secret_settings_are_encrypted_at_rest(self) -> None:
        import aiosqlite

        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "settings.sqlite3", Fernet.generate_key().decode("ascii"))
            await store.initialize()
            secret = "merchant-secret-value"
            await store.set_secret_setting("khqr_secret_key", secret)
            self.assertEqual(await store.get_secret_setting("khqr_secret_key"), secret)
            async with aiosqlite.connect(store.path) as db:
                row = await (await db.execute(
                    "SELECT setting_value FROM bot_settings WHERE setting_key = 'secret:khqr_secret_key'"
                )).fetchone()
            self.assertNotEqual(row[0], secret)

    async def test_contact_card_uses_current_photo_and_requested_username(self) -> None:
        class BotDouble:
            def __init__(self) -> None:
                self.photo_request_user_id = None
                self.sent_photo = None

            async def get_user_profile_photos(self, user_id: int, limit: int):
                self.photo_request_user_id = user_id
                return SimpleNamespace(photos=[[SimpleNamespace(file_id="latest-photo")]])

            async def get_chat(self, user_id: int):
                raise AssertionError("configured contact ID should not need admin permissions")

            async def send_photo(self, **kwargs):
                self.sent_photo = kwargs

            async def send_message(self, *args, **kwargs):
                raise AssertionError("a current profile photo is available")
        bot = BotDouble()
        await _send_admin_contact(bot, 10, frozenset(), contact_user_id=20, username="NEWOWNER")
        self.assertEqual(bot.photo_request_user_id, 20)
        self.assertEqual(bot.sent_photo["photo"], "latest-photo")
        button = bot.sent_photo["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.url, "https://t.me/NEWOWNER")
        self.assertTrue(str(button.style).lower().endswith("danger"))

    async def test_admin_help_button_is_red_and_targets_configured_admin(self) -> None:
        contact = admin_contact_keyboard("HELPER_ADMIN").inline_keyboard[0][0]
        self.assertEqual(contact.url, "https://t.me/HELPER_ADMIN")
        self.assertEqual(contact.style, ButtonStyle.DANGER)


if __name__ == "__main__":
    unittest.main()