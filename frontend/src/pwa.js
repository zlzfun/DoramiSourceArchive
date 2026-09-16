import { useSyncExternalStore } from 'react';

const media = window.matchMedia('(display-mode: standalone), (display-mode: fullscreen), (display-mode: minimal-ui)');
const listeners = new Set();
let installEvent;
// Product scope, not a capability test. Huawei's compatibility UA can omit the OS;
// keep its browser out of this release's installation flow even if it emits an event.
const installEnabled = !/HarmonyOS|OpenHarmony|HuaweiBrowser/i.test(`${navigator.userAgent} ${navigator.userAgentData?.platform || ''}`);
let snapshot = { installEnabled, installed: media.matches || navigator.standalone === true, canPrompt: false, updateAvailable: false, online: navigator.onLine };
function publish(patch) {
  snapshot = { ...snapshot, ...patch };
  listeners.forEach((listener) => listener());
}
const subscribe = (listener) => { listeners.add(listener); return () => listeners.delete(listener); };
export function usePwa() { return useSyncExternalStore(subscribe, () => snapshot); }

// Capture before React mounts; a late-opened settings page must not miss this one-shot event.
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  if (!installEnabled) return;
  installEvent = event;
  publish({ canPrompt: true });
});
window.addEventListener('appinstalled', () => {
  installEvent = undefined;
  publish({ installed: true, canPrompt: false });
});
media.addEventListener('change', () => publish({ installed: media.matches || navigator.standalone === true }));
window.addEventListener('online', () => publish({ online: true }));
window.addEventListener('offline', () => publish({ online: false }));

export async function promptInstall() {
  const event = installEvent;
  if (!installEnabled || !event) return 'unavailable';
  installEvent = undefined; // A browser event can only be consumed once, including cancellation.
  publish({ canPrompt: false });
  try {
    await event.prompt();
    return (await event.userChoice).outcome;
  } catch {
    return 'error';
  }
}

// Within the supported product scope, UA only selects instructions; it cannot prove capability.
export function installGuide(nav = navigator, secure = window.isSecureContext) {
  const ua = nav.userAgent;
  if (!secure) return 'insecure';
  if (/MicroMessenger|\bwv\b|FBAN|FBAV/i.test(ua)) return 'embedded';
  if (/iPad|iPhone|iPod/i.test(ua) || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1)) return 'ios';
  if (/Chrome|Chromium|Edg\//i.test(ua)) return 'chromium';
  return 'generic';
}

export function dismissUpdate() { publish({ updateAvailable: false }); }

export function startPwa() {
  if (!import.meta.env.PROD || !window.isSecureContext || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).then((registration) => {
    const observe = (worker) => {
      if (!worker) return;
      worker.addEventListener('statechange', () => {
        if (worker.state === 'installed' && navigator.serviceWorker.controller) publish({ updateAvailable: true });
      });
    };
    observe(registration.installing);
    registration.addEventListener('updatefound', () => observe(registration.installing));
    let lastCheck = Date.now();
    const check = () => {
      if (document.visibilityState === 'visible' && navigator.onLine && Date.now() - lastCheck > 300_000) {
        lastCheck = Date.now();
        registration.update().catch(() => {}); // Offline/failed checks must not break reading.
      }
    };
    document.addEventListener('visibilitychange', check);
    window.addEventListener('online', check);
  }).catch(() => {}); // Installation guidance and ordinary browser reading still work without SW.
}
