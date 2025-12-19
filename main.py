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
# 0. إعدادات النظام (V31 - Stable List)
# ==========================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "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")

WP_DOMAIN = os.getenv("WP_DOMAIN", "https://cryptoepochs.com")
WP_USER = os.getenv("WP_USER", "jad")
WP_APP_PASS = os.getenv("WP_APP_PASS", "DBy4 QJKf grn2 XsY5 CKm9 jQlD")

WP_ENDPOINT = f"{WP_DOMAIN}/wp-json/wp/v2"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Referer": "https://google.com"
}

# === قائمة النماذج المجانية المستقرة (V31) ===
# تم حذف النماذج التي تسبب مشاكل 404
FREE_TEXT_MODELS = [
    "google/gemini-2.0-flash-exp:free",          # الأقوى والأحدث
    "meta-llama/llama-3.3-70b-instruct:free",    # مستقر جداً
    "deepseek/deepseek-chat:free",               # ممتاز للكود والنصوص
    "qwen/qwen-2.5-72b-instruct:free",           # بديل قوي
    "meta-llama/llama-3.1-405b-instruct:free",   # الأذكى (قد يكون بطيئاً)
    "huggingfaceh4/zephyr-7b-beta:free",         # خفيف وسريع
]

# === قائمة نماذج الرؤية ===
FREE_VISION_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.2-90b-vision-instruct:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
]

# ==========================================
# 1. خريطة الصور والأقسام
# ==========================================
EMERGENCY_MAP = {
    "bitcoin": [
        "https://images.unsplash.com/photo-1621761191319-c6fb62004040?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1596239464385-2800555f68b4?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1518546305927-5a555bb7020d?auto=format&fit=crop&w=1280&q=80"
    ],
    "ethereum": [
        "https://images.unsplash.com/photo-1622630998477-20aa696fab05?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1621416894569-0f39ed31d247?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1644361566696-3d442b5b482a?auto=format&fit=crop&w=1280&q=80"
    ],
    "regulation": [
        "https://images.unsplash.com/photo-1589829085413-56de8ae18c73?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1505664194779-8beaceb93744?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1639322537228-ad71053ade42?auto=format&fit=crop&w=1280&q=80"
    ],
    "market": [
        "https://images.unsplash.com/photo-1611974765270-ca12586343bb?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1642790106117-e829e14a795f?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?auto=format&fit=crop&w=1280&q=80"
    ],
    "security": [
        "https://images.unsplash.com/photo-1563986768609-322da13575f3?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1280&q=80"
    ],
    "default": [
        "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1642543492481-44e81e3914a7?auto=format&fit=crop&w=1280&q=80",
        "https://images.unsplash.com/photo-1620321023374-d1a68fddadb3?auto=format&fit=crop&w=1280&q=80"
    ]
}

CATEGORY_MAP = {
    "News": 2, "Bitcoin": 2, "Ethereum": 2, "Web3": 2, "Regulation": 2, "Crypto": 2,
    "Market": 3, "Analysis": 3, "Price": 3, "Trading": 3, "Chart": 3,
    "DeFi": 4, "DEX": 4, "Swap": 4, "Lending": 4,
    "Stablecoin": 6, "USDT": 6, "USDC": 6, "Tether": 6,
    "DAO": 7, "Governance": 7,
    "Education": 5, "Guide": 5, "Tutorial": 5, "Learn": 5, "How": 5,
    "Uncategorized": 1
}
DEFAULT_CATEGORY_ID = 2

DB_FILE = "/app/data/history.db" if os.path.exists("/app") else "history.db"

# ==========================================
# 2. دوال قاعدة البيانات
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

# ==========================================
# 3. نظام منع التكرار
# ==========================================
def is_duplicate_semantic(new_title):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT title FROM history ORDER BY published_at DESC LIMIT 30")
    rows = c.fetchall()
    conn.close()
    if not rows: return False
    recent_titles = [row[0] for row in rows if row[0]]
    for existing in recent_titles:
        if SequenceMatcher(None, new_title.lower(), existing.lower()).ratio() > 0.75:
            return True
    return False

# ==========================================
# 4. معالجة الصور (Updated)
# ==========================================
def get_smart_image_url(title):
    clean_title = re.sub(r'[^\w\s]', '', title)
    words = clean_title.split()[:8]
    prompt_text = " ".join(words)
    final_prompt = f"{prompt_text}, crypto news style, futuristic, 8k, cinematic, digital art"
    encoded_prompt = urllib.parse.quote(final_prompt)
    seed = int(time.time())
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&nologo=true&seed={seed}&model=flux"

