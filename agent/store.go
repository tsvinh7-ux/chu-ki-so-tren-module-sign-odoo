package main

import (
	"errors"
	"fmt"
)

// CertInfo is the JSON representation of one signing certificate.
type CertInfo struct {
	ID         string   `json:"id"`
	Subject    string   `json:"subject"`
	CommonName string   `json:"common_name"`
	Issuer     string   `json:"issuer"`
	Serial     string   `json:"serial"`
	NotBefore  string   `json:"not_before"`
	NotAfter   string   `json:"not_after"`
	Expired    bool     `json:"expired"`
	KeyType    string   `json:"key_type"`
	DER        string   `json:"der"`
	Chain      []string `json:"chain"`
}

// Store is a source of certificates with private keys (a Windows
// certificate store, a PEM file pair, ...).
type Store interface {
	// List returns the certificates that have an associated private key.
	// It must never touch the private key (no PIN prompt).
	List() ([]CertInfo, error)
	// Sign signs a 32-byte SHA-256 digest with the private key of the
	// certificate whose SHA-1 thumbprint (uppercase hex) is id.
	// RSA: RSASSA-PKCS1-v1_5 over DigestInfo(SHA-256, digest).
	// EC: ASN.1 DER encoded ECDSA signature.
	Sign(id string, digest []byte) (sig []byte, keyType string, err error)
}

// Error codes of the HTTP protocol.
const (
	codeOriginNotAllowed = "origin_not_allowed"
	codeUserCancelled    = "user_cancelled"
	codeCertNotFound     = "cert_not_found"
	codeBadRequest       = "bad_request"
	codeSignFailed       = "sign_failed"
	codeUnsupported      = "unsupported"
)

// AgentError carries a protocol error code and a Vietnamese message for the
// end user. Stores return it to give precise feedback (wrong PIN, token
// unplugged, ...).
type AgentError struct {
	Code    string
	Message string
	Err     error // underlying technical error, logged only
}

func (e *AgentError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("%s: %s (%v)", e.Code, e.Message, e.Err)
	}
	return e.Code + ": " + e.Message
}

func (e *AgentError) Unwrap() error { return e.Err }

var (
	// ErrCertNotFound is returned when no certificate matches the id.
	ErrCertNotFound = &AgentError{Code: codeCertNotFound, Message: msgCertNotFound}
	// ErrUserCancelled is returned when the user cancels (PIN dialog, confirmation).
	ErrUserCancelled = &AgentError{Code: codeUserCancelled, Message: msgUserCancelled}
)

const (
	msgOriginNotAllowed = "Trang web này chưa được phép sử dụng phần mềm DMAG Ký số."
	msgUserCancelled    = "Bạn đã hủy thao tác ký số."
	msgCertNotFound     = "Không tìm thấy chứng thư số. Hãy kiểm tra USB token đã được cắm vào máy."
	msgUnsupportedAlg   = "Chỉ hỗ trợ thuật toán băm SHA-256."
	msgSignFailed       = "Ký số không thành công."
	msgUnsupportedKey   = "Loại khóa của chứng thư số này không được hỗ trợ (chỉ hỗ trợ RSA và ECDSA)."
)

// asAgentError converts any error into an AgentError (sign_failed by default).
func asAgentError(err error) *AgentError {
	var ae *AgentError
	if errors.As(err, &ae) {
		return ae
	}
	return &AgentError{Code: codeSignFailed, Message: msgSignFailed, Err: err}
}
