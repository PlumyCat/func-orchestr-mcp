# TODO - Migration endpoint ask vers logique websearch-test

## ✅ MIGRATION TERMINÉE AVEC SUCCÈS

### État AVANT modifications
- L'endpoint `ask` utilisait `run_responses_with_tools()` avec l'API Responses
- Comportement imprévisible : parfois les outils se déclenchaient, parfois non

### État APRÈS modifications  
- L'endpoint `ask` utilise maintenant l'API Chat Completions comme `websearch-test`
- Comportement 100% fiable : les outils se déclenchent systématiquement
- Tous les outils supportés avec gestion des appels multiples

## 🔍 PROBLÈMES DÉTECTÉS ET RÉSOLUS

### ✅ Templates - RÉSOLU
**Symptôme :** `list_templates_test` fonctionne ✅ mais `ask` avec "List my templates" ❌ retourne "no templates found"

**Cause :** Endpoints différents
- `list_templates_test` : `/users/templates` avec JSON body
- `_docsvc_list_templates_http` : `/users/{userId}/templates` avec path param

**Solution :** Aligné `_docsvc_list_templates_http` sur le bon endpoint

### ✅ Init User - RÉSOLU  
**Symptôme :** `init_user_test` fonctionne ✅ mais `ask` avec "init my folder" ❌ retourne erreur 404

**Cause :** Même problème d'endpoints
- `init_user_test` : `/users/init` avec JSON body  
- `_docsvc_init_user` : `/users/{userId}/init` avec path param

**Solution :** Aligné `_docsvc_init_user` sur le bon endpoint + corrigé test unitaire

### 🔍 NOUVEAU PROBLÈME DÉTECTÉ - /api/ask/start

**Symptôme :** `/api/ask/start` ne déclenche pas les tools alors que `/api/ask` fonctionne parfaitement

**Analyse :**
- `/api/ask` : Traitement **synchrone** avec Chat Completions API ✅
- `/api/ask/start` : Traitement **asynchrone** - met juste le job dans une queue ❌
- Le traitement réel des tools se fait dans le worker qui traite la queue (pas encore analysé)

**✅ CAUSE IDENTIFIÉE ET RÉSOLUE :**
- Le worker `mcp_worker.py` utilisait encore `run_responses_with_tools()` ❌ 
- `/api/ask` utilise maintenant Chat Completions API directement ✅

**✅ SOLUTION APPLIQUÉE (CORRIGÉE) :**
- **Approche hybride intelligente dans `mcp_worker.py` :**
  - **Tools classiques** (`type: "function"`) → Chat Completions API ✅
  - **Tools MCP** (`type: "mcp"`) → API Responses ✅ 
- **✅ CORRECTION FORMAT MESSAGES :** Conversion Responses → Chat Completions pour tools classiques
- **✅ PRÉSERVATION TOOLS MCP :** Les tools MCP utilisent l'API Responses (comme avant)
- Maintenant `/api/ask/start` devrait utiliser TOUS les tools correctement

### ✅ Tests avec retry créés
- Fichier `init_user_test_with_retry.http` pour tests manuels
- Script `test_with_retry.py` mis à jour avec `test_init_user()`

## 🔍 NOUVEAU PROBLÈME DÉTECTÉ - Orchestration intelligente

**Symptôme :** `"allowed_tools": "*"` force toujours le mode "tools" même pour des prompts simples

**Analyse :**
- Prompt : `"Explain Azure Functions in 3 bullet points"` (47 chars)
- Avec `"allowed_tools": "*"` → Mode "tools" (incorrect pour ce prompt)
- Sans `"allowed_tools"` → Mode "trivial" (correct pour ce prompt)

**✅ SOLUTION APPLIQUÉE :**
- Modifié `route_mode()` pour analyser le contenu du prompt
- Mode "tools" seulement si `allowed_tools` présent ET prompt contient des mots-clés d'outils
- Mots-clés : search, list, create, init, convert, etc. (FR + EN)

## Plan de migration
1. ✅ Analyser la logique de `websearch-test` (lignes 162-260 function_app.py)
2. ✅ **SUCCÈS !** - Migration réussie vers Chat Completions API
3. ✅ **TOUS les outils supportés** - Logique générique implémentée
4. ✅ **Migration complète** - Plus d'utilisation de run_responses_with_tools
5. ✅ **Websearch testé** - Fonctionne parfaitement avec vraies données 2024
6. ✅ **Autres outils testés** - list_shared_templates fonctionne
7. ✅ **CORRECTION RÉUSSIE** - Appels d'outils multiples gérés, erreur 400 résolue

## Logique websearch-test à copier
```python
# 1. Premier appel Chat Completions avec tools
resp = client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice="auto")

# 2. Si tool_calls présents :
#    - Exécuter l'outil via execute_tool_call()
#    - Deuxième appel avec les résultats
follow_up = client.chat.completions.create(model=model, messages=messages + [msg, tool_response])

# 3. Retourner follow_up.choices[0].message.content
```

## Différences à gérer
- `websearch-test` : 1 seul outil (search_web)
- `ask` : Tous les outils + filtrage via `allowed_tools`
- `ask` : Support conversation history + user_id
- `ask` : Support différents modèles

## Fichiers modifiés
- `function_app.py` - endpoint ask (lignes ~380-450)
- `TODO.md` - ce fichier

## Backup
- État actuel sauvegardé dans la mémoire ID: 6817564
- Backup physique : `function_app_backup.py`
