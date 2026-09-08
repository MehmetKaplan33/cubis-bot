import os
import time
import logging
import threading
import telebot
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Loglama ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# .env dosyasındaki bilgileri yükle
load_dotenv()

OGRENCI_NO = os.getenv("OGRENCI_NO")
SIFRE = os.getenv("SIFRE")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
KONTROL_ARALIGI = int(os.getenv("KONTROL_ARALIGI_DAKIKA", "3")) * 60

# Yeni Ders Değişkenleri (Boşlukları temizleyip listeye çeviriyoruz)
raw_takip = os.getenv("TAKIP_EDILECEK_DERSLER", "")
TAKIP_DERSLERI = [d.strip().upper() for d in raw_takip.split(",") if d.strip()]

raw_alarm = os.getenv("ALARM_DERSLERI", "")
ALARM_DERSLERI = [d.strip().upper() for d in raw_alarm.split(",") if d.strip()]

LOGIN_URL = "https://login.cu.edu.tr/Login.aspx?ReturnUrl=%2f"
DERS_KAYIT_URL = "https://derskayit.cu.edu.tr/DerseYazilma"

INPUT_USER_SELECTOR = "input[type='text']"
INPUT_PASS_SELECTOR = "input[type='password']"
BTN_LOGIN_SELECTOR = "input[type='submit'], button[type='submit'], .btn-primary"

# Telegram Botunu Başlat
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Global Durum Hafızası (Sürekli güncellenecek)
LATEST_DATA = {}
LAST_UPDATE_TIME = "Henüz veri çekilmedi"


# --- TELEGRAM MESAJ DİNLEYİCİLERİ ---

@bot.message_handler(commands=['start', 'basla'])
def send_welcome(message):
    """Kullanıcı bota ilk girdiğinde veya /start yazdığında çalışır."""
    # Menüye hızlı buton ekleyelim
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    itembtn = telebot.types.KeyboardButton('/durum')
    markup.add(itembtn)
    
    bot.reply_to(message, "🤖 ÇÜBİS Bot'a Hoş Geldiniz!\n\nTakip edilen derslerin güncel kontenjanlarını öğrenmek için klavyenizdeki **/durum** butonuna basabilir veya doğrudan /durum yazabilirsiniz.", reply_markup=markup, parse_mode='Markdown')


@bot.message_handler(commands=['durum'])
def send_status(message):
    """Kullanıcı /durum yazdığında anlık olarak LATEST_DATA hafızasını okuyup cevap verir."""
    if not LATEST_DATA:
        bot.reply_to(message, "⏳ Henüz sistemden hiç veri çekilmedi. Lütfen botun ilk kontrolü (1-2 dk) tamamlamasını bekleyin.")
        return
    
    mesaj = f"📊 <b>GÜNCEL KONTENJAN DURUMU</b>\n"
    mesaj += f"🕒 <i>Son Güncelleme: {LAST_UPDATE_TIME}</i>\n\n"
    
    for ders_kod in TAKIP_DERSLERI:
        data = LATEST_DATA.get(ders_kod)
        if data:
            if data["kalan"] > 0:
                durum_ikon = "✅"
            else:
                durum_ikon = "❌"
                
            mesaj += f"{durum_ikon} <b>{ders_kod}</b> - {data['ad']}\n"
            mesaj += f"    └ Grup {data['grup']} | Kayıtlı: {data['kayitli']}/{data['limit']} (Kalan: {data['kalan']})\n\n"
        else:
            mesaj += f"❓ <b>{ders_kod}</b>: Sistemde bulunamadı veya size açık değil.\n\n"
            
    bot.reply_to(message, mesaj, parse_mode='HTML')


# --- ARKA PLAN (SCRAPER) GÖREVİ ---

