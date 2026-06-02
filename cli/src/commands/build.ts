import { type CliOptions, packBasePath, packPath, repoRoot } from "../workspace.ts";
import { buildImage } from "../vm/smolvm.ts";

export async function buildCommand(_opts: CliOptions): Promise<void> {
  const root = repoRoot();
  const base = packBasePath();
  console.log("smolduck build — baking the microVM image + pack");
  try {
    await buildImage(root, base);
  } catch (err) {
    console.error(`build failed: ${err instanceof Error ? err.message : err}`);
    process.exit(1);
  }
  console.log(`\n  ✦ pack ready → ${packPath()}`);
  console.log("  run it with `smolduck run <workspace>`.");
}
