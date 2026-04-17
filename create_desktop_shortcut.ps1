$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcherPath = Join-Path $projectPath "run_knife_inventory_app.bat"
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "Knife Inventory.lnk"

$wshShell = New-Object -ComObject WScript.Shell
$shortcut = $wshShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $projectPath
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
$shortcut.Save()

Write-Host "Shortcut created: $shortcutPath"
