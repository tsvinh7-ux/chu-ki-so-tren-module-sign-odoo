package main

import (
	"bytes"
	"crypto"
	"crypto/ecdsa"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
)

const testOrigin = "https://dmagroup.vn"

type fakeStore struct {
	certs   []CertInfo
	signErr error
	signed  int
}

func (f *fakeStore) List() ([]CertInfo, error) { return f.certs, nil }
func (f *fakeStore) Sign(id string, digest []byte) ([]byte, string, error) {
	if f.signErr != nil {
		return nil, "", f.signErr
	}
	f.signed++
	return []byte("sig:" + id), "rsa", nil
}

type fakePrompter struct {
	mu          sync.Mutex
	interactive bool
	allowOrigin bool
	allowSign   bool
	askedOrigin []string
	askedSign   []string
}

func (p *fakePrompter) Interactive() bool { return p.interactive }
func (p *fakePrompter) AskOrigin(o string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.askedOrigin = append(p.askedOrigin, o)
	return p.allowOrigin
}
func (p *fakePrompter) ConfirmSign(origin, doc, cn string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.askedSign = append(p.askedSign, origin+"|"+doc+"|"+cn)
	return p.allowSign
}

func newTestServer(t *testing.T, store Store, pr Prompter) (*Server, string) {
	t.Helper()
	cfg := filepath.Join(t.TempDir(), "DMAGKySo", "config.json")
	pol, err := NewOriginPolicy(strings.Split(defaultOrigins, ","), nil, cfg)
	if err != nil {
		t.Fatal(err)
	}
	return &Server{Store: store, Backend: "fake", Policy: pol, Prompter: pr, Port: defaultPort}, cfg
}

func do(t *testing.T, s *Server, method, path, origin string, body any, hdr map[string]string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	var rd *bytes.Reader
	switch b := body.(type) {
	case nil:
		rd = bytes.NewReader(nil)
	case []byte:
		rd = bytes.NewReader(b)
	default:
		data, _ := json.Marshal(b)
		rd = bytes.NewReader(data)
	}
	req := httptest.NewRequest(method, path, rd)
	req.Host = "127.0.0.1:" + strconv.Itoa(defaultPort)
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	var out map[string]any
	if rec.Body.Len() > 0 {
		_ = json.Unmarshal(rec.Body.Bytes(), &out)
	}
	return rec, out
}

func sampleCert() CertInfo {
	return CertInfo{ID: "ABCDEF0123456789ABCDEF0123456789ABCDEF01", CommonName: "Nguyễn Văn A", KeyType: "rsa"}
}

func digestB64() string {
	d := sha256.Sum256([]byte("hello"))
	return base64.StdEncoding.EncodeToString(d[:])
}

func TestPreflightAllowedOrigin(t *testing.T) {
	s, _ := newTestServer(t, &fakeStore{}, &fakePrompter{})
	for _, path := range []string{"/v1/info", "/v1/certificates", "/v1/sign"} {
		rec, _ := do(t, s, http.MethodOptions, path, testOrigin, nil, map[string]string{
			"Access-Control-Request-Method":          "POST",
			"Access-Control-Request-Headers":         "content-type",
			"Access-Control-Request-Private-Network": "true",
		})
		if rec.Code != http.StatusNoContent {
			t.Fatalf("%s: status %d", path, rec.Code)
		}
		h := rec.Header()
		want := map[string]string{
			"Access-Control-Allow-Origin":          testOrigin,
			"Vary":                                 "Origin",
			"Access-Control-Allow-Methods":         "GET, POST, OPTIONS",
			"Access-Control-Allow-Headers":         "Content-Type",
			"Access-Control-Allow-Private-Network": "true",
			"Access-Control-Max-Age":               "600",
		}
		for k, v := range want {
			if got := h.Get(k); got != v {
				t.Errorf("%s: header %s = %q, want %q", path, k, got, v)
			}
		}
	}
}

func TestPreflightUnknownOriginRejected(t *testing.T) {
	s, _ := newTestServer(t, &fakeStore{}, &fakePrompter{interactive: false})
	rec, out := do(t, s, http.MethodOptions, "/v1/sign", "https://evil.example", nil, nil)
	if rec.Code != http.StatusForbidden || out["error"] != codeOriginNotAllowed {
		t.Fatalf("got %d %v", rec.Code, out)
	}
	if rec.Header().Get("Access-Control-Allow-Private-Network") != "" {
		t.Fatal("PNA header must not be sent to unknown origins")
	}
}

