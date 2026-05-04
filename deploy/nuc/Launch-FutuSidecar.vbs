' Launch-FutuSidecar.vbs - hidden launcher for futu-sidecar.exe.
' Phase 7a follow-up. Mirrors Launch-IBKRSidecar.vbs but for the single
' Futu sidecar (port 18005). Invoked by Scheduled Task BrokerSidecarFutu via:
'     wscript.exe "C:\dashboard\deploy\nuc\Launch-FutuSidecar.vbs"
'
' Spawns futu-sidecar.exe with intWindowStyle=0 (hidden) so the console never
' flashes - matches the IBKR sidecar UX. Without this, registering
' BrokerSidecarFutu directly on futu-sidecar.exe via schtasks shows a
' persistent console window because it is a console-subsystem PE.

Option Explicit

Dim secretsDir, sidecarExe, logDir, stateDir
Dim sh, fso, cmd

secretsDir = "C:\dashboard\secrets"
sidecarExe = "C:\dashboard\dist-staging-futu\futu-sidecar.exe"
logDir = "C:\ProgramData\dashboard\sidecar-futu"
stateDir = logDir & "\state"

Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FolderExists("C:\ProgramData\dashboard") Then
    fso.CreateFolder "C:\ProgramData\dashboard"
End If
If Not fso.FolderExists(logDir) Then fso.CreateFolder logDir
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir

' Quoting strategy: every path in double quotes so spaces in ProgramData
' paths don't break argv splitting on the sidecar side.
cmd = """" & sidecarExe & """" & _
    " --tls-cert-pem """ & secretsDir & "\sidecar-futu.crt""" & _
    " --tls-key-pem """ & secretsDir & "\sidecar-futu.key""" & _
    " --tls-ca-bundle-pem """ & secretsDir & "\ca.pem""" & _
    " --tls-crl-pem """ & secretsDir & "\crl.pem"""

Set sh = CreateObject("WScript.Shell")
' Run hidden (intWindowStyle=0) and don't wait - let the sidecar own its own
' lifetime. Task Scheduler will relaunch on exit.
sh.Run cmd, 0, False
