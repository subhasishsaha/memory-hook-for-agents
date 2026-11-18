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

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