func TestPreflightUnknownOriginInteractive(t *testing.T) {
	// On Windows the real request shows an approval dialog, so the
	// preflight must let it through (the request itself is re-checked).
	s, _ := newTestServer(t, &fakeStore{}, &fakePrompter{interactive: true})
	rec, _ := do(t, s, http.MethodOptions, "/v1/certificates", "https://clinic.example", nil, nil)
	if rec.Code != http.StatusNoContent || rec.Header().Get("Access-Control-Allow-Private-Network") != "true" {
		t.Fatalf("got %d %v", rec.Code, rec.Header())
	}
	// ...but not for /v1/info, nor for malformed origins.
	rec, _ = do(t, s, http.MethodOptions, "/v1/info", "https://clinic.example", nil, nil)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("info preflight: %d", rec.Code)
	}
	rec, _ = do(t, s, http.MethodOptions, "/v1/sign", "null", nil, nil)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("null origin preflight: %d", rec.Code)
	}
}

func TestInfoWithoutOrigin(t *testing.T) {
	s, _ := newTestServer(t, &fakeStore{}, &fakePrompter{})
	rec, out := do(t, s, http.MethodGet, "/v1/info", "", nil, nil)
	if rec.Code != 200 {
		t.Fatalf("status %d", rec.Code)
	}
	if out["name"] != "DMAG Ky so" || out["version"] != version || out["backend"] != "fake" || out["os"] == "" {
		t.Fatalf("bad info %v", out)
	}
	if rec.Header().Get("Access-Control-Allow-Origin") != "" {
		t.Fatal("no ACAO expected without Origin")
	}
	rec, _ = do(t, s, http.MethodGet, "/v1/info", testOrigin, nil, nil)
	if rec.Code != 200 || rec.Header().Get("Access-Control-Allow-Origin") != testOrigin {
		t.Fatalf("allowed origin: %d %v", rec.Code, rec.Header())
	}
	rec, out = do(t, s, http.MethodGet, "/v1/info", "https://evil.example", nil, nil)
	if rec.Code != 403 || out["error"] != codeOriginNotAllowed {
		t.Fatalf("unknown origin: %d %v", rec.Code, out)
	}
}

func TestOriginRequired(t *testing.T) {
	st := &fakeStore{certs: []CertInfo{sampleCert()}}
	s, _ := newTestServer(t, st, &fakePrompter{allowSign: true})
	signBody := map[string]string{"id": sampleCert().ID, "hash": digestB64(), "hash_alg": "sha256", "document": "a.pdf"}
	cases := []struct{ method, path, origin string }{
		{http.MethodGet, "/v1/certificates", ""},
		{http.MethodGet, "/v1/certificates", "https://evil.example"},
		{http.MethodGet, "/v1/certificates", "null"},
		{http.MethodGet, "/v1/certificates", "https://dmagroup.vn.evil.example"},
		{http.MethodGet, "/v1/certificates", "http://dmagroup.vn"},
		{http.MethodPost, "/v1/sign", ""},
		{http.MethodPost, "/v1/sign", "https://evil.example"},
	}
	for _, c := range cases {
		var body any
		if c.method == http.MethodPost {
			body = signBody
		}
		rec, out := do(t, s, c.method, c.path, c.origin, body, nil)
		if rec.Code != 403 || out["error"] != codeOriginNotAllowed || out["message"] == "" {
			t.Errorf("%s %s origin=%q: %d %v", c.method, c.path, c.origin, rec.Code, out)
		}
	}
	if st.signed != 0 {
		t.Fatal("store must not sign for rejected origins")
	}
}

func TestBadHostRejected(t *testing.T) {
	s, _ := newTestServer(t, &fakeStore{}, &fakePrompter{})
	req := httptest.NewRequest(http.MethodGet, "/v1/info", nil)
	req.Host = "attacker.example:19876" // DNS rebinding
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	if rec.Code != 403 {
		t.Fatalf("status %d", rec.Code)
	}
	req.Host = "localhost:19876"
	rec = httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("localhost: status %d", rec.Code)
	}
}

