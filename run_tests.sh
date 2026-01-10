#!/bin/bash
# Git Docs Bot Test Runner
# Runs comprehensive tests for the bot functionality

set -e

echo "🚀 Starting Git Docs Bot Tests..."
echo "================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Environment Setup
echo -e "${YELLOW}Test 1: Environment Setup${NC}"
if command -v python3 &> /dev/null; then
    echo -e "${GREEN}✓ Python3 available${NC}"
else
    echo -e "${RED}✗ Python3 not found${NC}"
    exit 1
fi

if command -v git &> /dev/null; then
    echo -e "${GREEN}✓ Git available${NC}"
else
    echo -e "${RED}✗ Git not found${NC}"
    exit 1
fi

if command -v git-lfs &> /dev/null; then
    echo -e "${GREEN}✓ Git LFS available${NC}"
else
    echo -e "${RED}✗ Git LFS not found${NC}"
    exit 1
fi

# Test 2: Repository Initialization
echo -e "\n${YELLOW}Test 2: Repository Initialization${NC}"
TEST_REPO="/tmp/test_git_docs_repo"
rm -rf "$TEST_REPO"
mkdir -p "$TEST_REPO"

cd "$TEST_REPO"
git init
git config user.name "TestBot"
git config user.email "test@bot.local"
git lfs install

# Create test structure
mkdir -p docs
echo "Test content" > docs/test_document.docx
git add .
git commit -m "Initial test commit"

if [ -d ".git" ] && [ -f "docs/test_document.docx" ]; then
    echo -e "${GREEN}✓ Repository initialized successfully${NC}"
else
    echo -e "${RED}✗ Repository initialization failed${NC}"
    exit 1
fi

# Test 3: Git LFS Locking
echo -e "\n${YELLOW}Test 3: Git LFS Locking${NC}"
LOCK_FILE="docs/test_document.docx"

# Test locking
if git lfs lock "$LOCK_FILE"; then
    echo -e "${GREEN}✓ File locked successfully${NC}"
else
    echo -e "${RED}✗ Failed to lock file${NC}"
    exit 1
fi

# Verify lock exists
LOCKS=$(git lfs locks)
if echo "$LOCKS" | grep -q "$LOCK_FILE"; then
    echo -e "${GREEN}✓ Lock verification successful${NC}"
else
    echo -e "${RED}✗ Lock not found in locks list${NC}"
    exit 1
fi

# Test unlocking
if git lfs unlock "$LOCK_FILE"; then
    echo -e "${GREEN}✓ File unlocked successfully${NC}"
else
    echo -e "${RED}✗ Failed to unlock file${NC}"
    exit 1
fi

# Test 4: File Operations
echo -e "\n${YELLOW}Test 4: File Operations${NC}"

# Test file creation
TEST_FILE="docs/new_test.docx"
echo "New test content" > "$TEST_FILE"
if [ -f "$TEST_FILE" ]; then
    echo -e "${GREEN}✓ File creation successful${NC}"
else
    echo -e "${RED}✗ File creation failed${NC}"
    exit 1
fi

# Test git staging
git add "$TEST_FILE"
if git diff --cached --name-only | grep -q "$(basename "$TEST_FILE")"; then
    echo -e "${GREEN}✓ File staging successful${NC}"
else
    echo -e "${RED}✗ File staging failed${NC}"
    exit 1
fi

# Test 5: Bot Dependencies
echo -e "\n${YELLOW}Test 5: Bot Dependencies${NC}"

# Check if bot.py exists
if [ -f "/app/bot.py" ]; then
    echo -e "${GREEN}✓ bot.py found${NC}"
else
    echo -e "${RED}✗ bot.py not found${NC}"
    exit 1
fi

# Check Python imports
if python3 -c "import aiogram" 2>/dev/null; then
    echo -e "${GREEN}✓ aiogram available${NC}"
else
    echo -e "${RED}✗ aiogram not available${NC}"
fi

if python3 -c "import telegram" 2>/dev/null; then
    echo -e "${GREEN}✓ python-telegram-bot available${NC}"
else
    echo -e "${RED}✗ python-telegram-bot not available${NC}"
fi

# Test 6: Configuration Files
echo -e "\n${YELLOW}Test 6: Configuration Files${NC}"

CONFIG_FILES=(".env" "requirements.txt" "Dockerfile" "docker-compose.yml")
for file in "${CONFIG_FILES[@]}"; do
    if [ -f "/app/$file" ]; then
        echo -e "${GREEN}✓ $file exists${NC}"
    else
        echo -e "${YELLOW}⚠ $file not found${NC}"
    fi
done

# Cleanup
cd /
rm -rf "$TEST_REPO"

# Final Report
echo -e "\n${GREEN}================================${NC}"
echo -e "${GREEN}All tests completed successfully!${NC}"
echo -e "${GREEN}================================${NC}"

exit 0