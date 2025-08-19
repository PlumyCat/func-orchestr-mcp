#!/usr/bin/env python3
"""
Script de test pour l'instance SearXNG locale
Teste la connectivité et les fonctionnalités de base de SearXNG
"""

import requests
import json
import time
from urllib.parse import urlencode
from typing import Dict, Any, Optional


class SearXNGTester:
    """Classe pour tester l'instance SearXNG locale"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.base_url = base_url.rstrip('/')
        self.search_url = f"{self.base_url}/search"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SearXNG-Tester/1.0'
        })
    
    def test_connectivity(self) -> bool:
        """Teste la connectivité de base à SearXNG"""
        try:
            response = self.session.get(self.base_url, timeout=5)
            print(f"✅ Connectivité OK - Status: {response.status_code}")
            return response.status_code == 200
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur de connectivité: {e}")
            return False
    
    def test_search_endpoint(self, query: str = "test", format: str = "json") -> Optional[Dict[str, Any]]:
        """Teste l'endpoint de recherche"""
        params = {
            'q': query,
            'format': format
        }
        
        try:
            print(f"🔍 Test de recherche: '{query}'")
            response = self.session.get(self.search_url, params=params, timeout=10)
            
            if response.status_code == 200:
                print(f"✅ Recherche réussie - Status: {response.status_code}")
                
                if format == "json":
                    try:
                        data = response.json()
                        print(f"📊 Résultats trouvés: {len(data.get('results', []))}")
                        return data
                    except json.JSONDecodeError as e:
                        print(f"❌ Erreur de parsing JSON: {e}")
                        return None
                else:
                    print(f"📄 Réponse reçue (format: {format})")
                    return {"content": response.text}
            else:
                print(f"❌ Erreur HTTP: {response.status_code}")
                print(f"📄 Réponse: {response.text[:200]}...")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur de requête: {e}")
            return None
    
    def test_search_with_engines(self, query: str = "python", engines: list = None) -> Optional[Dict[str, Any]]:
        """Teste la recherche avec des moteurs spécifiques"""
        if engines is None:
            engines = ["google", "bing", "duckduckgo"]
        
        params = {
            'q': query,
            'format': 'json',
            'engines': ','.join(engines)
        }
        
        try:
            print(f"🔍 Test avec moteurs: {engines}")
            response = self.session.get(self.search_url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Recherche avec moteurs réussie")
                print(f"📊 Résultats: {len(data.get('results', []))}")
                return data
            else:
                print(f"❌ Erreur: {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur: {e}")
            return None
    
    def test_available_engines(self) -> Optional[Dict[str, Any]]:
        """Teste la récupération des moteurs disponibles"""
        try:
            response = self.session.get(f"{self.base_url}/preferences", timeout=5)
            if response.status_code == 200:
                print("✅ Endpoint preferences accessible")
                return {"status": "available"}
            else:
                print(f"❌ Preferences non accessible: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur preferences: {e}")
            return None
    
    def test_health_check(self) -> bool:
        """Teste la santé de l'instance"""
        try:
            # Test simple avec une requête courte
            params = {'q': 'test', 'format': 'json'}
            response = self.session.get(self.search_url, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if 'results' in data:
                    print("✅ Instance SearXNG en bonne santé")
                    return True
                else:
                    print("⚠️ Réponse inattendue de SearXNG")
                    return False
            else:
                print(f"❌ Instance non disponible: {response.status_code}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur de santé: {e}")
            return False
    
    def run_full_test_suite(self):
        """Exécute la suite complète de tests"""
        print("🚀 Démarrage des tests SearXNG")
        print("=" * 50)
        
        # Test 1: Connectivité
        print("\n1️⃣ Test de connectivité")
        if not self.test_connectivity():
            print("❌ Impossible de se connecter à SearXNG. Arrêt des tests.")
            return False
        
        # Test 2: Endpoint preferences
        print("\n2️⃣ Test des préférences")
        self.test_available_engines()
        
        # Test 3: Recherche simple
        print("\n3️⃣ Test de recherche simple")
        result1 = self.test_search_endpoint("Azure Functions")
        if not result1:
            print("❌ Échec du test de recherche simple")
            return False
        
        # Test 4: Recherche avec moteurs spécifiques
        print("\n4️⃣ Test avec moteurs spécifiques")
        result2 = self.test_search_with_engines("Python programming")
        if not result2:
            print("❌ Échec du test avec moteurs spécifiques")
            return False
        
        # Test 5: Recherche en français
        print("\n5️⃣ Test de recherche en français")
        result3 = self.test_search_endpoint("développement web")
        if not result3:
            print("❌ Échec du test de recherche en français")
            return False
        
        # Test 6: Santé de l'instance
        print("\n6️⃣ Test de santé")
        if not self.test_health_check():
            print("❌ Échec du test de santé")
            return False
        
        print("\n" + "=" * 50)
        print("✅ Tous les tests SearXNG sont passés avec succès!")
        return True


def main():
    """Fonction principale"""
    print("🧪 Testeur SearXNG Local")
    print("URL: http://127.0.0.1:8080")
    print("=" * 50)
    
    # Création du testeur
    tester = SearXNGTester()
    
    # Exécution des tests
    success = tester.run_full_test_suite()
    
    if success:
        print("\n🎉 Instance SearXNG prête pour la production!")
        print("💡 URL de recherche: http://127.0.0.1:8080/search?format=json")
    else:
        print("\n⚠️ Problèmes détectés avec l'instance SearXNG")
        print("🔧 Vérifiez que le container Docker est démarré et accessible")


if __name__ == "__main__":
    main()
