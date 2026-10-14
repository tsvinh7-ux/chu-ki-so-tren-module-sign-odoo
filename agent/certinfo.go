package main

import (
	"crypto/ecdsa"
	"crypto/rsa"
	"crypto/sha1"
	"crypto/x509"
	"encoding/asn1"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"math/big"
	"strings"
	"time"
	"unicode/utf16"
)

// certMeta is what the agent needs to know about a certificate. It is
// produced by a deliberately lenient parser: certificates issued by some
// CAs are rejected by crypto/x509 (negative serial numbers, odd string
// encodings, unusual extensions...), but they are still perfectly usable
// for signing, so we only decode the few fields we display.
type certMeta struct {
	Thumbprint string // SHA-1 of DER, uppercase hex
	Subject    string
	CommonName string
	Issuer     string
	SerialHex  string
	NotBefore  time.Time
	NotAfter   time.Time
	KeyType    string // "rsa" or "ec"
	SigSize    int    // expected raw signature size (RSA modulus bytes, or 2*coordinate for EC)
	PublicKey  any
	SelfSigned bool
}

type lenientCert struct {
	TBS    asn1.RawValue
	SigAlg asn1.RawValue
	Sig    asn1.RawValue
}

type lenientTBS struct {
	Version  int `asn1:"optional,explicit,default:0,tag:0"`
	Serial   asn1.RawValue
	SigAlg   asn1.RawValue
	Issuer   asn1.RawValue
	Validity asn1.RawValue
	Subject  asn1.RawValue
	SPKI     asn1.RawValue
	// Remaining optional fields (unique ids, extensions) are ignored.
}

type lenientValidity struct {
	NotBefore asn1.RawValue
	NotAfter  asn1.RawValue
}

type lenientATV struct {
	Type  asn1.ObjectIdentifier
	Value asn1.RawValue
}

// thumbprint returns the SHA-1 thumbprint (uppercase hex) of a DER certificate.
func thumbprint(der []byte) string {
	sum := sha1.Sum(der)
	return strings.ToUpper(hex.EncodeToString(sum[:]))
}

// normalizeThumbprint uppercases an id and strips separators that users or
// tools commonly insert (spaces, colons).
func normalizeThumbprint(id string) string {
	r := strings.NewReplacer(" ", "", ":", "", "-", "", "‎", "")
	return strings.ToUpper(r.Replace(strings.TrimSpace(id)))
}

func parseCertMeta(der []byte) (*certMeta, error) {
	var c lenientCert
	rest, err := asn1.Unmarshal(der, &c)
	if err != nil {
		return nil, fmt.Errorf("certificate: %w", err)
	}
	if len(rest) != 0 {
		return nil, errors.New("certificate: trailing data")
	}
	var tbs lenientTBS
	if _, err := asn1.Unmarshal(c.TBS.FullBytes, &tbs); err != nil {
		return nil, fmt.Errorf("tbsCertificate: %w", err)
	}
	if tbs.Serial.Tag != asn1.TagInteger || tbs.Serial.Class != asn1.ClassUniversal {
		return nil, errors.New("tbsCertificate: bad serial")
	}
	m := &certMeta{
		Thumbprint: thumbprint(der),
		SerialHex:  strings.ToUpper(hex.EncodeToString(tbs.Serial.Bytes)),
	}
	issuer, _, err := parseName(tbs.Issuer.FullBytes)
	if err != nil {
		return nil, fmt.Errorf("issuer: %w", err)
	}
	subject, cn, err := parseName(tbs.Subject.FullBytes)
	if err != nil {
		return nil, fmt.Errorf("subject: %w", err)
	}
	m.Issuer, m.Subject, m.CommonName = issuer, subject, cn
	m.SelfSigned = string(tbs.Issuer.FullBytes) == string(tbs.Subject.FullBytes)

	var v lenientValidity
	if _, err := asn1.Unmarshal(tbs.Validity.FullBytes, &v); err != nil {
		return nil, fmt.Errorf("validity: %w", err)
	}
	if m.NotBefore, err = parseASN1Time(v.NotBefore); err != nil {
		return nil, fmt.Errorf("notBefore: %w", err)
	}
	if m.NotAfter, err = parseASN1Time(v.NotAfter); err != nil {
		return nil, fmt.Errorf("notAfter: %w", err)
	}

	pub, err := x509.ParsePKIXPublicKey(tbs.SPKI.FullBytes)
	if err != nil {
		return nil, fmt.Errorf("public key: %w", err)
	}
	switch k := pub.(type) {
	case *rsa.PublicKey:
		m.KeyType = "rsa"
		m.SigSize = (k.N.BitLen() + 7) / 8
	case *ecdsa.PublicKey:
		m.KeyType = "ec"
		m.SigSize = 2 * ((k.Curve.Params().BitSize + 7) / 8)
	default:
		return nil, fmt.Errorf("unsupported public key type %T", pub)
	}
	m.PublicKey = pub
	return m, nil
}

