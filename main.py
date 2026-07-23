from flask import Flask, request, jsonify
import requests
import os
import re
import json
import sqlite3
import hashlib
import base64
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import quote
from threading import Lock

app = Flask(__name__)

OXYLABS_USER = os.environ.get('OXYLABS_USER', '')
OXYLABS_PASS = os.environ.get('OXYLABS_PASS', '')
OXYLABS_API_URL = "https://realtime.oxylabs.io/v1/queries"

DB_PATH = os.environ.get('DB_PATH', '/tmp/temu_cache.db')
CACHE_DAYS = int(os.environ.get('CACHE_DAYS', 7))

db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT UNIQUE, query TEXT, results TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS product_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash TEXT UNIQUE, url TEXT, product_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
        """)
        conn.commit()
        conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_text(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def is_fresh(created_at_str):
    try:
        created = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
        return datetime.now() - created < timedelta(days=CACHE_DAYS)
    except:
        return False

def get_cached_search(query):
    query_hash = hash_text(query.lower().strip())
    conn = get_db()
    row = conn.execute("SELECT * FROM search_cache WHERE query_hash = ?", (query_hash,)).fetchone()
    conn.close()
    if row and is_fresh(row['created_at']):
        return json.loads(row['results'])
    return None

def save_search_cache(query, results):
    query_hash = hash_text(query.lower().strip())
    conn = get_db()
    conn.execute("""
        INSERT INTO search_cache (query_hash, query, results)
        VALUES (?, ?, ?) ON CONFLICT(query_hash) DO UPDATE SET
        results=excluded.results, created_at=CURRENT_TIMESTAMP
    """, (query_hash, query, json.dumps(results)))
    conn.commit()
    conn.close()

def get_cached_product(url):
    url_hash = hash_text(url)
    conn = get_db()
    row = conn.execute("SELECT * FROM product_cache WHERE url_hash = ?", (url_hash,)).fetchone()
    conn.close()
    if row and is_fresh(row['created_at']):
        return json.loads(row['product_data'])
    return None

def save_product_cache(url, data):
    url_hash = hash_text(url)
    conn = get_db()
    conn.execute("""
        INSERT INTO product_cache (url_hash, url, product_data)
        VALUES (?, ?, ?) ON CONFLICT(url_hash) DO UPDATE SET
        product_data=excluded.product_data, created_at=CURRENT_TIMESTAMP
    """, (url_hash, url, json.dumps(data)))
    conn.commit()
    conn.close()

def fetch_oxylabs(target_url, timeout=60):
    """Try Oxylabs API first."""
    if not OXYLABS_USER or not OXYLABS_PASS:
        return None, "Oxylabs credentials not configured"

    payload = {
        "url": target_url,
        "source": "universal",
        "render": "html",
        "geo_location": "United States",
    }

    try:
        resp = requests.post(
            OXYLABS_API_URL,
            auth=(OXYLABS_USER, OXYLABS_PASS),
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results and len(results) > 0:
                content = results[0].get("content", "")
                if content:
                    return content, None
            if "content" in data:
                return data["content"], None
            return None, "Oxylabs returned empty content"
        else:
            try:
                err = resp.json()
                return None, f"HTTP {resp.status_code}: {json.dumps(err)[:300]}"
            except:
                return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return None, str(e)[:300]

def fetch_direct(target_url, timeout=30):
    """Try direct fetch without proxy."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        resp = requests.get(target_url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text, None
    except Exception as e:
        return None, str(e)[:300]

