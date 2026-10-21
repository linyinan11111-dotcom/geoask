# Windows desktop location API. Uses hardware GPS / Wi-Fi as provided by Windows.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$watcher = $null
try {
    Add-Type -AssemblyName System.Device
    $watcher = New-Object System.Device.Location.GeoCoordinateWatcher
    $ready = $watcher.TryStart($false, [TimeSpan]::FromSeconds(22))
    if ($watcher.Permission.ToString() -eq 'Denied') {
        @{ status = 'denied' } | ConvertTo-Json -Compress
        exit 0
    }
    if (-not $ready -or $watcher.Position.Location.IsUnknown) {
        @{ status = 'unavailable'; device_status = $watcher.Status.ToString() } | ConvertTo-Json -Compress
        exit 0
    }
    $position = $watcher.Position
    $coordinate = $position.Location
    $accuracy = $coordinate.HorizontalAccuracy
    if ([Double]::IsNaN($accuracy) -or [Double]::IsInfinity($accuracy)) { $accuracy = $null }
    @{
        status = 'ok'
        longitude = $coordinate.Longitude
        latitude = $coordinate.Latitude
        accuracy_m = $accuracy
        timestamp = $position.Timestamp.ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Compress
}
catch {
    @{ status = 'error'; detail = $_.Exception.GetType().Name } | ConvertTo-Json -Compress
}
finally {
    if ($watcher) { $watcher.Stop(); $watcher.Dispose() }
}
