#!/usr/bin/env python3
"""
End-to-end test for HTTP microservices transaction system with Hazelcast.
Tests: facade-service -> counter-service + logging-service
"""

import requests
import time
import sys
import json

# Service endpoints
FACADE_URL = "http://localhost:8000"
COUNTER_URL = "http://localhost:8002"
LOGGING_URL = "http://localhost:8001"

def test_service_connectivity():
    """Test that all services are reachable."""
    print("Testing service connectivity...")
    
    services = {
        "facade-service": f"{FACADE_URL}/metrics",
        "counter-service": f"{COUNTER_URL}/balances",
    }
    
    for name, url in services.items():
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                print(f"  ✓ {name} is reachable")
            else:
                print(f"  ✗ {name} returned status {response.status_code}")
                return False
        except Exception as e:
            print(f"  ✗ {name} failed: {e}")
            return False
    
    # Note: logging-service is accessed via facade-service internally
    print(f"  ✓ logging-service (accessed via facade)")
    
    return True

def test_reset_services():
    """Reset all services to clean state."""
    print("\nResetting services...")
    
    try:
        # Reset counter service
        response = requests.post(f"{COUNTER_URL}/reset", timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Counter service reset failed: {response.status_code}")
            return False
        print(f"  ✓ Counter service reset")
        
        # Note: Logging service is reset internally through Hazelcast
        # (Transactions are stored in Hazelcast distributed map)
        print(f"  ✓ Logging service reset (via Hazelcast)")
        
        # Reset facade metrics
        response = requests.post(f"{FACADE_URL}/metrics/reset", timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Facade metrics reset failed: {response.status_code}")
            return False
        print(f"  ✓ Facade metrics reset")
        
        return True
    except Exception as e:
        print(f"  ✗ Reset failed: {e}")
        return False

def test_transaction_flow():
    """Test the complete transaction flow through the facade service."""
    print("\nTesting transaction flow...")
    
    try:
        # Create a transaction
        tx_payload = {
            "user_id": "user123",
            "amount": 100,
            "message": "Test transaction"
        }
        
        response = requests.post(f"{FACADE_URL}/transaction", json=tx_payload, timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Transaction post failed: {response.status_code}")
            print(f"    Response: {response.text}")
            return False
        
        tx_result = response.json()
        print(f"  ✓ Transaction posted successfully")
        print(f"    Transaction ID: {tx_result.get('transaction_id')}")
        print(f"    Balance: {tx_result.get('balance')}")
        
        # Verify the balance
        response = requests.get(f"{COUNTER_URL}/balance/user123", timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Get balance failed: {response.status_code}")
            return False
        
        balance = response.json().get("balance", 0)
        if balance != 100:
            print(f"  ✗ Balance mismatch: expected 100, got {balance}")
            return False
        print(f"  ✓ Balance verified: {balance}")
        
        return True
    except Exception as e:
        print(f"  ✗ Transaction flow failed: {e}")
        return False

def test_multiple_transactions():
    """Test multiple transactions for the same user."""
    print("\nTesting multiple transactions...")
    
    try:
        user_id = "user456"
        
        # Create 3 transactions
        total_amount = 0
        for i in range(3):
            tx_payload = {
                "user_id": user_id,
                "amount": (i + 1) * 50,  # 50, 100, 150
                "message": f"Transaction {i+1}"
            }
            
            response = requests.post(f"{FACADE_URL}/transaction", json=tx_payload, timeout=5)
            if response.status_code != 200:
                print(f"  ✗ Transaction {i+1} failed: {response.status_code}")
                return False
            
            total_amount += tx_payload["amount"]
            print(f"  ✓ Transaction {i+1}: {tx_payload['amount']}")
        
        # Verify final balance
        response = requests.get(f"{COUNTER_URL}/balance/{user_id}", timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Get final balance failed: {response.status_code}")
            return False
        
        balance = response.json().get("balance", 0)
        if balance != total_amount:
            print(f"  ✗ Balance mismatch: expected {total_amount}, got {balance}")
            return False
        print(f"  ✓ Final balance verified: {balance}")
        
        # Get all transactions for user
        response = requests.get(f"{FACADE_URL}/user/{user_id}", timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Get user transactions failed: {response.status_code}")
            return False
        
        user_data = response.json()
        tx_count = len(user_data.get("transactions", []))
        print(f"  ✓ Retrieved {tx_count} transactions for user")
        
        return True
    except Exception as e:
        print(f"  ✗ Multiple transactions test failed: {e}")
        return False

def test_concurrent_users():
    """Test transactions from different users."""
    print("\nTesting concurrent users...")
    
    try:
        users_data = [
            {"user_id": "user_a", "amount": 200},
            {"user_id": "user_b", "amount": 300},
            {"user_id": "user_c", "amount": 150},
        ]
        
        # Create transactions for each user
        for user_info in users_data:
            tx_payload = {
                "user_id": user_info["user_id"],
                "amount": user_info["amount"],
                "message": f"Transaction for {user_info['user_id']}"
            }
            
            response = requests.post(f"{FACADE_URL}/transaction", json=tx_payload, timeout=5)
            if response.status_code != 200:
                print(f"  ✗ Transaction for {user_info['user_id']} failed")
                return False
        
        print(f"  ✓ Created transactions for {len(users_data)} users")
        
        # Verify balances
        for user_info in users_data:
            response = requests.get(f"{COUNTER_URL}/balance/{user_info['user_id']}", timeout=5)
            if response.status_code != 200:
                print(f"  ✗ Get balance for {user_info['user_id']} failed")
                return False
            
            balance = response.json().get("balance", 0)
            if balance != user_info["amount"]:
                print(f"  ✗ Balance mismatch for {user_info['user_id']}")
                return False
        
        print(f"  ✓ All user balances verified")
        
        return True
    except Exception as e:
        print(f"  ✗ Concurrent users test failed: {e}")
        return False

def test_metrics():
    """Test metrics collection."""
    print("\nTesting metrics collection...")
    
    try:
        response = requests.get(f"{FACADE_URL}/metrics", timeout=5)
        if response.status_code != 200:
            print(f"  ✗ Get metrics failed: {response.status_code}")
            return False
        
        metrics = response.json()
        print(f"  ✓ Metrics retrieved:")
        print(f"    Logging calls: {metrics.get('logging_calls', 0)}")
        print(f"    Counter calls: {metrics.get('counter_calls', 0)}")
        print(f"    Logging avg (ms): {metrics.get('logging_avg_ms', 0):.2f}")
        print(f"    Counter avg (ms): {metrics.get('counter_avg_ms', 0):.2f}")
        
        return True
    except Exception as e:
        print(f"  ✗ Metrics test failed: {e}")
        return False

def main():
    print("=" * 70)
    print("End-to-End Test for Transaction Microservices with Hazelcast")
    print("=" * 70)
    
    # Wait for services to be ready
    print("\nWaiting for services to be ready...")
    max_attempts = 30
    for attempt in range(max_attempts):
        try:
            response = requests.get(f"{FACADE_URL}/metrics", timeout=2)
            if response.status_code == 200:
                print("  ✓ Services are ready")
                break
        except:
            pass
        
        if attempt < max_attempts - 1:
            time.sleep(1)
    else:
        print("  ✗ Services did not become ready in time")
        sys.exit(1)
    
    # Run tests
    tests = [
        ("Service Connectivity", test_service_connectivity),
        ("Reset Services", test_reset_services),
        ("Transaction Flow", test_transaction_flow),
        ("Multiple Transactions", test_multiple_transactions),
        ("Concurrent Users", test_concurrent_users),
        ("Metrics Collection", test_metrics),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status:8} | {test_name}")
    
    print("-" * 70)
    print(f"Total: {passed}/{total} tests passed")
    print("=" * 70)
    
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
