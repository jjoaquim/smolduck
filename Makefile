# smolduck — one-command install for the supported platforms
# (Apple Silicon macOS 11+, or Linux with KVM). See README → Requirements.
#
#   make install     bootstrap deps + put `smolduck` on PATH + bake the image
#   make check        verify platform + prerequisites (no installs)
#   make build        (re)bake the microVM image only
#   make mcp-setup    prepare the MCP server + print client config
#   make uninstall / make clean

SHELL   := /bin/bash
PREFIX  ?= $(HOME)/.local
BINDIR  := $(PREFIX)/bin
REPO    := $(CURDIR)
WRAPPER := $(BINDIR)/smolduck

# Make tools installed by `deps` visible within this same run (bun → ~/.bun/bin,
# uv/smolvm → ~/.local/bin). We can't persist PATH into your shell rc — see the hint
# printed by `link`/`install`.
export PATH := $(HOME)/.bun/bin:$(HOME)/.local/bin:$(PATH)

.DEFAULT_GOAL := help
.PHONY: help install check platform deps link build mcp-setup uninstall clean

help:
	@echo "smolduck — make targets:"
	@echo "  make install    deps + install the \`smolduck\` command + bake the image"
	@echo "  make check      verify platform + prerequisites (bun, uv, smolvm)"
	@echo "  make build      (re)bake the microVM image"
	@echo "  make mcp-setup  prepare the MCP server + print client config"
	@echo "  make uninstall  remove the \`smolduck\` command"
	@echo "  make clean      remove the packed image artifacts"

install: platform deps link build
	@echo ""
	@echo "✓ smolduck installed → $(WRAPPER)"
	@echo "  try:  smolduck run ./examples/ecommerce"
	@case ":$$PATH:" in *":$(BINDIR):"*) ;; *) echo "  ⚠ add $(BINDIR) to your PATH:  export PATH=$(BINDIR):\$$PATH";; esac

# Refuse unsupported platforms early; warn (don't fail) on missing KVM.
platform:
	@os=$$(uname -s); arch=$$(uname -m); \
	if [ "$$os" = "Darwin" ] && { [ "$$arch" = "x86_64" ] || [ "$$arch" = "amd64" ]; }; then \
	  echo "✗ Intel Macs are not supported — smolvm ships no darwin-x86_64 build."; \
	  echo "  Use an Apple Silicon Mac or Linux with KVM (see README → Requirements)."; \
	  exit 1; \
	fi; \
	if [ "$$os" = "Linux" ] && [ ! -e /dev/kvm ]; then \
	  echo "⚠ /dev/kvm not found — smolvm needs KVM on Linux; 'smolduck run' may fail."; \
	fi

# Verify-only: report each tool + version; non-zero exit if any is missing.
check: platform
	@missing=0; \
	for t in bun uv smolvm; do \
	  if command -v $$t >/dev/null 2>&1; then \
	    echo "✓ $$t  $$($$t --version 2>/dev/null | head -1)"; \
	  else \
	    echo "✗ $$t  (missing)"; missing=1; \
	  fi; \
	done; \
	if [ $$missing -ne 0 ]; then \
	  echo "  run 'make deps' (or 'make install') to install the missing tool(s)."; \
	  exit 1; \
	fi; \
	echo "✓ platform + prerequisites OK"

# Install only the tools that are missing, each via its official installer.
deps:
	@command -v bun    >/dev/null 2>&1 || { echo "→ installing bun…";    curl -fsSL https://bun.sh/install | bash; }
	@command -v uv     >/dev/null 2>&1 || { echo "→ installing uv…";     curl -LsSf https://astral.sh/uv/install.sh | sh; }
	@command -v smolvm >/dev/null 2>&1 || { echo "→ installing smolvm…"; curl -sSL https://smolmachines.com/install.sh | bash; }
	@echo "✓ dependencies present"

# Put a tiny `smolduck` launcher on PATH (no build; the CLI runs straight from source).
link:
	@mkdir -p $(BINDIR)
	@printf '#!/bin/sh\nexec bun "%s/cli/src/index.ts" "$$@"\n' '$(REPO)' > $(WRAPPER)
	@chmod +x $(WRAPPER)
	@echo "✓ installed $(WRAPPER)"
	@case ":$$PATH:" in *":$(BINDIR):"*) ;; *) echo "  ⚠ $(BINDIR) is not on your PATH:  export PATH=$(BINDIR):\$$PATH";; esac

# Bake the microVM image (builder VM → provision.sh → pack). Needs smolvm + network.
build:
	@bun "$(REPO)/cli/src/index.ts" build

# Optional: pre-build the MCP server venv and print the client-config snippet.
mcp-setup:
	@cd mcp && uv sync
	@echo "✓ MCP server ready. Add to your MCP client config:"
	@echo '  "smolduck": { "command": "uvx", "args": ["--from", "$(REPO)/mcp", "smolduck-mcp", "--workspace", "<your-data-dir>"] }'

uninstall:
	@rm -f $(WRAPPER) && echo "removed $(WRAPPER)"

clean:
	@rm -f image/smolduck image/smolduck.smolmachine && echo "removed packed image artifacts"
