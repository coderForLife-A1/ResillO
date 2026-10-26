import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.resillo.triage",
  appName: "ResillO",
  webDir: "www",
  server: {
    androidScheme: "https",
    cleartext: true
  }
};

export default config;
