#!/bin/bash
# Script bash simple pour reset des queues Azurite

cd "$(dirname "$0")"

# Activer l'environnement virtuel s'il existe
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "✅ Environnement virtuel activé"
fi

# Exécuter le script Python
python reset_queues.py "$@"
