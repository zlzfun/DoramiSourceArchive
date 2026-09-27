#!/usr/bin/env python3
"""Generate PWA PNGs from the existing brand using the E2E Playwright dependency.

Run from the repository: .venv/bin/python frontend/pwa/generate_icons.py
The maskable icon reserves a 40%-radius safe circle for all brand foreground.
"""
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

BRAND = Path(__file__).resolve().parents[1] / "public/brand"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    try:
        page = browser.new_page()
        source = base64.b64encode((BRAND / "dorami-logo-512.png").read_bytes()).decode()
        for name, size, maskable in [("pwa-192.png", 192, False), ("pwa-apple-180.png", 180, False),
                                      ("pwa-maskable-512.png", 512, True)]:
            png = page.evaluate("""async ({source, size, maskable}) => {
                const img = new Image(); img.src = 'data:image/png;base64,' + source; await img.decode();
                const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#200647'; ctx.fillRect(0, 0, size, size);
                const side = maskable ? Math.round(size * .64) : size;
                if (maskable) {
                    // Feather only the source background margin; avoid a visible square inside the mask.
                    const tile = document.createElement('canvas'); tile.width = tile.height = side;
                    const t = tile.getContext('2d'); t.drawImage(img, 0, 0, side, side);
                    t.globalCompositeOperation = 'destination-in';
                    for (const coords of [[0,0,side,0], [0,0,0,side]]) {
                        const fade = t.createLinearGradient(...coords);
                        fade.addColorStop(0, 'transparent'); fade.addColorStop(.08, '#fff');
                        fade.addColorStop(.92, '#fff'); fade.addColorStop(1, 'transparent');
                        t.fillStyle = fade; t.fillRect(0, 0, side, side);
                    }
                    ctx.drawImage(tile, (size-side)/2, (size-side)/2);
                } else ctx.drawImage(img, 0, 0, side, side);
                return canvas.toDataURL('image/png').split(',')[1];
            }""", {"source": source, "size": size, "maskable": maskable})
            (BRAND / name).write_bytes(base64.b64decode(png))
            print(f"{name}: {size}x{size}")
    finally:
        browser.close()
