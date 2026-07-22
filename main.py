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
        'endpoints': ['/search', '/product-details', '/test-scrapingbee', '/debug'],
        'version': '5.0'
    }

@app.get('/debug')
async def debug():
    return {
        'has_scrapingbee': 'SCRAPINGBEE_API_KEY' in os.environ,
        'scrapingbee_length': len(SCRAPINGBEE_KEY),
    }

@app.get('/test-scrapingbee')
async def test_scrapingbee():
    """اختبار بسيط: هل ScrapingBee يعمل مع Temu؟"""
    if not SCRAPINGBEE_KEY:
        return {'error': 'No SCRAPINGBEE_API_KEY'}
    
    test_url = "https://www.temu.com/search_result.html?search_key=phone"
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Test 1: Simple request without extract_rules (just get HTML)
            r1 = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": test_url,
                    "render_js": "true",
                    "wait": "5000",
                }
            )
            
            # Test 2: With AI extraction (more reliable than CSS selectors)
            r2 = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": test_url,
                    "render_js": "true",
                    "wait": "5000",
                    "ai_extract_rules": json.dumps({
                        "products": "List all products with name, price, and image URL"
                    })
                }
            )
            
            return {
                'test1_simple_status': r1.status_code,
                'test1_html_length': len(r1.text),
                'test1_preview': r1.text[:500],
                'test2_ai_status': r2.status_code,
                'test2_ai_result': r2.json() if r2.status_code == 200 else r2.text[:500],
            }
    except Exception as e:
        return {'error': str(e)}

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
            # Use AI extraction - more reliable for Temu
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": search_url,
                    "render_js": "true",
                    "wait": "5000",
                    "ai_extract_rules": json.dumps({
                        "products": f"List up to {request.max_results} products. For each: name, price, original_price if shown, discount if shown, image URL, rating if shown, sold count if shown, product URL"
                    })
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code} - {r.text[:200]}')
            
            data = r.json()
            products = data.get('products', [])
            
            if not products:
                raise HTTPException(status_code=503, detail='No products found in response')
            
            return {
                'query': query,
                'total_results': len(products),
                'products': products,
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
                    "wait": "5000",
                    "ai_extract_rules": json.dumps({
                        "title": "Product title",
                        "price": "Current price",
                        "original_price": "Original price if shown",
                        "discount": "Discount percentage if shown",
                        "images": "All product image URLs",
                        "description": "Full product description",
                        "sizes": "Available sizes",
                        "colors": "Available colors",
                        "rating": "Product rating",
                        "reviews": "Customer reviews if available"
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
