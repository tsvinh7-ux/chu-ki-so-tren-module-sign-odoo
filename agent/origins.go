package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// defaultOrigins can be overridden at build time:
//
//	go build -ldflags "-X main.defaultOrigins=https://a.example,https://b.example"
var defaultOrigins = "https://dmagroup.vn,https://www.dmagroup.vn,http://localhost:8069"

// Config is persisted at <UserConfigDir>/DMAGKySo/config.json
// (%APPDATA%\DMAGKySo\config.json on Windows).
type Config struct {
	AllowedOrigins []string `json:"allowed_origins"`
}

// configPath returns the default config file path.
func configPath() (string, error) {
	if d := os.Getenv("DMAG_AGENT_CONFIG_DIR"); d != "" {
		return filepath.Join(d, "config.json"), nil
	}
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "DMAGKySo", "config.json"), nil
}

func loadConfig(path string) (Config, error) {
	var c Config
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return c, nil
	}
	if err != nil {
		return c, err
	}
	if err := json.Unmarshal(data, &c); err != nil {
		return Config{}, fmt.Errorf("config %s: %w", path, err)
	}
	return c, nil
}

func saveConfig(path string, c Config) error {
	if c.AllowedOrigins == nil {
		c.AllowedOrigins = []string{}
	}
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// normalizeOrigin validates a web origin (scheme://host[:port]) and returns
// its canonical lowercase form. Only http and https are accepted; paths,
// queries, user info and the opaque "null" origin are rejected.
func normalizeOrigin(o string) (string, bool) {
	o = strings.TrimSpace(o)
	o = strings.TrimSuffix(o, "/")
	if o == "" || o == "null" {
		return "", false
	}
	u, err := url.Parse(o)
	if err != nil || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" {
		return "", false
	}
	scheme := strings.ToLower(u.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", false
	}
	host := strings.ToLower(u.Hostname())
	if host == "" || strings.ContainsAny(host, " \t\r\n") {
		return "", false
	}
	port := u.Port()
	if (scheme == "http" && port == "80") || (scheme == "https" && port == "443") {
		port = ""
	}
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	if port != "" {
		return scheme + "://" + host + ":" + port, true
	}
	return scheme + "://" + host, true
}

// isInsecureRemoteOrigin reports whether an origin uses plain HTTP to a
// non-loopback host (traffic could be tampered with on the network).
func isInsecureRemoteOrigin(origin string) bool {
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "http" {
		return false
	}
	h := u.Hostname()
	if h == "localhost" {
		return false
	}
	if ip := net.ParseIP(h); ip != nil && ip.IsLoopback() {
		return false
	}
	return true
}

func splitOrigins(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if n, ok := normalizeOrigin(p); ok {
			out = append(out, n)
		}
	}
	return out
}

// OriginPolicy decides which web origins may use the agent.
type OriginPolicy struct {
	mu         sync.Mutex
	builtin    map[string]bool // build-time defaults + environment
	persisted  []string        // from config.json (user approved)
	configPath string
	denied     map[string]time.Time // origins the user refused recently
	promptMu   sync.Mutex           // one approval dialog at a time
}

const deniedTTL = 2 * time.Minute

// NewOriginPolicy builds a policy from built-in origins, extra origins
// (environment) and the persisted config file (may be empty to disable
// persistence).
func NewOriginPolicy(builtin []string, extra []string, cfgPath string) (*OriginPolicy, error) {
	p := &OriginPolicy{
		builtin:    map[string]bool{},
		configPath: cfgPath,
		denied:     map[string]time.Time{},
	}
	for _, o := range append(append([]string{}, builtin...), extra...) {
		if n, ok := normalizeOrigin(o); ok {
			p.builtin[n] = true
		}
	}
	if cfgPath == "" {
		return p, nil
	}
	c, err := loadConfig(cfgPath)
	for _, o := range c.AllowedOrigins {
		if n, ok := normalizeOrigin(o); ok {
			p.persisted = append(p.persisted, n)
		}
	}
	return p, err
}

// Allowed reports whether the (normalized) origin is allowed.
func (p *OriginPolicy) Allowed(origin string) bool {
	n, ok := normalizeOrigin(origin)
	if !ok {
		return false
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.allowedLocked(n)
}

func (p *OriginPolicy) allowedLocked(n string) bool {
	if p.builtin[n] {
		return true
	}
	for _, o := range p.persisted {
		if o == n {
			return true
		}
	}
	return false
}

// Add persists a user-approved origin.
func (p *OriginPolicy) Add(origin string) error {
	n, ok := normalizeOrigin(origin)
	if !ok {
		return fmt.Errorf("invalid origin %q", origin)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	delete(p.denied, n)
	if p.allowedLocked(n) {
		return nil
	}
	p.persisted = append(p.persisted, n)
	if p.configPath == "" {
		return nil
	}
	// Re-read the file so that concurrent edits are not lost.
	c, err := loadConfig(p.configPath)
	if err != nil {
		c = Config{}
	}
	seen := map[string]bool{}
	var merged []string
	for _, o := range append(c.AllowedOrigins, p.persisted...) {
		if m, ok := normalizeOrigin(o); ok && !seen[m] {
			seen[m] = true
			merged = append(merged, m)
		}
	}
	return saveConfig(p.configPath, Config{AllowedOrigins: merged})
}

func (p *OriginPolicy) markDenied(n string) {
	p.mu.Lock()
	p.denied[n] = time.Now()
	p.mu.Unlock()
}

func (p *OriginPolicy) recentlyDenied(n string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	t, ok := p.denied[n]
	if ok && time.Since(t) > deniedTTL {
		delete(p.denied, n)
		return false
	}
	return ok
}

// Authorize checks an origin and, if it is unknown and the prompter is
// interactive, asks the user. It returns (allowed, promptedAndDenied).
func (p *OriginPolicy) Authorize(origin string, pr Prompter) (bool, bool) {
	n, ok := normalizeOrigin(origin)
	if !ok {
		return false, false
	}
	if p.Allowed(n) {
		return true, false
	}
	if pr == nil || !pr.Interactive() {
		return false, false
	}
	p.promptMu.Lock()
	defer p.promptMu.Unlock()
	// Another request may have been approved/denied while we waited.
	if p.Allowed(n) {
		return true, false
	}
	if p.recentlyDenied(n) {
		return false, true
	}
	if !pr.AskOrigin(n) {
		p.markDenied(n)
		return false, true
	}
	if err := p.Add(n); err != nil {
		logf("không lưu được cấu hình: %v", err)
	}
	return true, false
}
