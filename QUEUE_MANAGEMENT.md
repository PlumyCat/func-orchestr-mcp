# Gestion des Queues Azurite

Ce document décrit les outils pour gérer les queues Azure Storage dans l'environnement de développement local (Azurite).

## Scripts disponibles

### 1. Script Python : `reset_queues.py`

Script complet avec toutes les fonctionnalités :

```bash
# Vider les queues du projet (mcpjobs-copilot, mcpjobs, etc.)
python reset_queues.py

# Lister toutes les queues existantes
python reset_queues.py --list

# Vider TOUTES les queues (attention !)
python reset_queues.py --all

# Aide
python reset_queues.py --help
```

### 2. Script Bash : `reset-queues.sh`

Wrapper bash qui active automatiquement l'environnement virtuel :

```bash
# Vider les queues du projet
./reset-queues.sh

# Lister toutes les queues
./reset-queues.sh --list

# Vider toutes les queues
./reset-queues.sh --all
```

## Queues du projet

Le projet utilise ces queues Azure :

- **`mcpjobs-copilot`** : Queue principale pour les jobs asynchrones (ask/start, orchestrate/start)
- **`mcpjobs-copilot-poison`** : Queue poison pour les messages qui échouent 5 fois
- **`mcpjobs`** : Queue legacy (ancienne version)
- **`mcpjobs-poison`** : Queue poison legacy

## Problèmes courants

### Messages en queue poison

Quand vous voyez ce message dans les logs :
```
Message has reached MaxDequeueCount of 5. Moving message to queue 'mcpjobs-copilot-poison'.
```

**Solution :**
```bash
./reset-queues.sh  # Vide les queues poison et principales
```

### Trop de queues temporaires

Si vous avez beaucoup de queues `mcp-backplane-*` :
```bash
./reset-queues.sh --all  # ⚠️ Attention : vide TOUTES les queues
```

### Vérifier l'état des queues

```bash
./reset-queues.sh --list  # Voir toutes les queues et leur état
```

## Configuration Azurite

Le script utilise la configuration Azurite par défaut :
- **Endpoint Queue** : `http://127.0.0.1:10001`
- **Account** : `devstoreaccount1`
- **Container** : `jobs` (pour les blobs de statut)

## Utilisation avec Docker

Si vous utilisez Azure Functions dans Docker, assurez-vous que :
1. Azurite est démarré et accessible
2. L'environnement virtuel Python est activé
3. Les modules `azure-storage-queue` sont installés

## Dépannage

### Module manquant
```bash
pip install azure-storage-queue
```

### Azurite non accessible
```bash
# Vérifier qu'Azurite répond
curl http://127.0.0.1:10001/devstoreaccount1
```

### Permissions
```bash
chmod +x reset_queues.py reset-queues.sh
```