def get_mock_search_results(query):
    """Return mock data for testing when all fetch methods fail."""
    return [
        {
            "product_id": "601099518075471",
            "title": f"Mock Product 1 for {query}",
            "price": "$5.99",
            "original_price": "$15.99",
            "rating": 4.7,
            "sold_count": "5K+ sold",
            "discount_percent": 62,
            "image": "https://img.kwcdn.com/product/open/2023-09-08/1694161234567-1234567890.jpg",
            "product_url": "https://www.temu.com/goods.html?goods_id=601099518075471"
        },
        {
            "product_id": "601099518075472",
            "title": f"Mock Product 2 for {query}",
            "price": "$3.49",
            "original_price": "$12.99",
            "rating": 4.5,
            "sold_count": "10K+ sold",
            "discount_percent": 73,
            "image": "https://img.kwcdn.com/product/open/2023-09-08/1694161234568-1234567891.jpg",
            "product_url": "https://www.temu.com/goods.html?goods_id=601099518075472"
        },
        {
            "product_id": "601099518075473",
            "title": f"Mock Product 3 for {query}",
            "price": "$8.99",
            "original_price": "$25.99",
            "rating": 4.8,
            "sold_count": "2K+ sold",
            "discount_percent": 65,
            "image": "https://img.kwcdn.com/product/open/2023-09-08/1694161234569-1234567892.jpg",
            "product_url": "https://www.temu.com/goods.html?goods_id=601099518075473"
        }
    ]

def get_mock_product_detail(product_url):
    """Return mock product details for testing."""
    return {
        "product_url": product_url,
        "title": "Mock Wireless Earbuds Bluetooth 5.3",
        "price": "$4.73",
        "original_price": "$20.20",
        "currency": "USD",
        "rating": 4.8,
        "review_count": 1677,
        "sold_count": "10K+ sold",
        "description": "High quality wireless earbuds with noise cancellation and long battery life.",
        "images": [
            "https://img.kwcdn.com/product/open/2023-09-08/1694161234567-1234567890.jpg",
            "https://img.kwcdn.com/product/open/2023-09-08/1694161234568-1234567891.jpg",
            "https://img.kwcdn.com/product/open/2023-09-08/1694161234569-1234567892.jpg"
        ],
        "colors": ["Black", "White", "Blue", "Pink"],
        "sizes": ["One Size"],
        "specs": {
            "Material": "Plastic",
            "Weight": "50g",
            "Battery": "30 hours",
            "Bluetooth": "5.3"
        },
        "store_info": {"name": "Mock Store"},
        "variants": [
            {"sku_id": "123", "color": "Black", "size": "One Size", "price": "$4.73", "available": True},
            {"sku_id": "124", "color": "White", "size": "One Size", "price": "$4.73", "available": True},
            {"sku_id": "125", "color": "Blue", "size": "One Size", "price": "$4.99", "available": True}
        ]
    }

def parse_search_results(html):
    soup = BeautifulSoup(html, 'lxml')
    products = []
    cards = (
        soup.select('div[data-testid="product-card"]') or
        soup.select('div[class*="goods-item"]') or
        soup.select('div[class*="product-card"]') or
        soup.select('a[href*="goods.html"]') or
        soup.find_all('div', class_=re.compile(r'.*goods.*'))
    )
    for card in cards[:24]:
        product = {}
        link_tag = card if card.name == 'a' else card.find('a', href=re.compile(r'goods_id'))
        if link_tag and link_tag.get('href'):
            href = link_tag['href']
            if href.startswith('/'):
                href = 'https://www.temu.com' + href
            product['product_url'] = href
            match = re.search(r'goods_id[=:](\d+)', href)
            if match:
                product['product_id'] = match.group(1)
        title_tag = (card.select_one('[class*="title"]') or card.select_one('h2') or 
                     card.select_one('h3') or card.select_one('span[class*="title"]'))
        if title_tag:
            product['title'] = title_tag.get_text(strip=True)
        price_tag = (card.select_one('[class*="price"]') or card.select_one('span[class*="_2de9"]') or
                     card.find(text=re.compile(r'\$\d+')))
        if price_tag:
            text = price_tag.get_text(strip=True) if hasattr(price_tag, 'get_text') else price_tag.strip()
            product['price'] = text
        orig_tag = card.select_one('[class*="original"]') or card.select_one('[class*="market"]')
        if orig_tag:
            product['original_price'] = orig_tag.get_text(strip=True)
        rating_tag = card.select_one('[class*="rating"]') or card.find(text=re.compile(r'\d\.\d'))
        if rating_tag:
            text = rating_tag.get_text(strip=True) if hasattr(rating_tag, 'get_text') else rating_tag
            match = re.search(r'(\d\.\d)', text)
            if match:
                product['rating'] = float(match.group(1))
        sold_tag = card.select_one('[class*="sold"]') or card.find(text=re.compile(r'\d+[KkMm]?\+?\s*sold'))
        if sold_tag:
            text = sold_tag.get_text(strip=True) if hasattr(sold_tag, 'get_text') else sold_tag
            product['sold_count'] = text.strip()
        img_tag = card.select_one('img[src*="kwcdn.com"]') or card.select_one('img[data-src*="kwcdn.com"]')
        if img_tag:
            product['image'] = img_tag.get('src') or img_tag.get('data-src')
        discount_tag = card.select_one('[class*="discount"]') or card.find(text=re.compile(r'-?\d+%'))
        if discount_tag:
            text = discount_tag.get_text(strip=True) if hasattr(discount_tag, 'get_text') else discount_tag
            match = re.search(r'(\d+)%', text)
            if match:
                product['discount_percent'] = int(match.group(1))
        if product.get('title') or product.get('product_id'):
            products.append(product)
    return products

