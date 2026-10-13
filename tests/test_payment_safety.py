import asyncio
import tempfile
import unittest
from datetime import datetime
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User
from cryptography.fernet import Fernet

from bot.database import Store
from bot.handlers import _deliver_paid_account, _parse_wallet_amount, _verify_pending_batch, buy_product
from bot.payments import _response_check, create_checkout_url


class PaymentResponseTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_wallet_amount_accepts_usd_values_with_two_decimals(self) -> None:
        self.assertEqual(_parse_wallet_amount("$7.50"), 750)
        self.assertEqual(_parse_wallet_amount("7.5 USD"), 750)
        self.assertEqual(_parse_wallet_amount("0.01"), 1)

    def test_custom_wallet_amount_rejects_invalid_precision_and_range(self) -> None:
        for value in ("abc", "0", "1000.01", "2.345", "NaN"):
            with self.subTest(value=value):
                self.assertIsNone(_parse_wallet_amount(value))


    async def test_pending_verify_requests_run_concurrently_and_keep_order(self) -> None:
        pending = [
            {"payment_tx_id": "TX1"},
            {"payment_tx_id": "TX2"},
            {"payment_tx_id": "TX3"},
        ]
        active_requests = 0
        max_active_requests = 0

        async def fake_verify(*, transaction_id, secret_key, verify_url, session):
            nonlocal active_requests, max_active_requests
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            await asyncio.sleep(0.02)
            active_requests -= 1
            return SimpleNamespace(status="success", transaction_id=transaction_id)

        with patch("bot.handlers.verify_transaction", side_effect=fake_verify):
            results = await _verify_pending_batch(pending, "secret", "https://verify.test", object())

        self.assertGreater(max_active_requests, 1)
        self.assertEqual([result.transaction_id for result in results], ["TX1", "TX2", "TX3"])

    def test_pending_response_is_not_success(self) -> None:
        result = _response_check({"responseCode": 0, "data": {"status": "pending", "amount": "34.00"}})
        self.assertEqual(result.status, "pending")

    def test_success_requires_integer_zero_response_code(self) -> None:
        result = _response_check({"responseCode": False, "data": {"status": "success", "amount": "34.00"}})
        self.assertEqual(result.status, "error")

    def test_success_requires_whole_cent_amount(self) -> None:
        result = _response_check({"responseCode": 0, "data": {"status": "success", "amount": "34.001"}})
        self.assertEqual(result.status, "error")

    def test_verified_success_returns_amount_in_cents(self) -> None:
        result = _response_check({"responseCode": 0, "data": {"status": "success", "amount": "34.00"}})
        self.assertEqual((result.status, result.amount_cents), ("success", 3400))

    def test_checkout_url_matches_khqrcc_managed_checkout_docs(self) -> None:
        url = create_checkout_url(
            profile_id="profile-123",
            secret_key="test-secret",
            transaction_id="ORDER_10001",
            amount_cents=3600,
            success_url="https://example.com/payment/success",
            remark="Order 10001",
        )
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/payment/requestv2/profile-123")
        self.assertEqual(params["transaction_id"], ["ORDER_10001"])
        self.assertEqual(params["amount"], ["36"])
        expected_hash = hashlib.sha1(
            "test-secretORDER_1000136https://example.com/payment/successOrder 10001".encode("utf-8")
        ).hexdigest()
        self.assertEqual(params["hash"], [expected_hash])

    def test_decimal_checkout_amount_uses_php_float_text(self) -> None:
        url = create_checkout_url(
            profile_id="profile-123",
            secret_key="test-secret",
            transaction_id="ORDER_10002",
            amount_cents=1250,
            success_url="https://example.com/payment/success",
            remark="Order 10002",
        )
        self.assertEqual(parse_qs(urlsplit(url).query)["amount"], ["12.5"])


class PaidOrderGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "orders.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(12345, "test", "test_user")
        self.product_id = await self.store.create_product(
            game="Free Fire",
            level="67",
            name="Test account",
            price_cents=3400,
            photo_file_ids=["photo-id"],
            account_email="test@example.com",
            account_password="test-password",
            created_by=1,
        )
        self.order = await self.store.create_order(self.product_id, 12345)
        assert self.order is not None

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_amount_mismatch_does_not_complete_or_release_account(self) -> None:
        result = await self.store.complete_paid_order(self.order["id"], verified_amount_cents=3399)
        self.assertIsNone(result)
        pending = await self.store.pending_orders()
        self.assertEqual(len(pending), 1)

    async def test_expired_order_cannot_complete(self) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.store.path) as db:
            await db.execute("UPDATE orders SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (self.order["id"],))
            await db.commit()
        result = await self.store.complete_paid_order(self.order["id"], verified_amount_cents=3400)
        self.assertIsNone(result)

    async def test_discounted_khqr_order_requires_exact_discounted_amount(self) -> None:
        await self.store.create_discount_code("SAVE10", 10, 1)
        self.assertEqual(await self.store.apply_discount_code(12345, "save10"), 10)
        product_id = await self.store.create_product(
            game="Free Fire",
            level="68",
            name="Discounted account",
            price_cents=3400,
            photo_file_ids=["photo-id"],
            account_email="discount@example.com",
            account_password="discount-password",
            created_by=1,
        )

        order = await self.store.create_order(product_id, 12345)

        self.assertEqual(order["price_cents"], 3060)
        self.assertIsNone(await self.store.complete_paid_order(order["id"], verified_amount_cents=3400))
        paid = await self.store.complete_paid_order(order["id"], verified_amount_cents=3060)
        self.assertEqual(paid["price_cents"], 3060)

    async def test_cancelled_discounted_order_restores_code(self) -> None:
        await self.store.create_discount_code("SAVE10", 10, 1)
        await self.store.apply_discount_code(12345, "SAVE10")
        product_id = await self.store.create_product(
            game="Free Fire",
            level="69",
            name="Restorable discount account",
            price_cents=3400,
            photo_file_ids=["photo-id"],
            account_email="restore@example.com",
            account_password="restore-password",
            created_by=1,
        )

        order = await self.store.create_order(product_id, 12345)
        self.assertEqual(order["price_cents"], 3060)
        self.assertIsNotNone(await self.store.cancel_order(order["id"], user_id=12345))

        self.assertEqual(await self.store.apply_discount_code(12345, "SAVE10"), 10)


class WalletPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "wallet-purchase.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(12345, "customer", "customer_name")
        self.product_id = await self.store.create_product(
            game="Software",
            level="License",
            name="Canva Pro",
            price_cents=3400,
            photo_file_ids=["product-photo"],
            account_email="",
            account_password="",
            created_by=1,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def _fund_wallet(self, amount_cents: int) -> None:
        topup = await self.store.create_wallet_topup(12345, amount_cents, amount_cents)
        self.assertIsNotNone(await self.store.complete_wallet_topup(topup["id"], amount_cents))

    async def test_insufficient_wallet_does_not_deduct_or_reserve_product(self) -> None:
        await self._fund_wallet(2000)

        result = await self.store.pay_product_with_wallet(self.product_id, 12345)

        self.assertEqual(result, {"status": "insufficient", "balance_cents": 2000, "price_cents": 3400})
        self.assertEqual(await self.store.wallet_balance_cents(12345), 2000)
        self.assertEqual((await self.store.available_product(self.product_id))["status"], "available")
        self.assertEqual(await self.store.pending_orders(), [])

    async def test_sufficient_wallet_deducts_exactly_and_marks_order_paid(self) -> None:
        await self._fund_wallet(5000)

        result = await self.store.pay_product_with_wallet(self.product_id, 12345)

        self.assertEqual(result["status"], "paid")
        self.assertEqual(result["balance_cents"], 1600)
        self.assertEqual(await self.store.wallet_balance_cents(12345), 1600)
        self.assertEqual((await self.store.available_product(self.product_id))["status"], "sold")
        history = await self.store.order_history(12345)
        self.assertEqual([(order["status"], order["price_cents"]) for order in history], [("paid", 3400)])

    async def test_concurrent_wallet_purchase_cannot_charge_twice(self) -> None:
        await self._fund_wallet(5000)

        first, second = await asyncio.gather(
            self.store.pay_product_with_wallet(self.product_id, 12345),
            self.store.pay_product_with_wallet(self.product_id, 12345),
        )

        self.assertEqual([first["status"], second["status"]].count("paid"), 1)
        self.assertEqual([first["status"], second["status"]].count("unavailable"), 1)
        self.assertEqual(await self.store.wallet_balance_cents(12345), 1600)

    async def test_wallet_purchase_deducts_discounted_total(self) -> None:
        await self._fund_wallet(4000)
        await self.store.create_discount_code("SAVE10", 10, 1)
        self.assertEqual(await self.store.apply_discount_code(12345, "SAVE10"), 10)

        result = await self.store.pay_product_with_wallet(self.product_id, 12345)

        self.assertEqual(result["price_cents"], 3060)
        self.assertEqual(result["balance_cents"], 940)
        self.assertEqual((await self.store.order_history(12345))[0]["price_cents"], 3060)


class PaidDigitalProductTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "digital.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(12345, "customer", "customer_name")

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_verified_product_can_have_delivery_without_credentials(self) -> None:
        product_id = await self.store.create_product(
            game="Software",
            level="License",
            name="Canva Pro",
            price_cents=500,
            photo_file_ids=["cover-photo"],
            account_email="",
            account_password="",
            created_by=1,
            delivery_url="https://example.com/download?key=value",
        )
        order = await self.store.create_order(product_id, 12345)
        self.assertIsNotNone(order)

        delivered = await self.store.complete_paid_order(order["id"], verified_amount_cents=500)

        self.assertIsNotNone(delivered)
        self.assertEqual(delivered["account_email"], "")
        self.assertEqual(delivered["account_password"], "")
        self.assertEqual(delivered["delivery_url"], "https://example.com/download?key=value")


class PaidDeliveryMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_file_is_sent_after_paid_order_message(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        order = {
            "id": 71,
            "user_id": 12345,
            "name": "Canva Pro",
            "game_name": "Software",
            "level_name": "License",
            "price_cents": 500,
            "account_email": "",
            "account_password": "",
            "delivery_file_id": "telegram-document-id",
            "delivery_url": "",
        }

        await _deliver_paid_account(bot, order, frozenset())

        sent_message = bot.send_message.await_args.args[1]
        self.assertIn("ការបង់ប្រាក់ជោគជ័យ", sent_message)
        self.assertNotIn("Gmail:", sent_message)
        self.assertNotIn("Password:", sent_message)
        bot.send_document.assert_awaited_once()
        self.assertEqual(bot.send_document.await_args.args[:2], (12345, "telegram-document-id"))

    async def test_download_link_is_clickable_in_verified_order_message(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        order = {
            "id": 72,
            "user_id": 12345,
            "name": "Canva Pro",
            "game_name": "Software",
            "level_name": "License",
            "price_cents": 500,
            "account_email": "",
            "account_password": "",
            "delivery_file_id": "",
            "delivery_url": "https://example.com/download?a=1&b=2",
        }

        await _deliver_paid_account(bot, order, frozenset())

        sent_message = bot.send_message.await_args.args[1]
        self.assertIn('<a href="https://example.com/download?a=1&amp;b=2">ចុចទីនេះ</a>', sent_message)
        bot.send_document.assert_not_awaited()


class WalletTopupGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "wallet.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(12345, "test", "test_user")
        self.topup = await self.store.create_wallet_topup(12345, 500, 525)
        self.assertTrue(await self.store.set_wallet_topup_transaction(self.topup["id"], "TOPUP_TEST_1"))

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_exact_verified_amount_credits_once(self) -> None:
        credited = await self.store.complete_wallet_topup(self.topup["id"], verified_amount_cents=500)

        self.assertIsNotNone(credited)
        self.assertEqual(credited["balance_cents"], 525)
        self.assertEqual(credited["first_name"], "test")
        self.assertEqual(credited["username"], "test_user")
        self.assertIsNone(await self.store.complete_wallet_topup(self.topup["id"], verified_amount_cents=500))
        self.assertEqual(await self.store.wallet_balance_cents(12345), 525)

    async def test_amount_mismatch_does_not_credit_wallet(self) -> None:
        result = await self.store.complete_wallet_topup(self.topup["id"], verified_amount_cents=499)

        self.assertIsNone(result)
        self.assertEqual(await self.store.wallet_balance_cents(12345), 0)
        self.assertEqual(len(await self.store.pending_wallet_topups()), 1)

    async def test_expired_topup_cannot_credit_wallet(self) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.store.path) as db:
            await db.execute(
                "UPDATE wallet_topups SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (self.topup["id"],),
            )
            await db.commit()

        result = await self.store.complete_wallet_topup(self.topup["id"], verified_amount_cents=500)
        self.assertIsNone(result)
        self.assertEqual(await self.store.wallet_balance_cents(12345), 0)


class AdminCredentialEditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "accounts.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(12345, "test", None)
        self.product_id = await self.store.create_product(
            game="Free Fire",
            level="67",
            name="Test account",
            price_cents=3400,
            photo_file_ids=["photo-id"],
            account_email="old@example.com",
            account_password="old-password",
            created_by=1,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_credential_replacements_are_encrypted(self) -> None:
        self.assertTrue(await self.store.update_product_email(self.product_id, "new@example.com"))
        self.assertTrue(await self.store.update_product_password(self.product_id, "new-password"))
        import aiosqlite

        async with aiosqlite.connect(self.store.path) as db:
            row = await (await db.execute(
                "SELECT account_email_encrypted, account_password_encrypted FROM products WHERE id = ?",
                (self.product_id,),
            )).fetchone()
        self.assertEqual(self.store._cipher.decrypt(row[0].encode("ascii")).decode("utf-8"), "new@example.com")
        self.assertEqual(self.store._cipher.decrypt(row[1].encode("ascii")).decode("utf-8"), "new-password")

    async def test_email_preview_is_masked_for_admin_identification(self) -> None:
        self.assertEqual(await self.store.product_email_preview(self.product_id), "ol***@example.com")

    async def test_admin_can_retrieve_credentials_for_available_product(self) -> None:
        self.assertEqual(
            await self.store.admin_product_credentials(self.product_id),
            {"email": "old@example.com", "password": "old-password"},
        )

    async def test_reserved_account_rejects_credential_changes(self) -> None:
        order = await self.store.create_order(self.product_id, 12345)
        self.assertIsNotNone(order)
        self.assertIsNone(await self.store.admin_product_credentials(self.product_id))
        self.assertFalse(await self.store.update_product_email(self.product_id, "new@example.com"))
        self.assertFalse(await self.store.update_product_password(self.product_id, "new-password"))


class TelegramServiceOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "services.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(12345, "customer", "customer")
        self.service_id = await self.store.create_product(
            game="Telegram Premium",
            level="សេវាកម្ម",
            name="3 ខែ",
            price_cents=1200,
            photo_file_ids=[],
            account_email="",
            account_password="",
            created_by=1,
            service_kind="telegram_premium",
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_service_option_is_reusable_and_keeps_username_for_both_payment_paths(self) -> None:
        pending = await self.store.create_order(self.service_id, 12345, "@first_user")
        self.assertEqual(pending["telegram_username"], "@first_user")
        self.assertEqual(pending["service_kind"], "telegram_premium")
        self.assertEqual((await self.store.available_product(self.service_id))["status"], "available")
        self.assertFalse(await self.store.hide_product(self.service_id))

        paid = await self.store.complete_paid_order(pending["id"], verified_amount_cents=1200)
        self.assertEqual(paid["telegram_username"], "@first_user")
        self.assertEqual((await self.store.available_product(self.service_id))["status"], "available")

        import aiosqlite

        async with aiosqlite.connect(self.store.path) as db:
            await db.execute("UPDATE wallet_accounts SET balance_cents = 2500 WHERE user_id = 12345")
            await db.commit()
        wallet_paid = await self.store.pay_product_with_wallet(self.service_id, 12345, "@second_user")
        self.assertEqual(wallet_paid["telegram_username"], "@second_user")
        self.assertEqual(wallet_paid["service_kind"], "telegram_premium")
        self.assertEqual(await self.store.wallet_balance_cents(12345), 1300)
        self.assertEqual((await self.store.available_product(self.service_id))["status"], "available")

    async def test_game_service_order_persists_two_customer_ids(self) -> None:
        game_service_id = await self.store.create_product(
            game="Mobile Legends",
            level="Top up",
            name="100 Diamonds",
            price_cents=500,
            photo_file_ids=["service-photo"],
            account_email="",
            account_password="",
            created_by=1,
            service_kind="game_service",
            identity_fields=["Game ID", "Zone/Server ID"],
        )
        identity_values = {"Game ID": "987654321", "Zone/Server ID": "1234"}
        order = await self.store.create_order(
            game_service_id,
            12345,
            "@player_user",
            identity_values,
        )
        self.assertEqual(order["identity_values"], identity_values)
        pending = (await self.store.pending_orders())[0]
        self.assertEqual(pending["identity_values"], identity_values)
        paid = await self.store.complete_paid_order(order["id"], verified_amount_cents=500)
        self.assertEqual(paid["telegram_username"], "@player_user")
        self.assertEqual(paid["identity_values"], identity_values)


class DisabledCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_checkout_does_not_reserve_or_create_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "disabled.sqlite3", Fernet.generate_key().decode("ascii"))
            await store.initialize()
            product_id = await store.create_product(
                game="Free Fire",
                level="67",
                name="Test account",
                price_cents=3400,
                photo_file_ids=["photo-id"],
                account_email="test@example.com",
                account_password="test-password",
                created_by=1,
            )
            answers = []

            async def answer(text: str = "", **kwargs) -> None:
                answers.append(text)

            message = Message(
                message_id=1,
                date=datetime.now(),
                chat=Chat(id=12345, type=ChatType.PRIVATE),
            )
            object.__setattr__(message, "answer", answer)
            query = SimpleNamespace(
                message=message,
                data=f"buy:{product_id}",
                from_user=User(id=12345, is_bot=False, first_name="test"),
                answer=answer,
            )
            await buy_product(query, store)
            self.assertTrue(answers)
            self.assertEqual(await store.pending_orders(), [])
            product = await store.available_product(product_id)
            self.assertEqual(product["status"], "available")


if __name__ == "__main__":
    unittest.main()