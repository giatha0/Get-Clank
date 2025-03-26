import os
import re
import json
import logging
import requests

from flask import Flask, request, jsonify
from telegram import Bot, Update, ParseMode
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext
from web3 import Web3

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEB3_PROVIDER_URL = os.environ.get("WEB3_PROVIDER_URL")

if not all([TELEGRAM_BOT_TOKEN, API_BASESCAN, BASESCAN_API_KEY, WEBHOOK_URL, WEB3_PROVIDER_URL]):
    logger.error("❌ Missing environment variables.")
    exit(1)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot, None, use_context=True)

w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER_URL))
try:
    with open("abi.json", "r") as f:
        abi = json.load(f)
    contract = w3.eth.contract(abi=abi)
    logger.info("✅ ABI loaded successfully.")
except Exception as e:
    logger.error(f"❌ Error loading ABI: {e}")
    exit(1)

app = Flask(__name__)

# Định nghĩa dictionary địa chỉ → nhãn
ADDRESS_LABELS = {
    "0x2112b8456AC07c15fA31ddf3Bf713E77716fF3F9".lower(): "bnkr deployer",
    "0xd9aCd656A5f1B519C9E76a2A6092265A74186e58".lower(): "clanker interface"
    # Bạn có thể thêm nữa theo định dạng .lower()
}

def get_creation_txhash(contract_address: str) -> str:
    try:
        logger.info(f"🔍 Getting creation txhash from BaseScan for contract {contract_address}")
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract_address,
            "apikey": BASESCAN_API_KEY
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        results = data.get("result", [])
        if not results or not isinstance(results, list):
            logger.error(f"❌ No result for contract {contract_address}")
            return None
        txhash = results[0].get("txHash")
        logger.info(f"✅ Found txhash: {txhash}")
        return txhash
    except Exception as e:
        logger.error(f"❌ Error fetching txhash: {e}")
        return None

def get_transaction_data(txhash: str) -> dict:
    try:
        logger.info(f"📦 Fetching transaction data for txhash: {txhash}")
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txhash,
            "apikey": BASESCAN_API_KEY
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        logger.info("✅ Transaction data retrieved.")
        return data.get("result", {})
    except Exception as e:
        logger.error(f"❌ Error fetching transaction data: {e}")
        return {}

def decode_input_with_web3(input_hex: str):
    try:
        logger.info("🔓 Decoding input with Web3...")
        func_obj, func_args = contract.decode_function_input(input_hex)
        logger.info(f"✅ Decoded function: {func_obj.fn_name}")
        return {"function": func_obj.fn_name, "args": func_args}
    except Exception as e:
        logger.error(f"❌ Error decoding input: {e}")
        return None

def handle_message(update: Update, context: CallbackContext):
    try:
        msg_text = update.message.text.strip()
        logger.info(f"📨 Received message: {msg_text}")

        if not re.match(r"^0x[a-fA-F0-9]{40}$", msg_text):
            logger.warning("⚠️ Not a valid contract address.")
            return

        update.message.reply_text(f"Processing contract: `{msg_text}`", parse_mode=ParseMode.MARKDOWN)

        txhash = get_creation_txhash(msg_text)
        if not txhash:
            update.message.reply_text("Could not find txhash from BaseScan.")
            return

        tx_data = get_transaction_data(txhash)
        if not tx_data:
            update.message.reply_text("Failed to retrieve transaction data from BaseScan.")
            return

        # Lấy from address
        from_address = tx_data.get("from")
        if not from_address:
            update.message.reply_text("No 'from' address found in the transaction.")
            return

        # Kiểm tra xem from_address có nằm trong ADDRESS_LABELS không
        # So sánh ở dạng .lower() để nhất quán
        from_label = ADDRESS_LABELS.get(from_address.lower())
        if from_label:
            # Nếu có label, thay thế from_address bằng label
            display_from = f"{from_label} ({from_address})"
        else:
            # Ngược lại, hiển thị địa chỉ như bình thường
            display_from = from_address

        input_data_raw = tx_data.get("input", "")
        if not input_data_raw:
            update.message.reply_text("No input data found in the transaction.")
            return

        logger.info(f"🔍 Input data raw (first 20 chars): {input_data_raw[:20]}... (length: {len(input_data_raw)})")

        decoded = decode_input_with_web3(input_data_raw)
        if not decoded:
            update.message.reply_text("Error decoding input data.")
            return

        if decoded.get("function") != "deployToken":
            update.message.reply_text(f"This is not a deployToken transaction (function: {decoded.get('function')}).")
            return

        deployment_config = decoded.get("args", {}).get("deploymentConfig")
        if not deployment_config:
            update.message.reply_text("deploymentConfig not found in the input data.")
            return

        token_config = deployment_config.get("tokenConfig", {})
        rewards_config = deployment_config.get("rewardsConfig", {})

        name = token_config.get("name")
        symbol = token_config.get("symbol")
        image = token_config.get("image")
        chain_id = token_config.get("originatingChainId")
        creator_reward_recipient = rewards_config.get("creatorRewardRecipient")

        context_raw = token_config.get("context")
        try:
            context_json = json.loads(context_raw)
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse context JSON: {e}")
            context_json = {"context": context_raw}

        context_lines = []
        if isinstance(context_json, dict):
            for key, value in context_json.items():
                if value and str(value).strip():
                    if key == "messageId":
                        context_lines.append(f"{key}: [Link]({value})")
                    else:
                        context_lines.append(f"{key}: {value}")
        else:
            context_lines.append(str(context_json))
        context_formatted = "\n".join(context_lines)

        reply = (
            f"*Token Deployment Information:*\n\n"
            f"*From:* `{display_from}`\n"
            f"*Name:* `{name}`\n"
            f"*Symbol:* `{symbol}`\n"
            f"*Chain ID:* `{chain_id}`\n"
            f"*Image:* [Link]({image})\n\n"
            f"*Context:*\n{context_formatted}\n\n"
            f"*Creator Reward Recipient:* `{creator_reward_recipient}`"
        )

        update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        logger.info("✅ Bot has responded successfully.")
    except Exception as e:
        logger.exception(f"❌ Unhandled error in handle_message: {e}")

def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot is ready. Please send a token contract address to process.")

dp.add_handler(CommandHandler("start", start_command))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"📨 Received update from Telegram: {data}")
        update_obj = Update.de_json(data, bot)
        dp.process_update(update_obj)
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception(f"❌ Error processing webhook: {e}")
        return jsonify({"ok": False}), 500

@app.route("/", methods=["GET"])
def index():
    return "🤖 Clanker Bot is running (Flask webhook)."

def main():
    bot.delete_webhook(drop_pending_updates=True)
    hook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
    if not bot.set_webhook(url=hook_url):
        logger.error("❌ Failed to set webhook with Telegram.")
        exit(1)
    logger.info(f"✅ Webhook has been set: {hook_url}")

    port = int(os.environ.get("PORT", 80))
    logger.info(f"🚀 Starting Flask server on port {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()