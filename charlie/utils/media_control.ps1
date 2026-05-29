# C.H.A.R.L.I.E. - Universal PowerShell Media Control (WinRT)
# Broadcasts playback commands to ALL active media sessions.

try {
    # Ensure WinRT namespaces are available
    [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
}
catch { }

function Get-MediaSessions {
    try {
        $manager = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync().GetAwaiter().GetResult()
        $sessions = $manager.GetSessions()
        if ($null -eq $sessions) { return @() }
        return $sessions
    }
    catch { return @() }
}

function Play-Media {
    $sessions = Get-MediaSessions
    foreach ($session in $sessions) {
        try { $session.TryPlayAsync().GetAwaiter().GetResult() | Out-Null } catch { }
    }
}

function Pause-Media {
    $sessions = Get-MediaSessions
    foreach ($session in $sessions) {
        try { $session.TryPauseAsync().GetAwaiter().GetResult() | Out-Null } catch { }
    }
}

function Stop-Media {
    $sessions = Get-MediaSessions
    foreach ($session in $sessions) {
        try { $session.TryStopAsync().GetAwaiter().GetResult() | Out-Null } catch { }
    }
}

function Toggle-Media {
    $sessions = Get-MediaSessions
    foreach ($session in $sessions) {
        try { $session.TryTogglePlayPauseAsync().GetAwaiter().GetResult() | Out-Null } catch { }
    }
}

function Next-Media {
    $sessions = Get-MediaSessions
    foreach ($session in $sessions) {
        try { $session.TrySkipNextAsync().GetAwaiter().GetResult() | Out-Null } catch { }
    }
}

function Previous-Media {
    $sessions = Get-MediaSessions
    foreach ($session in $sessions) {
        try { $session.TrySkipPreviousAsync().GetAwaiter().GetResult() | Out-Null } catch { }
    }
}

$action = $args[0]
if (-not $action) { exit }

switch ($action.ToLower()) {
    "play" { Play-Media }
    "pause" { Pause-Media }
    "stop" { Stop-Media }
    "toggle" { Toggle-Media }
    "next" { Next-Media }
    "previous" { Previous-Media }
    "prev" { Previous-Media }
}
