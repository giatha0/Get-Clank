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
API_BASESCAN = os.environ.get("API_BASESCAN")  # Ví dụ: "https://api.basescan.org"
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")        # Ví dụ: "https://get-clank-production.up.railway.app"
WEB3_PROVIDER_URL = os.environ.get("WEB3_PROVIDER_URL")  # Ví dụ: "https://mainnet.infura.io/v3/YOUR_INFURA_PROJECT_ID"

if not all([TELEGRAM_BOT_TOKEN, API_BASESCAN, BASESCAN_API_KEY, WEBHOOK_URL, WEB3_PROVIDER_URL]):
    logger.error("❌ Thiếu biến môi trường. Vui lòng cấu hình đầy đủ các biến: TELEGRAM_BOT_TOKEN, API_BASESCAN, BASESCAN_API_KEY, WEBHOOK_URL, WEB3_PROVIDER_URL")
    exit(1)

# Khởi tạo bot và Dispatcher của Telegram
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot, None, use_context=True)

# Khởi tạo Web3 và load ABI từ file abi.json
w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER_URL))
try:
    with open("abi.json", "r") as f:
        abi = json.load(f)
    contract = w3.eth.contract(abi=abi)
    logger.info("✅ ABI đã được load thành công.")
except Exception as e:
    logger.error(f"❌ Lỗi khi load ABI: {e}")
    exit(1)

# Tạo Flask app
app = Flask(__name__)

def get_creation_txhash(contract_address: str) -> str:
    """
    Lấy txhash của giao dịch tạo contract từ BaseScan.
    Endpoint: ?module=contract&action=getcontractcreation&contractaddresses=<address>&apikey=...
    """
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
        results = data.get("result", [])
        if not results or not isinstance(results, list):
            logger.error(f"❌ Không có kết quả trả về cho contract {contract_address}")
            return None
        txhash = results[0].get("txHash")
        logger.info(f"✅ txhash tìm được: {txhash}")
        return txhash
    except Exception as e:
        logger.error(f"❌ Lỗi khi lấy txhash: {e}")
        return None

def get_transaction_data(txhash: str) -> dict:
    """
    Lấy thông tin giao dịch từ BaseScan theo txhash.
    Endpoint: ?module=proxy&action=eth_getTransactionByHash&txhash=<txhash>&apikey=...
    """
    try:
        logger.info(f"📦 Đang truy vấn thông tin giao dịch cho txhash: {txhash}")
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txhash,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        logger.info("✅ Thông tin giao dịch đã được lấy.")
        return data.get("result", {})
    except Exception as e:
        logger.error(f"❌ Lỗi khi lấy dữ liệu giao dịch: {e}")
        return {}

def decode_input_with_web3(input_hex: str):
    """
    Sử dụng Web3.py để decode input data theo ABI của hàm deployToken.
    Trả về dict với function name và decoded arguments.
    """
    try:
        logger.info("🔓 Bắt đầu decode input data với Web3...")
        func_obj, func_args = contract.decode_function_input(input_hex)
        logger.info(f"✅ Decode thành công. Function: {func_obj.fn_name}")
        return {"function": func_obj.fn_name, "args": func_args}
    except Exception as e:
        logger.error(f"❌ Lỗi khi decode input: {e}")
        return None

def handle_message(update: Update, context: CallbackContext):
    try:
        message_text = update.message.text.strip()
        logger.info(f"📨 Tin nhắn nhận được: {message_text}")

        if not re.match(r"^0x[a-fA-F0-9]{40}$", message_text):
            logger.warning("⚠️ Tin nhắn không phải là địa chỉ contract hợp lệ.")
            return

        update.message.reply_text(f"Đang xử lý contract: `{message_text}`", parse_mode=ParseMode.MARKDOWN)
        txhash = get_creation_txhash(message_text)
        if not txhash:
            update.message.reply_text("❌ Không tìm thấy txhash từ BaseScan.")
            return

        tx_data = get_transaction_data(txhash)
        if not tx_data:
            update.message.reply_text("❌ Không lấy được thông tin giao dịch từ BaseScan.")
            return

        input_data_raw = tx_data.get("input", "")
        if not input_data_raw:
            update.message.reply_text("❌ Không có input data trong giao dịch.")
            return

        logger.info(f"🔍 Input data raw (first 20 chars): {input_data_raw[:20]}... (length: {len(input_data_raw)})")

        decoded = decode_input_with_web3(input_data_raw)
        if not decoded:
            update.message.reply_text("❌ Lỗi khi decode input data.")
            return

        if decoded.get("function") != "deployToken":
            update.message.reply_text(f"⚠️ Giao dịch không phải deployToken (function: {decoded.get('function')}).")
            return

        deployment_config = decoded.get("args", {}).get("deploymentConfig")
        if not deployment_config:
            update.message.reply_text("❌ Không tìm thấy deploymentConfig trong input data.")
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

        try:
            metadata_json = json.loads(metadata)
        except Exception as e:
            logger.warning(f"⚠️ Không parse được metadata JSON: {e}")
            metadata_json = metadata

        try:
            context_json = json.loads(context_raw)
        except Exception as e:
            logger.warning(f"⚠️ Không parse được context JSON: {e}")
            context_json = context_raw

        reply = (
            f"📌 *Thông tin token deploy:*\n\n"
            f"*Tên:* `{name}`\n"
            f"*Ký hiệu:* `{symbol}`\n"
            f"*Chain ID:* `{chain_id}`\n"
            f"*Image:* [Link]({image})\n\n"
            f"*Metadata:*\n```json\n{json.dumps(metadata_json, ensure_ascii=False, indent=2)}\n```\n"
            f"*Context:*\n```json\n{json.dumps(context_json, ensure_ascii=False, indent=2)}\n```\n"
            f"*ID (click copy):* `{context_json.get('id', 'N/A')}`\n\n"
            f"*creatorRewardRecipient:* `{creator_reward_recipient}` (click copy)"
        )

        update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        logger.info("✅ Bot đã trả lời xong.")
    except Exception as e:
        logger.exception(f"❌ Lỗi không xác định trong handle_message: {e}")

def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot đã sẵn sàng. Gửi địa chỉ token contract để xử lý.")

# Thêm handler vào dispatcher (không sử dụng decorator)
dp.add_handler(CommandHandler("start", start_command))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"📨 Nhận update từ Telegram: {data}")
        update_obj = Update.de_json(data, bot)
        dp.process_update(update_obj)
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception(f"❌ Lỗi xử lý webhook: {e}")
        return jsonify({"ok": False}), 500

@app.route("/", methods=["GET"])
def index():
    return "🤖 Clanker Bot đang hoạt động (Flask webhook)."

def main():
    # Xóa webhook cũ
    bot.delete_webhook(drop_pending_updates=True)

    # Thiết lập webhook mới với domain công khai
    hook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
    if not bot.set_webhook(url=hook_url):
        logger.error("❌ Không thể thiết lập webhook với Telegram.")
        exit(1)
    logger.info(f"✅ Webhook đã được thiết lập: {hook_url}")

    port = int(os.environ.get("PORT", 80))
    logger.info(f"🚀 Chạy Flask server trên cổng {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()