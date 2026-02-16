#!/bin/bash

# Navigate to the backend directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if virtual environment exists, create if it doesn't
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo -e "${GREEN}Activating virtual environment...${NC}"
source venv/bin/activate

# Check if requirements.txt exists and install dependencies
if [ -f "requirements.txt" ]; then
    echo -e "${GREEN}Installing/updating dependencies...${NC}"
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
else
    echo -e "${RED}Warning: requirements.txt not found!${NC}"
fi

# Check if videos directory exists
if [ ! -d "static/videos" ]; then
    echo -e "${YELLOW}Warning: static/videos directory not found!${NC}"
fi

# Parse command line arguments
MODE="all"  # Options: all, api, worker
BACKEND_PORT=7005

while [[ $# -gt 0 ]]; do
    case $1 in
        --port|-p)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: --port requires a port number${NC}"
                echo "Usage: $0 [--port|-p PORT] [--mode|-m all|api|worker]"
                exit 1
            fi
            BACKEND_PORT="$2"
            shift 2
            ;;
        --mode|-m)
            if [ -z "$2" ]; then
                echo -e "${RED}Error: --mode requires a value (all|api|worker)${NC}"
                echo "Usage: $0 [--port|-p PORT] [--mode|-m all|api|worker]"
                exit 1
            fi
            MODE="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --port, -p PORT          Set backend port (default: 7005)"
            echo "  --mode, -m MODE          Set run mode: all|api|worker (default: all)"
            echo "  --help, -h               Show this help message"
            echo ""
            echo "Modes:"
            echo "  all                      Run both API server and pipeline worker (default)"
            echo "  api                      Run only API server"
            echo "  worker                   Run only pipeline worker"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Usage: $0 [--port|-p PORT] [--mode|-m all|api|worker]"
            exit 1
            ;;
    esac
done

# Validate mode
if [[ "$MODE" != "all" && "$MODE" != "api" && "$MODE" != "worker" ]]; then
    echo -e "${RED}Error: Invalid mode '$MODE'. Must be 'all', 'api', or 'worker'${NC}"
    exit 1
fi

# Export BACKEND_PORT environment variable
export BACKEND_PORT

# Write backend port to config file for frontend to read
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/.backend-port"
echo "${BACKEND_PORT}" > "$CONFIG_FILE"
echo -e "${GREEN}Backend port ${BACKEND_PORT} written to $CONFIG_FILE${NC}"

# Define PID file locations
PID_DIR="$SCRIPT_DIR/.pids"
mkdir -p "$PID_DIR"
API_PID_FILE="$PID_DIR/api.pid"
WORKER_PID_FILE="$PID_DIR/worker.pid"

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    
    if [ -f "$API_PID_FILE" ]; then
        API_PID=$(cat "$API_PID_FILE")
        if ps -p $API_PID > /dev/null 2>&1; then
            echo -e "${BLUE}[API]${NC} Stopping (PID: $API_PID)..."
            kill $API_PID 2>/dev/null
            wait $API_PID 2>/dev/null
        fi
        rm -f "$API_PID_FILE"
    fi
    
    if [ -f "$WORKER_PID_FILE" ]; then
        WORKER_PID=$(cat "$WORKER_PID_FILE")
        if ps -p $WORKER_PID > /dev/null 2>&1; then
            echo -e "${BLUE}[WORKER]${NC} Stopping (PID: $WORKER_PID)..."
            kill $WORKER_PID 2>/dev/null
            wait $WORKER_PID 2>/dev/null
        fi
        rm -f "$WORKER_PID_FILE"
    fi
    
    echo -e "${GREEN}Shutdown complete${NC}"
    exit 0
}

# Kill existing processes before starting new ones
kill_existing_processes() {
    echo -e "${YELLOW}Checking for existing processes...${NC}"
    
    # Kill existing API server on this port
    EXISTING_API=$(lsof -ti :${BACKEND_PORT} 2>/dev/null)
    if [ ! -z "$EXISTING_API" ]; then
        echo -e "${YELLOW}Killing existing API on port ${BACKEND_PORT} (PID: $EXISTING_API)${NC}"
        kill -9 $EXISTING_API 2>/dev/null
        sleep 1
    fi
    
    # Kill ALL existing workers
    EXISTING_WORKERS=$(pgrep -f "python.*run_worker.py" 2>/dev/null)
    if [ ! -z "$EXISTING_WORKERS" ]; then
        echo -e "${YELLOW}Killing existing workers: $EXISTING_WORKERS${NC}"
        pkill -9 -f "python.*run_worker.py" 2>/dev/null
        sleep 1
    fi
    
    # Clean up stale PID files
    rm -f "$API_PID_FILE" "$WORKER_PID_FILE"
    
    echo -e "${GREEN}Cleanup complete${NC}"
}

# Register cleanup on signals
trap cleanup SIGINT SIGTERM EXIT

# Start API server
start_api() {
    echo ""
    echo -e "${BLUE}[API]${NC} Starting on port ${BACKEND_PORT}..."
    echo -e "${BLUE}[API]${NC} Server will be available at http://localhost:${BACKEND_PORT}"
    echo -e "${BLUE}[API]${NC} API documentation at http://localhost:${BACKEND_PORT}/docs"
    echo ""
    
    uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT} --reload 2>&1 | sed "s/^/[API] /" &
    API_PID=$!
    echo $API_PID > "$API_PID_FILE"
    echo -e "${GREEN}[API]${NC} Started with PID: $API_PID"
}

# Start Worker
start_worker() {
    echo ""
    echo -e "${BLUE}[WORKER]${NC} Starting pipeline worker..."
    echo ""
    
    ./venv/bin/python run_worker.py 2>&1 | sed "s/^/[WORKER] /" &
    WORKER_PID=$!
    echo $WORKER_PID > "$WORKER_PID_FILE"
    echo -e "${GREEN}[WORKER]${NC} Started with PID: $WORKER_PID"
}

# Display startup banner
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Video Moments Backend${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Mode: ${YELLOW}${MODE}${NC}"
echo -e "Port: ${YELLOW}${BACKEND_PORT}${NC}"
echo -e "${GREEN}========================================${NC}"

# Always cleanup before starting
kill_existing_processes

# Run based on mode
case $MODE in
    all)
        start_api
        sleep 2  # Let API start first
        start_worker
        echo ""
        echo -e "${GREEN}Both API and Worker started successfully!${NC}"
        echo -e "${YELLOW}Press Ctrl+C to stop all processes${NC}"
        ;;
    api)
        start_api
        echo ""
        echo -e "${GREEN}API server started successfully!${NC}"
        echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
        ;;
    worker)
        start_worker
        echo ""
        echo -e "${GREEN}Worker started successfully!${NC}"
        echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
        ;;
esac

# Wait for all background processes
wait
