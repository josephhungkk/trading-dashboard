' Launch-IBKRSidecar.vbs - hidden launcher for one ibkr-sidecar.exe instance.
' Phase 4 Task 27. Invoked by Scheduled Task IBKRSidecar-<label> via:
'     wscript.exe "C:\dashboard\deploy\nuc\Launch-IBKRSidecar.vbs" <label>
'
' Resolves the canonical (gateway-port, grpc-port) pair from the label per
' the Phase 4 design spec port map, builds the full sidecar invocation with
' cert + log/state paths, and spawns ibkr-sidecar.exe hidden via wscript so
' the console never flashes (memory feedback_ibc_gotchas.md issue 6).

Option Explicit

Dim args, label, gatewayPort, grpcPort, secretsDir, sidecarExe, logDir, stateDir
Dim sh, fso, cmd

Set args = WScript.Arguments
If args.Count <> 1 Then
    WScript.Echo "Usage: wscript.exe Launch-IBKRSidecar.vbs <label>"
    WScript.Quit 2
End If

label = args(0)

Select Case label
    Case "isa-live"
        gatewayPort = 4001 : grpcPort = 18001
    Case "isa-paper"
        gatewayPort = 4002 : grpcPort = 18002
    Case "normal-live"
        gatewayPort = 4003 : grpcPort = 18003
    Case "normal-paper"
        gatewayPort = 4004 : grpcPort = 18004
    Case Else
        WScript.Echo "Unknown label: " & label
        WScript.Quit 3
End Select

secretsDir = "C:\dashboard\secrets"
sidecarExe = "C:\dashboard\sidecar_ibkr\dist\ibkr-sidecar\ibkr-sidecar.exe"
logDir = "C:\ProgramData\dashboard\sidecar-" & label
stateDir = logDir & "\state"

Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FolderExists("C:\ProgramData\dashboard") Then
    fso.CreateFolder "C:\ProgramData\dashboard"
End If
If Not fso.FolderExists(logDir) Then fso.CreateFolder logDir
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir

' Quoting strategy: embed every path in double quotes so spaces in user
' profile-relative paths (e.g. ProgramData) don't break argv splitting on
' the sidecar side.
cmd = """" & sidecarExe & """" & _
    " --label " & label & _
    " --gateway-port " & gatewayPort & _
    " --grpc-port " & grpcPort & _
    " --tls-cert-pem """ & secretsDir & "\sidecar-" & label & ".crt""" & _
    " --tls-key-pem """ & secretsDir & "\sidecar-" & label & ".key""" & _
    " --tls-ca-bundle-pem """ & secretsDir & "\ca.pem""" & _
    " --tls-crl-pem """ & secretsDir & "\crl.pem""" & _
    " --log-dir """ & logDir & """" & _
    " --state-dir """ & stateDir & """"

Set sh = CreateObject("WScript.Shell")
' Run hidden (intWindowStyle=0) and don't wait - let the sidecar own its own
' lifetime. Task Scheduler will relaunch on exit per the M19 chassis.
sh.Run cmd, 0, False
