package main

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/pem"
	"flag"
	"io"
	"log"
	"math/big"
	"os"
	"testing"
	"time"
)

type testCert struct {
	der     []byte
	key     crypto.Signer
	certPEM []byte
	keyPEM  []byte
}

// makeTestCert creates a CA and a leaf certificate signed by it. The leaf
// subject contains Vietnamese text and a UID (MST) like real VN certificates.
func makeTestCert(t *testing.T, keyType string) (leaf testCert, caDER []byte) {
	t.Helper()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	caTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Test CA", Organization: []string{"DMAG Test"}, Country: []string{"VN"}},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
	}
	caDER, err = x509.CreateCertificate(rand.Reader, caTmpl, caTmpl, caKey.Public(), caKey)
	if err != nil {
		t.Fatal(err)
	}
	ca, _ := x509.ParseCertificate(caDER)

	var key crypto.Signer
	switch keyType {
	case "rsa":
		key, err = rsa.GenerateKey(rand.Reader, 2048)
	case "ec":
		key, err = ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	}
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: new(big.Int).SetBytes([]byte{0x54, 0x01, 0x02, 0x03, 0xAB}),
		Subject: pkix.Name{
			CommonName:   "Phòng khám Đa khoa Ánh Dương",
			Organization: []string{"Công ty TNHH Ánh Dương, chi nhánh 1"},
			Country:      []string{"VN"},
			ExtraNames: []pkix.AttributeTypeAndValue{
				{Type: asn1.ObjectIdentifier{0, 9, 2342, 19200300, 100, 1, 1}, Value: "MST:0101234567"},
			},
		},
		NotBefore: time.Now().Add(-time.Hour),
		NotAfter:  time.Now().Add(12 * time.Hour),
		KeyUsage:  x509.KeyUsageDigitalSignature | x509.KeyUsageContentCommitment,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, ca, key.Public(), caKey)
	if err != nil {
		t.Fatal(err)
	}
	pk8, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	leaf = testCert{
		der: der,
		key: key,
		certPEM: append(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
			pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})...),
		keyPEM: pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pk8}),
	}
	return leaf, caDER
}

func TestMain(m *testing.M) {
	flag.Parse()
	if !testing.Verbose() {
		logger = log.New(io.Discard, "", 0)
	}
	os.Exit(m.Run())
}
