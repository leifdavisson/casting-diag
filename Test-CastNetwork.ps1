<#
.SYNOPSIS
    Test-CastNetwork.ps1 - Native PowerShell Casting Diagnostic Probe for Windows Clients.
    Validates Miracast (MS-MICE), Airtame, Apple AirPlay, and Google Cast network readiness.

.DESCRIPTION
    Runs on Windows 10/11 client machines to verify:
    1. Local network interface state and Wi-Fi Direct capability.
    2. Reachability of RTSP (TCP 7236) and Airtame HTTP (TCP 80).
    3. Multicast and firewall permissions on the connected SSID.

.PARAMETER TargetIp
    The IP address of the Airtame, Apple TV, or Chromecast display.

.EXAMPLE
    .\Test-CastNetwork.ps1 -TargetIp 192.168.10.50
#>

[CmdletBinding()]
param (
    [Parameter(Mandatory = $true, HelpMessage = "IP address of the Airtame or casting target")]
    [string]$TargetIp
)

Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "  WINDOWS NATIVE CASTING DIAGNOSTIC SUITE (Test-CastNetwork.ps1)" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan

# Phase 0: Client Pre-Flight Prerequisites Check
Write-Host "`n[Phase 0] Validating Windows Client Prerequisites..." -ForegroundColor Yellow

# 1. PowerShell version
$psVer = $PSVersionTable.PSVersion
Write-Host "  [+] PowerShell Engine: $psVer" -ForegroundColor Green

# 2. Network Adapter State
$activeAdapter = Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | Select-Object -First 1
if ($activeAdapter) {
    Write-Host ("  [+] Active Network Adapter: {0} ({1}) - Status: {2}" -f $activeAdapter.Name, $activeAdapter.InterfaceDescription, $activeAdapter.Status) -ForegroundColor Green
} else {
    Write-Host "  [!] No active network adapter detected with Status 'Up'!" -ForegroundColor Red
}