def parse_product_detail(html, product_url):
    soup = BeautifulSoup(html, 'lxml')
    data = {
        'product_url': product_url, 'title': None, 'price': None, 'original_price': None,
        'currency': None, 'rating': None, 'review_count': None, 'sold_count': None,
        'description': None, 'images': [], 'colors': [], 'sizes': [],
        'specs': {}, 'store_info': {}, 'variants': [],
    }
    for sel in ['h1[data-testid="product-title"]', 'h1[class*="title"]', 'h1', 
                'div[class*="product-name"] h1', 'span[class*="product-title"]']:
        tag = soup.select_one(sel)
        if tag:
            data['title'] = tag.get_text(strip=True)
            break
    for sel in ['span[class*="price"]', 'div[class*="price"]', 'span[class*="_2de9"]', '[class*="current-price"]']:
        tag = soup.select_one(sel)
        if tag:
            data['price'] = tag.get_text(strip=True)
            break
    orig_tag = soup.select_one('[class*="original-price"]') or soup.select_one('[class*="market-price"]')
    if orig_tag:
        data['original_price'] = orig_tag.get_text(strip=True)
    rating_tag = soup.find(text=re.compile(r'\d\.\d'))
    if rating_tag:
        parent = rating_tag.parent
        if parent:
            match = re.search(r'(\d\.\d)', parent.get_text())
            if match:
                data['rating'] = float(match.group(1))
    review_tag = soup.find(text=re.compile(r'\d+\s*reviews?', re.I))
    if review_tag:
        match = re.search(r'(\d+)', review_tag)
        if match:
            data['review_count'] = int(match.group(1))
    sold_tag = soup.find(text=re.compile(r'\d+[KkMm]?\+?\s*sold', re.I))
    if sold_tag:
        data['sold_count'] = sold_tag.strip()
    img_tags = soup.select('img[src*="kwcdn.com"]') + soup.select('img[data-src*="kwcdn.com"]')
    seen = set()
    for img in img_tags:
        src = img.get('src') or img.get('data-src')
        if src and src not in seen and 'thumbnail' not in src.lower():
            seen.add(src)
            data['images'].append(src)
    for tag in soup.select('[class*="color"]') + soup.select('[class*="colour"]'):
        text = tag.get_text(strip=True)
        if text and len(text) < 50 and text not in data['colors']:
            data['colors'].append(text)
    for swatch in soup.select('[class*="swatch"]') + soup.select('[class*="sku-item"]'):
        text = swatch.get_text(strip=True)
        if text and text not in data['colors'] and len(text) < 50:
            data['colors'].append(text)
    for tag in soup.select('[class*="size"]') + soup.select('[class*="dimension"]'):
        text = tag.get_text(strip=True)
        if text and text not in data['sizes'] and len(text) < 30:
            data['sizes'].append(text)
    desc_tag = soup.select_one('[class*="description"]') or soup.select_one('[class*="detail"]')
    if desc_tag:
        data['description'] = desc_tag.get_text(strip=True)[:500]
    for row in soup.select('[class*="spec"]') + soup.select('table tr'):
        cells = row.select('td, th, div')
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key and val and len(key) < 50:
                data['specs'][key] = val
    store_tag = soup.select_one('[class*="store"]') or soup.select_one('[class*="mall"]')
    if store_tag:
        data['store_info']['name'] = store_tag.get_text(strip=True)
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            ld = json.loads(script.string)
            if isinstance(ld, dict) and ld.get('@type') == 'Product':
                data['title'] = data['title'] or ld.get('name')
                if ld.get('offers'):
                    offers = ld['offers'][0] if isinstance(ld['offers'], list) else ld['offers']
                    data['price'] = data['price'] or offers.get('price')
                    data['currency'] = data['currency'] or offers.get('priceCurrency')
                data['description'] = data['description'] or ld.get('description', '')[:500]
                if ld.get('image'):
                    imgs = [ld['image']] if isinstance(ld['image'], str) else ld['image']
                    for img in imgs:
                        if img not in data['images']:
                            data['images'].append(img)
        except:
            pass
    for script in soup.find_all('script'):
        if script.string and ('initialState' in script.string or 'goodsInfo' in script.string):
            try:
                match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', script.string, re.DOTALL)
                if match:
                    state = json.loads(match.group(1))
                    if 'goodsInfo' in state:
                        goods = state['goodsInfo']
                        data['title'] = data['title'] or goods.get('goodsName')
                        data['description'] = data['description'] or goods.get('goodsDesc', '')[:500]
                        if 'skuList' in goods:
                            for sku in goods['skuList']:
                                variant = {
                                    'sku_id': sku.get('skuId'), 'price': sku.get('price'),
                                    'original_price': sku.get('marketPrice'), 'available': sku.get('isOnsale'),
                                }
                                for spec in sku.get('specs', []):
                                    spec_name = spec.get('specName', '').lower()
                                    spec_value = spec.get('specValue', '')
                                    if 'color' in spec_name or 'colour' in spec_name:
                                        variant['color'] = spec_value
                                        if spec_value not in data['colors']:
                                            data['colors'].append(spec_value)
                                    elif 'size' in spec_name or 'dimension' in spec_name:
                                        variant['size'] = spec_value
                                        if spec_value not in data['sizes']:
                                            data['sizes'].append(spec_value)
                                data['variants'].append(variant)
            except:
                pass
    data['colors'] = list(dict.fromkeys(data['colors']))[:20]
    data['sizes'] = list(dict.fromkeys(data['sizes']))[:20]
    data['images'] = data['images'][:15]
    return data

