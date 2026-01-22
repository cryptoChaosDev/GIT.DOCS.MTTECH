#!/bin/bash
# GitLab Integration Tests Runner for Docker Environment

set -e

echo "🚀 Starting GitLab Integration Tests..."
echo "======================================"

# Check if running in Docker container
if [ ! -f /.dockerenv ]; then
    echo "⚠️  Warning: Not running in Docker container"
    echo "This script is designed to run inside the bot's Docker container"
fi

# Check required tools
echo "🔧 Checking environment..."

# Python check
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found"
    exit 1
fi

# Git check
if ! command -v git &> /dev/null; then
    echo "❌ Git not found"
    exit 1
fi

# Git LFS check
if ! command -v git-lfs &> /dev/null; then
    echo "❌ Git LFS not found"
    exit 1
fi

echo "✅ Environment checks passed"

# Install test dependencies if needed
echo "📦 Installing test dependencies..."
pip install requests pytest mock || echo "Dependencies already installed"

# Create test directory structure
TEST_DIR="/app/tests"
mkdir -p "$TEST_DIR"

# Copy test files if they exist in the mounted volume
if [ -f "/workspace/tests/test_gitlab_integration.py" ]; then
    cp /workspace/tests/test_gitlab_integration.py "$TEST_DIR/"
    echo "✅ Test files copied"
else
    echo "⚠️  Test files not found in mounted volume"
fi

# Run tests
echo "🧪 Running GitLab Integration Tests..."
echo "--------------------------------------"

cd "$TEST_DIR"

# Run the test suite
python3 test_gitlab_integration.py

# Capture exit code
TEST_EXIT_CODE=$?

echo ""
echo "======================================"

if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "🎉 All GitLab integration tests passed!"
else
    echo "💥 Some GitLab integration tests failed!"
fi

exit $TEST_EXIT_CODE