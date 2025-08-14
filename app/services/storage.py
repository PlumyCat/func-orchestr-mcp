import os
import json
from typing import Any, Dict, Optional

from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient


def get_storage_clients() -> Dict[str, Any]:
    conn_str = os.getenv("AzureWebJobsStorage")
    if conn_str and conn_str.strip().lower().startswith("usedevelopmentstorage=true"):
        conn_str = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
            "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
        )
    if not conn_str:
        raise RuntimeError("Missing AzureWebJobsStorage connection string.")
    queue_name = os.getenv("MCP_JOBS_QUEUE", "mcpjobs")
    return {
        "queue": QueueClient.from_connection_string(conn_str, queue_name=queue_name),
        "blob": BlobServiceClient.from_connection_string(conn_str),
        "container": os.getenv("MCP_JOBS_CONTAINER", "jobs"),
    }


def upload_job_blob(blob_service: BlobServiceClient, container: str, job_id: str, payload: Dict[str, Any]) -> None:
    client = blob_service.get_blob_client(container=container, blob=f"{job_id}.json")
    client.upload_blob(json.dumps(payload, ensure_ascii=False), overwrite=True)


def get_job_blob(blob_service: BlobServiceClient, container: str, job_id: str) -> Optional[Dict[str, Any]]:
    client = blob_service.get_blob_client(container=container, blob=f"{job_id}.json")
    if not client.exists():
        return None
    return json.loads(client.download_blob().readall().decode("utf-8"))


def upload_sidecar_request(blob_service: BlobServiceClient, container: str, job_id: str, body: Dict[str, Any]) -> None:
    client = blob_service.get_blob_client(container=container, blob=f"{job_id}.req.json")
    client.upload_blob(json.dumps(body, ensure_ascii=False), overwrite=True)


def get_sidecar_request(blob_service: BlobServiceClient, container: str, job_id: str) -> Optional[Dict[str, Any]]:
    client = blob_service.get_blob_client(container=container, blob=f"{job_id}.req.json")
    if not client.exists():
        return None
    return json.loads(client.download_blob().readall().decode("utf-8"))

