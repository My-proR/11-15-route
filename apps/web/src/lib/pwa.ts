/** Registers the service worker in production builds only (never during local development or tests). */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

export const isStandalone = (): boolean =>
  window.matchMedia?.("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;

export const isIosSafari = (): boolean => /iphone|ipad|ipod/i.test(navigator.userAgent) && !/crios|fxios/i.test(navigator.userAgent);
