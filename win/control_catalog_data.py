# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Control Center's four data tables, versioned and inert.

Nothing here reads the machine, opens a handle, or launches anything. These are
declarations: what Microsoft publishes, what this project is willing to admit,
and why. `control_catalog` is the only module that turns them into records.

WHY A TABLE AND NOT A DIRECTORY LISTING. The frozen spec (docs/CONTROL-CENTER.md)
excludes "arbitrary System32 executables" and admits administrative consoles and
.cpl modules "through explicit capability rules rather than blindly listing every
executable". A directory listing is a rule that says yes to whatever Windows --
or whatever installer -- happens to have dropped in System32. So every .cpl and
every .msc this product will ever name is written down here WITH THE REASON it
is named, and an entry on disk that is not in the table is not admitted; it is
reported as a rejection with its reason, which is a different thing from being
invisible.

The tables are also the only place an entry can be declared ABSENT-BUT-KNOWN.
`printmanagement.msc` is not on a Home installation. Listing it here is what
lets the catalog say "not installed on this edition" instead of saying nothing,
which the spec forbids: "Controls unavailable on the installed Windows edition
without an explicit explanation" is an excluded outcome, not a tolerated one.

PROVENANCE.

  MS_SETTINGS_PAGES derives from Microsoft's published `ms-settings:` URI
  inventory -- "Launch the Windows Settings app" (learn.microsoft.com,
  windows/apps/develop/launch/launch-settings-app), which is the same document
  that names LaunchUriAsync as the supported desktop activation path. It is a
  curated SUBSET of that inventory, not a transcription: a page is listed when
  it is a destination a person would go looking for, and pages that exist only
  as a sub-scroll of another page are left to the local search vocabulary
  (source 2) to reach. `page_id` is NOT from that document -- it is the
  SettingsPageXxx identifier this project observed in the local
  AllSystemSettings index, and it is filled in only where the mapping was
  confirmed against a real index. An unconfirmed mapping is None, never a guess,
  because a wrong page_id would make a live page report itself missing.

  CONTROL_PANEL_ITEMS keys on the canonical Control Panel name (System.
  ApplicationName under the item's CLSID), which Microsoft documents as the
  stable, non-localized launch contract -- "Canonical Control Panel names"
  (learn.microsoft.com, windows/win32/shell/controlpanel-canonical-names). The
  table supplies grouping and vocabulary only; WHICH items exist is read off
  this machine's registry, so a third-party control that registers itself
  appears without anyone editing this file.

  CPL_MODULES and MMC_CONSOLES follow "Executing Control Panel Items"
  (learn.microsoft.com, windows/win32/shell/executing-control-panel-items).

REVISING A TABLE. Bump CATALOG_REVISION. Ids are a published contract the
moment the GUI stores one, so a row's `topic` and the id derived from it may be
added and deprecated but never re-pointed at a different destination.
"""

from __future__ import annotations

from typing import NamedTuple


CATALOG_REVISION = "2026-08-24"

# ---- the twelve logical groups (docs/CONTROL-CENTER.md, "Logical organization")

GROUP_DISPLAY_SOUND = "display-sound"
GROUP_DEVICES_INPUT = "devices-input"
GROUP_NETWORK_SHARING = "network-sharing"
GROUP_APPS_DEFAULTS = "apps-defaults"
GROUP_ACCOUNTS_SIGNIN = "accounts-signin"
GROUP_PERSONALIZATION = "personalization"
GROUP_ACCESSIBILITY = "accessibility"
GROUP_PRIVACY_SECURITY = "privacy-security"
GROUP_TIME_LANGUAGE = "time-language"
GROUP_STORAGE_RECOVERY = "storage-recovery"
GROUP_UPDATES_DIAGNOSTICS = "updates-diagnostics"
GROUP_ADMINISTRATION = "administration"

# Order is the GUI's order. The tuple is the taxonomy; a record carrying a
# group id outside it is a bug, and control_catalog checks for exactly that.
GROUPS = (
    (GROUP_DISPLAY_SOUND, "Display and sound"),
    (GROUP_DEVICES_INPUT, "Devices and input"),
    (GROUP_NETWORK_SHARING, "Network and sharing"),
    (GROUP_APPS_DEFAULTS, "Apps and defaults"),
    (GROUP_ACCOUNTS_SIGNIN, "Accounts and sign-in"),
    (GROUP_PERSONALIZATION, "Personalization"),
    (GROUP_ACCESSIBILITY, "Accessibility"),
    (GROUP_PRIVACY_SECURITY, "Privacy and security"),
    (GROUP_TIME_LANGUAGE, "Time and language"),
    (GROUP_STORAGE_RECOVERY, "Storage and recovery"),
    (GROUP_UPDATES_DIAGNOSTICS, "Updates and diagnostics"),
    (GROUP_ADMINISTRATION, "Administration"),
)

GROUP_IDS = tuple(group for group, _title in GROUPS)
GROUP_TITLES = dict(GROUPS)


# ---- gate vocabulary -------------------------------------------------------
#
# A gate is a string a row declares and the resolver answers. Three kinds:
#
#   edition:<id>   answerable from the registry EditionID this build reports.
#   hardware:<id>  NOT answerable from a catalog. The record stays visible and
#                  says so -- "conditional", with the hardware named.
#   feature:<id>   an optional Windows feature; same treatment as hardware.
#
# Build gating is not a gate string; it is min_build/max_build, because a build
# number is ordered and a gate id is not.

EDITION_PRO = "edition:pro"
EDITION_ENTERPRISE = "edition:enterprise"

# EditionID values that satisfy edition:pro. Home ("Core") is deliberately
# absent -- gpedit.msc SHIPS on Home and its snap-in refuses to load there, so
# file presence is not evidence of availability and the edition gate is what
# separates the two.
PRO_EDITION_IDS = frozenset({
    "Professional", "ProfessionalN", "ProfessionalEducation",
    "ProfessionalWorkstation", "ProfessionalSingleLanguage",
    "Enterprise", "EnterpriseN", "EnterpriseS", "EnterpriseG",
    "Education", "EducationN", "ServerStandard", "ServerDatacenter",
    "IoTEnterprise", "Cloud", "CloudN",
})
ENTERPRISE_EDITION_IDS = frozenset({
    "Enterprise", "EnterpriseN", "EnterpriseS", "EnterpriseG",
    "Education", "EducationN", "ServerStandard", "ServerDatacenter",
    "IoTEnterprise",
})

HARDWARE_LABELS = {
    "hardware:bluetooth": "a Bluetooth radio",
    "hardware:cellular": "a cellular modem",
    "hardware:pen": "a pen digitiser",
    "hardware:touch": "a touch digitiser",
    "hardware:touchpad": "a precision touchpad",
    "hardware:battery": "a battery",
    "hardware:infrared": "an infrared transceiver",
    "hardware:modem": "a modem or telephony device",
    "hardware:gamecontroller": "a game controller",
    "hardware:tpm": "a TPM",
    "hardware:dial": "a Surface Dial or other radial controller",
    "hardware:eyetracker": "a supported eye tracker",
    "hardware:headset": "a Windows Mixed Reality headset",
    "hardware:storagespaces": "two or more eligible drives",
    "feature:hyper-v": "the Hyper-V optional feature",
    "feature:workfolders": "Work Folders configured by an administrator",
    "feature:mobilehotspot": "a shareable network connection",
    "feature:phonelink": "a linked phone",
    "feature:cortana": "Cortana, present only on builds that still ship it",
}


# ---- source 1: Microsoft's versioned ms-settings: catalog -------------------

class MsSettingsPage(NamedTuple):
    """One documented `ms-settings:` destination.

    `topic` is the deduplication key -- the thing a person is trying to reach,
    independent of which era of Windows control reaches it. A classic control
    naming the same topic is a candidate duplicate, not a separate destination.
    """

    uri: str
    title: str
    group: str
    topic: str
    aliases: tuple
    min_build: int = 10240
    max_build: int | None = None
    page_id: str | None = None
    requires: tuple = ()


_B_1511 = 10586
_B_1607 = 14393
_B_1703 = 15063
_B_1709 = 16299
_B_1803 = 17134
_B_1809 = 17763
_B_1903 = 18362
_B_2004 = 19041
_B_W11 = 22000

MS_SETTINGS_PAGES = (
    # -- Display and sound ---------------------------------------------------
    MsSettingsPage(
        "ms-settings:display", "Display", GROUP_DISPLAY_SOUND, "display",
        ("monitor", "monitors", "screen", "resolution", "scale", "scaling",
         "orientation", "multiple displays", "brightness", "hdr", "refresh rate"),
        page_id="SettingsPagePCSystemDisplay"),
    MsSettingsPage(
        "ms-settings:nightlight", "Night light", GROUP_DISPLAY_SOUND,
        "night-light",
        ("blue light", "colour temperature", "color temperature", "warm",
         "eye strain"),
        min_build=_B_1703),
    MsSettingsPage(
        "ms-settings:screenrotation", "Screen rotation", GROUP_DISPLAY_SOUND,
        "screen-rotation", ("rotate", "landscape", "portrait", "auto-rotate")),
    MsSettingsPage(
        "ms-settings:sound", "Sound", GROUP_DISPLAY_SOUND, "sound",
        ("audio", "volume", "speakers", "headphones", "microphone",
         "output device", "input device"),
        min_build=_B_1803, page_id="SettingsPageAudio"),
    MsSettingsPage(
        "ms-settings:apps-volume", "App volume and device preferences",
        GROUP_DISPLAY_SOUND, "app-volume",
        ("per-app volume", "mixer", "app audio", "route audio"),
        min_build=_B_1803),
    MsSettingsPage(
        "ms-settings:project", "Projecting to this PC", GROUP_DISPLAY_SOUND,
        "project", ("wireless display", "miracast", "second screen", "cast"),
        min_build=_B_1607),
    MsSettingsPage(
        "ms-settings:video", "Video playback", GROUP_DISPLAY_SOUND,
        "video-playback",
        ("hdr video", "streaming quality", "auto process video"),
        min_build=_B_1709, page_id="SettingsPageVideo"),
    MsSettingsPage(
        "ms-settings:screenpowerandsleep", "Power and sleep",
        GROUP_DISPLAY_SOUND, "power-sleep",
        ("screen off", "sleep timer", "idle", "power plan"),
        page_id="SettingsPageScreenPowerAndSleep"),

    # -- Devices and input ---------------------------------------------------
    MsSettingsPage(
        "ms-settings:bluetooth", "Bluetooth and other devices",
        GROUP_DEVICES_INPUT, "bluetooth",
        ("bluetooth", "pair", "pairing", "add device", "wireless device",
         "headset", "earbuds"),
        page_id="SettingsPagePCSystemBluetooth",
        requires=("hardware:bluetooth",)),
    MsSettingsPage(
        "ms-settings:connecteddevices", "Connected devices",
        GROUP_DEVICES_INPUT, "connected-devices",
        ("devices", "add a device", "usb device", "docking"),
        page_id="SettingsPagePCSystemDevices"),
    MsSettingsPage(
        "ms-settings:printers", "Printers and scanners", GROUP_DEVICES_INPUT,
        "printers", ("printer", "scanner", "print queue", "add printer"),
        page_id="SettingsPageDevicesPrinters"),
    MsSettingsPage(
        "ms-settings:mousetouchpad", "Touchpad", GROUP_DEVICES_INPUT,
        "touchpad", ("trackpad", "gestures", "tap", "two finger", "precision"),
        min_build=_B_1607, page_id="SettingsPageDevicesTouchpad",
        requires=("hardware:touchpad",)),
    MsSettingsPage(
        "ms-settings:mouse", "Mouse", GROUP_DEVICES_INPUT, "mouse",
        ("pointer", "cursor speed", "scroll", "primary button", "double click")),
    MsSettingsPage(
        "ms-settings:typing", "Typing", GROUP_DEVICES_INPUT, "typing",
        ("autocorrect", "spelling", "touch keyboard", "text suggestions"),
        page_id="SettingsPageTimeRegionSpelling"),
    MsSettingsPage(
        "ms-settings:pen", "Pen and Windows Ink", GROUP_DEVICES_INPUT, "pen",
        ("stylus", "ink", "handwriting", "digitiser", "digitizer"),
        min_build=_B_1703, page_id="SettingsPageDevicesPen",
        requires=("hardware:pen",)),
    MsSettingsPage(
        "ms-settings:wheel", "Wheel", GROUP_DEVICES_INPUT, "radial-controller",
        ("surface dial", "radial controller", "dial"),
        min_build=_B_1607, page_id="SettingsPageRadialController",
        requires=("hardware:dial",)),
    MsSettingsPage(
        "ms-settings:autoplay", "AutoPlay", GROUP_DEVICES_INPUT, "autoplay",
        ("removable drive", "memory card", "what happens when i insert"),
        page_id="SettingsPagePCSystemAutoPlay"),
    MsSettingsPage(
        "ms-settings:usb", "USB", GROUP_DEVICES_INPUT, "usb",
        ("usb notifications", "connection notifications", "battery saving usb"),
        min_build=_B_1703, page_id="SettingsPageUsb"),
    MsSettingsPage(
        "ms-settings:devices-touch", "Touch", GROUP_DEVICES_INPUT, "touch",
        ("touchscreen", "touch feedback", "digitiser"),
        min_build=_B_1809, requires=("hardware:touch",)),
    MsSettingsPage(
        "ms-settings:tabletmode", "Tablet mode", GROUP_DEVICES_INPUT,
        "tablet-mode", ("tablet", "continuum", "touch mode"),
        max_build=_B_W11 - 1, page_id="SettingsPageContinuum"),
    MsSettingsPage(
        "ms-settings:mobile-devices", "Your Phone", GROUP_DEVICES_INPUT,
        "phone-link", ("phone link", "your phone", "link a phone", "android"),
        min_build=_B_1809, page_id="SettingsPageManagePhone",
        requires=("feature:phonelink",)),
    MsSettingsPage(
        "ms-settings:crossdevice", "Shared experiences", GROUP_DEVICES_INPUT,
        "shared-experiences",
        ("nearby sharing", "continue on pc", "handoff"),
        min_build=_B_1703, page_id="SettingsPageSharedExperiences"),

    # -- Network and sharing -------------------------------------------------
    MsSettingsPage(
        "ms-settings:network-status", "Network status", GROUP_NETWORK_SHARING,
        "network-status",
        ("network", "internet", "connection", "connected", "adapter status",
         "change adapter options"),
        page_id="SettingsPageNetworkStatus"),
    MsSettingsPage(
        "ms-settings:network-wifi", "Wi-Fi", GROUP_NETWORK_SHARING, "wifi",
        ("wifi", "wireless", "wlan", "ssid", "join network"),
        page_id="SettingsPageNetworkWiFi"),
    MsSettingsPage(
        "ms-settings:network-wifisettings", "Manage known networks",
        GROUP_NETWORK_SHARING, "known-networks",
        ("forget network", "saved networks", "wifi profiles")),
    MsSettingsPage(
        "ms-settings:network-ethernet", "Ethernet", GROUP_NETWORK_SHARING,
        "ethernet", ("wired", "lan", "cable", "rj45"),
        page_id="SettingsPageNetworkEthernet"),
    MsSettingsPage(
        "ms-settings:network-vpn", "VPN", GROUP_NETWORK_SHARING, "vpn",
        ("virtual private network", "tunnel", "add vpn"),
        page_id="SettingsPageNetworkVPN"),
    MsSettingsPage(
        "ms-settings:network-proxy", "Proxy", GROUP_NETWORK_SHARING, "proxy",
        ("pac", "automatic proxy", "manual proxy", "wpad"),
        page_id="SettingsPageNetworkProxy"),
    MsSettingsPage(
        "ms-settings:network-airplanemode", "Airplane mode",
        GROUP_NETWORK_SHARING, "airplane-mode",
        ("flight mode", "radios off", "aeroplane"),
        page_id="SettingsPageNetworkAirplaneMode"),
    MsSettingsPage(
        "ms-settings:network-mobilehotspot", "Mobile hotspot",
        GROUP_NETWORK_SHARING, "mobile-hotspot",
        ("tethering", "share connection", "hotspot"),
        min_build=_B_1607, page_id="SettingsPageNetworkMobileHotspot",
        requires=("feature:mobilehotspot",)),
    MsSettingsPage(
        "ms-settings:network-cellular", "Cellular", GROUP_NETWORK_SHARING,
        "cellular", ("mobile broadband", "sim", "lte", "5g", "data plan"),
        page_id="SettingsPageNetworkMobileBroadband",
        requires=("hardware:cellular",)),
    MsSettingsPage(
        "ms-settings:network-dialup", "Dial-up", GROUP_NETWORK_SHARING,
        "dialup", ("modem", "ppp", "dial"),
        page_id="SettingsPageNetworkDialup", requires=("hardware:modem",)),
    MsSettingsPage(
        "ms-settings:datausage", "Data usage", GROUP_NETWORK_SHARING,
        "data-usage", ("metered", "data limit", "usage per app"),
        page_id="SettingsPageDataSenseOverview"),
    MsSettingsPage(
        "ms-settings:network-directaccess", "DirectAccess",
        GROUP_NETWORK_SHARING, "directaccess",
        ("corporate network", "always on vpn"),
        page_id="SettingsPageNetworkDirectAccess",
        requires=(EDITION_ENTERPRISE,)),
    MsSettingsPage(
        "ms-settings:remotedesktop", "Remote Desktop", GROUP_NETWORK_SHARING,
        "remote-desktop", ("rdp", "remote", "mstsc host", "allow remote"),
        min_build=_B_1709, page_id="SettingsPageRemoteDesktop",
        requires=(EDITION_PRO,)),

    # -- Apps and defaults ---------------------------------------------------
    MsSettingsPage(
        "ms-settings:appsfeatures", "Apps and features", GROUP_APPS_DEFAULTS,
        "apps-uninstall",
        ("uninstall", "remove program", "installed apps", "add or remove",
         "programs", "app size", "repair app", "modify app"),
        page_id="SettingsPageAppsSizes"),
    MsSettingsPage(
        "ms-settings:defaultapps", "Default apps", GROUP_APPS_DEFAULTS,
        "default-apps",
        ("default browser", "open with", "file associations", "protocol"),
        page_id="SettingsPageAppsDefaults"),
    MsSettingsPage(
        "ms-settings:appsforwebsites", "Apps for websites",
        GROUP_APPS_DEFAULTS, "apps-for-websites",
        ("open links in app", "uri handlers"),
        min_build=_B_1703, page_id="SettingsPageAppsForWebsites"),
    MsSettingsPage(
        "ms-settings:optionalfeatures", "Optional features",
        GROUP_APPS_DEFAULTS, "optional-features",
        ("windows features", "add a feature", "rsat", "openssh", "wordpad"),
        min_build=_B_1709, page_id="SettingsPageOptionalFeatures"),
    MsSettingsPage(
        "ms-settings:startupapps", "Startup apps", GROUP_APPS_DEFAULTS,
        "startup-apps",
        ("startup", "run at login", "boot apps", "autostart", "logon"),
        min_build=_B_1803, page_id="SettingsPageStartup"),
    MsSettingsPage(
        "ms-settings:maps", "Offline maps", GROUP_APPS_DEFAULTS, "offline-maps",
        ("maps", "download maps", "map updates"),
        page_id="SettingsPageMaps"),
    MsSettingsPage(
        "ms-settings:appsfeatures-app", "Advanced app settings",
        GROUP_APPS_DEFAULTS, "app-advanced",
        ("app permissions", "background app", "app repair"),
        min_build=_B_1803),

    # -- Accounts and sign-in ------------------------------------------------
    MsSettingsPage(
        "ms-settings:yourinfo", "Your info", GROUP_ACCOUNTS_SIGNIN,
        "your-info", ("account picture", "microsoft account", "profile"),
        page_id="SettingsPageAccountsPicture"),
    MsSettingsPage(
        "ms-settings:signinoptions", "Sign-in options", GROUP_ACCOUNTS_SIGNIN,
        "signin-options",
        ("password", "pin", "hello", "fingerprint", "face", "picture password",
         "dynamic lock", "security key"),
        page_id="SettingsPageSignInOptions"),
    MsSettingsPage(
        "ms-settings:emailandaccounts", "Email and accounts",
        GROUP_ACCOUNTS_SIGNIN, "email-accounts",
        ("add account", "mail account", "calendar account"),
        page_id="SettingsPageAccountsEmailApp"),
    MsSettingsPage(
        "ms-settings:otherusers", "Family and other users",
        GROUP_ACCOUNTS_SIGNIN, "other-users",
        ("add user", "local account", "child account", "family"),
        page_id="SettingsPageOtherUsers"),
    MsSettingsPage(
        "ms-settings:sync", "Sync your settings", GROUP_ACCOUNTS_SIGNIN,
        "sync-settings", ("roaming", "sync", "theme sync", "password sync"),
        page_id="SettingsPageAccountsSync"),
    MsSettingsPage(
        "ms-settings:workplace", "Access work or school",
        GROUP_ACCOUNTS_SIGNIN, "workplace",
        ("azure ad", "domain join", "mdm", "enroll", "work account"),
        page_id="SettingsPageWorkAccess"),
    MsSettingsPage(
        "ms-settings:assignedaccess", "Set up a kiosk",
        GROUP_ACCOUNTS_SIGNIN, "kiosk",
        ("assigned access", "kiosk mode", "single app"),
        min_build=_B_1709, requires=(EDITION_PRO,)),
    MsSettingsPage(
        "ms-settings:windowsanywhere", "Windows Anywhere",
        GROUP_ACCOUNTS_SIGNIN, "windows-anywhere",
        ("roaming device", "windows anywhere"),
        page_id="SettingsPageAccountsWindowsAnywhere"),

    # -- Personalization -----------------------------------------------------
    MsSettingsPage(
        "ms-settings:personalization-background", "Background",
        GROUP_PERSONALIZATION, "background",
        ("wallpaper", "desktop picture", "slideshow", "solid colour"),
        page_id="SettingsPageBackground"),
    MsSettingsPage(
        "ms-settings:personalization-colors", "Colours", GROUP_PERSONALIZATION,
        "colors", ("accent colour", "accent color", "dark mode", "light mode",
                   "transparency"),
        page_id="SettingsPageColors"),
    MsSettingsPage(
        "ms-settings:lockscreen", "Lock screen", GROUP_PERSONALIZATION,
        "lock-screen", ("spotlight", "lock screen image", "screen saver"),
        page_id="SettingsPageLockScreen"),
    MsSettingsPage(
        "ms-settings:themes", "Themes", GROUP_PERSONALIZATION, "themes",
        ("theme", "desktop icon settings", "mouse cursor theme"),
        page_id="SettingsPageThemes"),
    MsSettingsPage(
        "ms-settings:fonts", "Fonts", GROUP_PERSONALIZATION, "fonts",
        ("font", "typeface", "install font"),
        min_build=_B_1809, page_id="SettingsPageFonts"),
    MsSettingsPage(
        "ms-settings:personalization-start", "Start", GROUP_PERSONALIZATION,
        "start-menu", ("start menu", "tiles", "most used", "recently added"),
        page_id="SettingsPageStart"),
    MsSettingsPage(
        "ms-settings:taskbar", "Taskbar", GROUP_PERSONALIZATION, "taskbar",
        ("task bar", "notification area", "system tray", "combine buttons"),
        min_build=_B_1703, page_id="SettingsPageTaskbar"),
    MsSettingsPage(
        "ms-settings:multitasking", "Multitasking", GROUP_PERSONALIZATION,
        "multitasking", ("snap", "virtual desktops", "alt tab", "timeline"),
        page_id="SettingsPageMultiTasking"),
    MsSettingsPage(
        "ms-settings:notifications", "Notifications and actions",
        GROUP_PERSONALIZATION, "notifications",
        ("toast", "action centre", "action center", "quick actions", "banner"),
        page_id="SettingsPageAppsNotifications"),
    MsSettingsPage(
        "ms-settings:quiethours", "Focus assist", GROUP_PERSONALIZATION,
        "focus-assist", ("quiet hours", "do not disturb", "focus"),
        min_build=_B_1803, page_id="SettingsPageQuietHours"),
    MsSettingsPage(
        "ms-settings:clipboard", "Clipboard", GROUP_PERSONALIZATION,
        "clipboard", ("clipboard history", "sync clipboard", "paste"),
        min_build=_B_1809, page_id="SettingsPageClipboard"),

    # -- Accessibility -------------------------------------------------------
    MsSettingsPage(
        "ms-settings:easeofaccess-display", "Display (accessibility)",
        GROUP_ACCESSIBILITY, "eoa-display",
        ("text size", "make text bigger", "show animations"),
        min_build=_B_1809, page_id="SettingsPageEaseOfAccessDisplay"),
    MsSettingsPage(
        "ms-settings:easeofaccess-cursorandpointersize", "Cursor and pointer",
        GROUP_ACCESSIBILITY, "eoa-pointer",
        ("pointer size", "cursor thickness", "mouse pointer colour"),
        min_build=_B_1809, page_id="SettingsPageEaseOfAccessMousePointer"),
    MsSettingsPage(
        "ms-settings:easeofaccess-magnifier", "Magnifier",
        GROUP_ACCESSIBILITY, "magnifier", ("zoom", "magnify", "enlarge"),
        page_id="SettingsPageEaseOfAccessMagnifier"),
    MsSettingsPage(
        "ms-settings:easeofaccess-colorfilter", "Colour filters",
        GROUP_ACCESSIBILITY, "color-filters",
        ("colour blind", "color blind", "greyscale", "grayscale", "deuteranopia"),
        min_build=_B_1709, page_id="SettingsPageEaseOfAccessColorFilter"),
    MsSettingsPage(
        "ms-settings:easeofaccess-highcontrast", "High contrast",
        GROUP_ACCESSIBILITY, "high-contrast",
        ("contrast theme", "high contrast black"),
        page_id="SettingsPageEaseOfAccessHighContrast"),
    MsSettingsPage(
        "ms-settings:easeofaccess-narrator", "Narrator", GROUP_ACCESSIBILITY,
        "narrator", ("screen reader", "read aloud", "voice"),
        page_id="SettingsPageEaseOfAccessNarrator"),
    MsSettingsPage(
        "ms-settings:easeofaccess-audio", "Audio (accessibility)",
        GROUP_ACCESSIBILITY, "eoa-audio",
        ("mono audio", "audio alerts", "flash screen"),
        page_id="SettingsPageEaseOfAccessAudio"),
    MsSettingsPage(
        "ms-settings:easeofaccess-closedcaptioning", "Closed captions",
        GROUP_ACCESSIBILITY, "captions",
        ("subtitles", "caption style", "cc"),
        page_id="SettingsPageEaseOfAccessClosedCaptioning"),
    MsSettingsPage(
        "ms-settings:easeofaccess-speechrecognition", "Speech (accessibility)",
        GROUP_ACCESSIBILITY, "eoa-speech",
        ("dictation", "voice control", "speech recognition"),
        page_id="SettingsPageEaseOfAccessSpeechRecognition"),
    MsSettingsPage(
        "ms-settings:easeofaccess-keyboard", "Keyboard (accessibility)",
        GROUP_ACCESSIBILITY, "eoa-keyboard",
        ("sticky keys", "filter keys", "toggle keys", "on-screen keyboard"),
        page_id="SettingsPageEaseOfAccessKeyboard"),
    MsSettingsPage(
        "ms-settings:easeofaccess-mouse", "Mouse (accessibility)",
        GROUP_ACCESSIBILITY, "eoa-mouse",
        ("mouse keys", "numeric keypad pointer"),
        page_id="SettingsPageEaseOfAccessMouse"),
    MsSettingsPage(
        "ms-settings:easeofaccess-eyecontrol", "Eye control",
        GROUP_ACCESSIBILITY, "eye-control",
        ("eye tracker", "gaze", "tobii"),
        min_build=_B_1709, page_id="SettingsPageEaseOfAccessEyeGaze",
        requires=("hardware:eyetracker",)),
    # "texcursor" is not a typo here. It is Microsoft's spelling in the
    # published inventory and in the shipped handler; "textcursor" does not
    # activate. Correcting it would break the destination.
    MsSettingsPage(
        "ms-settings:easeofaccess-texcursor", "Text cursor",
        GROUP_ACCESSIBILITY, "text-cursor",
        ("caret", "text cursor indicator", "cursor thickness"),
        min_build=_B_2004, page_id="SettingsPageEaseOfAccessTextCursor"),

    # -- Privacy and security ------------------------------------------------
    MsSettingsPage(
        "ms-settings:privacy", "General privacy", GROUP_PRIVACY_SECURITY,
        "privacy-general",
        ("advertising id", "tracking", "privacy"),
        page_id="SettingsPagePrivacyGeneral"),
    MsSettingsPage(
        "ms-settings:privacy-location", "Location", GROUP_PRIVACY_SECURITY,
        "privacy-location", ("gps", "location history", "geofence"),
        page_id="SettingsPagePrivacyLocation"),
    MsSettingsPage(
        "ms-settings:privacy-webcam", "Camera", GROUP_PRIVACY_SECURITY,
        "privacy-camera", ("camera", "webcam access"),
        page_id="SettingsPagePrivacyWebcam"),
    MsSettingsPage(
        "ms-settings:privacy-microphone", "Microphone",
        GROUP_PRIVACY_SECURITY, "privacy-microphone",
        ("mic access", "microphone permission"),
        page_id="SettingsPagePrivacyMicrophone"),
    MsSettingsPage(
        "ms-settings:privacy-notifications", "Notifications privacy",
        GROUP_PRIVACY_SECURITY, "privacy-notifications",
        ("notification access",),
        min_build=_B_1903, page_id="SettingsPagePrivacyNotifications"),
    MsSettingsPage(
        "ms-settings:privacy-speech", "Speech privacy",
        GROUP_PRIVACY_SECURITY, "privacy-speech",
        ("online speech recognition",),
        min_build=_B_1809, page_id="SettingsPagePrivacySpeech"),
    MsSettingsPage(
        "ms-settings:privacy-accountinfo", "Account info privacy",
        GROUP_PRIVACY_SECURITY, "privacy-accountinfo",
        ("account access", "name and picture access"),
        page_id="SettingsPagePrivacyAccountInfo"),
    MsSettingsPage(
        "ms-settings:privacy-contacts", "Contacts privacy",
        GROUP_PRIVACY_SECURITY, "privacy-contacts", ("contacts access",),
        page_id="SettingsPagePrivacyContacts"),
    MsSettingsPage(
        "ms-settings:privacy-calendar", "Calendar privacy",
        GROUP_PRIVACY_SECURITY, "privacy-calendar", ("calendar access",),
        page_id="SettingsPagePrivacyCalendar"),
    MsSettingsPage(
        "ms-settings:privacy-documents", "Documents privacy",
        GROUP_PRIVACY_SECURITY, "privacy-documents",
        ("documents library access",),
        min_build=_B_1903, page_id="SettingsPagePrivacyDocuments"),
    MsSettingsPage(
        "ms-settings:privacy-broadfilesystemaccess", "File system privacy",
        GROUP_PRIVACY_SECURITY, "privacy-filesystem",
        ("file system access", "broad file system"),
        min_build=_B_1803, page_id="SettingsPagePrivacyBroadFileSystemAccess"),
    MsSettingsPage(
        "ms-settings:privacy-backgroundapps", "Background apps",
        GROUP_PRIVACY_SECURITY, "background-apps",
        ("run in background", "background permission"),
        page_id="SettingsPagePrivacyBackgroundApps"),
    MsSettingsPage(
        "ms-settings:privacy-activityhistory", "Activity history",
        GROUP_PRIVACY_SECURITY, "activity-history",
        ("timeline", "activity feed"),
        min_build=_B_1803, page_id="SettingsPagePrivacyActivityHistory"),
    MsSettingsPage(
        "ms-settings:privacy-feedback", "Diagnostics and feedback",
        GROUP_PRIVACY_SECURITY, "diagnostics-feedback",
        ("telemetry", "feedback frequency", "diagnostic data"),
        page_id="SettingsPagePrivacySIUFSettings"),
    MsSettingsPage(
        "ms-settings:privacy-radios", "Radios privacy", GROUP_PRIVACY_SECURITY,
        "privacy-radios", ("radio control", "app radio access"),
        page_id="SettingsPagePrivacyRadios"),
    MsSettingsPage(
        "ms-settings:windowsdefender", "Windows Security",
        GROUP_PRIVACY_SECURITY, "windows-security",
        ("defender", "antivirus", "virus", "firewall", "threat protection",
         "security centre", "security center", "smartscreen"),
        page_id="SettingsPageWindowsDefender"),
    MsSettingsPage(
        "ms-settings:findmydevice", "Find my device", GROUP_PRIVACY_SECURITY,
        "find-my-device", ("locate device", "find my pc"),
        min_build=_B_1511, page_id="SettingsPageFindMyDevice"),
    MsSettingsPage(
        "ms-settings:deviceencryption", "Device encryption",
        GROUP_PRIVACY_SECURITY, "device-encryption",
        ("bitlocker", "encrypt drive", "device encryption"),
        page_id="SettingsPageDeviceEncryption"),

    # -- Time and language ---------------------------------------------------
    MsSettingsPage(
        "ms-settings:dateandtime", "Date and time", GROUP_TIME_LANGUAGE,
        "date-time", ("clock", "time zone", "daylight saving", "sync time"),
        page_id="SettingsPageTimeRegionDateTime"),
    MsSettingsPage(
        "ms-settings:regionformatting", "Region", GROUP_TIME_LANGUAGE,
        "region", ("country", "regional format", "locale", "date format"),
        min_build=_B_1803, page_id="SettingsPageTimeRegionRegion"),
    MsSettingsPage(
        "ms-settings:regionlanguage", "Language", GROUP_TIME_LANGUAGE,
        "language", ("display language", "add language", "keyboard layout",
                     "input method", "ime"),
        page_id="SettingsPageTimeRegionLanguage"),
    MsSettingsPage(
        "ms-settings:keyboard", "Typing and keyboard layout",
        GROUP_TIME_LANGUAGE, "keyboard-layout",
        ("input language", "layout", "hotkey for input"),
        min_build=_B_1803),
    MsSettingsPage(
        "ms-settings:speech", "Speech", GROUP_TIME_LANGUAGE, "speech",
        ("speech language", "text to speech", "voice speed"),
        page_id="SettingsPageSpeech"),

    # -- Storage and recovery ------------------------------------------------
    MsSettingsPage(
        "ms-settings:storagesense", "Storage", GROUP_STORAGE_RECOVERY,
        "storage", ("disk space", "free up space", "storage sense",
                    "temporary files", "cleanup"),
        page_id="SettingsPageStorageSenseStorageOverview"),
    MsSettingsPage(
        "ms-settings:savelocations", "Where new content is saved",
        GROUP_STORAGE_RECOVERY, "save-locations",
        ("default save location", "new apps save to"),
        page_id="SettingsPageStorageSenseStorageOverview"),
    MsSettingsPage(
        "ms-settings:storagepolicies", "Storage Sense configuration",
        GROUP_STORAGE_RECOVERY, "storage-policies",
        ("storage sense settings", "delete temporary files"),
        min_build=_B_1703),
    MsSettingsPage(
        "ms-settings:backup", "Backup", GROUP_STORAGE_RECOVERY, "backup",
        ("file history", "backup and restore", "restore files"),
        page_id="SettingsPageRestoreOneBackup"),
    MsSettingsPage(
        "ms-settings:recovery", "Recovery", GROUP_STORAGE_RECOVERY, "recovery",
        ("reset this pc", "advanced startup", "go back", "refresh"),
        page_id="SettingsPageRestoreRestore"),
    MsSettingsPage(
        "ms-settings:activation", "Activation", GROUP_STORAGE_RECOVERY,
        "activation", ("licence", "license", "product key", "activate windows"),
        page_id="SettingsPageActivate"),

    # -- Updates and diagnostics ---------------------------------------------
    MsSettingsPage(
        "ms-settings:windowsupdate", "Windows Update",
        GROUP_UPDATES_DIAGNOSTICS, "windows-update",
        ("update", "patch", "check for updates", "cumulative"),
        page_id="SettingsPageRestoreMusUpdate"),
    MsSettingsPage(
        "ms-settings:windowsupdate-history", "Update history",
        GROUP_UPDATES_DIAGNOSTICS, "update-history",
        ("installed updates", "uninstall updates", "kb"),
        min_build=_B_1607),
    MsSettingsPage(
        "ms-settings:windowsupdate-options", "Advanced update options",
        GROUP_UPDATES_DIAGNOSTICS, "update-options",
        ("pause updates", "defer", "active hours"),
        min_build=_B_1607),
    MsSettingsPage(
        "ms-settings:windowsupdate-restartoptions", "Update restart options",
        GROUP_UPDATES_DIAGNOSTICS, "update-restart",
        ("restart time", "schedule restart"),
        min_build=_B_1607),
    MsSettingsPage(
        "ms-settings:delivery-optimization", "Delivery optimisation",
        GROUP_UPDATES_DIAGNOSTICS, "delivery-optimization",
        ("peer update", "bandwidth limit", "download from other pcs"),
        min_build=_B_1703, page_id="SettingsPageDeliveryOptimization"),
    MsSettingsPage(
        "ms-settings:troubleshoot", "Troubleshoot", GROUP_UPDATES_DIAGNOSTICS,
        "troubleshoot", ("troubleshooter", "fix problems", "diagnose"),
        min_build=_B_1703, page_id="SettingsPageTroubleshoot"),
    MsSettingsPage(
        "ms-settings:developers", "For developers",
        GROUP_UPDATES_DIAGNOSTICS, "developer-mode",
        ("developer mode", "device portal", "sideload", "ssh"),
        page_id="SettingsPageRestoreDeveloperOptions"),
    MsSettingsPage(
        "ms-settings:windowsinsider", "Windows Insider Programme",
        GROUP_UPDATES_DIAGNOSTICS, "insider",
        ("insider", "preview builds", "dev channel"),
        page_id="SettingsPageFlights"),
    MsSettingsPage(
        "ms-settings:about", "About this PC", GROUP_UPDATES_DIAGNOSTICS,
        "about", ("system info", "device specifications", "windows version",
                  "rename pc", "processor", "ram"),
        page_id="SettingsPagePCSystemInfo"),
)


# ---- source 3: grouping and vocabulary for canonical Control Panel names ----
#
# WHICH items exist is read off the registry -- that is the whole point of
# source 3, and it is why a third-party control appears without an edit here.
# This table only says where a KNOWN canonical name belongs and what a person
# might type to find it. An unknown canonical name still becomes a record; it
# lands in Administration with its registered name and no aliases.

class ControlPanelItem(NamedTuple):
    canonical: str
    title: str
    group: str
    topic: str
    aliases: tuple
    integrity: str = "medium"
    # WHY THIS CLASSIC CONTROL SURVIVES DEDUPLICATION, or empty.
    #
    # One field, not two. The obvious second field -- a duplicate_of_modern
    # flag -- would restate exactly what an empty reason already says, and a
    # fact restated in two places is a fact that can disagree with itself. So:
    # a reason here means the control does something the modern page cannot,
    # and it is retained flagged "classic"; empty means the modern page is the
    # same destination, and the spec's "duplicate search results that lead to
    # the same page" exclusion drops it -- with a rejection naming what
    # replaced it, never silently.
    classic_reason: str = ""
    requires: tuple = ()


CONTROL_PANEL_ITEMS = (
    ControlPanelItem(
        "Microsoft.ProgramsAndFeatures", "Programs and Features",
        GROUP_APPS_DEFAULTS, "apps-uninstall",
        ("uninstall", "remove program", "add or remove programs", "appwiz",
         "turn windows features on or off", "installed updates"),
        classic_reason=(
            "Reaches per-machine MSI products, installed updates, and Turn "
            "Windows features on or off, none of which Apps and features "
            "exposes.")),
    ControlPanelItem(
        "Microsoft.WindowsFirewall", "Windows Defender Firewall",
        GROUP_PRIVACY_SECURITY, "firewall",
        ("firewall", "allow an app", "inbound", "outbound", "block program"),
        classic_reason=(
            "Per-profile rules and Allow an app through the firewall have no "
            "equivalent in the Windows Security summary page.")),
    ControlPanelItem(
        "Microsoft.NetworkAndSharingCenter", "Network and Sharing Center",
        GROUP_NETWORK_SHARING, "network-status",
        ("sharing", "adapter", "network discovery", "homegroup",
         "advanced sharing settings"),
        classic_reason=(
            "Advanced sharing settings and per-profile discovery are not on "
            "the modern Network status page.")),
    ControlPanelItem(
        "Microsoft.DeviceManager", "Device Manager", GROUP_ADMINISTRATION,
        "device-manager",
        ("drivers", "hardware", "devmgmt", "update driver", "hidden devices"),
        integrity="high",
        classic_reason="No modern Settings page manages device drivers."),
    ControlPanelItem(
        "Microsoft.DevicesAndPrinters", "Devices and Printers",
        GROUP_DEVICES_INPUT, "connected-devices",
        ("printers", "scanners", "add a device", "printer properties"),
        classic_reason=(
            "Printer server properties and device troubleshooting live only "
            "in the classic folder.")),
    ControlPanelItem(
        "Microsoft.PowerOptions", "Power Options", GROUP_DISPLAY_SOUND,
        "power-sleep",
        ("power plan", "high performance", "balanced", "lid close",
         "hibernate", "fast startup"),
        classic_reason=(
            "Power plans, processor state and lid/button actions are absent "
            "from the modern Power and sleep page.")),
    ControlPanelItem(
        "Microsoft.Sound", "Sound (classic)", GROUP_DISPLAY_SOUND, "sound",
        ("playback devices", "recording devices", "spatial", "enhancements",
         "mmsys", "default device"),
        classic_reason=(
            "Per-endpoint properties, enhancements and exclusive mode are not "
            "in modern Sound.")),
    ControlPanelItem(
        "Microsoft.Personalization", "Personalization (classic)",
        GROUP_PERSONALIZATION, "themes", ("theme", "desktop icons", "cursors"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.Display", "Display (classic)", GROUP_DISPLAY_SOUND,
        "display", ("screen resolution", "desk.cpl"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.DateAndTime", "Date and Time (classic)",
        GROUP_TIME_LANGUAGE, "date-time",
        ("clock", "additional clocks", "internet time", "time server"),
        classic_reason=(
            "Additional clocks and the Internet time server are classic-only.")),
    ControlPanelItem(
        "Microsoft.RegionAndLanguage", "Region (classic)",
        GROUP_TIME_LANGUAGE, "region",
        ("regional format", "administrative", "system locale",
         "unicode utf-8", "beta utf-8"),
        classic_reason=(
            "The system locale, including the beta UTF-8 switch, is only "
            "reachable from the classic Administrative tab.")),
    ControlPanelItem(
        "Microsoft.Mouse", "Mouse Properties", GROUP_DEVICES_INPUT, "mouse",
        ("pointer scheme", "double-click speed", "wheel", "snap to",
         "pointer trails"),
        classic_reason=(
            "Pointer schemes, snap-to and per-button configuration are not on "
            "the modern Mouse page.")),
    ControlPanelItem(
        "Microsoft.Keyboard", "Keyboard Properties", GROUP_DEVICES_INPUT,
        "keyboard-hardware", ("repeat delay", "repeat rate", "cursor blink"),
        classic_reason="No modern page carries key repeat rate."),
    ControlPanelItem(
        "Microsoft.System", "System (classic)", GROUP_ADMINISTRATION,
        "system-advanced",
        ("environment variables", "performance options", "virtual memory",
         "page file", "system protection", "remote settings", "sysdm",
         "computer name"),
        classic_reason=(
            "Environment variables, virtual memory and system protection have "
            "no modern equivalent.")),
    ControlPanelItem(
        "Microsoft.UserAccounts", "User Accounts", GROUP_ACCOUNTS_SIGNIN,
        "user-accounts",
        ("uac", "user account control", "change account type",
         "credential manager", "netplwiz"),
        classic_reason=(
            "The User Account Control slider and account type change are "
            "classic-only.")),
    ControlPanelItem(
        "Microsoft.CredentialManager", "Credential Manager",
        GROUP_ACCOUNTS_SIGNIN, "credentials",
        ("saved passwords", "windows credentials", "web credentials", "vault"),
        classic_reason="There is no modern credential store page."),
    ControlPanelItem(
        "Microsoft.InternetOptions", "Internet Options", GROUP_NETWORK_SHARING,
        "internet-options",
        ("proxy", "lan settings", "certificates", "security zones", "inetcpl"),
        classic_reason=(
            "Security zones and per-connection LAN settings are still the "
            "system-wide WinINET configuration.")),
    ControlPanelItem(
        "Microsoft.BitLockerDriveEncryption", "BitLocker Drive Encryption",
        GROUP_PRIVACY_SECURITY, "bitlocker",
        ("encrypt", "recovery key", "tpm", "secure startup", "fvecpl"),
        integrity="high", requires=(EDITION_PRO,),
        classic_reason=(
            "Device encryption on Home is a different, non-manageable "
            "feature; full BitLocker management is a separate destination.")),
    ControlPanelItem(
        "Microsoft.Recovery", "Recovery (classic)", GROUP_STORAGE_RECOVERY,
        "recovery",
        ("system restore", "restore point", "configure restore",
         "recovery drive"),
        classic_reason=(
            "System Restore configuration and Create a recovery drive are not "
            "on the modern Recovery page.")),
    ControlPanelItem(
        "Microsoft.BackupAndRestore", "Backup and Restore (Windows 7)",
        GROUP_STORAGE_RECOVERY, "backup-legacy",
        ("windows 7 backup", "system image", "restore files"),
        classic_reason=(
            "System image backup exists nowhere else in Windows.")),
    ControlPanelItem(
        "Microsoft.FileHistory", "File History", GROUP_STORAGE_RECOVERY,
        "file-history",
        ("versions", "restore personal files", "backup drive"),
        classic_reason=(
            "File History target selection and retention are classic-only.")),
    ControlPanelItem(
        "Microsoft.StorageSpaces", "Storage Spaces", GROUP_STORAGE_RECOVERY,
        "storage-spaces", ("pool", "mirror", "parity", "resiliency"),
        integrity="high", requires=("hardware:storagespaces",),
        classic_reason="Storage pools have no modern page."),
    ControlPanelItem(
        "Microsoft.AdministrativeTools", "Windows Tools",
        GROUP_ADMINISTRATION, "admin-tools",
        ("administrative tools", "management console", "system tools"),
        classic_reason="The folder is the index of the consoles themselves."),
    ControlPanelItem(
        "Microsoft.Troubleshooting", "Troubleshooting (classic)",
        GROUP_UPDATES_DIAGNOSTICS, "troubleshoot",
        ("troubleshooter", "view history", "fix problems"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.ActionCenter", "Security and Maintenance",
        GROUP_PRIVACY_SECURITY, "security-maintenance",
        ("maintenance", "reliability history", "problem reports", "wscui"),
        classic_reason=(
            "Reliability history and maintenance scheduling are classic-only.")),
    ControlPanelItem(
        "Microsoft.IndexingOptions", "Indexing Options", GROUP_STORAGE_RECOVERY,
        "indexing", ("search index", "rebuild index", "indexed locations"),
        classic_reason=(
            "Index location and file-type rules are only in the classic "
            "dialog.")),
    ControlPanelItem(
        "Microsoft.FolderOptions", "File Explorer Options",
        GROUP_PERSONALIZATION, "folder-options",
        ("show hidden files", "file extensions", "folder view", "quick access"),
        classic_reason="No modern page carries File Explorer view options."),
    ControlPanelItem(
        "Microsoft.Fonts", "Fonts (classic)", GROUP_PERSONALIZATION, "fonts",
        ("font folder", "install font", "typeface"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.AutoPlay", "AutoPlay (classic)", GROUP_DEVICES_INPUT,
        "autoplay", ("removable media", "autorun"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.DefaultPrograms", "Default Programs", GROUP_APPS_DEFAULTS,
        "default-apps",
        ("set default programs", "set associations", "open with"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.Taskbar", "Taskbar and Start Menu (classic)",
        GROUP_PERSONALIZATION, "taskbar", ("task bar", "start menu"),
        classic_reason=""),
    ControlPanelItem(
        "Microsoft.EaseOfAccessCenter", "Ease of Access Center",
        GROUP_ACCESSIBILITY, "ease-of-access",
        ("accessibility", "magnifier", "narrator", "on-screen keyboard",
         "high contrast"),
        classic_reason=(
            "The classic centre is the only place several accessibility "
            "options are still grouped.")),
    ControlPanelItem(
        "Microsoft.SpeechRecognition", "Speech Recognition",
        GROUP_ACCESSIBILITY, "speech-recognition",
        ("dictation", "voice training", "speech tutorial"),
        classic_reason="Voice training and the tutorial are classic-only."),
    ControlPanelItem(
        "Microsoft.TextToSpeech", "Text to Speech", GROUP_ACCESSIBILITY,
        "text-to-speech", ("sapi", "voice", "speed", "preview voice"),
        classic_reason="SAPI voice selection is classic-only."),
    ControlPanelItem(
        "Microsoft.ColorManagement", "Color Management", GROUP_DISPLAY_SOUND,
        "color-management", ("icc", "colour profile", "color profile",
                             "calibrate"),
        classic_reason="ICC profile association has no modern page."),
    ControlPanelItem(
        "Microsoft.PenAndTouch", "Pen and Touch", GROUP_DEVICES_INPUT, "pen",
        ("flicks", "press and hold", "touch actions"),
        requires=("hardware:pen",),
        classic_reason="Flicks and press-and-hold are classic-only."),
    ControlPanelItem(
        "Microsoft.TabletPCSettings", "Tablet PC Settings",
        GROUP_DEVICES_INPUT, "tablet-pc",
        ("calibrate", "handwriting", "screen setup"),
        requires=("hardware:touch",),
        classic_reason="Digitiser calibration exists nowhere else."),
    ControlPanelItem(
        "Microsoft.PhoneAndModem", "Phone and Modem", GROUP_NETWORK_SHARING,
        "phone-modem", ("dialing rules", "modem", "telephony"),
        requires=("hardware:modem",),
        classic_reason="Dialing rules have no modern equivalent."),
    ControlPanelItem(
        "Microsoft.SyncCenter", "Sync Center", GROUP_STORAGE_RECOVERY,
        "sync-center", ("offline files", "sync partnerships"),
        classic_reason="Offline Files is managed only here."),
    ControlPanelItem(
        "Microsoft.WorkFolders", "Work Folders", GROUP_STORAGE_RECOVERY,
        "work-folders", ("work folders", "sync work files"),
        requires=("feature:workfolders",),
        classic_reason="Work Folders has no modern page."),
    ControlPanelItem(
        "Microsoft.MobilityCenter", "Windows Mobility Center",
        GROUP_DISPLAY_SOUND, "mobility-center",
        ("presentation mode", "brightness", "battery status"),
        requires=("hardware:battery",),
        classic_reason="Presentation mode exists only here."),
    ControlPanelItem(
        "Microsoft.RemoteAppAndDesktopConnections", "RemoteApp and Desktop "
        "Connections", GROUP_NETWORK_SHARING, "remoteapp",
        ("workspace", "remoteapp", "connection url"),
        classic_reason="RemoteApp workspaces have no modern page."),
)

CONTROL_PANEL_BY_CANONICAL = {item.canonical: item
                              for item in CONTROL_PANEL_ITEMS}

# Namespace CLSIDs with no canonical name of their own. Registered, therefore
# in scope; unlaunchable by canonical name, therefore recorded with the reason.
CONTROL_PANEL_CLSID_NOTES = {
    "{38A98528-6CBF-4CA9-8DC0-B1E1D10F7B1B}": (
        "View Available Networks", GROUP_NETWORK_SHARING, "available-networks",
        ("wifi list", "available networks", "van")),
    "{A0D4CD32-5D5D-4F72-BAAA-767A7AD6BAC5}": (
        "Mail (Microsoft Outlook)", GROUP_APPS_DEFAULTS, "mail-profiles",
        ("outlook profile", "mail profiles", "mapi")),
}


# ---- source 4: .cpl modules, by explicit capability rule -------------------

class CplModule(NamedTuple):
    filename: str
    title: str
    group: str
    topic: str
    aliases: tuple
    reason: str
    integrity: str = "medium"
    # Same single-field rule as ControlPanelItem.classic_reason above.
    classic_reason: str = ""
    requires: tuple = ()


CPL_MODULES = (
    CplModule(
        "appwiz.cpl", "Programs and Features", GROUP_APPS_DEFAULTS,
        "apps-uninstall",
        ("uninstall", "add remove programs", "installed updates",
         "windows features"),
        "Documented Control Panel item; the only route to per-machine MSI "
        "uninstall and Windows optional features.",
        # No reason of its own: this is the module-level route to the very
        # window Microsoft.ProgramsAndFeatures opens, so it folds in as an
        # alternate route and the canonical name carries the retention reason.
        classic_reason=""),
    CplModule(
        "bthprops.cpl", "Bluetooth Devices", GROUP_DEVICES_INPUT, "bluetooth",
        ("bluetooth", "pair", "com ports", "bluetooth settings"),
        "Documented Control Panel item; exposes Bluetooth COM ports, which "
        "the modern page does not.",
        requires=("hardware:bluetooth",),
        classic_reason="Bluetooth COM port assignment is classic-only."),
    CplModule(
        "desk.cpl", "Display Settings", GROUP_DISPLAY_SOUND, "display",
        ("screen resolution", "display settings"),
        "Documented Control Panel item; on Windows 10 and later it redirects "
        "to the modern Display page.",
        classic_reason=""),
    CplModule(
        "Firewall.cpl", "Windows Defender Firewall", GROUP_PRIVACY_SECURITY,
        "firewall", ("firewall", "allow an app", "block", "network profile"),
        "Documented Control Panel item; per-profile firewall state and the "
        "allowed-apps list.",
        classic_reason="Allow an app through the firewall is classic-only."),
    CplModule(
        "hdwwiz.cpl", "Add Hardware Wizard", GROUP_ADMINISTRATION,
        "add-hardware", ("legacy hardware", "add driver", "install device"),
        "Documented Control Panel item; installs legacy non-PnP devices.",
        integrity="high",
        classic_reason="No modern page installs legacy hardware."),
    CplModule(
        "inetcpl.cpl", "Internet Options", GROUP_NETWORK_SHARING,
        "internet-options",
        ("proxy", "lan settings", "security zones", "certificates"),
        "Documented Control Panel item; system-wide WinINET configuration.",
        classic_reason="Security zones have no modern equivalent."),
    CplModule(
        "intl.cpl", "Region", GROUP_TIME_LANGUAGE, "region",
        ("regional format", "system locale", "utf-8"),
        "Documented Control Panel item; carries the system locale.",
        classic_reason="The system locale switch is classic-only."),
    CplModule(
        "irprops.cpl", "Infrared", GROUP_DEVICES_INPUT, "infrared",
        ("infrared", "ir", "send or receive a file"),
        "Documented Control Panel item; the only infrared transfer surface.",
        requires=("hardware:infrared",),
        classic_reason="Infrared file transfer has no modern page."),
    CplModule(
        "joy.cpl", "Game Controllers", GROUP_DEVICES_INPUT, "game-controllers",
        ("joystick", "gamepad", "calibrate controller"),
        "Documented Control Panel item; controller calibration and test.",
        requires=("hardware:gamecontroller",),
        classic_reason="Controller calibration has no modern page."),
    CplModule(
        "main.cpl", "Mouse Properties", GROUP_DEVICES_INPUT, "mouse",
        ("pointer", "double-click speed", "pointer scheme", "snap to"),
        "Documented Control Panel item; pointer schemes and button setup.",
        classic_reason="Pointer schemes and snap-to are classic-only."),
    CplModule(
        "mmsys.cpl", "Sound (classic)", GROUP_DISPLAY_SOUND, "sound",
        ("playback", "recording", "default device", "enhancements",
         "exclusive mode", "spatial"),
        "Documented Control Panel item; per-endpoint audio properties.",
        classic_reason="Exclusive mode and endpoint properties are "
                       "classic-only."),
    CplModule(
        "ncpa.cpl", "Network Connections", GROUP_NETWORK_SHARING,
        "network-adapters",
        ("adapter", "change adapter settings", "disable adapter", "tcp/ip",
         "ipv4", "dns server"),
        "Documented Control Panel item; per-adapter TCP/IP and enable state.",
        classic_reason="Per-adapter TCP/IP properties have no modern page."),
    CplModule(
        "powercfg.cpl", "Power Options", GROUP_DISPLAY_SOUND, "power-sleep",
        ("power plan", "hibernate", "fast startup", "lid"),
        "Documented Control Panel item; power plans and button actions.",
        classic_reason="Power plans have no modern page."),
    CplModule(
        "sysdm.cpl", "System Properties", GROUP_ADMINISTRATION,
        "system-advanced",
        ("environment variables", "virtual memory", "page file",
         "system protection", "remote desktop", "computer name"),
        "Documented Control Panel item; environment variables and virtual "
        "memory.",
        classic_reason="Environment variables have no modern page."),
    CplModule(
        "TabletPC.cpl", "Tablet PC Settings", GROUP_DEVICES_INPUT, "tablet-pc",
        ("calibrate", "digitiser", "screen setup"),
        "Documented Control Panel item; digitiser calibration.",
        requires=("hardware:touch",),
        classic_reason="Digitiser calibration exists nowhere else."),
    CplModule(
        "telephon.cpl", "Phone and Modem", GROUP_NETWORK_SHARING, "phone-modem",
        ("dialing rules", "modem", "telephony"),
        "Documented Control Panel item; dialing rules.",
        requires=("hardware:modem",),
        classic_reason="Dialing rules have no modern equivalent."),
    CplModule(
        "timedate.cpl", "Date and Time (classic)", GROUP_TIME_LANGUAGE,
        "date-time", ("clock", "additional clocks", "internet time"),
        "Documented Control Panel item; additional clocks and time server.",
        classic_reason="Additional clocks are classic-only."),
    CplModule(
        "wscui.cpl", "Security and Maintenance", GROUP_PRIVACY_SECURITY,
        "security-maintenance",
        ("maintenance", "reliability", "problem reports"),
        "Documented Control Panel item; reliability history and maintenance.",
        classic_reason="Reliability history is classic-only."),
    # Known, and known to be absent on client editions without the feature.
    # Listing it is what turns "missing" into an explained record.
    CplModule(
        "fvecpl.cpl", "BitLocker Drive Encryption", GROUP_PRIVACY_SECURITY,
        "bitlocker", ("bitlocker", "encrypt", "recovery key"),
        "Documented Control Panel item; BitLocker management.",
        integrity="high", requires=(EDITION_PRO,),
        classic_reason="BitLocker management is not device encryption."),
)

CPL_BY_FILENAME = {module.filename.lower(): module for module in CPL_MODULES}


# ---- source 4: MMC consoles, by explicit capability rule -------------------

class MmcConsole(NamedTuple):
    filename: str
    title: str
    group: str
    topic: str
    aliases: tuple
    reason: str
    integrity: str = "high"
    requires: tuple = ()


MMC_CONSOLES = (
    MmcConsole(
        "compmgmt.msc", "Computer Management", GROUP_ADMINISTRATION,
        "computer-management",
        ("computer management", "system tools", "shared folders", "services",
         "disk management"),
        "Documented administrative console; the aggregate local management "
        "snap-in."),
    MmcConsole(
        "devmgmt.msc", "Device Manager", GROUP_ADMINISTRATION,
        "device-manager",
        ("drivers", "hardware", "update driver", "yellow triangle"),
        "Documented administrative console; driver and device management."),
    MmcConsole(
        "diskmgmt.msc", "Disk Management", GROUP_STORAGE_RECOVERY,
        "disk-management",
        ("partition", "volume", "drive letter", "format", "shrink", "extend"),
        "Documented administrative console; partitioning and drive letters."),
    MmcConsole(
        "services.msc", "Services", GROUP_ADMINISTRATION, "services",
        ("service", "startup type", "start service", "stop service",
         "automatic delayed"),
        "Documented administrative console; service configuration."),
    MmcConsole(
        "eventvwr.msc", "Event Viewer", GROUP_UPDATES_DIAGNOSTICS,
        "event-viewer",
        ("event log", "application log", "system log", "crash", "error log"),
        "Documented administrative console; the Windows event logs.",
        integrity="medium"),
    MmcConsole(
        "perfmon.msc", "Performance Monitor", GROUP_UPDATES_DIAGNOSTICS,
        "performance-monitor",
        ("counters", "data collector", "resource monitor", "performance"),
        "Documented administrative console; performance counters.",
        integrity="medium"),
    MmcConsole(
        "taskschd.msc", "Task Scheduler", GROUP_ADMINISTRATION,
        "task-scheduler",
        ("scheduled task", "trigger", "at logon", "run whether user is "
         "logged on"),
        "Documented administrative console; scheduled tasks. EsotericOS's own "
        "elevated sign-in task lives here."),
    MmcConsole(
        "WF.msc", "Windows Defender Firewall with Advanced Security",
        GROUP_PRIVACY_SECURITY, "firewall-advanced",
        ("firewall rules", "inbound rule", "outbound rule", "advanced "
         "firewall", "connection security"),
        "Documented administrative console; per-rule firewall configuration."),
    MmcConsole(
        "certmgr.msc", "Certificates (Current User)", GROUP_PRIVACY_SECURITY,
        "certificates-user",
        ("certificate", "personal certificates", "trusted root"),
        "Documented administrative console; the current user's certificate "
        "store.", integrity="medium"),
    MmcConsole(
        "certlm.msc", "Certificates (Local Computer)", GROUP_PRIVACY_SECURITY,
        "certificates-machine",
        ("machine certificate", "computer certificates", "trusted root"),
        "Documented administrative console; the machine certificate store."),
    MmcConsole(
        "lusrmgr.msc", "Local Users and Groups", GROUP_ACCOUNTS_SIGNIN,
        "local-users",
        ("local user", "group", "administrators group", "password never "
         "expires"),
        "Documented administrative console; local accounts and groups.",
        requires=(EDITION_PRO,)),
    MmcConsole(
        "gpedit.msc", "Local Group Policy Editor", GROUP_ADMINISTRATION,
        "group-policy",
        ("group policy", "policy", "administrative templates", "gpo"),
        "Documented administrative console; local policy.",
        requires=(EDITION_PRO,)),
    MmcConsole(
        "secpol.msc", "Local Security Policy", GROUP_PRIVACY_SECURITY,
        "security-policy",
        ("security policy", "audit policy", "user rights assignment",
         "account lockout"),
        "Documented administrative console; local security policy.",
        requires=(EDITION_PRO,)),
    MmcConsole(
        "rsop.msc", "Resultant Set of Policy", GROUP_ADMINISTRATION,
        "rsop", ("resultant set", "effective policy", "rsop"),
        "Documented administrative console; effective policy report.",
        requires=(EDITION_PRO,)),
    MmcConsole(
        "tpm.msc", "TPM Management", GROUP_PRIVACY_SECURITY, "tpm",
        ("trusted platform module", "tpm", "clear tpm", "attestation"),
        "Documented administrative console; TPM state and management.",
        requires=("hardware:tpm",)),
    MmcConsole(
        "fsmgmt.msc", "Shared Folders", GROUP_NETWORK_SHARING, "shared-folders",
        ("shares", "open files", "sessions", "share permissions"),
        "Documented administrative console; SMB shares and open sessions."),
    MmcConsole(
        "WmiMgmt.msc", "WMI Control", GROUP_ADMINISTRATION, "wmi",
        ("wmi", "namespace security", "repository"),
        "Documented administrative console; WMI namespace configuration."),
    MmcConsole(
        "comexp.msc", "Component Services", GROUP_ADMINISTRATION,
        "component-services",
        ("dcom", "com+", "distributed transaction coordinator"),
        "Documented administrative console; COM+ and DCOM configuration."),
    MmcConsole(
        "azman.msc", "Authorization Manager", GROUP_ADMINISTRATION,
        "authorization-manager", ("azman", "role based", "authorization store"),
        "Documented administrative console; role-based authorisation stores."),
    MmcConsole(
        "DevModeRunAsUserConfig.msc", "Developer Mode Run-As-User Config",
        GROUP_UPDATES_DIAGNOSTICS, "devmode-runas",
        ("developer mode", "run as user", "device portal"),
        "Documented console shipped with the developer-mode feature."),
    MmcConsole(
        "printmanagement.msc", "Print Management", GROUP_DEVICES_INPUT,
        "print-management",
        ("print server", "printer drivers", "print queues", "forms"),
        "Documented administrative console; print server management.",
        requires=(EDITION_PRO,)),
)

MMC_BY_FILENAME = {console.filename.lower(): console
                   for console in MMC_CONSOLES}
