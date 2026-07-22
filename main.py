from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import json
import os
import httpx
import re

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
        'version': '6.0'
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
            # PREMIUM PROXY + JS RENDERING — يتجاوز Cloudflare
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": search_url,
                    "render_js": "true",
                    "premium_proxy": "true",  # ← الحل السحري
                    "wait": "8000",
                    "block_resources": "false",
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code}')
            
            html = r.text
            
            # استخراج المنتجات من HTML مباشرة (regex)
            products = []
            
            # Temu يستخدم JSON مضمن في الصفحة
            json_matches = re.findall(r'window\._SSR_HYDRATED_DATA\s*=\s*({.+?});', html)
            if json_matches:
                try:
                    data = json.loads(json_matches[0])
                    items = data.get('searchResult', {}).get('data', [])
                    for item in items[:request.max_results]:
                        products.append({
                            'name': item.get('title', 'Unknown'),
                            'price': item.get('price', '$0'),
                            'original_price': item.get('market_price'),
                            'discount': f"-{item.get('discount')}" if item.get('discount') else None,
                            'image': item.get('thumb_url', ''),
                            'rating': str(item.get('rating')) if item.get('rating') else None,
                            'sold_count': item.get('sold_count'),
                            'product_url': f"https://www.temu.com{item.get('url', '')}" if item.get('url') else ''
                        })
                except:
                    pass
            
            # Fallback: regex على HTML
            if not products:
                titles = re.findall(r'"title":"([^"]+)"', html)
                prices = re.findall(r'"price":"([^"]+)"', html)
                images = re.findall(r'"thumb_url":"([^"]+)"', html)
                urls = re.findall(r'"url":"(/[^"]+)"', html)
                
                for i in range(min(len(titles), request.max_results)):
                    products.append({
                        'name': titles[i] if i < len(titles) else 'Unknown',
                        'price': prices[i] if i < len(prices) else '$0',
                        'image': images[i] if i < len(images) else '',
                        'product_url': f"https://www.temu.com{urls[i]}" if i < len(urls) else '',
                    })
            
            if not products:
                raise HTTPException(status_code=503, detail='No products found. Temu may be blocking.')
            
            return {
                'query': query,
                'total_results': len(products),
                'products': products,
                'source': 'scrapingbee_premium'
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
                    "premium_proxy": "true",  # ← الحل السحري
                    "wait": "8000",
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code}')
            
            html = r.text
            
            # استخراج من JSON مضمن
            product = {
                'name': 'Unknown',
                'price': '$0',
                'image': '',
                'images': [],
                'description': '',
                'sizes': [],
                'colors': [],
            }
            
            json_matches = re.findall(r'window\._SSR_HYDRATED_DATA\s*=\s*({.+?});', html)
            if json_matches:
                try:
                    data = json.loads(json_matches[0])
                    p = data.get('goods', {})
                    product = {
                        'name': p.get('title', 'Unknown'),
                        'price': p.get('price', '$0'),
                        'original_price': p.get('market_price'),
                        'discount': p.get('discount'),
                        'image': p.get('thumb_url', ''),
                        'images': p.get('thumb_url_list', []),
                        'description': p.get('description', ''),
                        'sizes': [s.get('name') for s in p.get('specs', []) if s.get('name')],
                        'colors': [c.get('name') for c in p.get('specs', []) if c.get('name')],
                        'rating': str(p.get('rating')) if p.get('rating') else None,
                        'sold_count': p.get('sold_count'),
                        'product_url': url,
                    }
                except:
                    pass
            
            return {
                'source': 'scrapingbee_premium',
                'product': product
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
