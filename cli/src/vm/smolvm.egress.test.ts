import { describe, expect, test } from "bun:test";
import { analystEgressFlags, egressPolicy } from "./smolvm.ts";

// The session VM is offline by default; these flags are the *only* egress the
// analyst opens. Mirrors providers.py provider selection (explicit var wins,
// else Anthropic when a key is present, else none).
describe("analystEgressFlags", () => {
  test("no analyst configured → no egress", () => {
    expect(analystEgressFlags({})).toEqual([]);
    expect(analystEgressFlags({ SMOLDUCK_TOKEN: "abc" })).toEqual([]);
  });

  test("explicit ollama → host-loopback only", () => {
    expect(analystEgressFlags({ SMOLDUCK_LLM_PROVIDER: "ollama" })).toEqual([
      "--outbound-localhost-only",
    ]);
    // case/space tolerant, like the backend's .strip().lower()
    expect(analystEgressFlags({ SMOLDUCK_LLM_PROVIDER: " Ollama " })).toEqual([
      "--outbound-localhost-only",
    ]);
  });

  test("explicit anthropic needs a key to actually open egress", () => {
    expect(analystEgressFlags({ SMOLDUCK_LLM_PROVIDER: "anthropic" })).toEqual([]);
    expect(
      analystEgressFlags({ SMOLDUCK_LLM_PROVIDER: "anthropic", ANTHROPIC_API_KEY: "sk-ant" }),
    ).toEqual(["--allow-host", "api.anthropic.com"]);
  });

  test("a key alone auto-selects anthropic", () => {
    expect(analystEgressFlags({ ANTHROPIC_API_KEY: "sk-ant" })).toEqual([
      "--allow-host",
      "api.anthropic.com",
    ]);
  });

  test("explicit provider overrides an ambient key (ollama wins over a stray key)", () => {
    expect(
      analystEgressFlags({ SMOLDUCK_LLM_PROVIDER: "ollama", ANTHROPIC_API_KEY: "sk-ant" }),
    ).toEqual(["--outbound-localhost-only"]);
  });

  test("fake mode makes no network calls → no egress", () => {
    expect(
      analystEgressFlags({ SMOLDUCK_AGENT_FAKE: "1", SMOLDUCK_LLM_PROVIDER: "ollama" }),
    ).toEqual([]);
  });
});

// The human-readable read of the same flags — drives the boot receipt, the
// teardown proof, and (via the backend) the UI badge, so it must track the flags.
describe("egressPolicy", () => {
  test("no analyst → offline, no hosts", () => {
    const p = egressPolicy({});
    expect(p.policy).toBe("offline");
    expect(p.hosts).toEqual([]);
    expect(p.label).toContain("offline");
  });

  test("ollama → local-only, loopback host", () => {
    const p = egressPolicy({ SMOLDUCK_LLM_PROVIDER: "ollama" });
    expect(p.policy).toBe("local-only");
    expect(p.hosts).toEqual(["127.0.0.1"]);
  });

  test("anthropic key → allow-host api.anthropic.com", () => {
    const p = egressPolicy({ ANTHROPIC_API_KEY: "sk-ant" });
    expect(p.policy).toBe("allow-host");
    expect(p.hosts).toEqual(["api.anthropic.com"]);
    expect(p.label).toContain("api.anthropic.com");
  });
});
