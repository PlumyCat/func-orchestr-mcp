import os
import json
import sys
import types
import pathlib
from unittest.mock import MagicMock, patch

# Ensure project root on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Stub Azure modules if they are not installed
# This allows importing storage module without azure dependencies.
if 'azure' not in sys.modules:
    azure = types.ModuleType('azure')
    storage_mod = types.ModuleType('storage')
    queue_mod = types.ModuleType('queue')
    blob_mod = types.ModuleType('blob')

    class QueueClient:  # minimal stub for patching
        @classmethod
        def from_connection_string(cls, conn_str, **kwargs):
            raise NotImplementedError

    class BlobServiceClient:  # minimal stub for patching
        @classmethod
        def from_connection_string(cls, conn_str, **kwargs):
            raise NotImplementedError

    queue_mod.QueueClient = QueueClient
    blob_mod.BlobServiceClient = BlobServiceClient
    storage_mod.queue = queue_mod
    storage_mod.blob = blob_mod
    azure.storage = storage_mod

    sys.modules['azure'] = azure
    sys.modules['azure.storage'] = storage_mod
    sys.modules['azure.storage.queue'] = queue_mod
    sys.modules['azure.storage.blob'] = blob_mod

from app.services.storage import (
    get_storage_clients,
    upload_job_blob,
    get_job_blob,
    upload_sidecar_request,
    get_sidecar_request,
)


def test_get_storage_clients_uses_env_and_calls_from_connection_string():
    conn_str = "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;"
    with patch.dict(os.environ, {"AzureWebJobsStorage": conn_str}):
        with patch("app.services.storage.QueueClient.from_connection_string") as queue_from_cs, \
             patch("app.services.storage.BlobServiceClient.from_connection_string") as blob_from_cs:
            queue_from_cs.return_value = "queue"
            blob_from_cs.return_value = "blob"

            result = get_storage_clients("testqueue")

            queue_from_cs.assert_called_once_with(conn_str, queue_name="testqueue")
            blob_from_cs.assert_called_once_with(conn_str)
            assert result["queue"] == "queue"
            assert result["blob"] == "blob"
            assert result["container"] == os.getenv("MCP_JOBS_CONTAINER", "jobs")


def test_upload_job_blob_serializes_json():
    blob_service = MagicMock()
    blob_client = blob_service.get_blob_client.return_value
    payload = {"message": "bonjour"}

    upload_job_blob(blob_service, "cont", "123", payload)

    blob_service.get_blob_client.assert_called_once_with(container="cont", blob="123.json")
    blob_client.upload_blob.assert_called_once_with(json.dumps(payload, ensure_ascii=False), overwrite=True)


def test_get_job_blob_deserializes_json():
    blob_service = MagicMock()
    blob_client = blob_service.get_blob_client.return_value
    payload = {"message": "salut"}
    blob_client.exists.return_value = True
    download = MagicMock()
    download.readall.return_value = json.dumps(payload).encode("utf-8")
    blob_client.download_blob.return_value = download

    result = get_job_blob(blob_service, "cont", "456")

    blob_service.get_blob_client.assert_called_once_with(container="cont", blob="456.json")
    blob_client.exists.assert_called_once()
    blob_client.download_blob.assert_called_once()
    assert result == payload


def test_upload_sidecar_request_serializes_json():
    blob_service = MagicMock()
    blob_client = blob_service.get_blob_client.return_value
    body = {"sidecar": "data"}

    upload_sidecar_request(blob_service, "cont", "789", body)

    blob_service.get_blob_client.assert_called_once_with(container="cont", blob="789.req.json")
    blob_client.upload_blob.assert_called_once_with(json.dumps(body, ensure_ascii=False), overwrite=True)


def test_get_sidecar_request_deserializes_json():
    blob_service = MagicMock()
    blob_client = blob_service.get_blob_client.return_value
    body = {"sidecar": "info"}
    blob_client.exists.return_value = True
    download = MagicMock()
    download.readall.return_value = json.dumps(body).encode("utf-8")
    blob_client.download_blob.return_value = download

    result = get_sidecar_request(blob_service, "cont", "321")

    blob_service.get_blob_client.assert_called_once_with(container="cont", blob="321.req.json")
    blob_client.exists.assert_called_once()
    blob_client.download_blob.assert_called_once()
    assert result == body
