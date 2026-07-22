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

SCRAPINGBEE_KEY = os.getenv('SCRAPINGBEE_API_KEY', '')
SCRAPINGBEE_URL = "https://app.scrapingbee.com/api/v1/"

@app.get('/')
async def root():
    return {
        'message': 'Temu Search API is running',
        'docs': '/docs',
        'endpoints': ['/search', '/product-details', '/products'],
        'version': '7.0'
    }

@app.post('/search')
async def search(request: SearchRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail='Query is required')
    
    if not SCRAPINGBEE_KEY:
        raise HTTPException(status_code=503, detail='SCRAPINGBEE_API_KEY not set')
    
    search_url = f"https://www.temu.com/search_result.html?search_key={query}"
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # AI EXTRACTION — يقرأ الصفحة كإنسان
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": search_url,
                    "render_js": "true",
                    "wait": "10000",
                    "ai_extract_rules": json.dumps({
                        "products": f"Extract a list of up to {request.max_results} products from this Temu search results page. For each product: name, price, original_price if shown, discount if shown, image URL, rating if shown, number of sold items if shown, and product URL"
                    })
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code}')
            
            data = r.json()
            products = data.get('products', [])
            
            if not products:
                raise HTTPException(status_code=503, detail='No products found')
            
            # Clean up
            cleaned = []
            for p in products:
                cleaned.append({
                    'name': p.get('name', 'Unknown'),
                    'price': p.get('price', '$0'),
                    'original_price': p.get('original_price'),
                    'discount': p.get('discount'),
                    'image': p.get('image_url') or p.get('image', ''),
                    'rating': p.get('rating'),
                    'sold_count': p.get('sold_count') or p.get('number_of_sold_items'),
                    'product_url': p.get('product_url') or p.get('url', ''),
                })
            
            return {
                'query': query,
                'total_results': len(cleaned),
                'products': cleaned,
                'source': 'scrapingbee_ai'
            }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f'Error: {str(e)}')

@app.get('/product-details')
async def product_details(url: str):
    if not url.startswith('https://www.temu.com'):
        raise HTTPException(status_code=400, detail='Invalid Temu URL')
    
    if not SCRAPINGBEE_KEY:
        raise HTTPException(status_code=503, detail='SCRAPINGBEE_API_KEY not set')
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": url,
                    "render_js": "true",
                    "wait": "10000",
                    "ai_extract_rules": json.dumps({
                        "title": "Product title/name",
                        "price": "Current selling price",
                        "original_price": "Original price before discount",
                        "discount": "Discount percentage",
                        "images": "All product image URLs as a list",
                        "description": "Full product description",
                        "sizes": "Available sizes as a list",
                        "colors": "Available colors as a list",
                        "rating": "Product rating/stars",
                        "sold_count": "Number of items sold",
                        "reviews": "Customer reviews as a list of {user, text, rating}"
                    })
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code}')
            
            return {
                'source': 'scrapingbee_ai',
                'product': r.json()
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f'Error: {str(e)}')

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
