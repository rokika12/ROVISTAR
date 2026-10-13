import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
from cryptography.fernet import Fernet

from bot.database import Store
from bot.keyboards import (
    customer_bot_plans_keyboard,
    customer_bot_setup_keyboard,
)


class CustomerBotSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "master.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(100, "Owner", "owner")

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def create_pending(self, transaction_id: str = "BOT_SETUP_TEST_1") -> int:
        return await self.store.create_customer_bot_subscription(
            owner_user_id=100,
            bot_token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRST",
            bot_username="owner_shop_bot",
            khqr_profile_id="merchant-profile-123",
            khqr_secret_key="merchant-secret-456",
            admin_user_id=100,
            payment_tx_id=transaction_id,
        )

    async def test_bot_credentials_are_encrypted_and_inactive_until_exact_payment(self) -> None:
        bot_id = await self.create_pending()
        pending = await self.store.pending_customer_bots()
        self.assertEqual(pending[0]["id"], bot_id)
        self.assertIsNone(await self.store.activate_customer_bot(bot_id, verified_amount_cents=249))
        self.assertEqual(await self.store.active_customer_bots(), [])

        async with aiosqlite.connect(self.store.path) as db:
            row = await (await db.execute(
                "SELECT bot_token_encrypted, khqr_secret_encrypted, status "
                "FROM customer_bots WHERE id = ?",
                (bot_id,),
            )).fetchone()
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", row[0])
        self.assertNotEqual(row[1], "merchant-secret-456")
        self.assertEqual(row[2], "pending")

        active = await self.store.activate_customer_bot(bot_id, verified_amount_cents=250)
        self.assertEqual(active["bot_username"], "owner_shop_bot")
        self.assertEqual(active["khqr_profile_id"], "merchant-profile-123")
        self.assertEqual(active["khqr_secret_key"], "merchant-secret-456")
        expiry = datetime.fromisoformat(active["subscription_expires_at"])
        remaining = expiry - datetime.now(expiry.tzinfo)
        self.assertGreater(remaining, timedelta(days=29, hours=23))
        self.assertEqual(len(await self.store.active_customer_bots()), 1)

    async def test_same_token_cannot_have_two_active_or_pending_subscriptions(self) -> None:
        await self.create_pending()
        with self.assertRaisesRegex(ValueError, "already has"):
            await self.create_pending("BOT_SETUP_TEST_2")

    async def test_renewal_reuses_expired_tenant_record(self) -> None:
        bot_id = await self.create_pending()
        await self.store.activate_customer_bot(bot_id, verified_amount_cents=250)
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute(
                "UPDATE customer_bots SET subscription_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (bot_id,),
            )
            await db.commit()
        self.assertEqual(await self.store.expire_customer_bots(), [{"id": bot_id, "owner_user_id": 100}])
        renewed_id = await self.create_pending("BOT_SETUP_RENEWAL_1")
        self.assertEqual(renewed_id, bot_id)
        self.assertEqual(await self.store.active_customer_bots(), [])

    async def test_expired_unpaid_setup_scrubs_credentials(self) -> None:
        bot_id = await self.create_pending()
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute(
                "UPDATE customer_bots SET payment_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (bot_id,),
            )
            await db.commit()
        self.assertEqual(await self.store.expire_pending_customer_bots(), [{"id": bot_id, "owner_user_id": 100}])
        async with aiosqlite.connect(self.store.path) as db:
            row = await (await db.execute(
                "SELECT bot_token_encrypted, khqr_secret_encrypted, status "
                "FROM customer_bots WHERE id = ?",
                (bot_id,),
            )).fetchone()
        self.assertEqual(row, ("", "", "cancelled"))

    async def test_first_time_user_gets_one_seven_day_trial(self) -> None:
        self.assertTrue(await self.store.customer_bot_trial_available(100))
        trial = await self.store.create_first_customer_bot_trial(
            owner_user_id=100,
            bot_token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRST",
            bot_username="friend_shop_bot",
            khqr_profile_id="friend-profile-123",
            khqr_secret_key="friend-secret-456",
            admin_user_id=100,
        )
        expiry = datetime.fromisoformat(trial["subscription_expires_at"])
        remaining = expiry - datetime.now(expiry.tzinfo)
        self.assertGreater(remaining, timedelta(days=6, hours=23))
        self.assertFalse(await self.store.customer_bot_trial_available(100))
        self.assertIsNone(
            await self.store.create_first_customer_bot_trial(
                owner_user_id=100,
                bot_token="987654321:ZYXWVUTSRQPONMLKJIHGFEDCBA_zyxwvutsr",
                bot_username="another_bot",
                khqr_profile_id="friend-profile-123",
                khqr_secret_key="friend-secret-456",
                admin_user_id=100,
            )
        )
        self.assertEqual(len(await self.store.active_customer_bots()), 1)

    async def test_plan_prices_and_setup_gate_require_verified_exact_payment(self) -> None:
        choices = customer_bot_plans_keyboard().inline_keyboard
        self.assertIn("$2.50", choices[0][0].text)
        self.assertIn("$3.50", choices[1][0].text)
        self.assertIn("$15.00", choices[2][0].text)
        self.assertEqual(choices[0][0].callback_data, "bot:plan:monthly")
        self.assertEqual(choices[1][0].callback_data, "bot:plan:two_months")
        self.assertEqual(choices[2][0].callback_data, "bot:plan:yearly")
        self.assertEqual(len(choices), 3)

        order = await self.store.create_customer_bot_order(100, "two_months", "BOT_PLAN_TX_1")
        self.assertEqual((order["amount_cents"], order["duration_days"]), (350, 60))
        self.assertIsNone(await self.store.mark_customer_bot_order_paid(order["id"], 250))
        self.assertIsNone(await self.store.prepare_customer_bot_setup(order["id"], 100))
        paid = await self.store.mark_customer_bot_order_paid(order["id"], 350)
        self.assertEqual(paid["status"], "pending")
        setup = await self.store.prepare_customer_bot_setup(order["id"], 100)
        self.assertEqual(setup["status"], "setup")
        self.assertEqual(
            customer_bot_setup_keyboard(order["id"]).inline_keyboard[0][0].callback_data,
            f"bot:setup:{order['id']}",
        )

        activated = await self.store.activate_paid_customer_bot_order(
            order["id"],
            100,
            bot_token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRST",
            bot_username="paid_shop_bot",
            khqr_profile_id="merchant-profile-123",
            khqr_secret_key="merchant-secret-456",
            admin_user_id=100,
        )
        self.assertEqual(activated["duration_days"], 60)
        expiry = datetime.fromisoformat(activated["subscription_expires_at"])
        remaining = expiry - datetime.now(expiry.tzinfo)
        self.assertGreater(remaining, timedelta(days=59, hours=23))


if __name__ == "__main__":
    unittest.main()
