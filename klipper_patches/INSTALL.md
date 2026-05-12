# Klipper MCU Patch — Display + Strain Gauge Coexistence

This patch adds serial bridge support (for the stock TJC display) to the Klipper MCU
firmware **while keeping the HX711 strain gauge fully functional**.

## Files

```
klipper_patches/
├── src/
│   ├── serial_bridge.c          ← Host-facing serial bridge command handler
│   ├── dirzctl.c                ← Direction control for display serial
│   └── stm32/
│       ├── serial_bridge.c      ← STM32 USART2 hardware implementation
│       └── serial_bridge.h      ← STM32 bridge constants/declarations
└── INSTALL.md                   ← This file
```

## 1. Copy source files

```bash
cp klipper_patches/src/serial_bridge.c ~/klipper/src/
cp klipper_patches/src/dirzctl.c       ~/klipper/src/
cp klipper_patches/src/stm32/serial_bridge.c ~/klipper/src/stm32/
cp klipper_patches/src/stm32/serial_bridge.h ~/klipper/src/stm32/
```

## 2. Edit `~/klipper/src/Makefile`

Add these two lines **at the top of the file**, before other `src-` entries:

```makefile
src-$(CONFIG_SERIAL_BRIDGE) += serial_bridge.c
```

And on the existing line that includes `trsync.c`, append `dirzctl.c`:

```makefile
# Before:
    trsync.c
# After:
    trsync.c dirzctl.c
```

## 3. Enable in `.config`

Run `make menuconfig` and enable:

| Setting | Value |
|---------|-------|
| Serial Bridge | **Enabled** |
| Serial Bridge USART | **USART2 (PA3/PA2)** |

Or add directly to `~/klipper/.config`:

```ini
CONFIG_SERIAL_BRIDGE=y
CONFIG_ENABLE_SERIAL_BRIDGE_USART2=y
```

## 4. Compile and flash

```bash
cd ~/klipper
make clean
make

# Copy to SD card and flash as usual
# Rename to a unique filename (e.g. firmware_display.bin)
```

## 5. Verify

In `klippy.log` after restart you should see:

```
Serial bridge: available configs for mcu: SERIAL_BRIDGE_CONFIG_USART2_PA3,PA2=3
```

## Why USART2?

The Ender 3 V3 SE display (TJC3224_011N) connects via USART2 on pins:
- TX: PA2
- RX: PA3

The serial bridge config ID `3` corresponds to `SERIAL_BRIDGE_CONFIG_USART2_PA3,PA2`.
