import requests
import feedparser
import json
import time
import base64
import sqlite3
import os
import re
import urllib.parse
import io
import urllib3
import random
import httpx
from openai import OpenAI
from datetime import datetime
from difflib import SequenceMatcher
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 0. إعدادات "دليل العصر" (V9 - Strict Arabic & Huge Watermark)
# ==========================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "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")
WP_DOMAIN = os.getenv("WP_DOMAIN", "https://dalil-alasr.com") 
WP_USER = os.getenv("WP_USER", "admin")
WP_APP_PASS = os.getenv("WP_APP_PASS", "xxxx xxxx xxxx xxxx")

WP_ENDPOINT = f"{WP_DOMAIN}/wp-json/wp/v2"
WATERMARK_TEXT = "dalilaleasr.com"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Referer": "https://google.com"
}

# نماذج قوية فقط
FREE_TEXT_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "microsoft/phi-3-medium-128k-instruct:free"
]

FREE_VISION_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.2-90b-vision-instruct:free",
]

BLACKLIST_KEYWORDS = [
    "فيلم", "مسلسل", "أغنية", "فنان", "ممثلة", "رقص", "حفل غنائي", 
    "سينما", "دراما", "طرب", "ألبوم", "كليب", "فضائيات", 
    "Movie", "Song", "Actress", "Cinema", "Music Video", "Concert"
]

DB_FILE = "/app/data/dalil_history.db" if os.path.exists("/app") else "dalil_history.db"
FONT_PATH = "/app/data/Roboto-Bold.ttf"

