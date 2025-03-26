import os
import re
import json
import logging
import requests
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Lấy biến môi trường
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")  # Ví dụ: https://api.basescan.org
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")

def get_creation_txhash(contract_address: str) -> str:
    """
    Gọi API của BaseScan để lấy giao dịch tạo contract.
    Sử dụng endpoint: module=contract, action=getcontractcreation, contractaddresses=<address>
    Ví dụ:
    https://api.basescan.org/api?module=contract&action=getcontractcreation&contractaddresses=<address>&apikey=...
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
        # Giả sử kết quả trả về có cấu trúc:
        # { "status": "1", "result": [ { "txHash": "..." } ] }
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
    Gọi API của BaseScan để lấy thông tin giao dịch theo txhash,
    sử dụng endpoint eth_getTransactionByHash.
    Ví dụ:
    https://api.basescan.org/api?module=proxy&action=eth_getTransactionByHash&txhash=<txhash>&apikey=...
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

def handle_message(update: Update, context: CallbackContext) -> None:
    message_text = update.message.text.strip()
    # Kiểm tra định dạng địa chỉ hợp đồng (0x + 40 ký tự hex)
    if not re.match(r'^0x[a-fA-F0-9]{40}$', message_text):
        return

    contract_address = message_text
    update.message.reply_text(f"Đang xử lý contract: `{contract_address}`", parse_mode=ParseMode.MARKDOWN)
    
    # Lấy txhash của giao dịch tạo token từ contract address
    txhash = get_creation_txhash(contract_address)
    if not txhash:
        update.message.reply_text("Không tìm thấy txhash từ BaseScan.")
        return

    # Lấy thông tin giao dịch (bao gồm input data) từ txhash
    tx_data = get_transaction_data(txhash)
    if not tx_data:
        update.message.reply_text("Không lấy được thông tin giao dịch từ BaseScan.")
        return

    # Lấy input data từ giao dịch (thường là chuỗi hex, giả sử đã decode thành JSON)
    input_hex = tx_data.get("input", "")
    if not input_hex:
        update.message.reply_text("Không tìm thấy input data trong giao dịch.")
        return

    try:
        # Nếu input data đã được decode thành chuỗi JSON
        input_data = json.loads(input_hex)
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
    update.message.reply_text("Bot đã sẵn sàng. Gửi địa chỉ token contract để xử lý.")

def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Chưa thiết lập TELEGRAM_BOT_TOKEN trong biến môi trường.")
        return
    if not API_BASESCAN:
        logger.error("Chưa thiết lập API_BASESCAN trong biến môi trường.")
        return
    if not BASESCAN_API_KEY:
        logger.error("Chưa thiết lập BASESCAN_API_KEY trong biến môi trường.")
        return

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    logger.info("Bot đang lắng nghe tin nhắn...")
    updater.idle()

if __name__ == '__main__':
    main()