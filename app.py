import os
import logging
import tempfile
import asyncio
from pathlib import Path

from flask import Flask, request, jsonify
import telegram
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import Conflict

from PIL import Image
import pytesseract
from fpdf import FPDF
from transliterate import translit

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask додаток
app = Flask(__name__)

# Конфігурація
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://your-app.onrender.com/webhook
PORT = int(os.environ.get("PORT", 5000))

if not BOT_TOKEN:
    logger.error("Змінна середовища BOT_TOKEN не встановлена!")
    raise ValueError("BOT_TOKEN is required")

# Шлях до шрифту (створимо директорію в проєкті)
FONT_PATH = "/app/fonts/DejaVuSans.ttf"
if not os.path.exists(FONT_PATH):
    # Альтернативний шлях для локальної розробки
    FONT_PATH = "./fonts/DejaVuSans.ttf"

# Глобальний об'єкт для Telegram Application
telegram_app = None

def safe_text_for_pdf(text):
    """Безпечно обробляє текст для PDF, намагаючись зберегти кирилицю."""
    try:
        text.encode('latin-1') 
        return text
    except UnicodeEncodeError:
        try:
            return translit(text, 'uk', reversed=True)
        except Exception as e:
            logger.warning(f"Помилка транслітерації: {e}. Використовується ASCII з ігноруванням.")
            return text.encode('ascii', 'ignore').decode('ascii')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /start"""
    await update.message.reply_text("👋 Надішли мені зображення, скан або текст, і я згенерую PDF!")

async def process_image_to_pdf(img_path: str, original_update: Update):
    """Допоміжна функція для обробки зображення та створення PDF."""
    try:
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, lang="ukr+eng")
        
        if not text.strip():
            await original_update.message.reply_text("⚠️ Не вдалося розпізнати текст на зображенні.")
            return None

        pdf = FPDF()
        pdf.add_page()
        
        font_loaded_successfully = False
        if os.path.exists(FONT_PATH):
            try:
                pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
                pdf.set_font("DejaVu", size=12)
                font_loaded_successfully = True
            except Exception as e:
                logger.warning(f"Не вдалося завантажити шрифт DejaVu: {e}")
        
        if not font_loaded_successfully:
            processed_text = safe_text_for_pdf(text)
            pdf.set_font("Arial", size=12)
            pdf.multi_cell(0, 10, processed_text)
        else:
            pdf.multi_cell(0, 10, text)
            
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf_file:
            pdf_output_path = temp_pdf_file.name
            pdf.output(pdf_output_path)
        
        return pdf_output_path

    except Exception as e:
        logger.error(f"Помилка під час обробки зображення для PDF: {e}")
        await original_update.message.reply_text("❌ Сталася помилка під час розпізнавання тексту або створення PDF.")
        return None
    finally:
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
            except Exception as e_remove:
                logger.error(f"Не вдалося видалити тимчасовий файл зображення {img_path}: {e_remove}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник фотографій"""
    if not update.message or not update.message.photo:
        return

    processing_msg = await update.message.reply_text("📷 Обробляю зображення...")
    img_download_path = None
    pdf_path = None
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_img_file:
            img_download_path = temp_img_file.name
            await photo_file.download_to_drive(img_download_path)

        pdf_path = await process_image_to_pdf(img_download_path, update)

        if pdf_path:
            try:
                with open(pdf_path, "rb") as f:
                    await update.message.reply_document(
                        InputFile(f, filename="scan_to_pdf.pdf"),
                        caption="📄 PDF створено з розпізнаного тексту"
                    )
            finally:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
        
        await processing_msg.delete()

    except Exception as e:
        logger.error(f"Помилка обробки фото: {e}")
        if 'processing_msg' in locals() and processing_msg:
            await processing_msg.edit_text("❌ Помилка при обробці зображення. Спробуйте ще раз.")
        else:
            await update.message.reply_text("❌ Помилка при обробці зображення. Спробуйте ще раз.")
    finally:
        if img_download_path and os.path.exists(img_download_path):
            os.remove(img_download_path)
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник документів (зображень, надісланих як файли)"""
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    img_download_path = None
    pdf_path = None

    if doc.mime_type and doc.mime_type.startswith("image/"):
        processing_msg = await update.message.reply_text(f"🖼️ Обробляю надісланий файл ({doc.file_name or 'файл'})...")
        try:
            doc_file = await doc.get_file()
            
            file_extension = os.path.splitext(doc.file_name)[1] if doc.file_name else '.jpg'
            if not file_extension.startswith('.'):
                file_extension = '.' + (file_extension if file_extension else 'dat')

            with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as temp_doc_file:
                img_download_path = temp_doc_file.name
                await doc_file.download_to_drive(img_download_path)

            pdf_path = await process_image_to_pdf(img_download_path, update)

            if pdf_path:
                try:
                    with open(pdf_path, "rb") as f:
                        output_filename = "ocr_document.pdf"
                        if doc.file_name:
                            base_name = os.path.splitext(doc.file_name)[0]
                            output_filename = f"{base_name}_ocr.pdf"
                        
                        await update.message.reply_document(
                            InputFile(f, filename=output_filename),
                            caption="📄 PDF створено з розпізнаного тексту документа"
                        )
                finally:
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
            
            await processing_msg.delete()

        except Exception as e:
            logger.error(f"Помилка обробки документа: {e}")
            if 'processing_msg' in locals() and processing_msg:
                await processing_msg.edit_text("❌ Помилка при обробці файлу. Переконайтесь, що це зображення.")
            else:
                await update.message.reply_text("❌ Помилка при обробці файлу. Переконайтесь, що це зображення.")
        finally:
            if img_download_path and os.path.exists(img_download_path):
                os.remove(img_download_path)
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
    else:
        await update.message.reply_text("⚠️ Будь ласка, надішліть зображення (як фото або файл) для перетворення в PDF.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник текстових повідомлень для створення PDF"""
    if not update.message or not update.message.text:
        return
        
    text_content = update.message.text.strip()
    
    if not text_content:
        await update.message.reply_text("❌ Текст порожній. Надішліть текст для створення PDF.")
        return
        
    processing_msg = await update.message.reply_text("📝 Створюю PDF з тексту...")
    pdf_output_path = None
    
    try:
        pdf = FPDF()
        pdf.add_page()
        
        font_loaded_successfully = False
        if os.path.exists(FONT_PATH):
            try:
                pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
                pdf.set_font("DejaVu", size=12)
                font_loaded_successfully = True
            except Exception as e:
                logger.warning(f"Не вдалося завантажити шрифт DejaVu для текстового PDF: {e}")

        if not font_loaded_successfully:
            processed_text = safe_text_for_pdf(text_content)
            pdf.set_font("Arial", size=12)
            pdf.multi_cell(0, 10, processed_text)
        else:
            pdf.multi_cell(0, 10, text_content)
            
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf_file:
            pdf_output_path = temp_pdf_file.name
            pdf.output(pdf_output_path)
            
        try:
            with open(pdf_output_path, "rb") as f:
                await update.message.reply_document(
                    InputFile(f, filename="text_to_pdf.pdf"),
                    caption="📄 PDF створено з вашого тексту"
                )
        finally:
            if os.path.exists(pdf_output_path):
                os.remove(pdf_output_path)
                
        await processing_msg.delete()
        
    except Exception as e:
        logger.error(f"Помилка створення PDF з тексту: {e}")
        if 'processing_msg' in locals() and processing_msg:
            await processing_msg.edit_text("❌ Помилка при створенні PDF з тексту. Спробуйте ще раз.")
        else:
            await update.message.reply_text("❌ Помилка при створенні PDF зтексту. Спробуйте ще раз.")
    finally:
        if pdf_output_path and os.path.exists(pdf_output_path):
            os.remove(pdf_output_path)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логує помилки, спричинені Update."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def setup_telegram_app():
    """Налаштовує Telegram додаток"""
    global telegram_app
    
    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connection_pool_size(10)
        .pool_timeout(30)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    telegram_app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    telegram_app.add_error_handler(error_handler)

