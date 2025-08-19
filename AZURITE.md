# Azurite Storage Emulator

Azurite est l'émulateur de stockage Azure local qui simule les services Azure Storage pour le développement.

## Installation

Azurite est installé globalement via npm :
```bash
npm install -g azurite
```

## Utilisation

### Démarrage rapide

```bash
# Démarrer Azurite
./start-azurite.sh

# Arrêter Azurite
./stop-azurite.sh
```

### Démarrage manuel

```bash
# Démarrer tous les services (Blob, Queue, Table)
azurite --location ./azurite-data

# Ou avec des ports personnalisés
azurite --location ./azurite-data --blobPort 10000 --queuePort 10001 --tablePort 10002
```

## Configuration

Votre `local.settings.json` est déjà configuré :
```json
{
  "AzureWebJobsStorage": "UseDevelopmentStorage=true"
}
```

Cette configuration utilise automatiquement Azurite avec les paramètres par défaut.

## Endpoints locaux

Quand Azurite est démarré, les services sont disponibles sur :

- **Blob Storage**: `http://127.0.0.1:10000`
- **Queue Storage**: `http://127.0.0.1:10001`
- **Table Storage**: `http://127.0.0.1:10002`

## Comptes de test

Azurite utilise des comptes de développement prédéfinis :

**Account name**: `devstoreaccount1`
**Account key**: `Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==`

## Outils de gestion

### Azure Storage Explorer
Vous pouvez connecter Azure Storage Explorer à Azurite :
1. Ouvrir Azure Storage Explorer
2. Cliquer sur "Add an account"
3. Sélectionner "Attach to a local emulator"
4. Utiliser les endpoints ci-dessus

### CLI Azure
```bash
# Lister les conteneurs blob
az storage container list --connection-string "UseDevelopmentStorage=true"

# Créer un conteneur
az storage container create --name "test" --connection-string "UseDevelopmentStorage=true"
```

## Données persistantes

Les données Azurite sont stockées dans `./azurite-data/` et sont exclues du git via `.gitignore`.

## Troubleshooting

### Port déjà utilisé
Si les ports par défaut sont occupés, modifiez `start-azurite.sh` pour utiliser d'autres ports.

### Permissions
Assurez-vous que les scripts sont exécutables :
```bash
chmod +x start-azurite.sh stop-azurite.sh
```

### Reset des données
Pour repartir avec un stockage vide :
```bash
./stop-azurite.sh
rm -rf ./azurite-data
./start-azurite.sh
```