@app.route('/')
def home():
    return jsonify({
        "service": "Temu Scraper API with Cache",
        "powered_by": "Oxylabs Web Scraper API + Fallback",
        "database": "SQLite (cached for " + str(CACHE_DAYS) + " days)",
        "endpoints": {
            "GET /search?q=<keyword>&limit=<n>": "Search products (cached)",
            "GET /product?url=<temu_url>": "Get product details (cached)",
            "POST /product": {"body": {"url": "temu product url"}},
            "GET /stats": "View cache statistics",
            "GET /health": "Check API health"
        }
    })

@app.route('/search', methods=['GET'])
def search_products():
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 12)), 24)
    use_mock = request.args.get('mock', 'false').lower() == 'true'

    if not query:
        return jsonify({"error": "Missing 'q' parameter"}), 400

    # 1. Check cache
    cached = get_cached_search(query)
    if cached:
        products = cached[:limit]
        return jsonify({"success": True, "source": "cache", "query": query, "count": len(products), "products": products})

    # 2. Try Oxylabs
    search_url = f"https://www.temu.com/search_result.html?search_key={quote(query)}"
    html, oxylabs_error = fetch_oxylabs(search_url, timeout=60)

    if html:
        products = parse_search_results(html)[:limit]
        if products:
            save_search_cache(query, products)
            return jsonify({"success": True, "source": "oxylabs", "query": query, "count": len(products), "products": products})

    # 3. Try direct scraping
    html, direct_error = fetch_direct(search_url, timeout=30)

    if html:
        products = parse_search_results(html)[:limit]
        if products:
            save_search_cache(query, products)
            return jsonify({"success": True, "source": "direct", "query": query, "count": len(products), "products": products})

    # 4. Fallback to mock data
    if use_mock:
        products = get_mock_search_results(query)[:limit]
        return jsonify({
            "success": True,
            "source": "mock",
            "query": query,
            "count": len(products),
            "products": products,
            "oxylabs_error": oxylabs_error,
            "direct_error": direct_error
        })

    return jsonify({
        "error": "All fetch methods failed",
        "oxylabs_error": oxylabs_error,
        "direct_error": direct_error,
        "note": "Add ?mock=true to get test data"
    }), 502

