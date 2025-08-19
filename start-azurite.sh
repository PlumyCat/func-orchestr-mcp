#!/bin/bash

# Start Azurite storage emulator for Azure Functions development
# Creates a local storage directory and starts all services

echo "🚀 Starting Azurite storage emulator..."

# Create storage directory if it doesn't exist
mkdir -p ./azurite-data

# Start Azurite with all services (Blob, Queue, Table)
# Using custom ports to avoid conflicts
azurite \
    --location ./azurite-data \
    --blobHost 0.0.0.0 \
    --blobPort 10000 \
    --queueHost 0.0.0.0 \
    --queuePort 10001 \
    --tableHost 0.0.0.0 \
    --tablePort 10002 \
    --debug ./azurite-data/debug.log

echo "✅ Azurite started successfully!"
echo "📁 Blob Storage: http://127.0.0.1:10000"
echo "📋 Queue Storage: http://127.0.0.1:10001" 
echo "🗃️ Table Storage: http://127.0.0.1:10002"
echo "📝 Debug logs: ./azurite-data/debug.log"
