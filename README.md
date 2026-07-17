# ربات تلگرام + NVIDIA Build API روی Railway

ربات تلگرامی که پیام‌های کاربران را به یک مدل زبانی روی **NVIDIA Build API**
ارسال می‌کند و پاسخ را بدون تغییر بازمی‌گرداند. اجرا کاملاً بدون VPS، روی
**Railway**، با **Webhook** (بدون Polling) و با **FastAPI** به‌عنوان وب‌سرور.

## ساختار پروژه

```
.
├── main.py            # کل منطق برنامه: FastAPI + python-telegram-bot + کلاینت NVIDIA
├── requirements.txt   # وابستگی‌های پایتون با نسخه‌های ثابت
├── Procfile           # دستور اجرا (پشتیبان/جایگزین railway.json)
├── runtime.txt        # نسخه پایتون (3.12)
├── railway.json       # پیکربندی Build/Deploy در Railway
├── .env.example        # نمونه متغیرهای محیطی مورد نیاز
├── .gitignore
└── README.md
```

## متغیرهای محیطی (Environment Variables)

هیچ Secret ای داخل کد نیست؛ همه از Environment Variables خوانده می‌شوند:

| متغیر | توضیح | الزامی |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | توکن ربات، از [@BotFather](https://t.me/BotFather) | بله |
| `NVIDIA_API_KEY` | کلید API از [build.nvidia.com](https://build.nvidia.com) | بله |
| `NVIDIA_MODEL` | نام مدل، مثلاً `meta/llama-3.1-70b-instruct` | خیر (مقدار پیش‌فرض دارد) |
| `WEBHOOK_URL` | آدرس عمومی سرویس روی Railway، مثلاً `https://your-app.up.railway.app` (بدون `/` انتهایی) | بله |
| `PORT` | پورتی که Railway خودش تزریق می‌کند | خیر (Railway مقداردهی می‌کند) |

اگر هرکدام از سه متغیر الزامی تنظیم نشده باشند، برنامه هنگام Start با خطای
واضح متوقف می‌شود تا Secretها هرگز به‌صورت پیش‌فرض خالی اجرا نشوند.

## عملکرد ربات

- `/start` → نمایش پیام خوش‌آمدگویی
- `/help` → نمایش راهنما
- هر پیام متنی دیگر → به NVIDIA Build API ارسال می‌شود و پاسخ مدل، **بدون
  تغییر**، برای کاربر ارسال می‌شود.
- اگر پاسخ مدل بلندتر از محدودیت ۴۰۹۶ کاراکتری تلگرام باشد، به‌صورت خودکار
  به چند پیام تقسیم می‌شود (با تلاش برای شکستن روی خط جدید یا فاصله، برای
  حفظ خوانایی).
- در صورت بروز خطای موقتی از سمت NVIDIA (کدهای 429 و 5xx) یا Timeout، تا ۳
  بار با backoff نمایی تلاش مجدد انجام می‌شود.
- در صورت خطای غیرقابل‌جبران (مثلاً کلید API نامعتبر)، پیام خطای مناسب و
  قابل‌فهم به کاربر نمایش داده می‌شود؛ جزئیات فنی فقط در لاگ‌ها ثبت می‌شود.
- پشتیبانی کامل از UTF-8 و متن فارسی، هم در دریافت و هم در ارسال پیام‌ها.

## نحوه‌ی کارکرد Webhook

روی استارت‌آپ FastAPI (در `lifespan`)، برنامه به‌صورت خودکار Webhook تلگرام
را روی آدرس زیر تنظیم می‌کند:

```
{WEBHOOK_URL}/webhook/{TELEGRAM_BOT_TOKEN}
```

قرار دادن توکن در مسیر Webhook باعث می‌شود درخواست‌های تصادفی به ریشه دامنه
نتوانند Update ای را پردازش کنند. نیازی به تنظیم دستی Webhook نیست — کافی
است `WEBHOOK_URL` به‌درستی تنظیم شده باشد.

## مراحل استقرار روی Railway (قدم‌به‌قدم)

1. **ساخت ربات در تلگرام**
   در تلگرام به [@BotFather](https://t.me/BotFather) پیام دهید، دستور
   `/newbot` را بزنید و توکن دریافتی را نگه دارید (`TELEGRAM_BOT_TOKEN`).

2. **دریافت API Key از NVIDIA Build**
   وارد [build.nvidia.com](https://build.nvidia.com) شوید، یک API Key
   بسازید (`NVIDIA_API_KEY`) و نام مدل موردنظر را از صفحه مدل کپی کنید
   (`NVIDIA_MODEL`، مثلاً `meta/llama-3.1-70b-instruct`).

3. **آپلود پروژه در یک ریپازیتوری Git**
   این پوشه را در یک ریپازیتوری GitHub (یا GitLab) قرار دهید. فایل `.env`
   واقعی را هرگز commit نکنید — `.gitignore` از قبل آن را نادیده می‌گیرد.

4. **ساخت پروژه جدید در Railway**
   - وارد [railway.app](https://railway.app) شوید.
   - `New Project` → `Deploy from GitHub repo` را انتخاب کنید.
   - ریپازیتوری پروژه را انتخاب کنید. Railway به‌صورت خودکار Nixpacks و
     `railway.json` را تشخیص می‌دهد و پایتون 3.12 را با توجه به
     `runtime.txt` نصب می‌کند.

5. **تنظیم Environment Variables**
   در پنل Railway وارد تب `Variables` شوید و مقادیر زیر را وارد کنید:
   - `TELEGRAM_BOT_TOKEN`
   - `NVIDIA_API_KEY`
   - `NVIDIA_MODEL`
   - `WEBHOOK_URL` → آدرس عمومی سرویس (مرحله بعد توضیح داده می‌شود)

   نیازی به تنظیم دستی `PORT` نیست؛ Railway خودش آن را تزریق می‌کند.

6. **فعال‌سازی Public Networking و تعیین WEBHOOK_URL**
   - در تب `Settings` سرویس، بخش `Networking` را باز کنید و
     `Generate Domain` را بزنید تا یک دامنه عمومی مثل
     `your-app-name.up.railway.app` بسازید.
   - این آدرس را (با `https://` و بدون `/` انتهایی) در متغیر `WEBHOOK_URL`
     قرار دهید و سرویس را دوباره Deploy/Restart کنید.

7. **Deploy**
   Railway به‌صورت خودکار `requirements.txt` را نصب و برنامه را با دستور
   مشخص‌شده در `railway.json` (یا `Procfile`) اجرا می‌کند:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
   در لاگ‌های Deploy باید خط زیر را ببینید:
   ```
   Webhook successfully set to: https://your-app-name.up.railway.app/webhook/<TOKEN>
   ```
   یعنی Webhook به‌صورت کاملاً خودکار، بدون هیچ دستور دستی، تنظیم شده است.

8. **تست ربات**
   در تلگرام به ربات خود پیام `/start` بدهید. باید پیام خوش‌آمدگویی را
   دریافت کنید. سپس یک پیام متنی معمولی ارسال کنید تا پاسخ مدل هوش مصنوعی
   را ببینید.

## اجرای محلی (اختیاری، برای تست قبل از Deploy)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # سپس مقادیر واقعی را در .env قرار دهید
export $(grep -v '^#' .env | xargs)   # بارگذاری متغیرها در شل
python main.py
```

توجه: برای دریافت Webhook در حالت لوکال باید دامنه شما از طریق HTTPS در
دسترس عموم باشد (مثلاً با ابزارهایی مانند ngrok/Cloudflare Tunnel). برای
Production همیشه از Railway استفاده کنید.

## نکات امنیتی

- هیچ API Key یا Tokenی داخل کد یا مخزن Git قرار نگیرد.
- فایل `.env` واقعی هرگز commit نشود (`.gitignore` این کار را تضمین می‌کند).
- مسیر Webhook شامل توکن ربات است تا از فراخوانی‌های ناخواسته جلوگیری شود.
- در صورت افشای هرکدام از کلیدها، بلافاصله در BotFather (`/revoke`) یا پنل
  NVIDIA Build، کلید را باطل و جایگزین کنید.
