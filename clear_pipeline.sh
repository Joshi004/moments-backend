#!/bin/bash

# Emergency pipeline cleanup script wrapper
# Auto-activates virtual environment and runs Python cleanup

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: Virtual environment not found at venv/${NC}"
    echo -e "${YELLOW}Please run ./start_backend.sh first to create the virtual environment${NC}"
    exit 1
fi

# Activate virtual environment
echo -e "${BLUE}Activating virtual environment...${NC}"
source venv/bin/activate

if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to activate virtual environment${NC}"
    exit 1
fi

# Run Python cleanup script with all passed arguments
echo -e "${GREEN}Running pipeline cleanup...${NC}"
python3 clear_pipeline.py "$@"
CLEANUP_EXIT_CODE=$?

# Deactivate virtual environment
deactivate

# Exit with the same code as the Python script
if [ $CLEANUP_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}Virtual environment deactivated${NC}"
else
    echo -e "${YELLOW}Cleanup completed with errors (exit code: $CLEANUP_EXIT_CODE)${NC}"
fi

exit $CLEANUP_EXIT_CODE
