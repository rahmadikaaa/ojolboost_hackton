import requests
import json

url = "https://ojolboost-mams-319306541900.asia-southeast1.run.app/mcp/call"

payload = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
        "name": "list_upcoming_events",
        "arguments": {
            "max_results": 2
        }
    }
}

try:
    print(f"Mengirim request ke: {url}...")
    response = requests.post(url, json=payload)
    
    print(f"Status Code: {response.status_code}")
    print("Response JSON:")
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"Gagal mengirim request: {e}")
