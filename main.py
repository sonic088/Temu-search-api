from flask import Flask, request, jsonify
import requests
import os
import re
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import quote
from threading import Lock

app = Flask(__name__)

# ===================== CONFIG =====================
SCRAPINGBEE_API_KEY = os.environ.get('SCRAPINGBEE_API_KEY', '')
OXYLABS_USER = os.environ.get('OXYLABS_USER', '')
OXYLABS_PASS = os.environ.get('OXYLABS_PASS', '')
OXYLABS_API_URL = "https://realtime.oxylabs.io/v1/queries"
DB_PATH = os.environ.get('DB_PATH', '/tmp/temu_cache.db')
CACHE_DAYS = int(os.environ.get('CACHE_DAYS', 7))

db_lock = Lock()

# ===================== DATABASE =====================
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

# ===================== FETCHERS =====================

def fetch_scrapingbee(target_url, timeout=60):
    if not SCRAPINGBEE_API_KEY:
        return None, "ScrapingBee API key not configured"

    api_url = "https://app.scrapingbee.com/api/v1/"
    params = {
        "api_key": SCRAPINGBEE_API_KEY,
        "url": target_url,
        "render_js": "true",
        "premium_proxy": "true",
        "country_code": "us",
        "wait": "8000",
    }

    try:
        resp = requests.get(api_url, params=params, timeout=timeout)
        if resp.status_code == 200:
            html = resp.text
            if len(html) < 1000:
                return None, f"ScrapingBee returned too short HTML ({len(html)} chars)"
            return html, None
        else:
            return None, f"ScrapingBee HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return None, f"ScrapingBee error: {str(e)[:300]}"

def fetch_oxylabs(target_url, timeout=60):
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
                return None, f"Oxylabs HTTP {resp.status_code}: {json.dumps(err)[:300]}"
            except:
                return None, f"Oxylabs HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return None, f"Oxylabs error: {str(e)[:300]}"

def fetch_direct(target_url, timeout=30):
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

# ===================== MOCK DATA =====================

def get_mock_search_results(query):
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

# ===================== ADVANCED JSON EXTRACTORS =====================

def extract_all_window_vars(html):
    """Extract ALL window.* variables from script tags."""
    soup = BeautifulSoup(html, 'html.parser')
    all_vars = {}

    for script in soup.find_all('script'):
        if not script.string:
            continue
        text = script.string

        # Find all window.VAR_NAME = {...} or window.VAR_NAME = [...]
        # Pattern: window.XXX = { ... }; or window.XXX = [ ... ];
        pattern = r'window\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*({.*?};|\[.*?\];)'
        # Better pattern that handles nested braces
        matches = re.finditer(r'window\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*', text)

        for match in matches:
            var_name = match.group(1)
            start = match.end()

            # Find the matching end of the object/array
            first_char = text[start:start+1].strip()
            if first_char not in ['{', '[']:
                continue

            opener = first_char
            closer = '}' if opener == '{' else ']'

            depth = 0
            in_string = False
            string_char = None
            escape_next = False

            for i in range(start, len(text)):
                char = text[i]

                if escape_next:
                    escape_next = False
                    continue

                if char == '\\':
                    escape_next = True
                    continue

                if char in ['"', "'"]:
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char:
                        in_string = False
                        string_char = None
                    continue

                if not in_string:
                    if char == opener:
                        depth += 1
                    elif char == closer:
                        depth -= 1
                        if depth == 0:
                            # Found complete object
                            json_str = text[start:i+1]
                            try:
                                data = json.loads(json_str)
                                all_vars[var_name] = data
                            except:
                                pass
                            break

    return all_vars

