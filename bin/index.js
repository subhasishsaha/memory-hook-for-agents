#!/usr/bin/env node

const { spawn } = require("child_process");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const pythonCmd = process.platform === "win32" ? "python" : "python3";
const args = ["-m", "memory_hook.cli", ...process.argv.slice(2)];

const env = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${rootDir}${path.delimiter}${process.env.PYTHONPATH}`
    : rootDir,
};

const child = spawn(pythonCmd, args, {
  cwd: process.cwd(),
  env: env,
  stdio: "inherit",
  shell: false,
});

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    console.error(
      `\x1b[31m[ERROR]\x1b[0m Could not find '${pythonCmd}' on PATH. Python 3.10+ is required.`
    );
  } else {
    console.error(`\x1b[31m[ERROR]\x1b[0m Failed to spawn '${pythonCmd}': ${err.message}`);
  }
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 0);
  }
});

const forwardSignal = (sig) => {
  if (!child.killed) {
    try {
      child.kill(sig);
    } catch (_) {}
  }
};

process.on("SIGINT", () => forwardSignal("SIGINT"));
process.on("SIGTERM", () => forwardSignal("SIGTERM"));
