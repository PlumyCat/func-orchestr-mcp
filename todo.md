# Résultat des tes simple de test des tools

## Tools classic 

### search_web
websearch_test: [POST] http://localhost:7071/api/websearch-test

[text](tests_http/websearch-test.http)

##### Test d'envoi
@host = http://localhost:7071

### websearch-test
name : websearch-test
POST {{host}}/api/websearch-test
Accept: application/json

{
  "prompt": "What are the latest updates in Azure Functions pricing for 2024?"
}

#### Response
{
  "answer": "As of 2024, the latest updates for Azure Functions pricing have not been explicitly detailed in available sources. However, generally, Azure Functions pricing typically includes:\n\n- **Consumption Plan:** Charged based on the number of executions, execution duration, and memory used.\n- **Premium Plan:** Fixed monthly fee based on the number of instances, with additional charges for execution time and memory.\n- **Dedicated (App Service) Plan:** Uses pre-allocated resources and is charged as part of App Service plans.\n\nFor the most accurate and up-to-date pricing, including any new 2024-specific changes, I recommend checking the official [Azure Functions pricing page](https://azure.microsoft.com/en-us/pricing/details/functions/) or the Azure updates blog.\n\nIf you want, I can also help summarize the detailed current pricing structure or any recent announcements from Microsoft regarding Azure Functions.",
  "tool_called": {
    "name": "search_web",
    "arguments": "{\"query\":\"Azure Functions pricing updates 2024\"}"
  }
}

### init_user
init_user_test: [POST] http://localhost:7071/api/init-user-test

[text](tests_http/init_user_test.http)

##### Test d'envoi
@host = http://localhost:7071
@user_id = user591

### api/list-images-test
name : api/list-images-test
POST {{host}}/api/init-user-test
Accept: application/json

{
  "prompt": "init my foder on blob.",
  "user_id": "{{user_id}}"
}

#### Response
{
  "answer": "L'espace pour 'user591' est déjà initialisé.",
  "tool_called": {
    "name": "init_user",
    "arguments": "{\"user_id\":\"user591\"}"
  },
  "backend_result": {
    "user_id": "user591",
    "created": []
  }
}

### convert_word_to_pdf
word_create_document_test: [POST] http://localhost:7071/api/word-create-document-test

[text](tests_http/convert_word_to_pdf_test.http)


##### Test d'envoi
@host = http://localhost:7071
@user_id = user123

POST {{host}}/api/convert-word-to-pdf-test
Content-Type: application/json

{
  "prompt": "Convert this DOCX to PDF.",
  "blob": "user123/new.docx"
}

#### Response
{
  "answer": "The DOCX file has been converted to PDF. You can download it using the following link:\n\n[Download PDF](https://stwordmcpserverdmo.blob.core.windows.net/stword/user123/new.pdf?se=2025-08-20T16%3A05%3A20Z&sp=r&sv=2025-07-05&sr=b&sig=sOH9wGv/87Q5GMC/o/8TRRLAVnGR3wrNU3mzaAdo9Z0%3D)",
  "tool_called": {
    "name": "convert_word_to_pdf",
    "arguments": "{\"blob\":\"user123/new.docx\"}"
  },
  "backend_result": {
    "container": "stword",
    "blob": "user123/new.pdf",
    "etag": "\"0x8DDDFFAFB05DD3A\"",
    "size": 31997,
    "contentType": "application/pdf",
    "lastModified": "2025-08-20T15:05:20+00:00",
    "sasUrl": "https://stwordmcpserverdmo.blob.core.windows.net/stword/user123/new.pdf?se=2025-08-20T16%3A05%3A20Z&sp=r&sv=2025-07-05&sr=b&sig=sOH9wGv/87Q5GMC/o/8TRRLAVnGR3wrNU3mzaAdo9Z0%3D",
    "expiresUtc": "2025-08-20T16:05:20.285722+00:00"
  }
}

### list_images
list_images_test: [POST] http://localhost:7071/api/list-images-test

[text](tests_http/list_images_test.http)

##### Test d'envoi
@host = http://localhost:7071
@user_id = user123

### api/list-images-test
name : api/list-images-test
POST {{host}}/api/list-images-test
Accept: application/json

{
  "prompt": "List my images in storage.",
  "user_id": "{{user_id}}"
}

