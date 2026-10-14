package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	agentName      = "DMAG Ky so"
	defaultPort    = 19876
	maxBodyBytes   = 64 << 10
	maxDocumentLen = 200
)

// version is the agent version (overridable with -ldflags "-X main.version=...").
var version = "1.0.0"

// Prompter shows dialogs to the user. On non-interactive platforms it
// auto-decides.
type Prompter interface {
	// Interactive reports whether unknown origins can be approved by the user.
	Interactive() bool
	// AskOrigin asks whether an unknown origin may use the agent.
	AskOrigin(origin string) bool
	// ConfirmSign asks the user to confirm one signature.
	ConfirmSign(origin, document, commonName string) bool
}

// Server implements the HTTP protocol.
type Server struct {
	Store    Store
	Backend  string
	Policy   *OriginPolicy
	Prompter Prompter
	Port     int
	// Shutdown, if set, is called by the internal POST /v1/shutdown endpoint
	// (used by a newer installer to replace a running older version).
	Shutdown func()

	signMu sync.Mutex
	now    func() time.Time
}

type errorBody struct {
	Error   string `json:"error"`
	Message string `json:"message"`
}

type signRequest struct {
	ID       string `json:"id"`
	Hash     string `json:"hash"`
	HashAlg  string `json:"hash_alg"`
	Document string `json:"document"`
}

type signResponse struct {
	Signature string `json:"signature"`
	KeyType   string `json:"key_type"`
}

func (s *Server) clock() time.Time {
	if s.now != nil {
		return s.now()
	}
	return time.Now()
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, msg string) {
	writeJSON(w, status, errorBody{Error: code, Message: msg})
}

func statusForCode(code string) int {
	switch code {
	case codeOriginNotAllowed:
		return http.StatusForbidden
	case codeUserCancelled:
		return http.StatusConflict
	case codeCertNotFound:
		return http.StatusNotFound
	case codeBadRequest, codeUnsupported:
		return http.StatusBadRequest
	default:
		return http.StatusInternalServerError
	}
}

// hostAllowed protects against DNS rebinding: the Host header must name the
// loopback address we listen on.
func (s *Server) hostAllowed(host string) bool {
	h, p, err := net.SplitHostPort(host)
	if err != nil {
		return false
	}
	if p != strconv.Itoa(s.Port) {
		return false
	}
	return h == "127.0.0.1" || strings.EqualFold(h, "localhost")
}

func setCORS(w http.ResponseWriter, origin string) {
	w.Header().Set("Access-Control-Allow-Origin", origin)
	w.Header().Add("Vary", "Origin")
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")

	if !s.hostAllowed(r.Host) {
		writeError(w, http.StatusForbidden, codeOriginNotAllowed, "Địa chỉ truy cập không hợp lệ.")
		return
	}

	var allowedMethod string
	switch r.URL.Path {
	case "/v1/info", "/v1/certificates":
		allowedMethod = http.MethodGet
	case "/v1/sign", "/v1/shutdown":
		allowedMethod = http.MethodPost
	default:
		writeError(w, http.StatusNotFound, codeBadRequest, "Không tìm thấy chức năng được yêu cầu.")
		return
	}

	origin := r.Header.Get("Origin")

	if r.URL.Path == "/v1/shutdown" {
		s.handleShutdown(w, r, origin)
		return
	}

	if r.Method == http.MethodOptions {
		s.handlePreflight(w, r, origin)
		return
	}
	if r.Method != allowedMethod {
		w.Header().Set("Allow", allowedMethod+", OPTIONS")
		writeError(w, http.StatusMethodNotAllowed, codeBadRequest, "Phương thức không được hỗ trợ.")
		return
	}

	norm, wellFormed := normalizeOrigin(origin)
	if origin == "" {
		if r.URL.Path != "/v1/info" {
			writeError(w, http.StatusForbidden, codeOriginNotAllowed, msgOriginNotAllowed)
			return
		}
	} else {
		var ok, denied bool
		if r.URL.Path == "/v1/info" {
			ok = s.Policy.Allowed(origin)
		} else {
			ok, denied = s.Policy.Authorize(origin, s.Prompter)
		}
		if !ok {
			// Let the page read the error so that it can explain what
			// happened; no data is disclosed.
			if wellFormed {
				setCORS(w, origin)
			}
			msg := msgOriginNotAllowed
			if denied {
				msg = "Bạn đã không cho phép trang " + norm + " sử dụng phần mềm DMAG Ký số."
			}
			logf("từ chối origin %q (%s %s)", origin, r.Method, r.URL.Path)
			writeError(w, http.StatusForbidden, codeOriginNotAllowed, msg)
			return
		}
		setCORS(w, origin)
	}

	switch r.URL.Path {
	case "/v1/info":
		s.handleInfo(w)
	case "/v1/certificates":
		s.handleCertificates(w)
	case "/v1/sign":
		s.handleSign(w, r, norm)
	}
}

