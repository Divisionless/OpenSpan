<#
.SYNOPSIS
  Launch a packaged (AppX/MSIX) app by AUMID the way Windows means it to be
  launched -- IApplicationActivationManager -- not by ShellExecute.

.DESCRIPTION
  The Cairo fork launches packaged apps with ShellExecute on
  "shell:appsFolder\<AUMID>" using the runas verb. Under our shell that fails
  with "Class not registered": the appsFolder path relies on activation COM
  that Explorer hosts, and runas is not a verb packaged apps answer to.

  IApplicationActivationManager is the documented activation route. It is
  Explorer-independent, which is exactly what a desktop without Explorer needs.

    .\activate-appx.ps1 -Aumid Claude_pzs8sxrjxfjjc!Claude
    .\activate-appx.ps1 -List            show installed AUMIDs
#>
param([string]$Aumid, [switch]$List)

$ErrorActionPreference = 'Stop'

if ($List) {
    Get-StartApps | Sort-Object Name | Format-Table Name, AppID -AutoSize
    exit 0
}
if (-not $Aumid) { 'give -Aumid or -List'; exit 1 }

$sig = @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("2e941141-7f97-4756-ba1d-9decde894a3d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IApplicationActivationManager {
    IntPtr ActivateApplication([In] string appUserModelId, [In] string arguments, [In] uint options, [Out] out uint processId);
    IntPtr ActivateForFile([In] string appUserModelId, [In] IntPtr itemArray, [In] string verb, [Out] out uint processId);
    IntPtr ActivateForProtocol([In] string appUserModelId, [In] IntPtr itemArray, [Out] out uint processId);
}

[ComImport, Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
public class ApplicationActivationManager { }

public static class Appx {
    public static uint Activate(string aumid) {
        var mgr = (IApplicationActivationManager)(new ApplicationActivationManager());
        uint pid;
        IntPtr hr = mgr.ActivateApplication(aumid, null, 0 /* AO_NONE */, out pid);
        if (hr != IntPtr.Zero) throw new Exception("ActivateApplication hr=0x" + hr.ToInt64().ToString("X"));
        return pid;
    }
}
'@

if (-not ('Appx' -as [type])) { Add-Type -TypeDefinition $sig -Language CSharp }

$pid_ = [Appx]::Activate($Aumid)
"activated $Aumid -> pid $pid_"