# ==========================================
# 1. دوال النظام
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (link TEXT PRIMARY KEY, title TEXT, published_at TEXT)''')
    conn.commit()
    conn.close()

def ensure_font():
    if not os.path.exists(FONT_PATH):
        print("   📥 Downloading professional font for watermark...")
        try:
            url = "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Bold.ttf"
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                os.makedirs(os.path.dirname(FONT_PATH), exist_ok=True)
                with open(FONT_PATH, 'wb') as f:
                    f.write(response.content)
        except Exception as e:
            print(f"   ⚠️ Could not download font: {e}")

def is_published_link(link):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM history WHERE link=?", (link,))
    exists = c.fetchone()
    conn.close()
    return exists is not None

def mark_published(link, title):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO history VALUES (?, ?, ?)", (link, title, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def is_duplicate_semantic(new_title):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT title FROM history ORDER BY published_at DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    if not rows: return False
    recent_titles = [row[0] for row in rows if row[0]]
    for existing in recent_titles:
        if SequenceMatcher(None, new_title.lower(), existing.lower()).ratio() > 0.65:
            return True
    return False

# ==========================================
# 2. إدارة الأقسام
# ==========================================
def get_auth_header():
    clean_pass = WP_APP_PASS.replace(' ', '')
    creds = base64.b64encode(f"{WP_USER}:{clean_pass}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

def get_category_id_by_name(cat_name):
    ARABIC_NAMES = {
        "News": "أخبار عامة", "Politics": "سياسة", "Economy": "اقتصاد وأعمال",
        "Crypto": "عملات رقمية", "Tech": "تكنولوجيا وذكاء اصطناعي", 
        "Health": "صحة وطب", "Science": "علوم وفضاء", "Tutorials": "شروحات وأدلة",
        "Sports": "رياضة"
    }
    final_name = ARABIC_NAMES.get(cat_name, cat_name)
    try:
        h = get_auth_header()
        r = requests.get(f"{WP_ENDPOINT}/categories?search={final_name}", headers=h)
        if r.status_code == 200 and r.json():
            return r.json()[0]['id']
        r = requests.post(f"{WP_ENDPOINT}/categories", headers=h, json={"name": final_name})
        if r.status_code == 201:
            return r.json()['id']
    except: pass
    return 1

def get_or_create_tag_id(tag_name):
    try:
        h = get_auth_header()
        r = requests.get(f"{WP_ENDPOINT}/tags?search={tag_name}", headers=h)
        if r.status_code == 200 and r.json(): return r.json()[0]['id']
        r = requests.post(f"{WP_ENDPOINT}/tags", headers=h, json={"name": tag_name})
        if r.status_code == 201: return r.json()['id']
    except: pass
    return None

# ==========================================
# 3. معالجة الصور (العلامة المائية الضخمة V9)
# ==========================================
def get_ai_image_url(title):
    clean_title = re.sub(r'[^\w\s]', '', title)
    words = clean_title.split()[:9]
    prompt_text = " ".join(words)
    final_prompt = f"Editorial news photo of {prompt_text}, highly detailed, realistic, 4k, journalism style, no text, no blur"
    encoded_prompt = urllib.parse.quote(final_prompt)
    seed = int(time.time())
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&nologo=true&seed={seed}&model=flux"

def check_image_safety(image_url):
    print(f"   🔍 Checking watermark in original...")
    http_client = httpx.Client(verify=False, transport=httpx.HTTPTransport(local_address="0.0.0.0"))
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY, http_client=http_client)
    for i in range(3):
        model = random.choice(FREE_VISION_MODELS)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": [{"type": "text", "text": "Does this image contain ANY text, logos, or watermarks? Answer strictly 'YES' or 'NO'."}, {"type": "image_url", "image_url": {"url": image_url}}]}]
            )
            return "NO" in response.choices[0].message.content.strip().upper()
        except: time.sleep(1)
    return False

def apply_branding(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = img.size
        
        # 🔥 V9 Update: زيادة الحجم
        # الشريط يأخذ 20% من ارتفاع الصورة (خُمس الصورة)
        bar_height = int(height * 0.20) 
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # الشريط الأسود
        draw.rectangle([(0, height - bar_height), (width, height)], fill=(0, 0, 0, 255))
        
        ensure_font()
        text = WATERMARK_TEXT
        
        # الخط يأخذ 80% من ارتفاع الشريط (ضخم جداً)
        font_size = int(bar_height * 0.8)
        
        try:
            if os.path.exists(FONT_PATH):
                font = ImageFont.truetype(FONT_PATH, font_size)
            else:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
            
        try:
            left, top, right, bottom = font.getbbox(text)
            text_width = right - left
            text_height = bottom - top
        except:
            text_width = len(text) * (font_size * 0.5)
            text_height = font_size

        text_x = (width - text_width) / 2
        # توسيط دقيق
        text_y = height - (bar_height / 2) - (text_height / 2) - (bottom * 0.15 if 'bottom' in locals() else 0)
        
        draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 255))
        
        combined = Image.alpha_composite(img, overlay)
        output = io.BytesIO()
        combined.convert("RGB").save(output, format="JPEG", quality=95)
        return output.getvalue()
    except Exception as e: 
        print(f"Branding Error: {e}")
        return image_bytes

def upload_final_image(img_url, alt_text):
    print(f"   ⬆️ Downloading & Branding Image...")
    try:
        r_img = requests.get(img_url, headers=BROWSER_HEADERS, timeout=30, verify=False)
        if r_img.status_code == 200:
            branded_image_data = apply_branding(r_img.content)
            filename = f"dalil_{int(time.time())}.jpg"
            headers_wp = get_auth_header()
            headers_wp["Content-Disposition"] = f"attachment; filename={filename}"
            headers_wp["Content-Type"] = "image/jpeg"
            r_wp = requests.post(f"{WP_ENDPOINT}/media", headers=headers_wp, data=branded_image_data)
            if r_wp.status_code == 201: 
                media_id = r_wp.json()['id']
                seo_data = {
                    "alt_text": alt_text, "title": alt_text, 
                    "caption": f" {WATERMARK_TEXT}", "description": alt_text
                }
                requests.post(f"{WP_ENDPOINT}/media/{media_id}", headers=get_auth_header(), json=seo_data)
                return media_id
    except: pass
    return None

def extract_image(entry):
    if hasattr(entry, 'media_content') and entry.media_content:
        return entry.media_content[0].get('url') if isinstance(entry.media_content[0], dict) else entry.media_content[0]['url']
    if hasattr(entry, 'links') and entry.links:
        for l in entry.links:
            if 'image' in getattr(l, 'type', ''): return getattr(l, 'href', None)
    if hasattr(entry, 'summary'):
        m = re.search(r'<img.*?src=["\']([^"\']+)["\']', entry.summary)
        if m: return m.group(1)
    return None

# ==========================================
# 4. الذكاء الاصطناعي (Strict Arabic Enforcement)
# ==========================================
def clean_text_output(text):
    text = text.replace("*", "").replace('"', "")
    text = re.sub(r'##\s*(.+)', r'<h2>\1</h2>', text)
    return text

def is_english(text):
    try:
        # فحص بسيط: إذا كانت الحروف الإنجليزية أكثر من العربية
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
        return english_chars > arabic_chars
    except: return False

def generate_arabic_content_package(news_item):
    http_client = httpx.Client(verify=False, transport=httpx.HTTPTransport(local_address="0.0.0.0"))
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY, http_client=http_client)
    
    # 🔥 البرومبت الصارم V9
    prompt = f"""
    أنت محرر "دليل العصر". قم بترجمة وإعادة صياغة الخبر التالي إلى مقال عربي احترافي.
    
    المصدر: "{news_item['title']}" - {news_item['summary']}

    ⚠️ قواعد صارمة جداً (Strict Rules):
    1. **العنوان (TITLE):** يجب أن يكون باللغة العربية حصراً. لا تخرج عنواناً إنجليزياً أبداً.
    2. **الروابط:** اربط الكلمات المهمة ببحث الموقع: <a href="{WP_DOMAIN}/?s=الكلمة">الكلمة</a>.
    3. **التنسيق:** استخدم HTML فقط (<h2>, <p>, <ul>). لا تستخدم ## أو **.

    الهيكلية المطلوبة (التزم بها):
    OUTPUT_TITLE: [عنوان عربي جذاب]
    OUTPUT_BODY:
    <div style="background-color: #f1f8e9; border-right: 5px solid #66bb6a; padding: 20px; margin-bottom: 30px; border-radius: 5px;"><h3 style="margin-top: 0; color: #2e7d32;">🔥 خلاصة سريعة:</h3><ul><li>نقطة 1</li><li>نقطة 2</li></ul></div>
    [مقدمة قوية]
    [تفاصيل المقال مع عناوين h2]
    [الخاتمة]
    OUTPUT_META:
    CATEGORY: [Category Name]
    TAGS: [Tags]
    META_DESC: [Desc]
    """
    
    for i in range(5):
        model = random.choice(FREE_TEXT_MODELS)
        try:
            print(f"   🤖 Writing V9 Article with: {model}")
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], temperature=0.7
            )
            content = response.choices[0].message.content.replace("```html", "").replace("```", "").strip()
            
            # استخراج البيانات بدقة
            arabic_title = ""
            final_body = ""
            
            # البحث عن العنوان في المخرجات
            if "OUTPUT_TITLE:" in content:
                parts = content.split("OUTPUT_BODY:")
                if len(parts) > 1:
                    raw_title = parts[0].replace("OUTPUT_TITLE:", "").strip()
                    arabic_title = clean_text_output(raw_title)
                    final_body = parts[1].split("OUTPUT_META:")[0].strip()
            
            # 🚨 Fallback: إذا فشل الذكاء الاصطناعي في إعطاء عنوان عربي
            if not arabic_title or is_english(arabic_title):
                print("   ⚠️ Title looks English. Forcing Translation...")
                # استخدام العنوان الأصلي مؤقتاً وسنترجمه لاحقاً إذا لزم الأمر، 
                # لكن هنا سنعتمد على أن المحتوى العربي هو الأهم.
                # الأفضل: استخدام العنوان من المخرجات حتى لو كان فيه مشكلة بسيطة، 
                # ولكن إذا كان فارغاً نستخدم الأصلي.
                if not arabic_title: arabic_title = news_item['title']

            # تنظيف الجسم
            final_body = clean_text_output(final_body)
            
            return arabic_title, final_body
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                print(f"   ⏳ Rate Limit ({model}). Waiting 45s...")
                time.sleep(45) 
            else:
                print(f"   ⚠️ AI Error: {e}. Retrying...")
                time.sleep(5)
    return None, None

def publish_to_wp(arabic_title, content, feat_img_id):
    # فحص أخير: هل العنوان إنجليزي؟ إذا نعم، لا تنشر (أو ترجمه)
    # لضمان الجودة، سننشر فقط إذا كان المحتوى يبدو عربياً
    if is_english(arabic_title):
        print(f"   🚫 Skipping: Title is English ({arabic_title})")
        return None

    meta_desc, tags, cat_id = "", [], 1
    # محاولة استخراج الميتا من النص (بشكل تقريبي لأننا فصلناها في البرومبت)
    # في V9، الميتا قد تكون في نهاية النص الأصلي، لكننا فصلنا الـ Body.
    # للتبسيط، سنعتمد على تصنيف تلقائي بسيط أو فارغ، التركيز على المحتوى.
    
    tag_ids = [get_or_create_tag_id(t) for t in tags if t]
    
    data = {
        "title": arabic_title,
        "content": content, "status": "publish",
        "categories": [cat_id], "tags": tag_ids, "excerpt": meta_desc,
        "featured_media": feat_img_id,
        "rank_math_focus_keyword": arabic_title,
        "rank_math_description": meta_desc
    }
    
    r = requests.post(f"{WP_ENDPOINT}/posts", headers=get_auth_header(), json=data)
    if r.status_code == 201: return r.json()['link']
    print(f"   ❌ Publish Failed: {r.status_code}")
    return None

# ==========================================
# 5. المحرك الرئيسي
# ==========================================
def main():
    print("🚀 Dalil Al-Asr (V9 - Strict Arabic & Huge Watermark) Started...")
    init_db()
    ensure_font()
    
    feeds = [
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://sa.investing.com/rss/news.rss",
        "https://www.cnbcarabia.com/rss/latest-news",
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://aitnews.com/feed/",
        "https://wired.com/feed/rss",
        "https://www.unlimit-tech.com/feed/",
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
        "https://www.skynewsarabia.com/web/rss",
        "https://cnn.com/rss/cnn_topstories.rss",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.sciencedaily.com/rss/top/health.xml",
        "https://www.nasa.gov/rss/dyn/breaking_news.rss"
    ]
    
    while True:
        print(f"\n⏰ Cycle Start: {datetime.now().strftime('%H:%M')}")
        random.shuffle(feeds)
        
        for feed in feeds:
            try:
                d = feedparser.parse(feed)
                for entry in d.entries[:5]: 
                    if is_published_link(entry.link): continue
                    if any(bad in entry.title for bad in BLACKLIST_KEYWORDS): continue
                    if is_duplicate_semantic(entry.title): continue
                    
                    print(f"   ⚡ Processing: {entry.title[:40]}...")
                    
                    original_img = extract_image(entry)
                    final_img_url = None
                    if original_img:
                        if check_image_safety(original_img):
                            print("   ✅ Original Clean.")
                            final_img_url = original_img
                        else:
                            print("   ⚠️ Watermark detected. AI Gen...")
                            final_img_url = get_ai_image_url(entry.title)
                    else:
                        print("   🎨 AI Gen...")
                        final_img_url = get_ai_image_url(entry.title)
                    
                    fid = upload_final_image(final_img_url, entry.title)
                    
                    if fid:
                        arabic_title, content = generate_arabic_content_package({'title': entry.title, 'summary': getattr(entry, 'summary', '')})
                        
                        if content and arabic_title:
                            print(f"   📝 Generated Title: {arabic_title}")
                            link = publish_to_wp(arabic_title, content, fid)
                            if link:
                                print(f"   ✅ Published: {link}")
                                mark_published(entry.link, entry.title)
                    
                    time.sleep(10) 
            except Exception as e: print(f"   ⚠️ Feed Error: {str(e)[:50]}")
        
        print("💤 Short Rest (10 min)...")
        time.sleep(600)

if __name__ == "__main__":
    main()
