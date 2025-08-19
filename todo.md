
@user_id = user-456

### Test convert_word_to_pdf (depuis un blob déjà uploadé)
POST http://localhost:7075/api/convert/word-to-pdf?blob={{user_id}}/new.docx

### Test init_user
POST http://localhost:7075/api/users/init
Content-Type: application/json

{ "user_id": "{{user_id}}" }

### Test list_images
GET http://localhost:7075/api/users/images
Content-Type: application/json

{ "user_id": "{{user_id}}", "pageSize": 10 }


### Test list_templates_http
GET http://localhost:7075/api/users/templates
Content-Type: application/json

{ "user_id": "{{user_id}}", "pageSize": 10 }

### Test upload_template
POST http://localhost:7075/api/users/templates
Content-Type: application/json

{ "user_id": "{{user_id}}" }


### Test list_shared_templates
GET http://localhost:7075/api/templates?pageSize=10

### Test mcp_exec
POST http://localhost:7075/api/mcp/exec
Content-Type: application/json

{
  "toolName": "word_add_paragraph",
  "arguments": {
  "user_id": "{{user_id}}",
    "filename": "test.docx",
    "text": "Ceci est un test."
  }
}




# TODO : Réparer les appel de tools le model répond un pseudo code au lieu d'enoyer les args vers les tools le problème est sur toutes le stools les classics comme MCP le code focntionnait toutes les tools ont été testées et ok 


"final_text": "```search_web\n{\"query\":\"weather in Paris today\",\"focus_mode\":\"webSearch\"}\n```"
}


"final_text": "```json\n{\"name\":\"init_user\",\"arguments\":{\"user_id\":\"\"}}\n``````json\n{\"name\":\"list_images\",\"arguments\":{\"user_id\":\"\"}}\n``````json\n{\n  \"status\": \"requested\",\n  \"message\": \"Initializing user storage and listing images. Results will be returned shortly.\"\n}\n```"
}