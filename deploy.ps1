# GitDocs Bot PowerShell Deployment Script

Write-Host "🚀 Starting GitDocs Bot deployment..." -ForegroundColor Green

# Check if docker is installed
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Docker is not installed. Please install Docker first." -ForegroundColor Red
    exit 1
}

# Check if docker-compose is installed
if (!(Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    Write-Host "⚠️  Docker Compose is not installed." -ForegroundColor Yellow
    Write-Host "❌ Please install Docker Desktop which includes Docker Compose." -ForegroundColor Red
    exit 1
}

# Check if .env file exists
if (!(Test-Path ".env")) {
    Write-Host "⚠️  .env file not found. Creating a template..." -ForegroundColor Yellow
    @"
# GitDocs Bot Configuration
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=309462378
AUTO_UNLOCK_ON_UPLOAD=false
"@ | Out-File -FilePath ".env" -Encoding utf8
    Write-Host "📝 Created .env template. Please update it with your bot token and other settings." -ForegroundColor Yellow
    Write-Host "📋 Open the .env file and replace 'your_bot_token_here' with your actual bot token." -ForegroundColor Yellow
    exit 1
}

# Load environment variables by reading the file
$content = Get-Content ".env"
foreach ($line in $content) {
    if ($line -match "^([^=]+)=(.*)") {
        $key = $matches[1]
        $value = $matches[2]
        [System.Environment]::SetEnvironmentVariable($key, $value)
    }
}

# Validate BOT_TOKEN is not default
$botToken = [System.Environment]::GetEnvironmentVariable("BOT_TOKEN")
if ($botToken -eq "your_bot_token_here" -or [string]::IsNullOrWhiteSpace($botToken)) {
    Write-Host "❌ BOT_TOKEN is not set properly in .env file. Please update it." -ForegroundColor Red
    exit 1
}

Write-Host "✅ Environment variables loaded successfully" -ForegroundColor Green

# Create necessary directories if they don't exist
New-Item -ItemType Directory -Path "user_repos" -Force | Out-Null
New-Item -ItemType Directory -Path "logs" -Force | Out-Null

# Build and start the containers
Write-Host "🐳 Building and starting Docker containers..." -ForegroundColor Cyan
docker-compose up -d --build

# Wait a moment for the container to start
Start-Sleep -Seconds 5

# Check if the container is running
$containerStatus = docker-compose ps git-docs-bot
if ($containerStatus -match "Up") {
    Write-Host "✅ GitDocs Bot deployed successfully!" -ForegroundColor Green
    Write-Host "📊 Bot logs are available with: docker-compose logs -f git-docs-bot" -ForegroundColor Cyan
    Write-Host "🔄 To restart the bot: docker-compose restart git-docs-bot" -ForegroundColor Cyan
    Write-Host "🛑 To stop the bot: docker-compose down" -ForegroundColor Cyan
} else {
    Write-Host "❌ Docker container failed to start properly" -ForegroundColor Red
    Write-Host "📋 Checking logs..." -ForegroundColor Yellow
    docker-compose logs git-docs-bot
    exit 1
}