def check_course_quota():
    """ÇÜBİS'e girip verileri çeken fonksiyon"""
    global LATEST_DATA, LAST_UPDATE_TIME
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(LOGIN_URL)
            page.locator(INPUT_USER_SELECTOR).first.fill(OGRENCI_NO)
            page.locator(INPUT_PASS_SELECTOR).first.fill(SIFRE)
            page.locator(BTN_LOGIN_SELECTOR).first.click()
            page.wait_for_timeout(3000)
            
            page.goto(DERS_KAYIT_URL)
            page.wait_for_load_state("networkidle")
            
            data_json = page.evaluate("() => typeof dataJSON !== 'undefined' ? dataJSON : null")
            
            if not data_json:
                logging.error("Sayfada dataJSON bulunamadı! Sayfa yapısı değişmiş veya giriş başarısız.")
                return False

            temp_data = {}
            alarm_gonderildi = False

            # JSON listesini tara
            for item in data_json:
                ders_bilgi = item.get("DersBilgi", {})
                ders_tanim = ders_bilgi.get("DersTanim", {})
                ders_kod = ders_tanim.get("DersKod", "").replace(" ", "").upper()
                
                # Sadece .env'de belirttiğimiz TAKIP_EDILECEK_DERSLER'e bak
                if ders_kod in TAKIP_DERSLERI:
                    ders_ad = ders_tanim.get("DersAd", "Bilinmeyen Ders")
                    grup = item.get("YazilmaGrup")
                    
                    if grup:
                        grup_ad = grup.get("Ad", "?")
                        limit = int(grup.get("Limit", 0))
                        kayitli = int(grup.get("OgrenciSayisi", 0))
                        kalan_kontenjan = limit - kayitli
                        
                        # Bu dersi hafızaya kaydet
                        temp_data[ders_kod] = {
                            "ad": ders_ad,
                            "grup": grup_ad,
                            "limit": limit,
                            "kayitli": kayitli,
                            "kalan": kalan_kontenjan
                        }
                        
                        logging.info(f"[DURUM] {ders_kod}: {kayitli}/{limit} (Kalan: {kalan_kontenjan})")
                        
                        # SADECE Alarm Listesindeki bir ders ise ve KONTENJAN VARSA mesaj at
                        if ders_kod in ALARM_DERSLERI and kalan_kontenjan > 0:
                            alarm_gonderildi = True
                            mesaj = (
                                f"🎉 <b>KONTENJAN AÇILDI!</b> 🎉\n\n"
                                f"📚 <b>Ders:</b> {ders_kod} - {ders_ad}\n"
                                f"🏷 <b>Grup:</b> {grup_ad}\n"
                                f"✅ <b>Kalan Kontenjan:</b> {kalan_kontenjan}\n\n"
                                f"Hemen sisteme girip dersi seçin: <a href='{DERS_KAYIT_URL}'>ÇÜBİS'e Git</a>"
                            )
                            try:
                                bot.send_message(TELEGRAM_CHAT_ID, mesaj, parse_mode='HTML')
                            except Exception as e:
                                logging.error(f"Alarm gönderilemedi: {e}")

            # Hafızayı ve Son Güncelleme saatini yeni verilerle yenile
            LATEST_DATA = temp_data
            LAST_UPDATE_TIME = datetime.now().strftime("%H:%M:%S")
            return alarm_gonderildi

        except Exception as e:
            logging.error(f"Hata oluştu: {e}")
            return False
        finally:
            browser.close()


def scraper_thread():
    """Arka planda sürekli çalışacak sonsuz döngü."""
    logging.info("🚀 Arka plan veri çekme işlemi başlatıldı...")
    try:
        bot.send_message(TELEGRAM_CHAT_ID, "🤖 Bot çoklu ders takibine başladı! Anlık durum için bana /durum yazabilir veya menüdeki butonu kullanabilirsiniz.")
    except Exception:
        pass

    while True:
        logging.info("--- Yeni Kontrol Başlıyor ---")
        kontenjan_acik_mi = check_course_quota()
        
        if kontenjan_acik_mi:
            logging.info("Alarm verildi! 1 dakika sonra tekrar kontrol edilecek.")
            time.sleep(60)
        else:
            logging.info(f"Bekleniyor... ({KONTROL_ARALIGI // 60} dakika)")
            time.sleep(KONTROL_ARALIGI)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        logging.error("Telegram Bot Token eksik!")
        exit(1)
        
    # 1. Arka planda çalışacak tarama işlemini "Thread" olarak başlatıyoruz
    # daemon=True sayesinde ana program kapanırsa bu da kapanır.
    t = threading.Thread(target=scraper_thread)
    t.daemon = True
    t.start()
    
    # 2. Ana program Telegram'dan gelecek komutları (örn: /durum) dinliyor
    logging.info("🎧 Telegram botu komutları dinlemeye hazır.")
    try:
        bot.infinity_polling()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot kullanıcı tarafından durduruldu.")
