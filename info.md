# Xiaomi LYWSD02 Clock

Keeps a Xiaomi LYWSD02 e-ink clock on time over Bluetooth, through your existing
Home Assistant Bluetooth proxies - no ESPHome node has to own the job.

- Time sync every 24 h, on demand, and whenever your UTC offset changes (DST)
- Verifies by reading the clock back; a write ack alone is not treated as success
- `sync_age` sensor to alert on, `drift` sensor measured before each correction
- °C/°F on the clock's own display, read back from the device

12/24-hour switching is **not** possible on LYWSD02 hardware - see the README for
the evidence.
