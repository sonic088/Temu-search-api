from flask import Flask, request, jsonify
import requests
import os
import re
import json
from bs4 import BeautifulSoup
from urllib.parse import urlencode, quote

app = Flask(__name__)

# ─── Oxylabs Configuration ─────────────────────────────────────────
# Use Web Unblocker for best results with Temu
OXYLABS_USER = os.environ.get('OXYLABS_USER', '')
OXYLABS_PASS = os.environ.get('OXYLABS_PASS', '')

# Web Unblocker endpoint (best for anti-bot sites like Temu)
OXYLABS_PROXY = f"http://{OXYLABS_USER}:{OXYLABS_PASS}@unblock.oxylabs.io:60000"

# Alternative: Web Scraper API (structured data)
OXYLABS_SCRAPER_API = "https://realtime.oxylabs.io/v1/queries"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}

# ─── Helper: Fetch via Oxylabs Web Unblocker ───────────────────────
def fetch_oxylabs(url, render_js=True, timeout=45):
    """Fetch URL through Oxylabs Web Unblocker with JS rendering."""
    proxies = {
        "http": OXYLABS_PROXY,
        "https": OXYLABS_PROXY,
    }

    # Add render instruction for Oxylabs
    target_url = url
    if render_js:
        # Oxylabs Web Unblocker handles JS automatically
        pass

    try:
        resp = requests.get(
            target_url,
            proxies=proxies,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[Oxylabs Error] {e}")
        return None


# ─── Helper: Parse Product List (Search Results) ───────────────────
def parse_search_results(html):
    """Extract product cards from Temu search results page."""
    soup = BeautifulSoup(html, 'lxml')
    products = []

    # Temu uses dynamic class names, so we use structural selectors
    # These may need updating as Temu changes their frontend

    # Try multiple selector strategies
    cards = (
        soup.select('div[data-testid="product-card"]') or
        soup.select('div[class*="goods-item"]') or
        soup.select('div[class*="product-card"]') or
        soup.select('a[href*="goods.html"]') or
        soup.find_all('div', class_=re.compile(r'.*goods.*'))
    )

    for card in cards[:24]:  # Limit to 24 results
        product = {}

        # Product ID from URL
        link_tag = card if card.name == 'a' else card.find('a', href=re.compile(r'goods_id'))
        if link_tag and link_tag.get('href'):
            href = link_tag['href']
            if href.startswith('/'):
                href = 'https://www.temu.com' + href
            product['product_url'] = href
            # Extract goods_id
            match = re.search(r'goods_id[=:](\d+)', href)
            if match:
                product['product_id'] = match.group(1)

        # Title
        title_tag = (
            card.select_one('[class*="title"]') or
            card.select_one('h2') or
            card.select_one('h3') or
            card.select_one('span[class*="title"]')
        )
        if title_tag:
            product['title'] = title_tag.get_text(strip=True)

        # Price
        price_tag = (
            card.select_one('[class*="price"]') or
            card.select_one('span[class*="_2de9"]') or
            card.find(text=re.compile(r'\$\d+'))
        )
        if price_tag:
            if hasattr(price_tag, 'get_text'):
                product['price'] = price_tag.get_text(strip=True)
            else:
                product['price'] = price_tag.strip()

        # Original Price
        original_price_tag = card.select_one('[class*="original"]') or card.select_one('[class*="market"]')
        if original_price_tag:
            product['original_price'] = original_price_tag.get_text(strip=True)

        # Rating
        rating_tag = card.select_one('[class*="rating"]') or card.find(text=re.compile(r'\d\.\d'))
        if rating_tag:
            text = rating_tag.get_text(strip=True) if hasattr(rating_tag, 'get_text') else rating_tag
            match = re.search(r'(\d\.\d)', text)
            if match:
                product['rating'] = float(match.group(1))

        # Sold count
        sold_tag = card.select_one('[class*="sold"]') or card.find(text=re.compile(r'\d+[KkMm]?\+?\s*sold'))
        if sold_tag:
            text = sold_tag.get_text(strip=True) if hasattr(sold_tag, 'get_text') else sold_tag
            product['sold_count'] = text.strip()

        # Image
        img_tag = card.select_one('img[src*="kwcdn.com"]') or card.select_one('img[data-src*="kwcdn.com"]')
        if img_tag:
            product['image'] = img_tag.get('src') or img_tag.get('data-src')

        # Discount
        discount_tag = card.select_one('[class*="discount"]') or card.find(text=re.compile(r'-?\d+%'))
        if discount_tag:
            text = discount_tag.get_text(strip=True) if hasattr(discount_tag, 'get_text') else discount_tag
            match = re.search(r'(\d+)%', text)
            if match:
                product['discount_percent'] = int(match.group(1))

        if product.get('title') or product.get('product_id'):
            products.append(product)

    return products


# ─── Helper: Parse Product Detail Page ─────────────────────────────
def parse_product_detail(html, product_url):
    """Extract full product details from Temu product page."""
    soup = BeautifulSoup(html, 'lxml')
    data = {
        'product_url': product_url,
        'title': None,
        'price': None,
        'original_price': None,
        'currency': None,
        'rating': None,
        'review_count': None,
        'sold_count': None,
        'description': None,
        'images': [],
        'colors': [],
        'sizes': [],
        'specs': {},
        'store_info': {},
        'variants': [],
    }

    # ── Title ──
    title_selectors = [
        'h1[data-testid="product-title"]',
        'h1[class*="title"]',
        'h1',
        'div[class*="product-name"] h1',
        'span[class*="product-title"]',
    ]
    for sel in title_selectors:
        tag = soup.select_one(sel)
        if tag:
            data['title'] = tag.get_text(strip=True)
            break

    # ── Price ──
    price_patterns = [
        'span[class*="price"]',
        'div[class*="price"]',
        'span[class*="_2de9"]',
        '[class*="current-price"]',
    ]
    for sel in price_patterns:
        tag = soup.select_one(sel)
        if tag:
            text = tag.get_text(strip=True)
            # Extract currency and amount
            match = re.search(r'([A-Z]{3})?\s*([$€£¥])?\s*([\d,]+\.?\d*)', text)
            if match:
                data['price'] = text
                if match.group(1):
                    data['currency'] = match.group(1)
            break

    # ── Original Price ──
    orig_tag = soup.select_one('[class*="original-price"]') or soup.select_one('[class*="market-price"]')
    if orig_tag:
        data['original_price'] = orig_tag.get_text(strip=True)

    # ── Rating & Reviews ──
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

    # ── Sold Count ──
    sold_tag = soup.find(text=re.compile(r'\d+[KkMm]?\+?\s*sold', re.I))
    if sold_tag:
        data['sold_count'] = sold_tag.strip()

    # ── Images (Gallery) ──
    img_tags = soup.select('img[src*="kwcdn.com"]') + soup.select('img[data-src*="kwcdn.com"]')
    seen = set()
    for img in img_tags:
        src = img.get('src') or img.get('data-src')
        if src and src not in seen and 'thumbnail' not in src.lower():
            seen.add(src)
            data['images'].append(src)

    # ── Colors ──
    color_tags = soup.select('[class*="color"]') + soup.select('[class*="colour"]')
    for tag in color_tags:
        text = tag.get_text(strip=True)
        if text and len(text) < 50 and text not in data['colors']:
            data['colors'].append(text)

    # Also look for color swatches
    swatches = soup.select('[class*="swatch"]') + soup.select('[class*="sku-item"]')
    for swatch in swatches:
        text = swatch.get_text(strip=True)
        if text and text not in data['colors'] and len(text) < 50:
            data['colors'].append(text)

    # ── Sizes ──
    size_tags = soup.select('[class*="size"]') + soup.select('[class*="dimension"]')
    for tag in size_tags:
        text = tag.get_text(strip=True)
        if text and text not in data['sizes'] and len(text) < 30:
            data['sizes'].append(text)

    # ── Description ──
    desc_tag = soup.select_one('[class*="description"]') or soup.select_one('[class*="detail"]')
    if desc_tag:
        data['description'] = desc_tag.get_text(strip=True)[:500]

    # ── Specs / Details ──
    spec_rows = soup.select('[class*="spec"]') + soup.select('table tr')
    for row in spec_rows:
        cells = row.select('td, th, div')
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key and val and len(key) < 50:
                data['specs'][key] = val

    # ── Store Info ──
    store_tag = soup.select_one('[class*="store"]') or soup.select_one('[class*="mall"]')
    if store_tag:
        data['store_info']['name'] = store_tag.get_text(strip=True)

    # ── Try to extract JSON-LD or page data ──
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            ld = json.loads(script.string)
            if isinstance(ld, dict):
                if ld.get('@type') == 'Product':
                    data['title'] = data['title'] or ld.get('name')
                    if ld.get('offers'):
                        offers = ld['offers']
                        if isinstance(offers, list):
                            offers = offers[0]
                        data['price'] = data['price'] or offers.get('price')
                        data['currency'] = data['currency'] or offers.get('priceCurrency')
                    data['description'] = data['description'] or ld.get('description', '')[:500]
                    if ld.get('image'):
                        if isinstance(ld['image'], str):
                            if ld['image'] not in data['images']:
                                data['images'].insert(0, ld['image'])
                        elif isinstance(ld['image'], list):
                            for img in ld['image']:
                                if img not in data['images']:
                                    data['images'].append(img)
        except:
            pass

    # ── Try window.__INITIAL_STATE__ or similar ──
    scripts_js = soup.find_all('script')
    for script in scripts_js:
        if script.string and ('initialState' in script.string or 'goodsInfo' in script.string):
            try:
                # Extract JSON from JS variable
                match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', script.string, re.DOTALL)
                if match:
                    state = json.loads(match.group(1))
                    # Navigate to product data if present
                    if 'goodsInfo' in state:
                        goods = state['goodsInfo']
                        data['title'] = data['title'] or goods.get('goodsName')
                        data['description'] = data['description'] or goods.get('goodsDesc', '')[:500]
                        if 'skuList' in goods:
                            for sku in goods['skuList']:
                                variant = {
                                    'sku_id': sku.get('skuId'),
                                    'price': sku.get('price'),
                                    'original_price': sku.get('marketPrice'),
                                    'available': sku.get('isOnsale'),
                                }
                                # Extract color/size from specs
                                specs = sku.get('specs', [])
                                for spec in specs:
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

    # Clean up empty lists
    data['colors'] = list(dict.fromkeys(data['colors']))[:20]
    data['sizes'] = list(dict.fromkeys(data['sizes']))[:20]
    data['images'] = data['images'][:15]

    return data


# ═══════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.route('/')
def home():
    return jsonify({
        "service": "Temu Scraper API",
        "powered_by": "Oxylabs",
        "endpoints": {
            "GET /search?q=<keyword>&limit=<n>": "Search Temu products by keyword",
            "GET /product?url=<temu_url>": "Get full product details (images, colors, sizes, prices)",
            "POST /product": {"body": {"url": "temu product url"}},
        },
        "note": "Temu changes their frontend frequently. Selectors may need updates."
    })


@app.route('/search', methods=['GET'])
def search_products():
    """Search Temu by keyword."""
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 12)), 24)
    locale = request.args.get('locale', 'en')

    if not query:
        return jsonify({"error": "Missing 'q' parameter"}), 400

    # Build Temu search URL
    search_url = f"https://www.temu.com/search_result.html?search_key={quote(query)}"
    if locale != 'en':
        search_url += f"&locale={locale}"

    html = fetch_oxylabs(search_url, render_js=True)
    if not html:
        return jsonify({"error": "Failed to fetch search results. Check Oxylabs credentials."}), 502

    products = parse_search_results(html)

    # Limit results
    products = products[:limit]

    return jsonify({
        "success": True,
        "query": query,
        "count": len(products),
        "products": products
    })


@app.route('/product', methods=['GET', 'POST'])
def product_detail():
    """Get full product details from Temu product URL."""
    if request.method == 'POST':
        body = request.get_json() or {}
        product_url = body.get('url', '').strip()
    else:
        product_url = request.args.get('url', '').strip()

    if not product_url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    if not product_url.startswith('http'):
        product_url = 'https://www.temu.com' + product_url

    # Validate it's a Temu URL
    if 'temu.com' not in product_url:
        return jsonify({"error": "Invalid Temu URL"}), 400

    html = fetch_oxylabs(product_url, render_js=True, timeout=60)
    if not html:
        return jsonify({"error": "Failed to fetch product page. Check Oxylabs credentials or URL."}), 502

    data = parse_product_detail(html, product_url)

    return jsonify({
        "success": True,
        "product": data
    })


@app.route('/health')
def health():
    return jsonify({"status": "ok", "oxylabs_configured": bool(OXYLABS_USER and OXYLABS_PASS)})


# ═══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
