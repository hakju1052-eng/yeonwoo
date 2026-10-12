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

    # ---- apps: main app (app_port.txt, default 8501) + management-data app (office_port.txt, default 8502) ----
    $files  = Get-ChildItem -Path (Join-Path $app 'mushroom_app') -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '__pycache__' }
    $newest = ($files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
    if (-not $newest) { exit }
    $asked = Test-Path $flag
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $justBooted = ($boot -and ((Get-Date) - $boot).TotalMinutes -lt 5)

    function Get-Port($file, $default) {
        $pf = Join-Path $app $file
        if (Test-Path $pf) { $p = (Get-Content $pf -TotalCount 1).Trim(); if ($p -match '^\d+$') { return [int]$p } }
        return $default
    }
    $apps = @(@{ name = 'app';    port = (Get-Port 'app_port.txt' 8501);    bat = 'serve_app.bat';    stamp = 'app_started.stamp';    log = 'app_log.txt' })
    if (Test-Path (Join-Path $app 'serve_office.bat')) {
        $apps += @{ name = 'office'; port = (Get-Port 'office_port.txt' 8502); bat = 'serve_office.bat'; stamp = 'office_started.stamp'; log = 'office_log.txt' }
    }

    $settled = $false
    foreach ($a in $apps) {
        $port = $a.port
        $stampF = Join-Path $app $a.stamp
        $running = @(Get-NetTCPConnection -LocalPort $port -State Listen).Count -gt 0
        if (Test-Path $stampF) { $oldTicks = [int64]((Get-Content $stampF -TotalCount 1).Trim()) } else { $oldTicks = 0; Log ($a.name + ' first run') }
        # right after boot the logon task starts the main app - do not race it
        if (-not $running -and $justBooted -and $a.name -eq 'app') { continue }
        $changed = $newest.Ticks -gt $oldTicks
        if (-not ($changed -or $asked -or -not $running)) { continue }

        # wait until the update finished (no file written in the last 20 seconds) - once for all apps
        if ($changed -and -not $settled) {
            $age = ((Get-Date).ToUniversalTime() - $newest).TotalSeconds
            if ($age -lt 20) { Start-Sleep -Seconds ([int](21 - $age)) }
            $files  = Get-ChildItem -Path (Join-Path $app 'mushroom_app') -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '__pycache__' }
            $newest = ($files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
            $settled = $true
        }

        $why = @()
        if ($changed) { $why += 'code updated' }
        if ($asked)   { $why += 'restart requested' }
        if (-not $running) { $why += 'not running' }

        foreach ($c in @(Get-NetTCPConnection -LocalPort $port -State Listen)) {
            Stop-Process -Id $c.OwningProcess -Force
            Log ($a.name + ' stopped pid ' + $c.OwningProcess)
        }
        Start-Sleep -Seconds 3
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', ('"' + (Join-Path $app $a.bat) + '"') -WorkingDirectory $app -WindowStyle Hidden
        Set-Content -Path $stampF -Value $newest.Ticks

        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            if (@(Get-NetTCPConnection -LocalPort $port -State Listen).Count -gt 0) { $ok = $true; break }
        }
        Log ($a.name + ' restart (' + ($why -join ', ') + ') -> ' + $(if ($ok) { 'running on port ' + $port } else { 'NOT running - see ' + $a.log }))
    }
    if ($asked) { Remove-Item $flag -Force }
}
finally {
    Remove-Item $lock -Force
}
