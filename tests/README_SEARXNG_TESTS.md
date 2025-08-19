# Tests SearXNG Local

Ce dossier contient les scripts de test pour votre instance SearXNG locale.

## Configuration

Votre instance SearXNG est configurée sur :
- **URL**: `http://127.0.0.1:8080`
- **Endpoint de recherche**: `http://127.0.0.1:8080/search?format=json`
- **Timeout**: 6 secondes
- **Max résultats**: 8
- **Max caractères**: 6000

## Scripts disponibles

### 1. Test rapide (`quick_searxng_test.py`)
Test simple et rapide pour vérifier la connectivité.

```bash
python3 tests/quick_searxng_test.py
```

### 2. Test complet (`test_searxng_local.py`)
Test complet avec toutes les fonctionnalités de SearXNG.

```bash
python3 tests/test_searxng_local.py
```

### 3. Test d'intégration (`test_searxng_function_integration.py`)
Test qui simule exactement l'utilisation dans Azure Functions.

```bash
python3 tests/test_searxng_function_integration.py
```

### 4. Script shell (`../test_searxng.sh`)
Script principal pour exécuter tous les types de tests.

```bash
# Test rapide (par défaut)
./test_searxng.sh

# Test complet
./test_searxng.sh full

# Test d'intégration
./test_searxng.sh integration

# Aide
./test_searxng.sh help
```

## Utilisation

### Test quotidien
Pour vérifier rapidement que SearXNG fonctionne :

```bash
./test_searxng.sh quick
```

### Test avant déploiement
Avant de déployer vos Azure Functions, exécutez le test d'intégration :

```bash
./test_searxng.sh integration
```

### Diagnostic complet
Si vous rencontrez des problèmes, exécutez le test complet :

```bash
./test_searxng.sh full
```

## Dépannage

### SearXNG non accessible
Si SearXNG n'est pas accessible :

1. Vérifiez que le container Docker est démarré :
   ```bash
   docker ps | grep searxng
   ```

2. Vérifiez les logs du container :
   ```bash
   docker logs <container_id>
   ```

3. Redémarrez le container si nécessaire :
   ```bash
   docker restart <container_id>
   ```

### Erreurs de timeout
Si vous obtenez des erreurs de timeout :

1. Vérifiez la charge du système
2. Augmentez le timeout dans `local.settings.json` si nécessaire
3. Vérifiez la connectivité réseau

### Erreurs de parsing JSON
Si vous obtenez des erreurs de parsing JSON :

1. Vérifiez que SearXNG retourne bien du JSON
2. Testez directement l'URL dans un navigateur
3. Vérifiez la configuration de SearXNG

## Intégration avec Azure Functions

Ces tests simulent exactement l'utilisation de SearXNG dans votre configuration Azure Functions :

- **URL**: Correspond à `WEBSEARCH_FUNCTION_URL` dans `local.settings.json`
- **Timeout**: Correspond à `WEBSEARCH_TIMEOUT_SECONDS`
- **Limites**: Respectent `WEBSEARCH_MAX_RESULTS` et `WEBSEARCH_MAX_CHARS`

## Dépendances

Les scripts nécessitent :
- Python 3.6+
- Module `requests`
- `curl` (pour le script shell)

Installation des dépendances Python :
```bash
pip install requests
```

## Exemples de sortie

### Test rapide réussi
```
🔍 Test rapide SearXNG...
✅ SearXNG OK - 8 résultats
```

### Test d'intégration réussi
```
🚀 Test d'intégration SearXNG pour Azure Functions
============================================================
📡 URL: http://127.0.0.1:8080/search?format=json
⏱️ Timeout: 6s
📊 Max résultats: 8
📝 Max caractères: 6000
============================================================

1️⃣ Test 1/4
🔍 Recherche: 'Azure Functions Python'
⏱️ Timeout: 6s
✅ Succès - 8 résultats trouvés
   ✅ 8 résultats traités
   📄 Premier résultat: Azure Functions Python - Microsoft Docs...

🎉 Tous les tests d'intégration sont passés!
💡 SearXNG est prêt pour Azure Functions
```