#### Response
{
  "answer": "You have the following images in storage:\n\n1. test-image.png (22,072 bytes, image/png, last modified on 2025-08-18)\n2. watermark.png (97,410 bytes, image/png, last modified on 2025-08-18)\n\nLet me know if you need any actions with these images.",
  "tool_called": {
    "name": "list_images",
    "arguments": "{\"user_id\":\"user123\"}"
  },
  "backend_result": {
    "items": [
      {
        "name": "user123/image_blob/test-image.png",
        "size": 22072,
        "contentType": "image/png",
        "lastModified": "2025-08-18T17:19:33+00:00"
      },
      {
        "name": "user123/image_blob/watermark.png",
        "size": 97410,
        "contentType": "image/png",
        "lastModified": "2025-08-18T17:19:34+00:00"
      }
    ]
  }
}

### list_templates_user
list_templates_test: [POST] http://localhost:7071/api/list-templates-test

[text](tests_http/list_templates_user_test.http)

##### Test d'envoi
@host = http://localhost:7071
@user_id = user123

### list_templates_test
name : list_templates_test
POST {{host}}/api/list-templates-test
Accept: application/json

{
  "prompt": "List my templates.",
  "user_id": "{{user_id}}",
  "pageSize": 10,
  "includeShared": true
}

#### Response
{
  "answer": "You have 1 template:\n\n1. modele.dotx (size: 66,863 bytes, last modified: August 18, 2025)",
  "tool_called": {
    "name": "list_templates_http",
    "arguments": "{\"user_id\":\"user123\",\"pageSize\":10,\"includeShared\":true}"
  },
  "backend_result": {
    "items": [
      {
        "name": "user123/templates/modele.dotx",
        "size": 66863,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-18T17:20:23+00:00"
      }
    ]
  }
}

### list_shared_templates
list_shared_templates_test: [GET] http://localhost:7071/api/list-shared-templates-test

[text](tests_http/list_shared_templates.http)

##### Test d'envoi
@host = http://localhost:7071

### list_templates_test
name : list_templates_test
GET {{host}}/api/list-shared-templates-test?prompt=List%20shared%20templates.&pageSize=10
Accept: application/json

