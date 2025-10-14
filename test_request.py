import requests

url = "http://127.0.0.1:8000/data"
headers = {"x-api-key": "KEY_BASIC"}

res = requests.get(url, headers=headers)
print(res.status_code, res.json())
