import os
import re
import json
import logging
import requests
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Cấu hình logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Lấy biến môi trường
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")  # Ví dụ: https://api.basescan.io
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")

def get_creation_txhash(contract_address: str) -> str:
    """
    Gọi API của BaseScan để lấy txhash của giao dịch tạo token, truyền thêm apikey.
    """
    try:
        url = f"{API_BASESCAN}/api/txhash"
        params = {
            "contract": contract_address,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        txhash = data.get("txhash")
        if not txhash:
            logger.error("Không tìm thấy txhash cho contract: %s", contract_address)
        return txhash
    except Exception as e:
        logger.error("Lỗi khi lấy txhash: %s", e)
        return None

def get_input_data(txhash: str) -> dict:
    """
    Gọi API của BaseScan để lấy input data từ txhash, truyền thêm apikey.
    """
    try:
        url = f"{API_BASESCAN}/api/input"
        params = {
            "txhash": txhash,
            "apikey": BASESCAN_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data  # Giả sử data trả về là JSON với cấu trúc tokenconfig
    except Exception as e:
        logger.error("Lỗi khi lấy input data: %s", e)
        return None

def handle_message(update: Update, context: CallbackContext) -> None:
    message_text = update.message.text.strip()
    # Kiểm tra định dạng địa chỉ hợp đồng (40 ký tự hex sau 0x)
    if not re.match(r'^0x[a-fA-F0-9]{40}$', message_text):
        return

    contract_address = message_text
    update.message.reply_text(f"Đang xử lý contract: `{contract_address}`", parse_mode=ParseMode.MARKDOWN)
    
    # Lấy txhash của giao dịch tạo token
    txhash = get_creation_txhash(contract_address)
    if not txhash:
        update.message.reply_text("Không tìm thấy txhash từ BaseScan.")
        return

    # Lấy input data từ txhash
    input_data = get_input_data(txhash)
    if not input_data:
        update.message.reply_text("Không lấy được input data từ BaseScan.")
        return

    try:
        # Giả sử input_data có cấu trúc giống như tokenconfig được mô tả
        params = input_data.get("params", [])
        if not params or not isinstance(params[0], list):
            update.message.reply_text("Dữ liệu input không đúng định dạng.")
            return

        main_tuple = params[0]
        token_config = main_tuple[0]
        # Lấy metadata và context
        metadata_url = token_config[3]
        metadata_json = token_config[4]
        context_json = token_config[5]
        metadata = json.loads(metadata_json)
        context_data = json.loads(context_json)
        context_id = context_data.get("id", "N/A")
        # Lấy creatorRewardRecipient từ rewardsConfig (tuple thứ 5)
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