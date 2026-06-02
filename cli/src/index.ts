#!/usr/bin/env bun
import { parseArgs } from "node:util";
import { type CliOptions } from "./workspace.ts";
import { runCommand } from "./commands/run.ts";
import { stopCommand } from "./commands/stop.ts";
import { statusCommand } from "./commands/status.ts";
import { buildCommand } from "./commands/build.ts";

const HELP = `smolduck — a data analyst in a box

Usage:
  smolduck run [path]      boot the workbench against a workspace folder (default: .)
  smolduck stop [path]     stop the running session; the workspace is left intact
  smolduck status [path]   show the running session for a workspace
  smolduck build           (re)build the microVM image + pack

Flags:
  --port <n>     UI port (default: 4290)
  --no-open      do not open a browser
  --readonly     mount the workspace read-only (session artifacts are ephemeral)
  --mem <size>   microVM memory (e.g. 2g, 2048m)
  --cpus <n>     microVM vCPUs
  -h, --help     show this help
`;

async function main(): Promise<void> {
  const { values, positionals } = parseArgs({
    args: Bun.argv.slice(2),
    allowPositionals: true,
    options: {
      port: { type: "string" },
      "no-open": { type: "boolean", default: false },
      readonly: { type: "boolean", default: false },
      mem: { type: "string" },
      cpus: { type: "string" },
      help: { type: "boolean", short: "h", default: false },
    },
  });

  const [command, pathArg] = positionals;

  if (values.help || !command) {
    console.log(HELP);
    return;
  }

  const opts: CliOptions = {
    path: pathArg ?? ".",
    port: values.port ? parseInt(values.port, 10) : 4290,
    open: !values["no-open"],
    readonly: !!values.readonly,
    mem: values.mem,
    cpus: values.cpus,
  };

  switch (command) {
    case "run":
      await runCommand(opts);
      break;
    case "stop":
      stopCommand(opts);
      break;
    case "status":
      statusCommand(opts);
      break;
    case "build":
      await buildCommand(opts);
      break;
    default:
      console.error(`unknown command: ${command}\n`);
      console.log(HELP);
      process.exit(1);
  }
}

main();
