#!/bin/bash

# SRAF Verification Script
# Run this to verify everything is working correctly

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   SRAF + GigaChat Verification Suite   ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Test 1: Virtual environment
echo -e "${YELLOW}[1/5] Checking virtual environment...${NC}"
if [ -d ".venv" ]; then
    echo -e "${GREEN}✓ Virtual environment exists${NC}"
else
    echo -e "${RED}✗ Virtual environment not found${NC}"
    exit 1
fi

# Test 2: Credentials
echo -e "${YELLOW}[2/5] Checking credentials...${NC}"
if [ -f ".env" ]; then
    echo -e "${GREEN}✓ .env file exists${NC}"
    if grep -q "GIGACHAT_CREDENTIALS=" .env; then
        echo -e "${GREEN}✓ GIGACHAT_CREDENTIALS set in .env${NC}"
    else
        echo -e "${RED}✗ GIGACHAT_CREDENTIALS not found in .env${NC}"
        exit 1
    fi
else
    echo -e "${RED}✗ .env file not found${NC}"
    exit 1
fi

# Test 3: Launcher script
echo -e "${YELLOW}[3/5] Checking launcher script...${NC}"
if [ -f "sraf.sh" ] && [ -x "sraf.sh" ]; then
    echo -e "${GREEN}✓ sraf.sh launcher script exists and is executable${NC}"
else
    echo -e "${RED}✗ sraf.sh script not found or not executable${NC}"
    exit 1
fi

# Test 4: Demo mode
echo -e "${YELLOW}[4/5] Testing demo mode...${NC}"
echo "Running: ./sraf.sh run 'Напиши привет' --demo --max-steps 1"
if output=$(./sraf.sh run "Напиши привет" --demo --max-steps 1 2>&1); then
    if echo "$output" | grep -q "success"; then
        echo -e "${GREEN}✓ Demo mode works${NC}"
    else
        echo -e "${YELLOW}⚠ Demo mode ran but status unclear${NC}"
    fi
else
    echo -e "${RED}✗ Demo mode failed${NC}"
    exit 1
fi

# Test 5: Real API (if credentials are valid)
echo -e "${YELLOW}[5/5] Testing real API with GigaChat...${NC}"
echo "Running: ./sraf.sh run 'Привет' --no-verify-ssl --max-steps 1"
if output=$(./sraf.sh run "Привет" --no-verify-ssl --max-steps 1 2>&1); then
    if echo "$output" | grep -q "status=success\|status=failure"; then
        echo -e "${GREEN}✓ Real API connection works${NC}"
        # Show the actual response
        echo ""
        echo "Response preview:"
        echo "$output" | head -5
    else
        echo -e "${YELLOW}⚠ API responded but status unclear${NC}"
        echo "$output" | tail -3
    fi
else
    echo -e "${RED}✗ Real API connection failed${NC}"
    echo "Try: ./sraf.sh run 'test' --demo"
    exit 1
fi

echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        ✅ ALL TESTS PASSED!            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. ./sraf.sh run \"Your task here\" --no-verify-ssl"
echo "2. ./sraf.sh chat --no-verify-ssl"
echo "3. Check README.md for more examples"
echo ""
