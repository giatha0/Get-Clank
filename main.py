import os
import re
import json
import logging
import requests

from flask import Flask, request, jsonify
from telegram import Bot, Update, ParseMode
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext
from web3 import Web3

# Thiết lập logger chi tiết
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Đọc biến môi trường
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEB3_PROVIDER_URL = os.environ.get("WEB3_PROVIDER_URL")

if not all([TELEGRAM_BOT_TOKEN, API_BASESCAN, BASESCAN_API_KEY, WEBHOOK_URL, WEB3_PROVIDER_URL]):
    logger.error("❌ Thiếu biến môi trường. Vui lòng cấu hình đầy đủ.")
    exit(1)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot, None, use_context=True)

# Web3 & Contract ABI
w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER_URL))
try:
    with open("abi.json", "r") as f:
        abi = json.load(f)
    contract = w3.eth.contract(abi=abi)
    logger.info("✅ ABI đã được load.")
except Exception as e:
    logger.error(f"❌ Lỗi khi load ABI: {e}")
    exit(1)

app = Flask(__name__)

def get_creation_txhash(contract_address: str) -> str:
    try:
        logger.info(f"🔍 Đang truy vấn BaseScan để lấy txhash cho contract {contract_address}")
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract_address,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        txhash = data.get("result", [{}])[0].get("txHash")
        logger.info(f"✅ txhash tìm được: {txhash}")
        return txhash
    except Exception as e:
        logger.error(f"❌ Lỗi khi lấy txhash: {e}")
        return None

def get_transaction_data(txhash: str) -> dict:
    try:
        logger.info(f"📦 Truy vấn thông tin giao dịch cho txhash: {txhash}")
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txhash,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        logger.info("✅ Giao dịch đã lấy thành công.")
        return data.get("result", {})
    except Exception as e:
        logger.error(f"❌ Lỗi khi lấy dữ liệu giao dịch: {e}")
        return {}

def decode_input_with_web3(input_hex: str):
    try:
        logger.info(f"🔓 Bắt đầu decode input bằng Web3...")
        func_obj, func_args = contract.decode_function_input(input_hex)
        logger.info(f"✅ Đã decode: function = {func_obj.fn_name}")
        return {"function": func_obj.fn_name, "args": func_args}
    except Exception as e:
        logger.error(f"❌ Lỗi khi decode input: {e}")
        return None

def handle_message(update: Update, context: CallbackContext):
    try:
        message_text = update.message.text.strip()
        logger.info(f"📨 Tin nhắn nhận được: {message_text}")

        if not re.match(r"^0x[a-fA-F0-9]{40}$", message_text):
            logger.warning("⚠️ Không phải địa chỉ contract hợp lệ.")
            return

        update.message.reply_text(f"Đang xử lý contract: `{message_text}`", parse_mode=ParseMode.MARKDOWN)

        txhash = get_creation_txhash(message_text)
        if not txhash:
            update.message.reply_text("❌ Không tìm thấy txhash từ BaseScan.")
            return

        tx_data = get_transaction_data(txhash)
        if not tx_data:
            update.message.reply_text("❌ Không lấy được thông tin giao dịch.")
            return

        input_data = tx_data.get("input", "")
        if not input_data:
            update.message.reply_text("❌ Không có input data trong giao dịch.")
            return

        logger.info(f"🔍 Input Data Raw: {input_data[:20]}... (length: {len(input_data)})")

        decoded = decode_input_with_web3(input_data)
        if not decoded:
            update.message.reply_text("❌ Lỗi khi decode input data.")
            return

        if decoded["function"] != "deployToken":
            update.message.reply_text(f"⚠️ Đây không phải giao dịch deployToken (function: {decoded['function']})")
            return

        deployment_config = decoded["args"].get("deploymentConfig")
        if not deployment_config:
            update.message.reply_text("❌ Không tìm thấy deploymentConfig trong input.")
            return

        token_config = deployment_config.get("tokenConfig", {})
        rewards_config = deployment_config.get("rewardsConfig", {})

        name = token_config.get("name")
        symbol = token_config.get("symbol")
        image = token_config.get("image")
        metadata = token_config.get("metadata")
        context_raw = token_config.get("context")
        chain_id = token_config.get("originatingChainId")
        creator_reward_recipient = rewards_config.get("creatorRewardRecipient")

        # Parse JSON context và metadata nếu có
        try:
            metadata_json = json.loads(metadata)
        except:
            metadata_json = metadata

        try:
            context_json = json.loads(context_raw)
        except:
            context_json = context_raw

        reply = (
            f"📌 *Thông tin token deploy:*\n\n"
            f"*Tên:* `{name}`\n"
            f"*Ký hiệu:* `{symbol}`\n"
            f"*Chain ID:* `{chain_id}`\n"
            f"*Image:* [IPFS]({image})\n\n"
            f"*Metadata:*\n```json\n{json.dumps(metadata_json, ensure_ascii=False, indent=2)}\n```\n"
            f"*Context:*\n```json\n{json.dumps(context_json, ensure_ascii=False, indent=2)}\n```\n"
            f"*ID (click copy):* `{context_json.get('id', 'N/A')}`\n\n"
            f"*creatorRewardRecipient:* `{creator_reward_recipient}`"
        )

        update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        logger.info("✅ Bot đã trả lời xong.")
    except Exception as e:
        logger.exception(f"❌ Lỗi không xác định trong handle_message: {e}")

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        dp.process_update(update)
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("❌ Lỗi xử lý webhook: %s", e)
        return jsonify({"ok": False}), 500

@app.route("/", methods=["GET"])
def index():
    return "🤖 Clanker Bot đang hoạt động."

def main():
    bot.delete_webhook(drop_pending_updates=True)

    webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
    bot.set_webhook(url=webhook_url)
    logger.info(f"✅ Webhook đã được thiết lập: {webhook_url}")

    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()