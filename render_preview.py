#!/usr/bin/env python3
"""
Render a single dashboard frame to a PNG, with no e-paper hardware required.

Same codebase as the Pi: this script auto-detects whether the real
waveshare_epd driver is importable.

- On the Raspberry Pi (with spidev/gpiozero/lgpio installed): the real
  epd10in85.EPD is used, and it drives the physical panel as normal.
- Everywhere else (e.g. this Windows/VS Code dev machine): the
  waveshare_epd package plus its RPi.GPIO/spidev imports are stubbed out
  *before* main.py is imported, so main.py's `from waveshare_epd import
  epd10in85` line resolves to a fake EPD whose display()/getbuffer() write
  a PNG instead of talking to SPI. See docs/CLAUDE.md for why this has to
  happen at the sys.modules level rather than by subclassing.

Usage:
    python render_preview.py [-o output.png] [--settle SECONDS]
"""
import argparse
import os
import sys
import threading
import time
import types

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(BASE_DIR, 'lib')
if os.path.isdir(LIB_DIR) and LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _hardware_available():
    """Try the real driver import. True only where the Pi's GPIO/SPI stack
    (spidev, gpiozero, lgpio) is actually installed."""
    try:
        import waveshare_epd.epd10in85  # noqa: F401
        return True
    except Exception:
        # A failed import can leave a half-initialised package behind;
        # clear it so the mock installed below starts from a clean slate.
        for name in list(sys.modules):
            if name == 'waveshare_epd' or name.startswith('waveshare_epd.'):
                del sys.modules[name]
        return False


def _install_hardware_mocks(output_path):
    """Inject fake RPi.GPIO / spidev / waveshare_epd modules into
    sys.modules so main.py's hardware imports succeed without a Pi, and
    make the fake EPD write its frame to `output_path` instead of SPI."""
    from PIL import Image

    rpi_pkg = types.ModuleType('RPi')
    rpi_gpio = types.ModuleType('RPi.GPIO')
    rpi_gpio.BCM = rpi_gpio.BOARD = rpi_gpio.OUT = rpi_gpio.IN = 0
    rpi_gpio.setmode = lambda *a, **k: None
    rpi_gpio.setwarnings = lambda *a, **k: None
    rpi_gpio.setup = lambda *a, **k: None
    rpi_gpio.output = lambda *a, **k: None
    rpi_gpio.input = lambda *a, **k: 1
    rpi_gpio.cleanup = lambda *a, **k: None
    rpi_pkg.GPIO = rpi_gpio
    sys.modules['RPi'] = rpi_pkg
    sys.modules['RPi.GPIO'] = rpi_gpio

    spidev_mod = types.ModuleType('spidev')

    class _FakeSpiDev:
        max_speed_hz = 0
        mode = 0

        def open(self, *a, **k): pass
        def close(self): pass
        def writebytes(self, *a, **k): pass
        def writebytes2(self, *a, **k): pass

    spidev_mod.SpiDev = _FakeSpiDev
    sys.modules['spidev'] = spidev_mod

    epd_pkg = types.ModuleType('waveshare_epd')
    epd10in85_mod = types.ModuleType('waveshare_epd.epd10in85')

    class _FakeEpdConfig:
        @staticmethod
        def module_init(cleanup=False):
            return 0

        @staticmethod
        def module_exit(cleanup=False):
            pass

    class EPD:
        width = 1360
        height = 480

        def init(self):
            return 0

        def init_Part(self):
            return 0

        def Clear(self):
            pass

        def Clear_Black(self):
            pass

        def sleep(self):
            pass

        def getbuffer(self, image):
            # Mirrors the real driver's packing so the mock exercises the
            # same code path main.py's render loop always takes.
            if image.mode != '1':
                image = image.convert('1')
            return bytearray(image.tobytes())

        def display(self, buf):
            self._save(buf)

        def display_Partial(self, buf, xstart, ystart, xend, yend):
            self._save(buf)

        def _save(self, buf):
            frame = Image.frombytes('1', (self.width, self.height), bytes(buf))
            frame.save(output_path)
            print(f"[mock epd] wrote {output_path}")

    epd10in85_mod.EPD = EPD
    epd10in85_mod.epdconfig = _FakeEpdConfig
    epd_pkg.epd10in85 = epd10in85_mod

    sys.modules['waveshare_epd'] = epd_pkg
    sys.modules['waveshare_epd.epd10in85'] = epd10in85_mod


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-o', '--output', default=os.path.join(BASE_DIR, 'output.png'),
                         help="Path to write the rendered PNG (default: output.png)")
    parser.add_argument('--settle', type=float, default=6.0,
                         help="Seconds to let the background data-fetch thread populate "
                              "weather/crypto/ping before rendering the frame (default: 6)")
    args = parser.parse_args()

    on_hardware = _hardware_available()
    if on_hardware:
        print("Real e-paper driver detected -> hardware mode.")
    else:
        print("e-paper driver unavailable -> preview mode (mocking display + GPIO/SPI).")
        _install_hardware_mocks(args.output)

    import main as dashboard  # noqa: E402  (import must follow the mock setup above)

    # One-time interactive setup (e.g. Spotify/Gmail's OAuth code exchange):
    # a no-op once the relevant ENABLE_* flag is off or the token file
    # already exists, so this is safe to leave in on every run.
    dashboard.auth_spotify()
    dashboard.auth_gmail()

    epd = dashboard.epd10in85.EPD()
    epd.init()
    epd.Clear()
    epd.init_Part()

    fonts = dashboard.load_fonts()

    fetch_thread = threading.Thread(target=dashboard.update_data_thread, daemon=True)
    fetch_thread.start()
    if args.settle > 0:
        print(f"Waiting {args.settle:.0f}s for weather/crypto/ping to populate...")
        time.sleep(args.settle)

    image = dashboard.render_screen(epd, fonts)
    buf = epd.getbuffer(image)
    epd.display(buf)

    if on_hardware:
        print("Frame pushed to the display.")
    else:
        print(f"Preview written to {args.output} ({image.size[0]}x{image.size[1]})")


if __name__ == '__main__':
    main()
