from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

OX_USER = os.getenv("OX_USER", "")
OX_PASS = os.getenv("OX_PASS", "")

@app.route('/')
def home():
    return {"message": "Temu API with Oxylabs", "status": "running"}

@app.route('/search', methods=['POST'])
def search():
    if not OX_USER or not OX_PASS:
        return jsonify({"error": "Oxylabs credentials not configured"}), 500
    
    data = request.get_json() or {}
    keyword = data.get('keyword', 'phone case')
    
    temu_url = f"https://www.temu.com/search_result.html?search_key={keyword.replace(' ', '+')}"
    
    try:
        r = requests.post(
            "https://realtime.oxylabs.io/v1/queries",
            auth=(OX_USER, OX_PASS),
            json={
                "source": "universal_ecommerce",
                "url": temu_url,
                "geo_location": "United States",
                "parse": True
            },
            timeout=60
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
