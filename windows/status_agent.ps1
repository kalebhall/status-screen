param(
  [Parameter(Mandatory=$true)][string]$PiBaseUrl,
  [Parameter(Mandatory=$true)][string]$Token,
  [int]$PollSeconds = 10,
  [int]$BusyMinutes = 5,
  [string]$BusyDetail = "On a call (mic active)"
)

function Test-MicInUse {
  $paths = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
  )

  foreach ($base in $paths) {
    if (Test-Path $base) {
      Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
        try {
          $p = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
          if ($null -ne $p.LastUsedTimeStop -and $p.LastUsedTimeStop -eq 0) {
            return $true
          }
        } catch {}
      }
    }
  }
  return $false
}

function Set-OverrideBusy {
  $uri = "$PiBaseUrl/api/override"
  $body = @{
    state   = "busy"
    label   = "BUSY"
    detail  = $BusyDetail
    minutes = $BusyMinutes
  } | ConvertTo-Json

  try {
    Invoke-RestMethod -Method Post -Uri $uri -Headers @{ "X-Auth-Token" = $Token } -ContentType "application/json" -Body $body | Out-Null
  } catch { }
}

Write-Host "Mic agent -> $PiBaseUrl  Poll=${PollSeconds}s Busy=${BusyMinutes}m"

while ($true) {
  if (Test-MicInUse) { Set-OverrideBusy }
  Start-Sleep -Seconds $PollSeconds
}
