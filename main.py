import os
import sqlite3
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
CACHE_DAYS = int(os.getenv("CACHE_DAYS", "7"))
DB_PATH = os.getenv("DB_PATH", "temu_cache.db")

ACTOR_SEARCH = "amit123/temu-products-scraper"
ACTOR_PRODUCT = "piotrv1001/temu-listings-scraper"

# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            query TEXT PRIMARY KEY,
            data TEXT,
            created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS product_cache (
            url TEXT PRIMARY KEY,
            data TEXT,
            created_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def _get_conn():
    return sqlite3.connect(DB_PATH)

def get_cache(table: str, key: str) -> Optional[Any]:
    conn = _get_conn()
    c = conn.cursor()
    col = "query" if table == "search_cache" else "url"
    c.execute(f"SELECT data, created_at FROM {table} WHERE {col} = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row:
        created_at = datetime.fromisoformat(row[1])
        if datetime.now() - created_at < timedelta(days=CACHE_DAYS):
            return json.loads(row[0])
    return None

def set_cache(table: str, key: str, data: Any):
    conn = _get_conn()
    c = conn.cursor()
    col = "query" if table == "search_cache" else "url"
    c.execute(
        f"INSERT OR REPLACE INTO {table} ({col}, data, created_at) VALUES (?, ?, ?)",
        (key, json.dumps(data, ensure_ascii=False), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════════════
# APIFY CLIENT
# ═══════════════════════════════════════════════════════════════
def run_apify_sync(actor_id: str, run_input: dict, timeout: int = 120) -> Optional[List[dict]]:
    if not APIFY_API_TOKEN:
        print("[Apify] API token not configured")
        return None

    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={APIFY_API_TOKEN}"
    try:
        resp = requests.post(url, json=run_input, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", []) or data.get("items", [])
            return []
        print(f"[Apify] HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    except requests.exceptions.Timeout:
        print("[Apify] Request timed out")
        return None
    except Exception as e:
        print(f"[Apify] Exception: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# NORMALIZERS
# ═══════════════════════════════════════════════════════════════
def normalize_search(items: List[dict], image_map: Dict[str, List[str]] = None) -> List[dict]:
    results = []
    image_map = image_map or {}
    for item in items:
        price_info = item.get("price_info", {}) or {}
        comment = item.get("comment", {}) or {}
        product_url = item.get("link_url", "")

        price_str = price_info.get("price_str", "")
        market_str = price_info.get("market_price_str", "")
        discount = None
        try:
            p = float(price_str.replace("$", "").replace(",", ""))
            m = float(market_str.replace("$", "").replace(",", ""))
            if m > p:
                discount = round((m - p) / m * 100)
        except:
            pass

        extra_images = image_map.get(product_url, [])

        results.append({
            "id": str(item.get("goods_id", "")),
            "title": item.get("title", ""),
            "price": price_str,
            "originalPrice": market_str,
            "discountPercentage": discount,
            "rating": comment.get("goods_score"),
            "totalReviews": str(comment.get("comment_num", "")) if comment.get("comment_num") else "",
            "salesCount": item.get("sales_num", ""),
            "imageUrl": item.get("thumb_url", ""),
            "additionalImages": extra_images,
            "description": item.get("title", ""),
            "productUrl": product_url,
            "currency": "USD",
            "source": "apify"
        })
    return results

def normalize_product(items: List[dict]) -> Optional[dict]:
    if not items:
        return None
    item = items[0]
    return {
        "id": str(item.get("id", "")),
        "title": item.get("title", ""),
        "price": item.get("price"),
        "originalPrice": item.get("originalPrice"),
        "discountPercentage": item.get("discountPercentage"),
        "rating": item.get("rating"),
        "totalReviews": str(item.get("totalReviews", "")) if item.get("totalReviews") else "",
        "salesCount": item.get("salesCount", ""),
        "imageUrl": item.get("imageUrl", ""),
        "additionalImages": item.get("additionalImages", []) or [],
        "description": item.get("description", ""),
        "productUrl": item.get("productUrl", ""),
        "currency": item.get("currency", "USD"),
        "storeId": item.get("storeId"),
        "videoUrl": item.get("videoUrl"),
        "source": "apify"
    }

def extract_image_map(items: List[dict]) -> Dict[str, List[str]]:
    mapping = {}
    for item in items:
        url = item.get("productUrl", "")
        extra = item.get("additionalImages", []) or []
        if url and extra:
            mapping[url] = extra
    return mapping

# ═══════════════════════════════════════════════════════════════
# MOCK DATA
# ═══════════════════════════════════════════════════════════════
MOCK_SEARCH = [
    {
        "id": "601099769178067",
        "title": "2pcs Men's Suit Fashion Set Jacket Suit and Trousers",
        "price": "$17.99",
        "originalPrice": "$24.99",
        "discountPercentage": 28,
        "rating": 4.7,
        "totalReviews": "437",
        "salesCount": "7.4K+",
        "imageUrl": "https://img.kwcdn.com/product/fancy/4b9f58cb-9d38-4d66-9a6e-d4be3e179647.jpg",
        "additionalImages": [
            "https://img.kwcdn.com/product/fancy/da7dd1b0-2479-4cb4-b808-ea9027e279b0.jpg",
            "https://img.kwcdn.com/product/fancy/3f690c9d-a63e-4a84-a7a3-936a27a26173.jpg"
        ],
        "description": "Elegant business casual banquet party suit set",
        "productUrl": "https://www.temu.com/goods.html?goods_id=601099769178067",
        "currency": "USD",
        "source": "mock"
    },
    {
        "id": "601099952001588",
        "title": "Men's 3pcs Formal Suit Set - Solid Color",
        "price": "$24.99",
        "originalPrice": "$31.99",
        "discountPercentage": 22,
        "rating": 4.7,
        "totalReviews": "52",
        "salesCount": "1.2K+",
        "imageUrl": "https://img.kwcdn.com/product/fancy/9873815e-3e00-4845-a34e-adbe89bccf0c.jpg",
        "additionalImages": [],
        "description": "Lightweight design formal suit for business events",
        "productUrl": "https://www.temu.com/goods.html?goods_id=601099952001588",
        "currency": "USD",
        "source": "mock"
    },
    {
        "id": "601100384222130",
        "title": "Women's Stylish Open Toe Sandals - Black & Crocodile Pattern",
        "price": "$14.72",
        "originalPrice": "$24.36",
        "discountPercentage": 40,
        "rating": 4.6,
        "totalReviews": "1.2K+",
        "salesCount": "3.2K+",
        "imageUrl": "https://img.kwcdn.com/product/fancy/b0726781-e777-4699-8fc3-1f0ebea85b7a.jpg",
        "additionalImages": [],
        "description": "Chunky heel shoes, mid-height summer casual wear",
        "productUrl": "https://www.temu.com/goods.html?goods_id=601100384222130",
        "currency": "USD",
        "source": "mock"
    }
]

MOCK_PRODUCT = {
    "id": "601099769178067",
    "title": "2pcs Men's Suit Fashion Set Jacket Suit and Trousers Elegant Business Casual Banquet Party",
    "price": 176,
    "originalPrice": 196.69,
    "discountPercentage": 1,
    "rating": 4.7,
    "totalReviews": "437",
    "salesCount": "7.4K+",
    "imageUrl": "https://img.kwcdn.com/product/fancy/4b9f58cb-9d38-4d66-9a6e-d4be3e179647.jpg",
    "additionalImages": [
        "https://img.kwcdn.com/product/fancy/4b9f58cb-9d38-4d66-9a6e-d4be3e179647.jpg",
        "https://img.kwcdn.com/product/fancy/da7dd1b0-2479-4cb4-b808-ea9027e279b0.jpg",
        "https://img.kwcdn.com/product/fancy/3f690c9d-a63e-4a84-a7a3-936a27a26173.jpg"
    ],
    "description": "2pcs mens suit fashion set jacket suit and trousers elegant business casual banquet party",
    "productUrl": "https://www.temu.com/goods.html?goods_id=601099769178067",
    "currency": "PLN",
    "storeId": 634418212822033,
    "videoUrl": "https://goods-vod.kwcdn.com/goods-video/b99e09f76414cc6c3aafc51a034430731169f12d.f30.mp4",
    "source": "mock"
}

# ═══════════════════════════════════════════════════════════════
# LIFESPAN (FastAPI modern startup)
# ═══════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Temu Scraper API", version="2.1.1", lifespan=lifespan)

# ═══════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════
@app.get("/")
async def root():
    return {
        "service": "Temu Scraper API",
        "version": "2.1.1",
        "source": "Apify Primary (Multi-Image)",
        "endpoints": ["/search", "/product", "/stats"]
    }

@app.get("/search")
async def search(
    q: str = Query(..., description="Search keyword"),
    limit: int = Query(3, ge=1, le=50, description="Max results")
):
    cache_key = f"{q.strip().lower()}:{limit}"
    cached = get_cache("search_cache", cache_key)
    if cached:
        return {"source": "cache", "count": len(cached), "results": cached}

    # 1. Apify Search
    run_input = {
        "searchQueries": [q],
        "currency": "USD",
        "maxResults": limit
    }
    items = run_apify_sync(ACTOR_SEARCH, run_input, timeout=90)

    if not items:
        return {"source": "mock", "count": len(MOCK_SEARCH[:limit]), "results": MOCK_SEARCH[:limit]}

    # 2. Fetch Additional Images (Multi-Image)
    product_urls = [item.get("link_url", "") for item in items if item.get("link_url")]
    image_map = {}

    if product_urls and len(product_urls) <= 10:
        product_input = {"startUrls": product_urls}
        product_items = run_apify_sync(ACTOR_PRODUCT, product_input, timeout=120)
        if product_items:
            image_map = extract_image_map(product_items)

    # 3. Normalize & Cache
    results = normalize_search(items, image_map)[:limit]
    set_cache("search_cache", cache_key, results)

    return {"source": "apify", "count": len(results), "results": results}

@app.get("/product")
async def product(
    url: str = Query(..., description="Full Temu product URL")
):
    cached = get_cache("product_cache", url)
    if cached:
        return {"source": "cache", "data": cached}

    run_input = {"startUrls": [url]}
    items = run_apify_sync(ACTOR_PRODUCT, run_input, timeout=60)

    if items:
        data = normalize_product(items)
        if data:
            set_cache("product_cache", url, data)
            return {"source": "apify", "data": data}

    return {"source": "mock", "data": MOCK_PRODUCT}

@app.get("/stats")
async def stats():
    conn = _get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM search_cache")
    search_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM product_cache")
    product_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM search_cache WHERE created_at > ?", ((datetime.now() - timedelta(days=1)).isoformat(),))
    search_24h = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM product_cache WHERE created_at > ?", ((datetime.now() - timedelta(days=1)).isoformat(),))
    product_24h = c.fetchone()[0]
    conn.close()

    return {
        "cache_ttl_days": CACHE_DAYS,
        "search_cache_entries": search_count,
        "product_cache_entries": product_count,
        "search_cache_24h": search_24h,
        "product_cache_24h": product_24h,
        "apify_configured": bool(APIFY_API_TOKEN),
        "apify_actors": {
            "search": ACTOR_SEARCH,
            "product": ACTOR_PRODUCT
        }
    }

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)}
    )
