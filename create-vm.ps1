# create-vm.ps1 - stand up the OpenSpan bridge VM with the exact hardware
# config the Windows app expects, so you never hand-set (and mis-set) it.
#
#   powershell -ExecutionPolicy Bypass -File create-vm.ps1 -Iso <debian-netinst.iso>
#
# Creates a VM named "OpenSpan" with xHCI USB passthrough, the three NAT
# port-forwards the app uses (ssh 2222, daemon 9955, audio UDP 4010), a blank
# disk, and a USB filter for your Bluetooth radio. Then install Debian 12 into
# it and run the guest provisioner (see README / guest/). Requires VirtualBox
# + the Extension Pack (xHCI needs it).
#
# Safe: refuses to touch an existing VM of the same name.
[CmdletBinding()]
param(
  [string]$Name = "OpenSpan",
  [string]$Iso = "",          # Debian netinst ISO to install from (optional)
  [string]$Disk = "",         # attach an existing .vdi/.raw instead of a blank one
  [int]$DiskGB = 12,
  [int]$RamMB = 1024,
  [int]$Cpus = 2,
  [string]$UsbVendor = "8087",   # Intel Bluetooth; change for your radio
  [string]$UsbProduct = "0aaa",  # (VBoxManage list usbhost shows yours)
  [string]$VBox = ""             # override VBoxManage.exe path
)
$ErrorActionPreference = "Stop"

function Find-VBoxManage {
  if ($VBox -and (Test-Path $VBox)) { return $VBox }
  foreach ($env in @($env:VBOX_MSI_INSTALL_PATH, $env:VBOX_INSTALL_PATH)) {
    if ($env) {
      $p = Join-Path $env "VBoxManage.exe"
      if (Test-Path $p) { return $p }
    }
  }
  try {
    $reg = Get-ItemProperty "HKLM:\SOFTWARE\Oracle\VirtualBox" -ErrorAction Stop
    if ($reg.InstallDir) {
      $p = Join-Path $reg.InstallDir "VBoxManage.exe"
      if (Test-Path $p) { return $p }
    }
  } catch {}
  $def = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
  if (Test-Path $def) { return $def }
  $cmd = Get-Command VBoxManage.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "VBoxManage.exe not found. Install VirtualBox, or pass -VBox <path>."
}

function VBox { param([Parameter(ValueFromRemainingArguments=$true)]$a)
  & $vbm @a
  if ($LASTEXITCODE -ne 0) { throw "VBoxManage failed: $($a -join ' ')" }
}

$vbm = Find-VBoxManage
Write-Output "Using $vbm"

# --- safety: never clobber an existing VM ---
$existing = & $vbm list vms
if ($existing -match "`"$Name`"") {
  throw "A VM named '$Name' already exists. Delete it first, or pass -Name."
}

# --- where the disk lives (VirtualBox default machine folder) ---
$sysprops = & $vbm list systemproperties
$folderLine = $sysprops | Select-String "Default machine folder:"
$baseFolder = ($folderLine -split ":", 2)[1].Trim()
$vmFolder = Join-Path $baseFolder $Name

Write-Output "Creating VM '$Name' ..."
VBox createvm --name $Name --ostype "Debian_64" --register

Write-Output "Configuring CPU / RAM / USB (xHCI) / networking ..."
VBox modifyvm $Name --memory $RamMB --cpus $Cpus --firmware bios
VBox modifyvm $Name --usbxhci on            # USB 3.0 - the only mode the
#                                             Intel radio holds under load
VBox modifyvm $Name --nic1 nat
# NAT port-forwards the app relies on: name,proto,hostip,hostport,guestip,guestport
VBox modifyvm $Name --natpf1 "ssh,tcp,,2222,,22"
VBox modifyvm $Name --natpf1 "daemon,tcp,,9955,,9955"
VBox modifyvm $Name --natpf1 "audio,udp,,4010,,4010"
VBox modifyvm $Name --graphicscontroller vmsvga --vram 16

# --- storage: SATA disk (create a blank one or attach an existing image) ---
VBox storagectl $Name --name "SATA" --add sata --controller IntelAhci --portcount 2
if ($Disk -and (Test-Path $Disk)) {
  Write-Output "Attaching existing disk $Disk ..."
  VBox storageattach $Name --storagectl "SATA" --port 0 --device 0 --type hdd --medium $Disk
} else {
  $vdi = Join-Path $vmFolder "$Name.vdi"
  Write-Output "Creating a ${DiskGB}GB disk ..."
  VBox createmedium disk --filename $vdi --size ($DiskGB * 1024)
  VBox storageattach $Name --storagectl "SATA" --port 0 --device 0 --type hdd --medium $vdi
}

# --- optional install ISO ---
if ($Iso -and (Test-Path $Iso)) {
  Write-Output "Attaching install ISO $Iso ..."
  VBox storagectl $Name --name "IDE" --add ide
  VBox storageattach $Name --storagectl "IDE" --port 0 --device 0 --type dvddrive --medium $Iso
  VBox modifyvm $Name --boot1 dvd --boot2 disk
} else {
  VBox modifyvm $Name --boot1 disk
  Write-Output "No -Iso given: attach a Debian 12 netinst ISO and install, or pass -Disk <image>."
}

# --- USB filter so the Bluetooth radio auto-attaches to this VM ---
Write-Output "Adding USB filter for Bluetooth radio $UsbVendor`:$UsbProduct ..."
VBox usbfilter add 0 --target $Name --name "BT radio" --vendorid $UsbVendor --productid $UsbProduct

Write-Output ""
Write-Output "Done. VM '$Name' created."
Write-Output "Next:"
Write-Output "  1. Start it and install Debian 12 (root login enabled)."
Write-Output "  2. Copy guest/ into the VM and run the provisioner (see README)."
Write-Output "  3. Install the host key: guest/install-authorized-key.sh id_openspan.pub"
Write-Output "  (id_openspan is generated on the app's first launch.)"