func parseASN1Time(rv asn1.RawValue) (time.Time, error) {
	s := string(rv.Bytes)
	var layouts []string
	switch rv.Tag {
	case asn1.TagUTCTime:
		layouts = []string{"060102150405Z0700", "0601021504Z0700"}
	case asn1.TagGeneralizedTime:
		layouts = []string{"20060102150405Z0700", "20060102150405.999999999Z0700"}
	default:
		return time.Time{}, fmt.Errorf("unexpected time tag %d", rv.Tag)
	}
	for _, l := range layouts {
		if t, err := time.Parse(l, s); err == nil {
			if rv.Tag == asn1.TagUTCTime && t.Year() >= 2050 {
				t = t.AddDate(-100, 0, 0)
			}
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("cannot parse time %q", s)
}

var oidNames = map[string]string{
	"2.5.4.3":                    "CN",
	"2.5.4.4":                    "SN",
	"2.5.4.5":                    "SERIALNUMBER",
	"2.5.4.6":                    "C",
	"2.5.4.7":                    "L",
	"2.5.4.8":                    "ST",
	"2.5.4.9":                    "STREET",
	"2.5.4.10":                   "O",
	"2.5.4.11":                   "OU",
	"2.5.4.12":                   "T",
	"2.5.4.42":                   "GN",
	"2.5.4.97":                   "organizationIdentifier",
	"1.2.840.113549.1.9.1":       "E",
	"0.9.2342.19200300.100.1.1":  "UID",
	"0.9.2342.19200300.100.1.25": "DC",
}

// parseName decodes an X.501 Name into an RFC 4514-style string (most
// specific RDN first) and returns the last CN found.
func parseName(der []byte) (string, string, error) {
	var rdns []asn1.RawValue
	rest, err := asn1.Unmarshal(der, &rdns)
	if err != nil {
		return "", "", err
	}
	if len(rest) != 0 {
		return "", "", errors.New("trailing data")
	}
	var parts []string
	var cn string
	for _, set := range rdns {
		var atvs []lenientATV
		if _, err := asn1.UnmarshalWithParams(set.FullBytes, &atvs, "set"); err != nil {
			return "", "", err
		}
		var sub []string
		for _, atv := range atvs {
			oid := atv.Type.String()
			key, ok := oidNames[oid]
			if !ok {
				key = oid
			}
			val, isString := decodeDirectoryString(atv.Value)
			if oid == "2.5.4.3" && isString {
				cn = val
			}
			if isString {
				val = escapeRFC4514(val)
			} else {
				val = "#" + strings.ToUpper(hex.EncodeToString(atv.Value.FullBytes))
			}
			sub = append(sub, key+"="+val)
		}
		parts = append(parts, strings.Join(sub, "+"))
	}
	// RFC 4514: reverse order (most specific first).
	for i, j := 0, len(parts)-1; i < j; i, j = i+1, j-1 {
		parts[i], parts[j] = parts[j], parts[i]
	}
	return strings.Join(parts, ", "), cn, nil
}

func decodeDirectoryString(v asn1.RawValue) (string, bool) {
	if v.Class != asn1.ClassUniversal {
		return "", false
	}
	switch v.Tag {
	case asn1.TagUTF8String, asn1.TagPrintableString, asn1.TagIA5String,
		asn1.TagNumericString, 26 /* VisibleString */ :
		return strings.ToValidUTF8(string(v.Bytes), "�"), true
	case asn1.TagT61String:
		// Latin-1 approximation.
		r := make([]rune, len(v.Bytes))
		for i, b := range v.Bytes {
			r[i] = rune(b)
		}
		return string(r), true
	case asn1.TagBMPString:
		if len(v.Bytes)%2 != 0 {
			return "", false
		}
		u := make([]uint16, len(v.Bytes)/2)
		for i := range u {
			u[i] = binary.BigEndian.Uint16(v.Bytes[2*i:])
		}
		return string(utf16.Decode(u)), true
	case 28: // UniversalString (UTF-32BE)
		if len(v.Bytes)%4 != 0 {
			return "", false
		}
		r := make([]rune, len(v.Bytes)/4)
		for i := range r {
			r[i] = rune(binary.BigEndian.Uint32(v.Bytes[4*i:]))
		}
		return strings.ToValidUTF8(string(r), "�"), true
	}
	return "", false
}

func escapeRFC4514(s string) string {
	var b strings.Builder
	for i, r := range s {
		switch {
		case r == ',' || r == '+' || r == '"' || r == '\\' || r == '<' || r == '>' || r == ';':
			b.WriteByte('\\')
			b.WriteRune(r)
		case i == 0 && (r == ' ' || r == '#'):
			b.WriteByte('\\')
			b.WriteRune(r)
		case i == len(s)-1 && r == ' ':
			b.WriteString("\\ ")
		case r < 0x20 || r == 0x7f:
			fmt.Fprintf(&b, "\\%02X", r)
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// buildCertInfo produces the JSON description of a certificate.
func buildCertInfo(der []byte, chain [][]byte, now time.Time) (CertInfo, *certMeta, error) {
	m, err := parseCertMeta(der)
	if err != nil {
		return CertInfo{}, nil, err
	}
	info := CertInfo{
		ID:         m.Thumbprint,
		Subject:    m.Subject,
		CommonName: m.CommonName,
		Issuer:     m.Issuer,
		Serial:     m.SerialHex,
		NotBefore:  m.NotBefore.Format(time.RFC3339),
		NotAfter:   m.NotAfter.Format(time.RFC3339),
		Expired:    now.After(m.NotAfter),
		KeyType:    m.KeyType,
		DER:        base64.StdEncoding.EncodeToString(der),
		Chain:      []string{},
	}
	for _, c := range chain {
		info.Chain = append(info.Chain, base64.StdEncoding.EncodeToString(c))
	}
	return info, m, nil
}

// ecdsaRawToDER converts a raw r||s ECDSA signature (as returned by CNG) to
// the ASN.1 DER form SEQUENCE { r INTEGER, s INTEGER }.
func ecdsaRawToDER(raw []byte) ([]byte, error) {
	if len(raw) == 0 || len(raw)%2 != 0 {
		return nil, fmt.Errorf("invalid raw ECDSA signature length %d", len(raw))
	}
	half := len(raw) / 2
	sig := struct{ R, S *big.Int }{
		R: new(big.Int).SetBytes(raw[:half]),
		S: new(big.Int).SetBytes(raw[half:]),
	}
	return asn1.Marshal(sig)
}

// reverseBytes returns a reversed copy (CryptoAPI returns little-endian signatures).
func reverseBytes(b []byte) []byte {
	out := make([]byte, len(b))
	for i := range b {
		out[len(b)-1-i] = b[i]
	}
	return out
}