def find_products_in_data(data, depth=0, max_depth=10):
    """Recursively find product arrays/objects in any JSON structure."""
    if depth > max_depth:
        return []

    products = []

    if isinstance(data, dict):
        # Check if this dict itself is a product
        product_keys = ['goodsId', 'goods_id', 'productId', 'itemId', 'skuId', 'goodsName', 'title', 'productName']
        if any(k in data for k in product_keys):
            # It might be a product if it has a name/title
            if any(k in data for k in ['goodsName', 'title', 'productName', 'itemName', 'name']):
                products.append(data)

        # Search in values
        for key, value in data.items():
            # Check if key suggests products
            if isinstance(value, list) and len(value) > 0:
                if key.lower() in ['goods', 'products', 'items', 'skus', 'list', 'data', 'result', 'results', 'goodslist', 'productlist', 'itemlist']:
                    # Check first item
                    if isinstance(value[0], dict):
                        if any(k in value[0] for k in product_keys + ['goodsName', 'title', 'name']):
                            products.extend(value)
                            continue
                # Recursively search list items
                for item in value:
                    products.extend(find_products_in_data(item, depth + 1, max_depth))
            elif isinstance(value, dict):
                products.extend(find_products_in_data(value, depth + 1, max_depth))

    elif isinstance(data, list):
        for item in data:
            products.extend(find_products_in_data(item, depth + 1, max_depth))

    return products

def normalize_product(raw_product):
    """Convert raw Temu product data to standard format."""
    product = {}

    # Product ID
    product['product_id'] = str(raw_product.get('goodsId') or 
                              raw_product.get('goods_id') or 
                              raw_product.get('productId') or 
                              raw_product.get('itemId') or 
                              raw_product.get('id') or '')

    # Title
    product['title'] = (raw_product.get('goodsName') or 
                        raw_product.get('title') or 
                        raw_product.get('productName') or 
                        raw_product.get('itemName') or 
                        raw_product.get('name') or '')

    # Price - try multiple fields
    price = (raw_product.get('price') or 
             raw_product.get('salePrice') or 
             raw_product.get('minOnSalePrice') or 
             raw_product.get('minPrice') or 
             raw_product.get('sale_price') or '')
    if price:
        price_str = str(price)
        if not price_str.startswith('$'):
            price_str = f"${price_str}"
        product['price'] = price_str

    # Original price
    orig_price = (raw_product.get('marketPrice') or 
                  raw_product.get('originalPrice') or 
                  raw_product.get('maxPrice') or 
                  raw_product.get('original_price') or '')
    if orig_price:
        orig_str = str(orig_price)
        if not orig_str.startswith('$'):
            orig_str = f"${orig_str}"
        product['original_price'] = orig_str

    # Rating
    rating = (raw_product.get('goodsStar') or 
              raw_product.get('rating') or 
              raw_product.get('avgStar') or 
              raw_product.get('star') or '')
    if rating:
        try:
            product['rating'] = float(rating)
        except:
            pass

    # Sold count
    sold = (raw_product.get('sales') or 
            raw_product.get('soldQuantity') or 
            raw_product.get('soldCount') or 
            raw_product.get('sold') or '')
    if sold:
        product['sold_count'] = str(sold)

    # Image
    img = (raw_product.get('thumbUrl') or 
           raw_product.get('imageUrl') or 
           raw_product.get('mainImage') or 
           raw_product.get('thumb') or 
           raw_product.get('image') or 
           raw_product.get('imgUrl') or '')
    if img:
        if img.startswith('//'):
            img = 'https:' + img
        product['image'] = img

    # Product URL
    goods_id = product.get('product_id')
    if goods_id:
        product['product_url'] = f"https://www.temu.com/goods.html?goods_id={goods_id}"
    elif raw_product.get('link'):
        product['product_url'] = raw_product.get('link')
    elif raw_product.get('url'):
        product['product_url'] = raw_product.get('url')

    # Discount
    if product.get('price') and product.get('original_price'):
        try:
            p = float(str(product['price']).replace('$', '').replace(',', ''))
            o = float(str(product['original_price']).replace('$', '').replace(',', ''))
            if o > p:
                product['discount_percent'] = int((1 - p/o) * 100)
        except:
            pass

    # Also check for explicit discount
    discount = raw_product.get('discount') or raw_product.get('discountRate')
    if discount and not product.get('discount_percent'):
        try:
            product['discount_percent'] = int(float(discount))
        except:
            pass

    return product

# ===================== PARSERS =====================

