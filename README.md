# ROKIRA Telegram Bot

A Telegram-only Free Fire account shop built with aiogram 3. The customer and admin menus are persistent reply keyboards displayed at the bottom of Telegram, not inline keyboards attached to the welcome message. Admin menu keys are shown only to IDs in the allowlist.

## Setup on Windows

Requirements: Python 3.10 or newer.

1. Create a bot with BotFather.
2. Create the Python environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Generate a new encryption key for account credentials:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

4. Copy `.env.example` to `.env`. Set `TELEGRAM_BOT_TOKEN`, put the generated value in `ACCOUNT_DATA_KEY`, and copy the actual `KHQRCC_SECRET_KEY` from your KHQRcc merchant dashboard. The supplied profile ID and admin IDs are prefilled; adjust the admin list if needed. Keep all keys private.
5. Start the bot:

```powershell
python -m bot.main
```

## Deployka

Deploy this repository as a Telegram bot or Docker project. Deployka can build the included `Dockerfile`; it starts the bot with `python -m bot.main` and does not need an inbound port because Telegram updates are received through long polling.

Add the variables from `.env.example` in the Deployka project environment settings. Do not upload `.env` or put secrets in the image. `TELEGRAM_BOT_TOKEN`, `ADMIN_USER_IDS`, and `ACCOUNT_DATA_KEY` are required. Generate `ACCOUNT_DATA_KEY` locally using the command above. Keep `PAYMENTS_ENABLED=false` until the KHQR merchant credentials and verification flow are configured.

Attach persistent storage to `/data` and set `DATABASE_PATH=/data/bot_data.sqlite3`; relative database paths are redirected to `/data` when it exists. Without persistent storage, SQLite data may be lost when the container is rebuilt. Deployka's `KHMER_TOPUP_GAME_SLUGS` setting is not used by this ROKIRA bot; that setting belongs to a different application/configuration.

## Customer menu and catalog

`/start` shows a resizable, collapsible bottom keyboard that stays available after a button is pressed; `/menu` reopens it after it is collapsed in the Telegram client. Regular accounts and Pro products appear under Browse products. Telegram Premium, Stars, and configured game services have a separate Top up menu with package-and-price buttons sorted from low to high. Game service listings collect the customer's Telegram username and the configured Game UID or Game ID plus Zone/Server ID; Pro/software products do not request those identifiers. Customers choose a regular category and immediately receive each listed product's photos, type/plan, description, price, and purchase button; there is no separate level/account picker. Available products have green purchase buttons; reserved and sold products have red status buttons. Products may have any number of photos starting from one. Telegram albums are sent in chunks of up to ten photos; one-photo products show their details and purchase button in the photo caption.

A customer sees the product photos and details, then presses “ទិញឥឡូវនេះ” and chooses ABA Mobile or the bot wallet. The discount-code button is directly below those payment methods; after entering a code, the customer returns to the same checkout choices. ABA creates a direct KHQR payment card and the order remains pending until the provider verifies the exact payment. Wallet checkout atomically checks the internal balance, deducts the exact product price, marks the order paid, and sells the product; if funds are insufficient, nothing is deducted or reserved and the customer is offered top-up options. After successful external verification or wallet payment, the bot sends any configured Gmail/password and either the attached document or HTTPS download link. The bot never sends credentials or digital delivery based on a redirect or user claim.

Customers may enter a custom wallet top-up from $0.01 to $1,000.00, with at most two decimal places. Existing preset top-up amounts and bonus rules are unchanged.

## KHQR verification

The checkout request is signed with `sha1(secret + transaction_id + amount + success_url + remark)`. The bot polls the supplied Verify V2 endpoint every second for the three-minute checkout window, checking up to ten pending transactions concurrently, using `sha1(secret + transaction_id)`. It only fulfills an order if the provider returns `responseCode: 0`, `data.status: success`, and an amount exactly matching the order in USD cents. Mismatches are not fulfilled and admins are notified.

`PAYMENTS_ENABLED` defaults to `false`. The merchant profile must have the KHQRcc ABA Access Plan enabled and its managed checkout page must display a QR for the order amount. Set it to `true` only after that is confirmed; this enables checkout and Verify V2 polling.

The profile ID from the provided integration notes is in `.env.example`. `KHQRCC_SECRET_KEY` is deliberately a placeholder; payment cannot start until you enter the merchant secret. The default success URL opens the bot again and is only a return destination; the backend Verify V2 response is the payment authority. Set `KHQRCC_SUCCESS_URL` to another HTTPS destination if your merchant setup requires it. If KHQR configuration is missing, the bot refuses to reserve the account and alerts the customer.

## Admin dashboard

Only Telegram user IDs in `ADMIN_USER_IDS` can access `/admin` or the dashboard key shown on their `/start` keyboard. In private chat, admins can:

