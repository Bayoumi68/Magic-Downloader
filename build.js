#!/usr/bin/env node
/**
 * Magic Downloader — cross-browser manifest builder.
 *
 * Chrome loads `browser_extension/` as an unpacked extension and refuses to run
 * if it finds `background.scripts` in a Manifest V3 file — including any stray
 * `manifest.firefox.json` left in that folder. To make Chrome and Firefox
 * mutually exclusive, the platform manifests live OUTSIDE the extension folder,
 * in `manifests/`, and this script assembles exactly ONE `manifest.json` into
 * the extension root for the target browser.
 *
 *   node build.js --chrome     -> background.service_worker (MV3)
 *   node build.js --firefox    -> background.scripts + gecko settings
 *
 * It also deletes any older per-browser manifest files that may still be sitting
 * inside `browser_extension/`, so the folder Chrome loads never contains a
 * `scripts` key anywhere.
 */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const SRC_DIR = path.join(ROOT, "manifests");
const EXT_DIR = path.join(ROOT, "browser_extension");
const OUT_FILE = path.join(EXT_DIR, "manifest.json");

// Stale per-browser manifests that must NOT remain in the folder Chrome loads.
const STALE_IN_EXT = ["manifest.chrome.json", "manifest.firefox.json"];

const TARGETS = {
  "--chrome": { overlay: "manifest.chrome.json", forbid: "scripts", require: "service_worker" },
  "--firefox": { overlay: "manifest.firefox.json", forbid: "service_worker", require: "scripts" },
};

function fail(message) {
  console.error("build.js: " + message);
  process.exit(1);
}

function readJson(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (err) {
    fail("cannot read " + path.relative(ROOT, file) + " (" + err.message + ")");
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    fail("invalid JSON in " + path.relative(ROOT, file) + " (" + err.message + ")");
  }
}

function parseTarget(argv) {
  const flags = argv.slice(2).filter((a) => a.startsWith("--"));
  const picked = flags.filter((a) => a in TARGETS);
  if (picked.length !== 1) {
    fail("usage: node build.js --chrome | --firefox");
  }
  return picked[0];
}

function main() {
  const flag = parseTarget(process.argv);
  const target = TARGETS[flag];
  const browser = flag.replace("--", "");

  const base = readJson(path.join(SRC_DIR, "manifest.base.json"));
  const overlay = readJson(path.join(SRC_DIR, target.overlay));

  // Top-level overlay wins. `background` comes entirely from the overlay (the
  // base intentionally has none), so a Chrome build can never carry a `scripts`
  // key and a Firefox build can never carry a `service_worker` key.
  const manifest = Object.assign({}, base, overlay);

  const background = manifest.background || {};
  if (target.forbid in background) {
    fail("refusing to write " + browser + " manifest: background." + target.forbid + " is present");
  }
  if (!(target.require in background)) {
    fail("refusing to write " + browser + " manifest: background." + target.require + " is missing");
  }

  if (!fs.existsSync(EXT_DIR)) {
    fail("missing extension folder: " + path.relative(ROOT, EXT_DIR));
  }

  fs.writeFileSync(OUT_FILE, JSON.stringify(manifest, null, 2) + "\n", "utf8");

  // Remove any per-browser manifests that would otherwise linger in the folder
  // Chrome loads (this is the whole point — no `scripts` key anywhere).
  const removed = [];
  for (const name of STALE_IN_EXT) {
    const p = path.join(EXT_DIR, name);
    if (fs.existsSync(p)) {
      fs.unlinkSync(p);
      removed.push(name);
    }
  }

  console.log(
    "build.js: wrote " +
      path.relative(ROOT, OUT_FILE) +
      " for " +
      browser +
      " (background." +
      target.require +
      ", v" +
      (manifest.version || "?") +
      ")"
  );
  if (removed.length) {
    console.log("build.js: removed stale " + removed.join(", ") + " from browser_extension/");
  }
}

main();
