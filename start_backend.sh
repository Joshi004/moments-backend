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

# Start the server
echo "Starting FastAPI server on port 7005..."
echo "Server will be available at http://localhost:7005"
echo "API documentation at http://localhost:7005/docs"
echo ""
uvicorn app.main:app --host 0.0.0.0 --port 7005 --reload




