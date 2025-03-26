import os
import re
import json
import logging
import requests

from flask import Flask, request, jsonify
from telegram import Bot, Update, ParseMode
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext
from web3 import Web3

# Thiết lập logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Lấy biến môi trường
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
API_BASESCAN = os.environ.get("API_BASESCAN")            # ví dụ: "https://api.basescan.org"
BASESCAN_API_KEY = os.environ.get("BASESCAN_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")              # ví dụ: "https://get-clank-production.up.railway.app"
WEB3_PROVIDER_URL = os.environ.get("WEB3_PROVIDER_URL")  # ví dụ: "https://mainnet.infura.io/v3/YOUR_INFURA_PROJECT_ID"

if not (TELEGRAM_BOT_TOKEN and API_BASESCAN and BASESCAN_API_KEY and WEBHOOK_URL and WEB3_PROVIDER_URL):
    logger.error("Chưa thiết lập đầy đủ các biến môi trường cần thiết.")
    exit(1)

# Khởi tạo bot và Dispatcher của Telegram
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot, None, use_context=True)

# Khởi tạo Web3 và load ABI từ file abi.json
w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER_URL))
try:
    with open("abi.json", "r") as f:
        abi = json.load(f)
except Exception as e:
    logger.error("Không thể load ABI từ abi.json: %s", e)
    exit(1)

# Tạo đối tượng contract (không cần địa chỉ cụ thể để decode function input)
contract = w3.eth.contract(abi=abi)

# Tạo Flask app
app = Flask(__name__)

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
        return {}

def decode_input_with_web3(input_hex: str) -> dict:
    """
    Sử dụng Web3 để decode input data theo ABI của hàm deployToken.
    Trả về tuple (function_name, decoded_args) nếu thành công, None nếu thất bại.
    """
    try:
        # Hàm decode_function_input nhận chuỗi hex (bao gồm "0x" ở đầu)
        func_obj, func_args = contract.decode_function_input(input_hex)
        return {"function": func_obj.fn_name, "args": func_args}
    except Exception as e:
        logger.error("Lỗi khi decode input với Web3: %s", e)
        return None

def handle_message(update: Update, context: CallbackContext) -> None:
    """
    Xử lý tin nhắn nhận được từ Telegram.
    Nếu tin nhắn là contract address hợp lệ, tiến hành lấy txhash, thông tin giao dịch,
    và decode input data bằng Web3 để tách bạch các tham số.
    Sau đó gửi phản hồi về cho người dùng.
    """
    message_text = update.message.text.strip()
    if not re.match(r'^0x[a-fA-F0-9]{40}$', message_text):
        return  # Bỏ qua nếu không phải contract address hợp lệ

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
    if not input_data_raw:
        update.message.reply_text("Không tìm thấy input data trong giao dịch.")
        return

    logger.info(f"Input data raw: {input_data_raw}")
    decoded = decode_input_with_web3(input_data_raw)
    if not decoded:
        update.message.reply_text("Lỗi khi decode input data.")
        return

    # Giả sử hàm deployToken có tên "deployToken" và nhận 1 tham số là deploymentConfig (tuple)
    if decoded.get("function") != "deployToken":
        update.message.reply_text("Không phải giao dịch deployToken.")
        return

    args = decoded.get("args", {})
    # Theo ABI, tham số duy nhất có tên "deploymentConfig"
    deployment_config = args.get("deploymentConfig")
    if not deployment_config:
        update.message.reply_text("Không tìm thấy deploymentConfig trong input data.")
        return

    # Tách các thành phần theo ABI:
    token_config = deployment_config.get("tokenConfig")
    vault_config = deployment_config.get("vaultConfig")
    pool_config = deployment_config.get("poolConfig")
    initial_buy_config = deployment_config.get("initialBuyConfig")
    rewards_config = deployment_config.get("rewardsConfig")

    # Lấy các trường cần thiết từ tokenConfig
    name = token_config.get("name")
    symbol = token_config.get("symbol")
    image = token_config.get("image")
    metadata_str = token_config.get("metadata")
    context_str = token_config.get("context")
    originating_chain_id = token_config.get("originatingChainId")

    # Chuyển metadata và context sang dict nếu có thể
    try:
        metadata = json.loads(metadata_str)
    except Exception as e:
        metadata = metadata_str
    try:
        context_data = json.loads(context_str)
    except Exception as e:
        context_data = context_str

    # Lấy giá trị creatorRewardRecipient từ rewardsConfig
    creator_reward_recipient = rewards_config.get("creatorRewardRecipient")

    # Xây dựng phản hồi
    reply_text = (
        f"*Thông tin triển khai hợp đồng:*\n\n"
        f"*Token Name:* {name}\n"
        f"*Symbol:* {symbol}\n"
        f"*Image:* [Link]({image})\n"
        f"*Originating Chain ID:* {originating_chain_id}\n\n"
        f"*Metadata:*\n```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n"
        f"*Context:*\n```json\n{json.dumps(context_data, ensure_ascii=False, indent=2)}\n```\n"
        f"_ID riêng (context id): {context_data.get('id', 'N/A')}_\n\n"
        f"*Rewards Config - Creator Reward Recipient:* `{creator_reward_recipient}` (click copy)"
    )
    update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN)

def start_command(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Bot đã sẵn sàng. Gửi địa chỉ token contract để xử lý.")

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """
    Endpoint để Telegram gửi update (webhook).
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False}), 400

    update_obj = Update.de_json(data, bot)
    dp.process_update(update_obj)
    return jsonify({"ok": True}), 200

@app.route("/", methods=["GET"])
def index():
    return "Bot đang chạy (Flask webhook)."

def main():
    # Xóa webhook cũ
    bot.delete_webhook(drop_pending_updates=True)

    # Thiết lập webhook với Telegram (domain công khai + token)
    hook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
    if not bot.set_webhook(url=hook_url):
        logger.error("Không thể thiết lập webhook với Telegram.")
        exit(1)
    logger.info("Webhook đã được thiết lập: %s", hook_url)

    # Chạy Flask server. Railway thường map domain đến cổng được chỉ định qua biến PORT.
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()