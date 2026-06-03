#!/bin/bash

# SRAF Launcher Script
# Simple wrapper to run SRAF with proper environment setup

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Ensure we're in the right directory
cd "$SCRIPT_DIR"

# Activate virtual environment if not already active
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo -e "${BLUE}Activating virtual environment...${NC}"
    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
        python3 -m venv .venv
    fi
    source .venv/bin/activate
fi

# Load environment variables from .env file
if [ -f .env ]; then
    echo -e "${BLUE}Loading credentials from .env...${NC}"
    export $(cat .env | grep -v '#' | xargs)
else
    echo -e "${YELLOW}Warning: .env file not found${NC}"
fi

# Check if credentials are set
if [ -z "$GIGACHAT_CREDENTIALS" ]; then
    echo -e "${YELLOW}Warning: GIGACHAT_CREDENTIALS is not set${NC}"
fi

# Pass all arguments to sraf
exec sraf "$@"
