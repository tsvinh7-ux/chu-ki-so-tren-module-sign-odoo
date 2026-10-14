package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/asn1"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNormalizeOrigin(t *testing.T) {
	cases := map[string]string{
		"https://dmagroup.vn":        "https://dmagroup.vn",
		"https://DMAGroup.VN/":       "https://dmagroup.vn",
		"https://dmagroup.vn:443":    "https://dmagroup.vn",
		"http://localhost:8069":      "http://localhost:8069",
		"http://localhost:80":        "http://localhost",
		"http://[::1]:8069":          "http://[::1]:8069",
		"null":                       "",
		"":                           "",
		"file://":                    "",
		"chrome-extension://abc":     "",
		"https://dmagroup.vn/path":   "",
		"https://user@dmagroup.vn":   "",
		"https://dmagroup.vn?x=1":    "",
		"javascript:alert(1)":        "",
		"https://dmagroup.vn#frag":   "",
		"dmagroup.vn":                "",
		"https://dmagroup.vn\nEvil:": "",
	}
	for in, want := range cases {
		got, ok := normalizeOrigin(in)
		if got != want || ok != (want != "") {
			t.Errorf("normalizeOrigin(%q) = %q,%v want %q", in, got, ok, want)
		}
	}
}

func TestConfigPersistence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "config.json")
	p, err := NewOriginPolicy([]string{"https://dmagroup.vn"}, []string{"https://extra.example"}, path)
	if err != nil {
		t.Fatal(err)
	}
	if !p.Allowed("https://dmagroup.vn") || !p.Allowed("https://extra.example") || p.Allowed("https://other.example") {
		t.Fatal("initial policy wrong")
	}
	if err := p.Add("https://Other.Example/"); err != nil {
		t.Fatal(err)
	}
	if err := p.Add("https://other.example"); err != nil { // idempotent
		t.Fatal(err)
	}
	c, err := loadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(c.AllowedOrigins) != 1 || c.AllowedOrigins[0] != "https://other.example" {
		t.Fatalf("config %+v", c)
	}
	data, _ := os.ReadFile(path)
	if !strings.Contains(string(data), `"allowed_origins"`) {
		t.Fatalf("file content %s", data)
	}
	p2, err := NewOriginPolicy(nil, nil, path)
	if err != nil || !p2.Allowed("https://other.example") || p2.Allowed("https://dmagroup.vn") {
		t.Fatalf("reload: %v", err)
	}
	// Corrupt config: ignored with an error, policy still usable.
	os.WriteFile(path, []byte("{oops"), 0o600)
	p3, err := NewOriginPolicy([]string{"https://dmagroup.vn"}, nil, path)
	if err == nil || p3 == nil || !p3.Allowed("https://dmagroup.vn") {
		t.Fatal("corrupt config handling")
	}
}

func TestSplitOriginsEnv(t *testing.T) {
	got := splitOrigins(" https://a.example , bogus, http://localhost:8069/ ,")
	if len(got) != 2 || got[0] != "https://a.example" || got[1] != "http://localhost:8069" {
		t.Fatalf("got %v", got)
	}
	if !isInsecureRemoteOrigin("http://192.168.1.10:8069") || isInsecureRemoteOrigin("http://localhost:8069") || isInsecureRemoteOrigin("https://x.example") || isInsecureRemoteOrigin("http://127.0.0.1:8069") {
		t.Fatal("isInsecureRemoteOrigin")
	}
}

func TestDefaultOrigins(t *testing.T) {
	want := []string{"https://dmagroup.vn", "https://www.dmagroup.vn", "http://localhost:8069"}
	got := splitOrigins(defaultOrigins)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("defaultOrigins = %v", got)
	}
}

func TestECDSARawToDER(t *testing.T) {
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	d := sha256.Sum256([]byte("x"))
	r, s, err := ecdsa.Sign(rand.Reader, key, d[:])
	if err != nil {
		t.Fatal(err)
	}
	raw := make([]byte, 64)
	r.FillBytes(raw[:32])
	s.FillBytes(raw[32:])
	der, err := ecdsaRawToDER(raw)
	if err != nil {
		t.Fatal(err)
	}
	if !ecdsa.VerifyASN1(&key.PublicKey, d[:], der) {
		t.Fatal("converted signature does not verify")
	}
	// Leading zero bytes / high bit set are encoded as proper INTEGERs.
	raw2 := append(append(make([]byte, 31), 0x80), append(make([]byte, 31), 0x01)...)
	der2, _ := ecdsaRawToDER(raw2)
	var parsed struct{ R, S *big.Int }
	if _, err := asn1.Unmarshal(der2, &parsed); err != nil || parsed.R.Int64() != 0x80 || parsed.S.Int64() != 1 {
		t.Fatalf("der2 %x %v", der2, err)
	}
	if _, err := ecdsaRawToDER([]byte{1, 2, 3}); err == nil {
		t.Fatal("odd length must fail")
	}
}

func TestReverseBytes(t *testing.T) {
	if got := reverseBytes([]byte{1, 2, 3, 4}); string(got) != string([]byte{4, 3, 2, 1}) {
		t.Fatalf("got %v", got)
	}
}