func TestCertificatesList(t *testing.T) {
	c := sampleCert()
	s, _ := newTestServer(t, &fakeStore{certs: []CertInfo{c}}, &fakePrompter{})
	rec, out := do(t, s, http.MethodGet, "/v1/certificates", "https://www.dmagroup.vn", nil, nil)
	if rec.Code != 200 {
		t.Fatalf("status %d", rec.Code)
	}
	list := out["certificates"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["id"] != c.ID {
		t.Fatalf("bad list %v", out)
	}
	if ch, ok := list[0].(map[string]any)["chain"].([]any); !ok || len(ch) != 0 {
		t.Fatalf("chain must be an empty list, got %v", list[0].(map[string]any)["chain"])
	}
	// Empty store -> empty list, not null.
	s2, _ := newTestServer(t, &fakeStore{}, &fakePrompter{})
	rec, _ = do(t, s2, http.MethodGet, "/v1/certificates", testOrigin, nil, nil)
	if !strings.Contains(rec.Body.String(), `"certificates":[]`) {
		t.Fatalf("got %s", rec.Body.String())
	}
}

func TestSignValidation(t *testing.T) {
	c := sampleCert()
	st := &fakeStore{certs: []CertInfo{c}}
	s, _ := newTestServer(t, st, &fakePrompter{allowSign: true})
	short := base64.StdEncoding.EncodeToString(make([]byte, 20))
	cases := []struct {
		name   string
		body   any
		status int
		code   string
	}{
		{"bad json", []byte("{not json"), 400, codeBadRequest},
		{"short digest", map[string]string{"id": c.ID, "hash": short, "hash_alg": "sha256"}, 400, codeBadRequest},
		{"not base64", map[string]string{"id": c.ID, "hash": "!!!", "hash_alg": "sha256"}, 400, codeBadRequest},
		{"sha1", map[string]string{"id": c.ID, "hash": digestB64(), "hash_alg": "sha1"}, 400, codeUnsupported},
		{"missing alg", map[string]string{"id": c.ID, "hash": digestB64()}, 400, codeUnsupported},
		{"no id", map[string]string{"hash": digestB64(), "hash_alg": "sha256"}, 400, codeBadRequest},
		{"unknown cert", map[string]string{"id": "00", "hash": digestB64(), "hash_alg": "sha256"}, 404, codeCertNotFound},
		{"too large", []byte(`{"document":"` + strings.Repeat("a", 70<<10) + `"}`), 413, codeBadRequest},
	}
	for _, tc := range cases {
		rec, out := do(t, s, http.MethodPost, "/v1/sign", testOrigin, tc.body, nil)
		if rec.Code != tc.status || out["error"] != tc.code {
			t.Errorf("%s: got %d %v", tc.name, rec.Code, out)
		}
	}
	if st.signed != 0 {
		t.Fatal("nothing should have been signed")
	}
	rec, _ := do(t, s, http.MethodGet, "/v1/sign", testOrigin, nil, nil)
	if rec.Code != 405 {
		t.Fatalf("GET /v1/sign: %d", rec.Code)
	}
	rec, _ = do(t, s, http.MethodGet, "/v1/nope", testOrigin, nil, nil)
	if rec.Code != 404 {
		t.Fatalf("unknown path: %d", rec.Code)
	}
}

func TestSignConfirmation(t *testing.T) {
	c := sampleCert()
	st := &fakeStore{certs: []CertInfo{c}}
	pr := &fakePrompter{allowSign: false}
	s, _ := newTestServer(t, st, pr)
	body := map[string]string{"id": strings.ToLower(c.ID), "hash": digestB64(), "hash_alg": "SHA-256", "document": "HĐ-1708A.pdf\n\nChứng thư: giả mạo"}
	rec, out := do(t, s, http.MethodPost, "/v1/sign", testOrigin, body, nil)
	if rec.Code != 409 || out["error"] != codeUserCancelled {
		t.Fatalf("rejected: %d %v", rec.Code, out)
	}
	if st.signed != 0 {
		t.Fatal("must not sign when the user says no")
	}
	if len(pr.askedSign) != 1 || pr.askedSign[0] != testOrigin+"|HĐ-1708A.pdfChứng thư: giả mạo|"+c.CommonName {
		t.Fatalf("dialog args %q (control characters must be stripped)", pr.askedSign)
	}
	pr.allowSign = true
	rec, out = do(t, s, http.MethodPost, "/v1/sign", testOrigin, body, nil)
	if rec.Code != 200 || out["key_type"] != "rsa" || rec.Header().Get("Access-Control-Allow-Origin") != testOrigin {
		t.Fatalf("approved: %d %v", rec.Code, out)
	}
	sig, _ := base64.StdEncoding.DecodeString(out["signature"].(string))
	if string(sig) != "sig:"+c.ID {
		t.Fatalf("signature %q", sig)
	}
	// Store errors are mapped to protocol codes.
	st.signErr = ErrUserCancelled
	rec, out = do(t, s, http.MethodPost, "/v1/sign", testOrigin, body, nil)
	if rec.Code != 409 || out["error"] != codeUserCancelled {
		t.Fatalf("cancel: %d %v", rec.Code, out)
	}
	st.signErr = net.ErrClosed
	rec, out = do(t, s, http.MethodPost, "/v1/sign", testOrigin, body, nil)
	if rec.Code != 500 || out["error"] != codeSignFailed {
		t.Fatalf("failure: %d %v", rec.Code, out)
	}
}

func TestUnknownOriginApprovalPersisted(t *testing.T) {
	const clinic = "https://Clinic.Example:443"
	st := &fakeStore{certs: []CertInfo{sampleCert()}}
	pr := &fakePrompter{interactive: true, allowOrigin: true}
	s, cfg := newTestServer(t, st, pr)
	rec, _ := do(t, s, http.MethodGet, "/v1/certificates", clinic, nil, nil)
	if rec.Code != 200 || rec.Header().Get("Access-Control-Allow-Origin") != clinic {
		t.Fatalf("approved origin: %d %v", rec.Code, rec.Header())
	}
	rec, _ = do(t, s, http.MethodGet, "/v1/certificates", "https://clinic.example", nil, nil)
	if rec.Code != 200 || len(pr.askedOrigin) != 1 || pr.askedOrigin[0] != "https://clinic.example" {
		t.Fatalf("second call must not prompt: %d %v", rec.Code, pr.askedOrigin)
	}
	// Persisted: a fresh policy reading the same file allows it.
	pol, err := NewOriginPolicy(nil, nil, cfg)
	if err != nil || !pol.Allowed("https://clinic.example") {
		t.Fatalf("not persisted: %v", err)
	}
}

func TestUnknownOriginDenied(t *testing.T) {
	st := &fakeStore{certs: []CertInfo{sampleCert()}}
	pr := &fakePrompter{interactive: true, allowOrigin: false, allowSign: true}
	s, cfg := newTestServer(t, st, pr)
	body := map[string]string{"id": sampleCert().ID, "hash": digestB64(), "hash_alg": "sha256"}
	for i := 0; i < 3; i++ {
		rec, out := do(t, s, http.MethodPost, "/v1/sign", "https://evil.example", body, nil)
		if rec.Code != 403 || out["error"] != codeOriginNotAllowed {
			t.Fatalf("denied: %d %v", rec.Code, out)
		}
	}
	if len(pr.askedOrigin) != 1 {
		t.Fatalf("a refused origin must not re-prompt immediately, asked %d times", len(pr.askedOrigin))
	}
	if st.signed != 0 || len(pr.askedSign) != 0 {
		t.Fatal("must not reach signing")
	}
	if pol, _ := NewOriginPolicy(nil, nil, cfg); pol.Allowed("https://evil.example") {
		t.Fatal("denied origin persisted")
	}
}

func TestShutdownEndpoint(t *testing.T) {
	s, _ := newTestServer(t, &fakeStore{}, &fakePrompter{})
	called := make(chan struct{}, 1)
	s.Shutdown = func() { called <- struct{}{} }
	rec, _ := do(t, s, http.MethodPost, "/v1/shutdown", testOrigin, nil, map[string]string{"X-DMAG-Shutdown": "1"})
	if rec.Code != 403 {
		t.Fatalf("browser (Origin present) must not shut down the agent: %d", rec.Code)
	}
	rec, _ = do(t, s, http.MethodPost, "/v1/shutdown", "", nil, nil)
	if rec.Code != 403 {
		t.Fatalf("missing header: %d", rec.Code)
	}
	rec, _ = do(t, s, http.MethodPost, "/v1/shutdown", "", nil, map[string]string{"X-DMAG-Shutdown": "1"})
	if rec.Code != 200 {
		t.Fatalf("local shutdown: %d", rec.Code)
	}
	<-called
}

// TestPEMSignRoundTrip runs the real HTTP server with the PEM backend and
// verifies RSA and ECDSA signatures with crypto/rsa and crypto/ecdsa.
func TestPEMSignRoundTrip(t *testing.T) {
	for _, kt := range []string{"rsa", "ec"} {
		t.Run(kt, func(t *testing.T) {
			leaf, caDER := makeTestCert(t, kt)
			store, err := NewPEMStore(leaf.certPEM, leaf.keyPEM)
			if err != nil {
				t.Fatal(err)
			}
			s, _ := newTestServer(t, store, &fakePrompter{allowSign: true})
			ts := httptest.NewServer(s)
			defer ts.Close()
			_, portStr, _ := net.SplitHostPort(ts.Listener.Addr().String())
			s.Port, _ = strconv.Atoi(portStr)

			get := func(path string, v any) {
				req, _ := http.NewRequest(http.MethodGet, ts.URL+path, nil)
				req.Header.Set("Origin", "http://localhost:8069")
				resp, err := http.DefaultClient.Do(req)
				if err != nil {
					t.Fatal(err)
				}
				defer resp.Body.Close()
				if resp.StatusCode != 200 {
					t.Fatalf("%s: %d", path, resp.StatusCode)
				}
				json.NewDecoder(resp.Body).Decode(v)
			}
			var list struct{ Certificates []CertInfo }
			get("/v1/certificates", &list)
			if len(list.Certificates) != 1 {
				t.Fatalf("certs: %+v", list)
			}
			ci := list.Certificates[0]
			if ci.KeyType != kt || ci.CommonName != "Phòng khám Đa khoa Ánh Dương" || ci.Expired || ci.Serial != "54010203AB" {
				t.Fatalf("cert info: %+v", ci)
			}
			if !strings.Contains(ci.Subject, "UID=MST:0101234567") || !strings.Contains(ci.Subject, `O=Công ty TNHH Ánh Dương\, chi nhánh 1`) || !strings.Contains(ci.Subject, "CN=Phòng khám Đa khoa Ánh Dương") || !strings.HasSuffix(ci.Subject, "C=VN") {
				t.Fatalf("subject: %s", ci.Subject)
			}
			if !strings.Contains(ci.Issuer, "CN=Test CA") {
				t.Fatalf("issuer: %s", ci.Issuer)
			}
			if len(ci.Chain) != 1 || ci.Chain[0] != base64.StdEncoding.EncodeToString(caDER) {
				t.Fatalf("chain: %v", ci.Chain)
			}
			der, _ := base64.StdEncoding.DecodeString(ci.DER)
			cert, err := x509.ParseCertificate(der)
			if err != nil {
				t.Fatal(err)
			}

			digest := sha256.Sum256([]byte("Hợp đồng HD-1708A"))
			body, _ := json.Marshal(signRequest{ID: ci.ID, Hash: base64.StdEncoding.EncodeToString(digest[:]), HashAlg: "sha256", Document: "HD-1708A.pdf"})
			req, _ := http.NewRequest(http.MethodPost, ts.URL+"/v1/sign", bytes.NewReader(body))
			req.Header.Set("Origin", "http://localhost:8069")
			req.Header.Set("Content-Type", "application/json")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			var sr signResponse
			json.NewDecoder(resp.Body).Decode(&sr)
			if resp.StatusCode != 200 || sr.KeyType != kt {
				t.Fatalf("sign: %d %+v", resp.StatusCode, sr)
			}
			sig, err := base64.StdEncoding.DecodeString(sr.Signature)
			if err != nil {
				t.Fatal(err)
			}
			switch pub := cert.PublicKey.(type) {
			case *rsa.PublicKey:
				if err := rsa.VerifyPKCS1v15(pub, crypto.SHA256, digest[:], sig); err != nil {
					t.Fatalf("RSA verify: %v", err)
				}
				// Also verifiable as a regular X.509 signature over the message.
				if err := cert.CheckSignature(x509.SHA256WithRSA, []byte("Hợp đồng HD-1708A"), sig); err != nil {
					t.Fatalf("CheckSignature: %v", err)
				}
			case *ecdsa.PublicKey:
				if !ecdsa.VerifyASN1(pub, digest[:], sig) {
					t.Fatal("ECDSA verify failed")
				}
			}
		})
	}
}

func TestPEMStoreCancelAndMismatch(t *testing.T) {
	leaf, _ := makeTestCert(t, "ec")
	store, err := NewPEMStore(leaf.certPEM, leaf.keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("DMAG_AGENT_PEM_CANCEL", "1")
	id := thumbprint(leaf.der)
	if _, _, err := store.Sign(id, make([]byte, 32)); asAgentError(err).Code != codeUserCancelled {
		t.Fatalf("want user_cancelled, got %v", err)
	}
	if _, _, err := store.Sign("FFFF", make([]byte, 32)); asAgentError(err).Code != codeCertNotFound {
		t.Fatalf("want cert_not_found, got %v", err)
	}
	other, _ := makeTestCert(t, "ec")
	if _, err := NewPEMStore(leaf.certPEM, other.keyPEM); err == nil {
		t.Fatal("mismatched key must be rejected")
	}
}
