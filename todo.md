Réparer les conneries de Cursor

les tools ne fonctionnent plus correctement un a plus q'un seul appel vers le model donc il indique qu'il va utiliser la tool mais le fait pas 


exmple problème 

{
  "ok": true,
  "job_id": "b1a654e8-1448-4407-9ddb-98677b9d494e",
  "status": "completed",
  "message": "Completed",
  "progress": 100,
  "tool": "",
  "mode": "tools",
  "selected_model": "gpt-4.1-mini",
  "conversation_id": "user123_1755636733",
  "final_text": "I will check the current weather in Paris for you.{\n  \"query\": \"weather in Paris today\",\n  \"focus_mode\": \"newsSearch\"\n}{\n  \"query\": \"weather in Paris today\",\n  \"focus_mode\": \"webSearch\"\n}{\n  \"query\": \"current weather in Paris\",\n  \"focus_mode\": \"webSearch\"\n}{\n  \"query\": \"weather Paris today\",\n  \"focus_mode\": \"webSearch\"\n}The weather in Paris today is generally mild with some clouds and occasional sunshine. Temperatures range around 15-20°C (59-68°F). There might be a light breeze, but no significant rain is expected.\n\nIf you want precise and up-to-date details, please let me know!"
}

de pire en pire Cursor a du mettre des mock ou autres on peut plus demander que websearch tout est céssé alors que tout fonctionnait ce matin ce qui a été ajouté ets le streaming 

note : ne pas oublier de supprimer tous abonnement cursor !! 