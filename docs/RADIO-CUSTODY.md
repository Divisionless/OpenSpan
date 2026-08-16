# Radio custody

**EsotericOS takes a Bluetooth radio off Windows permanently, instead of
wrestling Windows for it once per VM start.**

Code: `win\radio_custody.py` · tests: `win\test_radio_custody.py` · UI: the
Bluetooth panel's *Take custody* button · install/uninstall: `bake-in.ps1
-Custody` / `-Undo`.

---

## 1. The wedge this exists to end

The bridge needs a Bluetooth radio inside a Linux VM, so the radio is passed
through over USB. VirtualBox's way of doing that is a runtime capture, and the
mechanism is violent:

1. A USB filter on the VM arms for the radio's vendor:product.
2. At VM start, `VBoxUSBMon.sys` **tears the live device stack down** — the
   radio is in use by `bthusb` at that moment — and re-adds the device so that
   PnP sees the hardware id `USB\VID_80EE&PID_CAFE`.
3. `VBoxUSB.inf` claims that hardware id, so `VBoxUSB.sys` binds, and the
   device becomes a VirtualBox proxy the guest can own.

Step 2 is a race against a device Windows is actively using, and it is re-run
**every single VM start**. When it loses, the re-add never completes: the real
device node is left registered but not enumerated — a PnP phantom — with no
proxy standing in for it. VirtualBox then reports the device `Unavailable`
forever.

On a dongle that is survivable. Unplug it, plug it in; a fresh arrival is a new
device object, and one replug has historically completed *every* pending
capture at once (DEVLOG, 2026-08-08/09). On the **onboard Intel radio there is
no plug**, which is why that one radio has been lost repeatedly.

Doug's direction: *"exclusively taking control of this from Windows until the
program is uninstalled."*

## 2. The design

Stop running the race. Bind `VBoxUSB.sys` as the **function driver of the
radio's REAL device node**, persistently, in the registry. That is the Device
Manager *Have Disk → Let me pick → show all models* install, done
programmatically.

Then at every boot PnP enumerates the radio, finds `VBoxUSB` already registered
as its driver, loads it, and `bthusb` never touches the radio at all. There is
no teardown, so there is nothing to lose.

**Return** is the inverse: uninstall the device node and rescan, so PnP
re-enumerates the real hardware id and the vendor driver (`Intel(R) Wireless
Bluetooth(R)` / `bthusb`) binds again exactly as before.

## 3. What is actually on this machine (probed 2026-08-16, read-only)

| | Intel onboard | TP-Link ×2 |
|---|---|---|
| real node | `USB\VID_8087&PID_0AAA\5&3B2D9A0D&0&14` | `USB\VID_2357&PID_0604\<serial>` |
| its service | `BTHUSB` | `BTHUSB` |
| its class | `{e0cbf06c-…}` (Bluetooth) | `{e0cbf06c-…}` (Bluetooth) |
| present | **False** | **False** |
| VBox proxy node | `USB\VID_80EE&PID_CAFE\5&3B2D9A0D&0&14` | `USB\VID_80EE&PID_CAFE\<serial>` |
| proxy service / class | `VBoxUSB` / `{36FC9E60-…}` (USB) | `VBoxUSB` / `{36FC9E60-…}` (USB) |
| removal policy (from the proxy) | **1 = ExpectNoRemoval** → built in | **3 = surprise removal** → has a plug |

`VBoxUSB.inf` at `C:\Program Files\Oracle\VirtualBox\drivers\USB\device\`:
`Class=USB`, `ClassGUID={36FC9E60-C465-11CF-8056-444553540000}`,
`CatalogFile=VBoxUSB.cat`, `PnpLockdown=1`, one hardware id
`USB\VID_80EE&PID_CAFE`, driver node `VirtualBox USB Driver` / Oracle
Corporation / 7.2.12.24389.

### The correction that matters

The obvious reading — *"real node present = False means the radio is a
phantom"* — is **wrong**, and the audit was written the wrong way first because
of it. A perfectly healthy dongle under runtime capture *also* reports
`present = False` on its real node, because that node was torn down and the
proxy stands in its place. On 2026-08-16 all three radios read `present =
False` and two of them were working fine.

What separates a working capture from a wedge is **VirtualBox's own host
state**, not the node:

| real node | proxy | VBox host state | verdict |
|---|---|---|---|
| present, service `VBoxUSB` | — | any | **ESOTERICOS-CUSTODY** |
| present, service anything else | — | any | **WINDOWS-OWNED** |
| torn down | present | `Busy`/`Available`/`Captured`/`Held` | **WINDOWS-OWNED** (runtime capture; the persistent binding is still Windows', so the race runs again next boot) |
| torn down | present | `Unavailable` | **PHANTOM** — the wedge |
| torn down | absent | any | **PHANTOM** |
| no node at all | — | — | **ABSENT** |

## 4. The SetupAPI sequence, and why each step is there

Four facts were probed on this machine before anything was written:

* `SetupDiOpenDeviceInfoW` on the Intel instance id **in a class-less set
  works**. Phantom nodes are openable — everything else can proceed.
* `SetupDiBuildDriverInfoList(SPDIT_COMPATDRIVER)` with `DI_ENUMSINGLEINF` on
  `VBoxUSB.inf` → **0 nodes**. Expected: hardware ids do not match.
* `SetupDiBuildDriverInfoList(SPDIT_CLASSDRIVER)` on the **device** → **0
  nodes**, even with `DI_FLAGSEX_ALLOWEXCLUDEDDRVS`.
* The same call at **set** level on a set created for the USB class GUID → **1
  node**, `VirtualBox USB Driver`. The driver node is right there.
* `SetupDiOpenDeviceInfoW` of the Bluetooth-class device into a USB-class set
  fails `ERROR_CLASS_MISMATCH` (0xE0000201), so "use the other set" is out.

The obstacle is documented, not mysterious:

> If the driver list is associated with a device instance (that is,
> *DeviceInfoData* is specified), the resulting list is composed of drivers
> that have the same class as the device instance with which they are
> associated.
> — [SetupDiBuildDriverInfoList][sdbdil]

So the class has to be changed **before** the device's own driver list is
built. `SetupDiSetDeviceRegistryProperty` is the documented lever:

> If the **ClassGuid** property is set, *DeviceInfoData.***ClassGuid** is set
> upon return to the new class for the device. […] When the ClassGUID property
> changes, **SetupDiSetDeviceRegistryProperty** automatically cleans up any
> software keys associated with the device.
> — [SetupDiSetDeviceRegistryPropertyW][sdsdrp]

**The sequence `take --apply` runs**, in this order:

| # | call | why |
|---|---|---|
| 1 | `SetupDiSetDeviceRegistryPropertyW(SPDRP_CLASSGUID, {36FC9E60-…})` | the class change. After it the device and `VBoxUSB.inf` share a class, so the driver node stops being filtered out. |
| 2 | `SetupDiSetDeviceInstallParamsW` — `Flags \|= DI_ENUMSINGLEINF`, `FlagsEx \|= DI_FLAGSEX_ALLOWEXCLUDEDDRVS`, `DriverPath = …\VBoxUSB.inf` | single INF only. `ALLOWEXCLUDEDDRVS` is not optional: *"Drivers for PnP devices are typically 'Exclude From Select' […] To build a list of driver files for a PnP device a caller of SetupDiBuildDriverInfoList must set this flag."* [(same page)][sdbdil] |
| 3 | `SetupDiBuildDriverInfoList(device, SPDIT_CLASSDRIVER)` | now at **device** level. The call that returned 0 before the class change should return the Oracle node. |
| 4 | `SetupDiEnumDriverInfoW` → pick `Description == "VirtualBox USB Driver"` | one `SP_DRVINFO_DATA_V2_W` naming exactly what to install. |
| 5 | `SetupDiSetSelectedDriverW` | the programmatic half of clicking the driver in the Have Disk list. |
| 6 | `DiInstallDevice(NULL, set, dev, drv, 0, &needReboot)` | *"Only call **DiInstallDevice** if it is necessary to install a specific driver on a specific device."* [(newdev.h)][diid] |

### Why `DiInstallDevice` and not the simpler APIs

* `UpdateDriverForPlugAndPlayDevices` is the usual choice and is **unusable
  here**: it *"scans the devices on the system and attempts to install the
  drivers specified by FullInfPath for any devices that match the specified
  HardwareId"* — and the radio's hardware id is precisely what does **not**
  match `VBoxUSB.inf`. [(newdev.h)][upd]
* `pnputil /add-driver … /install` has the same matching problem.
* `SetupDiInstallDevice` is documented as *"Only a class installer should call
  SetupDiInstallDevice"* and *"The caller of SetupDiInstallDevice must be a
  member of the Administrators group."* [(setupapi.h)][sdid] `DiInstallDevice`
  is the application-facing wrapper for exactly this case.
* `SetupDiSelectDevice` *"handles the user interface"* — it is the DIF_SELECTDEVICE
  default handler and would put a dialog on screen. It is also where the class
  normally changes (*"sets DeviceInfoData.ClassGuid to the GUID of the device
  setup class for the selected driver"* [(setupapi.h)][sdsd]), which is the
  wizard's route to the same end state. We take the non-UI route instead.

`NeedReboot` is read rather than left `NULL` deliberately: *"If this parameter
is NULL and a system restart is required to complete the installation,
**DiInstallDevice** displays a system restart dialog box."* [(newdev.h)][diid]
This app does not do native dialogs.

### What is NOT verified

**`SPDRP_CLASSGUID` is listed both ways on its own MSDN page.** The
`DeviceInfoData` parameter and the Remarks both describe setting it, quoted
above — but the same page also lists `SPDRP_CLASSGUID` under *"reserved for use
by the operating system and cannot be used in the Property parameter."*
[(setupapi.h)][sdsdrp] Whether Windows 10 22H2 honours the write has **not been
tested here**, because testing it is a state change and this work was code +
dry-run only. `take --apply` handles both outcomes: if the write is refused it
stops with the class change named as the failing step and installs nothing onto
a device still in the wrong class.

The Device Manager wizard reaches the same end state through
`DIF_SELECTDEVICE` → `SetupDiSelectDevice`, which sets the class as part of
selection. If the direct property write turns out to be refused, that is the
fallback to implement.

## 5. Using it

```
C:\Python313\python.exe D:\_EsotericOS\app\win\radio_custody.py audit
C:\Python313\python.exe D:\_EsotericOS\app\win\radio_custody.py take <instance-id>
C:\Python313\python.exe D:\_EsotericOS\app\win\radio_custody.py take <instance-id> --apply
C:\Python313\python.exe D:\_EsotericOS\app\win\radio_custody.py return <instance-id> --apply
```

`--all` in place of an instance id. `--json` for machine-readable output.
`audit` never changes anything. `take` and `return` are **dry runs by default**:
they run every read-only step for real, print the exact calls the apply step
would make with their arguments, and stop.

In the app: the Bluetooth panel shows one custody line per radio and one **Take
custody** button. The first click runs the dry run and prints the plan in the
console; the button then reads *Confirm: take custody* and a second click
applies it. Both on a worker thread. There is no dialog.

`bake-in.ps1 -Custody` prints the audit and the take commands; `bake-in.ps1
-Undo` removes the sign-in entry and prints the return commands. **Neither ever
passes `--apply`.** Custody is taken by Doug's click or Doug's command, never
by an installer.

### The rule: TP-Link first, Intel second

**Prove the sequence on a TP-Link dongle before ever pointing it at the Intel.**
If a bind goes wrong on a dongle, the recovery is unplug-and-replug. If it goes
wrong on the built-in Intel radio, the recovery is a Windows restart — the
expensive one, and the exact operation that has twice re-run the boot-time race
and lost.

### Refusals

`take` refuses, and says which, when:

* not elevated (`DiInstallDevice` returns `ERROR_ACCESS_DENIED` otherwise);
* `VBoxUSB.inf` or `VBoxUSB.cat` is missing;
* the node is **ABSENT** — nothing to bind to;
* the node is a **PHANTOM** — a bind written into a node that never starts is
  worthless, so the physical recovery comes first;
* **VirtualBox is holding a live runtime capture on that radio right now.**
  Rewriting the real node's driver underneath a live capture is the layered
  ownership that wedged this stack before. Power the VM off, confirm
  `VBoxManage list usbhost` no longer says `Captured`, then take custody.

`return` refuses when not elevated, when there is no node, and when the VM
currently has the device attached (checked via `VBoxManage showvminfo
--machinereadable`, `USBAttachActive*`).

## 6. Recovering the CURRENT wedge — this part is Doug's hands

Look at the PROXY node first, not the real one. Under a runtime capture the real
node (`USB\VID_8087&PID_0AAA\…`) is always `present = False`; what matters is
whether `USB\VID_80EE&PID_CAFE\<same suffix>` exists and is present. Two cases:

* **Proxy present and OK, VirtualBox says `Unavailable`** — the 2026-08-16 shape.
  The device is held by `VBoxUSB.sys` but VirtualBox cannot open it. The single
  sanctioned kick, proven that day on Doug's word:

      pnputil /restart-device "USB\VID_80EE&PID_CAFE\5&3B2D9A0D&0&14"

  (the Intel's proxy id; take it from `radio_custody.py audit`). One restart of
  that one node — surprise-remove and re-enumerate on its port — took the Intel
  from `Unavailable` to `Captured` in 8 s and the iPad lane's daemon answered
  within a minute. This is a *software replug*, not churn: one call, one node,
  the TP-Links and the VM untouched. Do it once; if it comes back `Unavailable`
  again the firmware is hosed and only a power cycle reaches it (below).
* **No proxy at all, real node registered but not enumerated** — the true
  phantom. `pnputil /restart-device` answers *"not connected"* and that is the
  end of what code can do:
  * **A TP-Link dongle:** unplug it and plug it back in. A fresh arrival is a
    new device object. One replug has historically completed every pending
    capture at once (2026-08-08) — but not always (2026-08-16: it re-captured
    itself and did nothing for the Intel).
* **The built-in Intel radio, truly phantom:** there is no plug, so the recovery is a Windows
  restart — but **restart Windows only with NO VM CAPTURES HELD.** Power the VM
  off, run `VBoxManage list usbhost` and confirm no radio reads `Captured`, and
  only then restart. A bare restart with captures still armed re-runs the same
  boot-time race and can wedge a different radio; that is documented in the
  DEVLOG for 29 July and again for 2026-08-08/09.
* **Do not restart the VM to fix this.** It spreads the fault to radios that
  were working — on 29 July it took out the internal Intel that had been fine
  all day.

## 7. The Windows Update caveat

Windows Update can push a vendor driver (`Intel(R) Wireless Bluetooth(R)`) and
re-bind a radio that was in custody. **The guard is the launch audit**: the
Bluetooth panel re-audits every launch, and a radio that has gone back to
`bthusb` reads *Windows-owned* again with the *Take custody* button right
there. Nothing re-binds automatically.

Per-device driver-update blocking **does exist**, and this project deliberately
**does not set it**:

* **Device Installation Restrictions** — *Computer Configuration → Administrative
  Templates → System → Device Installation → Device Installation Restrictions →
  "Prevent installation of devices that match any of these device IDs"*, backed
  by `HKLM\Software\Policies\Microsoft\Windows\DeviceInstall\Restrictions`
  (`DenyDeviceIDs = 1` plus a `DenyDeviceIDsList` subkey). This is a blunt
  instrument: while it is in force the device cannot be updated **by any route,
  including manually**, which would also block `radio_custody.py` itself.
* **"Do not include drivers with Windows Updates"** (`ExcludeWUDriversInQualityUpdate`)
  is machine-wide, not per device.

Neither is set by this code, and neither should be set without Doug asking for
it. The audit-and-offer loop is the guard.

## 8. References

* [SetupDiBuildDriverInfoList (setupapi.h)][sdbdil]
* [SetupDiSetDeviceRegistryPropertyW (setupapi.h)][sdsdrp]
* [SetupDiSelectDevice (setupapi.h)][sdsd]
* [SetupDiInstallDevice (setupapi.h)][sdid]
* [DIF_SELECTDEVICE][difsel]
* [DiInstallDevice (newdev.h)][diid]
* [UpdateDriverForPlugAndPlayDevicesW (newdev.h)][upd]

[sdbdil]: https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdibuilddriverinfolist
[sdsdrp]: https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdisetdeviceregistrypropertyw
[sdsd]: https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdiselectdevice
[sdid]: https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdiinstalldevice
[difsel]: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/dif-selectdevice
[diid]: https://learn.microsoft.com/en-us/windows/win32/api/newdev/nf-newdev-diinstalldevice
[upd]: https://learn.microsoft.com/en-us/windows/win32/api/newdev/nf-newdev-updatedriverforplugandplaydevicesw