def parse_search_results(html):
    """Extract products from HTML using all possible methods."""
    products = []
    debug_info = {"methods_tried": []}

    # Method 1: Extract ALL window variables and search for products
    try:
        window_vars = extract_all_window_vars(html)
        debug_info["window_vars_found"] = list(window_vars.keys())
        debug_info["methods_tried"].append("window_vars")

        for var_name, data in window_vars.items():
            # Skip obvious config variables
            if any(skip in var_name.lower() for skip in ['config', 'cdn', 'img', 'npm', 'package', 'locale', 'i18n']):
                continue

            found = find_products_in_data(data)
            if found:
                debug_info[f"products_from_{var_name}"] = len(found)
                for raw in found:
                    product = normalize_product(raw)
                    if product.get('title') or product.get('product_id'):
                        products.append(product)
    except Exception as e:
        debug_info["window_vars_error"] = str(e)

    # Method 2: Look for JSON-LD
    if not products:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    ld = json.loads(script.string)
                    if isinstance(ld, dict) and ld.get('@type') == 'ItemList':
                        for item in ld.get('itemListElement', []):
                            if isinstance(item, dict):
                                p = item.get('item', {})
                                if p:
                                    products.append(normalize_product(p))
                except:
                    pass
            debug_info["methods_tried"].append("json_ld")
        except:
            pass

    # Method 3: DOM fallback
    if not products:
        try:
            soup = BeautifulSoup(html, 'html.parser')
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

                img_tag = card.select_one('img[src*="kwcdn.com"]') or card.select_one('img[data-src*="kwcdn.com"]')
                if img_tag:
                    product['image'] = img_tag.get('src') or img_tag.get('data-src')

                if product.get('title') or product.get('product_id'):
                    products.append(product)
            debug_info["methods_tried"].append("dom")
            debug_info["dom_cards_found"] = len(cards)
        except Exception as e:
            debug_info["dom_error"] = str(e)

    # Remove duplicates
    seen = set()
    unique = []
    for p in products:
        pid = p.get('product_id') or p.get('title', '')
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(p)

    debug_info["total_unique_products"] = len(unique)
    return unique, debug_info

