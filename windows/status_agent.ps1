param(
  [Parameter(Mandatory=$true)][string]$PiBaseUrl,
  [Parameter(Mandatory=$true)][string]$Token,
  [int]$PollSeconds = 10,
  [int]$BusyMinutes = 5,
  [string]$BusyDetail = "On a call (mic active)",
  [switch]$HideWindow = $true
)

if ($HideWindow -and -not $env:STATUS_AGENT_BACKGROUND) {
  $env:STATUS_AGENT_BACKGROUND = "1"
  $powershellw = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershellw.exe"
  $argList = @(
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $PSCommandPath
  )

  foreach ($key in $PSBoundParameters.Keys) {
    if ($key -eq "HideWindow") {
      continue
    }
    $value = $PSBoundParameters[$key]
    if ($value -is [System.Management.Automation.SwitchParameter]) {
      if ($value.IsPresent) {
        $argList += "-$key"
      }
    } else {
      $argList += "-$key"
      $argList += "$value"
    }
  }

  Start-Process -FilePath $powershellw -ArgumentList $argList -WindowStyle Hidden | Out-Null
  exit
}

function Hide-ConsoleWindow {
  Add-Type -Namespace StatusScreen -Name WindowUtils -MemberDefinition @"
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("kernel32.dll")]
    public static extern bool FreeConsole();
"@

  $hwnd = (Get-Process -Id $PID).MainWindowHandle
  if ($hwnd -ne [IntPtr]::Zero) {
    [StatusScreen.WindowUtils]::ShowWindowAsync($hwnd, 0) | Out-Null
  }
  [StatusScreen.WindowUtils]::FreeConsole() | Out-Null
}

if ($HideWindow) {
  Hide-ConsoleWindow
}

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

function Clear-Override {
  $uri = "$PiBaseUrl/api/clear"
  try {
    Invoke-RestMethod -Method Post -Uri $uri -Headers @{ "X-Auth-Token" = $Token } | Out-Null
  } catch { }
}

if (-not $HideWindow) {
  Write-Host "Mic agent -> $PiBaseUrl  Poll=${PollSeconds}s Busy=${BusyMinutes}m"
}

$micWasInUse = $false
while ($true) {
  $micInUse = Test-MicInUse
  if ($micInUse) {
    Set-OverrideBusy
  } elseif ($micWasInUse) {
    Clear-Override
  }
  $micWasInUse = $micInUse
  Start-Sleep -Seconds $PollSeconds
}
