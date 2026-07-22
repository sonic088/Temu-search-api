from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import json
import os
import httpx

app = FastAPI(title='Temu Search API')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

DB_FILE = 'temu_products.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT, name TEXT, price TEXT, original_price TEXT,
        discount TEXT, image TEXT, images TEXT, rating TEXT,
        sold_count TEXT, product_url TEXT, description TEXT,
        sizes TEXT, colors TEXT, reviews TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS search_cache (
        query TEXT PRIMARY KEY, results TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

class SearchRequest(BaseModel):
    query: str
    max_results: int = 20

class Product(BaseModel):
    name: str
    price: str
    original_price: Optional[str] = None
    discount: Optional[str] = None
    image: str
    images: Optional[List[str]] = None
    rating: Optional[str] = None
    sold_count: Optional[str] = None
    product_url: Optional[str] = None
    description: Optional[str] = None
    sizes: Optional[List[str]] = None
    colors: Optional[List[str]] = None
    reviews: Optional[List[dict]] = None

class SearchResponse(BaseModel):
    query: str
    total_results: int
    products: List[Product]
    source: str

SCRAPINGBEE_KEY = os.getenv('SCRAPINGBEE_API_KEY', '')
PARSE_API_KEY = os.getenv('PARSE_API_KEY', '')
SCRAPINGBEE_URL = "https://app.scrapingbee.com/api/v1/"
PARSE_BASE_URL = 'https://api.parse.bot/scraper/19417d13-c955-4a31-bfb8-d40635cf048d'

async def search_parse(query: str, limit: int = 20):
    if not PARSE_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f'{PARSE_BASE_URL}/search_products',
                headers={'X-API-Key': PARSE_API_KEY},
                params={'query': query, 'limit': limit, 'offset': 0, 'locale': 'en'}
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return data.get('data', {}).get('products', [])
    except Exception as e:
        print(f"Parse search failed: {e}")
        return None

async def fetch_product_details(temu_url: str):
    if not SCRAPINGBEE_KEY:
        return None
    extract_rules = {
        "title": "h1",
        "price": "[data-testid='price']",
        "original_price": "[data-testid='original-price']",
        "discount": "[data-testid='discount']",
        "rating": "[data-testid='rating']",
        "sold_count": "[data-testid='sold-count']",
        "description": "[data-testid='description']",
        "images": {"selector": "img[data-testid='product-image']", "type": "list", "output": "@src"},
        "sizes": {"selector": "[data-testid='size-option']", "type": "list", "output": ".text"},
        "colors": {"selector": "[data-testid='color-option']", "type": "list", "output": "@title"},
        "reviews": {"selector": "[data-testid='review-item']", "type": "list",
                    "output": {"user": ".reviewer-name", "text": ".review-text", "stars": ".review-stars"}}
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": temu_url,
                    "render_js": "true",
                    "extract_rules": json.dumps(extract_rules)
                }
            )
            if r.status_code != 200:
                print(f"ScrapingBee error: {r.status_code}")
                return None
            return r.json()
    except Exception as e:
        print(f"ScrapingBee failed: {e}")
        return None

def get_cached_results(query: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT results FROM search_cache WHERE query = ?', (query.lower(),))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None

def cache_results(query: str, results: list):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO search_cache (query, results) VALUES (?, ?)',
              (query.lower(), json.dumps(results)))
    conn.commit()
    conn.close()

def save_product(query: str, p: dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO products
        (query, name, price, original_price, discount, image, images, rating,
         sold_count, product_url, description, sizes, colors, reviews)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (query, p.get('name'), p.get('price'), p.get('original_price'),
         p.get('discount'), p.get('image'),
         json.dumps(p.get('images')) if p.get('images') else None,
         p.get('rating'), p.get('sold_count'), p.get('product_url'),
         p.get('description'),
         json.dumps(p.get('sizes')) if p.get('sizes') else None,
         json.dumps(p.get('colors')) if p.get('colors') else None,
         json.dumps(p.get('reviews')) if p.get('reviews') else None))
    conn.commit()
    conn.close()

def get_product_by_url(url: str):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM products WHERE product_url = ?', (url,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

@app.get('/')
async def root():
    return {
        'message': 'Temu Search API is running',
        'docs': '/docs',
        'endpoints': ['/search', '/product-details', '/products'],
        'scraper': 'parse_api + scrapingbee',
        'version': '3.0'
    }

@app.post('/search', response_model=SearchResponse)
async def search(request: SearchRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail='Query is required')
    
    cached = get_cached_results(query)
    if cached:
        return SearchResponse(query=query, total_results=len(cached), products=cached, source='cache')
    
    items = await search_parse(query, request.max_results)
    if not items:
        raise HTTPException(status_code=503, detail='Search failed. Check PARSE_API_KEY.')
    
    products = []
    for item in items:
        products.append({
            'name': item.get('title', 'Unknown'),
            'price': item.get('price', '$0'),
            'original_price': item.get('market_price'),
            'discount': f"-{item.get('discount_percent')}%" if item.get('discount_percent') else None,
            'image': item.get('thumbnail', ''),
            'images': None, 'rating': str(item.get('rating')) if item.get('rating') is not None else None,
            'sold_count': item.get('sold_count'), 'product_url': item.get('product_url'),
            'description': None, 'sizes': None, 'colors': None, 'reviews': None
        })
    
    cache_results(query, products)
    return SearchResponse(query=query, total_results=len(products), products=products, source='parse_api')

@app.get('/product-details')
async def product_details(url: str):
    if not url.startswith('https://www.temu.com'):
        raise HTTPException(status_code=400, detail='Invalid Temu URL')
    
    db_product = get_product_by_url(url)
    if db_product and db_product.get('description'):
        return {'source': 'database', 'product': {
            'name': db_product['name'], 'price': db_product['price'],
            'original_price': db_product['original_price'], 'discount': db_product['discount'],
            'image': db_product['image'], 'images': json.loads(db_product['images']) if db_product['images'] else None,
            'rating': db_product['rating'], 'sold_count': db_product['sold_count'],
            'product_url': db_product['product_url'], 'description': db_product['description'],
            'sizes': json.loads(db_product['sizes']) if db_product['sizes'] else None,
            'colors': json.loads(db_product['colors']) if db_product['colors'] else None,
            'reviews': json.loads(db_product['reviews']) if db_product['reviews'] else None,
        }}
    
    details = await fetch_product_details(url)
    if not details:
        raise HTTPException(status_code=503, detail='Failed to fetch details. Check SCRAPINGBEE_API_KEY.')
    
    product = {
        'name': details.get('title', 'Unknown'), 'price': details.get('price', '$0'),
        'original_price': details.get('original_price'), 'discount': details.get('discount'),
        'image': details.get('images', [''])[0] if details.get('images') else '',
        'images': details.get('images'), 'rating': details.get('rating'),
        'sold_count': details.get('sold_count'), 'product_url': url,
        'description': details.get('description'), 'sizes': details.get('sizes'),
        'colors': details.get('colors'), 'reviews': details.get('reviews'),
    }
    save_product('', product)
    return {'source': 'scrapingbee', 'product': product}

@app.get('/products')
async def get_all_products():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM products ORDER BY created_at DESC LIMIT 100')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=10000)
