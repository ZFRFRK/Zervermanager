# zervermanager-go — build & verification
# Targets: linux/amd64 (prod) + linux/arm64 (RPi); tests run on any OS.
#
# `make build` regenerates the COMMITTED prebuilts in bin/ — after a
# meaningful change, rebuild and commit bin/ so users always get a
# working clone-and-install experience without a Go toolchain.

GO ?= go
BIN := bin
LDFLAGS := -s -w

.PHONY: build build-amd64 build-arm64 test vet fmt lint clean

build: build-amd64 build-arm64

build-amd64:
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 $(GO) build -trimpath -ldflags "$(LDFLAGS)" \
		-o $(BIN)/zervermanager-linux-amd64 ./cmd/zervermanager

build-arm64:
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 $(GO) build -trimpath -ldflags "$(LDFLAGS)" \
		-o $(BIN)/zervermanager-linux-arm64 ./cmd/zervermanager

# Host build — quick dev loop on the dev box (not committed).
dev:
	$(GO) build -o $(BIN)/zervermanager ./cmd/zervermanager

test: fmt vet
	$(GO) test ./...

vet:
	$(GO) vet ./...

# gofmt must be clean: formatting is enforced, not negotiated.
fmt:
	@out=$$($(GO) fmt ./...); if [ -n "$$out" ]; then echo "$$out"; exit 1; fi

lint: test
	@which golangci-lint >/dev/null 2>&1 && golangci-lint run || echo "golangci-lint not installed — skipped (dev box)"

clean:
	rm -rf $(BIN)/zervermanager $(BIN)/*.tar.gz