def parse_product_detail(html, product_url):
    soup = BeautifulSoup(html, 'html.parser')
    data = {
        'product_url': product_url, 'title': None, 'price': None, 'original_price': None,
        'currency': None, 'rating': None, 'review_count': None, 'sold_count': None,
        'description': None, 'images': [], 'colors': [], 'sizes': [],
        'specs': {}, 'store_info': {}, 'variants': [],
    }

    # Try window variables first
    try:
        window_vars = extract_all_window_vars(html)
        for var_name, var_data in window_vars.items():
            if any(skip in var_name.lower() for skip in ['config', 'cdn', 'npm', 'package', 'locale']):
                continue

            # Look for goods info
            goods = None
            if isinstance(var_data, dict):
                if 'goodsInfo' in var_data:
                    goods = var_data['goodsInfo']
                elif 'goods' in var_data and isinstance(var_data['goods'], dict):
                    goods = var_data['goods']
                elif 'product' in var_data and isinstance(var_data['product'], dict):
                    goods = var_data['product']

            if goods and isinstance(goods, dict):
                data['title'] = data['title'] or goods.get('goodsName') or goods.get('title') or goods.get('name')
                data['description'] = data['description'] or goods.get('goodsDesc', '')[:500] or goods.get('description', '')[:500]

                price = (goods.get('price') or goods.get('salePrice') or goods.get('minOnSalePrice') or goods.get('sale_price'))
                if price and not data['price']:
                    data['price'] = f"${price}" if not str(price).startswith('$') else str(price)

                orig = goods.get('marketPrice') or goods.get('originalPrice') or goods.get('original_price')
                if orig and not data['original_price']:
                    data['original_price'] = f"${orig}" if not str(orig).startswith('$') else str(orig)

                rating = goods.get('goodsStar') or goods.get('avgStar') or goods.get('rating') or goods.get('star')
                if rating and not data['rating']:
                    try:
                        data['rating'] = float(rating)
                    except:
                        pass

                sold = goods.get('sales') or goods.get('soldQuantity') or goods.get('soldCount') or goods.get('sold')
                if sold and not data['sold_count']:
                    data['sold_count'] = str(sold)

                # Images
                imgs = (goods.get('thumbUrlList') or goods.get('imageUrlList') or goods.get('images') or 
                        goods.get('imgUrlList') or goods.get('gallery') or [])
                if isinstance(imgs, list):
                    for img in imgs:
                        if img and img not in data['images']:
                            if img.startswith('//'):
                                img = 'https:' + img
                            data['images'].append(img)

                # Variants/SKUs
                sku_list = goods.get('skuList') or goods.get('skus') or goods.get('sku_list') or []
                for sku in sku_list:
                    variant = {
                        'sku_id': sku.get('skuId') or sku.get('id') or sku.get('sku_id'),
                        'price': sku.get('price') or sku.get('salePrice') or sku.get('sale_price'),
                        'original_price': sku.get('marketPrice') or sku.get('originalPrice'),
                        'available': sku.get('isOnsale') or sku.get('inStock') or sku.get('available'),
                    }
                    specs = sku.get('specs') or sku.get('specifications') or sku.get('skuSpecs') or []
                    for spec in specs:
                        name = (spec.get('specName') or spec.get('name') or '').lower()
                        value = spec.get('specValue') or spec.get('value') or spec.get('spec_value') or ''
                        if 'color' in name or 'colour' in name:
                            variant['color'] = value
                            if value not in data['colors']:
                                data['colors'].append(value)
                        elif 'size' in name or 'dimension' in name:
                            variant['size'] = value
                            if value not in data['sizes']:
                                data['sizes'].append(value)
                    data['variants'].append(variant)
    except:
        pass

    # Fallback to DOM
    if not data['title']:
        for sel in ['h1[data-testid="product-title"]', 'h1[class*="title"]', 'h1', 
                    'div[class*="product-name"] h1', 'span[class*="product-title"]']:
            tag = soup.select_one(sel)
            if tag:
                data['title'] = tag.get_text(strip=True)
                break

    if not data['price']:
        for sel in ['span[class*="price"]', 'div[class*="price"]', 'span[class*="_2de9"]', '[class*="current-price"]']:
            tag = soup.select_one(sel)
            if tag:
                data['price'] = tag.get_text(strip=True)
                break

    if not data['images']:
        img_tags = soup.select('img[src*="kwcdn.com"]') + soup.select('img[data-src*="kwcdn.com"]')
        seen = set()
        for img in img_tags:
            src = img.get('src') or img.get('data-src')
            if src and src not in seen and 'thumbnail' not in src.lower():
                seen.add(src)
                data['images'].append(src)

    data['colors'] = list(dict.fromkeys(data['colors']))[:20]
    data['sizes'] = list(dict.fromkeys(data['sizes']))[:20]
    data['images'] = data['images'][:15]
    return data

# ===================== ROUTES =====================

