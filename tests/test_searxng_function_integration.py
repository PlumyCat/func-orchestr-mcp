#!/usr/bin/env python3
"""
Test d'intégration SearXNG pour Azure Functions
Simule l'utilisation exacte de SearXNG dans votre configuration
"""

import requests
import json
import time
from typing import Dict, Any, Optional


class SearXNGFunctionTester:
    """Testeur pour l'intégration SearXNG avec Azure Functions"""
    
    def __init__(self):
        # Configuration exacte de local.settings.json
        self.websearch_url = "http://127.0.0.1:8080/search?format=json"
        self.timeout_seconds = 6
        self.max_results = 8
        self.max_chars = 6000
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Azure-Functions-SearXNG/1.0',
            'Accept': 'application/json'
        })
    
    def search_web(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Effectue une recherche web comme le ferait Azure Functions
        """
        params = {
            'q': query,
            'format': 'json'
        }
        
        try:
            print(f"🔍 Recherche: '{query}'")
            print(f"⏱️ Timeout: {self.timeout_seconds}s")
            
            response = self.session.get(
                self.websearch_url, 
                params=params, 
                timeout=self.timeout_seconds
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                print(f"✅ Succès - {len(results)} résultats trouvés")
                
                # Simulation du traitement Azure Functions
                processed_results = self._process_results(results)
                
                return {
                    'success': True,
                    'query': query,
                    'results_count': len(results),
                    'processed_results': processed_results,
                    'raw_data': data
                }
            else:
                print(f"❌ Erreur HTTP: {response.status_code}")
                return {
                    'success': False,
                    'error': f"HTTP {response.status_code}",
                    'query': query
                }
                
        except requests.exceptions.Timeout:
            print(f"❌ Timeout après {self.timeout_seconds}s")
            return {
                'success': False,
                'error': 'timeout',
                'query': query
            }
        except requests.exceptions.ConnectionError:
            print("❌ Erreur de connexion")
            return {
                'success': False,
                'error': 'connection_error',
                'query': query
            }
        except Exception as e:
            print(f"❌ Erreur: {e}")
            return {
                'success': False,
                'error': str(e),
                'query': query
            }
    
    def _process_results(self, results: list) -> list:
        """
        Traite les résultats comme le ferait Azure Functions
        """
        processed = []
        total_chars = 0
        
        for i, result in enumerate(results[:self.max_results]):
            if total_chars >= self.max_chars:
                break
                
            # Extraction des informations importantes
            processed_result = {
                'title': result.get('title', ''),
                'url': result.get('url', ''),
                'content': result.get('content', '')[:200] + '...' if len(result.get('content', '')) > 200 else result.get('content', ''),
                'engine': result.get('engine', 'unknown')
            }
            
            processed.append(processed_result)
            total_chars += len(processed_result['title']) + len(processed_result['content'])
        
        return processed
    
    def test_function_integration(self):
        """
        Test complet de l'intégration fonctionnelle
        """
        print("🚀 Test d'intégration SearXNG pour Azure Functions")
        print("=" * 60)
        print(f"📡 URL: {self.websearch_url}")
        print(f"⏱️ Timeout: {self.timeout_seconds}s")
        print(f"📊 Max résultats: {self.max_results}")
        print(f"📝 Max caractères: {self.max_chars}")
        print("=" * 60)
        
        # Tests avec différentes requêtes
        test_queries = [
            "Azure Functions Python",
            "SearXNG documentation",
            "Docker container management",
            "Python web development"
        ]
        
        all_success = True
        
        for i, query in enumerate(test_queries, 1):
            print(f"\n{i}️⃣ Test {i}/{len(test_queries)}")
            result = self.search_web(query)
            
            if result['success']:
                print(f"   ✅ {result['results_count']} résultats traités")
                if result['processed_results']:
                    first_result = result['processed_results'][0]
                    print(f"   📄 Premier résultat: {first_result['title'][:50]}...")
            else:
                print(f"   ❌ Échec: {result['error']}")
                all_success = False
        
        print("\n" + "=" * 60)
        if all_success:
            print("🎉 Tous les tests d'intégration sont passés!")
            print("💡 SearXNG est prêt pour Azure Functions")
        else:
            print("⚠️ Certains tests ont échoué")
            print("🔧 Vérifiez la configuration SearXNG")
        
        return all_success
    
    def test_performance(self):
        """
        Test de performance avec mesure des temps de réponse
        """
        print("\n⚡ Test de performance")
        print("-" * 30)
        
        query = "performance test"
        times = []
        
        for i in range(3):
            start_time = time.time()
            result = self.search_web(query)
            end_time = time.time()
            
            response_time = end_time - start_time
            times.append(response_time)
            
            print(f"Test {i+1}: {response_time:.2f}s")
        
        avg_time = sum(times) / len(times)
        print(f"⏱️ Temps moyen: {avg_time:.2f}s")
        
        if avg_time < self.timeout_seconds:
            print("✅ Performance acceptable")
        else:
            print("⚠️ Performance lente - risque de timeout")


def main():
    """Fonction principale"""
    tester = SearXNGFunctionTester()
    
    # Test d'intégration
    success = tester.test_function_integration()
    
    if success:
        # Test de performance si l'intégration fonctionne
        tester.test_performance()
    
    print("\n📋 Résumé de la configuration:")
    print(f"   URL: {tester.websearch_url}")
    print(f"   Timeout: {tester.timeout_seconds}s")
    print(f"   Max résultats: {tester.max_results}")
    print(f"   Max caractères: {tester.max_chars}")


if __name__ == "__main__":
    main()
