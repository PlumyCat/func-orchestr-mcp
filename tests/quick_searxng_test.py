#!/usr/bin/env python3
"""
Test rapide de SearXNG local
Script simple pour vérifier rapidement l'état de l'instance
"""

import requests
import json
import sys


def quick_test():
    """Test rapide de SearXNG"""
    url = "http://127.0.0.1:8080/search"
    params = {
        'q': 'test',
        'format': 'json'
    }
    
    try:
        print("🔍 Test rapide SearXNG...")
        response = requests.get(url, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            results_count = len(data.get('results', []))
            print(f"✅ SearXNG OK - {results_count} résultats")
            return True
        else:
            print(f"❌ Erreur HTTP: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Impossible de se connecter à SearXNG")
        print("💡 Vérifiez que le container Docker est démarré")
        return False
    except requests.exceptions.Timeout:
        print("❌ Timeout - SearXNG ne répond pas")
        return False
    except Exception as e:
        print(f"❌ Erreur: {e}")
        return False


if __name__ == "__main__":
    success = quick_test()
    sys.exit(0 if success else 1)
