#!/usr/bin/env python3
"""
Script pour tester les fonctions Azure avec retry automatique
pour gérer les timeouts de démarrage à froid.
"""

import requests
import time
import json
from typing import Optional, Dict, Any

def test_with_retry(
    url: str,
    method: str = "POST",
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: int = 30
) -> Optional[requests.Response]:
    """
    Teste un endpoint avec retry automatique en cas de timeout.
    
    Args:
        url: URL à tester
        method: Méthode HTTP (GET, POST, etc.)
        data: Données JSON à envoyer
        headers: Headers HTTP
        max_retries: Nombre maximum de tentatives
        retry_delay: Délai entre les tentatives (secondes)
        timeout: Timeout par requête (secondes)
    
    Returns:
        Response de la dernière tentative réussie ou None
    """
    if headers is None:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
    for attempt in range(max_retries):
        try:
            print(f"Tentative {attempt + 1}/{max_retries}: {method} {url}")
            
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method.upper() == "POST":
                response = requests.post(url, json=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Méthode HTTP non supportée: {method}")
            
            print(f"✅ Succès! Status: {response.status_code}")
            print(f"Response: {response.text[:200]}{'...' if len(response.text) > 200 else ''}")
            return response
            
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout sur la tentative {attempt + 1}")
            if attempt < max_retries - 1:
                print(f"Retry dans {retry_delay} secondes...")
                time.sleep(retry_delay)
            else:
                print("❌ Échec après toutes les tentatives (timeout)")
                
        except requests.exceptions.ConnectionError as e:
            print(f"🔌 Erreur de connexion sur la tentative {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                print(f"Retry dans {retry_delay} secondes...")
                time.sleep(retry_delay)
            else:
                print("❌ Échec après toutes les tentatives (connexion)")
                
        except Exception as e:
            print(f"❌ Erreur sur la tentative {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                print(f"Retry dans {retry_delay} secondes...")
                time.sleep(retry_delay)
            else:
                print("❌ Échec après toutes les tentatives (erreur)")
    
    return None

def test_list_images():
    """Test de la fonction list-images-test avec retry."""
    host = "http://localhost:7071"
    url = f"{host}/api/list-images-test"
    data = {
        "prompt": "List my images in storage.",
        "user_id": "user123"
    }
    
    print("=== Test de list-images-test avec retry ===")
    response = test_with_retry(url, "POST", data)
    
    if response:
        try:
            result = response.json()
            print("\n📋 Résultat détaillé:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except:
            print(f"\n📄 Réponse brute: {response.text}")
    else:
        print("\n❌ Test échoué après tous les retries")

def test_init_user():
    """Test de la fonction init-user-test avec retry."""
    host = "http://localhost:7071"
    url = f"{host}/api/init-user-test"
    data = {
        "prompt": "init my folder on blob.",
        "user_id": "user591"
    }
    
    print("\n=== Test de init-user-test avec retry ===")
    response = test_with_retry(url, "POST", data)
    
    if response:
        try:
            result = response.json()
            print("\n📋 Résultat détaillé:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except:
            print(f"\n📄 Réponse brute: {response.text}")
    else:
        print("\n❌ Test échoué après tous les retries")

def test_health_check():
    """Test du health check."""
    host = "http://localhost:7071"
    url = f"{host}/api/ping"
    
    print("\n=== Test du health check ===")
    response = test_with_retry(url, "GET", max_retries=2, timeout=10)
    
    if response:
        print(f"✅ Health check OK: {response.text}")
        return True
    else:
        print("❌ Health check échoué")
        return False

if __name__ == "__main__":
    # Test d'abord le health check
    if test_health_check():
        # Si le health check passe, on peut tester les fonctions
        test_list_images()
        test_init_user()
    else:
        print("\n⚠️ La fonction Azure semble ne pas être démarrée.")
        print("Lancez 'func start' dans un autre terminal et réessayez.")
