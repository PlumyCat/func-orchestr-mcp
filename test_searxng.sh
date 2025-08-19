#!/bin/bash

# Script de test pour SearXNG local
# Usage: ./test_searxng.sh [quick|full|integration]

set -e

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SEARXNG_URL="http://127.0.0.1:8080"
TESTS_DIR="tests"

echo -e "${BLUE}🧪 Testeur SearXNG Local${NC}"
echo -e "${BLUE}URL: ${SEARXNG_URL}${NC}"
echo ""

# Fonction pour vérifier si SearXNG est accessible
check_searxng_availability() {
    echo -e "${YELLOW}🔍 Vérification de la disponibilité de SearXNG...${NC}"
    
    if curl -s --max-time 3 "${SEARXNG_URL}" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ SearXNG est accessible${NC}"
        return 0
    else
        echo -e "${RED}❌ SearXNG n'est pas accessible${NC}"
        echo -e "${YELLOW}💡 Vérifiez que le container Docker est démarré:${NC}"
        echo "   docker ps | grep searxng"
        echo "   docker logs <container_id>"
        return 1
    fi
}

# Fonction pour le test rapide
run_quick_test() {
    echo -e "${BLUE}⚡ Test rapide SearXNG${NC}"
    echo "================================"
    
    if ! check_searxng_availability; then
        exit 1
    fi
    
    python3 "${TESTS_DIR}/quick_searxng_test.py"
}

# Fonction pour le test complet
run_full_test() {
    echo -e "${BLUE}🚀 Test complet SearXNG${NC}"
    echo "================================"
    
    if ! check_searxng_availability; then
        exit 1
    fi
    
    python3 "${TESTS_DIR}/test_searxng_local.py"
}

# Fonction pour le test d'intégration
run_integration_test() {
    echo -e "${BLUE}🔗 Test d'intégration Azure Functions${NC}"
    echo "=========================================="
    
    if ! check_searxng_availability; then
        exit 1
    fi
    
    python3 "${TESTS_DIR}/test_searxng_function_integration.py"
}

# Fonction pour afficher l'aide
show_help() {
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  quick        Test rapide de connectivité"
    echo "  full         Test complet avec toutes les fonctionnalités"
    echo "  integration  Test d'intégration Azure Functions"
    echo "  help         Affiche cette aide"
    echo ""
    echo "Exemples:"
    echo "  $0 quick        # Test rapide"
    echo "  $0 full         # Test complet"
    echo "  $0 integration  # Test d'intégration"
    echo ""
    echo "Configuration:"
    echo "  URL: ${SEARXNG_URL}"
    echo "  Timeout: 6s"
    echo "  Max résultats: 8"
}

# Vérification des dépendances
check_dependencies() {
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}❌ Python3 n'est pas installé${NC}"
        exit 1
    fi
    
    if ! command -v curl &> /dev/null; then
        echo -e "${RED}❌ curl n'est pas installé${NC}"
        exit 1
    fi
    
    # Vérification des modules Python
    if ! python3 -c "import requests" 2>/dev/null; then
        echo -e "${YELLOW}⚠️ Module 'requests' non trouvé${NC}"
        echo -e "${YELLOW}💡 Installation: pip install requests${NC}"
        exit 1
    fi
}

# Script principal
main() {
    # Vérification des dépendances
    check_dependencies
    
    # Traitement des arguments
    case "${1:-quick}" in
        "quick")
            run_quick_test
            ;;
        "full")
            run_full_test
            ;;
        "integration")
            run_integration_test
            ;;
        "help"|"-h"|"--help")
            show_help
            ;;
        *)
            echo -e "${RED}❌ Option invalide: $1${NC}"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Exécution du script principal
main "$@"
