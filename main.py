import os
import re
import json
import logging
import requests

from flask import Flask, request, jsonify
from telegram import Bot, Update, ParseMode
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Lấy biến môi trường
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # Domain công khai của bạn, ví dụ: https://get-clank-production.up.railway.app

# Tạo bot và dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot, None, use_context=True)

# Tạo Flask app
app = Flask(__name__)

def get_creation_txhash(contract_address: str) -> str:
    try:
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract_address,
            "apikey": BASESCAN_API_KEY
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("result", [])
        if not results or not isinstance(results, list):
            return None
        txhash = results[0].get("txHash")
        return txhash
    except Exception as e:
        logger.error("Lỗi khi lấy txhash: %s", e)
        return None

def get_transaction_data(txhash: str) -> dict:
    try:
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txhash,
            "apikey": BASESCAN_API_KEY
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {})
    except Exception as e:
        logger.error("Lỗi khi lấy thông tin giao dịch: %s", e)
        return {}

def decode_input(hex_str: str) -> str:
    try:
        if hex_str.startswith("0x"):
            hex_str = hex_str[2:]
        bytes_data = bytes.fromhex(hex_str)
        return bytes_data.decode('utf-8', errors='replace').strip()
    except Exception as e:
        logger.error("Lỗi khi giải mã input data: %s", e)
        return ""

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Bot đã sẵn sàng. Gửi địa chỉ token contract để xử lý.")

def handle_message(update: Update, context: CallbackContext) -> None:
    message_text = update.message.text.strip()
    if not re.match(r'^0x[a-fA-F0-9]{40}$', message_text):
        return

    contract_address = message_text
    update.message.reply_text(f"Đang xử lý contract: `{contract_address}`", parse_mode=ParseMode.MARKDOWN)

    txhash = get_creation_txhash(contract_address)
    if not txhash:
        update.message.reply_text("Không tìm thấy txhash từ BaseScan.")
        return

    tx_data = get_transaction_data(txhash)
    if not tx_data:
        update.message.reply_text("Không lấy được thông tin giao dịch từ BaseScan.")
        return

    input_data_raw = tx_data.get("input", "")
    logger.info(f"input_data_raw: {input_data_raw}")
    if not input_data_raw:
        update.message.reply_text("Không tìm thấy input data trong giao dịch.")
        return

    # Nếu chuỗi bắt đầu bằng { thì coi như JSON, nếu không thì decode
    if input_data_raw.strip().startswith("{"):
        input_str = input_data_raw.strip()
        logger.info(f"Input đã ở dạng JSON: {input_str}")
    else:
        input_str = decode_input(input_data_raw)
        logger.info(f"Input sau decode: {input_str}")
    if not input_str:
        update.message.reply_text("Không thể giải mã input data từ giao dịch.")
        return

    try:
        input_data = json.loads(input_str)
    except Exception as e:
        logger.error("Lỗi khi parse input data: %s", e)
        update.message.reply_text("Lỗi khi parse input data từ giao dịch.")
        return

    try:
        params = input_data.get("params", [])
        if not params or not isinstance(params[0], list):
            update.message.reply_text("Dữ liệu input không đúng định dạng.")
            return

        main_tuple = params[0]
        token_config = main_tuple[0]
        metadata_url = token_config[3]
        metadata_json = token_config[4]
        context_json = token_config[5]
        metadata = json.loads(metadata_json)
        context_data = json.loads(context_json)
        context_id = context_data.get("id", "N/A")
        rewards_config = main_tuple[4]
        creator_reward_recipient = rewards_config[1]

        reply_text = (
            f"*Thông tin triển khai hợp đồng:*\n\n"
            f"*Metadata URL:* [Link]({metadata_url})\n"
            f"*Metadata:*\n```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n"
            f"*Context:*\n```json\n{json.dumps(context_data, ensure_ascii=False, indent=2)}\n```\n"
            f"_ID riêng: `{context_id}` (click copy)_\n\n"
            f"*Rewards Config - Creator Reward Recipient:* `{creator_reward_recipient}` (click copy)"
        )
        update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("Lỗi khi xử lý input data: %s", e)
        update.message.reply_text("Đã xảy ra lỗi khi xử lý dữ liệu từ input.")

# Thêm handler vào dispatcher
dp.add_handler(CommandHandler("start", start))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """
    Endpoint cho Telegram gửi update (webhook).
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False}), 400

    update = Update.de_json(data, bot)
    dp.process_update(update)
    return jsonify({"ok": True}), 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running (Flask webhook)."

def main():
    if not TELEGRAM_BOT_TOKEN or not API_BASESCAN or not BASESCAN_API_KEY or not WEBHOOK_URL:
        logger.error("Chưa thiết lập đầy đủ biến môi trường.")
        return

    # 1. Xoá webhook cũ
    bot.delete_webhook(drop_pending_updates=True)

    # 2. Thiết lập webhook với domain công khai
    set_hook = bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}")
    if not set_hook:
        logger.error("Không thể thiết lập webhook với Telegram.")
        return
    logger.info("Webhook đã được thiết lập: %s/%s", WEBHOOK_URL, TELEGRAM_BOT_TOKEN)

    # 3. Chạy Flask trên cổng 80 (hoặc 443), Railway sẽ map domain -> cổng
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()