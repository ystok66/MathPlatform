param([Parameter(Mandatory=$true)][int]$Port)

$listener = $null
try {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
  $listener.Start()
  "true"
} catch {
  "false"
} finally {
  if ($listener) {
    $listener.Stop()
  }
}
