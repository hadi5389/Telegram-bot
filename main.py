import os
import telebot
from openai import OpenAI

BOT_TOKEN = os.environ.get('BOT_TOKEN')
NVIDIA_API_KEY = os.environ.get('NVIDIA_API_KEY')

bot = telebot.TeleBot(BOT_TOKEN)

# آپدیت آدرس بیس به نسخه پایدار و جهانی انویدیا
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY
)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        # تغییر مدل به یک مدل بسیار سریع و استاندارد
        completion = client.chat.completions.create(
            model="meta/llama3-70b-instruct", 
            messages=[{"role": "user", "content": message.text}],
            temperature=0.7,
            max_tokens=1024
        )
        
        response = completion.choices[0].message.content
        bot.reply_to(message, response)
        
    except Exception as e:
        bot.reply_to(message, f"خطایی رخ داد: {str(e)}")

print("ربات با تنظیمات جدید روشن شد...")
bot.infinity_polling()
