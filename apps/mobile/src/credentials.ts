/** Input rules for the passenger boarding code (mirrors the API: 4 digits). */
export const digitsOnly = (v: string, max: number): string => v.replace(/\D/g, "").slice(0, max);
export const isBoardingCode = (v: string): boolean => /^\d{4}$/.test(v);

/** A scanned value must look like a RoutePilot trip QR token before it is sent to the server. */
export const isTripQr = (v: string): boolean => /^rp1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(v) && v.length <= 600;