def get_emergency_image_list(title):
    t = title.lower()
    key = "default"
    if "bitcoin" in t or "btc" in t: key = "bitcoin"
    elif "ethereum" in t or "eth" in t: key = "ethereum"
    elif any(x in t for x in ["law", "sec", "regulation", "court"]): key = "regulation"
    elif any(x in t for x in ["hack", "scam", "security", "stolen"]): key = "security"
    elif any(x in t for x in ["market", "price", "trading", "bull", "bear"]): key = "market"
    images = EMERGENCY_MAP[key].copy()
    random.shuffle(images)
    return images

def check_image_safety(image_url):
    print(f"   🔍 Checking watermark: {image_url[:40]}...")
    http_client = httpx.Client(verify=False, transport=httpx.HTTPTransport(local_address="0.0.0.0"))
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY, http_client=http_client)
    
    # محاولة 3 مرات
    for i in range(3):
        model = random.choice(FREE_VISION_MODELS)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": [{"type": "text", "text": "Does this image contain ANY text, watermarks, or news logos? Answer strictly 'YES' or 'NO'."}, {"type": "image_url", "image_url": {"url": image_url}}]}]
            )
            return "YES" not in response.choices[0].message.content.strip().upper()
        except:
            time.sleep(1)
    return False

def apply_watermark(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        width, height = img.size
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bar_height = int(height * 0.08) 
        draw.rectangle([(0, height - bar_height), (width, height)], fill=(0, 0, 0, 180))
        try: font = ImageFont.load_default() 
        except: return image_bytes
        text = "CryptoEpochs.com"
        text_x = width / 2 - 50
        text_y = height - (bar_height / 1.5)
        draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 255))
        combined = Image.alpha_composite(img, overlay)
        output = io.BytesIO()
        combined.convert("RGB").save(output, format="JPEG", quality=90)
        return output.getvalue()
    except: return image_bytes

# ==========================================
# 5. توليد المحتوى (More Retries)
# ==========================================
def generate_content(news_item):
    http_client = httpx.Client(verify=False, transport=httpx.HTTPTransport(local_address="0.0.0.0"))
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY, http_client=http_client)
    
    prompt = f"""
    Act as a crypto analyst. Write an article based on:
    "{news_item['title']}" - {news_item['summary']}
    
    Structure:
    1. HTML Box: <div style="background-color: #f0f4f8; border-left: 5px solid #3498db; padding: 15px;"><h4>🔥 Key Takeaways</h4><ul><li>...</li></ul></div>
    2. Body: Use <h2> and <p>.
    3. Footer: META_DESC: ... TAGS: ... CATEGORY: [Select one: News, Market Analysis, DeFi, Stablecoins, DAOs, Education]
    """
    
    # محاولة 5 مرات بدلاً من 3 لزيادة فرص النجاح
    for i in range(5):
        model = random.choice(FREE_TEXT_MODELS)
        try:
            print(f"   🤖 Using Model: {model}")
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], temperature=0.8
            )
            content = response.choices[0].message.content.replace("```html", "").replace("```", "").strip()
            return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', content)
        except Exception as e:
            print(f"   ⚠️ Text Model ({model}) Failed: {e}. Retrying in 5s...")
            time.sleep(5) # انتظار أطول لتهدئة السيرفر
            
    return None

