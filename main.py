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
# 0. إعدادات "دليل العصر"
# ==========================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "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")

WP_DOMAIN = os.getenv("WP_DOMAIN", "https://dalil-alasr.com") 
WP_USER = os.getenv("WP_USER", "admin")
WP_APP_PASS = os.getenv("WP_APP_PASS", "xxxx xxxx xxxx xxxx")

WP_ENDPOINT = f"{WP_DOMAIN}/wp-json/wp/v2"

# اسم الموقع الذي سيظهر على الصور
SITE_NAME_WATERMARK = "Dalil Al-Asr"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Referer": "https://google.com"
}

# النماذج المستقرة
FREE_TEXT_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free", 
    "deepseek/deepseek-chat:free"
]

FREE_VISION_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.2-90b-vision-instruct:free",
]

# كلمات محظورة (فن، أغاني)
BLACKLIST_KEYWORDS = [
    "فيلم", "مسلسل", "أغنية", "فنان", "ممثلة", "رقص", "حفل غنائي", 
    "سينما", "دراما", "طرب", "ألبوم", "كليب", "فضائيات", 
    "Movie", "Song", "Actress", "Cinema", "Music Video", "Concert"
]

DB_FILE = "/app/data/dalil_history.db" if os.path.exists("/app") else "dalil_history.db"

