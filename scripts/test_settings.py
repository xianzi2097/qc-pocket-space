"""Test settings PUT endpoint (no requests lib)"""
import urllib.request, urllib.error, json, os

BASE = "http://127.0.0.1:8000"

def api(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# 1. Login
code, data = api("POST", "/api/login", {"username": "Arin", "password": "123456"})
print("LOGIN:", code, data.get("user", {}).get("username", "FAIL"))
token = data.get("token")
if not token:
    print("No token, exiting"); exit(1)

# 2. GET settings
code, data = api("GET", "/api/user/settings", token=token)
print("\nGET settings:", code, json.dumps(data, ensure_ascii=False))

# 3. PUT sidebarExpanded=False
code, data = api("PUT", "/api/user/settings", {"sidebarExpanded": False}, token=token)
print("\nPUT sidebarExpanded=False:", code, json.dumps(data, ensure_ascii=False))

# 4. GET again
code, data = api("GET", "/api/user/settings", token=token)
print("\nGET after PUT:", code, json.dumps(data, ensure_ascii=False))

# 5. Check file on disk
fp = r"C:\QC-Packet\data\users\teachers\2\settings.json"
print("\nFile on disk:", open(fp).read() if os.path.exists(fp) else "NOT FOUND")
