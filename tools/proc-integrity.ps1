# SPDX-License-Identifier: AGPL-3.0-or-later

<#
.SYNOPSIS
  Read-only: report the integrity level (and elevation) of running processes.

.DESCRIPTION
  Windows will not show a process's mandatory integrity level through any stock
  cmdlet, and the shell-takeover question turns on exactly that: what token does
  Winlogon hand the shell on this box? Explorer is the control -- whatever level
  it runs at now is what the fork would have been given.

  Opens each process read-only (QUERY_LIMITED_INFORMATION), reads
  TokenIntegrityLevel and TokenElevation, closes. Changes nothing.

    .\proc-integrity.ps1                       explorer, EsotericOS.Shell, CairoDesktop, EsotericOS
    .\proc-integrity.ps1 -Names explorer,code  any names you like
#>
param([string[]]$Names = @('explorer', 'EsotericOS.Shell', 'CairoDesktop', 'EsotericOS', 'powershell'))

$ErrorActionPreference = 'Stop'

$sig = @'
using System;
using System.Runtime.InteropServices;
public static class Tok {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool CloseHandle(IntPtr h);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool OpenProcessToken(IntPtr proc, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool GetTokenInformation(IntPtr token, int cls, IntPtr info, int len, out int ret);

    const uint QUERY_LIMITED = 0x1000;
    const uint TOKEN_QUERY   = 0x0008;

    // returns "<integrity>|<elevated>" or "ERR:<code>"
    public static string Describe(int pid) {
        IntPtr p = OpenProcess(QUERY_LIMITED, false, pid);
        if (p == IntPtr.Zero) return "ERR:open:" + Marshal.GetLastWin32Error();
        IntPtr t;
        if (!OpenProcessToken(p, TOKEN_QUERY, out t)) {
            int e = Marshal.GetLastWin32Error(); CloseHandle(p); return "ERR:token:" + e;
        }
        string integrity = "?";
        int need;
        GetTokenInformation(t, 25 /*TokenIntegrityLevel*/, IntPtr.Zero, 0, out need);
        IntPtr buf = Marshal.AllocHGlobal(need);
        if (GetTokenInformation(t, 25, buf, need, out need)) {
            IntPtr sid = Marshal.ReadIntPtr(buf);            // TOKEN_MANDATORY_LABEL.Label.Sid
            byte count = Marshal.ReadByte(sid, 1);           // SubAuthorityCount
            int rid = Marshal.ReadInt32(sid, 8 + 4 * (count - 1));
            if      (rid >= 0x4000) integrity = "System";
            else if (rid >= 0x3000) integrity = "High";
            else if (rid >= 0x2000) integrity = "Medium";
            else if (rid >= 0x1000) integrity = "Low";
            else                    integrity = "Untrusted";
            integrity += " (0x" + rid.ToString("X") + ")";
        }
        Marshal.FreeHGlobal(buf);

        string elev = "?";
        IntPtr eb = Marshal.AllocHGlobal(4);
        if (GetTokenInformation(t, 20 /*TokenElevation*/, eb, 4, out need))
            elev = Marshal.ReadInt32(eb) != 0 ? "elevated" : "not-elevated";
        Marshal.FreeHGlobal(eb);

        CloseHandle(t); CloseHandle(p);
        return integrity + "|" + elev;
    }
}
'@

if (-not ('Tok' -as [type])) { Add-Type -TypeDefinition $sig -Language CSharp }

'{0,-16} {1,-7} {2,-18} {3,-14} {4}' -f 'PROCESS', 'PID', 'INTEGRITY', 'ELEVATION', 'STARTED'
foreach ($n in $Names) {
    $procs = Get-Process -Name $n -ErrorAction SilentlyContinue
    if (-not $procs) { '{0,-16} {1}' -f $n, '(not running)'; continue }
    foreach ($p in $procs) {
        $d = [Tok]::Describe($p.Id)
        $parts = $d -split '\|'
        $start = try { $p.StartTime.ToString('HH:mm:ss') } catch { '?' }
        '{0,-16} {1,-7} {2,-18} {3,-14} {4}' -f $p.ProcessName, $p.Id, $parts[0], $(if ($parts.Count -gt 1) { $parts[1] } else { '' }), $start
    }
}
