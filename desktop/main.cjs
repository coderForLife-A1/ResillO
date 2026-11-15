const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const HOST = "127.0.0.1";
const PORT = 8000;
let backend;
let stopping = false;
let restartTimer;

function waitForBackend() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 30000;
    const check = () => {
      const request = http.get(`http://${HOST}:${PORT}/api/health`, (response) => {
        response.resume();
        if (response.statusCode === 200) {
          resolve();
        } else {
          retry();
        }
      });
      request.on("error", retry);
      request.setTimeout(1000, () => request.destroy());
    };
    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error("ResillO backend did not start within 30 seconds."));
        return;
      }
      setTimeout(check, 250);
    };
    check();
  });
}

async function createWindow() {
  backend = spawn(
    process.env.RESILIO_PYTHON || "python",
    ["-m", "uvicorn", "main:app", "--host", HOST, "--port", String(PORT)],
    { cwd: path.join(__dirname, ".."), windowsHide: true, stdio: "ignore" }
  );
  backend.on("error", (error) => console.error("Could not start ResillO backend:", error));
  backend.on("exit", (code, signal) => {
    backend = undefined;
    if (!stopping) {
      const delay = code === 0 ? 250 : 1000;
      restartTimer = setTimeout(() => createWindow().catch((error) => {
        console.error("ResillO backend restart failed:", error);
        app.quit();
      }), delay);
    }
    console.warn(`ResillO backend exited (${code ?? "null"}/${signal ?? "none"})`);
  });

  await waitForBackend();
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 960,
    minHeight: 700,
    title: "ResillO",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  await window.loadURL(`http://${HOST}:${PORT}/`);
}

app.whenReady().then(() => createWindow().catch((error) => {
  console.error(error);
  app.quit();
}));

app.on("window-all-closed", () => {
  stopping = true;
  if (restartTimer) clearTimeout(restartTimer);
  if (backend && !backend.killed) backend.kill("SIGTERM");
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopping = true;
  if (restartTimer) clearTimeout(restartTimer);
  if (backend && !backend.killed) backend.kill("SIGTERM");
});
