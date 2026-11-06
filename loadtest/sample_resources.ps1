# Sample the app process (CPU % of one core, memory) and the PostgreSQL container (CPU %, memory) every 10 s.
# usage: powershell -File loadtest/sample_resources.ps1 -AppPid <uvicorn pid> -Out results/resources.csv [-Seconds 640]
#        [-Project <folder with docker-compose.yml>]   (default: the folder above this script)
param([int]$AppPid, [string]$Out, [int]$Seconds = 640, [string]$Project = (Split-Path $PSScriptRoot -Parent))
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) { $docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$Out = [IO.Path]::GetFullPath($Out)
if (-not (Test-Path (Join-Path $Project 'docker-compose.yml'))) { throw "No docker-compose.yml in $Project - pass -Project" }
Set-Location $Project
$db = "$(& $docker compose ps -q db)".Trim()
# an empty id would make `docker stats` list EVERY container and the first one (e.g. the AI model) be sampled
if (-not $db) { throw "The database container isn't running (docker compose ps -q db returned nothing)" }
"t_s,epoch,app_cpu_pct,app_mem_mb,db_cpu_pct,db_mem_mb" | Out-File -FilePath $Out -Encoding utf8
$start = Get-Date
$p = Get-Process -Id $AppPid
$prevCpu = $p.TotalProcessorTime.TotalSeconds; $prevT = Get-Date
while (((Get-Date) - $start).TotalSeconds -lt $Seconds) {
    Start-Sleep -Seconds 10
    try { $p = Get-Process -Id $AppPid -ErrorAction Stop } catch { break }
    $now = Get-Date
    $cpu = [math]::Round(($p.TotalProcessorTime.TotalSeconds - $prevCpu) / ($now - $prevT).TotalSeconds * 100, 1)
    $prevCpu = $p.TotalProcessorTime.TotalSeconds; $prevT = $now
    $mem = [math]::Round($p.WorkingSet64 / 1MB, 1)
    $stats = (& $docker stats --no-stream --format '{{.CPUPerc}};{{.MemUsage}}' $db) -split ';'
    $dbCpu = $stats[0].TrimEnd('%')
    $dbMem = ($stats[1] -split '/')[0].Trim()
    $dbMb = if ($dbMem -match '([\d.]+)GiB') { [math]::Round([double]$Matches[1] * 1024, 1) } elseif ($dbMem -match '([\d.]+)MiB') { [double]$Matches[1] } else { 0 }
    $epoch = [math]::Round(([DateTimeOffset]$now).ToUnixTimeMilliseconds() / 1000, 1)
    "{0},{1},{2},{3},{4},{5}" -f [math]::Round(($now - $start).TotalSeconds), $epoch, $cpu, $mem, $dbCpu, $dbMb | Out-File -FilePath $Out -Append -Encoding utf8
}
