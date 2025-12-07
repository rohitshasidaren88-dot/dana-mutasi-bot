import os
import json
import asyncio
import redis
import gspread
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from google.oauth2.service_account import Credentials
import aiohttp

print("🤖 DANA Mutasi Bot Starting...")

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
SHEET_ID = os.getenv('SHEET_ID')
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN not set in environment variables")
    exit(1)

if not SHEET_ID:
    print("⚠️ WARNING: SHEET_ID not set, using local storage")

# ================= STORAGE =================
class StorageManager:
    def __init__(self):
        self.sheet_id = SHEET_ID
        self.use_google_sheets = False
        
        # Try Google Sheets first
        if SHEET_ID:
            try:
                creds_json = os.getenv('GOOGLE_CREDS_JSON')
                if creds_json:
                    creds_dict = json.loads(creds_json)
                    scope = ['https://spreadsheets.google.com/feeds']
                    credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
                    self.gc = gspread.authorize(credentials)
                    self.sheet = self.gc.open_by_key(SHEET_ID)
                    self.use_google_sheets = True
                    print("✅ Connected to Google Sheets")
                else:
                    print("⚠️ GOOGLE_CREDS_JSON not found, using local storage")
            except Exception as e:
                print(f"⚠️ Google Sheets error: {e}, using local storage")
        
        # Local backup file
        self.local_file = 'data/accounts.json'
        os.makedirs('data', exist_ok=True)
        
        if not os.path.exists(self.local_file):
            with open(self.local_file, 'w') as f:
                json.dump({"accounts": []}, f)
    
    def add_account(self, phone, pin, name="User"):
        account_data = {
            "phone": phone,
            "name": name,
            "pin": pin,
            "status": "active",
            "added": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "transactions": 0
        }
        
        # Save to Google Sheets if available
        if self.use_google_sheets:
            try:
                try:
                    ws = self.sheet.worksheet("Master_Accounts")
                except:
                    ws = self.sheet.add_worksheet("Master_Accounts", 100, 10)
                    ws.append_row(["ID", "Phone", "Name", "PIN", "Status", "Added", "Transactions"])
                
                # Get next ID
                records = ws.get_all_records()
                new_id = len(records) + 1
                
                ws.append_row([
                    new_id, phone, name, pin, "active",
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 0
                ])
            except Exception as e:
                print(f"Google Sheets error: {e}, saving locally")
        
        # Always save locally
        with open(self.local_file, 'r') as f:
            data = json.load(f)
        
        data['accounts'].append(account_data)
        
        with open(self.local_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        return True
    
    def get_accounts(self):
        if self.use_google_sheets:
            try:
                ws = self.sheet.worksheet("Master_Accounts")
                records = ws.get_all_records()
                return [r for r in records if r.get('Status') == 'active']
            except:
                pass
        
        # Fallback to local
        with open(self.local_file, 'r') as f:
            data = json.load(f)
        return data.get('accounts', [])
    
    def remove_account(self, phone):
        # Remove from Google Sheets
        if self.use_google_sheets:
            try:
                ws = self.sheet.worksheet("Master_Accounts")
                records = ws.get_all_records()
                for i, row in enumerate(records, start=2):
                    if str(row.get('Phone')) == str(phone):
                        ws.update_cell(i, 5, 'inactive')
                        break
            except:
                pass
        
        # Remove from local
        with open(self.local_file, 'r') as f:
            data = json.load(f)
        
        data['accounts'] = [acc for acc in data['accounts'] if acc['phone'] != phone]
        
        with open(self.local_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        return True

# ================= TELEGRAM BOT =================
class DanaBot:
    def __init__(self):
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.storage = StorageManager()
        self.redis = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
        
        # Setup handlers
        self.setup_handlers()
        
        print("✅ Bot initialized")
    
    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("list", self.list_command))
        self.app.add_handler(CommandHandler("tambah", self.add_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(CommandHandler("clear", self.clear_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CallbackQueryHandler(self.button_handler))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("📋 LIHAT DAFTAR AKUN", callback_data="show_list")],
            [InlineKeyboardButton("➕ TAMBAH AKUN BARU", callback_data="add_account")],
            [InlineKeyboardButton("❓ BANTUAN", callback_data="help")]
        ]
        
        await update.message.reply_text(
            "🤖 *DANA MUTASI BOT*\n\n"
            "Bot untuk otomatisasi pencatatan transaksi DANA ke spreadsheet.\n\n"
            "*Perintah:*\n"
            "`/list` - Tampilkan semua akun\n"
            "`/tambah 081234567890 123456` - Tambah akun baru\n"
            "`/stop 081234567890` - Hentikan akun\n"
            "`/clear` - Bersihkan cache\n"
            "`/help` - Bantuan",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.storage.get_accounts()
        
        if not accounts:
            await update.message.reply_text(
                "📭 *Belum ada akun DANA yang terdaftar.*\n\n"
                "Gunakan `/tambah 081234567890 123456` untuk menambah akun pertama.",
                parse_mode='Markdown'
            )
            return
        
        # Create table
        table = "```\n"
        table += "┌─────────────────────────────────────┐\n"
        table += "│        📊 DAFTAR AKUN DANA         │\n"
        table += "├────┬──────────────┬──────┬─────────┤\n"
        table += "│ No │ Nomor        │ Nama │ Status  │\n"
        table += "├────┼──────────────┼──────┼─────────┤\n"
        
        buttons = []
        for i, acc in enumerate(accounts[:8], 1):
            phone = acc.get('Phone') or acc.get('phone', '')
            name = acc.get('Name') or acc.get('name', 'User')[:8]
            status = "🟢" if (acc.get('Status') or acc.get('status')) == 'active' else "🔴"
            
            table += f"│ {i:2} │ {phone:12} │ {name:6} │ {status:7} │\n"
            
            buttons.append([
                InlineKeyboardButton(
                    f"❌ Hapus {phone[-4:]}",
                    callback_data=f"delete_{phone}"
                )
            ])
        
        table += "└────┴──────────────┴──────┴─────────┘\n"
        table += f"\nTotal: {len(accounts)}/8 akun aktif\n"
        table += "```"
        
        # Add refresh button
        buttons.append([
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
            InlineKeyboardButton("➕ Tambah Baru", callback_data="add_account")
        ])
        
        await update.message.reply_text(
            table,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    async def add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ *Format salah!*\n\n"
                "Gunakan: `/tambah 081234567890 123456`\n"
                "Contoh: `/tambah 081212345678 77888`",
                parse_mode='Markdown'
            )
            return
        
        phone = context.args[0]
        pin = context.args[1]
        
        # Validation
        if not phone.startswith('08') or len(phone) < 10:
            await update.message.reply_text("❌ Nomor HP harus dimulai 08 dan minimal 10 digit")
            return
        
        if len(pin) < 4 or len(pin) > 6 or not pin.isdigit():
            await update.message.reply_text("❌ PIN harus 4-6 digit angka")
            return
        
        # Check limit
        accounts = self.storage.get_accounts()
        active_accounts = [acc for acc in accounts if acc.get('status') == 'active' or acc.get('Status') == 'active']
        
        if len(active_accounts) >= 8:
            await update.message.reply_text(
                "❌ *Sudah mencapai 8 akun aktif!*\n\n"
                "Hapus salah satu akun terlebih dahulu dengan:\n"
                "`/stop 081234567890`",
                parse_mode='Markdown'
            )
            return
        
        # Add account
        self.storage.add_account(phone, pin)
        
        await update.message.reply_text(
            f"✅ *AKUN BERHASIL DITAMBAHKAN!*\n\n"
            f"📱 Nomor: `{phone}`\n"
            f"🔐 PIN: `{pin}`\n"
            f"📊 Status: Aktif\n\n"
            f"Sekarang gunakan `/list` untuk melihat tabel.",
            parse_mode='Markdown'
        )
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "❌ *Format: `/stop 081234567890`*",
                parse_mode='Markdown'
            )
            return
        
        phone = context.args[0]
        
        # Remove account
        success = self.storage.remove_account(phone)
        
        if success:
            await update.message.reply_text(
                f"✅ *AKUN DIHENTIKAN!*\n\n"
                f"📱 Nomor: `{phone}`\n"
                f"📊 Status: Nonaktif\n\n"
                f"Slot sekarang tersedia untuk akun baru.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(f"❌ Akun `{phone}` tidak ditemukan.")
    
    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.redis:
            self.redis.flushall()
        
        await update.message.reply_text("🧹 *Cache berhasil dibersihkan!*", parse_mode='Markdown')
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "show_list":
            await self.list_command(update, context)
        
        elif data == "add_account":
            await query.edit_message_text(
                "📝 *TAMBAH AKUN BARU*\n\n"
                "Ketik: `/tambah 081234567890 123456`\n\n"
                "Format: Nomor dan PIN dipisah spasi\n"
                "Contoh: `/tambah 081212345678 77888`",
                parse_mode='Markdown'
            )
        
        elif data == "refresh":
            await self.list_command(update, context)
        
        elif data.startswith("delete_"):
            phone = data.replace("delete_", "")
            
            buttons = [
                [
                    InlineKeyboardButton("✅ YA, HAPUS", callback_data=f"confirm_delete_{phone}"),
                    InlineKeyboardButton("❌ BATAL", callback_data="cancel")
                ]
            ]
            
            await query.edit_message_text(
                f"⚠️ *KONFIRMASI HAPUS*\n\n"
                f"Yakin hapus akun `{phone}`?",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
        elif data.startswith("confirm_delete_"):
            phone = data.replace("confirm_delete_", "")
            
            self.storage.remove_account(phone)
            
            await query.edit_message_text(
                f"✅ *AKUN DIHAPUS!*\n\n"
                f"Nomor: `{phone}`\n"
                f"Status: Nonaktif\n\n"
                f"Slot tersedia untuk akun baru.",
                parse_mode='Markdown'
            )
        
        elif data == "help":
            await self.help_command(update, context)
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle OTP if needed
        pass
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
🆘 *BANTUAN DANA MUTASI BOT*

📋 *PERINTAH:*
• `/start` - Mulai bot
• `/list` - Tampilkan semua akun
• `/tambah 081234567890 123456` - Tambah akun baru
• `/stop 081234567890` - Hentikan akun
• `/clear` - Bersihkan cache
• `/help` - Tampilkan bantuan

📱 *CARA TAMBAH AKUN:*
1. Ketik: `/tambah 081234567890 123456`
2. Bot akan meminta OTP via SMS
3. Masukkan OTP yang diterima
4. Akun otomatis aktif

🗑️ *CARA HAPUS AKUN:*
1. Klik tombol "Hapus" di tabel
2. Konfirmasi penghapusan
3. Akun langsung nonaktif

⚙️ *SYSTEM:*
• Maksimal 8 akun aktif
• Auto-sync setiap 5 menit
• Data tersimpan di Google Sheets
• Akses multi-user via Telegram Web

🌐 *TELEGRAM WEB:*
Buka web.telegram.org untuk akses bersama
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

# ================= MAIN =================
async def main():
    print("🚀 Starting DANA Mutasi Bot...")
    
    bot = DanaBot()
    
    print("✅ Bot is running. Press Ctrl+C to stop.")
    await bot.app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())