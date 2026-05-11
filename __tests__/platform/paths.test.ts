import { describe, it, expect } from "vitest";
import { tmpdir as osTempdir, homedir as osHomedir } from "node:os";
import { realpathSync } from "node:fs";
import { sep } from "node:path";
import { PathsImpl } from "../../src/platform/capabilities/paths.js";

const paths = new PathsImpl("stackowl");

describe("PathsImpl", () => {
  it("tempdir() returns realpath-resolved os.tmpdir()", () => {
    const expected = realpathSync(osTempdir());
    expect(paths.tempdir()).toBe(expected);
  });

  it("home() returns os.homedir()", () => {
    expect(paths.home()).toBe(osHomedir());
  });

  it("configDir() returns an absolute path containing the app name", () => {
    const dir = paths.configDir();
    expect(dir.length).toBeGreaterThan(0);
    expect(dir.toLowerCase()).toContain("stackowl");
  });

  it("cacheDir/dataDir/logDir return distinct absolute paths", () => {
    const c = paths.cacheDir();
    const d = paths.dataDir();
    const l = paths.logDir();
    expect(c).not.toBe(d);
    expect(c).not.toBe(l);
    expect(d).not.toBe(l);
  });

  it("isInside detects child paths inside a root", () => {
    const root = paths.tempdir();
    const child = root + sep + "subdir" + sep + "file.txt";
    expect(paths.isInside(child, root)).toBe(true);
  });

  it("isInside rejects siblings of root", () => {
    expect(paths.isInside("/etc/passwd", paths.tempdir())).toBe(false);
  });

  it("isInside accepts the root itself", () => {
    const root = paths.tempdir();
    expect(paths.isInside(root, root)).toBe(true);
  });
});
