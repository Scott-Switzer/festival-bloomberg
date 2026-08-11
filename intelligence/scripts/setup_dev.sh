#!/bin/bash
# Local development environment setup script for Festival Intelligence Terminal

set -e

echo "========================================="
echo "Festival Intelligence Terminal Setup"
echo "========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Found Python $python_version"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env 2>/dev/null || cat > .env << EOF
# Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=festival_intelligence
DB_USER=postgres
DB_PASSWORD=

# Monid.ai Configuration
MONID_API_KEY=
MONID_BASE_URL=https://api.monid.ai
MONID_MCP_URL=https://mcp.monid.ai/v1

# Environment
ENVIRONMENT=development

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/festival_intelligence.log
EOF
    echo "⚠  Please update .env with your configuration"
fi

# Create logs directory
mkdir -p logs

# Initialize database
echo ""
echo "Initializing database..."
python scripts/init_database.py

echo ""
echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Update .env with your configuration"
echo "2. Start the API server: cd apps/api && python main.py"
echo "3. Start the frontend: cd apps/web && npm run dev"
echo ""
echo "API will be available at http://localhost:8000"
echo "Frontend will be available at http://localhost:3000"
echo ""
