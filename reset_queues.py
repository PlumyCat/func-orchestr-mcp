#!/usr/bin/env python3
"""
Script pour reset les queues Azurite (Azure Storage Emulator)
Usage: python reset_queues.py [--all]
"""

import argparse
import sys
from typing import List

try:
    from azure.storage.queue import QueueClient
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    print("❌ Module azure-storage-queue manquant. Installez avec:")
    print("   pip install azure-storage-queue")
    sys.exit(1)

# Configuration Azurite par défaut
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
)

# Queues du projet
PROJECT_QUEUES = [
    "mcpjobs-copilot",
    "mcpjobs-copilot-poison",
    "mcpjobs",
    "mcpjobs-poison",
]


def clear_queue(queue_name: str) -> bool:
    """Vide une queue spécifique."""
    try:
        queue_client = QueueClient.from_connection_string(
            AZURITE_CONNECTION_STRING, queue_name
        )
        
        # Vérifier si la queue existe
        properties = queue_client.get_queue_properties()
        message_count = properties.approximate_message_count
        
        if message_count == 0:
            print(f"✅ Queue '{queue_name}' déjà vide")
            return True
            
        # Vider la queue
        queue_client.clear_messages()
        print(f"✅ Queue '{queue_name}' vidée ({message_count} messages supprimés)")
        return True
        
    except ResourceNotFoundError:
        print(f"ℹ️  Queue '{queue_name}' n'existe pas")
        return True
        
    except Exception as e:
        print(f"❌ Erreur avec queue '{queue_name}': {e}")
        return False


def list_all_queues() -> List[str]:
    """Liste toutes les queues existantes."""
    try:
        from azure.storage.queue import QueueServiceClient
        queue_service = QueueServiceClient.from_connection_string(AZURITE_CONNECTION_STRING)
        
        queues = []
        for queue in queue_service.list_queues():
            queues.append(queue.name)
        return queues
        
    except Exception as e:
        print(f"❌ Impossible de lister les queues: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Reset des queues Azurite")
    parser.add_argument(
        "--all", 
        action="store_true", 
        help="Vider TOUTES les queues existantes (pas seulement celles du projet)"
    )
    parser.add_argument(
        "--list", 
        action="store_true", 
        help="Lister toutes les queues existantes"
    )
    
    args = parser.parse_args()
    
    print("🔄 Script de reset des queues Azurite")
    print(f"📡 Connexion: {AZURITE_CONNECTION_STRING.split(';')[2]}...")  # Masquer la clé
    print()
    
    if args.list:
        print("📋 Listing des queues existantes:")
        queues = list_all_queues()
        if queues:
            for queue in queues:
                print(f"   - {queue}")
        else:
            print("   Aucune queue trouvée")
        return
    
    success_count = 0
    total_count = 0
    
    if args.all:
        # Vider toutes les queues existantes
        print("⚠️  Mode --all: vidage de TOUTES les queues existantes")
        queues_to_clear = list_all_queues()
        if not queues_to_clear:
            print("ℹ️  Aucune queue à vider")
            return
    else:
        # Vider seulement les queues du projet
        print("🎯 Mode normal: vidage des queues du projet uniquement")
        queues_to_clear = PROJECT_QUEUES
    
    for queue_name in queues_to_clear:
        total_count += 1
        if clear_queue(queue_name):
            success_count += 1
    
    print()
    if success_count == total_count:
        print(f"✅ Toutes les queues traitées avec succès ({success_count}/{total_count})")
    else:
        print(f"⚠️  Quelques erreurs: {success_count}/{total_count} queues traitées")
        sys.exit(1)


if __name__ == "__main__":
    main()
