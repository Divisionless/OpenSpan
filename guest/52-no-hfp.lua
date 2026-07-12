-- Only do A2DP (high-quality headphone audio). Disable the HFP/HSP
-- (headset/mic) backend, which bluez refuses to register in this VM and
-- which otherwise floods WirePlumber with retry errors, starving A2DP.
bluez_monitor.properties["bluez5.roles"] = "[ a2dp_sink a2dp_source ]"
bluez_monitor.properties["bluez5.hfphsp-backend"] = "none"
