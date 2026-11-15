import type { ExpoConfig } from "expo/config";

// Build-time settings (set in eas.json "env" or your shell). Only PUBLIC values belong here.
const PACKAGE = process.env.ANDROID_PACKAGE ?? "in.routepilot.app";        // change BEFORE your first Play upload; it can never change afterwards
const APP_LINK_HOST = process.env.APP_LINK_HOST ?? "";                       // e.g. routepilot.vercel.app — makes WhatsApp invite links open the app
const EAS_PROJECT_ID = process.env.EAS_PROJECT_ID ?? "";                     // printed by `eas init`

const config: ExpoConfig = {
  name: "RoutePilot",
  slug: "routepilot",
  scheme: "routepilot",
  version: "1.0.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "automatic",
  ios: {
    supportsTablet: false,
    bundleIdentifier: PACKAGE,
    infoPlist: {
      NSLocationWhenInUseUsageDescription: "RoutePilot uses your location only while you are on an active trip (drivers) or when you choose to attach it to an SOS alert, and only if your organization has enabled it.",
      NSCameraUsageDescription: "RoutePilot uses the camera only when you tap \"Scan trip QR to board\" to scan your driver's QR code.",
    },
  },
  android: {
    package: PACKAGE,
    versionCode: Number(process.env.ANDROID_VERSION_CODE ?? 1),
    permissions: ["ACCESS_COARSE_LOCATION", "ACCESS_FINE_LOCATION", "POST_NOTIFICATIONS", "CAMERA"],
    blockedPermissions: ["RECORD_AUDIO", "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE", "SYSTEM_ALERT_WINDOW"],
    adaptiveIcon: { foregroundImage: "./assets/adaptive-foreground.png", backgroundColor: "#4f46e5" },
    allowBackup: false, // tokens live in the Keystore; do not let them be restored onto another device
    intentFilters: APP_LINK_HOST
      ? [{ action: "VIEW", autoVerify: true, data: [{ scheme: "https", host: APP_LINK_HOST, pathPrefix: "/join" }], category: ["BROWSABLE", "DEFAULT"] }]
      : [],
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    "expo-web-browser",
    "expo-system-ui",
    ["expo-splash-screen", { image: "./assets/splash-icon.png", imageWidth: 200, resizeMode: "contain", backgroundColor: "#ffffff", dark: { backgroundColor: "#0e1120" } }],
    ["expo-camera", { cameraPermission: "RoutePilot uses the camera only when you tap \"Scan trip QR to board\" to scan your driver's QR code.", recordAudioAndroid: false }],
    ["expo-location", { locationWhenInUsePermission: "Allow RoutePilot to use your location during an active trip or an SOS you send.", isAndroidBackgroundLocationEnabled: false, isIosBackgroundLocationEnabled: false }],
    ["expo-notifications", { icon: "./assets/notification-icon.png", color: "#4f46e5" }],
  ],
  experiments: { typedRoutes: false },
  extra: { eas: { projectId: EAS_PROJECT_ID || undefined } },
};
export default config;
