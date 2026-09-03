#!/usr/bin/env node
/**
 * Check only JavaScript and TypeScript files changed between two validated commits.
 *
 * Copyright (c) 2026-present Ngo Quoc Huy
 * SPDX-License-Identifier: MIT
 */

import { spawnSync } from "node:child_process";

const filePatterns = ["*.js", "*.jsx", "*.ts", "*.tsx", "*.cjs", "*.mjs", "*.cts", "*.mts"];
const [baseRef = "origin/main", headRef = "HEAD"] = process.argv.slice(2);

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    encoding: "utf8",
    ...options,
  });
}

function resolveCommit(label, ref) {
  if (ref.length === 0) {
    console.error(`check:anti-slop: ${label} ref must not be empty`);
    process.exit(2);
  }

  const result = run("git", [
    "rev-parse",
    "--verify",
    "--quiet",
    "--end-of-options",
    `${ref}^{commit}`,
  ]);
  if (result.error) {
    console.error(`check:anti-slop: could not run Git: ${result.error.message}`);
    process.exit(1);
  }
  const commit = result.stdout.trim();
  if (result.status !== 0 || !/^[0-9a-f]+$/iu.test(commit)) {
    console.error(`check:anti-slop: invalid ${label} ref: ${ref}`);
    process.exit(2);
  }
  return commit;
}

const baseCommit = resolveCommit("base", baseRef);
const headCommit = resolveCommit("head", headRef);
const changed = run("git", [
  "diff",
  "--name-only",
  "--diff-filter=ACMR",
  `${baseCommit}...${headCommit}`,
  "--",
  ...filePatterns,
  ":(exclude)tools/oxlint/anti-slop/**",
]);

if (changed.status !== 0) {
  process.stderr.write(changed.stderr ?? "");
  process.exit(changed.status ?? 1);
}

const files = changed.stdout.split(/\r?\n/u).filter(Boolean);
if (files.length === 0) process.exit(0);

const oxlint = run(process.platform === "win32" ? "oxlint.cmd" : "oxlint", files, {
  stdio: "inherit",
});
if (oxlint.error) {
  console.error(`check:anti-slop: could not start Oxlint: ${oxlint.error.message}`);
  process.exit(1);
}
process.exit(oxlint.status ?? 1);