# ==========================================
# 6. الرفع والنشر
# ==========================================
def get_auth_header():
    clean_pass = WP_APP_PASS.replace(' ', '')
    creds = base64.b64encode(f"{WP_USER}:{clean_pass}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

def get_or_create_tag_id(tag_name):
    try:
        h = get_auth_header()
        r = requests.get(f"{WP_ENDPOINT}/tags?search={tag_name}", headers=h)
        if r.status_code == 200 and r.json(): return r.json()[0]['id']
        r = requests.post(f"{WP_ENDPOINT}/tags", headers=h, json={"name": tag_name})
        if r.status_code == 201: return r.json()['id']
    except: pass
    return None

def upload_image_with_seo(img_url, alt_text):
    print(f"   ⬆️ Uploading: {alt_text[:20]}...")
    try:
        r_img = requests.get(img_url, headers=BROWSER_HEADERS, timeout=30, verify=False)
        if r_img.status_code == 200:
            final_image_data = apply_watermark(r_img.content)
            filename = f"img_{int(time.time())}.jpg"
            headers_wp = get_auth_header()
            headers_wp["Content-Disposition"] = f"attachment; filename={filename}"
            headers_wp["Content-Type"] = "image/jpeg"
            r_wp = requests.post(f"{WP_ENDPOINT}/media", headers=headers_wp, data=final_image_data)
            if r_wp.status_code == 201: 
                media_id = r_wp.json()['id']
                seo_data = {"alt_text": alt_text, "title": alt_text, "caption": f"Source: CryptoEpochs - {alt_text}", "description": alt_text}
                requests.post(f"{WP_ENDPOINT}/media/{media_id}", headers=get_auth_header(), json=seo_data)
                print("   ✅ Image Uploaded.")
                return media_id
    except: pass
    return None

def publish_to_wp(title, content, feat_img_id, is_generated_image=False):
    meta_desc, tags, cat_id = "", [], DEFAULT_CATEGORY_ID
    if "META_DESC:" in content:
        try:
            parts = content.split("META_DESC:")
            content = parts[0]
            rest = parts[1]
            if "TAGS:" in rest:
                t_parts = rest.split("TAGS:")
                meta_desc = t_parts[0].strip()
                rest = t_parts[1]
                if "CATEGORY:" in rest:
                    c_parts = rest.split("CATEGORY:")
                    tags = [t.strip() for t in c_parts[0].split(',')]
                    
                    found_cat = False
                    for k, v in CATEGORY_MAP.items():
                        if k.lower() in c_parts[1].lower(): 
                            cat_id = v
                            found_cat = True
                            break
                    if not found_cat:
                        cat_id = DEFAULT_CATEGORY_ID

        except: pass

    if is_generated_image: tags.append("AI Art")
    tag_ids = [get_or_create_tag_id(t) for t in tags if t]
    
    focus_keyword = tags[0] if tags else "Crypto News"
    
    data = {
        "title": title, "content": content, "status": "publish",
        "categories": [cat_id], "tags": tag_ids, "excerpt": meta_desc,
        "featured_media": feat_img_id,
        "rank_math_focus_keyword": focus_keyword,
        "rank_math_description": meta_desc
    }
    
    data["meta"] = { 
        "rank_math_focus_keyword": focus_keyword,
        "rank_math_description": meta_desc
    }
    
    r = requests.post(f"{WP_ENDPOINT}/posts", headers=get_auth_header(), json=data)
    if r.status_code == 201: return r.json()['link']
    print(f"   ❌ Publish Failed: {r.status_code} - {r.text[:100]}")
    return None

def extract_image(entry):
    if hasattr(entry, 'media_content') and entry.media_content:
        return entry.media_content[0].get('url') if isinstance(entry.media_content[0], dict) else entry.media_content[0]['url']
    if hasattr(entry, 'links') and entry.links:
        for l in entry.links:
            link_type = getattr(l, 'type', '') or ''
            if 'image' in link_type: return getattr(l, 'href', None)
    if hasattr(entry, 'summary') and entry.summary:
        m = re.search(r'<img.*?src=["\']([^"\']+)["\']', entry.summary)
        if m: return m.group(1)
    if hasattr(entry, 'content') and entry.content:
        for c in entry.content:
            content_value = getattr(c, 'value', '') or ''
            m = re.search(r'<img.*?src=["\']([^"\']+)["\']', content_value)
            if m: return m.group(1)
    return None

# ==========================================
# 7. المحرك الرئيسي
# ==========================================
def main():
    print("🚀 CryptoEpochs V31 (Stable Models) Started...")
    print(f"   👤 User: {WP_USER}")
    init_db()
    feeds = [
        "https://cointelegraph.com/rss", "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/", "https://bitcoinmagazine.com/.rss/full/",
        "https://blockworks.co/feed/", "https://u.today/rss",
        "https://cryptonews.com/news/feed/", "https://beincrypto.com/feed/",
        "https://dailyhodl.com/feed/", "https://zycrypto.com/feed/"
    ]
    while True:
        print(f"\n⏰ Cycle: {datetime.now().strftime('%H:%M')}")
        for feed in feeds:
            try:
                d = feedparser.parse(feed)
                for entry in d.entries[:2]:
                    if is_published_link(entry.link): continue
                    if is_duplicate_semantic(entry.title): continue
                    print(f"   ⚡ Processing: {entry.title}")
                    
                    original_img = extract_image(entry)
                    final_img_url, is_generated = None, False
                    
                    if original_img:
                        if check_image_safety(original_img):
                            print("   ✅ Original OK.")
                            final_img_url = original_img
                        else:
                            print("   ⚠️ Watermark. Smart Gen...")
                            final_img_url = get_smart_image_url(entry.title)
                            is_generated = True
                    else:
                        print("   🎨 Generating Image...")
                        final_img_url = get_smart_image_url(entry.title)
                        is_generated = True
                    
                    fid = upload_image_with_seo(final_img_url, entry.title)
                    if not fid and is_generated:
                        print("   🚨 Fallback (Rotation)...")
                        for f_url in get_emergency_image_list(entry.title):
                            fid = upload_image_with_seo(f_url, entry.title)
                            if fid: break
                    
                    if fid:
                        content = generate_content({'title': entry.title, 'summary': getattr(entry, 'summary', '')})
                        if content:
                            link = publish_to_wp(entry.title, content, fid, is_generated)
                            if link:
                                print(f"   ✅ Published: {link}")
                                mark_published(entry.link, entry.title)
                    time.sleep(15)
            except Exception as e: print(f"   ⚠️ Loop Error: {e}")
        print("💤 Resting 15 min...")
        time.sleep(900)

if __name__ == "__main__":
    main()