func TestLenientCertParsing(t *testing.T) {
	leaf, caDER := makeTestCert(t, "rsa")
	m, err := parseCertMeta(leaf.der)
	if err != nil {
		t.Fatal(err)
	}
	if m.KeyType != "rsa" || m.SigSize != 256 || m.SelfSigned || m.CommonName != "Phòng khám Đa khoa Ánh Dương" {
		t.Fatalf("meta %+v", m)
	}
	ca, err := parseCertMeta(caDER)
	if err != nil || !ca.SelfSigned || ca.KeyType != "ec" || ca.SigSize != 64 {
		t.Fatalf("ca meta %+v %v", ca, err)
	}
	if normalizeThumbprint(" ab:cd ef ") != "ABCDEF" {
		t.Fatal("normalizeThumbprint")
	}
	if _, err := parseCertMeta([]byte{0x30, 0x00}); err == nil {
		t.Fatal("garbage must fail")
	}
}

func TestNegativeSerialAndBMPString(t *testing.T) {
	// Build a certificate by hand with a negative serial number and a
	// BMPString CN; crypto/x509 refuses such certificates by default, the
	// lenient parser must not.
	leaf, _ := makeTestCert(t, "ec")
	var c lenientCert
	asn1.Unmarshal(leaf.der, &c)
	var tbs struct {
		Raw      asn1.RawContent
		Version  asn1.RawValue `asn1:"optional,explicit,tag:0"`
		Serial   asn1.RawValue
		SigAlg   asn1.RawValue
		Issuer   asn1.RawValue
		Validity asn1.RawValue
		Subject  asn1.RawValue
		SPKI     asn1.RawValue
	}
	if _, err := asn1.Unmarshal(c.TBS.FullBytes, &tbs); err != nil {
		t.Fatal(err)
	}
	// BMPString "Bé" (UTF-16BE).
	bmp := asn1.RawValue{Class: asn1.ClassUniversal, Tag: asn1.TagBMPString, Bytes: []byte{0, 'B', 0, 0xE9}}
	type atv struct {
		Type  asn1.ObjectIdentifier
		Value asn1.RawValue
	}
	subj, _ := asn1.Marshal([]([]atv){{{Type: asn1.ObjectIdentifier{2, 5, 4, 3}, Value: bmp}}})
	// asn1.Marshal encodes the inner slice as SEQUENCE; fix it to SET (0x31).
	subj = fixInnerSet(subj)
	serial, _ := asn1.Marshal(big.NewInt(-12345))
	newTBS, err := asn1.Marshal(struct {
		Version  asn1.RawValue `asn1:"optional,explicit,tag:0"`
		Serial   asn1.RawValue
		SigAlg   asn1.RawValue
		Issuer   asn1.RawValue
		Validity asn1.RawValue
		Subject  asn1.RawValue
		SPKI     asn1.RawValue
	}{tbs.Version, asn1.RawValue{FullBytes: serial}, tbs.SigAlg, tbs.Issuer, tbs.Validity, asn1.RawValue{FullBytes: subj}, tbs.SPKI})
	if err != nil {
		t.Fatal(err)
	}
	der, err := asn1.Marshal(lenientCert{TBS: asn1.RawValue{FullBytes: newTBS}, SigAlg: c.SigAlg, Sig: c.Sig})
	if err != nil {
		t.Fatal(err)
	}
	m, err := parseCertMeta(der)
	if err != nil {
		t.Fatal(err)
	}
	if m.CommonName != "Bé" || m.Subject != "CN=Bé" || m.SerialHex != "CFC7" || m.KeyType != "ec" {
		t.Fatalf("meta %+v", m)
	}
}

// fixInnerSet turns SEQUENCE OF SEQUENCE OF X into SEQUENCE OF SET OF X for
// a single-RDN name (test helper).
func fixInnerSet(b []byte) []byte {
	out := append([]byte{}, b...)
	// outer header is 2 bytes for these short names
	if out[2] == 0x30 {
		out[2] = 0x31
	}
	return out
}

func TestRotatingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.log")
	r, err := openRotatingFile(path, 100)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 10; i++ {
		if _, err := r.Write([]byte(strings.Repeat("x", 30) + "\n")); err != nil {
			t.Fatal(err)
		}
	}
	r.Close()
	st, _ := os.Stat(path)
	if st.Size() > 100 {
		t.Fatalf("log not rotated: %d bytes", st.Size())
	}
	if _, err := os.Stat(path + ".1"); err != nil {
		t.Fatal("rotated file missing")
	}
}

func TestSanitizeDocument(t *testing.T) {
	if got := sanitizeDocument("  a\r\nb‮c\t "); got != "abc" {
		t.Fatalf("got %q", got)
	}
	if got := sanitizeDocument(""); got == "" {
		t.Fatal("empty name must have a placeholder")
	}
	long := sanitizeDocument(strings.Repeat("ạ", 500))
	if n := len([]rune(long)); n != maxDocumentLen+1 {
		t.Fatalf("len %d", n)
	}
}
