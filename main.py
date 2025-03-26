import os
import re
import json
import logging
import requests
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Thiết lập logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Lấy biến môi trường
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")  # Ví dụ: "https://api.basescan.org"
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # Ví dụ: "https://get-clank-production.up.railway.app"

if not TELEGRAM_BOT_TOKEN or not API_BASESCAN or not BASESCAN_API_KEY or not WEBHOOK_URL:
    logger.error("Chưa thiết lập đầy đủ biến môi trường cần thiết.")
    exit(1)

def get_creation_txhash(contract_address: str) -> str:
    """
    Gọi API của BaseScan để lấy giao dịch tạo contract.
    Endpoint: ?module=contract&action=getcontractcreation&contractaddresses=<address>&apikey=...
    """
    try:
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract_address,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = data.get("result", [])
        if not results or not isinstance(results, list):
            logger.error("Không có kết quả trả về cho contract: %s", contract_address)
            return None
        txhash = results[0].get("txHash")
        if not txhash:
            logger.error("Không tìm thấy txHash trong kết quả trả về cho contract: %s", contract_address)
        return txhash
    except Exception as e:
        logger.error("Lỗi khi lấy txhash: %s", e)
        return None

def get_transaction_data(txhash: str) -> dict:
    """
    Gọi API của BaseScan để lấy thông tin giao dịch theo txhash.
    Endpoint: ?module=proxy&action=eth_getTransactionByHash&txhash=<txhash>&apikey=...
    """
    try:
        url = f"{API_BASESCAN}/api"
        params = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txhash,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("result", {})
    except Exception as e:
        logger.error("Lỗi khi lấy thông tin giao dịch: %s", e)
        return None

def decode_input(hex_str: str) -> str:
    """
    Giải mã dữ liệu input dạng hex thành chuỗi UTF-8.
    Dùng errors='replace' để tránh lỗi decode khi gặp byte không hợp lệ.
    """
    try:
        if hex_str.startswith("0x"):
            hex_str = hex_str[2:]
        bytes_data = bytes.fromhex(hex_str)
        return bytes_data.decode('utf-8', errors='replace').strip()
    except Exception as e:
        logger.error("Lỗi khi giải mã input data: %s", e)
        return None

def handle_message(update: Update, context: CallbackContext) -> None:
    """
    Xử lý tin nhắn nhận được từ Telegram:
    - Nhận contract address, 
    - Lấy txhash từ BaseScan,
    - Lấy thông tin giao dịch và input data,
    - Giải mã và parse JSON input data,
    - Trích xuất metadata, context và creatorRewardRecipient.
    """
    message_text = update.message.text.strip()
    if not re.match(r'^0x[a-fA-F0-9]{40}$', message_text):
        return  # Nếu không phải contract address hợp lệ, bỏ qua.

    contract_address = message_text
    update.message.reply_text(f"Đang xử lý contract: `{contract_address}`", parse_mode=ParseMode.MARKDOWN)
    
    # Lấy txhash của giao dịch tạo contract
    txhash = get_creation_txhash(contract_address)
    if not txhash:
        update.message.reply_text("Không tìm thấy txhash từ BaseScan.")
        return

    # Lấy thông tin giao dịch từ txhash
    tx_data = get_transaction_data(txhash)
    if not tx_data:
        update.message.reply_text("Không lấy được thông tin giao dịch từ BaseScan.")
        return

    # Lấy input data từ giao dịch
    input_data_raw = tx_data.get("input", "")
    if not input_data_raw:
        update.message.reply_text("Không tìm thấy input data trong giao dịch.")
        return

    try:
        # Nếu input data bắt đầu bằng '{', coi như đã là JSON, ngược lại giải mã từ hex.
        if input_data_raw.strip().startswith("{"):
            input_str = input_data_raw.strip()
        else:
            input_str = decode_input(input_data_raw)
            if not input_str:
                update.message.reply_text("Không thể giải mã input data từ giao dịch.")
                return
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

def start(update: Update, context: CallbackContext) -> None:
    """Xử lý lệnh /start"""
    update.message.reply_text("Bot đã sẵn sàng. Gửi địa chỉ token contract để xử lý.")

def main() -> None:
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Đăng ký các handler
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Xóa webhook nếu còn tồn tại (để tránh xung đột)
    updater.bot.delete_webhook(drop_pending_updates=True)

    # Cấu hình webhook
    port = 8080  # Port được Railway cung cấp
    # Lắng nghe trên tất cả các interface (0.0.0.0) trên port 8080
    updater.start_webhook(listen="0.0.0.0",
                          port=port,
                          url_path=TELEGRAM_BOT_TOKEN)
    # Thiết lập webhook với Telegram: ví dụ "https://get-clank-production.up.railway.app/<bot_token>"
    webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
    updater.bot.set_webhook(url=webhook_url)
    logger.info("Webhook đã được thiết lập tại: %s", webhook_url)

    updater.idle()

if __name__ == '__main__':
    main()