@app.route('/product', methods=['GET', 'POST'])
def product_detail():
    if request.method == 'POST':
        body = request.get_json() or {}
        product_url = body.get('url', '').strip()
    else:
        product_url = request.args.get('url', '').strip()
    use_mock = request.args.get('mock', 'false').lower() == 'true'

    if not product_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400
    if not product_url.startswith('http'):
        product_url = 'https://www.temu.com' + product_url
    if 'temu.com' not in product_url:
        return jsonify({"error": "Invalid Temu URL"}), 400

    # 1. Check cache
    cached = get_cached_product(product_url)
    if cached:
        return jsonify({"success": True, "source": "cache", "product": cached})

    # 2. Try Oxylabs
    html, oxylabs_error = fetch_oxylabs(product_url, timeout=90)

    if html:
        data = parse_product_detail(html, product_url)
        save_product_cache(product_url, data)
        return jsonify({"success": True, "source": "oxylabs", "product": data})

    # 3. Try direct scraping
    html, direct_error = fetch_direct(product_url, timeout=30)

    if html:
        data = parse_product_detail(html, product_url)
        save_product_cache(product_url, data)
        return jsonify({"success": True, "source": "direct", "product": data})

    # 4. Fallback to mock data
    if use_mock:
        data = get_mock_product_detail(product_url)
        return jsonify({
            "success": True,
            "source": "mock",
            "product": data,
            "oxylabs_error": oxylabs_error,
            "direct_error": direct_error
        })

    return jsonify({
        "error": "All fetch methods failed",
        "oxylabs_error": oxylabs_error,
        "direct_error": direct_error,
        "note": "Add ?mock=true to get test data"
    }), 502

@app.route('/stats')
def stats():
    conn = get_db()
    search_count = conn.execute("SELECT COUNT(*) as c FROM search_cache").fetchone()['c']
    product_count = conn.execute("SELECT COUNT(*) as c FROM product_cache").fetchone()['c']
    conn.close()
    return jsonify({
        "cached_searches": search_count,
        "cached_products": product_count,
        "cache_duration_days": CACHE_DAYS,
        "oxylabs_configured": bool(OXYLABS_USER and OXYLABS_PASS),
        "oxylabs_user_prefix": OXYLABS_USER[:5] + "..." if OXYLABS_USER else None
    })

@app.route('/health')
def health():
    return jsonify({"status": "ok", "oxylabs_configured": bool(OXYLABS_USER and OXYLABS_PASS)})

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