#### Response
{
  "answer": "Here are some shared templates:\n\n1. shared/templates/en/AdjacencyLetter.dotx\n2. shared/templates/en/AdjacencyReport.dotx\n3. shared/templates/en/AdjacencyResume.dotx\n4. shared/templates/en/ApothecaryLetter.dotx\n5. shared/templates/en/ApothecaryNewsletter.dotx\n6. shared/templates/en/ApothecaryResume.dotx\n7. shared/templates/en/Blog.dotx\n8. shared/templates/en/ChronologicalLetter.dotx\n9. shared/templates/en/ChronologicalResume.dotx\n10. shared/templates/en/EssentialLetter.dotx\n\nWould you like to see more?",
  "tool_called": {
    "name": "list_shared_templates",
    "arguments": "{\"pageSize\":10}"
  },
  "backend_result": {
    "items": [
      {
        "name": "shared/templates/en/AdjacencyLetter.dotx",
        "size": 200457,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:19+00:00"
      },
      {
        "name": "shared/templates/en/AdjacencyReport.dotx",
        "size": 3601841,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:23:37+00:00"
      },
      {
        "name": "shared/templates/en/AdjacencyResume.dotx",
        "size": 238821,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:19+00:00"
      },
      {
        "name": "shared/templates/en/ApothecaryLetter.dotx",
        "size": 162712,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:18+00:00"
      },
      {
        "name": "shared/templates/en/ApothecaryNewsletter.dotx",
        "size": 226432,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:22+00:00"
      },
      {
        "name": "shared/templates/en/ApothecaryResume.dotx",
        "size": 221191,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:26+00:00"
      },
      {
        "name": "shared/templates/en/Blog.dotx",
        "size": 14909,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:21+00:00"
      },
      {
        "name": "shared/templates/en/ChronologicalLetter.dotx",
        "size": 57200,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:22+00:00"
      },
      {
        "name": "shared/templates/en/ChronologicalResume.dotx",
        "size": 72353,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:25+00:00"
      },
      {
        "name": "shared/templates/en/EssentialLetter.dotx",
        "size": 124214,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:30+00:00"
      },
      {
        "name": "shared/templates/en/EssentialReport.dotx",
        "size": 765929,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:43+00:00"
      },
      {
        "name": "shared/templates/en/EssentialResume.dotx",
        "size": 284990,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:36+00:00"
      },
      {
        "name": "shared/templates/en/Office Word 2003 Look.dotx",
        "size": 27596,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:27+00:00"
      },
      {
        "name": "shared/templates/en/OriginLetter.Dotx",
        "size": 121759,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:35+00:00"
      },
      {
        "name": "shared/templates/en/OriginReport.Dotx",
        "size": 368457,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:52+00:00"
      },
      {
        "name": "shared/templates/en/OriginResume.Dotx",
        "size": 137891,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:40+00:00"
      },
      {
        "name": "shared/templates/en/RedAndBlackLetter.dotx",
        "size": 52667,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:39+00:00"
      },
      {
        "name": "shared/templates/en/RedAndBlackReport.dotx",
        "size": 1791654,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:23:27+00:00"
      },
      {
        "name": "shared/templates/en/StudentReport.dotx",
        "size": 654417,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:23:06+00:00"
      },
      {
        "name": "shared/templates/en/TimelessLetter.dotx",
        "size": 53016,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:47+00:00"
      },
      {
        "name": "shared/templates/en/TimelessReport.dotx",
        "size": 276307,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:23:00+00:00"
      },
      {
        "name": "shared/templates/en/TimelessResume.dotx",
        "size": 48754,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T09:22:56+00:00"
      },
      {
        "name": "shared/templates/fr/AdjacencyLetter.dotx",
        "size": 158916,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:04+00:00"
      },
      {
        "name": "shared/templates/fr/AdjacencyReport.dotx",
        "size": 3521595,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:45:07+00:00"
      },
      {
        "name": "shared/templates/fr/AdjacencyResume.dotx",
        "size": 195198,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:01+00:00"
      },
      {
        "name": "shared/templates/fr/ApothecaryLetter.dotx",
        "size": 124178,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:00+00:00"
      },
      {
        "name": "shared/templates/fr/ApothecaryNewsletter.dotx",
        "size": 224106,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:02+00:00"
      },
      {
        "name": "shared/templates/fr/ApothecaryResume.dotx",
        "size": 183911,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:04+00:00"
      },
      {
        "name": "shared/templates/fr/Blog.dotx",
        "size": 14909,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:04+00:00"
      },
      {
        "name": "shared/templates/fr/ChronologicalLetter.dotx",
        "size": 71276,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:08+00:00"
      },
      {
        "name": "shared/templates/fr/ChronologicalResume.dotx",
        "size": 77742,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:05+00:00"
      },
      {
        "name": "shared/templates/fr/EssentialLetter.dotx",
        "size": 111983,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:08+00:00"
      },
      {
        "name": "shared/templates/fr/EssentialReport.dotx",
        "size": 691170,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:28+00:00"
      },
      {
        "name": "shared/templates/fr/EssentialResume.dotx",
        "size": 269341,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:11+00:00"
      },
      {
        "name": "shared/templates/fr/Office Word 2003 Look.dotx",
        "size": 36548,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:15+00:00"
      },
      {
        "name": "shared/templates/fr/OriginLetter.Dotx",
        "size": 108564,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:14+00:00"
      },
      {
      "name": "shared/templates/fr/OriginReport.Dotx",
        "size": 296582,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:21+00:00"
      },
      {
        "name": "shared/templates/fr/OriginResume.Dotx",
        "size": 124299,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:20+00:00"
      },
      {
        "name": "shared/templates/fr/RedAndBlackLetter.dotx",
        "size": 80680,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:23+00:00"
      },
      {
        "name": "shared/templates/fr/RedAndBlackReport.dotx",
        "size": 649101,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:41+00:00"
      },
      {
        "name": "shared/templates/fr/StudentReport.dotx",
        "size": 643322,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:43+00:00"
      },
      {
        "name": "shared/templates/fr/TimelessLetter.dotx",
        "size": 75025,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:28+00:00"
      },
      {
        "name": "shared/templates/fr/TimelessReport.dotx",
        "size": 289386,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:42+00:00"
      },
      {
        "name": "shared/templates/fr/TimelessResume.dotx",
        "size": 57412,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:44:31+00:00"
      },
      {
        "name": "shared/templates/modele.dotx",
        "size": 66863,
        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "lastModified": "2025-08-14T08:31:08+00:00"
      }
    ]
  }
}

## Tools MCP

### hello_mcp
hello_mcp_test: [POST] http://localhost:7071/api/hello-mcp-test