- Add a product: enter category, type/plan, one or more photos, product name, optional formatted description, and USD price. Gmail and password are optional; attach a Telegram document or HTTPS delivery link to send after verified payment, or skip delivery. Review and confirm to publish and broadcast the stock announcement.
- Manage Telegram services: open the Telegram services panel and choose Premium or Stars. Select a numbered option button to enter its name and USD price; use Add 10 to expand the grid, then Publish to add all drafted options to the catalog or Cancel to discard them. Customers see these services as compact package-and-price buttons rather than product cards, then confirm their Telegram username before payment. Regular account and Pro product cards are unchanged. Admins receive the selected service, option, and username after successful payment verification and complete the service manually.
- Add game services: while adding a listing, choose “សេវា Game / Top up” and select whether to request Game UID or Game ID plus Zone/Server ID. Customers must confirm their Telegram username and all configured IDs before payment; admins receive these values with the order after payment.
- Change KHQR credentials: open the KHQR settings panel to replace the Profile ID or Secret Key. Profile changes update the Verify V2 URL automatically unless `KHQRCC_VERIFY_URL` is explicitly set. The Secret Key is encrypted in SQLite using `ACCOUNT_DATA_KEY`, its chat message is deleted after receipt, and the value is never shown in the settings panel. The setting takes effect immediately and persists across restart.
- Create a customer bot: a user's first bot can be created with a one-time free seven-day trial, without inviting anyone. After using the trial, customers choose a paid plan: $2.50 for 30 days, $3.50 for 60 days, or $15 for 365 days. The setup prompt opens `@BotFather`; customers send `/newbot` there, then return to paste the HTTP API token. A format-valid token advances directly to Telegram ID entry; the customer bot runtime verifies it in the background. They then enter their Telegram ID using the `@userinfo3bot` button. Once connected, the master bot sends a one-time button to open the new bot directly. Each customer bot has its own database and storefront; use `/admin` there to add products, set a shop name, configure its KHQR Profile ID/Secret Key, and open **កែ Emoji ប៊ូតុងនីមួយៗ** to customize each button-icon category. Send a custom emoji or its numeric ID, select the target from the paginated list, and send `0` to remove that target's override; the operation can be repeated or all overrides reset. The KHQR screen's blue contact button opens the master admin; its username follows the master bot's support setting and is synchronized to tenant bots. Customer bots do not show the create-bot action and cannot create nested customer bots. Their welcome uses the tenant's `shop_name` setting, or no shop brand if unset; the master store name is not copied into tenant messages. KHQR checkout stays disabled until configured. The token and KHQR secret are encrypted. Paid subscriptions activate only after an exact master-storefront KHQR Verify V2 response. The bot stops at expiration; renewal reuses its storefront data.
- Manage accounts: select an account to see a masked Gmail preview, reveal the full Gmail/password in an allowlisted admin's private chat, or replace either value. Revealed credential messages are protected from forwarding/saving and auto-delete after 60 seconds. Sold or reserved accounts cannot be viewed or edited.
- Delete a product: select a listed product and confirm. This hides it from customer listings while retaining order history. A currently reserved product must first have its order cancelled or expire.
- View pending orders, recheck KHQR, or cancel an order to release the account.
- Edit the support username and public channel or invite link from the contact settings panel. The customer menu shows support and channel as red buttons at the bottom; saved settings remain after restart.

Admin account form inputs are sent in private chat. The bot attempts to delete Gmail and password messages after receiving them. Credentials are Fernet-encrypted before SQLite storage. Admin credential reveal is restricted to private chat and available products; credentials are sent to customers only after verified payment.

The support card links to `@kaidokmglaor`. Set optional `ADMIN_CONTACT_USER_ID` to that user's numeric Telegram ID to show their current profile photo; this value does not grant admin permissions. Without it, the contact link still works but the card has no profile photo.

## Emoji, colors, and message formatting

Custom emoji IDs for buttons and messages can be changed in `.env` without editing code. Use `ROKIRA_EMOJI_<KEY>=<numeric_custom_emoji_id>`. Supported keys are `WELCOME`, `NEW_STOCK`, `CATALOG`, `GUIDE`, `CART`, `WALLET`, `ADD`, `PRODUCT`, `ACCOUNT`, `SUPPORT`, `CHAT`, `PROFILE_MAIL`, `PROFILE_WATER`, `PROFILE_WALLET`, `PROFILE_MONEY`, `WALLET_TOPUP`, `PAY_ABA`, `PAY_KHQR`, `MOBILE`, `VERIFIED`, `ADD_MORE`, `HEART`, `CHAT_BUBBLE`, `WARNING`, `CROSS`, `WALLET_ICON`, `GALLERY`, `VOICE`, and `LINK`. For example, set `ROKIRA_EMOJI_CART=1234567890123456789`, then restart the bot. Unset keys keep their existing defaults. Ordinary Unicode emoji are removed from product labels; rich descriptions preserve Telegram formatting and custom emoji while removing ordinary emoji.

## Data and secrets

`bot_data.sqlite3` stores user IDs, product metadata, order history, photo file IDs, payment transaction IDs, wallet balances/top-ups, delivery file IDs and links, and encrypted credentials. The wallet is an internal prepaid balance in this database, not a bank account or a direct bank transfer. `.env` and database files are ignored by Git. Back up the Fernet key separately from the database; losing it makes stored account credentials unrecoverable. Never commit or share the bot token, KHQR merchant secret, account passwords, or Fernet key.
