# Auto restart for the farm app (Streamlit). Run every minute by Task Scheduler via app_watch.vbs.
# Restarts the app when any mushroom_app\*.py file is newer than the last start,
# or when RESTART_REQUEST.txt exists. Also starts the app if it is not running.
# If git_sync.on exists (made by the GitHub setup bat), it also commits/pulls/pushes with GitHub every minute,
# so code pushed by anyone arrives here and is restarted within ~2 minutes.
$ErrorActionPreference = 'SilentlyContinue'
$app   = Split-Path -Parent $MyInvocation.MyCommand.Path
$stamp = Join-Path $app 'app_started.stamp'
$flag  = Join-Path $app 'RESTART_REQUEST.txt'
$log   = Join-Path $app 'watch_log.txt'
$lock  = Join-Path $app 'app_watch.lock'
function Log($m) { Add-Content -Path $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $m) }

# one watcher at a time
if (Test-Path $lock) {
    if (((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes -lt 5) { exit }
}
Set-Content -Path $lock -Value $PID

try {
    # ---- GitHub sync (only after the GitHub setup bat created git_sync.on) ----
    # commit local changes (e.g. files Claude or a person wrote on this PC), pull others' changes, push.
    if ((Test-Path (Join-Path $app 'git_sync.on')) -and (Test-Path (Join-Path $app '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
        $env:GIT_TERMINAL_PROMPT = '0'          # never wait for a password in the hidden window
        $env:GCM_INTERACTIVE = 'never'
        $glog = Join-Path $app 'git_log.txt'
        Push-Location $app
        try {
            # skip while an update is still being written (a changed file was written in the last 20 s)
            $dirty = @(git status --porcelain 2>$null)
            $busy = $false
            foreach ($l in $dirty) {
                if ($l.Length -lt 4) { continue }
                $rel = ($l.Substring(3) -replace '^.* -> ', '').Trim('"') -replace '/', '\'
                $f = Join-Path $app $rel
                if ((Test-Path $f) -and ((Get-Item $f).LastWriteTime -gt (Get-Date).AddSeconds(-20))) { $busy = $true }
            }
            if (-not $busy) {
                if ($dirty.Count -gt 0) {
                    git add -A 2>&1 | Out-Null
                    git commit -q -m ('auto: main PC ' + (Get-Date -Format 'yyyy-MM-dd HH:mm')) 2>&1 | Out-Null
                    if ($LASTEXITCODE -eq 0) { Log ('git commit: ' + $dirty.Count + ' file(s) changed on this PC') }
                }
                $failflag = Join-Path $app 'git_fail.flag'
                $before = git rev-parse HEAD 2>$null
                $out = git pull --no-rebase --no-edit -X ours origin main 2>&1
                if ($LASTEXITCODE -ne 0) {
                    git merge --abort 2>&1 | Out-Null
                    Add-Content -Path $glog -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' pull failed: ' + ($out -join ' | '))
                    if (-not (Test-Path $failflag)) { Log 'git pull FAILED (see git_log.txt) - will keep trying every minute'; Set-Content -Path $failflag -Value 1 }
                } else {
                    if (Test-Path $failflag) { Remove-Item $failflag -Force; Log 'git pull OK again' }
                    $after = git rev-parse HEAD 2>$null
                    if ($after -ne $before) { Log 'git pull: received changes from GitHub' }
                    $ahead = git rev-list --count origin/main..HEAD 2>$null
                    if ($ahead -match '^\d+$' -and [int]$ahead -gt 0) {
                        $out = git push -q origin main 2>&1
                        if ($LASTEXITCODE -eq 0) { Log ('git push: ' + $ahead + ' commit(s) sent to GitHub') }
                        else { Add-Content -Path $glog -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' push failed: ' + ($out -join ' | ')); Log 'git push FAILED (see git_log.txt)' }
                    }
                }
            }
        } finally { Pop-Location }
    }

    $port = 8501
    $pf = Join-Path $app 'app_port.txt'
    if (Test-Path $pf) { $p = (Get-Content $pf -TotalCount 1).Trim(); if ($p -match '^\d+$') { $port = [int]$p } }

    $files  = Get-ChildItem -Path (Join-Path $app 'mushroom_app') -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '__pycache__' }
    $newest = ($files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
    if (-not $newest) { exit }
    $newTicks = $newest.Ticks

    $listen = @(Get-NetTCPConnection -LocalPort $port -State Listen)
    $running = $listen.Count -gt 0

    if (Test-Path $stamp) {
        $oldTicks = [int64]((Get-Content $stamp -TotalCount 1).Trim())
    } else {
        $oldTicks = 0                       # first run: restart once so the newest code is loaded
        Log "first run"
    }
    # right after boot the logon task (start_app.vbs) starts the app - do not race it
    if (-not $running) {
        $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
        if ($boot -and ((Get-Date) - $boot).TotalMinutes -lt 5) { exit }
    }

    $changed = $newTicks -gt $oldTicks
    $asked   = Test-Path $flag
    if (-not ($changed -or $asked -or -not $running)) { exit }

    # wait until the update finished (no file written in the last 20 seconds)
    if ($changed) {
        $age = ((Get-Date).ToUniversalTime() - $newest).TotalSeconds
        if ($age -lt 20) { Start-Sleep -Seconds ([int](21 - $age)) }
        $files  = Get-ChildItem -Path (Join-Path $app 'mushroom_app') -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '__pycache__' }
        $newTicks = ($files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc.Ticks
    }

    $why = @()
    if ($changed) { $why += 'code updated' }
    if ($asked)   { $why += 'restart requested' }
    if (-not $running) { $why += 'app was not running' }

    # stop: every process listening on the app port (same as stop_app.bat)
    foreach ($c in @(Get-NetTCPConnection -LocalPort $port -State Listen)) {
        Stop-Process -Id $c.OwningProcess -Force
        Log ("stopped pid " + $c.OwningProcess)
    }
    Start-Sleep -Seconds 3

    # start hidden (same as start_app.vbs -> serve_app.bat)
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', ('"' + (Join-Path $app 'serve_app.bat') + '"') -WorkingDirectory $app -WindowStyle Hidden
    Set-Content -Path $stamp -Value $newTicks
    if ($asked) { Remove-Item $flag -Force }

    # check it came up (up to 60 s)
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (@(Get-NetTCPConnection -LocalPort $port -State Listen).Count -gt 0) { $ok = $true; break }
    }
    Log ("restart (" + ($why -join ', ') + ") -> " + $(if ($ok) { 'running on port ' + $port } else { 'NOT running - see app_log.txt' }))
}
finally {
    Remove-Item $lock -Force
}