# Flask routes
@app.route('/')
def health_check():
    """Health check endpoint для Render"""
    return jsonify({
        "status": "healthy",
        "service": "telegram-pdf-bot",
        "version": "1.0.0"
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Вебхук для отримання повідомлень від Telegram"""
    try:
        # Отримуємо JSON дані від Telegram
        json_data = request.get_json()
        
        if not json_data:
            logger.warning("Отримано порожні дані у вебхуку")
            return jsonify({"status": "error", "message": "No data received"}), 400
        
        # Створюємо Update об'єкт
        update = Update.de_json(json_data, telegram_app.bot)
        
        # Обробляємо update асинхронно
        asyncio.create_task(telegram_app.process_update(update))
        
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Помилка в обробці вебхука: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/set_webhook', methods=['POST'])
def set_webhook():
    """Встановлює вебхук (для ручного налаштування)"""
    try:
        if not WEBHOOK_URL:
            return jsonify({"status": "error", "message": "WEBHOOK_URL not configured"}), 400
            
        webhook_url = f"{WEBHOOK_URL}/webhook"
        
        # Використовуємо синхронний bot для встановлення вебхука
        bot = telegram.Bot(token=BOT_TOKEN)
        result = asyncio.run(bot.set_webhook(webhook_url))
        
        if result:
            logger.info(f"Вебхук встановлено: {webhook_url}")
            return jsonify({"status": "success", "webhook_url": webhook_url})
        else:
            return jsonify({"status": "error", "message": "Failed to set webhook"}), 500
            
    except Exception as e:
        logger.error(f"Помилка встановлення вебхука: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/delete_webhook', methods=['POST'])
def delete_webhook():
    """Видаляє вебхук"""
    try:
        bot = telegram.Bot(token=BOT_TOKEN)
        result = asyncio.run(bot.delete_webhook())
        
        if result:
            logger.info("Вебхук видалено")
            return jsonify({"status": "success", "message": "Webhook deleted"})
        else:
            return jsonify({"status": "error", "message": "Failed to delete webhook"}), 500
            
    except Exception as e:
        logger.error(f"Помилка видалення вебхука: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def initialize_app():
    """Ініціалізує додаток"""
    try:
        # Налаштовуємо Telegram додаток
        setup_telegram_app()
        
        # Автоматично встановлюємо вебхук, якщо WEBHOOK_URL налаштований
        if WEBHOOK_URL:
            try:
                webhook_url = f"{WEBHOOK_URL}/webhook"
                bot = telegram.Bot(token=BOT_TOKEN)
                asyncio.run(bot.set_webhook(webhook_url))
                logger.info(f"Вебхук автоматично встановлено: {webhook_url}")
            except Exception as e:
                logger.warning(f"Не вдалося автоматично встановити вебхук: {e}")
        
        logger.info("Додаток успішно ініціалізовано")
        
    except Exception as e:
        logger.error(f"Помилка ініціалізації додатку: {e}")
        raise

if __name__ == '__main__':
    initialize_app()
    
    # Запускаємо Flask додаток
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False  # У продакшені завжди False
    )
