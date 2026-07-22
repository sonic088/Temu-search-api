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
SCRAPINGBEE_URL = "https://app.scrapingbee.com/api/v1/"

async def scrape_temu_search(query: str, limit: int = 20):
    """البحث في Temu عبر ScrapingBee"""
    if not SCRAPINGBEE_KEY:
        return None
    search_url = f"https://www.temu.com/search_result.html?search_key={query}"
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": search_url,
                    "render_js": "true",
                    "extract_rules": json.dumps({
                        "products": {
                            "selector": "[data-testid='product-card']",
                            "type": "list",
                            "output": {
                                "name": "[data-testid='product-title']",
                                "price": "[data-testid='product-price']",
                                "original_price": "[data-testid='original-price']",
                                "image": "img[data-testid='product-image']@src",
                                "rating": "[data-testid='rating']",
                                "sold_count": "[data-testid='sold-count']",
                                "product_url": "a@href"
                            }
                        }
                    })
                }
            )
            if r.status_code != 200:
                print(f"ScrapingBee search error: {r.status_code}")
                return None
            data = r.json()
            products = data.get('products', [])
            # Clean up URLs
            for p in products:
                if p.get('product_url') and not p['product_url'].startswith('http'):
                    p['product_url'] = 'https://www.temu.com' + p['product_url']
            return products[:limit]
    except Exception as e:
        print(f"ScrapingBee search failed: {e}")
        return None

async def fetch_product_details(temu_url: str):
    """جلب تفاصيل المنتج"""
    if not SCRAPINGBEE_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": temu_url,
                    "render_js": "true",
                    "extract_rules": json.dumps({
                        "title": "h1",
                        "price": "[data-testid='price']",
                        "original_price": "[data-testid='original-price']",
                        "discount": "[data-testid='discount']",
                        "rating": "[data-testid='rating']",
                        "sold_count": "[data-testid='sold-count']",
                        "description": "[data-testid='description']",
                        "images": {
                            "selector": "img[data-testid='product-image']",
                            "type": "list",
                            "output": "@src"
                        },
                        "sizes": {
                            "selector": "[data-testid='size-option']",
                            "type": "list",
                            "output": ".text"
                        },
                        "colors": {
                            "selector": "[data-testid='color-option']",
                            "type": "list",
                            "output": "@title"
                        }
                    })
                }
            )
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        print(f"ScrapingBee details failed: {e}")
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

@app.get('/')
async def root():
    return {
        'message': 'Temu Search API is running',
        'docs': '/docs',
        'endpoints': ['/search', '/product-details', '/products'],
        'scraper': 'scrapingbee_only',
        'version': '4.0'
    }

@app.post('/search', response_model=SearchResponse)
async def search(request: SearchRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail='Query is required')
    
    cached = get_cached_results(query)
    if cached:
        return SearchResponse(query=query, total_results=len(cached), products=cached, source='cache')
    
    items = await scrape_temu_search(query, request.max_results)
    if not items:
        raise HTTPException(status_code=503, detail='Search failed. Check SCRAPINGBEE_API_KEY.')
    
    products = []
    for item in items:
        products.append({
            'name': item.get('name', 'Unknown'),
            'price': item.get('price', '$0'),
            'original_price': item.get('original_price'),
            'discount': item.get('discount'),
            'image': item.get('image', ''),
            'images': None,
            'rating': item.get('rating'),
            'sold_count': item.get('sold_count'),
            'product_url': item.get('product_url'),
            'description': None,
            'sizes': None,
            'colors': None,
            'reviews': None
        })
    
    cache_results(query, products)
    return SearchResponse(query=query, total_results=len(products), products=products, source='scrapingbee')

@app.get('/product-details')
async def product_details(url: str):
    if not url.startswith('https://www.temu.com'):
        raise HTTPException(status_code=400, detail='Invalid Temu URL')
    
    details = await fetch_product_details(url)
    if not details:
        raise HTTPException(status_code=503, detail='Failed to fetch details. Check SCRAPINGBEE_API_KEY.')
    
    product = {
        'name': details.get('title', 'Unknown'),
        'price': details.get('price', '$0'),
        'original_price': details.get('original_price'),
        'discount': details.get('discount'),
        'image': details.get('images', [''])[0] if details.get('images') else '',
        'images': details.get('images'),
        'rating': details.get('rating'),
        'sold_count': details.get('sold_count'),
        'product_url': url,
        'description': details.get('description'),
        'sizes': details.get('sizes'),
        'colors': details.get('colors'),
        'reviews': None,
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
