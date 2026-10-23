// electron-builder settings (ADR 0028). Signing and notarization stay off unless the
// person building provides an Apple Developer ID through the environment; this file
// never holds a certificate, password or key.
//
// - Signing turns on with CSC_NAME (a "Developer ID Application" identity already in
//   the login keychain) or CSC_LINK + CSC_KEY_PASSWORD (an exported .p12).
// - Notarization turns on, when signing, with one of electron-builder's credential
//   sets: APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER, or APPLE_ID +
//   APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID, or APPLE_KEYCHAIN_PROFILE.
const env = process.env;
const signing = Boolean(env.CSC_NAME || env.CSC_LINK);
const notarizing =
  signing &&
  Boolean(
    (env.APPLE_API_KEY && env.APPLE_API_KEY_ID && env.APPLE_API_ISSUER) ||
    (env.APPLE_ID && env.APPLE_APP_SPECIFIC_PASSWORD && env.APPLE_TEAM_ID) ||
    env.APPLE_KEYCHAIN_PROFILE,
  );

/** @type {import("electron-builder").Configuration} */
module.exports = {
  appId: "com.pensivetraveler.siw.research",
  productName: "SIW Research",
  directories: {
    output: "release",
    buildResources: "assets",
  },
  files: [
    "main.cjs",
    "api-process.cjs",
    "repo-location.cjs",
    "ui/**/*",
    "siw-config.json",
  ],
  mac: {
    category: "public.app-category.finance",
    icon: "assets/icon.png",
    target: ["dmg"],
    // null skips signing; undefined lets electron-builder use CSC_NAME / CSC_LINK.
    identity: signing ? undefined : null,
    hardenedRuntime: true,
    entitlements: "assets/entitlements.mac.plist",
    entitlementsInherit: "assets/entitlements.mac.plist",
    notarize: notarizing,
  },
};
