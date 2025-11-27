#!/bin/bash

# Navigate to the backend directory
cd "$(dirname "$0")"

# Check if virtual environment exists, create if it doesn't
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Check if requirements.txt exists and install dependencies
if [ -f "requirements.txt" ]; then
    echo "Installing/updating dependencies..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
else
    echo "Warning: requirements.txt not found!"
fi

# Check if videos directory exists
if [ ! -d "static/videos" ]; then
    echo "Warning: static/videos directory not found!"
fi

# Parse command line arguments for port
BACKEND_PORT=7005
while [[ $# -gt 0 ]]; do
    case $1 in
        --port|-p)
            if [ -z "$2" ]; then
                echo "Error: --port requires a port number"
                echo "Usage: $0 [--port|-p PORT]"
                exit 1
            fi
            BACKEND_PORT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--port|-p PORT]"
            exit 1
            ;;
    esac
done

# Export BACKEND_PORT environment variable
export BACKEND_PORT

# Write backend port to config file for frontend to read
# Get the project root directory (parent of moments-backend)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/.backend-port"

# Write port to config file
echo "${BACKEND_PORT}" > "$CONFIG_FILE"
echo "Backend port ${BACKEND_PORT} written to $CONFIG_FILE"

# Start the server
echo "Starting FastAPI server on port ${BACKEND_PORT}..."
echo "Server will be available at http://localhost:${BACKEND_PORT}"
echo "API documentation at http://localhost:${BACKEND_PORT}/docs"
echo "BACKEND_PORT=${BACKEND_PORT} exported"
echo ""
uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT} --reload