@app.route('/')
def home():
    return jsonify({
        "service": "Temu Scraper API with Cache",
        "powered_by": "ScrapingBee (Primary) + Oxylabs (Fallback) + Direct + Mock",
        "database": "SQLite (cached for " + str(CACHE_DAYS) + " days)",
        "endpoints": {
            "GET /search?q=<keyword>&limit=<n>": "Search products (cached)",
            "GET /product?url=<temu_url>": "Get product details (cached)",
            "POST /product": {"body": {"url": "temu product url"}},
            "GET /stats": "View cache statistics",
            "GET /health": "Check API health",
            "GET /debug?url=<temu_url>": "Debug HTML structure"
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

    search_url = f"https://www.temu.com/search_result.html?search_key={quote(query)}"

    # 2. Try ScrapingBee (Primary)
    html, scrapingbee_error = fetch_scrapingbee(search_url, timeout=60)
    if html:
        products, debug_info = parse_search_results(html)
        if products:
            save_search_cache(query, products)
            return jsonify({"success": True, "source": "scrapingbee", "query": query, "count": len(products), "products": products})
        scrapingbee_error = f"ScrapingBee: {json.dumps(debug_info)}"

    # 3. Try Oxylabs (Fallback)
    html, oxylabs_error = fetch_oxylabs(search_url, timeout=60)
    if html:
        products, debug_info = parse_search_results(html)
        if products:
            save_search_cache(query, products)
            return jsonify({"success": True, "source": "oxylabs", "query": query, "count": len(products), "products": products})
        oxylabs_error = f"Oxylabs: {json.dumps(debug_info)}"

    # 4. Try direct scraping
    html, direct_error = fetch_direct(search_url, timeout=30)
    if html:
        products, debug_info = parse_search_results(html)
        if products:
            save_search_cache(query, products)
            return jsonify({"success": True, "source": "direct", "query": query, "count": len(products), "products": products})
        direct_error = f"Direct: {json.dumps(debug_info)}"

    # 5. Fallback to mock data
    if use_mock:
        products = get_mock_search_results(query)[:limit]
        return jsonify({
            "success": True,
            "source": "mock",
            "query": query,
            "count": len(products),
            "products": products,
            "scrapingbee_error": scrapingbee_error,
            "oxylabs_error": oxylabs_error,
            "direct_error": direct_error
        })

    return jsonify({
        "error": "All fetch methods failed",
        "scrapingbee_error": scrapingbee_error,
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

    # 2. Try ScrapingBee (Primary)
    html, scrapingbee_error = fetch_scrapingbee(product_url, timeout=90)
    if html:
        data = parse_product_detail(html, product_url)
        if data.get('title'):
            save_product_cache(product_url, data)
            return jsonify({"success": True, "source": "scrapingbee", "product": data})
        scrapingbee_error = f"ScrapingBee returned {len(html)} chars but no title found"

    # 3. Try Oxylabs (Fallback)
    html, oxylabs_error = fetch_oxylabs(product_url, timeout=90)
    if html:
        data = parse_product_detail(html, product_url)
        if data.get('title'):
            save_product_cache(product_url, data)
            return jsonify({"success": True, "source": "oxylabs", "product": data})
        oxylabs_error = f"Oxylabs returned {len(html)} chars but no title found"

    # 4. Try direct scraping
    html, direct_error = fetch_direct(product_url, timeout=30)
    if html:
        data = parse_product_detail(html, product_url)
        if data.get('title'):
            save_product_cache(product_url, data)
            return jsonify({"success": True, "source": "direct", "product": data})
        direct_error = f"Direct returned {len(html)} chars but no title found"

    # 5. Fallback to mock data
    if use_mock:
        data = get_mock_product_detail(product_url)
        return jsonify({
            "success": True,
            "source": "mock",
            "product": data,
            "scrapingbee_error": scrapingbee_error,
            "oxylabs_error": oxylabs_error,
            "direct_error": direct_error
        })

    return jsonify({
        "error": "All fetch methods failed",
        "scrapingbee_error": scrapingbee_error,
        "oxylabs_error": oxylabs_error,
        "direct_error": direct_error,
        "note": "Add ?mock=true to get test data"
    }), 502

@app.route('/debug')
def debug_fetch():
    """Debug endpoint to inspect HTML structure and find window variables."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    html, err = fetch_scrapingbee(url, timeout=60)
    if not html:
        return jsonify({"error": err or "Failed to fetch"}), 502

    # Extract all window variables
    window_vars = extract_all_window_vars(html)

    # For each variable, check if it contains products
    var_analysis = {}
    for var_name, data in window_vars.items():
        products = find_products_in_data(data)
        var_analysis[var_name] = {
            "type": type(data).__name__,
            "has_products": len(products) > 0,
            "product_count": len(products),
            "data_preview": str(data)[:200] if not isinstance(data, (dict, list)) else json.dumps(data)[:200]
        }

    # Try parsing
    products, debug_info = parse_search_results(html)

    return jsonify({
        "success": True,
        "url": url,
        "html_length": len(html),
        "window_vars_found": list(window_vars.keys()),
        "var_analysis": var_analysis,
        "parse_debug": debug_info,
        "products_found": len(products),
        "html_preview": html[:1500]
    })

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
        "scrapingbee_configured": bool(SCRAPINGBEE_API_KEY),
        "oxylabs_configured": bool(OXYLABS_USER and OXYLABS_PASS),
        "oxylabs_user_prefix": OXYLABS_USER[:5] + "..." if OXYLABS_USER else None
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "scrapingbee_configured": bool(SCRAPINGBEE_API_KEY),
        "oxylabs_configured": bool(OXYLABS_USER and OXYLABS_PASS)
    })

init_db()
