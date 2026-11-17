#!/usr/bin/env node

const { spawn } = require("child_process");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const pythonScript = path.join(rootDir, "memory_hook", "cli.py");
const args = [pythonScript, ...process.argv.slice(2)];

const pythonCmd = process.platform === "win32" ? "python" : "python3";

const child = spawn(pythonCmd, args, {
  stdio: "inherit",
  shell: true,
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