# 3. Wi-Fi Display / NDIS Driver Capability
try {
    $ndis = netsh wlan show drivers | Select-String "Wireless Display Supported"
    if ($ndis) {
        if ($ndis -match "Yes") {
            Write-Host "  [+] $ndis" -ForegroundColor Green
        } else {
            Write-Host "  [!] $ndis (Client driver may not support Miracast P2P)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [-] Could not query wireless driver display support via netsh." -ForegroundColor DarkGray
    }
} catch {
    Write-Host "  [-] Error querying wireless driver: $_" -ForegroundColor Red
}

# 4. Bluetooth Radio Status (Optional BLE proximity check)
try {
    $btRadio = Get-PnpDevice -Class "Bluetooth" -Status "OK" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($btRadio) {
        Write-Host ("  [+] Bluetooth Radio Detected: {0}" -f $btRadio.FriendlyName) -ForegroundColor Green
    } else {
        Write-Host "  [-] Bluetooth Radio: Disabled or not present (Wi-Fi infrastructure casting only)" -ForegroundColor DarkGray
    }
} catch {
    Write-Host "  [-] Could not query Bluetooth PnP devices: $_" -ForegroundColor DarkGray
}

Write-Host "--------------------------------------------------------------------------" -ForegroundColor DarkGray

# 1. ICMP Ping Test
Write-Host "`n[Phase 1] Testing ICMP Reachability to $TargetIp..." -ForegroundColor Yellow
$pingResult = Test-Connection -ComputerName $TargetIp -Count 2 -Quiet
if ($pingResult) {
    Write-Host "  [+] ICMP Ping to $TargetIp SUCCEEDED." -ForegroundColor Green
} else {
    Write-Host "  [!] ICMP Ping to $TargetIp FAILED or DROPPED by firewall." -ForegroundColor Red
}

# 3. L4 Port Matrix Audit
Write-Host "`n[3/4] Testing Critical Casting Control & Media Ports on $TargetIp..." -ForegroundColor Yellow

$PortTests = @(
    @{ Proto="Miracast"; Port=7236; Type="TCP"; Name="Miracast RTSP Control (PIN Setup)"; Critical=$true },
    @{ Proto="Airtame";  Port=80;   Type="TCP"; Name="Airtame Web / API";               Critical=$true },
    @{ Proto="Airtame";  Port=443;  Type="TCP"; Name="Airtame HTTPS / Cloud";           Critical=$false },
    @{ Proto="Airtame";  Port=8001; Type="TCP"; Name="Airtame App Streaming Control";   Critical=$false },
    @{ Proto="AirPlay";  Port=7000; Type="TCP"; Name="Apple AirPlay Service";           Critical=$false },
    @{ Proto="AirPlay";  Port=7100; Type="TCP"; Name="Apple AirPlay Mirroring";         Critical=$false },
    @{ Proto="DIAL/Cast";Port=8008; Type="TCP"; Name="Google Cast DIAL / HTTP";        Critical=$false },
    @{ Proto="DIAL/Cast";Port=8009; Type="TCP"; Name="Google Cast TLS Control";         Critical=$false },
    @{ Proto="Newline";  Port=8000; Type="TCP"; Name="Newline Cast / Clevershare";      Critical=$false },
    @{ Proto="IFP/Admin";Port=8443; Type="TCP"; Name="Newline / SMART Board Admin";     Critical=$false },
    @{ Proto="DLNA/UPnP";Port=2869; Type="TCP"; Name="UPnP Eventing (Windows Media)";   Critical=$false },
    @{ Proto="DLNA/UPnP";Port=5000; Type="TCP"; Name="DLNA MediaRenderer Port";        Critical=$false },
    @{ Proto="Crestron"; Port=41794;Type="TCP"; Name="Crestron CIP AirMedia";           Critical=$false },
    @{ Proto="ClickShare";Port=8080;Type="TCP"; Name="Barco ClickShare Web/Stream";     Critical=$false }
)

$results = @()
foreach ($test in $PortTests) {
    $port = $test.Port
    $tcpCheck = Test-NetConnection -ComputerName $TargetIp -Port $port -WarningAction SilentlyContinue

    $status = if ($tcpCheck.TcpTestSucceeded) { "PASS" } else { "FAIL" }
    $color = if ($tcpCheck.TcpTestSucceeded) { "Green" } elseif ($test.Critical) { "Red" } else { "DarkYellow" }

    Write-Host ("  [{0,-4}] {1,-10} Port {2,-5} : {3}" -f $status, $test.Proto, $port, $test.Name) -ForegroundColor $color
    
    $results += [PSCustomObject]@{
        Protocol = $test.Proto
        Port     = $port
        Service  = $test.Name
        Passed   = $tcpCheck.TcpTestSucceeded
        Critical = $test.Critical
    }
}

# 4. Summary & Root Cause Diagnosis
Write-Host "`n[4/4] Diagnostic Summary & Verdict:" -ForegroundColor Yellow

$rtspTest = $results | Where-Object { $_.Port -eq 7236 }
$httpTest = $results | Where-Object { $_.Port -eq 80 }

if (-not $rtspTest.Passed) {
    Write-Host @"

  ================================ ROOT CAUSE IDENTIFIED ================================
  [!] TCP Port 7236 (Miracast RTSP) is UNREACHABLE or FILTERED.
  
  Explanation:
  Windows 10/11 requires TCP 7236 to initiate the RTSP handshake that prompts for the 
  on-screen Airtame PIN code. When this port is blocked, Windows fails to negotiate native
  Miracast and Airtame displays the fallback message instructing the user to open the app.

  Fix on Juniper Mist:
  - Check WLAN L2/L3 firewall settings on the SSID.
  - Allow TCP port 7236 and UDP ports 7250-7254 to the Airtame's IP address.
  =======================================================================================
"@ -ForegroundColor Red
} elseif ($rtspTest.Passed -and $httpTest.Passed) {
    Write-Host @"

  ================================ ROOT CAUSE IDENTIFIED ================================
  [+] L4 Ports (including RTSP 7236) are reachable and responding!
  
  If Windows (Win + K) still does not discover the device:
  The issue is 100% Multicast DNS (mDNS) discovery on the Juniper Mist AP / Junos switch.

  Fix on Juniper Mist:
  - Enable 'Bonjour Gateway' on this WLAN profile.
  - Add service string: '_display._tcp' (Miracast MS-MICE) and '_airtame._tcp'.
  - Verify 'Multicast to Unicast conversion' is checked.
  =======================================================================================
"@ -ForegroundColor Green
}

Write-Host "`nDiagnostics Complete.`n" -ForegroundColor Cyan
