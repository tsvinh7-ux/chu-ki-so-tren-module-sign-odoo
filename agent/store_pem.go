package main

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"time"
)

// PEMStore is a single-certificate "token" backed by PEM files. It is used
// on non-Windows systems, mainly for automated end-to-end tests.
type PEMStore struct {
	der   []byte
	chain [][]byte
	key   crypto.Signer
	meta  *certMeta
}

// NewPEMStoreFromEnv loads DMAG_AGENT_PEM_CERT / DMAG_AGENT_PEM_KEY. If they
// are not set it returns an empty store.
func NewPEMStoreFromEnv() (*PEMStore, error) {
	certPath, keyPath := os.Getenv("DMAG_AGENT_PEM_CERT"), os.Getenv("DMAG_AGENT_PEM_KEY")
	if certPath == "" && keyPath == "" {
		return &PEMStore{}, nil
	}
	if certPath == "" || keyPath == "" {
		return &PEMStore{}, errors.New("cần đặt cả DMAG_AGENT_PEM_CERT và DMAG_AGENT_PEM_KEY")
	}
	certPEM, err := os.ReadFile(certPath)
	if err != nil {
		return &PEMStore{}, err
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return &PEMStore{}, err
	}
	return NewPEMStore(certPEM, keyPEM)
}

// NewPEMStore builds a store from PEM data. The first CERTIFICATE block is
// the signing certificate; further blocks are returned as its chain.
func NewPEMStore(certPEM, keyPEM []byte) (*PEMStore, error) {
	s := &PEMStore{}
	for rest := certPEM; ; {
		var b *pem.Block
		b, rest = pem.Decode(rest)
		if b == nil {
			break
		}
		if b.Type != "CERTIFICATE" {
			continue
		}
		if s.der == nil {
			s.der = b.Bytes
		} else {
			s.chain = append(s.chain, b.Bytes)
		}
	}
	if s.der == nil {
		return nil, errors.New("không tìm thấy CERTIFICATE trong tệp PEM")
	}
	meta, err := parseCertMeta(s.der)
	if err != nil {
		return nil, err
	}
	s.meta = meta

	key, err := parsePrivateKeyPEM(keyPEM)
	if err != nil {
		return nil, err
	}
	type equaler interface{ Equal(crypto.PublicKey) bool }
	if eq, ok := key.Public().(equaler); !ok || !eq.Equal(meta.PublicKey) {
		return nil, errors.New("khóa riêng không khớp với chứng thư số")
	}
	s.key = key
	return s, nil
}

func parsePrivateKeyPEM(data []byte) (crypto.Signer, error) {
	for rest := data; ; {
		var b *pem.Block
		b, rest = pem.Decode(rest)
		if b == nil {
			return nil, errors.New("không tìm thấy khóa riêng trong tệp PEM")
		}
		switch b.Type {
		case "PRIVATE KEY":
			k, err := x509.ParsePKCS8PrivateKey(b.Bytes)
			if err != nil {
				return nil, err
			}
			signer, ok := k.(crypto.Signer)
			if !ok {
				return nil, fmt.Errorf("loại khóa %T không được hỗ trợ", k)
			}
			return signer, nil
		case "RSA PRIVATE KEY":
			return x509.ParsePKCS1PrivateKey(b.Bytes)
		case "EC PRIVATE KEY":
			return x509.ParseECPrivateKey(b.Bytes)
		}
	}
}

// List implements Store.
func (s *PEMStore) List() ([]CertInfo, error) {
	if s.der == nil {
		return []CertInfo{}, nil
	}
	info, _, err := buildCertInfo(s.der, s.chain, time.Now())
	if err != nil {
		return nil, err
	}
	return []CertInfo{info}, nil
}

// Sign implements Store.
func (s *PEMStore) Sign(id string, digest []byte) ([]byte, string, error) {
	if s.der == nil || normalizeThumbprint(id) != s.meta.Thumbprint {
		return nil, "", ErrCertNotFound
	}
	if len(digest) != 32 {
		return nil, "", &AgentError{Code: codeBadRequest, Message: "Giá trị băm phải dài 32 byte."}
	}
	if os.Getenv("DMAG_AGENT_PEM_CANCEL") == "1" {
		// Simulates a user cancelling the token PIN dialog (tests).
		return nil, "", ErrUserCancelled
	}
	switch k := s.key.(type) {
	case *rsa.PrivateKey:
		sig, err := rsa.SignPKCS1v15(rand.Reader, k, crypto.SHA256, digest)
		return sig, "rsa", err
	case *ecdsa.PrivateKey:
		sig, err := ecdsa.SignASN1(rand.Reader, k, digest)
		return sig, "ec", err
	default:
		return nil, "", &AgentError{Code: codeUnsupported, Message: msgUnsupportedKey}
	}
}
