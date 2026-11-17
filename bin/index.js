#!/usr/bin/env node

const { spawn } = require("child_process");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const pythonCmd = process.platform === "win32" ? "python" : "python3";
const args = ["-m", "memory_hook.cli", ...process.argv.slice(2)];

const child = spawn(pythonCmd, args, {
  cwd: rootDir,
  stdio: "inherit",
  shell: false,
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
