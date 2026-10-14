// Command DMAGKySo ("DMAG Ký số") is a small local signing agent. A web page
// served by an allowed origin (the Odoo contract signing page) calls it on
// http://127.0.0.1:19876 to list the user's certificates and to sign a
// SHA-256 digest with the private key of one of them (USB token). The server
// builds the PAdES/CMS structure itself; the agent only returns the raw
// signature.
package main

import (
	"context"
	"errors"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

// listenPort returns the TCP port (DMAG_AGENT_PORT, default 19876). The
// address is always 127.0.0.1.
func listenPort() int {
	if p, err := strconv.Atoi(os.Getenv("DMAG_AGENT_PORT")); err == nil && p > 0 && p < 65536 {
		return p
	}
	return defaultPort
}

func run(args []string) int {
	opts, exit, code := platformStart(args)
	if exit {
		return code
	}
	port := listenPort()
	addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))

	ln, err := net.Listen("tcp", addr)
	if err != nil {
		logf("cổng %s đang bận (có thể DMAG Ký số đã chạy): %v", addr, err)
		platformAlreadyRunning(opts)
		return 0
	}

	cfg, err := configPath()
	if err != nil {
		logf("không xác định được thư mục cấu hình: %v", err)
		cfg = ""
	}
	policy, err := NewOriginPolicy(strings.Split(defaultOrigins, ","), platformExtraOrigins(), cfg)
	if err != nil {
		logf("đọc cấu hình lỗi (bỏ qua): %v", err)
	}
	store, backend, err := platformStore()
	if err != nil {
		logf("khởi tạo kho chứng thư lỗi: %v", err)
	}

	srv := &Server{
		Store:    store,
		Backend:  backend,
		Policy:   policy,
		Prompter: platformPrompter(),
		Port:     port,
	}
	httpSrv := &http.Server{
		Handler:           srv,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
		// No WriteTimeout: a signature waits for the user (dialogs, PIN).
	}
	srv.Shutdown = func() {
		logf("nhận yêu cầu dừng từ trình cài đặt")
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(ctx)
	}
	logf("%s %s đang chạy tại http://%s (backend %s)", agentName, version, addr, backend)
	if err := httpSrv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logf("máy chủ HTTP dừng: %v", err)
		return 1
	}
	return 0
}
