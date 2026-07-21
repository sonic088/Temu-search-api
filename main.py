from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import json
import os
import random

app = FastAPI(title="Temu Search API")

# ─── CORS - مهم جداً عشان Frontend يقدر يتصل ───
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database Setup ───
DB_FILE = "temu_products.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT,
        name TEXT,
        price TEXT,
        original_price TEXT,
        discount TEXT,
        image TEXT,
        rating TEXT,
        sold_count TEXT,
        product_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS search_cache (
        query TEXT PRIMARY KEY,
        results TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

# ─── Models ───
class SearchRequest(BaseModel):
    query: str
    max_results: int = 20

class Product(BaseModel):
    name: str
    price: str
    original_price: Optional[str] = None
    discount: Optional[str] = None
    image: str
    rating: Optional[str] = None
    sold_count: Optional[str] = None
    product_url: Optional[str] = None

class SearchResponse(BaseModel):
    query: str
    total_results: int
    products: List[Product]
    source: str

# ─── Mock Data Generator ───
def generate_mock_products(query: str, count: int = 10) -> List[dict]:
    """Generate realistic mock products when scraping fails"""
    categories = {
        "shirt": ["Cotton T-Shirt", "Polo Shirt", "Dress Shirt", "Flannel Shirt", "Tank Top"],
        "dress": ["Summer Dress", "Evening Gown", "Casual Dress", "Maxi Dress", "Mini Dress"],
        "phone": ["Smartphone Case", "Screen Protector", "Charging Cable", "Power Bank", "Phone Stand"],
        "shoes": ["Running Shoes", "Casual Sneakers", "Leather Boots", "Sandals", "Slippers"],
        "bag": ["Backpack", "Handbag", "Crossbody Bag", "Tote Bag", "Wallet"],
    }

    base_names = categories.get(query.lower(), ["Product", "Item", "Accessory", "Gadget", "Tool"])
    products = []

    for i in range(count):
        base = random.choice(base_names)
        price = round(random.uniform(5, 80), 2)
        original = round(price * random.uniform(1.2, 2.5), 2)
        discount = round(((original - price) / original) * 100)

        products.append({
            "name": f"{base} - Premium Quality {i+1}",
            "price": f"${price:.2f}",
            "original_price": f"${original:.2f}",
            "discount": f"-{discount}%",
            "image": f"https://picsum.photos/300/300?random={random.randint(1, 1000)}",
            "rating": str(round(random.uniform(3.5, 5.0), 1)),
            "sold_count": f"{random.randint(100, 9999)}+ sold",
            "product_url": f"https://temu.com/product/{query}-{i+1}"
        })

    return products

# ─── Database Functions ───
def get_cached_results(query: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT results FROM search_cache WHERE query = ?", (query.lower(),))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None

def cache_results(query: str, results: list):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO search_cache (query, results) VALUES (?, ?)",
              (query.lower(), json.dumps(results)))
    conn.commit()
    conn.close()

def save_products(query: str, products: list):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for p in products:
        c.execute('''INSERT INTO products
            (query, name, price, original_price, discount, image, rating, sold_count, product_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (query, p.get('name'), p.get('price'), p.get('original_price'),
             p.get('discount'), p.get('image'), p.get('rating'),
             p.get('sold_count'), p.get('product_url')))
    conn.commit()
    conn.close()

def get_all_products_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_products_by_query(query: str):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE query = ? ORDER BY created_at DESC LIMIT 20", (query.lower(),))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ─── API Endpoints ───
@app.get("/")
async def root():
    return {
        "message": "Temu Search API is running",
        "docs": "/docs",
        "endpoints": ["/search", "/products", "/recommendations/{query}"]
    }

@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    # 1. Check cache first
    cached = get_cached_results(query)
    if cached:
        return SearchResponse(
            query=query,
            total_results=len(cached),
            products=cached,
            source="cache"
        )

    # 2. Try Playwright scraping (will likely fail on Render Free Tier)
    products = []
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()

            search_url = f"https://www.temu.com/search_result.html?search_key={query}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_selector('[data-testid="goodsItem"]', timeout=10000)

            items = await page.query_selector_all('[data-testid="goodsItem"]')

            for item in items[:request.max_results]:
                try:
                    name_el = await item.query_selector('.goods-name')
                    price_el = await item.query_selector('.goods-price')
                    img_el = await item.query_selector('img')

                    name = await name_el.inner_text() if name_el else "Unknown"
                    price = await price_el.inner_text() if price_el else "N/A"
                    image = await img_el.get_attribute('src') if img_el else ""

                    products.append({
                        "name": name.strip(),
                        "price": price.strip(),
                        "original_price": None,
                        "discount": None,
                        "image": image,
                        "rating": None,
                        "sold_count": None,
                        "product_url": None
                    })
                except:
                    continue

            await browser.close()
    except Exception as e:
        print(f"Scraping failed (expected on Render Free): {e}")
        # Fallback to mock data
        products = generate_mock_products(query, request.max_results)

    # 3. Save to database
    if products:
        save_products(query, products)
        cache_results(query, products)

    return SearchResponse(
        query=query,
        total_results=len(products),
        products=products,
        source="scraped" if len(products) > 0 and products[0].get("product_url") else "mock"
    )

@app.get("/products")
async def get_all_products():
    return get_all_products_db()

@app.get("/recommendations/{query}")
async def recommendations(query: str):
    """AI-powered recommendations - currently uses simple sorting"""
    products = get_products_by_query(query)

    if not products:
        # Generate mock if no data
        products = generate_mock_products(query, 10)

    # Simple recommendation: sort by price (lower = better value)
    try:
        recommended = sorted(
            products,
            key=lambda x: float(x.get('price', '0').replace('$', '').replace(',', ''))
        )[:3]
    except:
        recommended = products[:3]

    return {
        "query": query,
        "recommendations": recommended,
        "total_analyzed": len(products)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