func (s *Server) handlePreflight(w http.ResponseWriter, r *http.Request, origin string) {
	_, wellFormed := normalizeOrigin(origin)
	ok := s.Policy.Allowed(origin)
	if !ok && wellFormed && r.URL.Path != "/v1/info" && s.Prompter != nil && s.Prompter.Interactive() {
		// The actual request will trigger the approval dialog; it is
		// checked again there, so letting the preflight through is safe.
		ok = true
	}
	if !ok {
		writeError(w, http.StatusForbidden, codeOriginNotAllowed, msgOriginNotAllowed)
		return
	}
	h := w.Header()
	setCORS(w, origin)
	h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	h.Set("Access-Control-Allow-Headers", "Content-Type")
	h.Set("Access-Control-Allow-Private-Network", "true")
	h.Set("Access-Control-Max-Age", "600")
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleShutdown(w http.ResponseWriter, r *http.Request, origin string) {
	// Internal endpoint for the installer. Browsers always send an Origin
	// header with POST requests, so requiring its absence (plus a custom
	// header) means only local non-browser programs can call it.
	if r.Method != http.MethodPost || origin != "" || r.Header.Get("X-DMAG-Shutdown") != "1" || s.Shutdown == nil {
		writeError(w, http.StatusForbidden, codeOriginNotAllowed, msgOriginNotAllowed)
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	go func() {
		time.Sleep(200 * time.Millisecond)
		s.Shutdown()
	}()
}

func (s *Server) handleInfo(w http.ResponseWriter) {
	writeJSON(w, http.StatusOK, map[string]string{
		"name":    agentName,
		"version": version,
		"os":      runtime.GOOS,
		"backend": s.Backend,
	})
}

func (s *Server) handleCertificates(w http.ResponseWriter) {
	certs, err := s.Store.List()
	if err != nil {
		logf("liệt kê chứng thư lỗi: %v", err)
		ae := asAgentError(err)
		if ae.Code == codeSignFailed {
			ae = &AgentError{Code: codeSignFailed, Message: "Không đọc được danh sách chứng thư số."}
		}
		writeError(w, statusForCode(ae.Code), ae.Code, ae.Message)
		return
	}
	if certs == nil {
		certs = []CertInfo{}
	}
	for i := range certs {
		if certs[i].Chain == nil {
			certs[i].Chain = []string{}
		}
	}
	// Valid certificates first, then the ones expiring last.
	sort.SliceStable(certs, func(i, j int) bool {
		if certs[i].Expired != certs[j].Expired {
			return !certs[i].Expired
		}
		return certs[i].NotAfter > certs[j].NotAfter
	})
	writeJSON(w, http.StatusOK, map[string]any{"certificates": certs})
}

// sanitizeDocument makes the document name safe to show in a dialog:
// no control characters (which could fake extra dialog lines), bounded length.
func sanitizeDocument(d string) string {
	d = strings.ToValidUTF8(d, "")
	d = strings.Map(func(r rune) rune {
		if unicode.IsControl(r) || r == '‮' || r == '‭' || r == '⁦' || r == '⁧' || r == '⁨' || r == '⁩' {
			return -1
		}
		return r
	}, d)
	d = strings.TrimSpace(d)
	if utf8.RuneCountInString(d) > maxDocumentLen {
		d = string([]rune(d)[:maxDocumentLen]) + "…"
	}
	if d == "" {
		d = "(không có tên)"
	}
	return d
}

func decodeDigest(s string) ([]byte, error) {
	s = strings.TrimSpace(s)
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		if b, err := enc.DecodeString(s); err == nil {
			return b, nil
		}
	}
	return nil, errors.New("invalid base64")
}

func (s *Server) handleSign(w http.ResponseWriter, r *http.Request, origin string) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	var req signRequest
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&req); err != nil {
		var mbe *http.MaxBytesError
		if errors.As(err, &mbe) {
			writeError(w, http.StatusRequestEntityTooLarge, codeBadRequest, "Dữ liệu gửi lên quá lớn.")
			return
		}
		writeError(w, http.StatusBadRequest, codeBadRequest, "Dữ liệu gửi lên không đúng định dạng JSON.")
		return
	}
	alg := strings.ToLower(strings.ReplaceAll(strings.TrimSpace(req.HashAlg), "-", ""))
	if alg != "sha256" {
		writeError(w, http.StatusBadRequest, codeUnsupported, msgUnsupportedAlg)
		return
	}
	digest, err := decodeDigest(req.Hash)
	if err != nil || len(digest) != 32 {
		writeError(w, http.StatusBadRequest, codeBadRequest, "Giá trị băm (hash) phải là 32 byte SHA-256 mã hóa base64.")
		return
	}
	id := normalizeThumbprint(req.ID)
	if id == "" {
		writeError(w, http.StatusBadRequest, codeBadRequest, "Thiếu mã chứng thư số (id).")
		return
	}
	document := sanitizeDocument(req.Document)

	// One signature (one confirmation + PIN dialog) at a time.
	s.signMu.Lock()
	defer s.signMu.Unlock()

	certs, err := s.Store.List()
	if err != nil {
		logf("liệt kê chứng thư lỗi: %v", err)
	}
	var cert *CertInfo
	for i := range certs {
		if certs[i].ID == id {
			cert = &certs[i]
			break
		}
	}
	if cert == nil {
		writeError(w, http.StatusNotFound, codeCertNotFound, msgCertNotFound)
		return
	}
	certName := cert.CommonName
	if certName == "" {
		certName = cert.Subject
	}
	if !s.Prompter.ConfirmSign(origin, document, certName) {
		logf("người dùng từ chối ký %q cho %s", document, origin)
		writeError(w, statusForCode(codeUserCancelled), codeUserCancelled, msgUserCancelled)
		return
	}
	sig, keyType, err := s.Store.Sign(id, digest)
	if err != nil {
		ae := asAgentError(err)
		logf("ký %q cho %s bằng %s thất bại: %v", document, origin, id, err)
		writeError(w, statusForCode(ae.Code), ae.Code, ae.Message)
		return
	}
	logf("đã ký %q cho %s bằng chứng thư %s (%s)", document, origin, id, keyType)
	writeJSON(w, http.StatusOK, signResponse{
		Signature: base64.StdEncoding.EncodeToString(sig),
		KeyType:   keyType,
	})
}
