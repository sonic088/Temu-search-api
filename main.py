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

def extract_products_from_html(html: str, max_results: int):
    """استخراج المنتجات من HTML Temu"""
    products = []
    
    # الطريقة 1: JSON مضمن في script
    json_patterns = [
        r'window\._SSR_HYDRATED_DATA\s*=\s*({.+?});',
        r'"goodsList":\s*(\[.+?\])',
        r'"searchResult":\s*({.+?})',
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, html, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, dict):
                    items = data.get('goodsList', []) or data.get('searchResult', {}).get('data', [])
                elif isinstance(data, list):
                    items = data
                else:
                    items = []
                
                for item in items[:max_results]:
                    p = {
                        'name': item.get('title', item.get('goodsName', 'Unknown')),
                        'price': item.get('price', item.get('salePrice', '$0')),
                        'original_price': item.get('market_price', item.get('normalPrice')),
                        'discount': item.get('discount'),
                        'image': item.get('thumb_url', item.get('goodsImg', '')),
                        'rating': str(item.get('rating', item.get('goodsRate', ''))),
                        'sold_count': item.get('sold_count', item.get('soldQuantity')),
                        'product_url': item.get('url', item.get('goodsUrl', '')),
                    }
                    if p['product_url'] and not p['product_url'].startswith('http'):
                        p['product_url'] = 'https://www.temu.com' + p['product_url']
                    products.append(p)
                
                if products:
                    return products
            except:
                continue
    
    # الطريقة 2: regex مباشر على HTML
    if not products:
        titles = re.findall(r'alt="([^"]+)"[^>]*class="[^"]*title', html)
        prices = re.findall(r'[\$€£]\d+\.?\d*', html)
        images = re.findall(r'src="(https://img\.kwcdn\.com/[^"]+)"', html)
        
        for i in range(min(len(titles), max_results)):
            products.append({
                'name': titles[i],
                'price': prices[i] if i < len(prices) else '$0',
                'image': images[i] if i < len(images) else '',
                'product_url': '',
            })
    
    return products

@app.get('/')
async def root():
    return {
        'message': 'Temu Search API is running',
        'docs': '/docs',
        'endpoints': ['/search', '/product-details', '/products'],
        'version': '8.0'
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
            r = await client.get(
                SCRAPINGBEE_URL,
                params={
                    "api_key": SCRAPINGBEE_KEY,
                    "url": search_url,
                    "render_js": "true",
                    "wait": "8000",
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code}')
            
            html = r.text
            
            if len(html) < 1000:
                raise HTTPException(status_code=503, detail='Empty response from Temu')
            
            products = extract_products_from_html(html, request.max_results)
            
            if not products:
                raise HTTPException(status_code=503, detail='No products found in page')
            
            return {
                'query': query,
                'total_results': len(products),
                'products': products,
                'source': 'scrapingbee_html'
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
                    "wait": "8000",
                }
            )
            
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail=f'ScrapingBee error: {r.status_code}')
            
            html = r.text
            
            # استخراج تفاصيل المنتج من JSON المضمن
            product = {
                'name': 'Unknown',
                'price': '$0',
                'image': '',
                'images': [],
                'description': '',
                'sizes': [],
                'colors': [],
            }
            
            matches = re.findall(r'window\._SSR_HYDRATED_DATA\s*=\s*({.+?});', html, re.DOTALL)
            if matches:
                try:
                    data = json.loads(matches[0])
                    goods = data.get('goods', {})
                    product = {
                        'name': goods.get('title', goods.get('goodsName', 'Unknown')),
                        'price': goods.get('price', goods.get('salePrice', '$0')),
                        'original_price': goods.get('market_price', goods.get('normalPrice')),
                        'discount': goods.get('discount'),
                        'image': goods.get('thumb_url', goods.get('goodsImg', '')),
                        'images': goods.get('thumb_url_list', goods.get('goodsImgList', [])),
                        'description': goods.get('description', ''),
                        'sizes': [s.get('name') for s in goods.get('specs', []) if s.get('name')],
                        'colors': [c.get('name') for c in goods.get('specs', []) if c.get('name')],
                        'rating': str(goods.get('rating', '')),
                        'sold_count': goods.get('sold_count', goods.get('soldQuantity')),
                        'product_url': url,
                    }
                except:
                    pass
            
            return {
                'source': 'scrapingbee_html',
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
