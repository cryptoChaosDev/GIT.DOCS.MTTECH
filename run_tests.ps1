# Git Docs Bot Test Runner - PowerShell Version
# Runs comprehensive tests for the bot functionality

Write-Host "🚀 Starting Git Docs Bot Tests..." -ForegroundColor Yellow
Write-Host "================================" -ForegroundColor Yellow

$testsPassed = 0
$totalTests = 0

function Run-Test {
    param(
        [string]$TestName,
        [scriptblock]$TestScript
    )
    
    $totalTests++
    Write-Host "`n$TestName" -ForegroundColor Cyan
    
    try {
        & $TestScript
        Write-Host "✓ PASSED" -ForegroundColor Green
        $script:testsPassed++
        return $true
    }
    catch {
        Write-Host "✗ FAILED: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

# Test 1: Environment Setup
Run-Test "Test 1: Environment Setup" {
    # Check Python
    $pythonResult = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonResult) {
        throw "Python not found"
    }
    Write-Host "  ✓ Python available"
    
    # Check Git
    $gitResult = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitResult) {
        throw "Git not found"
    }
    Write-Host "  ✓ Git available"
    
    # Check Git LFS
    $lfsResult = git lfs version 2>$null
    if (-not $lfsResult) {
        throw "Git LFS not found"
    }
    Write-Host "  ✓ Git LFS available"
}

# Test 2: Repository Initialization
Run-Test "Test 2: Repository Initialization" {
    $testRepo = "$env:TEMP\test_git_docs_repo"
    if (Test-Path $testRepo) {
        Remove-Item $testRepo -Recurse -Force
    }
    New-Item -ItemType Directory -Path $testRepo -Force | Out-Null
    
    Set-Location $testRepo
    git init | Out-Null
    git config user.name "TestBot"
    git config user.email "test@bot.local"
    git lfs install | Out-Null
    
    # Create test structure
    New-Item -ItemType Directory -Path "docs" -Force | Out-Null
    "Test content" > "docs\test_document.docx"
    git add .
    git commit -m "Initial test commit" | Out-Null
    
    if (-not (Test-Path ".git") -or -not (Test-Path "docs\test_document.docx")) {
        throw "Repository initialization failed"
    }
    Write-Host "  ✓ Repository initialized successfully"
    
    # Cleanup
    Set-Location $env:TEMP
    Remove-Item $testRepo -Recurse -Force
}

# Test 3: Git LFS Locking
Run-Test "Test 3: Git LFS Locking" {
    $testRepo = "$env:TEMP\test_git_docs_repo_lock"
    New-Item -ItemType Directory -Path $testRepo -Force | Out-Null
    Set-Location $testRepo
    
    git init | Out-Null
    git config user.name "TestBot"
    git config user.email "test@bot.local"
    git lfs install | Out-Null
    
    New-Item -ItemType Directory -Path "docs" -Force | Out-Null
    "Test content" > "docs\test_document.docx"
    git add .
    git commit -m "Initial commit" | Out-Null
    
    # Test locking
    $lockResult = git lfs lock "docs/test_document.docx" 2>$null
    if (-not $lockResult) {
        throw "Failed to lock file"
    }
    Write-Host "  ✓ File locked successfully"
    
    # Verify lock exists
    $locks = git lfs locks
    if ($locks -notmatch "docs/test_document.docx") {
        throw "Lock not found in locks list"
    }
    Write-Host "  ✓ Lock verification successful"
    
    # Test unlocking
    $unlockResult = git lfs unlock "docs/test_document.docx" 2>$null
    if (-not $unlockResult) {
        throw "Failed to unlock file"
    }
    Write-Host "  ✓ File unlocked successfully"
    
    # Cleanup
    Set-Location $env:TEMP
    Remove-Item $testRepo -Recurse -Force
}

# Test 4: File Operations
Run-Test "Test 4: File Operations" {
    $testRepo = "$env:TEMP\test_git_docs_repo_fileops"
    New-Item -ItemType Directory -Path $testRepo -Force | Out-Null
    Set-Location $testRepo
    
    git init | Out-Null
    New-Item -ItemType Directory -Path "docs" -Force | Out-Null
    
    # Test file creation
    "New test content" > "docs\new_test.docx"
    if (-not (Test-Path "docs\new_test.docx")) {
        throw "File creation failed"
    }
    Write-Host "  ✓ File creation successful"
    
    # Test git staging
    git add "docs\new_test.docx"
    $stagedFiles = git diff --cached --name-only
    if ($stagedFiles -notmatch "new_test.docx") {
        throw "File staging failed"
    }
    Write-Host "  ✓ File staging successful"
    
    # Cleanup
    Set-Location $env:TEMP
    Remove-Item $testRepo -Recurse -Force
}

# Test 5: Bot Dependencies (if running in container)
if (Test-Path "/app/bot.py") {
    Run-Test "Test 5: Bot Dependencies" {
        # Check if bot.py exists
        if (-not (Test-Path "/app/bot.py")) {
            throw "bot.py not found"
        }
        Write-Host "  ✓ bot.py found"
        
        # Check Python modules (basic check)
        try {
            python -c "import aiogram" 2>$null
            Write-Host "  ✓ aiogram available"
        }
        catch {
            Write-Host "  ⚠ aiogram not available"
        }
        
        try {
            python -c "import telegram" 2>$null
            Write-Host "  ✓ python-telegram-bot available"
        }
        catch {
            Write-Host "  ⚠ python-telegram-bot not available"
        }
    }
}

# Test 6: Configuration Files
Run-Test "Test 6: Configuration Files" {
    $configFiles = @(".env", "requirements.txt", "Dockerfile", "docker-compose.yml")
    foreach ($file in $configFiles) {
        $filePath = Join-Path $pwd.Path $file
        if (Test-Path $filePath) {
            Write-Host "  ✓ $file exists"
        }
        else {
            Write-Host "  ⚠ $file not found"
        }
    }
}

# Final Report
Write-Host "`n================================" -ForegroundColor Green
Write-Host "TEST RESULTS SUMMARY" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host "Total tests: $totalTests"
Write-Host "Passed: $testsPassed"
Write-Host "Failed: $($totalTests - $testsPassed)"
$successRate = [math]::Round(($testsPassed / $totalTests) * 100, 1)
Write-Host "Success rate: $successRate%" -ForegroundColor $(if ($successRate -eq 100) { "Green" } else { "Yellow" })

if ($testsPassed -eq $totalTests) {
    Write-Host "`n🎉 All tests passed!" -ForegroundColor Green
    exit 0
}
else {
    Write-Host "`n❌ Some tests failed" -ForegroundColor Red
    exit 1
}