<#
  Ставит задачу в планировщик: поднимать супервизор при входе в систему.

  Смысл — не удобство, а ребуты. Машина уходит в перезагрузку пару раз в день,
  и без автозапуска каждый такой ребут = склады офлайн до тех пор, пока их
  не поднимут руками.

  Установить:  powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
  Снять:       powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Remove
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"

$TaskName = "brainbot-supervisor"
$Root     = Split-Path -Parent $PSScriptRoot
$Python   = (Get-Command python).Source
$RunPy    = Join-Path $Root "run.py"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        "Задача $TaskName снята."
    } else {
        "Задачи $TaskName нет."
    }
    return
}

if (-not (Test-Path $RunPy)) { throw "Не найден $RunPy" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$RunPy`" up" -WorkingDirectory $Root

# При входе в систему, а не при загрузке: клиенту Roblox нужна живая сессия
# с рабочим столом — без неё окно не отрисуется и захват экрана не сработает.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Держит аккаунты Steal a Brainrot в игре" | Out-Null

"Задача $TaskName поставлена."
"  python:  $Python"
"  скрипт:  $RunPy up"
"  запуск:  при входе в систему, с перезапуском каждые 2 минуты при падении"
""
"Проверить:  Get-ScheduledTask -TaskName $TaskName"
"Запустить:  Start-ScheduledTask -TaskName $TaskName"