# ==========================================
# 1. دوال النظام وقاعدة البيانات
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (link TEXT PRIMARY KEY, title TEXT, published_at TEXT)''')
    conn.commit()
    conn.close()

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
        # نسبة تطابق أقل (65%) لأننا نترجم، فالعناوين قد تختلف قليلاً
        if SequenceMatcher(None, new_title.lower(), existing.lower()).ratio() > 0.65:
            return True
    return False

# ==========================================
# 2. إدارة الأقسام (Auto Category Creator)
# ==========================================
def get_auth_header():
    clean_pass = WP_APP_PASS.replace(' ', '')
    creds = base64.b64encode(f"{WP_USER}:{clean_pass}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

def get_category_id_by_name(cat_name):
    """يبحث عن القسم، إذا لم يجده يقوم بإنشائه"""
    # ترجمة أسماء الأقسام الإنجليزية إلى عربية لإنشائها في الموقع
    ARABIC_NAMES = {
        "News": "أخبار عامة", "Politics": "سياسة", "Economy": "اقتصاد وأعمال",
        "Crypto": "عملات رقمية", "Tech": "تكنولوجيا وذكاء اصطناعي", 
        "Health": "صحة وطب", "Science": "علوم وفضاء", "Tutorials": "شروحات وأدلة",
        "Sports": "رياضة"
    }
    
    final_name = ARABIC_NAMES.get(cat_name, cat_name)
    
    try:
        h = get_auth_header()
        # البحث
        r = requests.get(f"{WP_ENDPOINT}/categories?search={final_name}", headers=h)
        if r.status_code == 200 and r.json():
            return r.json()[0]['id']
        
        # الإنشاء إذا لم يوجد
        print(f"   🔨 Creating Category: {final_name}...")
        r = requests.post(f"{WP_ENDPOINT}/categories", headers=h, json={"name": final_name})
        if r.status_code == 201:
            return r.json()['id']
    except: pass
    return 1 # Uncategorized fallback

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
# 3. معالجة الصور والعلامة المائية
# ==========================================
def get_ai_image_url(title):
    clean_title = re.sub(r'[^\w\s]', '', title)
    words = clean_title.split()[:9]
    prompt_text = " ".join(words)
    # طلب صورة واقعية جداً
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
            is_clean = "NO" in response.choices[0].message.content.strip().upper()
            return is_clean
        except: time.sleep(1)
    return False

def apply_branding(image_bytes):
    """تضيف العلامة المائية الخاصة بموقعك على أي صورة"""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = img.size
        
        # إنشاء طبقة شفافة
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        # شريط أسود شفاف في الأسفل
        bar_height = int(height * 0.08) 
        draw.rectangle([(0, height - bar_height), (width, height)], fill=(0, 0, 0, 160))
        
        # كتابة اسم الموقع
        try: 
            # محاولة تحميل خط، وإذا فشل نستخدم الافتراضي
            font_size = int(bar_height * 0.6)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()
        except: return image_bytes
        
        text = SITE_NAME_WATERMARK
        # حساب مكان النص ليكون في المنتصف
        # (بما أن load_default لا يدعم getbbox بدقة في بعض النسخ القديمة، سنضعه تقريبياً)
        text_x = width / 2 - (len(text) * 5) 
        text_y = height - (bar_height * 0.8)
        
        draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 255))
        
        combined = Image.alpha_composite(img, overlay)
        output = io.BytesIO()
        combined.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue()
    except Exception as e:
        print(f"   ⚠️ Branding Error: {e}")
        return image_bytes

def upload_final_image(img_url, alt_text):
    print(f"   ⬆️ Downloading & Branding Image...")
    try:
        r_img = requests.get(img_url, headers=BROWSER_HEADERS, timeout=30, verify=False)
        if r_img.status_code == 200:
            # 💡 الخطوة المهمة: وضع العلامة المائية
            branded_image_data = apply_branding(r_img.content)
            
            filename = f"dalil_{int(time.time())}.jpg"
            headers_wp = get_auth_header()
            headers_wp["Content-Disposition"] = f"attachment; filename={filename}"
            headers_wp["Content-Type"] = "image/jpeg"
            
            r_wp = requests.post(f"{WP_ENDPOINT}/media", headers=headers_wp, data=branded_image_data)
            if r_wp.status_code == 201: 
                media_id = r_wp.json()['id']
                seo_data = {
                    "alt_text": alt_text, 
                    "title": alt_text, 
                    "caption": f"حقوق الصورة محفوظة لـ {SITE_NAME_WATERMARK}", 
                    "description": alt_text
                }
                requests.post(f"{WP_ENDPOINT}/media/{media_id}", headers=get_auth_header(), json=seo_data)
                return media_id
    except Exception as e: print(f"   ❌ Upload Error: {e}")
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
# 4. الذكاء الاصطناعي (محرر وصحفي)
# ==========================================
def generate_arabic_content(news_item):
    http_client = httpx.Client(verify=False, transport=httpx.HTTPTransport(local_address="0.0.0.0"))
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY, http_client=http_client)
    
    prompt = f"""
    أنت رئيس تحرير موقع "دليل العصر". مهمتك قراءة النص التالي (بأي لغة كانت) وإعادة كتابته بمقال احترافي باللغة العربية الفصحى.

    النص الأصلي: "{news_item['title']}" - {news_item['summary']}

    الشروط الصارمة:
    1. **الحفاظ على المضمون:** لا تغير الحقائق أو الأرقام. انقل الخبر بدقة.
    2. **اللغة:** عربية فصحى، قوية، وسهلة الفهم.
    3. **الهيكلية (HTML):**
       - عنوان فرعي <h2> للمقدمة.
       - فقرات <p> (Direction: RTL).
       - إذا كان الخبر شرحاً (Tutorial)، استخدم خطوات مرقمة <ol><li>...</li></ol>.
       - صندوق معلومات: <div style="background-color: #e3f2fd; padding: 15px; border-radius: 5px;"><strong>ℹ️ معلومة سريعة:</strong> ...</div>
    4. **التصنيف:** في آخر سطر، اختر قسماً واحداً من القائمة التالية بالإنجليزية:
       [Politics, Economy, Crypto, Tech, Science, Health, Sports, Tutorials, News]
       الشكل المطلوب في آخر سطر:
       CATEGORY: Tech
       TAGS: كلمة1, كلمة2, كلمة3
       META_DESC: وصف قصير وشيق للمقال لا يتجاوز 150 حرف.

    ابدأ الكتابة مباشرة.
    """
    
    for i in range(5):
        model = random.choice(FREE_TEXT_MODELS)
        try:
            print(f"   🤖 Writing with: {model}")
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], temperature=0.5
            )
            content = response.choices[0].message.content.replace("```html", "").replace("```", "").strip()
            return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', content)
        except Exception as e:
            print(f"   ⚠️ AI Error: {e}. Retrying...")
            time.sleep(2)
    return None

def publish_to_wp(title, content, feat_img_id):
    meta_desc, tags, cat_id = "", [], 1
    
    # استخراج البيانات من رد الذكاء الاصطناعي
    try:
        if "CATEGORY:" in content:
            parts = content.split("CATEGORY:")
            body_content = parts[0].strip()
            metadata = parts[1]
            
            # استخراج القسم
            cat_name = metadata.split("\n")[0].strip()
            cat_id = get_category_id_by_name(cat_name)
            
            # استخراج الوسوم والوصف
            if "TAGS:" in metadata:
                tags_part = metadata.split("TAGS:")[1].split("META_DESC:")[0]
                tags = [t.strip() for t in tags_part.split(",")]
            if "META_DESC:" in metadata:
                meta_desc = metadata.split("META_DESC:")[1].strip()
                
            content = body_content
    except: pass

    tag_ids = [get_or_create_tag_id(t) for t in tags if t]
    
    # تنظيف العنوان من أي كلمات إنجليزية زائدة إذا وجدت
    clean_title = title # يمكن تحسين هذا لاحقاً ليترجم العنوان أيضاً
    # ملاحظة: في النسخة القادمة يمكننا جعل الـ AI يعطينا العنوان العربي أيضاً.
    # حالياً سنعتمد على أن المحتوى عربي والعنوان أصلي (قد يكون إنجليزي)،
    # سأضيف تعديلاً بسيطاً لجعل العنوان عربياً أيضاً في الـ Prompt القادم،
    # لكن حالياً المحتوى هو الأهم.
    
    data = {
        "title": title, # سيتم استخدام العنوان الأصلي، يفضل تعديل البرومبت ليعطي عنواناً عربياً
        "content": content, "status": "publish",
        "categories": [cat_id], "tags": tag_ids, "excerpt": meta_desc,
        "featured_media": feat_img_id,
        "rank_math_focus_keyword": tags[0] if tags else "أخبار",
        "rank_math_description": meta_desc
    }
    data["meta"] = {"rank_math_focus_keyword": data["rank_math_focus_keyword"], "rank_math_description": meta_desc}
    
    r = requests.post(f"{WP_ENDPOINT}/posts", headers=get_auth_header(), json=data)
    if r.status_code == 201: return r.json()['link']
    print(f"   ❌ Publish Failed: {r.status_code}")
    return None

# ==========================================
# 5. المصادر المتنوعة (كثافة عالية)
# ==========================================
def main():
    print("🚀 Dalil Al-Asr (Content Machine V2) Started...")
    init_db()
    
    # قائمة ضخمة من المصادر لضمان التنوع
    feeds = [
        # --- كريبتو ومال ---
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://sa.investing.com/rss/news.rss",
        "https://www.cnbcarabia.com/rss/latest-news",
        
        # --- تقنية وذكاء اصطناعي ---
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://aitnews.com/feed/",
        "https://wired.com/feed/rss",
        "https://www.unlimit-tech.com/feed/",
        
        # --- سياسة وعالم ---
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
        "https://www.skynewsarabia.com/web/rss",
        "https://cnn.com/rss/cnn_topstories.rss",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        
        # --- علوم وصحة ---
        "https://www.sciencedaily.com/rss/top/health.xml",
        "https://www.nasa.gov/rss/dyn/breaking_news.rss"
    ]
    
    while True:
        print(f"\n⏰ Cycle Start: {datetime.now().strftime('%H:%M')}")
        random.shuffle(feeds) # خلط المصادر لعدم نشر نفس النوع متتالياً
        
        for feed in feeds:
            try:
                d = feedparser.parse(feed)
                # زيادة العدد إلى 5 لملء الموقع
                for entry in d.entries[:5]: 
                    if is_published_link(entry.link): continue
                    
                    # فلتر الفن
                    if any(bad in entry.title for bad in BLACKLIST_KEYWORDS):
                        continue
                        
                    if is_duplicate_semantic(entry.title): continue
                    
                    print(f"   ⚡ Processing: {entry.title[:50]}...")
                    
                    # 1. الصورة
                    original_img = extract_image(entry)
                    final_img_url, img_source = None, ""
                    
                    if original_img:
                        # نفحص هل هي نظيفة؟
                        if check_image_safety(original_img):
                            print("   ✅ Original Image Clean. Branding it...")
                            final_img_url = original_img
                        else:
                            print("   ⚠️ Original has watermark. Generating AI Image...")
                            final_img_url = get_ai_image_url(entry.title)
                            img_source = "AI"
                    else:
                        print("   🎨 No image found. Generating AI Image...")
                        final_img_url = get_ai_image_url(entry.title)
                        img_source = "AI"
                    
                    # رفع الصورة مع العلامة المائية
                    fid = upload_final_image(final_img_url, entry.title)
                    
                    if fid:
                        # 2. المحتوى العربي
                        content = generate_arabic_content({'title': entry.title, 'summary': getattr(entry, 'summary', '')})
                        if content:
                            link = publish_to_wp(entry.title, content, fid)
                            if link:
                                print(f"   ✅ Published: {link}")
                                mark_published(entry.link, entry.title)
                    
                    # انتظار قصير بين المقالات (سرعة أعلى)
                    time.sleep(10) 
            except Exception as e: 
                print(f"   ⚠️ Feed Error: {str(e)[:50]}")
        
        print("💤 Short Rest (10 min)...")
        time.sleep(600)

if __name__ == "__main__":
    main()
