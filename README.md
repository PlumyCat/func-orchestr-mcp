# Azure Functions
## Chat using Azure OpenAI (Python v2 Function) + MCP Orchestration

This project demonstrates how to build a Python Azure Functions app that interacts with Azure OpenAI and orchestrates tools via the Model Context Protocol (MCP). It includes a simple `/api/ask` endpoint, several `/api/mcp-*` endpoints for tool execution, and optional chat endpoints backed by assistant bindings.

## Run on your local environment

### Pre-requisites
1. [Python 3.8+](https://www.python.org/)
2. [Azure Functions Core Tools 4.0.6610 or higher](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local?tabs=v4%2Cmacos%2Ccsharp%2Cportal%2Cbash#install-the-azure-functions-core-tools)
3. [Azurite](https://github.com/Azure/Azurite) for local storage emulation

The easiest way to install Azurite is using a Docker container:

```bash
docker run -d -p 10000:10000 -p 10001:10001 -p 10002:10002 mcr.microsoft.com/azure-storage/azurite
```

### Provision Azure resources

```bash
azd provision
```

This creates the Azure resources including an Azure OpenAI instance.  The `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY` values can be found in `./.azure/<env-name>/.env`.

### Local settings

Create a `local.settings.json` file in the repository root.  Replace `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY` with your values.

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AZURE_OPENAI_ENDPOINT": "https://cog-<unique>.openai.azure.com/",
    "AZURE_OPENAI_KEY": "<your-key>",
    "CHAT_MODEL_DEPLOYMENT_NAME": "chat",
    "AzureWebJobsFeatureFlags": "EnableWorkerIndexing",
    "PYTHON_ISOLATE_WORKER_DEPENDENCIES": "1"
  }
}
```

### MCP configuration

If you plan to use the MCP endpoints, configure these additional environment variables (they can be added under `Values` in `local.settings.json`):

- `TOOLS_SSE_URL`: SSE endpoint of the MCP Function server.  If not set the service will also check `LOCAL_MCP_SSE_URL` or `MCP_SSE_URL`.
- `TOOLS_FUNCTIONS_KEY`: Optional Functions key used as `x-functions-key`.  Also checked: `LOCAL_MCP_FUNCTIONS_KEY` or `MCP_SSE_KEY`.
- `ALLOW_CLIENT_MCP_OVERRIDE`: Set to `true` to allow callers to override `mcp_url` and headers per request.
- `AZURE_OPENAI_MODEL`: Default model to use for `mcp-*` endpoints.
- `DEFAULT_REASONING_EFFORT`: One of `low`, `medium`, or `high`.
- `ALLOWED_CORS_ORIGINS`: Comma-separated list of allowed origins for MCP HTTP endpoints.
- `AZURE_OPENAI_API_VERSION`: Defaults to `2025-01-01-preview`. Ensure your Azure OpenAI resource supports this version.
- Storage settings: `MCP_JOBS_QUEUE` (default `mcpjobs`) and `MCP_JOBS_CONTAINER` (default `jobs`).

### Start the app

```bash
pip install -r requirements.txt
func start
```

See [`test.http`](test.http) for ready-to-run examples.

## Available endpoints

- `GET /api/ping` – health check.
- `POST /api/ask` – send a prompt and receive a completion. Supports optional `user_id` and `conversation_id` for memory.
- MCP endpoints:
  - `POST /api/mcp-run` – run a prompt with optional tools.
  - `POST /api/mcp-enqueue` – enqueue a prompt for background processing.
  - `POST /api/mcp-process` – process a queued job (useful when running locally without a queue trigger).
  - `GET /api/mcp-result?job_id=<id>` – poll the status or result of a queued job.
  - `GET /api/mcp-memories` and `GET /api/mcp-memory` – query stored memories.

Optional chat endpoints (`PUT|GET|POST /api/chats/{chatId}`) are enabled when the `ENABLE_ASSISTANT_BINDINGS` environment variable is set.

## Deploy to Azure

Use the [Azure Developer CLI](https://aka.ms/azd) to provision and deploy:

```bash
azd up
```

## License

This project is licensed under the [MIT License](LICENSE).
