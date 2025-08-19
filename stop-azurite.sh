#!/bin/bash

# Stop Azurite storage emulator

echo "🛑 Stopping Azurite storage emulator..."

# Find and kill Azurite processes
pkill -f azurite

if [ $? -eq 0 ]; then
    echo "✅ Azurite stopped successfully!"
else
    echo "ℹ️ No running Azurite processes found."
fi