[text](tests_http/hello_mcp_test.http)

##### Test d'envoi
@host = http://localhost:7071
@user_id = user591

### hello_mcp_test
name : hello-mcp-test
POST {{host}}/api/hello-mcp-test
Accept: application/json

{ "prompt": "Call hello_mcp." }

#### Response
{
  "answer": "Hello! I have called hello_mcp and it responded: \"Hello I am MCPTool!\" How can I assist you further?"
}


### word-create-document
convert_word_to_pdf_test: [POST] http://localhost:7071/api/convert-word-to-pdf-test

[text](<tests_http/word_create_document_test copy.http>)

##### Test d'envoi
@host = http://localhost:7071
@user_id = user591

### word-create-document-test
name : word-create-document-test
POST {{host}}/api/word-create-document-test
Accept: application/json

{
    "prompt": "Create a doc from template and set properties.",
    "user_id": "user123",
    "filename": "test.docx",
    "title": "Demo Title",
    "author": "Eric"
}

#### Response
{
  "answer": "I have created a Word document named \"test.docx\" using the specified template and set the document title to \"Demo Title\" and the author to \"Eric.\" \n\nYou can download the document from this link:\n[Download test.docx](https://stwordmcpserverdmo.blob.core.windows.net/stword/user123/test.docx?se=2025-08-20T16%3A13%3A48Z&sp=r&sv=2025-07-05&sr=b&sig=iUaKRfGxlHdejVIIHUk6LofJmEqxc25YWKUW8PaeexA%3D)"
}


# Résumer TOUS les tools fonctionnent plus AUCUNE raison de mettre ça en question

Le souci est que tous les endpoints d'utilisation ont été cassé trop d'ajout de trucs inutile perdre du fil 

ce qui est à reprendre :

## 1
ask: [POST] http://localhost:7071/api/ask

Encpoint nécessitant de choisir le model sans treaming
doit permettre d'utiliser toutes les tools calssic et MCP autant de fois que nécessaire tant que le model indique utiliser une tool on continu la boucle un efois terminer on repasse pour préparer la réponse finale 

ask_start: [POST,OPTIONS] http://localhost:7071/api/ask/start
ask_status: [GET,OPTIONS] http://localhost:7071/api/ask/status

Meme chose que ask mais avec streaming

## 2
orchestrate: [POST] http://localhost:7071/api/orchestrate

permet de sélectionner le model pour le user suivant complexité de la demande sinon pareil que ask c la seule différence à avoir toutes le stools classic et MCP doivent fonctionner antant de fois que le model juge nécessaire sans streaming

orchestrate_start: [POST,OPTIONS] http://localhost:7071/api/orchestrate/start
orchestrate_status: [GET,OPTIONS] http://localhost:7071/api/orchestrate/status

Pareil que orchestrate, mais avec streaming 


## 3 

mcp_run: [POST] http://localhost:7071/api/mcp-run

Permet seulement l'utilisation des tools MCP sans streaming

mcp_process: [POST,OPTIONS] http://localhost:7071/api/mcp-process
mcp_result: [GET,OPTIONS] http://localhost:7071/api/mcp-result

meme que mcp-run avec streaming

## 4

mcp_get_memory: [GET] http://localhost:7071/api/mcp-memory
mcp_list_memories: [GET] http://localhost:7071/api/mcp-memories

A ignorer laisser comme ça pour une utilisation externe du suivi des conversation, ce n'est pas une tool

models: [GET] http://localhost:7071/api/models

listes les models dispo sur l'endpoitn foundry déclaré en variable ne pas toucher lasser tel quel

ping: [GET] http://localhost:7071/api/ping

ne pas toucher laisser tel quel

## consignes 

Tous les endpoints au-dessus doievnt aussi être enregistrer sur cosmos db pour suivi/reprise des conversation, le principe est que si dans la requête il y a "user_id": = on enregistre sur Cosmosdb, si "conversation_id": "init" ou pas de "conversation_id": = nouvelle conversation, si exemple "conversation_id": "user123_1755704653" on reprends le conversation pour ça normalement [text](app/services/memory.py) est ok mais a vérifier 

Tous ce qui est dans [text](app/services/conversation.py), [text](app/services/memory.py), [text](app/services/storage.py), [text](app/services/storage.py), [text](app/services/tools.py) est a remettre en question du grand n'importe quoi 