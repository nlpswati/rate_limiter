import requests
import time
import sys

def test_rate_limiting_and_auto_deactivation():
    """Test script to demonstrate rate limiting and auto-deactivation"""
    
    # You'll need to replace this with an actual API key from your database
    # You can get one by registering a user and checking the database or welcome page
    api_key = input("Enter your API key to test (or press Enter to use 'KEY_BASIC'): ").strip()
    if not api_key:
        api_key = "KEY_BASIC"
    
    url = "http://127.0.0.1:8000/data"
    headers = {"x-api-key": api_key}
    
    print(f"Testing API key: {api_key}")
    print("=" * 50)
    
    # Test normal requests first
    print("1. Testing normal requests...")
    for i in range(5):
        try:
            res = requests.get(url, headers=headers)
            print(f"Request {i+1}: {res.status_code} - {res.json() if res.status_code == 200 else res.text}")
            time.sleep(0.1)
        except Exception as e:
            print(f"Request {i+1}: Error - {e}")
    
    print("\n2. Testing rate limiting (sending many requests quickly)...")
    rate_limited_count = 0
    success_count = 0
    
    for i in range(50):  # Send 50 requests quickly to trigger rate limiting
        try:
            res = requests.get(url, headers=headers)
            if res.status_code == 429:
                rate_limited_count += 1
                if rate_limited_count <= 5:  # Only show first 5 rate limit responses
                    print(f"Request {i+1}: {res.status_code} - Rate limit exceeded")
            elif res.status_code == 200:
                success_count += 1
            elif res.status_code == 403:
                print(f"Request {i+1}: {res.status_code} - API key deactivated!")
                break
            else:
                print(f"Request {i+1}: {res.status_code} - {res.text}")
            
            time.sleep(0.05)  # Small delay between requests
            
        except Exception as e:
            print(f"Request {i+1}: Error - {e}")
    
    print(f"\nResults:")
    print(f"- Successful requests: {success_count}")
    print(f"- Rate limited requests: {rate_limited_count}")
    print(f"- Total requests sent: {success_count + rate_limited_count}")
    
    print(f"\n3. Testing if API key is still active...")
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 403:
            print("✅ API key has been auto-deactivated!")
        elif res.status_code == 429:
            print("⚠️  API key is still active but rate limited")
        elif res.status_code == 200:
            print("✅ API key is still active and working")
        else:
            print(f"❓ Unexpected response: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ Error testing final status: {e}")

if __name__ == "__main__":
    test_rate_limiting_and_auto_deactivation()
